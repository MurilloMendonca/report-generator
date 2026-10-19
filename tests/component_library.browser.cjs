// Exercise the component editor against the real catalog schema; no provider calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { execFileSync } = require('child_process');

(async () => {
    const root = path.resolve(__dirname, '..');
    const fixture = JSON.parse(execFileSync(process.env.PYTHON_EXECUTABLE || path.join(root, 'venv/bin/python'), ['-c',
        'import json; from app.utils.component_library import component_library_payload, component_preview_image_path; print(json.dumps({"catalog": component_library_payload(), "previews": {key: component_preview_image_path(key) for key in ("bar_ranked", "line_timeseries", "table_summary")}}))',
    ], { cwd: root, env: { ...process.env, MPLCONFIGDIR: '/tmp/pdf-library-browser-mpl' } }));
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
        const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/api/**', async route => {
            const url = new URL(route.request().url()).pathname;
            if (url.startsWith('/api/component-library/presets/')) {
                const preset = url.split('/')[4];
                await route.fulfill({ contentType: 'image/png', body: fs.readFileSync(path.join(root, fixture.previews[preset] || fixture.previews.bar_ranked)) });
                return;
            }
            let data = { status: 'OK' };
            if (url === '/api/models') data = { providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek' }] }, default_provider: 'deepseek', default_model: 'deepseek-flash' };
            else if (url === '/api/component-library') data = fixture.catalog;
            else if (url === '/api/layout-templates') data = { templates: [] };
            else if (url === '/api/sessions') data = { sessions: [] };
            else if (url.endsWith('/preview')) {
                await route.fulfill({ status: 204 });
                return;
            }
            await route.fulfill({ json: data });
        });
        const session = {
            job_id: 'library-browser', current_step: 'completed', status: 'Concluído', progress: 100,
            files: [], logs: [], components: {}, template: [], settings: {}, pdf_version: 0,
            artifacts: [{ id: 'table', type: 'table', indicator: 'Receita e custo por região', columns: ['data.csv:region', 'data.csv:revenue', 'data.csv:cost'],
                component_preset_id: 'table_summary', render_mode: 'library', visual_options: { top_n: 5 },
                data_binding: { source: 'data.csv', roles: { dimensions: ['region'], metrics: ['revenue', 'cost'] } } }],
        };
        await page.goto(`http://127.0.0.1:${server.address().port}`, { waitUntil: 'networkidle' });
        await page.evaluate(session => { App.currentSession = session; App.showWorkspace(); App.updateUI(session); App.handleEditIndicator(-1); }, session);
        assert.strictEqual(await page.locator('#m-render-mode').inputValue(), '');
        assert.strictEqual(await page.evaluate(() => App.currentIndicatorPayload().newArt.render_mode), undefined);
        await page.locator('#m-type').selectOption('line');
        await page.locator('#m-preset').selectOption('line_timeseries');
        assert((await page.locator('#preset-inspector').innerText()).includes('date_granularity'));
        await page.evaluate(() => App.handleEditIndicator(0));
        assert.strictEqual(await page.locator('#m-code').getAttribute('readonly'), '');
        const inspector = await page.locator('#preset-inspector').innerText();
        assert(inspector.includes('metrics (uma ou mais colunas)'));
        assert(inspector.includes('metrics: revenue, cost'));
        await page.screenshot({ path: process.env.COMPONENT_LIBRARY_SCREENSHOT || '/tmp/pdf-library-editor.png', fullPage: true });
        assert.deepStrictEqual(errors, []);
        console.log('component library browser checks passed');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
