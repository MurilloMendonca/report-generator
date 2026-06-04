const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

let documentRef = null;

class TextNode {
    constructor(text) {
        this.tagName = '#text';
        this.textContent = String(text ?? '');
        this.children = [];
    }
}

class Element {
    constructor(tagName) {
        this.tagName = String(tagName || 'div').toUpperCase();
        this.children = [];
        this.dataset = {};
        this.style = {
            setProperty: (key, value) => {
                this.style[key] = value;
            },
        };
        this.classList = {
            add: (...classes) => {
                const current = new Set(String(this.className || '').split(/\s+/).filter(Boolean));
                classes.forEach(cls => current.add(cls));
                this.className = Array.from(current).join(' ');
            },
            remove: (...classes) => {
                const current = new Set(String(this.className || '').split(/\s+/).filter(Boolean));
                classes.forEach(cls => current.delete(cls));
                this.className = Array.from(current).join(' ');
            },
            toggle: (cls, force) => {
                const current = new Set(String(this.className || '').split(/\s+/).filter(Boolean));
                const shouldAdd = force === undefined ? !current.has(cls) : Boolean(force);
                if (shouldAdd) current.add(cls);
                else current.delete(cls);
                this.className = Array.from(current).join(' ');
                return shouldAdd;
            },
            contains: (cls) => String(this.className || '').split(/\s+/).filter(Boolean).includes(cls),
        };
        this.className = '';
        this.id = '';
        this.href = '';
        this.value = '';
        this.checked = false;
        this.scrollTop = 0;
        this.clientHeight = 0;
        this.scrollHeight = 0;
        this._text = '';
        this._html = '';
    }

    appendChild(node) {
        if (node && node.tagName === '#fragment') {
            node.children.forEach(child => this.appendChild(child));
            return node;
        }
        if (node && typeof node === 'object') {
            node.parentElement = this;
        }
        this.children.push(node);
        return node;
    }

    removeChild(node) {
        this.children = this.children.filter(child => child !== node);
        if (node && typeof node === 'object') {
            node.parentElement = null;
        }
        return node;
    }

    replaceChildren(...nodes) {
        this.children = [];
        nodes.forEach(node => this.appendChild(node));
    }

    addEventListener() {}

    focus() {
        if (documentRef) {
            documentRef.activeElement = this;
        }
    }

    setAttribute(name, value) {
        this[name] = String(value);
    }

    getAttribute(name) {
        return this[name];
    }

    getBoundingClientRect() {
        const parseSize = (value) => {
            const parsed = parseFloat(String(value ?? '0'));
            return Number.isFinite(parsed) ? parsed : 0;
        };
        return {
            left: parseSize(this.style.left),
            top: parseSize(this.style.top),
            width: this.offsetWidth,
            height: this.offsetHeight,
            right: parseSize(this.style.left) + this.offsetWidth,
            bottom: parseSize(this.style.top) + this.offsetHeight,
        };
    }

    get offsetWidth() {
        const parsed = parseFloat(String(this.style.width ?? '0'));
        return Number.isFinite(parsed) ? parsed : 0;
    }

    get offsetHeight() {
        const parsed = parseFloat(String(this.style.height ?? '0'));
        return Number.isFinite(parsed) ? parsed : 0;
    }

    set textContent(value) {
        this._text = String(value ?? '');
        this.children = [];
    }

    get textContent() {
        if (this.children.length) {
            return this.children.map(child => child.textContent || '').join('');
        }
        return this._text;
    }

    set innerHTML(value) {
        this._html = String(value ?? '');
        this.children = [];
    }

    get innerHTML() {
        return this._html || '';
    }

    querySelector(selector) {
        return queryFirst(this, selector);
    }

    querySelectorAll(selector) {
        const results = [];
        queryAll(this, selector, results);
        return results;
    }

    closest(selector) {
        let current = this;
        while (current) {
            if (matchesSelector(current, selector)) return current;
            current = current.parentElement || null;
        }
        return null;
    }
}

class DocumentFragment extends Element {
    constructor() {
        super('#fragment');
    }
}

