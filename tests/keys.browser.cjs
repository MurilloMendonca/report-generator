// Run with PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE when they are not installed locally.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

(async () => {
    const root = path.resolve(__dirname, '..');
    const assets = {
        '/keys': ['static/keys.html', 'text/html'],
        '/static/js/api.js': ['static/js/api.js', 'application/javascript'],
        '/static/js/keys.js': ['static/js/keys.js', 'application/javascript'],
        '/static/css/styles.css': ['static/css/styles.css', 'text/css'],
    };
    const server = http.createServer((req, res) => {
        const asset = assets[new URL(req.url, 'http://localhost').pathname];
        if (!asset) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': asset[1] });
        res.end(fs.readFileSync(path.join(root, asset[0])));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
        const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
        const origin = `http://127.0.0.1:${server.address().port}`;
        await page.context().addCookies([{ name: 'csrf_token', value: 'browser-csrf', url: origin }]);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        const rows = {};
        let preferences = {};
        let validated = 0;
        let rejectTest = false;
        const models = { deepseek: 'deepseek-flash', gemini: 'gemini-3.8-flash', anthropic: 'claude-opus-5-5', openai: 'gpt-6.1-sol', openrouter: 'openai/gpt-6.1-sol' };
        const labels = { deepseek: 'DeepSeek', gemini: 'Google Gemini', anthropic: 'Anthropic', openai: 'OpenAI', openrouter: 'OpenRouter' };
        const secret = 'sk-planted-browser-key-abcdefghijklmnop';
        await page.route('**/api/**', async route => {
            const request = route.request();
            const url = new URL(request.url()).pathname;
            const method = request.method();
            if (method !== 'GET') assert.equal(request.headers()['x-csrf-token'], 'browser-csrf');
            let data = {};
            let statusCode = 200;
            if (url === '/api/auth/me') data = { user: { email_verified: true, email: 'test@example.com' } };
            else if (url === '/api/credentials') data = { credentials: Object.values(rows) };
            else if (url === '/api/model-preferences') {
                if (method === 'PUT') preferences = request.postDataJSON();
                data = preferences;
            } else if (url === '/api/models') {
                data = { providers: {}, credential_status: {}, defaults: {} };
                for (const provider of Object.keys(models)) {
                    const enabled = rows[provider]?.status === 'valid';
                    const model = models[provider];
                    data.providers[provider] = enabled ? [{ id: model, label: model }] : [];
                    data.defaults[provider] = model;
                    data.credential_status[provider] = { label: labels[provider], enabled, status: rows[provider]?.status || 'missing' };
                }
                data.default_provider = Object.keys(models).find(provider => data.credential_status[provider].enabled) || 'deepseek';
                data.default_model = models[data.default_provider];
            } else if (url.startsWith('/api/credentials/')) {
                const provider = url.split('/')[3];
                if (method === 'DELETE') delete rows[provider];
                else if (method === 'PUT') {
                    assert.equal(request.postDataJSON().api_key, secret);
                    rows[provider] = { provider, key_last4: secret.slice(-4), status: 'valid', model_count: 1, last_validated_at: new Date().toISOString() };
                } else if (method === 'POST') {
                    validated++;
                    if (rejectTest) {
                        rows[provider].status = 'invalid';
                        rows[provider].last_error = 'O provedor recusou esta chave.';
                        statusCode = 400;
                        data = { detail: { code: 'invalid_provider_key', message: 'Atualize sua chave.' } };
                    }
                }
            }
            await route.fulfill({ status: statusCode, contentType: 'application/json', body: JSON.stringify(data) });
        });
        await page.goto(`${origin}/keys`);
        const card = page.locator('[data-provider="deepseek"]');
        await card.getByText('Sem chave', { exact: true }).waitFor();
        assert.equal(await page.locator('.credential-card').count(), 5);
        await card.locator('input').fill(secret);
        await card.getByRole('button', { name: 'Salvar chave', exact: true }).click();
        await card.getByText('Válida', { exact: true }).waitFor();
        assert.equal(await card.locator('input').inputValue(), '');
        assert(!(await page.locator('body').textContent()).includes(secret));
        await card.getByRole('button', { name: 'Testar', exact: true }).click();
        await page.getByText('Chave validada.', { exact: true }).waitFor();
        assert.equal(validated, 1);
        await page.selectOption('#default-provider', 'deepseek');
        await page.selectOption('#fallback-provider', 'deepseek');
        await page.getByRole('button', { name: 'Salvar preferências' }).click();
        await page.getByText('Preferências salvas.', { exact: true }).waitFor();
        assert.equal(preferences.default_model, 'deepseek-flash');
        assert.equal(preferences.llm_fallback_provider, 'deepseek');
        await page.screenshot({ path: '/tmp/phase5-keys-desktop.png', fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.getByRole('button', { name: 'Salvar preferências' }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: '/tmp/phase5-keys-mobile.png', fullPage: true });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
        rejectTest = true;
        await card.getByRole('button', { name: 'Testar', exact: true }).click();
        await card.getByText('Inválida', { exact: true }).waitFor();
        assert.equal(await page.locator('#default-provider option').count(), 1);
        await card.getByRole('button', { name: 'Excluir', exact: true }).click();
        await card.getByText('Sem chave', { exact: true }).waitFor();
        assert.deepEqual(rows, {});
        await page.addScriptTag({ path: path.join(root, 'static/js/ui.js') });
        await page.addScriptTag({ path: path.join(root, 'static/js/app.js') });
        await page.evaluate(() => {
            for (const id of ['provider', 's-provider', 'model_name', 's-model-name']) {
                const select = document.createElement('select');
                select.id = id;
                document.body.append(select);
            }
        });
        for (const provider of ['anthropic', 'openai', 'openrouter']) {
            const providerCard = page.locator(`[data-provider="${provider}"]`);
            await providerCard.locator('input').fill(secret);
            await providerCard.getByRole('button', { name: 'Salvar chave', exact: true }).click();
            await providerCard.getByText('Válida', { exact: true }).waitFor();
            await page.selectOption('#default-provider', provider);
            await page.locator('#anthropic-server-fallback').uncheck();
            await page.getByRole('button', { name: 'Salvar preferências' }).click();
            await page.getByText('Preferências salvas.', { exact: true }).waitFor();
            assert.equal(preferences.default_provider, provider);
            assert.equal(preferences.anthropic_server_side_fallback, false);
            await page.evaluate(async provider => {
                await App.loadModels();
                App.updateModels();
                document.getElementById('s-provider').value = provider;
                App.updateSettingsModels();
            }, provider);
            assert.equal(await page.locator('#provider').inputValue(), provider);
            assert.equal(await page.locator('#model_name').inputValue(), models[provider]);
            assert.equal(await page.locator('#s-model-name').inputValue(), models[provider]);
            assert.equal(await page.locator('#provider option:not([disabled])').count(), 1);
            await providerCard.getByRole('button', { name: 'Excluir', exact: true }).click();
            await providerCard.getByText('Sem chave', { exact: true }).waitFor();
        }
        assert.deepEqual(errors, []);
        console.log('Five-provider browser flow passed (keys, preferences, Anthropic opt-out, model pickers, invalidation, responsive layout).');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
