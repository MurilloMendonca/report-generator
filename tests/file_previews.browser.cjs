// Run with PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE when they are not installed locally.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

(async () => {
    const root = path.resolve(__dirname, '..');
    const assets = {
        '/session/browser-files': ['static/index.html', 'text/html'],
        '/static/css/styles.css': ['static/css/styles.css', 'text/css'],
    };
    for (const name of ['api', 'utils', 'layout_editor', 'ui', 'saved_sources', 'app', 'components', 'sharing']) {
        assets[`/static/js/${name}.js`] = [`static/js/${name}.js`, 'application/javascript'];
    }
    const server = http.createServer((request, response) => {
        const asset = assets[new URL(request.url, 'http://localhost').pathname];
        if (!asset) { response.writeHead(404); response.end(); return; }
        response.writeHead(200, { 'Content-Type': asset[1] });
        response.end(fs.readFileSync(path.join(root, asset[0])));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
        const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        const workbook = (processed) => ({
            filename: 'operations.xls', type: 'excel_workbook', sheets: [
                { sheet_name: 'Entregas', shape: { rows: processed ? 250 : 1200, columns: 3 },
                    columns: [{ name: 'Região' }, { name: 'Entregas' }, { name: 'No prazo' }],
                    sample: [{ 'Região': processed ? 'Processed workbook' : 'Original workbook', 'Entregas': 120, 'No prazo': 112 },
                        { 'Região': 'Sul', 'Entregas': 80, 'No prazo': 75 }, { 'Região': 'Centro-Oeste', 'Entregas': 50, 'No prazo': 48 }] },
                { sheet_name: 'Metas', shape: { rows: 2, columns: 2 },
                    columns: [{ name: 'Região' }, { name: 'Meta' }], sample: [{ 'Região': 'Norte', 'Meta': 100 }, { 'Região': 'Sul', 'Meta': 80 }] },
            ],
        });
        const session = {
            job_id: 'browser-files', current_step: 'completed', status: 'Concluído', progress: 100,
            settings: {}, artifacts: [], template: [], logs: [], pdf_version: 0,
            provider: 'deepseek', model_name: 'deepseek-flash', files: [{
                original_filename: 'operations.xls', mime_type: 'application/vnd.ms-excel',
                preprocessed_path: '/report/filtered/operations.xlsx',
                raw_artifact: workbook(false), artifact: workbook(true),
            }],
        };
        let previews = 0;
        const downloads = [];
        await page.route('**/api/**', async route => {
            const url = new URL(route.request().url());
            let data = {};
            if (url.pathname === '/api/auth/me') data = { user: { email: 'test@example.com', email_verified: true } };
            else if (url.pathname === '/api/models') data = {
                providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek Flash' }] },
                default_provider: 'deepseek', default_model: 'deepseek-flash',
            };
            else if (url.pathname === '/api/sessions/browser-files') data = session;
            else if (url.pathname.endsWith('/preview')) previews += 1;
            else if (url.pathname.endsWith('/download')) {
                downloads.push(url.searchParams.get('variant'));
                await route.fulfill({
                    contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    body: fs.readFileSync(path.join(__dirname, 'canonical/fixtures/logistics_quality/incident_log.xlsx')),
                });
                return;
            }
            await route.fulfill({ json: data });
        });
        await page.goto(`http://127.0.0.1:${server.address().port}/session/browser-files`);
        await page.getByRole('button', { name: 'Visualizar operations.xls', exact: true }).click();
        const modal = page.locator('#file-preview-modal');
        await modal.getByText('Original workbook', { exact: false }).waitFor();
        const processed = modal.getByRole('button', { name: 'Pré-processado', exact: true });
        const original = modal.getByRole('button', { name: 'Original', exact: true });
        await processed.click();
        await modal.getByRole('heading', { name: 'operations_preprocessed.xlsx', exact: true }).waitFor();
        assert((await modal.locator('.preview-content').innerText()).includes('Processed workbook'));
        assert((await modal.locator('#file-preview-meta').innerText()).includes('XLSX'));
        assert.equal(await modal.getByRole('table').count(), 2, 'each sheet must have a sample table');
        await original.click();
        await processed.click();
        assert.equal(previews, 0, 'metadata previews must not request workbook bytes');
        const downloadPromise = page.waitForEvent('download');
        await modal.getByRole('button', { name: 'Baixar', exact: true }).click();
        const download = await downloadPromise;
        assert.equal(download.suggestedFilename(), 'operations_preprocessed.xlsx');
        assert.deepStrictEqual(downloads, ['preprocessed'], 'switching tabs must not leave stale download handlers');
        if (process.env.FILE_PREVIEW_SCREENSHOT) await page.screenshot({ path: process.env.FILE_PREVIEW_SCREENSHOT });
        await modal.getByRole('button', { name: 'Fechar', exact: true }).click();
        assert.equal(await modal.isVisible(), false);
        assert.deepStrictEqual(errors, []);
        process.stdout.write('ok\n');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
