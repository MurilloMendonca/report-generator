const assert = require('assert');
const path = require('path');
const {execFileSync} = require('child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.EXPLORER_TEST_URL || 'http://127.0.0.1:8000';
const fixture = (...args) => JSON.parse(execFileSync(process.env.PYTHON_EXECUTABLE || 'python', [path.join(__dirname, 'explorer_browser_fixture.py'), ...args], {
    cwd: path.resolve(__dirname, '..'), encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
}));

(async () => {
    const session = fixture('create');
    let browser;
    try {
        browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE, args:['--no-sandbox']});
        const context = await browser.newContext({viewport:{width:1440,height:1000}});
        await context.addCookies([{name:'pdf_report_session',value:session.token,url:base,httpOnly:true,sameSite:'Lax'}]);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let calls = 0;
        let mode = 'ask';
        let release;
        let gate = new Promise(resolve => { release = resolve; });
        await page.route(`**/api/sessions/${session.job_id}/explore`, async route => {
            calls += 1;
            if (gate) await gate;
            const prompt = new URLSearchParams(route.request().postData()).get('prompt');
            const result = fixture(mode, session.job_id, prompt);
            await route.fulfill({status:result.status,json:result.data});
        });
        await page.goto(`${base}/session/${session.job_id}`, {waitUntil:'networkidle'});
        await page.getByRole('button', {name:'Explorador 🔍',exact:true}).click();
        await page.getByLabel('Pergunta ou pedido de gráfico').fill('Show a revenue chart by region');
        await page.getByRole('button', {name:'Consultar',exact:true}).click();
        await page.locator('#explore-chat .pending').waitFor();
        assert.strictEqual(await page.locator('#explore-submit').isDisabled(), true);
        await page.getByLabel('Pergunta ou pedido de gráfico').fill('Show a revenue chart by region');
        await page.getByLabel('Pergunta ou pedido de gráfico').press('Enter');
        assert.strictEqual(calls, 1, 'pending queries cannot be submitted twice');
        release();
        gate = null;
        await page.waitForFunction(() => document.querySelector('#explore-chat img')?.naturalWidth > 0);
        assert.strictEqual(await page.locator('#explore-chat .explore-chart').count(), 1);
        assert((await page.locator('#explore-chat').textContent()).includes('Total revenue: 45'));
        assert.strictEqual(await page.locator('#explore-chat img').getAttribute('alt'), 'Revenue by region');
        const assertCompactPreview = async () => {
            const dimensions = await page.locator('#explore-chat img').first().evaluate(image => {
                const box = image.getBoundingClientRect();
                return {width:box.width,height:box.height,ratio:image.naturalWidth / image.naturalHeight};
            });
            const card = await page.locator('#explore-chat .explore-chart').first().boundingBox();
            const message = await page.locator('#explore-chat .explorer-message.ai').first().boundingBox();
            assert(card.width <= 560.5, 'chart cards must stay compact instead of filling the desktop chat');
            assert(message.width <= 680.5, 'assistant messages must have a readable maximum width');
            assert(dimensions.width > 0 && dimensions.height > 0 && dimensions.height <= 280.5, 'chart previews must have a compact height');
            assert(Math.abs(dimensions.width / dimensions.height - dimensions.ratio) < 0.02, 'chart previews must preserve their aspect ratio');
        };
        await assertCompactPreview();
        await page.setViewportSize({width:1920,height:1080});
        await assertCompactPreview();
        await page.setViewportSize({width:1440,height:1000});
        const image = page.locator('#explore-chat img').first();
        const originalSource = await image.getAttribute('src');
        await image.evaluate(async element => {
            const canvas = document.createElement('canvas');
            canvas.width = 200;
            canvas.height = 1000;
            element.src = canvas.toDataURL('image/png');
            await element.decode();
        });
        await assertCompactPreview();
        await image.evaluate(async (element, src) => { element.src = src; await element.decode(); }, originalSource);
        const [download] = await Promise.all([
            page.waitForEvent('download'), page.getByRole('link', {name:'Baixar PNG',exact:true}).click(),
        ]);
        assert.strictEqual(download.suggestedFilename(), 'grafico-chart_1.png');
        await page.screenshot({path:'/tmp/explorer-charts-desktop.png',fullPage:true});
        await page.reload({waitUntil:'networkidle'});
        await page.getByRole('button', {name:'Explorador 🔍',exact:true}).click();
        await page.waitForFunction(() => document.querySelector('#explore-chat img')?.naturalWidth > 0);
        assert.strictEqual(await page.locator('#explore-chat .explore-chart').count(), 1, 'reload must restore saved attachments');
        await page.setViewportSize({width:390,height:844});
        await assertCompactPreview();
        const box = await page.locator('#explore-chat img').boundingBox();
        await page.screenshot({path:'/tmp/explorer-charts-mobile.png',fullPage:true});
        assert(box.width > 100 && box.x >= 0 && box.x + box.width <= 391, 'chart must fit the narrow screen');
        mode = 'fail';
        await page.getByLabel('Pergunta ou pedido de gráfico').fill('Try an invalid query');
        await page.getByRole('button', {name:'Consultar',exact:true}).click();
        await page.getByRole('button', {name:'Tentar novamente',exact:true}).waitFor();
        assert.strictEqual(await page.getByLabel('Pergunta ou pedido de gráfico').inputValue(), 'Try an invalid query');
        mode = 'ask';
        await page.getByRole('button', {name:'Tentar novamente',exact:true}).click();
        await page.waitForFunction(() => document.querySelectorAll('#explore-chat .explore-chart').length === 2 && !document.querySelector('#explore-chat .pending'));
        await page.route('**/explore/*/charts/chart_1', route => route.fulfill({status:404,body:'Missing image'}));
        await page.reload({waitUntil:'networkidle'});
        await page.getByRole('button', {name:'Explorador 🔍',exact:true}).click();
        await page.locator('#explore-chat img').evaluateAll(images => images.forEach(image => { image.loading = 'eager'; }));
        await page.locator('.explore-chart-unavailable').first().waitFor({state:'visible'});
        assert.strictEqual(await page.locator('.explore-chart-download[aria-disabled="true"]').count(), 2);
        assert.deepStrictEqual(errors, []);
        console.log(JSON.stringify({chartCards:2,compactPreview:true,portraitAspectRatio:true,pending:true,download:true,reload:true,narrowScreen:true,retry:true,missingImage:true,browserErrors:errors}));
    } finally {
        if (browser) await browser.close();
        fixture('delete', session.job_id);
    }
})().catch(error => {console.error(error);process.exitCode=1;});
