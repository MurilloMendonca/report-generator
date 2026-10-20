const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('assert');
const browserPath = process.env.CHROMIUM_EXECUTABLE;
const template = { id: 'browser-layout', name: 'Resumo executivo', description: 'Texto e evidências visuais em uma página.', slots: [
    { slot_id: 'intro', name: 'Resumo executivo', type: 'text', page: 1, x: 10, y: 30, w: 190, h: 40 },
    { slot_id: 'chart', name: 'Receita por região', type: 'graphic', page: 1, x: 10, y: 80, w: 90, h: 65 },
    { slot_id: 'table', name: 'Comparação de resultados', type: 'graphic', page: 1, x: 110, y: 80, w: 90, h: 65 },
    { slot_id: 'conclusion', name: 'Conclusão e próximos passos', type: 'text', page: 1, x: 10, y: 155, w: 190, h: 45 },
] };
const session = { job_id: 'browser-layout', initial_prompt: 'Relatório executivo de vendas', current_step: 'completed', status: 'Concluído', progress: 100,
    files: [], logs: [], artifacts: template.slots.map(slot => ({ id: slot.slot_id, indicator: slot.name, type: slot.type, columns: [], reason: '' })),
    template: template.slots.map(({slot_id, page, x, y, w, h}) => ({id:slot_id, page, x, y, w, h})), settings: {title:'Resumo executivo',font:'Arial',font_size:10,line_height:5,alignment:'J',language:'Portuguese'}, pdf_version:0,
};
(async () => {
    const browser = await chromium.launch({headless:true, executablePath:browserPath, args:['--no-sandbox']});
    try {
        for (const name of ['template', 'report']) {
            const page = await browser.newPage({viewport:{width:1440,height:1000}});
            const errors=[];
            let savedPayload = null;
            let deleteCalls = 0;
            page.on('pageerror', error => errors.push(error.message));
            await page.route('**/api/**', async route => {
                const url=new URL(route.request().url()).pathname;
                let data={status:'OK'};
                if (route.request().method() === 'DELETE') deleteCalls += 1;
                if (route.request().method() === 'PUT') {
                    savedPayload = route.request().postDataJSON();
                    if (url.includes('/sessions/')) session.template = savedPayload;
                    else data = { ...savedPayload, id: template.id };
                }
                if(url==='/api/layout-templates')data={templates:[template]};
                else if(url==='/api/models') data={providers:{deepseek:[{id:'deepseek-flash',label:'DeepSeek Flash'}]},default_provider:'deepseek',default_model:'deepseek-flash'};
                else if(url==='/api/component-library') data={presets:[],themes:[],chart_styles:[]};
                else if(url==='/api/sessions')data={sessions:[]};
                else if(url==='/api/sessions/browser-layout')data=session;
                await route.fulfill({json:data});
            });
            await page.goto((process.env.LAYOUT_TEST_URL || 'http://127.0.0.1:8000')+'/'+(name==='template'?'templates':''),{waitUntil:'networkidle'});
            if(name==='report'){
                await page.evaluate(session => {App.currentSession=session;App.showWorkspace();App.updateUI(session);},session);
                await page.locator('[data-tab-target="tab-design"]').click();
            }
            const host=page.locator(name==='template'?'#template-canvas':'#layout-canvas');
            const model = () => page.evaluate(name => {
                const editor = name === 'template' ? TemplateEditor.editor : App.reportLayoutEditor;
                return { items: editor.getItems(), dirty: editor.dirty, pages: editor.state.pages };
            }, name);
            const drag = async (selector, dx, dy) => {
                const block = host.locator('[data-item-id="chart"]');
                await block.click();
                const handle = selector ? block.locator(selector) : block;
                const rect = await handle.boundingBox();
                const paper = await host.locator('.editor-page').first().boundingBox();
                const factor = paper.width / 210;
                await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
                await page.mouse.down();
                await page.mouse.move(rect.x + rect.width / 2 + dx * factor, rect.y + rect.height / 2 + dy * factor, { steps: 8 });
                await page.mouse.up();
            };
            await drag(null, 15, 10);
            let chart = (await model()).items.find(item => item.id === 'chart');
            assert.strictEqual(chart.x, 25);
            assert.strictEqual(chart.y, 90);
            await host.locator('[data-item-id="chart"]').focus();
            await page.keyboard.press('ArrowRight');
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').x, 26);
            await host.getByRole('button', { name: 'Desfazer (Ctrl+Z)', exact: true }).click();
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').x, 25);
            await host.getByRole('button', { name: 'Refazer (Ctrl+Shift+Z)', exact: true }).click();
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').x, 26);
            await host.getByRole('button', { name: 'Aumentar zoom', exact: true }).click();
            await drag(null, 9, 0);
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').x, 35, 'drag distance must account for zoom');
            await drag('[data-resize="e"]', 10, 0);
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').w, 100);
            const xInput = host.getByLabel('X (mm)', { exact: true });
            await xInput.fill('999');
            await xInput.press('Tab');
            chart = (await model()).items.find(item => item.id === 'chart');
            assert.strictEqual(chart.x + chart.w, 210, 'geometry must remain on the page');
            await xInput.fill('36');
            await xInput.press('Tab');
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').x, 36);
            if (name === 'report') {
                const beforePoll = await model();
                await page.evaluate(() => App.updateUI({ ...App.currentSession, logs: ['Background refresh'] }));
                assert.deepStrictEqual((await model()).items, beforePoll.items, 'polling cannot overwrite an unsaved layout');
                await host.getByRole('button', { name: 'Retirar o bloco selecionado do layout', exact: true }).click();
                assert.strictEqual((await model()).items.length, 3);
                assert.strictEqual(await page.evaluate(() => App.currentSession.artifacts.length), 4);
                await host.getByRole('button', { name: 'Desfazer (Ctrl+Z)', exact: true }).click();
                assert.strictEqual((await model()).items.length, 4);
            } else {
                await host.getByRole('button', { name: 'Duplicar o bloco selecionado', exact: true }).click();
                assert.strictEqual((await model()).items.length, 5);
                await host.getByRole('button', { name: 'Desfazer (Ctrl+Z)', exact: true }).click();
                assert.strictEqual((await model()).items.length, 4);
            }
            await host.locator('[data-item-id="chart"]').click();
            await host.getByRole('button', { name: 'Adicionar página', exact: true }).click();
            await host.getByLabel('Página', { exact: true }).selectOption('2');
            assert.strictEqual((await model()).items.find(item => item.id === 'chart').page, 2);
            assert.strictEqual(await host.locator('[data-item-id="chart"]').evaluate(block => block.parentElement.dataset.page), '2');
            if (name === 'template') {
                await page.locator('#save-template-btn').click();
                await page.waitForFunction(() => document.getElementById('save-status').textContent === 'Template salvo.');
                assert.strictEqual(savedPayload.slots.find(item => item.slot_id === 'chart').x, 36);
                assert.strictEqual(savedPayload.page_count, 2);
            } else {
                await page.locator('#apply-layout-btn').click();
                await page.waitForFunction(() => !App.reportLayoutEditor.dirty);
                assert.strictEqual(savedPayload.find(item => item.id === 'chart').x, 36);
                assert.strictEqual(deleteCalls, 0, 'layout changes must not delete source components');
            }
            assert.strictEqual((await model()).dirty, false);
            assert.deepStrictEqual(errors, []);
            if (name === 'report') {
                await page.locator('#layout-focus-btn').click();
                assert.strictEqual(await page.locator('.sidebar').isVisible(), false);
                await page.locator('#layout-focus-btn').click();
                assert.strictEqual(await page.locator('.sidebar').isVisible(), true);
            }
            await host.getByRole('button', { name: 'Ajustar página à tela', exact: true }).click();
            await host.locator('.editor-outline-item').filter({ hasText: 'Receita por região' }).click();
            await host.getByLabel('Página', { exact: true }).selectOption('1');
            await page.screenshot({path:'/tmp/pdf-'+name+'-editor-redesign.png',fullPage:true});
            console.log(JSON.stringify({name,errors,blocks:await host.locator('.editor-block').count(),zoom:await host.locator('.editor-zoom-value').textContent()}));
            await page.close();
        }
    } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
