// Geometry is stored in millimeters; zoom only affects the page presentation.
class LayoutState {
    static bounds(item) {
        const number = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
        const w = Math.round(Math.max(10, Math.min(210, number(item.w, 90))));
        const h = Math.round(Math.max(10, Math.min(255, number(item.h, 40))));
        return {
            ...item,
            page: Math.max(1, Math.min(100, Math.round(number(item.page, 1)))),
            x: Math.round(Math.max(0, Math.min(210 - w, number(item.x, 10)))),
            y: Math.round(Math.max(30, Math.min(285 - h, number(item.y, 30)))),
            w, h,
        };
    }

    constructor(items = []) { this.reset(items); }

    snapshot() { return JSON.stringify({ items: this.items, pages: this.pages }); }
    restore(snapshot) { const value = JSON.parse(snapshot); this.items = value.items; this.pages = value.pages; }
    reset(items, pages = 1) {
        this.items = items.map(item => LayoutState.bounds({ ...item }));
        this.pages = Math.max(1, Math.min(100, pages), ...this.items.map(item => item.page));
        this.past = [];
        this.future = [];
        this.saved = this.snapshot();
    }
    get dirty() { return this.snapshot() !== this.saved; }
    record(before) {
        if (before === this.snapshot()) return false;
        this.past.push(before);
        if (this.past.length > 60) this.past.shift();
        this.future = [];
        return true;
    }
    change(action) {
        const before = this.snapshot();
        action(this);
        this.items = this.items.map(LayoutState.bounds);
        this.pages = Math.max(this.pages, 1, ...this.items.map(item => item.page));
        return this.record(before);
    }
    undo() {
        if (!this.past.length) return false;
        this.future.push(this.snapshot());
        this.restore(this.past.pop());
        return true;
    }
    redo() {
        if (!this.future.length) return false;
        this.past.push(this.snapshot());
        this.restore(this.future.pop());
        return true;
    }
}

class LayoutEditor {
    constructor(container, options = {}) {
        this.container = container;
        this.options = options;
        this.state = new LayoutState();
        this.selectedId = null;
        this.zoom = 1;
        this.fit = true;
        this.snap = true;
        this.available = [];
        this.blocks = new Map();
        this.pageElements = new Map();
        this.mount();
        this.container.addEventListener('keydown', event => this.keydown(event));
        this.container.addEventListener('pointerdown', event => this.pointerdown(event));
        this.container.addEventListener('pointermove', event => this.pointermove(event));
        this.container.addEventListener('pointerup', event => this.pointerup(event));
        this.container.addEventListener('pointercancel', event => this.cancelDrag(event));
        this.container.addEventListener('lostpointercapture', event => this.cancelDrag(event));
        if (typeof ResizeObserver !== 'undefined') {
            this.observer = new ResizeObserver(() => { if (this.fit && !this.drag) this.fitPage(); });
            this.observer.observe(this.viewport);
        }
    }

    element(tag, className = '', text = '') {
        const element = document.createElement(tag);
        element.className = className;
        if (text) element.textContent = text;
        return element;
    }

    button(label, title, action, className = '') {
        const button = this.element('button', `editor-button ${className}`, label);
        button.type = 'button';
        button.title = title;
        button.setAttribute('aria-label', title);
        button.addEventListener('click', action);
        return button;
    }

