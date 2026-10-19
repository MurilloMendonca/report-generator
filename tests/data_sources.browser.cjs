// Run with PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE when needed.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

(async () => {
    const root = path.resolve(__dirname, '..');
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, 'http://localhost').pathname;
        const relative = pathname === '/' ? 'static/index.html' : pathname.slice(1);
        if (!relative.startsWith('static/') || relative.includes('..')) {
            response.writeHead(404).end();
            return;
        }
        const type = relative.endsWith('.html') ? 'text/html' : relative.endsWith('.js') ? 'application/javascript' : 'text/css';
        response.writeHead(200, { 'Content-Type': type });
        response.end(fs.readFileSync(path.join(root, relative)));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
        const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
        const origin = `http://127.0.0.1:${server.address().port}`;
        await page.context().addCookies([{ name: 'csrf_token', value: 'sources-csrf', url: origin }]);
        const errors = [], imports = [];
        page.on('pageerror', error => errors.push(error.message));
        const session = {
            job_id: 'sources-browser', current_step: 'completed', status: 'Concluído', progress: 100,
            files: [{ original_filename: 'snapshot.json', mime_type: 'application/json', size_bytes: 30,
                artifact: { shape: { rows: 1, columns: 1 } } },
                { original_filename: 'brief.txt', mime_type: 'text/plain', validation: { encoding: 'cp1252' } }],
            artifacts: [], components: {}, settings: {}, template: [], logs: [], pdf_version: 0,
        };
        await page.route('**/api/**', async route => {
            const request = route.request();
            const url = new URL(request.url()).pathname;
            let data = {};
            if (request.method() === 'POST') {
                assert.equal(request.headers()['x-csrf-token'], 'sources-csrf');
                imports.push({ url, body: request.postData() });
                data = url === '/api/sessions' ? { job_id: session.job_id } : { status: 'OK' };
            } else if (url === '/api/auth/me') data = { user: { email_verified: true, email: 'test@example.com' } };
            else if (url === '/api/models') data = { providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek Flash' }] }, default_provider: 'deepseek', default_model: 'deepseek-flash' };
            else if (url === '/api/component-library') data = { presets: [], themes: [], chart_styles: [] };
            else if (url === '/api/layout-templates') data = { templates: [] };
            else if (url === '/api/sessions') data = { sessions: [] };
            else if (url.endsWith('/preview')) {
                if (url.includes('/files/1/')) {
                    await route.fulfill({ contentType: 'text/plain', body: Buffer.from('Orçamento aprovado: 12 milhões.', 'latin1') });
                } else {
                    await route.fulfill({ contentType: 'application/json', body: '[{"note":"<script>alert(1)</script>"}]' });
                }
                return;
            } else if (url === '/api/sessions/' + session.job_id) data = session;
            await route.fulfill({ json: data });
        });
        await page.goto(origin, { waitUntil: 'networkidle' });
        for (const id of ['file', 'add-file-input']) {
            const accepted = await page.locator('#' + id).getAttribute('accept');
            for (const extension of ['.json', '.jsonl', '.ndjson', '.txt']) assert(accepted.includes(extension));
        }
        await page.locator('#api-url').fill('https://example.com/api/data');
        await page.locator('#api-records-path').fill('/data/items');
        await page.screenshot({ path: process.env.DATA_SOURCES_SCREENSHOT || '/tmp/data-sources-start.png', fullPage: true });
        await page.locator('#upload-form button[type="submit"]').click();
        await page.waitForFunction(() => App.currentSession?.current_step === 'completed');
        assert.equal(imports[0].url, '/api/sessions');
        assert(imports[0].body.includes('https://example.com/api/data'));
        assert(imports[0].body.includes('/data/items'));
        await page.locator('.api-source-details summary').first().click();
        await page.locator('#add-api-url').fill('https://example.com/more');
        await page.locator('#api-source-form button[type="submit"]').click();
        await page.waitForFunction(() => document.getElementById('add-api-url').value === '');
        assert.equal(imports[1].url, '/api/sessions/sources-browser/files');
        assert(imports[1].body.includes('https://example.com/more'));
        await page.locator('[data-action="preview-file"]').first().click();
        await page.locator('.preview-text').waitFor();
        assert((await page.locator('.preview-text').textContent()).includes('<script>alert(1)</script>'));
        assert.equal(await page.locator('.preview-text script').count(), 0);
        await page.screenshot({ path: '/tmp/data-sources-preview.png', fullPage: true });
        await page.locator('[data-action="close-file-preview"]').click();
        await page.locator('[data-action="preview-file"][data-file-index="1"]').first().click();
        await page.locator('.preview-text').waitFor();
        assert((await page.locator('.preview-text').textContent()).includes('Orçamento aprovado: 12 milhões.'));
        assert.deepEqual(errors, []);
        console.log('Data source browser flow passed (API-only report, add API, CSRF, safe JSON preview, TXT encoding).');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
