const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

// Serve the real frontend through intercepted requests; this check needs no app or database.
(async () => {
    const browser = await chromium.launch({
        headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox']
    });
    try {
        for (const requested of [3, null]) {
            const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
            const errors = [];
            const submissions = [];
            const saves = [];
            const session = {
                job_id: 'browser-pages', current_step: 'completed', status: 'Concluído', progress: 100,
                initial_prompt: 'Analyze performance', files: [], logs: [], artifacts: [], pdf_version: 0,
                provider: 'deepseek', model_name: 'deepseek-flash',
                settings: { title: 'Multi page report', page_count: requested },
                template: [1, 2, 3].map(number => ({ id: `section_${number}`, page: number, x: 10, y: 30, w: 190, h: 80 }))
            };
            page.on('pageerror', error => errors.push(error.message));
            await page.route('http://report.test/**', async route => {
                const request = route.request();
                const url = new URL(request.url());
                if (url.pathname.startsWith('/api/')) {
                    let data = {};
                    if (url.pathname === '/api/auth/me') data = { user: { email: 'browser@example.com', email_verified: true } };
                    if (url.pathname === '/api/models') data = {
                        providers: { deepseek: [{ id: 'deepseek-flash', label: 'DeepSeek Flash' }] },
                        default_provider: 'deepseek', default_model: 'deepseek-flash'
                    };
                    if (url.pathname === '/api/component-library') data = { presets: [], themes: [], chart_styles: [] };
                    if (url.pathname === '/api/layout-templates') data = { templates: [] };
                    if (url.pathname === '/api/sessions' && request.method() === 'GET') data = { sessions: [] };
                    if (url.pathname === '/api/sessions' && request.method() === 'POST') {
                        submissions.push(request.postDataBuffer().toString());
                        data = { job_id: session.job_id };
                    }
                    if (url.pathname === `/api/sessions/${session.job_id}`) data = session;
                    if (url.pathname.endsWith('/settings')) {
                        const settings = request.postDataJSON();
                        saves.push(settings);
                        session.settings = { ...session.settings, ...settings };
                        data = { status: 'OK' };
                    }
                    return route.fulfill({ json: data });
                }
                const file = url.pathname.startsWith('/static/') ? url.pathname.slice(1) : 'static/index.html';
                const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
                return route.fulfill({ body: fs.readFileSync(path.join(__dirname, '..', file)), contentType });
            });
            await page.goto('http://report.test/', { waitUntil: 'networkidle' });
            const count = page.getByLabel('Número de páginas', { exact: false }).first();
            assert.strictEqual(await count.inputValue(), '');
            assert.strictEqual(await count.getAttribute('placeholder'), 'A IA decide');
            await page.locator('#file').setInputFiles({ name: 'data.csv', mimeType: 'text/csv', buffer: Buffer.from('region,revenue\nNorth,10\n') });
            await count.fill('0');
            await page.locator('#upload-form button[type="submit"]').click();
            assert.strictEqual(submissions.length, 0, 'invalid page counts must block form submission');
            await count.fill(requested === null ? '' : String(requested));
            await page.screenshot({ path: `/tmp/multipage-create-${requested ?? 'auto'}.png`, fullPage: true });
            await page.locator('#upload-form button[type="submit"]').click();
            await page.waitForFunction(() => App.currentSession?.current_step === 'completed');
            assert.strictEqual(submissions.length, 1);
            if (requested === null) assert(!submissions[0].includes('name="page_count"'));
            else assert(submissions[0].includes('name="page_count"\r\n\r\n3'));

            await page.locator('[data-tab-target="tab-settings"]').click();
            assert.strictEqual(await page.locator('#s-page-count').inputValue(), requested === null ? '' : '3');
            assert.strictEqual(await page.locator('#s-page-count-status').textContent(), 'Layout atual: 3 página(s).');
            await page.locator('#s-page-count').fill('4');
            const fixedSaved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PUT');
            await page.locator('#settings-form button[type="submit"]').click();
            await fixedSaved;
            assert.strictEqual(saves.at(-1).page_count, 4);
            await page.locator('#s-page-count').fill('');
            const saved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PUT');
            await page.locator('#settings-form button[type="submit"]').click();
            await saved;
            assert.strictEqual(saves.at(-1).page_count, null);
            await page.screenshot({ path: `/tmp/multipage-settings-${requested ?? 'auto'}.png`, fullPage: true });
            assert.deepStrictEqual(errors, []);
            console.log(JSON.stringify({ requested, submissions: submissions.length, savedCounts: saves.map(item => item.page_count), errors }));
            await page.close();
        }
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