    mount() {
        this.container.replaceChildren();
        this.container.classList.add('layout-editor');
        const toolbar = this.element('div', 'editor-toolbar');
        const history = this.element('div', 'editor-toolbar-group');
        this.undoButton = this.button('↶', 'Desfazer (Ctrl+Z)', () => this.undo());
        this.redoButton = this.button('↷', 'Refazer (Ctrl+Shift+Z)', () => this.redo());
        history.appendChild(this.undoButton);
        history.appendChild(this.redoButton);
        toolbar.appendChild(history);
        toolbar.appendChild(this.button('+ Página', 'Adicionar página', () => this.addPage()));
        const zoom = this.element('div', 'editor-toolbar-group editor-zoom');
        zoom.appendChild(this.button('−', 'Diminuir zoom', () => this.setZoom(this.zoom - 0.15)));
        this.zoomLabel = this.element('span', 'editor-zoom-value', '100%');
        zoom.appendChild(this.zoomLabel);
        zoom.appendChild(this.button('+', 'Aumentar zoom', () => this.setZoom(this.zoom + 0.15)));
        zoom.appendChild(this.button('Ajustar', 'Ajustar página à tela', () => { this.fit = true; this.fitPage(); }));
        toolbar.appendChild(zoom);
        const snapLabel = this.element('label', 'editor-toggle');
        const snap = this.element('input');
        snap.type = 'checkbox';
        snap.checked = true;
        snap.addEventListener('change', () => { this.snap = snap.checked; });
        snapLabel.appendChild(snap);
        snapLabel.appendChild(this.element('span', '', 'Encaixar na grade'));
        toolbar.appendChild(snapLabel);
        this.changeLabel = this.element('span', 'editor-change-status', 'Sem alterações');
        this.changeLabel.setAttribute('role', 'status');
        toolbar.appendChild(this.changeLabel);
        this.container.appendChild(toolbar);

        const body = this.element('div', 'editor-body');
        this.viewport = this.element('div', 'editor-viewport');
        this.viewport.setAttribute('aria-label', 'Páginas do layout');
        this.viewport.addEventListener('click', event => {
            if (!event.target.closest('.layout-block')) this.select(null);
        });
        this.sheets = this.element('div', 'editor-sheets');
        this.sheets.setAttribute('id', `${this.container.id}-sheets`);
        this.viewport.appendChild(this.sheets);
        body.appendChild(this.viewport);
        this.inspector = this.element('aside', 'editor-inspector');
        this.inspector.setAttribute('aria-label', 'Propriedades do bloco');
        body.appendChild(this.inspector);
        this.container.appendChild(body);
        const footer = this.element('div', 'editor-footer', 'Arraste para mover · Use as alças para redimensionar · Setas: 1 mm · Shift + setas: 5 mm · Alt: sem encaixe');
        this.container.appendChild(footer);
        this.makeInspector();
    }

    makeInspector() {
        this.inspector.appendChild(this.element('h3', '', 'Blocos da página'));
        this.outline = this.element('div', 'editor-outline');
        this.inspector.appendChild(this.outline);
        if (this.options.allowAdd) {
            const actions = this.element('div', 'editor-add-actions');
            for (const [type, label] of [['text', '+ Texto'], ['graphic', '+ Gráfico']]) {
                actions.appendChild(this.button(label, `Adicionar bloco de ${type === 'text' ? 'texto' : 'gráfico'}`, () => this.addBlock(type)));
            }
            this.inspector.appendChild(actions);
        } else {
            this.addSelect = this.element('select', 'editor-add-select');
            this.addSelect.setAttribute('aria-label', 'Adicionar componente ao layout');
            this.addSelect.addEventListener('change', () => {
                const artifact = this.available.find(item => item.id === this.addSelect.value);
                if (artifact) this.addBlock(artifact.type, artifact);
            });
            this.inspector.appendChild(this.addSelect);
        }
        this.emptyInspector = this.element('p', 'editor-empty-selection', 'Clique em um bloco na página ou na lista para ajustar sua posição e tamanho.');
        this.inspector.appendChild(this.emptyInspector);
        this.properties = this.element('div', 'editor-properties');
        this.inspector.appendChild(this.properties);
        this.inputs = {};
        const field = (name, label, type = 'number') => {
            const wrapper = this.element('label', 'editor-field');
            wrapper.appendChild(this.element('span', '', label));
            const input = this.element(type === 'select' ? 'select' : 'input');
            if (type !== 'select') input.type = type;
            input.setAttribute('aria-label', label);
            if (type === 'number') { input.min = name === 'page' ? 1 : 0; input.step = 1; }
            input.addEventListener('change', () => this.updateSelected(name, type === 'number' ? Number(input.value) : input.value));
            wrapper.appendChild(input);
            this.inputs[name] = input;
            return wrapper;
        };
        if (this.options.allowAdd) {
            this.properties.appendChild(field('label', 'Nome do bloco', 'text'));
            this.properties.appendChild(field('type', 'Tipo de bloco', 'select'));
            for (const [value, label] of [['text', 'Texto'], ['graphic', 'Gráfico']]) {
                const option = this.element('option', '', label);
                option.value = value;
                this.inputs.type.appendChild(option);
            }
            this.properties.appendChild(field('description', 'Descrição', 'text'));
        } else {
            this.selectedName = this.element('h4', 'editor-selected-name');
            this.properties.appendChild(this.selectedName);
        }
        this.properties.appendChild(field('page', 'Página', 'select'));
        const geometry = this.element('div', 'editor-geometry');
        for (const [name, label] of [['x', 'X (mm)'], ['y', 'Y (mm)'], ['w', 'Largura (mm)'], ['h', 'Altura (mm)']]) geometry.appendChild(field(name, label));
        this.properties.appendChild(geometry);
        this.properties.appendChild(this.element('div', 'editor-field-heading', 'Alinhar na página'));
        const alignment = this.element('div', 'editor-align-actions');
        for (const [value, label] of [['left', 'Esquerda'], ['center', 'Centro'], ['right', 'Direita']]) {
            alignment.appendChild(this.button(label, `Alinhar à ${label.toLowerCase()}`, () => this.align(value)));
        }
        this.properties.appendChild(alignment);
        this.properties.appendChild(this.button('Ocupar largura', 'Usar a largura disponível entre as margens', () => {
            this.modifySelected(item => { item.x = 10; item.w = 190; });
        }));
        this.properties.appendChild(this.element('p', 'editor-safe-note', 'A área pontilhada indica as margens do PDF. O topo está reservado para o título.'));
        if (this.options.onEdit) this.properties.appendChild(this.button('Editar conteúdo', 'Editar o conteúdo deste componente', () => this.options.onEdit(this.selectedId)));
        if (this.options.allowAdd) this.properties.appendChild(this.button('Duplicar bloco', 'Duplicar o bloco selecionado', () => this.duplicate()));
        this.properties.appendChild(this.button(this.options.allowAdd ? 'Excluir bloco' : 'Retirar do layout', 'Retirar o bloco selecionado do layout', () => this.remove(), 'editor-danger'));
    }