function matchesSelector(node, selector) {
    if (!node || node.tagName === '#text' || node.tagName === '#fragment') return false;
    const classMatch = selector.match(/^\.([a-zA-Z0-9_-]+)/);
    if (classMatch) {
        const className = classMatch[1];
        const classes = String(node.className || '').split(/\s+/).filter(Boolean);
        if (!classes.includes(className)) return false;
    }
    const dataMatch = selector.match(/\[data-([a-zA-Z0-9_-]+)="([^"]*)"\]/);
    if (dataMatch) {
        const key = dataMatch[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        if (String(node.dataset?.[key] ?? '') !== dataMatch[2]) return false;
    }
    const tagMatch = selector.match(/^[a-zA-Z0-9_-]+/);
    if (tagMatch && !selector.startsWith('.')) {
        if (node.tagName.toLowerCase() !== tagMatch[0].toLowerCase()) return false;
    }
    return true;
}

function queryFirst(root, selector) {
    for (const child of root.children || []) {
        if (matchesSelector(child, selector)) return child;
        const nested = queryFirst(child, selector);
        if (nested) return nested;
    }
    return null;
}

function queryAll(root, selector, results) {
    for (const child of root.children || []) {
        if (matchesSelector(child, selector)) results.push(child);
        queryAll(child, selector, results);
    }
}

const nodes = new Map();
const documentStub = {
    activeElement: null,
    createElement(tag) {
        return new Element(tag);
    },
    createElementNS(_ns, tag) {
        return new Element(tag);
    },
    createDocumentFragment() {
        return new DocumentFragment();
    },
    createTextNode(text) {
        return new TextNode(text);
    },
    getElementById(id) {
        if (!nodes.has(id)) {
            const el = new Element('div');
            el.id = id;
            nodes.set(id, el);
        }
        return nodes.get(id);
    },
    querySelectorAll(selector) {
        const results = [];
        nodes.forEach(node => queryAll(node, selector, results));
        return results;
    },
    querySelector(selector) {
        for (const node of nodes.values()) {
            const found = queryFirst(node, selector);
            if (found) return found;
        }
        return null;
    },
};
documentRef = documentStub;

const context = {
    console,
    assert,
    document: documentStub,
    window: {},
    structuredClone: global.structuredClone,
    Event: class {},
    Utils: {
        makeDraggable() {},
        formatJson(value) {
            return JSON.stringify(value, null, 2);
        },
    },
    API: {
        authHeaders() {
            return {};
        },
    },
    MM_TO_PX: 3.7795275591,
};
context.window = context;

vm.createContext(context);

const uiSource = fs.readFileSync(require.resolve('./../static/js/ui.js'), 'utf8') + '\nthis.UI = UI;';
vm.runInContext(uiSource, context);

const appSource = fs.readFileSync(require.resolve('./../static/js/app.js'), 'utf8') + '\nthis.App = App;';
vm.runInContext(appSource, context);

const templatesSource = fs.readFileSync(require.resolve('./../static/js/templates.js'), 'utf8') + '\nthis.TemplateEditor = TemplateEditor;';
vm.runInContext(templatesSource, context);

const malicious = '<img src=x onerror=alert(1)><script>alert(2)</script>';

const recentSessions = context.document.getElementById('recent-sessions');
context.UI.renderRecentSessions([{
    job_id: 'job-1',
    initial_prompt: malicious,
    status: malicious,
    file_count: 1,
    updated_at: '2026-01-01T00:00:00Z',
}]);
assert.strictEqual(recentSessions.children[0].textContent.includes(malicious), true);
assert.strictEqual(recentSessions.children[0].children.some(child => child.tagName === 'SCRIPT' || child.tagName === 'IMG'), false);

const logs = context.document.getElementById('log-container');
context.UI.renderLogs([malicious]);
assert.strictEqual(logs.children[0].textContent.includes(malicious), true);
assert.strictEqual(logs.children[0].children.some(child => child.tagName === 'IMG' || child.tagName === 'SCRIPT'), false);

context.App.models = {
    deepseek: [{ id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' }],
    gemini: [{ id: 'gemini-3.1-flash-lite-preview', label: 'Gemini 3.1 Flash Lite Preview' }],
};
const modelSelect = context.document.getElementById('model_name');
context.document.getElementById('provider').value = 'deepseek';
context.App.updateModels();
assert.strictEqual(modelSelect.children.length > 0, true);
assert.strictEqual(modelSelect.children[0].value, 'deepseek-v4-flash');

const settingsModelSelect = context.document.getElementById('s-model-name');
context.document.getElementById('s-provider').value = 'gemini';
context.App.updateSettingsModels();
assert.strictEqual(settingsModelSelect.children.length > 0, true);
assert.strictEqual(settingsModelSelect.children[0].value, 'gemini-3.1-flash-lite-preview');

const previewTabs = context.App.renderFilePreviewTabs(0, 'original');
assert.strictEqual(previewTabs.children.length, 2);
assert.strictEqual(previewTabs.children[0].textContent, 'Original');

context.UI.renderTemplatePreviews([
    {
        id: 'template-a',
        name: 'Resumo',
        description: 'Template de teste',
        slots: [{ page: 1, x: 10, y: 10, w: 50, h: 30, type: 'text' }],
    },
], 'template-a');
const previewContainer = context.document.getElementById('layout-template-previews');
assert.strictEqual(previewContainer.children.length > 0, true);
assert.strictEqual(previewContainer.children[1].textContent, 'Resumo');

context.TemplateEditor.currentTemplate = {
    id: 'template-x',
    name: 'Template X',
    slots: [{ slot_id: 'slot-1', name: 'Texto', description: '', type: 'text', page: 1, x: 10, y: 20, w: 50, h: 30 }],
};
context.TemplateEditor.selectedSlotId = 'slot-1';
context.TemplateEditor.renderCanvas();
context.TemplateEditor.renderInspector();
const templateCanvas = context.document.getElementById('template-canvas');
assert.strictEqual(templateCanvas.children.length > 0, true);
const selectableSlot = templateCanvas.querySelector('.template-slot[data-slot-id="slot-1"]');
assert.strictEqual(selectableSlot.textContent.includes('Texto'), true);
assert.strictEqual(selectableSlot.tabIndex, 0);
assert.strictEqual(selectableSlot.getAttribute('role'), 'button');
assert.strictEqual(selectableSlot.getAttribute('aria-label').includes('Use as setas para mover'), true);
context.TemplateEditor.moveSelectedSlot(2, 3);
assert.strictEqual(selectableSlot.style.left, `${(10 + 2) * context.MM_TO_PX}px`);
assert.strictEqual(selectableSlot.style.top, `${(20 + 3) * context.MM_TO_PX}px`);
assert.strictEqual(templateCanvas.querySelector('.a4-page').style['--page-label'], '"Página do template 1"');

context.TemplateEditor.newTemplate();
context.TemplateEditor.addSlot('text');

const chat = context.document.getElementById('chat-history');
context.UI.renderRefinementChat({
    initial_prompt: malicious,
    refinement_messages: [{
        role: 'user',
        content: malicious,
        kind: 'message',
    }],
});
assert.strictEqual(chat.textContent.includes(malicious), true);
assert.strictEqual(chat.children.some(child => child.tagName === 'SCRIPT' || child.tagName === 'IMG'), false);

const canvas = context.document.getElementById('template-canvas');
context.TemplateEditor.currentTemplate = {
    id: 'tmpl-1',
    name: 'Template',
    slots: [{
        slot_id: 'slot-1',
        name: malicious,
        description: malicious,
        type: 'text',
        page: 1,
        x: 10,
        y: 10,
        w: 100,
        h: 40,
    }],
};
context.TemplateEditor.selectedSlotId = 'slot-1';
context.TemplateEditor.renderCanvas();
const slot = canvas.querySelector('.template-slot[data-slot-id="slot-1"]');
assert(slot, 'template slot should render');
assert.strictEqual(slot.children[0].tagName, 'SPAN');
assert.strictEqual(slot.children[1].tagName, 'SMALL');
assert.strictEqual(slot.children.some(child => child.tagName === 'SCRIPT' || child.tagName === 'IMG'), false);

const focusTarget = context.document.createElement('button');
context.document.activeElement = context.document.createElement('input');
context.App.filePreviewFocusTarget = focusTarget;
context.App.restoreFilePreviewFocus();
assert.strictEqual(context.document.activeElement, focusTarget);

context.document.activeElement = context.document.createElement('textarea');
context.App.indicatorModalFocusTarget = focusTarget;
context.App.restoreIndicatorModalFocus();
assert.strictEqual(context.document.activeElement, focusTarget);

process.stdout.write('ok');
