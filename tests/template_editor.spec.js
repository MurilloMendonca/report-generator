const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
    constructor() {
        this.children = [];
        this.dataset = {};
        this.style = { setProperty: (name, value) => { this.style[name] = value; } };
        this.className = '';
        this.classList = { add() {}, toggle() {} };
        this.attributes = {};
        this.value = '';
    }
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return this.text || ''; }
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
    replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener() {}
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
        const className = (selector.match(/^\.([\w-]+)/) || [])[1];
        const page = (selector.match(/data-page="(\d+)"/) || [])[1];
        const matches = child => child.className.split(' ').includes(className) && (!page || String(child.dataset.page) === page);
        return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
}

const template = {
    id: 'executive', name: 'Executive layout', description: '',
    slots: [
        { slot_id: 'intro', name: 'Introduction', type: 'text', page: 1, x: 10, y: 30, w: 190, h: 40 },
        { slot_id: 'chart', name: 'Chart', type: 'graphic', page: 1, x: 10, y: 80, w: 90, h: 60 },
    ],
};

async function loadEditor(templates, failure = null) {
    const root = path.join(__dirname, '..');
    const html = fs.readFileSync(path.join(root, 'static', 'templates.html'), 'utf8');
    const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
    const document = {
        getElementById: id => nodes.get(id),
        createElement: () => new Element(),
        addEventListener() {},
        querySelectorAll: selector => nodes.get('template-canvas').querySelectorAll(selector),
    };
    const context = vm.createContext({
        document, window: {}, structuredClone, confirm: () => true,
        fetch: async url => ({
            ok: !failure || url === '/api/auth/me',
            status: failure && url !== '/api/auth/me' ? 500 : 200,
            json: async () => url === '/api/auth/me' ? { user: { email: 'a@example.com' } } : failure ? { detail: failure } : { templates },
        }),
    });
    // Load only the dependencies declared by the real page; no injected UI helper.
    for (const match of html.matchAll(/<script src="([^"]+)"/g)) {
        vm.runInContext(fs.readFileSync(path.join(root, match[1]), 'utf8'), context);
    }
    await context.window.onload();
    return { editor: vm.runInContext('TemplateEditor', context), utils: vm.runInContext('Utils', context), nodes, document };
}

async function main() {
    const loaded = await loadEditor([template]);
    assert.strictEqual(loaded.nodes.get('app-shell').style.display, 'flex');
    assert.strictEqual(loaded.nodes.get('template-select').children.length, 1);
    assert.strictEqual(loaded.document.querySelectorAll('.a4-page').length, 1);
    assert.strictEqual(loaded.document.querySelectorAll('.template-slot').length, 2);
    assert.strictEqual(loaded.editor.currentTemplate.id, 'executive');
    loaded.editor.editor.select('chart');
    assert.strictEqual(loaded.editor.editor.properties.hidden, false);
    assert.strictEqual(loaded.editor.editor.inputs.label.value, 'Chart');

    let dragStops = 0;
    const draggable = new Element();
    loaded.utils.makeDraggable(draggable, () => { dragStops += 1; });
    draggable.onmousedown({ target: { tagName: 'SPAN', className: '' }, clientX: 0, clientY: 0 });
    loaded.document.onmouseup();
    assert.strictEqual(dragStops, 1);
    assert.strictEqual(loaded.document.onmouseup, null, 'later clicks must not repeat a completed drag');

    const empty = await loadEditor([]);
    assert.strictEqual(empty.document.querySelectorAll('.a4-page').length, 1);
    assert.strictEqual(empty.document.querySelectorAll('.template-slot').length, 0);

    const failed = await loadEditor([], 'Could not load layout library');
    assert.strictEqual(failed.nodes.get('save-status').textContent, 'Could not load layout library');
    assert.strictEqual(failed.nodes.get('save-status').attributes.role, 'alert');
    assert.strictEqual(failed.editor.currentTemplate, null);
}

main().then(() => console.log('ok')).catch(error => { console.error(error); process.exitCode = 1; });