    setItems(items, { force = false, available = [] } = {}) {
        const signature = JSON.stringify(items);
        this.available = available;
        if (!force && (this.state.dirty || this.drag || signature === this.remoteSignature)) return;
        this.remoteSignature = signature;
        this.state.reset(items);
        this.selectedId = null;
        this.render();
    }

    getItems() { return this.state.items.map(item => ({ ...item })); }
    markSaved() { this.state.saved = this.state.snapshot(); this.refreshControls(); }
    get dirty() { return this.state.dirty; }
    selected() { return this.state.items.find(item => item.id === this.selectedId); }

    render() {
        this.sheets.replaceChildren();
        this.pageElements.clear();
        this.blocks.clear();
        for (let page = 1; page <= this.state.pages; page++) {
            const sheet = this.element('section', 'editor-sheet');
            const heading = this.element('div', 'editor-page-heading');
            heading.appendChild(this.element('span', '', `Página ${page}`));
            heading.appendChild(this.element('small', '', 'A4 · 210 × 297 mm'));
            sheet.appendChild(heading);
            const frame = this.element('div', 'editor-page-frame');
            const paper = this.element('div', 'a4-page editor-page');
            paper.dataset.page = String(page);
            paper.setAttribute('aria-label', `Página ${page}`);
            const margins = this.element('div', 'editor-page-margins');
            paper.appendChild(margins);
            if (page === 1) paper.appendChild(this.element('span', 'editor-title-zone', 'Área do título'));
            frame.appendChild(paper);
            sheet.appendChild(frame);
            this.sheets.appendChild(sheet);
            this.pageElements.set(page, { sheet, frame, paper });
        }
        for (const item of this.state.items) {
            const block = this.element('div', `layout-block editor-block ${this.options.allowAdd ? 'template-slot' : 'report-layout-block'} editor-block-${item.type === 'text' ? 'text' : 'graphic'}`);
            block.dataset.itemId = item.id;
            if (this.options.allowAdd) block.dataset.slotId = item.id;
            block.tabIndex = 0;
            block.setAttribute('role', 'button');
            block.setAttribute('aria-label', `${item.label || 'Bloco'}, página ${item.page}. Use as setas para mover.`);
            const badge = this.element('small', 'editor-block-kind', item.type === 'text' ? 'TEXTO' : 'VISUAL');
            block.appendChild(badge);
            block.appendChild(this.element('span', 'editor-block-label', item.label || 'Sem título'));
            const measure = this.element('span', 'editor-block-measure');
            block.appendChild(measure);
            for (const direction of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']) {
                const handle = this.element('span', `editor-resize-handle editor-resize-${direction}`);
                handle.dataset.resize = direction;
                handle.setAttribute('aria-hidden', 'true');
                block.appendChild(handle);
            }
            block.addEventListener('click', event => { event.stopPropagation(); this.select(item.id); });
            block.addEventListener('focus', () => this.select(item.id));
            block.addEventListener('dblclick', () => { if (this.options.onEdit) this.options.onEdit(item.id); });
            this.blocks.set(item.id, { block, measure });
            this.paint(item);
        }
        this.refreshInspector();
        this.refreshOutline();
        this.refreshControls();
        if (this.fit) this.fitPage(); else this.applyZoom();
    }

