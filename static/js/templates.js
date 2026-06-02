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
        document.getElementById('template-select').onchange = (event) => {
            this.selectTemplate(event.target.value);
        };
        document.getElementById('new-template-btn').onclick = () => this.newTemplate();
        document.getElementById('duplicate-template-btn').onclick = () => this.duplicateTemplate();
        document.getElementById('delete-template-btn').onclick = () => this.deleteTemplate();
        document.getElementById('add-text-btn').onclick = () => this.addSlot('text');
        document.getElementById('add-graphic-btn').onclick = () => this.addSlot('graphic');
        document.getElementById('add-page-btn').onclick = () => this.addPage();
        document.getElementById('template-form').onsubmit = (event) => this.saveTemplate(event);
        document.getElementById('slot-type').onchange = (event) => this.updateSelectedSlotType(event.target.value);
        document.getElementById('slot-name').oninput = (event) => this.updateSelectedSlotField('name', event.target.value);
        document.getElementById('slot-description').oninput = (event) => this.updateSelectedSlotField('description', event.target.value);
        ['page', 'x', 'y', 'w', 'h'].forEach(field => {
            document.getElementById(`slot-${field}`).oninput = (event) => this.updateSelectedSlotNumber(field, event.target.value);
        });
        document.getElementById('remove-slot-btn').onclick = () => this.removeSelectedSlot();
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
        document.getElementById('template-select').innerHTML = this.templates.map(template => (
            `<option value="${this.escapeHtml(template.id)}">${this.escapeHtml(template.name)}</option>`
        )).join('');
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
            name: 'New template',
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
        this.currentTemplate.name = `${this.currentTemplate.name} Copy`;
        document.getElementById('template-name').value = this.currentTemplate.name;
        this.setStatus('Duplicated locally. Save to add it to the library.');
    },

    async deleteTemplate() {
        if (!this.currentTemplate || !this.currentTemplate.id) return;
        if (!confirm('Delete this template from the library?')) return;
        await fetch(`/api/layout-templates/${this.currentTemplate.id}`, {
            method: 'DELETE',
            headers: API.authHeaders()
        });
        this.setStatus('Template deleted.');
        await this.loadTemplates();
    },

    addSlot(type) {
        if (!this.currentTemplate) this.newTemplate();
        this.syncSlotsFromCanvas();
        const pageCount = Math.max(1, ...this.currentTemplate.slots.map(slot => slot.page || 1));
        const slot = {
            slot_id: `slot_${Date.now()}`,
            name: type === 'text' ? 'Text block' : 'Graphic block',
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
        canvas.innerHTML = '';
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
            block.innerHTML = `
                <span>${this.escapeHtml(label)}</span>
                <small>${this.escapeHtml(slot.type)}</small>
                <div class="resizer"></div>
            `;
            block.onclick = (event) => {
                event.stopPropagation();
                this.selectSlot(slot.slot_id);
            };
            Utils.makeDraggable(block);
            pageEl.appendChild(block);
        });
    },

    createPage(page) {
        const pageEl = document.createElement('div');
        pageEl.className = 'a4-page';
        pageEl.dataset.page = page;
        pageEl.style.setProperty('--page-label', `"Template Page ${page}"`);
        pageEl.onclick = () => this.selectSlot(null);
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
        this.setStatus(`Saved "${saved.name}".`);
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
