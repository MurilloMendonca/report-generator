const TemplateEditor = {
    templates: [],
    currentTemplate: null,
    selectedSlotId: null,

    async init() {
        this.bindEvents();
        const isAuthenticated = await this.ensureAuthenticated();
        if (!isAuthenticated) return;
        await this.loadTemplates();
    },

    async ensureAuthenticated() {
        const loginGate = document.getElementById('login-gate');
        const appShell = document.getElementById('app-shell');

        try {
            await API.checkLogin();
            loginGate.style.display = 'none';
            appShell.style.display = 'flex';
            return true;
        } catch (error) {
            appShell.style.display = 'none';
            loginGate.style.display = 'flex';
            return false;
        }
    },

    bindEvents() {
        document.getElementById('login-form').onsubmit = (event) => this.handleLogin(event);
        document.getElementById('template-select').addEventListener('change', (event) => {
            this.selectTemplate(event.target.value);
        });
        document.getElementById('new-template-btn').addEventListener('click', () => this.newTemplate());
        document.getElementById('duplicate-template-btn').addEventListener('click', () => this.duplicateTemplate());
        document.getElementById('delete-template-btn').addEventListener('click', () => this.deleteTemplate());
        document.getElementById('add-text-btn').addEventListener('click', () => this.addSlot('text'));
        document.getElementById('add-graphic-btn').addEventListener('click', () => this.addSlot('graphic'));
        document.getElementById('add-page-btn').addEventListener('click', () => this.addPage());
        document.getElementById('template-form').onsubmit = (event) => this.saveTemplate(event);
        document.getElementById('slot-type').addEventListener('change', (event) => this.updateSelectedSlotType(event.target.value));
        document.getElementById('slot-name').addEventListener('input', (event) => this.updateSelectedSlotField('name', event.target.value));
        document.getElementById('slot-description').addEventListener('input', (event) => this.updateSelectedSlotField('description', event.target.value));
        ['page', 'x', 'y', 'w', 'h'].forEach(field => {
            document.getElementById(`slot-${field}`).addEventListener('input', (event) => this.updateSelectedSlotNumber(field, event.target.value));
        });
        document.getElementById('remove-slot-btn').addEventListener('click', () => this.removeSelectedSlot());
        document.addEventListener('click', (event) => this.handleDocumentClick(event));
    },

    handleDocumentClick(event) {
        const target = event.target.closest('[data-action]');
        if (!target) return;

        switch (target.dataset.action) {
            case 'template-preview-select':
                this.selectTemplate(target.dataset.templateId);
                break;
            default:
                break;
        }
    },

    async handleLogin(event) {
        event.preventDefault();
        const input = document.getElementById('access-secret');
        const errorEl = document.getElementById('login-error');
        errorEl.textContent = '';

        try {
            await API.login(input.value);
            input.value = '';
            await this.ensureAuthenticated();
            await this.loadTemplates();
        } catch (error) {
            errorEl.textContent = 'Segredo inválido.';
        }
    },

    async loadTemplates() {
        const response = await fetch('/api/layout-templates', { headers: API.authHeaders() });
        const data = await response.json();
        this.templates = data.templates || [];
        this.renderTemplateSelect();
        if (this.templates.length) {
            this.selectTemplate(this.templates[0].id);
        } else {
            this.newTemplate();
        }
    },

    renderTemplateSelect() {
        const select = document.getElementById('template-select');
        select.textContent = '';
        this.templates.forEach(template => {
            const option = document.createElement('option');
            option.value = template.id;
            option.textContent = template.name;
            select.appendChild(option);
        });
    },

    selectTemplate(templateId) {
        const template = this.templates.find(item => item.id === templateId);
        if (!template) return;
        this.currentTemplate = structuredClone(template);
        this.selectedSlotId = null;
        document.getElementById('template-select').value = template.id;
        document.getElementById('template-name').value = template.name || '';
        document.getElementById('template-description').value = template.description || '';
        this.renderCanvas();
        this.renderInspector();
    },

    newTemplate() {
        this.currentTemplate = {
            id: '',
            name: 'Novo template',
            description: '',
            slots: []
        };
        this.selectedSlotId = null;
        document.getElementById('template-name').value = this.currentTemplate.name;
        document.getElementById('template-description').value = '';
        document.getElementById('template-select').value = '';
        this.renderCanvas();
        this.renderInspector();
    },

    duplicateTemplate() {
        if (!this.currentTemplate) return;
        this.currentTemplate = structuredClone(this.currentTemplate);
        this.currentTemplate.id = '';
        this.currentTemplate.name = `${this.currentTemplate.name} - cópia`;
        document.getElementById('template-name').value = this.currentTemplate.name;
        this.setStatus('Duplicado localmente. Salve para adicioná-lo à biblioteca.');
    },

    async deleteTemplate() {
        if (!this.currentTemplate || !this.currentTemplate.id) return;
        if (!confirm('Excluir este template da biblioteca?')) return;
        await fetch(`/api/layout-templates/${this.currentTemplate.id}`, {
            method: 'DELETE',
            headers: API.authHeaders()
        });
        this.setStatus('Template excluído.');
        await this.loadTemplates();
    },

    addSlot(type) {
        if (!this.currentTemplate) this.newTemplate();
        this.syncSlotsFromCanvas();
        const pageCount = Math.max(1, ...this.currentTemplate.slots.map(slot => slot.page || 1));
        const slot = {
            slot_id: `slot_${Date.now()}`,
            name: type === 'text' ? 'Bloco de texto' : 'Bloco gráfico',
            description: '',
            type,
            page: pageCount,
            x: type === 'text' ? 10 : 20,
            y: type === 'text' ? 30 : 80,
            w: type === 'text' ? 190 : 90,
            h: type === 'text' ? 35 : 60
        };
        this.currentTemplate.slots.push(slot);
        this.selectedSlotId = slot.slot_id;
        this.renderCanvas();
        this.renderInspector();
    },

    addPage() {
        const canvas = document.getElementById('template-canvas');
        const pageCount = canvas.querySelectorAll('.a4-page').length;
        canvas.appendChild(this.createPage(pageCount + 1));
    },

    renderCanvas() {
        const canvas = document.getElementById('template-canvas');
        UI.clearElement(canvas);
        const slots = this.currentTemplate?.slots || [];
        const pageCount = Math.max(1, ...slots.map(slot => slot.page || 1));
        for (let page = 1; page <= pageCount; page++) {
            canvas.appendChild(this.createPage(page));
        }

        slots.forEach(slot => {
            const pageEl = canvas.querySelector(`.a4-page[data-page="${slot.page || 1}"]`);
            if (!pageEl) return;
            const block = document.createElement('div');
            block.className = `layout-block template-slot template-slot-${slot.type}`;
            if (slot.slot_id === this.selectedSlotId) block.classList.add('selected');
            block.id = `slot-${slot.slot_id}`;
            block.dataset.slotId = slot.slot_id;
            block.style.left = `${slot.x * MM_TO_PX}px`;
            block.style.top = `${slot.y * MM_TO_PX}px`;
            block.style.width = `${slot.w * MM_TO_PX}px`;
            block.style.height = `${slot.h * MM_TO_PX}px`;
            const label = slot.name || slot.type;
            block.tabIndex = 0;
            block.setAttribute('role', 'button');
            block.setAttribute('aria-label', `Bloco ${label}, página ${slot.page || 1}. Use as setas para mover.`);
            const labelEl = document.createElement('span');
            labelEl.textContent = label;
            const typeEl = document.createElement('small');
            typeEl.textContent = slot.type;
            const resizer = document.createElement('div');
            resizer.className = 'resizer';
            block.appendChild(labelEl);
            block.appendChild(typeEl);
            block.appendChild(resizer);
            block.addEventListener('click', (event) => {
                event.stopPropagation();
                this.selectSlot(slot.slot_id);
            });
            block.addEventListener('focus', () => this.selectSlot(slot.slot_id));
            block.addEventListener('keydown', (event) => {
                const key = event.key;
                if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete', 'Backspace'].includes(key)) return;
                event.preventDefault();
                this.selectSlot(slot.slot_id);
                if (key === 'Delete' || key === 'Backspace') {
                    this.removeSelectedSlot();
                    return;
                }
                const step = event.shiftKey ? 5 : 1;
                const deltaX = key === 'ArrowLeft' ? -step : key === 'ArrowRight' ? step : 0;
                const deltaY = key === 'ArrowUp' ? -step : key === 'ArrowDown' ? step : 0;
                this.moveSelectedSlot(deltaX, deltaY);
            });
            Utils.makeDraggable(block);
            pageEl.appendChild(block);
        });
    },

    createPage(page) {
        const pageEl = document.createElement('div');
        pageEl.className = 'a4-page';
        pageEl.dataset.page = page;
        pageEl.style.setProperty('--page-label', `"Página do template ${page}"`);
        pageEl.addEventListener('click', () => this.selectSlot(null));
        return pageEl;
    },

    selectSlot(slotId) {
        this.selectedSlotId = slotId;
        document.querySelectorAll('.template-slot').forEach(block => {
            block.classList.toggle('selected', block.dataset.slotId === slotId);
        });
        this.renderInspector();
    },

    renderInspector() {
        const inspector = document.getElementById('slot-inspector');
        const slot = this.getSelectedSlot();
        inspector.style.display = slot ? 'block' : 'none';
        if (slot) {
            document.getElementById('slot-name').value = slot.name || '';
            document.getElementById('slot-description').value = slot.description || '';
            document.getElementById('slot-type').value = slot.type;
            document.getElementById('slot-page').value = slot.page || 1;
            document.getElementById('slot-x').value = slot.x || 0;
            document.getElementById('slot-y').value = slot.y || 0;
            document.getElementById('slot-w').value = slot.w || 10;
            document.getElementById('slot-h').value = slot.h || 10;
        }
    },

    getSelectedSlot() {
        return (this.currentTemplate?.slots || []).find(slot => slot.slot_id === this.selectedSlotId);
    },

    updateSelectedSlotType(type) {
        this.syncSlotsFromCanvas();
        const slot = this.getSelectedSlot();
        if (!slot) return;
        slot.type = type;
        this.renderCanvas();
        this.renderInspector();
    },

    updateSelectedSlotField(field, value) {
        const slot = this.getSelectedSlot();
        if (!slot) return;
        slot[field] = value;
        const block = document.querySelector(`.template-slot[data-slot-id="${slot.slot_id}"]`);
        if (block) {
            const label = block.querySelector('span');
            if (label) label.textContent = slot.name || slot.type;
        }
    },

    updateSelectedSlotNumber(field, value) {
        const slot = this.getSelectedSlot();
        if (!slot) return;
        const parsed = parseInt(value);
        if (Number.isNaN(parsed)) return;

        if (field === 'page') {
            slot.page = Math.max(1, parsed);
        } else if (field === 'w' || field === 'h') {
            slot[field] = Math.max(10, parsed);
        } else {
            slot[field] = Math.max(0, parsed);
        }

        this.applySlotGeometry(slot);
    },

    moveSelectedSlot(deltaX, deltaY) {
        const slot = this.getSelectedSlot();
        if (!slot) return;
        slot.x = Math.max(0, slot.x + deltaX);
        slot.y = Math.max(0, slot.y + deltaY);
        this.applySlotGeometry(slot);
        this.renderInspector();
    },

    applySlotGeometry(slot) {
        const canvas = document.getElementById('template-canvas');
        let pageEl = canvas.querySelector(`.a4-page[data-page="${slot.page}"]`);
        while (!pageEl) {
            const pageCount = canvas.querySelectorAll('.a4-page').length;
            canvas.appendChild(this.createPage(pageCount + 1));
            pageEl = canvas.querySelector(`.a4-page[data-page="${slot.page}"]`);
        }

        const block = document.querySelector(`.template-slot[data-slot-id="${slot.slot_id}"]`);
        if (!block) return;
        if (block.parentElement !== pageEl) pageEl.appendChild(block);
        block.style.left = `${slot.x * MM_TO_PX}px`;
        block.style.top = `${slot.y * MM_TO_PX}px`;
        block.style.width = `${slot.w * MM_TO_PX}px`;
        block.style.height = `${slot.h * MM_TO_PX}px`;
    },

    removeSelectedSlot() {
        if (!this.currentTemplate || !this.selectedSlotId) return;
        this.syncSlotsFromCanvas();
        this.currentTemplate.slots = this.currentTemplate.slots.filter(slot => slot.slot_id !== this.selectedSlotId);
        this.selectedSlotId = null;
        this.renderCanvas();
        this.renderInspector();
    },

    syncSlotsFromCanvas() {
        if (!this.currentTemplate) return;
        const blocks = document.querySelectorAll('.template-slot');
        if (!blocks.length) return;
        this.currentTemplate.slots = this.collectSlotsFromCanvas();
    },

    collectSlotsFromCanvas() {
        const pages = Array.from(document.querySelectorAll('.a4-page'));
        return Array.from(document.querySelectorAll('.template-slot')).map(block => {
            const blockRect = block.getBoundingClientRect();
            const blockCenterY = blockRect.top + blockRect.height / 2;
            const pageEl = pages.find(page => {
                const pageRect = page.getBoundingClientRect();
                return blockCenterY >= pageRect.top && blockCenterY <= pageRect.bottom;
            }) || block.closest('.a4-page');
            const pageRect = pageEl.getBoundingClientRect();
            const existing = (this.currentTemplate.slots || []).find(slot => slot.slot_id === block.dataset.slotId);
            return {
                slot_id: block.dataset.slotId,
                name: existing?.name || '',
                description: existing?.description || '',
                type: existing?.type || 'graphic',
                page: parseInt(pageEl.dataset.page || '1'),
                x: Math.round((blockRect.left - pageRect.left) / MM_TO_PX),
                y: Math.round((blockRect.top - pageRect.top) / MM_TO_PX),
                w: Math.round(block.offsetWidth / MM_TO_PX),
                h: Math.round(block.offsetHeight / MM_TO_PX)
            };
        });
    },

    async saveTemplate(event) {
        event.preventDefault();
        if (!this.currentTemplate) return;
        const payload = {
            id: this.currentTemplate.id,
            name: document.getElementById('template-name').value,
            description: document.getElementById('template-description').value,
            slots: this.collectSlotsFromCanvas()
        };
        const url = payload.id ? `/api/layout-templates/${payload.id}` : '/api/layout-templates';
        const method = payload.id ? 'PUT' : 'POST';
        const response = await fetch(url, {
            method,
            headers: API.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload)
        });
        const saved = await response.json();
        this.setStatus(`Salvo "${saved.name}".`);
        await this.loadTemplates();
        this.selectTemplate(saved.id);
    },

    setStatus(message) {
        document.getElementById('save-status').textContent = message;
    },

    escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
};

window.onload = () => TemplateEditor.init();