    paint(item) {
        const entry = this.blocks.get(item.id);
        if (!entry) return;
        const { block, measure } = entry;
        const paper = this.pageElements.get(item.page)?.paper;
        if (paper && block.parentElement !== paper) paper.appendChild(block);
        for (const [property, name] of [['left', 'x'], ['top', 'y'], ['width', 'w'], ['height', 'h']]) block.style[property] = `${item[name] * MM_TO_PX}px`;
        measure.textContent = `${item.w} × ${item.h} mm`;
        block.classList.toggle('selected', item.id === this.selectedId);
        block.classList.toggle('editor-block-compact', item.h < 28 || item.w < 45);
    }

    select(id, reveal = false) {
        this.selectedId = id;
        for (const [key, { block }] of this.blocks) block.classList.toggle('selected', key === id);
        this.refreshInspector();
        this.refreshOutline();
        this.options.onSelect?.(this.selected());
        if (reveal && id) {
            const block = this.blocks.get(id)?.block;
            block?.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
            block?.focus?.({ preventScroll: true });
        }
    }

    refreshInspector() {
        const item = this.selected();
        this.properties.hidden = !item;
        this.emptyInspector.hidden = Boolean(item);
        if (!item) return;
        this.inputs.page.replaceChildren();
        for (let page = 1; page <= this.state.pages; page++) {
            const option = this.element('option', '', `Página ${page}`);
            option.value = String(page);
            this.inputs.page.appendChild(option);
        }
        for (const [name, input] of Object.entries(this.inputs)) {
            if (document.activeElement !== input) input.value = String(item[name] ?? '');
        }
        if (this.selectedName) this.selectedName.textContent = item.label || 'Bloco selecionado';
    }

    refreshOutline() {
        this.outline.replaceChildren();
        if (!this.state.items.length) this.outline.appendChild(this.element('p', 'editor-empty-selection', 'Nenhum bloco no layout. Adicione um para começar.'));
        for (const item of this.state.items) {
            const button = this.button('', `Selecionar ${item.label || 'bloco'}`, () => this.select(item.id, true), 'editor-outline-item');
            button.setAttribute('aria-pressed', String(item.id === this.selectedId));
            button.appendChild(this.element('span', 'editor-outline-dot', item.type === 'text' ? 'T' : '▥'));
            button.appendChild(this.element('span', 'editor-outline-label', item.label || 'Sem título'));
            button.appendChild(this.element('small', '', `P${item.page}`));
            this.outline.appendChild(button);
        }
        if (this.addSelect) {
            this.addSelect.replaceChildren();
            const placeholder = this.element('option', '', '+ Adicionar componente');
            placeholder.value = '';
            this.addSelect.appendChild(placeholder);
            for (const item of this.available.filter(artifact => !this.state.items.some(block => block.id === artifact.id))) {
                const option = this.element('option', '', item.label);
                option.value = item.id;
                this.addSelect.appendChild(option);
            }
            this.addSelect.value = '';
        }
    }

    refreshControls() {
        this.undoButton.disabled = !this.state.past.length;
        this.redoButton.disabled = !this.state.future.length;
        this.changeLabel.textContent = this.dirty ? 'Alterações não salvas' : 'Sem alterações';
        this.changeLabel.classList.toggle('is-dirty', this.dirty);
    }

