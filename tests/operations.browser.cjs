const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

(async () => {
    const root = path.resolve(__dirname, '..');
    const server = http.createServer((req, res) => {
        const pathname = new URL(req.url, 'http://localhost').pathname;
        const files = {'/usage': 'static/usage.html', '/admin': 'static/admin.html', '/account': 'static/account.html'};
        const file = files[pathname] || ({'/static/js/api.js': 'static/js/api.js', '/static/js/account.js': 'static/js/account.js', '/static/js/operations.js': 'static/js/operations.js', '/static/css/styles.css': 'static/css/styles.css'})[pathname];
        if (!file) { res.writeHead(404); res.end(); return; }
        res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
        res.end(fs.readFileSync(path.join(root, file)));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox']});
        const page = await browser.newPage({viewport: {width: 1200, height: 1000}});
        const origin = `http://127.0.0.1:${server.address().port}`;
        await page.context().addCookies([{name: 'csrf_token', value: 'operations-csrf', url: origin}]);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        const summary = {calls: 3, input_tokens: 100, output_tokens: 50, failed_calls: 1, pending_calls: 0, unknown_token_calls: 1,
            monthly_token_limit: 10000, report_call_limit: 100, by_provider: [{provider: 'openai', calls: 3, input_tokens: 100, output_tokens: 50}]};
        const account = {id: 'admin', email: 'admin@example.com', email_verified: true, role: 'admin', status: 'active', monthly_tokens: 100, reports: 1, storage_bytes: 1024,
            monthly_token_limit: null, report_call_limit: null, effective_limits: {monthly_token_limit: 0, report_call_limit: 0}};
        const other = {...account, id: 'other', email: 'other@example.com', role: 'user'};
        let cleanup = 0;
        await page.route('**/api/**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            const method = request.method();
            if (method !== 'GET') assert.equal(request.headers()['x-csrf-token'], 'operations-csrf');
            let data;
            if (url.pathname === '/api/auth/me') {
                if (method === 'PATCH') Object.assign(account, request.postDataJSON());
                data = {user: account};
            }
            else if (url.pathname === '/api/auth/sessions') data = {sessions: [{id: 'current', current: true, user_agent: 'Chrome/140 Linux', ip: '127.0.0.1', last_seen_at: '2026-10-19T12:00:00Z'}]};
            else if (url.pathname === '/api/usage') data = {llm: summary, storage_bytes: 1024, storage_limit_bytes: 10 * 1024 ** 3, reports_last_24h: 1, reports_per_day_limit: 50};
            else if (url.pathname === '/api/usage/reports') data = {reports: [{job_id: 'test-owned', title: '<img src=x onerror=alert(1)>', usage: summary}]};
            else if (url.pathname === '/api/admin/usage') data = summary;
            else if (url.pathname === '/api/admin/workspace-usage') data = {total: {human_size: '1.00 MB'}};
            else if (url.pathname === '/api/admin/users') data = {total: 2, users: [account, other]};
            else if (url.pathname === '/api/admin/users/other') { Object.assign(other, request.postDataJSON()); data = {status: 'OK'}; }
            else if (url.pathname === '/api/admin/audit') data = {events: [{id: 1, event: 'admin_user_updated', user_id: 'admin', ip: '127.0.0.1', details: {status: other.status}, created_at: '2026-10-18T12:00:00Z'}], next_before: null};
            else if (url.pathname === '/api/admin/cleanup') { cleanup++; data = {status: 'OK'}; }
            else throw new Error(`Unexpected endpoint ${url.pathname}`);
            await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)});
        });
        await page.goto(`${origin}/usage`);
        await page.getByText('3 chamadas', {exact: false}).waitFor();
        assert.equal(await page.locator('#usage-total-calls').textContent(), '3');
        assert.equal(await page.locator('#usage-total-tokens').textContent(), '150');
        assert.equal(await page.locator('#usage-total-failures').textContent(), '1');
        const link = page.locator('#report-usage a');
        assert.equal(await link.textContent(), '<img src=x onerror=alert(1)>');
        assert.equal(await link.getAttribute('href'), '/session/test-owned');
        assert.equal(await page.locator('#report-usage img').count(), 0);
        await page.screenshot({path: '/tmp/phase67-usage-desktop.png', fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({path: '/tmp/phase67-usage-mobile.png', fullPage: true});
        await page.goto(`${origin}/admin`);
        await page.locator('.admin-user').nth(1).waitFor();
        const otherCard = page.locator('.admin-user').nth(1);
        await otherCard.locator('input').nth(0).fill('10000');
        await otherCard.locator('input').nth(1).fill('15');
        await otherCard.getByRole('button', {name: 'Salvar limites'}).click();
        await page.getByText('Limites salvos.', {exact: true}).waitFor();
        assert.equal(other.report_call_limit, 15);
        await otherCard.getByRole('button', {name: 'Desativar conta'}).click();
        await otherCard.getByRole('button', {name: 'Reativar conta'}).waitFor();
        assert.equal(other.status, 'disabled');
        assert(await page.locator('.admin-user').first().getByRole('button', {name: 'Desativar conta'}).isDisabled());
        await page.getByRole('button', {name: 'Executar limpeza por retenção'}).click();
        await page.getByText('Limpeza iniciada.', {exact: false}).waitFor();
        assert.equal(cleanup, 1);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({path: '/tmp/phase67-admin-mobile.png', fullPage: true});
        await page.setViewportSize({width: 1200, height: 1000});
        await page.screenshot({path: '/tmp/phase67-admin-desktop.png', fullPage: true});
        await page.goto(`${origin}/account`);
        await page.locator('#profile-name').fill('Workspace owner');
        await page.locator('#profile-form button[type=submit]').click();
        await page.getByText('Perfil salvo.', {exact: true}).waitFor();
        assert.equal(account.display_name, 'Workspace owner');
        await page.locator('#session-list .badge').waitFor();
        await page.screenshot({path: '/tmp/pdf-account-desktop.png', fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({path: '/tmp/pdf-account-mobile.png', fullPage: true});
        assert.deepEqual(errors, []);
        console.log('Usage/admin browser checks passed: quotas, disable, cleanup, CSRF, safe text, desktop and mobile.');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
