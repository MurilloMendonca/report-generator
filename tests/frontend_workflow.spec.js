const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', textContent: '', hidden: true, open: false, style: {}, disabled: false });
    return elements.get(id);
};
let settingsUpdates = 0;
let scheduled = [];
const ui = new Proxy({}, { get: (_target, key) => (...args) => {
    if (key === 'updateSettingsForm') settingsUpdates += 1;
} });
const context = vm.createContext({
    document: { getElementById: element },
    window: { history: { replaceState() {} } },
    UI: ui, FormData: class {}, URL: { revokeObjectURL() {} },
    setTimeout: callback => { scheduled.push(callback); return scheduled.length; },
    clearTimeout() {},
});
for (const file of ['api.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', file), 'utf8'), context);
}
const api = vm.runInContext('API', context);
const app = vm.runInContext('App', context);
const flush = () => new Promise(resolve => setImmediate(resolve));

async function main() {
    await assert.rejects(api.parseResponse({ ok: false, status: 422, json: async () => ({ detail: [{ msg: 'Invalid model' }] }) }), /Invalid model/);
    await assert.rejects(api.parseResponse({ ok: false, status: 401, json: async () => ({ detail: 'Unauthorized' }) }), error => error.status === 401 && /expirou/.test(error.message));

    const button = element('submit');
    const event = { preventDefault() {}, target: { querySelector: () => button } };
    element('file').files = [{ name: 'data.json' }];
    let rejectUpload;
    let uploadCalls = 0;
    api.startSession = () => { uploadCalls += 1; return new Promise((_resolve, reject) => { rejectUpload = reject; }); };
    const upload = app.handleStartSession(event);
    await app.handleStartSession(event);
    assert.strictEqual(uploadCalls, 1, 'double submission must not create duplicate sessions');
    rejectUpload(new Error('Unsupported file'));
    await upload;
    assert.strictEqual(button.disabled, false);
    assert.strictEqual(element('workspace-notice').textContent, 'Unsupported file');

    element('file').files = [];
    await app.handleStartSession(event);
    assert.strictEqual(uploadCalls, 1, 'empty imports must be rejected before making a request');
    assert(element('workspace-notice').textContent.includes('API JSON'));
    element('api-url').value = 'https://example.com/data';
    api.startSession = async () => { throw new Error('API unavailable'); };
    await app.handleStartSession(event);
    assert.strictEqual(element('workspace-notice').textContent, 'API unavailable', 'API-only reports can be submitted');

    const session = { job_id: 'one', status: 'Working', settings: {}, artifacts: [], files: [] };
    app.currentSession = session;
    app.updateSettingsModels = () => {};
    app.updateUI(session);
    app.updateUI(session);
    assert.strictEqual(settingsUpdates, 1, 'unchanged polling must preserve form values');
    app.updateUI({ ...session, llm_usage: { last_fallback: { provider: 'anthropic', served_model: 'claude-sonnet-5-5' } } });
    assert(element('workspace-notice').textContent.includes('claude-sonnet-5-5'), 'provider fallback must identify the serving model');
    app.settingsDirty = true;
    app.updateUI({ ...session, settings: { title: 'server title' } });
    assert.strictEqual(settingsUpdates, 1, 'polling must preserve unsaved settings');

    let resolveFirst;
    let resolveSecond;
    let calls = 0;
    api.getSession = () => {
        calls += 1;
        return new Promise(resolve => { if (calls === 1) resolveFirst = resolve; else resolveSecond = resolve; });
    };
    app.startPolling();
    assert.strictEqual(calls, 1);
    assert.strictEqual(scheduled.length, 0, 'polling must wait for the response');
    app.startPolling();
    assert.strictEqual(calls, 2);
    resolveFirst({ ...session, status: 'stale' });
    await flush();
    assert.notStrictEqual(app.currentSession.status, 'stale', 'an earlier poll cannot replace the current run');
    resolveSecond(session);
    await flush();
    assert.strictEqual(scheduled.length, 1);
    api.getSession = async () => ({ ...session, current_step: 'completed' });
    await scheduled.pop()();
    assert.strictEqual(scheduled.length, 0, 'terminal sessions must stop polling');
    assert.strictEqual(app.isTerminalSession({ current_step: 'awaiting_clarification' }), true, 'clarification pauses polling until the user replies');
    assert.strictEqual(app.isTerminalSession({ current_step: 'refinement_routing', status: 'Relatório concluído anteriormente' }), false, 'current stage takes precedence over old status text');

    assert.strictEqual(api.explorerChartUrl('one', 'a'.repeat(32), 'chart_1'), `/api/sessions/one/explore/${'a'.repeat(32)}/charts/chart_1`);
    assert.strictEqual(api.explorerChartUrl('one', 'a'.repeat(32), '../chart_1'), null);
    assert.strictEqual(api.explorerChartUrl('../one', 'a'.repeat(32), 'chart_1'), null);
    assert.strictEqual(api.explorerChartUrl('one', 'javascript:alert(1)', 'chart_1'), null);

    app.currentSession = { ...session, explore_history: [] };
    element('explore-input').value = 'Show a chart';
    let resolveExplore;
    let exploreCalls = 0;
    api.exploreData = () => { exploreCalls += 1; return new Promise(resolve => { resolveExplore = resolve; }); };
    const explore = app.handleExplore(event);
    await app.handleExplore(event);
    assert.strictEqual(exploreCalls, 1, 'pending explorations cannot be submitted twice');
    assert.strictEqual(app.explorePending.prompt, 'Show a chart');
    app.updateUI({ ...app.currentSession, explore_history: [] });
    assert.strictEqual(app.explorePending.prompt, 'Show a chart', 'polling must preserve the pending request');
    const history = [{role:'user',content:'Show a chart'},{role:'assistant',content:'Answer',charts:[{id:'chart_1'}]}];
    resolveExplore({answer:'Answer',history});
    await explore;
    assert.strictEqual(app.explorePending, null);
    assert.strictEqual(app.currentSession.explore_history, history);

    element('explore-input').value = 'Failed query';
    api.exploreData = async () => { const error = new Error('Query failed'); error.details = {history:[...history,{role:'assistant',content:'Query failed',status:'error'}]}; throw error; };
    await app.handleExplore(event);
    assert.strictEqual(element('explore-input').value, 'Failed query', 'failed questions remain available for retry');
    assert.strictEqual(app.currentSession.explore_history.at(-1).status, 'error');

    element('explore-input').value = 'Old session query';
    api.exploreData = () => new Promise(resolve => { resolveExplore = resolve; });
    const stale = app.handleExplore(event);
    app.currentSession = { job_id:'two', explore_history:[] };
    resolveExplore({answer:'Stale answer',history});
    await stale;
    assert.strictEqual(app.currentSession.explore_history.length, 0, 'late results cannot enter another session');
    assert.strictEqual(app.explorePending, null);

    const details = {message:'Try again',history:[]};
    await assert.rejects(api.parseResponse({ok:false,status:422,json:async () => ({detail:details})}), error => error.details === details);
}

main().then(() => console.log('ok')).catch(error => { console.error(error); process.exitCode = 1; });
