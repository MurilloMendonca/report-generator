const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

(async () => {
    const root = path.resolve(__dirname, '..');
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, 'http://localhost').pathname;
        const relative = pathname.startsWith('/share/') ? 'static/shared.html' : pathname === '/session/shared-one' ? 'static/index.html' : pathname.slice(1);
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
            settings: { title: 'Relatório de receita', language: 'Portuguese', font_size: 10 }, provider: 'deepseek', model_name: 'deepseek-flash',
            artifacts: [{ id: 'summary', type: 'text', indicator: 'Resumo executivo', reason: 'Explicar os resultados' }, { id: 'sales', type: 'bar', indicator: 'Receita por região', data_binding: { source: 'sales.csv', roles: { category: 'region', value: 'revenue' } } }],
            template: [{ id: 'summary', page: 1, x: 10, y: 30, w: 190, h: 40 }, { id: 'sales', page: 1, x: 10, y: 80, w: 190, h: 100 }],
            components: { summary: 'def render_summary(pdf, dataframes, x, y, w, h):\n    pass' }, component_texts: { summary: 'A receita total é 30.' },
            calculated_metrics: { total: 30 }, script: '# Código do relatório\nprint("Revenue: 30")', logs: ['Relatório concluído.'],
            files: [{ original_filename: 'sales.csv', mime_type: 'text/csv', has_preprocessed: true, raw_artifact: { filename: 'sales.csv' }, artifact: { filename: 'sales.csv' } }],
            refinement_messages: [{ role: 'user', content: 'Mostre os valores no gráfico.' }], explore_history: [{ role: 'assistant', content: 'A região Sul lidera.' }]
        };
        const route = (page, isPublic) => page.route('**/api/**', async handler => {
            const request = handler.request(); const url = new URL(request.url()); const pathname = url.pathname;
            if (isPublic) publicRequests.push({ method: request.method(), path: pathname });
            let data = {};
            if (pathname.startsWith('/api/shared-reports/')) {
                const token = pathname.split('/')[3]; const link = links.find(item => item.token === token && item.active);
                if (!link) { await handler.fulfill({ status: 404, json: { detail: 'Link indisponível.' } }); return; }
                if (pathname.endsWith('/pdf')) { await handler.fulfill({ contentType: 'application/pdf', body: fs.readFileSync(path.join(root, 'tests/canonical/fixtures/sales_ops/sales_context.pdf')) }); return; }
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

        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        const viewer = await context.newPage(); viewer.on('pageerror', error => errors.push(error.message)); await route(viewer, true);
        await viewer.goto(`${origin}/share/${links[1].token}`);
        await viewer.getByRole('heading', { name: 'Relatório de receita', exact: true }).waitFor();
        assert((await viewer.locator('#shared-expiry').innerText()).includes('sem expiração'));
        assert.equal(await viewer.locator('form, textarea, input').count(), 0, 'anonymous viewer must contain no editing forms');
        await viewer.getByRole('button', { name: 'Componentes', exact: true }).click();
        assert((await viewer.locator('#shared-components').innerText()).includes('A receita total é 30.'));
        await viewer.getByRole('button', { name: 'Código', exact: true }).click(); assert((await viewer.locator('#shared-script').innerText()).includes('Revenue: 30'));
        await viewer.getByRole('button', { name: 'Configurações', exact: true }).click(); assert((await viewer.locator('#shared-settings').innerText()).includes('font_size'));
        await viewer.getByRole('button', { name: 'Métricas e dados', exact: true }).click(); assert((await viewer.locator('#shared-data').innerText()).includes('30'));
        await viewer.getByRole('button', { name: 'Layout', exact: true }).click(); assert.equal(await viewer.locator('#shared-layout svg').count(), 1);
        await viewer.getByRole('button', { name: 'Histórico e consultas', exact: true }).click(); assert((await viewer.locator('#shared-history').innerText()).includes('região Sul'));
        await viewer.getByRole('button', { name: 'Arquivos', exact: true }).click();
        await viewer.locator('.shared-file-actions').nth(1).getByRole('button', { name: 'Visualizar', exact: true }).click();
        await viewer.locator('#shared-file-body .preview-text').waitFor(); assert((await viewer.locator('#shared-file-body').innerText()).includes('60'));
        await viewer.getByRole('button', { name: 'Fechar', exact: true }).click();
        await viewer.getByRole('button', { name: 'Componentes', exact: true }).click();
        await viewer.screenshot({ path: process.env.SHARE_VIEWER_SCREENSHOT || '/tmp/repgen-share-viewer.png', fullPage: true });
        await viewer.setViewportSize({ width: 390, height: 844 });
        assert((await viewer.locator('.shared-report-shell').boundingBox()).width <= 390);
        assert(publicRequests.every(item => item.method === 'GET' && item.path.startsWith('/api/shared-reports/')), 'public viewer must only use the read-only token API');
        await owner.locator('.share-link-row').filter({ hasText: 'Cliente' }).getByRole('button', { name: 'Revogar', exact: true }).click();
        await owner.waitForFunction(() => document.getElementById('share-link-status').textContent.includes('revogado'));
        await viewer.evaluate(() => SharedReport.load());
        assert.equal(await viewer.locator('#shared-content').isVisible(), false);
        assert.equal(await viewer.locator('#shared-download').isVisible(), false);
        assert((await viewer.locator('#shared-notice').innerText()).includes('revogado'));
        assert.equal(await viewer.locator('#shared-script').textContent(), '', 'revocation clears previously rendered report content');
        assert.deepStrictEqual(errors, []);
        console.log('sharing browser checks passed');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
