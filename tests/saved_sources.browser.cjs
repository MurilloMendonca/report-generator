// Real account/report forms against deterministic mocked connector responses.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
(async () => {
    const root = path.resolve(__dirname, '..');
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, 'http://localhost').pathname;
        const relative = pathname === '/sources' ? 'static/sources.html' : pathname === '/' ? 'static/index.html' : pathname.slice(1);
        if (!relative.startsWith('static/') || relative.includes('..')) return response.writeHead(404).end();
        response.writeHead(200, { 'Content-Type': relative.endsWith('.html') ? 'text/html' : relative.endsWith('.js') ? 'application/javascript' : 'text/css' });
        response.end(fs.readFileSync(path.join(root, relative)));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true, args: ['--no-sandbox'] });
        const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
        const origin = `http://127.0.0.1:${server.address().port}`;
        await page.context().addCookies([{ name: 'csrf_token', value: 'saved-csrf', url: origin }]);
        const errors = [], requests = [], connections = [], datasets = [];
        let rejectCatalogOnce = false;
        page.on('pageerror', error => errors.push(error.message));
        const session = { job_id: 'saved-browser', current_step: 'completed', status: 'Concluído', progress: 100, files: [], artifacts: [], components: {}, settings: {}, template: [], logs: [], pdf_version: 0, source_snapshot_history: [] };
        const firstId = '11111111-1111-1111-1111-111111111111', datasetId = '22222222-2222-2222-2222-222222222222';
        const publicConnectionId = '33333333-3333-3333-3333-333333333333', publicDatasetId = '44444444-4444-4444-4444-444444444444';
        function snapshot(amount, parameters, selectedId = datasetId) {
            const id = String(amount).padStart(32, '0');
            const file = { original_filename: 'dataset_orders.json', source: { snapshot_id: id, dataset_id: selectedId, dataset_name: datasets.find(item => item.id === selectedId).name, row_count: amount, parameters, fetched_at: '2026-10-19T12:00:00Z', type: 'saved_dataset' } };
            session.files = [file]; session.source_snapshot_history.push(file);
        }
        await page.route('**/api/**', async route => {
            const request = route.request(); const url = new URL(request.url()).pathname; const method = request.method();
            let data = {};
            if (method !== 'GET') {
                assert.equal(request.headers()['x-csrf-token'], 'saved-csrf');
                requests.push({ url, method, body: request.postData() });
            }
            if (url === '/api/auth/me') data = { user: { email_verified: true, email: 'saved@example.com' } };
            else if (url === '/api/data-sources') {
                if (rejectCatalogOnce) {
                    rejectCatalogOnce = false;
                    await route.fulfill({ status: 503, json: { detail: 'Fontes temporariamente indisponíveis.' } });
                    return;
                }
                data = { connections, datasets };
            }
            else if (url === '/api/data-sources/connections' && method === 'POST') {
                const body = request.postDataJSON();
                if (body.config.auth_mode === 'none') assert(!Object.hasOwn(body, 'secret'));
                else { assert.equal(body.secret, 'browser-secret'); assert.equal(body.config.auth_mode, 'bearer'); }
                const { secret, ...safe } = body; data = { ...safe, id: connections.length ? publicConnectionId : firstId, version: 1, has_secret: !!secret }; connections.push(data);
            } else if (url === '/api/data-sources/connections/' + firstId && method === 'PUT') {
                const body = request.postDataJSON(); assert(!Object.hasOwn(body, 'secret'), 'editing must preserve the stored secret without sending a mask');
                Object.assign(connections[0], body); data = connections[0];
            } else if (url.endsWith('/test')) data = { ok: true, message: 'Conexão testada com sucesso.' };
            else if (url === '/api/data-sources/datasets' && method === 'POST') {
                const body = request.postDataJSON(); data = { ...body, id: datasets.length ? publicDatasetId : datasetId, version: 1 }; datasets.push(data);
            } else if (url.endsWith('/preview')) data = { rows: [{ note: '<script>alert(1)</script>', amount: 10 }], preview: true, complete: false };
            else if (url === '/api/models') data = { providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek' }] }, default_provider: 'deepseek', default_model: 'deepseek-flash' };
            else if (url === '/api/component-library') data = { themes: [], chart_styles: [], presets: [] };
            else if (url === '/api/layout-templates') data = { templates: [] };
            else if (url === '/api/sessions' && method === 'GET') data = { sessions: [] };
            else if (url === '/api/sessions' && method === 'POST') {
                const isPublic = request.postData().includes(publicDatasetId);
                if (!isPublic) { assert(request.postData().includes(datasetId)); assert(request.postData().includes('2026-09-01')); }
                assert(!request.postData().includes('filename=""'), 'source-only reports must not submit an empty upload');
                assert(!request.postData().includes('name="api_url"'), 'external APIs are configured through saved sources');
                snapshot(10, isPublic ? {} : { start_date: '2026-09-01' }, isPublic ? publicDatasetId : datasetId); data = { job_id: session.job_id };
            } else if (url.endsWith('/sources/refresh')) {
                const body = request.postDataJSON(); assert.equal(body.sources[0].parameters.start_date, '2026-10-01');
                snapshot(20, body.sources[0].parameters); data = { status: 'queued' };
            } else if (url === '/api/sessions/' + session.job_id) data = session;
            await route.fulfill({ json: data });
        });
        await page.goto(origin + '/sources', { waitUntil: 'networkidle' });
        assert.equal(await page.locator('#connection-count').textContent(), '0');
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: '/tmp/pdf-sources-empty-mobile.png', fullPage: true });
        await page.setViewportSize({ width: 1440, height: 1100 });
        await page.locator('#connection-editor summary').click();
        await page.locator('#connection-name').fill('Sales API'); await page.locator('#connection-url').fill('https://api.example.com/v1'); await page.locator('#connection-secret').fill('browser-secret');
        await page.locator('#connection-form button[type=submit]').click();
        await page.getByText('Conexão salva.', { exact: false }).waitFor();
        await page.waitForFunction(() => document.getElementById('connection-secret').value === '');
        await page.locator('#connection-list').getByRole('button', { name: 'Editar' }).click();
        assert.equal(await page.locator('#connection-secret').inputValue(), '');
        await page.locator('#connection-name').fill('Sales API renamed'); await page.locator('#connection-form button[type=submit]').click();
        await page.locator('#connection-list').getByRole('button', { name: 'Testar conexão' }).click();
        await page.getByText('Conexão testada com sucesso.').waitFor();
        await page.locator('#dataset-editor summary').click();
        await page.locator('#dataset-name').fill('Orders <daily>'); await page.locator('#dataset-endpoint').fill('orders');
        await page.locator('#dataset-parameters').fill('{"start_date":"2026-01-01"}');
        await page.locator('#dataset-form button[type=submit]').click();
        await page.locator('#dataset-list').getByRole('button', { name: 'Prévia', exact: true }).click();
        await page.locator('#source-preview-table table').waitFor();
        assert((await page.locator('#source-preview-table').textContent()).includes('<script>alert(1)</script>'));
        assert.equal(await page.locator('#source-preview-table script').count(), 0);
        await page.locator('.preview-json summary').click();
        await page.locator('#source-preview-body').waitFor();
        assert((await page.locator('#source-preview-body').textContent()).includes('<script>alert(1)</script>'));
        assert.equal(await page.locator('#source-preview-body script').count(), 0);
        await page.screenshot({ path: '/tmp/pdf-sources-account.png', fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: '/tmp/pdf-sources-mobile.png', fullPage: true });
        await page.setViewportSize({ width: 1440, height: 1100 });
        rejectCatalogOnce = true;
        await page.goto(origin, { waitUntil: 'networkidle' });
        await page.getByText('Não foi possível carregar suas fontes.', { exact: false }).waitFor();
        await page.locator('#reload-initial-sources-btn').click();
        await page.locator('#initial-source-list input[type=checkbox]').waitFor();
        assert.equal(await page.locator('#api-url, #add-api-url').count(), 0);
        await page.locator('#initial-source-list input[type=checkbox]').check();
        await page.locator('#initial-source-list [data-parameter=start_date]').fill('2026-09-01');
        await page.locator('#reload-initial-sources-btn').click();
        await page.waitForFunction(() => !document.getElementById('reload-initial-sources-btn').disabled);
        assert(await page.locator('#initial-source-list input[type=checkbox]').isChecked());
        assert.equal(await page.locator('#initial-source-list [data-parameter=start_date]').inputValue(), '2026-09-01');
        await page.screenshot({ path: '/tmp/pdf-new-report-desktop.png', fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: '/tmp/pdf-new-report-mobile.png', fullPage: true });
        await page.setViewportSize({ width: 1440, height: 1100 });
        await page.locator('#upload-form button[type=submit]').click();
        await page.waitForFunction(() => App.currentSession?.source_snapshot_history?.length === 1);
        await page.locator('#file-workspace summary').filter({ hasText: 'Fontes de dados' }).click();
        await page.locator('#report-source-list input[type=checkbox]').check();
        await page.locator('#report-source-list [data-parameter=start_date]').fill('2026-10-01');
        await page.locator('#refresh-sources-btn').click();
        await page.waitForFunction(() => App.currentSession?.source_snapshot_history?.length === 2);
        await page.locator('#source-snapshot-history summary').click();
        assert.equal(await page.locator('#source-snapshot-list a').count(), 2);
        assert((await page.locator('#source-snapshot-list a').first().textContent()).includes('em uso'));
        assert.equal(await page.locator('#file-tray script').count(), 0);
        await page.screenshot({ path: '/tmp/pdf-sources-report.png', fullPage: true });
        await page.goto(origin + '/sources', { waitUntil: 'networkidle' });
        await page.locator('#connection-editor summary').click();
        await page.locator('#connection-kind').selectOption('postgres');
        assert(await page.locator('#postgres-connection-fields').isVisible());
        assert.equal(await page.locator('#connection-url').getAttribute('required'), null);
        await page.locator('#connection-kind').selectOption('api');
        await page.locator('#connection-auth').selectOption('none');
        assert(await page.locator('#secret-field').isHidden());
        await page.locator('#connection-name').fill('Public JSON');
        await page.locator('#connection-url').fill('https://public.example.com/data');
        await page.locator('#connection-form button[type=submit]').click();
        await page.waitForFunction(() => document.getElementById('connection-count').textContent === '2');
        await page.locator('#connection-list .source-card').last().getByRole('button', { name: 'Criar conjunto' }).click();
        assert.equal(await page.locator('#dataset-connection').inputValue(), publicConnectionId);
        await page.locator('#dataset-name').fill('Public records');
        await page.locator('#dataset-parameters').fill('[]');
        await page.locator('#dataset-form button[type=submit]').click();
        await page.getByText('Os parâmetros precisam ser um objeto JSON', { exact: false }).waitFor();
        await page.locator('#dataset-parameters').fill('{}');
        await page.locator('#dataset-form button[type=submit]').click();
        await page.waitForFunction(() => document.getElementById('dataset-count').textContent === '2');
        await page.goto(origin, { waitUntil: 'networkidle' });
        await page.locator(`#initial-source-list input[value="${publicDatasetId}"]`).check();
        await page.locator('#upload-form button[type=submit]').click();
        await page.waitForFunction(id => App.currentSession?.files?.[0]?.source?.dataset_id === id, publicDatasetId);
        assert.deepEqual(errors, []);
        console.log('Saved source browser flow passed: public/authenticated APIs, connection forms, preview, selection, parameters, catalog retry, refresh, history, mobile and CSRF.');
    } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
