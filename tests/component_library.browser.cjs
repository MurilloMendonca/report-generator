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
        'import json, tempfile; from fpdf import FPDF; from app.utils.component_library import component_library_payload, component_preview_image_path; previews = {key: component_preview_image_path(key) for key in ("bar_ranked", "line_timeseries", "table_summary")}; pdf = FPDF(format=(120,80)); pdf.add_page(); pdf.image(previews["table_summary"], x=0, y=0, w=120, h=80); target = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name; pdf.output(target); print(json.dumps({"catalog": component_library_payload(), "previews": previews, "pdf": target}))',
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
        const edits = [];
        const previewRequests = [];
        let pending = null;
        let rejectEdit = false;
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/api/**', async route => {
            const url = new URL(route.request().url()).pathname;
            if (url.startsWith('/api/component-library/presets/')) {
                const preset = url.split('/')[4];
                await route.fulfill({ contentType: 'image/png', body: fs.readFileSync(path.join(root, fixture.previews[preset] || fixture.previews.bar_ranked)) });
                return;
            }
            let data = { status: 'OK' };
            if (url === '/api/auth/me') data = { user: { email_verified: true, email: 'editor@example.com' } };
            else if (url === '/api/models') data = { providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek' }] }, default_provider: 'deepseek', default_model: 'deepseek-flash' };
            else if (url === '/api/component-library') data = fixture.catalog;
            else if (url === '/api/layout-templates') data = { templates: [] };
            else if (url === '/api/sessions') data = { sessions: [] };
            else if (url.endsWith('/edit')) {
                const payload = route.request().postDataJSON(); edits.push(payload);
                const componentId = url.split('/')[5];
                pending = { componentId, payload, polls: 0, fail: rejectEdit };
                session.component_edit = { id: `operation-${edits.length}`, component_id: componentId, status: 'queued', message: 'Edição recebida.' };
                session.current_step = 'component_queued';
                await route.fulfill({ status: 202, json: { status: 'queued', operation: session.component_edit } }); return;
            } else if (url === '/api/sessions/library-browser') {
                if (pending && pending.polls++ === 0) {
                    session.component_edit.status = 'running'; session.component_edit.message = 'Melhorando apenas este componente...';
                } else if (pending) {
                    session.component_edit.status = pending.fail ? 'failed' : 'completed';
                    session.component_edit.message = pending.fail ? 'O modelo demorou demais. Tente novamente.' : 'Componente atualizado.';
                    session.current_step = pending.fail ? 'failed' : 'completed';
                    if (!pending.fail) {
                        session.pdf_version += 1;
                        if (pending.payload.artifact) session.artifacts = session.artifacts.map(item => item.id === pending.componentId ? pending.payload.artifact : item);
                        if (pending.componentId === 'summary') session.component_texts.summary = 'A receita total é 30. Priorize a região Sul.';
                    }
                    pending = null;
                }
                data = session;
            }
            else if (url.endsWith('/preview')) {
                previewRequests.push(url);
                await route.fulfill({ contentType: 'application/pdf', body: fs.readFileSync(fixture.pdf) });
                return;
            }
            await route.fulfill({ json: data });
        });
        const session = {
            job_id: 'library-browser', current_step: 'completed', status: 'Concluído', progress: 100,
            files: [{ original_filename: 'data.csv', artifact: { filename: 'data.csv', columns: [{ name: 'region' }, { name: 'revenue' }, { name: 'cost' }] } }],
            logs: [], components: { revenue_total: 'stale metric code' }, calculated_metrics: { revenue_total: 30 }, component_texts: { summary: 'A receita total é 30. A região Sul representa 20.' }, template: [], settings: {}, pdf_version: 0,
            artifacts: [{ id: 'table', type: 'table', indicator: 'Receita e custo por região', columns: ['data.csv:region', 'data.csv:revenue', 'data.csv:cost'],
                component_preset_id: 'table_summary', render_mode: 'library', visual_options: { top_n: 5 },
                data_binding: { source: 'data.csv', roles: { dimensions: ['region'], metrics: ['revenue', 'cost'] } } },
                { id: 'summary', type: 'text', indicator: 'Resumo executivo', columns: [], reason: 'Contextualizar o resultado e orientar decisões.' },
                { id: 'revenue_total', type: 'metric', indicator: 'Receita total', columns: ['data.csv:revenue'] }],
        };
        await page.goto(`http://127.0.0.1:${server.address().port}`, { waitUntil: 'networkidle' });
        await page.evaluate(session => { App.currentSession = session; App.showWorkspace(); App.updateUI(session); App.handleEditIndicator(-1); }, session);
        assert.strictEqual(await page.locator('#m-render-mode').inputValue(), '');
        assert.equal(await page.locator('#m-type option[value="metric"]').count(), 0, 'calculated metrics do not belong in the visual component editor');
        assert.strictEqual(await page.evaluate(() => App.currentIndicatorPayload().newArt.render_mode), undefined);
        await page.locator('#m-type').selectOption('line');
        await page.locator('#m-preset').selectOption('line_timeseries');
        await page.locator('#component-advanced').evaluate(element => { element.open = true; });
        assert((await page.locator('#preset-inspector').innerText()).includes('date_granularity'));
        await page.evaluate(() => App.handleEditIndicator(0));
        await page.locator('#component-advanced').evaluate(element => { element.open = true; });
        assert.strictEqual(await page.locator('#m-code').getAttribute('readonly'), '');
        const inspector = await page.locator('#preset-inspector').innerText();
        assert(inspector.includes('metrics (uma ou mais colunas)'));
        assert(inspector.includes('metrics: revenue, cost'));
        await page.locator('#component-advanced').evaluate(element => { element.open = false; });
        await page.locator('.component-data-section').evaluate(element => { element.open = true; });
        assert.deepStrictEqual(await page.getByLabel('Valores (selecione uma ou mais) *', { exact: true }).evaluate(select => Array.from(select.selectedOptions, option => option.value)), ['revenue', 'cost']);
        await page.locator('.component-data-section').evaluate(element => { element.open = false; });
        await page.screenshot({ path: process.env.COMPONENT_LIBRARY_SCREENSHOT || '/tmp/pdf-library-editor.png', fullPage: true });

        await page.evaluate(() => App.closeIndicatorModal());
        await page.locator('[data-tab-target="tab-artifacts"]').click();
        await page.locator('#component-filter').selectOption('metric');
        assert.equal(await page.locator('#artifacts-visual .component-card').count(), 1);
        assert.equal(await page.locator('#artifacts-visual [data-action="edit-indicator"]').count(), 0, 'metrics must not offer the visual editor');
        assert((await page.locator('#artifacts-visual').innerText()).includes('chat do relatório'));
        const previewsBeforeMetric = previewRequests.length;
        await page.getByRole('button', { name: 'Ver resultado de Receita total', exact: true }).click();
        assert(await page.locator('#tab-data').isVisible());
        assert((await page.locator('#data-artifacts-container').innerText()).includes('30'));
        assert.equal(await page.locator('#indicator-modal').isVisible(), false);
        await page.locator('.tab[data-tab-target="tab-artifacts"]').click();
        await page.evaluate(() => App.handleEditIndicator(2));
        assert(await page.locator('#tab-data').isVisible(), 'stale edit actions must also route metrics to their calculated results');
        assert.equal(await page.evaluate(() => App.isRenderableIndicator(App.currentSession.artifacts[2])), false, 'stale metric code must never enable a PDF preview');
        assert.equal(await page.locator('#indicator-modal').isVisible(), false);
        assert.equal(previewRequests.length, previewsBeforeMetric, 'viewing a metric must not request a rendered preview');
        await page.locator('.tab[data-tab-target="tab-artifacts"]').click();
        await page.locator('#component-filter').selectOption('all');
        await page.locator('#component-search').fill('Resumo');
        assert.equal(await page.locator('#artifacts-visual .component-card').count(), 1);
        await page.locator('#component-search').fill('');
        await page.locator('#component-filter').selectOption('text');
        assert.equal(await page.locator('#artifacts-visual .component-card').count(), 1);
        await page.getByRole('button', { name: 'Editar componente Resumo executivo', exact: true }).click();
        assert.equal(await page.locator('#component-text').inputValue(), session.component_texts.summary);
        await page.locator('#component-text').fill('A receita total é 30. Esta é uma edição local.');
        assert((await page.locator('.component-text-preview').innerText()).includes('edição local'), 'text drafts must update the reading preview immediately');
        await page.evaluate(() => App.updateUI({ ...App.currentSession, logs: ['Atualização em segundo plano'] }));
        assert((await page.locator('#component-text').inputValue()).includes('edição local'), 'polling must preserve unsaved text');
        await page.getByRole('button', { name: 'Mais conciso', exact: true }).click();
        await page.getByRole('button', { name: 'Aplicar melhoria', exact: true }).click();
        await page.waitForFunction(() => document.getElementById('component-editor-status').textContent === 'Melhorando apenas este componente...');
        assert.equal(await page.locator('#component-save-btn').isDisabled(), true);
        await page.waitForFunction(() => document.getElementById('component-editor-status').dataset.state === 'success');
        assert.equal(edits.length, 1);
        assert.equal(edits[0].artifact.id, 'summary');
        assert(edits[0].instruction.includes('Encurte'));
        assert(edits[0].text.includes('edição local'));
        assert.equal(edits[0].code, undefined, 'unchanged generated code must not suppress text changes');
        assert.equal(edits[0].artifact.visual_theme_id, null, 'using the global theme must explicitly clear a previous override');
        assert.equal(await page.locator('#component-text').inputValue(), session.component_texts.summary);
        assert((await page.locator('.component-text-preview').innerText()).includes('Priorize a região Sul.'));
        await page.locator('#indicator-modal').evaluate(element => { element.scrollTop = 0; });
        await page.screenshot({ path: process.env.COMPONENT_TEXT_SCREENSHOT || '/tmp/pdf-component-text-editor.png', fullPage: true });

        rejectEdit = true;
        await page.locator('#component-instruction').fill('Revisar o texto sem perder os números.');
        await page.getByRole('button', { name: 'Aplicar melhoria', exact: true }).click();
        await page.waitForFunction(() => document.getElementById('component-editor-status').dataset.state === 'error');
        assert((await page.locator('#component-instruction').inputValue()).includes('sem perder os números'), 'failed requests must preserve the instruction for retry');
        assert.equal(await page.locator('#component-improve-btn').isEnabled(), true);
        assert((await page.locator('#component-editor-status').innerText()).includes('preservada'));
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.locator('#component-text').isVisible());
        assert((await page.locator('#indicator-modal').boundingBox()).width <= 390);
        assert.deepStrictEqual(errors, []);
        console.log('component library browser checks passed');
    } finally {
        if (browser) await browser.close();
        fs.unlinkSync(fixture.pdf);
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