    changed() {
        this.refreshControls();
        this.options.onChange?.(this.getItems(), this.dirty);
    }

    commit(action) {
        if (!this.state.change(action)) return;
        if (!this.selected()) this.selectedId = null;
        this.render();
        this.changed();
    }

    modifySelected(action) {
        if (!this.selected()) return;
        this.commit(state => action(state.items.find(item => item.id === this.selectedId)));
    }

    updateSelected(name, value) {
        if (['x', 'y', 'w', 'h', 'page'].includes(name) && !Number.isFinite(Number(value))) return;
        this.modifySelected(item => { item[name] = value; });
        // Show any correction made to keep the block on the page.
        const item = this.selected();
        if (item && this.inputs[name]) this.inputs[name].value = String(item[name]);
        if (name === 'page') this.select(this.selectedId, true);
    }

    addPage() {
        this.commit(state => { if (state.pages < 100) state.pages += 1; });
        this.pageElements.get(this.state.pages)?.sheet.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    }
    newId() { return `slot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`; }
    addBlock(type, existing = null) {
        const page = this.selected()?.page || this.state.pages;
        const item = { id: existing?.id || this.newId(), label: existing?.label || (type === 'text' ? 'Bloco de texto' : 'Bloco visual'), type, description: '', page, x: 10, y: 30, w: type === 'text' ? 190 : 90, h: type === 'text' ? 40 : 60 };
        const pageItems = this.state.items.filter(block => block.page === page);
        item.y = Math.min(285 - item.h, Math.max(30, ...pageItems.map(block => block.y + block.h + 5)));
        this.selectedId = item.id;
        this.commit(state => state.items.push(item));
        this.select(item.id, true);
    }
    duplicate() {
        const item = this.selected();
        if (!item) return;
        const copy = { ...item, id: this.newId(), label: `${item.label} — cópia`, x: item.x + 5, y: item.y + 5 };
        this.selectedId = copy.id;
        this.commit(state => state.items.push(copy));
    }
    remove() { if (this.selected()) this.commit(state => { state.items = state.items.filter(item => item.id !== this.selectedId); }); }
    align(direction) {
        this.modifySelected(item => { item.x = direction === 'left' ? 10 : direction === 'right' ? 200 - item.w : (210 - item.w) / 2; });
    }
    undo() { if (this.state.undo()) { this.render(); this.changed(); this.blocks.get(this.selectedId)?.block.focus?.({ preventScroll: true }); } }
    redo() { if (this.state.redo()) { this.render(); this.changed(); this.blocks.get(this.selectedId)?.block.focus?.({ preventScroll: true }); } }

    setZoom(value) { this.fit = false; this.zoom = Math.max(0.3, Math.min(2, value)); this.applyZoom(); }
    fitPage() {
        if (!this.viewport.clientWidth || !this.viewport.clientHeight) return;
        this.zoom = Math.max(0.3, Math.min(1.5, (this.viewport.clientWidth - 56) / (210 * MM_TO_PX), (this.viewport.clientHeight - 66) / (297 * MM_TO_PX)));
        this.applyZoom();
    }
    applyZoom() {
        this.zoomLabel.textContent = `${Math.round(this.zoom * 100)}%`;
        for (const { frame, paper } of this.pageElements.values()) {
            paper.style.setProperty('--editor-zoom', this.zoom);
            frame.style.width = `${210 * MM_TO_PX * this.zoom}px`;
            frame.style.height = `${297 * MM_TO_PX * this.zoom}px`;
            paper.style.transform = `scale(${this.zoom})`;
        }
    }

