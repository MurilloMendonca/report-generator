const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

(async () => {
    const root = path.resolve(__dirname, '..');
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, 'http://localhost').pathname;
        const relative = pathname.startsWith('/share/') ? 'static/index.html' : pathname === '/session/shared-one' ? 'static/index.html' : pathname.slice(1);
        if (!relative.startsWith('static/') || relative.includes('..') || !fs.existsSync(path.join(root, relative))) { response.writeHead(404).end(); return; }
        response.writeHead(200, { 'Content-Type': relative.endsWith('.html') ? 'text/html' : relative.endsWith('.js') ? 'application/javascript' : 'text/css', 'Referrer-Policy': 'no-referrer' });
        response.end(fs.readFileSync(path.join(root, relative)));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
        const owner = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        const origin = `http://127.0.0.1:${server.address().port}`;
        const links = [], creations = [], errors = [], publicRequests = [];
        const session = {
            job_id: 'shared-one', current_step: 'completed', status: 'Concluído', progress: 100, pdf_version: 1, has_pdf: true,
            initial_prompt: 'Analisar receita e orientar decisões.', current_goal: 'Analisar receita e orientar decisões.',
            settings: { title: 'Relatório de receita', language: 'Portuguese', font_size: 10, visual_theme_id: 'executive_clean', chart_style_id: 'modern', selected_template_id: 'report-layout' }, provider: 'deepseek', model_name: 'deepseek-flash',
            artifacts: [{ id: 'summary', type: 'text', indicator: 'Resumo executivo', reason: 'Explicar os resultados' }, { id: 'sales', type: 'bar', indicator: 'Receita por região', data_binding: { source: 'sales.csv', roles: { category: 'region', value: 'revenue' } } }],
            template: [{ id: 'summary', page: 1, x: 10, y: 30, w: 190, h: 40 }, { id: 'sales', page: 1, x: 10, y: 80, w: 190, h: 100 }],
            components: { summary: 'def render_summary(pdf, dataframes, x, y, w, h):\n    pass' }, component_texts: { summary: 'A receita total é 30.' },
            calculated_metrics: { total: 30 }, script: '# Código do relatório\nprint("Revenue: 30")', logs: ['Relatório concluído.'],
            quality_review: { status: 'warning', error_count: 0, warning_count: 1, checked_at: '2026-10-22T12:00:00Z', published_version: 1,
                components: [{ id: 'summary', page: 1, references: [{ pointer: '/total', display: '30' }] }],
                issues: [{ component_id: 'summary', severity: 'warning', code: 'unreferenced_numbers', message: 'Confira valores, unidades e datas nos dados.' }] },
            files: [{ original_filename: 'sales.csv', mime_type: 'text/csv', has_preprocessed: true, raw_artifact: { filename: 'sales.csv' }, artifact: { filename: 'sales.csv' } }],
            refinement_messages: [{ role: 'user', content: 'Mostre os valores no gráfico.' }],
            explore_history: [{ role: 'assistant', content: 'A região Sul lidera.', turn_id: 'c'.repeat(32), charts: [{ id: 'chart_1', title: 'Receita regional' }] }],
            source_snapshot_history: [{ snapshot_id: 'd'.repeat(32), dataset_name: 'Receita anterior', row_count: 2 }]
        };
        const route = (page, isPublic) => page.route('**/api/**', async handler => {
            const request = handler.request(); const url = new URL(request.url()); const pathname = url.pathname;
            if (isPublic) publicRequests.push({ method: request.method(), path: pathname, cookie: request.headers().cookie });
            let data = {};
            if (pathname.startsWith('/api/shared-reports/')) {
                const token = pathname.split('/')[3]; const link = links.find(item => item.token === token && item.active);
                if (!link) { await handler.fulfill({ status: 404, json: { detail: 'Link indisponível.' } }); return; }
                if (pathname.endsWith('/docx')) { await handler.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', body: Buffer.from('PK docx') }); return; }
                if (pathname.endsWith('/pdf')) { await handler.fulfill({ contentType: 'application/pdf', body: fs.readFileSync(path.join(root, 'tests/canonical/fixtures/sales_ops/sales_context.pdf')) }); return; }
                if (pathname.includes('/explore/')) { await handler.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') }); return; }
                if (pathname.includes('/files/')) { await handler.fulfill({ contentType: 'text/csv', body: url.searchParams.get('variant') === 'preprocessed' ? 'revenue\n60\n' : 'revenue\n30\n' }); return; }
                data = { ...session, sharing: { read_only: true, expires_at: link.expires_at } };
            } else if (pathname.includes('/share-links')) {
                if (request.method() === 'POST') {
                    const payload = request.postDataJSON(); creations.push(payload);
                    const token = String.fromCharCode(97 + links.length).repeat(43);
                    const link = { id: `link-${links.length + 1}`, token, label: payload.label, active: true, expires_at: payload.expires_in_hours === null ? null : new Date(Date.now() + payload.expires_in_hours * 3600000).toISOString(), revoked_at: null };
                    links.push(link); data = { ...link, path: `/share/${token}` };
                } else if (request.method() === 'DELETE') { const link = links.find(item => pathname.endsWith(item.id)); link.active = false; link.revoked_at = new Date().toISOString(); data = link; }
                else data = { links };
            } else if (pathname === '/api/auth/me') data = { user: { email_verified: true, email: 'owner@example.com' } };
            else if (pathname === '/api/models') data = { providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek Flash' }] }, default_provider: 'deepseek', default_model: 'deepseek-flash' };
            else if (pathname === '/api/sessions/shared-one') data = session;
            else if (pathname.endsWith('/pdf')) { await handler.fulfill({ contentType: 'application/pdf', body: fs.readFileSync(path.join(root, 'tests/canonical/fixtures/sales_ops/sales_context.pdf')) }); return; }
            await handler.fulfill({ json: data });
        });
        owner.on('pageerror', error => errors.push(error.message)); await route(owner, false);
        await owner.goto(`${origin}/session/shared-one`);
        await owner.getByRole('button', { name: 'Compartilhar', exact: true }).click();
        await owner.locator('#share-link-label').fill('Equipe'); await owner.locator('#share-link-expiry').selectOption('24');
        await owner.getByRole('button', { name: 'Criar link de leitura', exact: true }).click();
        await owner.waitForFunction(() => document.getElementById('share-link-url').value.includes('/share/'));
        assert.equal(creations[0].expires_in_hours, 24);
        await owner.locator('#share-link-expiry').selectOption('none'); await owner.locator('#share-link-label').fill('Cliente');
        await owner.getByRole('button', { name: 'Criar link de leitura', exact: true }).click();
        await owner.waitForFunction(() => document.getElementById('share-link-url').value.endsWith('b'.repeat(43)));
        assert.equal(creations[1].expires_in_hours, null);
        await owner.screenshot({ path: process.env.SHARE_OWNER_SCREENSHOT || '/tmp/repgen-share-owner.png', fullPage: true });
        await owner.getByRole('button', { name: 'Fechar', exact: true }).click();
        assert(await owner.locator('#s-title').isEnabled(), 'owner settings remain editable');
        await owner.locator('[data-tab-target="tab-design"]').click();
        const ownerX = await owner.evaluate(() => App.reportLayoutEditor.getItems()[0].x);
        await owner.locator('.editor-block').first().press('ArrowRight');
        assert.equal(await owner.evaluate(() => App.reportLayoutEditor.getItems()[0].x), ownerX + 1, 'owner can still move layout blocks');
        await owner.locator('.editor-block').first().press('Control+z');
        await owner.locator('[data-tab-target="tab-quality"]').click();
        assert((await owner.locator('#tab-quality').innerText()).includes('Publicado com pontos para conferir'));
        await owner.evaluate(() => App.updateUI({ ...App.currentSession, quality_review: {} }));
        assert((await owner.locator('#tab-quality').innerText()).includes('próxima renderização'));
        await owner.evaluate(() => App.updateUI(App.currentSession));
        await owner.screenshot({ path: '/tmp/repgen-quality-owner.png', fullPage: true });
        assert.equal(await owner.locator('#tab-quality').getByRole('button', { name: 'Editar componente', exact: true }).count(), 1);
        await owner.locator('#tab-quality').getByRole('button', { name: 'Conferir métricas e fontes', exact: true }).click();
        assert(await owner.locator('#tab-data').isVisible());
        await owner.locator('#share-report-btn').click();

        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        const viewer = await context.newPage(); viewer.on('pageerror', error => errors.push(error.message)); await route(viewer, true);
        await context.addCookies([{ name: 'session', value: 'viewer-cookie', url: origin }]);
        await viewer.goto(`${origin}/share/${links[1].token}`);
        await viewer.getByRole('heading', { name: 'Relatório de receita', exact: true }).waitFor();
        assert((await viewer.locator('#shared-expiry').innerText()).includes('sem expiração'));
        assert(await viewer.locator('#app-shell .sidebar').isVisible());
        assert((await viewer.locator('#chat-history').innerText()).includes('Mostre os valores no gráfico.'));
        assert.deepStrictEqual(await viewer.locator('[data-tab-target]').allTextContents(), await owner.locator('[data-tab-target]').allTextContents());
        for (const selector of ['#chat-input', '#chat-form button', '#explore-input', '#explore-submit', '#share-report-btn', '#apply-layout-btn', '[data-action="trigger-add-file"]', '[data-action="edit-indicator-new"]', '[data-action="delete-file"]']) {
            assert(await viewer.locator(selector).isDisabled(), selector + ' must be disabled');
        }
        await viewer.evaluate(() => {
            for (const id of ['chat-form', 'explore-form', 'settings-form', 'saved-source-form']) {
                document.getElementById(id).dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
        });
        await viewer.locator('[data-tab-target="tab-quality"]').click();
        await viewer.locator('#tab-quality summary').click();
        assert((await viewer.locator('#tab-quality').innerText()).includes('/total: 30'));
        await viewer.screenshot({ path: '/tmp/repgen-quality-shared.png', fullPage: true });
        assert(await viewer.locator('#tab-quality').getByRole('button', { name: 'Editar componente', exact: true }).isDisabled());
        await viewer.locator('#tab-quality').getByRole('button', { name: 'Conferir métricas e fontes', exact: true }).click();
        assert(await viewer.locator('#tab-data').isVisible());
        assert((await viewer.locator('#tab-data').innerText()).includes('30'));
        await viewer.locator('[data-tab-target="tab-artifacts"]').click();
        assert((await viewer.locator('#artifacts-visual').innerText()).includes('Receita por região'));
        assert(await viewer.locator('[data-action="edit-indicator"]').first().isDisabled());
        await viewer.locator('#component-search').fill('Receita');
        assert.equal(await viewer.locator('.component-card').count(), 1);
        assert(await viewer.locator('[data-action="delete-indicator"]').isDisabled(), 'filtering keeps new controls disabled');
        await viewer.locator('#component-search').fill('');
        await viewer.locator('[data-tab-target="tab-script"]').click();
        assert((await viewer.locator('#full-script-content').innerText()).includes('Revenue: 30'));
        await viewer.locator('[data-tab-target="tab-settings"]').click();
        assert.equal(await viewer.locator('#s-title').inputValue(), 'Relatório de receita');
        assert.equal(await viewer.locator('#s-font-size').inputValue(), '10');
        assert.equal(await viewer.locator('#s-model-name').inputValue(), 'deepseek-flash');
        assert.equal(await viewer.locator('#s-visual-theme').inputValue(), 'executive_clean');
        assert.equal(await viewer.locator('#settings-form').locator('button:enabled, input:enabled, select:enabled, textarea:enabled').count(), 0, 'every setting and action is disabled');
        await viewer.locator('[data-tab-target="tab-design"]').click();
        assert.equal(await viewer.locator('#layout-canvas .editor-page').count(), 1);
        const before = await viewer.evaluate(() => JSON.stringify(App.reportLayoutEditor.getItems()));
        const block = viewer.locator('#layout-canvas .editor-block').first();
        await block.click();
        await block.press('ArrowRight');
        await block.press('Delete');
        await block.dblclick();
        assert(!await viewer.locator('#indicator-modal').isVisible());
        const box = await block.boundingBox();
        await viewer.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await viewer.mouse.down();
        await viewer.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 40);
        await viewer.mouse.up();
        assert.equal(await viewer.evaluate(() => JSON.stringify(App.reportLayoutEditor.getItems())), before, 'dragging and keyboard cannot alter shared layout');
        assert(await viewer.getByRole('button', { name: 'Adicionar página', exact: true }).isDisabled());
        assert(await viewer.locator('#layout-canvas input[aria-label="X (mm)"]').isDisabled());
        const zoom = await viewer.locator('.editor-zoom-value').textContent();
        await viewer.getByRole('button', { name: 'Aumentar zoom', exact: true }).click();
        assert.notEqual(await viewer.locator('.editor-zoom-value').textContent(), zoom);
        await viewer.locator('[data-tab-target="tab-explore"]').click();
        assert((await viewer.locator('#explore-chat').innerText()).includes('região Sul'));
        const chart = viewer.locator('.explore-chart img');
        await chart.waitFor();
        assert.equal(await chart.getAttribute('src'), `/api/shared-reports/${links[1].token}/explore/${'c'.repeat(32)}/charts/chart_1`);
        assert.equal(await viewer.locator('#source-snapshot-list a').getAttribute('href'), `/api/shared-reports/${links[1].token}/source-snapshots/${'d'.repeat(32)}/download`);
        await viewer.locator('.file-open').click();
        await viewer.locator('[data-action="preview-file-variant"][data-variant="preprocessed"]').click();
        await viewer.locator('#file-preview-body .preview-text').waitFor();
        assert((await viewer.locator('#file-preview-body').innerText()).includes('60'));
        await viewer.locator('[data-action="close-file-preview"]').click();
        await viewer.locator('[data-tab-target="tab-artifacts"]').click();
        await viewer.screenshot({ path: process.env.SHARE_VIEWER_SCREENSHOT || '/tmp/repgen-share-viewer.png', fullPage: true });
        // Polling rerenders newly added actions using the same read-only mode.
        session.artifacts.push({ id: 'new', type: 'text', indicator: 'Novo resumo' });
        session.pdf_version = 2;
        await viewer.evaluate(() => SharedReport.load());
        assert(await viewer.getByRole('button', { name: 'Editar componente Novo resumo', exact: true }).isDisabled());
        await viewer.waitForFunction(() => App.lastPdfVersion === 2);
        // Viewers can download the Word export through the token API too.
        assert(await viewer.locator('#download-docx-btn').isEnabled());
        const [docx] = await Promise.all([viewer.waitForEvent('download'), viewer.locator('#download-docx-btn').click()]);
        assert.equal(docx.suggestedFilename(), 'relatorio-shared-one.docx');
        assert(publicRequests.some(item => item.path === `/api/shared-reports/${links[1].token}/docx`));
        await viewer.setViewportSize({ width: 390, height: 844 });
        assert((await viewer.locator('#app-shell').boundingBox()).width <= 390);
        await viewer.screenshot({ path: '/tmp/repgen-share-mobile.png', fullPage: true });
        assert(publicRequests.every(item => item.method === 'GET' && item.path.startsWith('/api/shared-reports/')), 'public viewer must only use the read-only token API');
        assert(publicRequests.filter(item => !item.path.includes('/explore/')).every(item => !item.cookie), 'public fetches must omit account cookies');
        await owner.locator('.share-link-row').filter({ hasText: 'Cliente' }).getByRole('button', { name: 'Revogar', exact: true }).click();
        await owner.waitForFunction(() => document.getElementById('share-link-status').textContent.includes('revogado'));
        await viewer.evaluate(() => SharedReport.load());
        assert((await viewer.locator('#workspace-notice').innerText()).includes('revogado'));
        for (const selector of ['#tab-script', '#tab-quality', '#chat-history', '#file-workspace', '#header-progress', '#file-preview-body']) {
            assert.equal(await viewer.locator(selector).textContent(), '', 'revocation clears ' + selector);
        }
        assert.equal(await viewer.locator('#pdf-viewer').count(), 0);
        assert.equal(await viewer.evaluate(() => App.currentSession), null);
        const invalid = await context.newPage(); await route(invalid, true);
        invalid.on('pageerror', error => errors.push(error.message));
        await invalid.goto(origin + '/share/invalid');
        await invalid.getByRole('heading', { name: 'Relatório indisponível' }).waitFor();
        assert((await invalid.locator('#workspace-notice').innerText()).includes('não está disponível'));
        assert.deepStrictEqual(errors, []);
        console.log('sharing browser checks passed');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
