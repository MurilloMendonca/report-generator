const TemplateEditor = {
    templates: [],
    currentTemplate: null,
    selectedSlotId: null,
    metadataDirty: false,
    editor: null,

    async init() {
        this.editor = new LayoutEditor(document.getElementById('template-canvas'), {
            allowAdd: true,
            onSelect: item => { this.selectedSlotId = item?.id || null; },
            onChange: () => {
                this.currentTemplate.slots = this.collectSlotsFromCanvas();
                this.setStatus(this.dirty ? 'Alterações não salvas.' : 'Sem alterações.');
            },
        });
        this.bindEvents();
        if (await this.ensureAuthenticated()) await this.loadTemplates();
    },

    get dirty() { return this.metadataDirty || Boolean(this.editor?.dirty); },

    async ensureAuthenticated() {
        let user;
        try {
            user = await API.me();
        } catch (error) {
            API.redirectToLogin();
            return false;
        }
        document.getElementById('app-shell').style.display = 'flex';
        Utils.setupAccountBar(user);
        return true;
    },

    bindEvents() {
        document.getElementById('template-form').onsubmit = event => this.saveTemplate(event);
        document.getElementById('template-select').addEventListener('change', event => this.selectTemplate(event.target.value));
        document.getElementById('new-template-btn').addEventListener('click', () => this.newTemplate());
        document.getElementById('duplicate-template-btn').addEventListener('click', () => this.duplicateTemplate());
        document.getElementById('delete-template-btn').addEventListener('click', () => this.deleteTemplate());
        for (const id of ['template-name', 'template-description']) {
            document.getElementById(id).addEventListener('input', () => {
                this.metadataDirty = true;
                this.setStatus('Alterações não salvas.');
            });
        }
        window.addEventListener?.('beforeunload', event => {
            if (this.dirty) { event.preventDefault(); event.returnValue = ''; }
        });
        document.addEventListener('keydown', event => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                event.preventDefault();
                if (document.getElementById('template-form').reportValidity()) this.saveTemplate(event);
            }
        });
    },

    async loadTemplates(selectedId = null) {
        this.setStatus('Carregando templates de layout...');
        const data = await API.getLayoutTemplates();
        if (!Array.isArray(data.templates)) throw new Error('Não foi possível ler a biblioteca de templates.');
        this.templates = data.templates;
        this.renderTemplateSelect();
        if (this.templates.length) this.selectTemplate(selectedId || this.templates[0].id, true);
        else this.newTemplate(true);
    },

    renderTemplateSelect() {
        const select = document.getElementById('template-select');
        select.replaceChildren();
        for (const template of this.templates) {
            const option = document.createElement('option');
            option.value = template.id;
            option.textContent = template.name;
            select.appendChild(option);
        }
    },

    canDiscard() { return !this.dirty || confirm('Há alterações não salvas. Deseja descartá-las?'); },

    adopt(template) {
        this.currentTemplate = structuredClone(template);
        this.selectedSlotId = null;
        this.metadataDirty = false;
        document.getElementById('template-select').value = template.id;
        document.getElementById('template-name').value = template.name || '';
        document.getElementById('template-description').value = template.description || '';
        this.renderCanvas();
        document.getElementById('delete-template-btn').disabled = !template.id;
        this.setStatus('Adicione blocos ou selecione um para ajustar o layout.');
    },

    selectTemplate(templateId, force = false) {
        if (!force && !this.canDiscard()) {
            document.getElementById('template-select').value = this.currentTemplate?.id || '';
            return;
        }
        const template = this.templates.find(item => item.id === templateId);
        if (template) this.adopt(template);
    },

    newTemplate(force = false) {
        if (!force && !this.canDiscard()) return;
        this.adopt({ id: '', name: 'Novo template', description: '', slots: [] });
        this.metadataDirty = true;
    },

    duplicateTemplate() {
        if (!this.currentTemplate) return;
        const copy = {
            ...this.currentTemplate,
            id: '',
            name: document.getElementById('template-name').value + ' — cópia',
            description: document.getElementById('template-description').value,
            slots: this.collectSlotsFromCanvas(),
        };
        this.adopt(copy);
        this.metadataDirty = true;
        this.setStatus('Cópia pronta. Salve para adicioná-la à biblioteca.');
    },

    async deleteTemplate() {
        if (!this.currentTemplate?.id || !confirm('Excluir este template da biblioteca?')) return;
        try {
            const response = await fetch('/api/layout-templates/' + encodeURIComponent(this.currentTemplate.id), {
                method: 'DELETE', headers: API.authHeaders(),
            });
            await API.parseResponse(response, 'Não foi possível excluir o template.');
            await this.loadTemplates();
            this.setStatus('Template excluído.');
        } catch (error) { this.setStatus(error.message, true); }
    },

    renderCanvas() {
        const items = (this.currentTemplate?.slots || []).map(slot => ({
            ...slot, id: slot.slot_id, label: slot.name || (slot.type === 'text' ? 'Bloco de texto' : 'Bloco visual'),
        }));
        this.editor.setItems(items, { force: true });
        this.editor.state.pages = Math.max(this.editor.state.pages, Math.min(100, this.currentTemplate?.page_count || 1));
        this.editor.state.saved = this.editor.state.snapshot();
        this.editor.render();
    },

    collectSlotsFromCanvas() {
        return this.editor.getItems().map(({ id, label, type, description, page, x, y, w, h }) => ({
            slot_id: id, name: label, type, description: description || '', page, x, y, w, h,
        }));
    },

    async saveTemplate(event) {
        event.preventDefault();
        if (!this.currentTemplate) return;
        const button = document.getElementById('save-template-btn');
        if (button.disabled) return;
        const payload = {
            id: this.currentTemplate.id,
            name: document.getElementById('template-name').value,
            description: document.getElementById('template-description').value,
            slots: this.collectSlotsFromCanvas(),
            page_count: this.editor.state.pages,
        };
        const url = payload.id ? '/api/layout-templates/' + encodeURIComponent(payload.id) : '/api/layout-templates';
        button.disabled = true;
        this.editor.container.inert = true;
        for (const id of ['template-name', 'template-description', 'template-select', 'new-template-btn', 'duplicate-template-btn', 'delete-template-btn']) document.getElementById(id).disabled = true;
        this.setStatus('Salvando template...');
        try {
            const response = await fetch(url, {
                method: payload.id ? 'PUT' : 'POST',
                headers: API.authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
            });
            const saved = await API.parseResponse(response, 'Não foi possível salvar o template.');
            const index = this.templates.findIndex(template => template.id === saved.id);
            if (index === -1) this.templates.push(saved); else this.templates[index] = saved;
            this.renderTemplateSelect();
            this.adopt(saved);
            this.setStatus('Template salvo.');
        } catch (error) { this.setStatus(error.message, true); }
        finally {
            button.disabled = false;
            this.editor.container.inert = false;
            for (const id of ['template-name', 'template-description', 'template-select', 'new-template-btn', 'duplicate-template-btn']) document.getElementById(id).disabled = false;
            document.getElementById('delete-template-btn').disabled = !this.currentTemplate.id;
        }
    },

    setStatus(message, isError = false) {
        const status = document.getElementById('save-status');
        status.textContent = message;
        status.setAttribute('role', isError ? 'alert' : 'status');
    },
};

window.onload = () => TemplateEditor.init().catch(error => TemplateEditor.setStatus(error.message, true));