    keydown(event) {
        if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
        const key = event.key;
        if ((event.ctrlKey || event.metaKey) && key.toLowerCase() === 'z') {
            event.preventDefault();
            if (event.shiftKey) this.redo(); else this.undo();
            return;
        }
        if (!this.selected()) return;
        if (key === 'Escape') { event.preventDefault(); this.cancelDrag(event); this.select(null); return; }
        if (key === 'Delete' || key === 'Backspace') { event.preventDefault(); this.remove(); return; }
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) return;
        event.preventDefault();
        const step = event.shiftKey ? 5 : 1;
        this.modifySelected(item => {
            item.x += key === 'ArrowLeft' ? -step : key === 'ArrowRight' ? step : 0;
            item.y += key === 'ArrowUp' ? -step : key === 'ArrowDown' ? step : 0;
        });
        this.blocks.get(this.selectedId)?.block.focus?.({ preventScroll: true });
    }

    pointerdown(event) {
        if (event.button !== 0 || this.drag) return;
        const block = event.target.closest('.editor-block');
        if (!block) return;
        const item = this.state.items.find(entry => entry.id === block.dataset.itemId);
        if (!item) return;
        const handle = event.target.closest('[data-resize]');
        event.preventDefault();
        this.select(item.id);
        block.focus({ preventScroll: true });
        const rect = this.pageElements.get(item.page).paper.getBoundingClientRect();
        this.drag = {
            pointerId: event.pointerId, before: this.state.snapshot(), original: { ...item },
            x: event.clientX, y: event.clientY, handle: handle?.dataset.resize,
            grabX: (event.clientX - rect.left) / (MM_TO_PX * this.zoom) - item.x,
            grabY: (event.clientY - rect.top) / (MM_TO_PX * this.zoom) - item.y,
        };
        this.container.setPointerCapture?.(event.pointerId);
        this.container.classList.add('is-manipulating');
    }

    pointermove(event) {
        if (!this.drag || event.pointerId !== this.drag.pointerId) return;
        const original = this.drag.original;
        let item = this.state.items.find(entry => entry.id === original.id);
        const snap = value => this.snap && !event.altKey ? Math.round(value / 5) * 5 : Math.round(value);
        const dx = (event.clientX - this.drag.x) / (MM_TO_PX * this.zoom);
        const dy = (event.clientY - this.drag.y) / (MM_TO_PX * this.zoom);
        let next = { ...original };
        if (this.drag.handle) {
            const handle = this.drag.handle;
            if (handle.includes('e')) next.w = Math.max(10, Math.min(210 - original.x, snap(original.w + dx)));
            if (handle.includes('s')) next.h = Math.max(10, Math.min(285 - original.y, snap(original.h + dy)));
            if (handle.includes('w')) { next.x = Math.max(0, Math.min(original.x + original.w - 10, snap(original.x + dx))); next.w = original.x + original.w - next.x; }
            if (handle.includes('n')) { next.y = Math.max(30, Math.min(original.y + original.h - 10, snap(original.y + dy))); next.h = original.y + original.h - next.y; }
        } else {
            let destination = this.pageElements.get(original.page);
            for (const [page, entry] of this.pageElements) {
                const rect = entry.paper.getBoundingClientRect();
                if (event.clientY >= rect.top && event.clientY <= rect.bottom && event.clientX >= rect.left && event.clientX <= rect.right) {
                    destination = entry;
                    next.page = page;
                    break;
                }
            }
            const rect = destination.paper.getBoundingClientRect();
            next.x = snap((event.clientX - rect.left) / (MM_TO_PX * this.zoom) - this.drag.grabX);
            next.y = snap((event.clientY - rect.top) / (MM_TO_PX * this.zoom) - this.drag.grabY);
        }
        Object.assign(item, LayoutState.bounds(next));
        this.paint(item);
        this.refreshInspector();
        const viewport = this.viewport.getBoundingClientRect();
        if (event.clientY > viewport.bottom - 35) this.viewport.scrollTop += 15;
        if (event.clientY < viewport.top + 35) this.viewport.scrollTop -= 15;
    }

    endDrag(event) {
        const pointerId = this.drag.pointerId;
        this.drag = null;
        if (this.container.hasPointerCapture?.(pointerId)) this.container.releasePointerCapture(pointerId);
        this.container.classList.remove('is-manipulating');
    }
    pointerup(event) {
        if (!this.drag || event.pointerId !== this.drag.pointerId) return;
        const changed = this.state.record(this.drag.before);
        this.endDrag(event);
        this.refreshOutline();
        this.refreshControls();
        if (changed) this.changed();
    }
    cancelDrag(event) {
        if (!this.drag) return;
        this.state.restore(this.drag.before);
        this.endDrag(event);
        this.render();
    }
}
