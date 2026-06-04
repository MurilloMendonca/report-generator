const API = {
    async parseResponse(res, fallbackMessage) {
        if (!res.ok) {
            let detail = fallbackMessage || 'Falha na requisição';
            try {
                const payload = await res.json();
                detail = payload.detail || payload.message || detail;
            } catch (error) {
                // Keep fallback message.
            }
            throw new Error(detail);
        }
        return await res.json();
    },

    authHeaders(extra = {}) {
        return extra;
    },

    async login(secret) {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ secret })
        });
        if (!res.ok) throw new Error('Segredo de acesso inválido');
        return await res.json();
    },

    async logout() {
        const res = await fetch('/api/auth/logout', {
            method: 'POST',
            headers: this.authHeaders()
        });
        return await this.parseResponse(res, 'Não foi possível sair');
    },

    async checkLogin() {
        const res = await fetch('/api/auth/check', { headers: this.authHeaders() });
        if (!res.ok) throw new Error('Não autorizado');
        return await res.json();
    },

    async startSession(formData) {
        const res = await fetch('/api/sessions', { method: 'POST', headers: this.authHeaders(), body: formData });
        return await this.parseResponse(res, 'Não foi possível iniciar a sessão');
    },

    async getRecentSessions() {
        const res = await fetch('/api/sessions', { headers: this.authHeaders() });
        if (!res.ok) throw new Error('Não foi possível carregar as sessões recentes');
        return await res.json();
    },

    async getModels() {
        const res = await fetch('/api/models', { headers: this.authHeaders() });
        return await this.parseResponse(res, 'Não foi possível carregar os modelos');
    },

    async getComponentLibrary() {
        const res = await fetch('/api/component-library', { headers: this.authHeaders() });
        return await this.parseResponse(res, 'Não foi possível carregar a biblioteca de componentes');
    },

    async getSession(jobId) {
        const res = await fetch(`/api/sessions/${jobId}`, { headers: this.authHeaders() });
        if (!res.ok) throw new Error(`Sessão ${jobId} não encontrada`);
        return await res.json();
    },

    async deleteSession(jobId) {
        const res = await fetch(`/api/sessions/${jobId}`, {
            method: 'DELETE',
            headers: this.authHeaders()
        });
        return await this.parseResponse(res, 'Não foi possível excluir a sessão');
    },

    async addFiles(jobId, formData) {
        const res = await fetch(`/api/sessions/${jobId}/files`, { method: 'POST', headers: this.authHeaders(), body: formData });
        return await this.parseResponse(res, 'Não foi possível adicionar os arquivos');
    },

    async getFileBlob(jobId, fileIndex, mode = 'preview', variant = 'original') {
        const query = variant && variant !== 'original' ? `?variant=${encodeURIComponent(variant)}` : '';
        const res = await fetch(`/api/sessions/${jobId}/files/${fileIndex}/${mode}${query}`, {
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Não foi possível carregar o arquivo');
        return await res.blob();
    },

    async deleteFile(jobId, fileIndex) {
        const res = await fetch(`/api/sessions/${jobId}/files/${fileIndex}`, {
            method: 'DELETE',
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Não foi possível excluir o arquivo');
        return await res.json();
    },

    async sendChat(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/chat`, { 
            method: 'POST', 
            headers: this.authHeaders({ 'Content-Type': 'application/x-www-form-urlencoded' }),
            body: new URLSearchParams({ prompt }) 
        });
        return await this.parseResponse(res, 'Não foi possível enviar o feedback');
    },

    async syncArtifacts(jobId, artifacts, components, options = {}) {
        const res = await fetch(`/api/sessions/${jobId}/artifacts`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                artifacts,
                components,
                defer_pipeline: Boolean(options.deferPipeline)
            })
        });
        return await this.parseResponse(res, 'Não foi possível atualizar os artefatos');
    },

    async saveLayout(jobId, template) {
        const res = await fetch(`/api/sessions/${jobId}/template`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(template)
        });
        return await this.parseResponse(res, 'Não foi possível salvar o layout');
    },

    async updateSettings(jobId, settings) {
        const res = await fetch(`/api/sessions/${jobId}/settings`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(settings)
        });
        return await this.parseResponse(res, 'Não foi possível atualizar as configurações');
    },

    async regenerate(jobId, scope, settings = null, componentId = null) {
        const res = await fetch(`/api/sessions/${jobId}/regenerate`, {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ scope, settings, component_id: componentId })
        });
        if (!res.ok) throw new Error('Não foi possível atualizar o relatório');
        return await res.json();
    },

    async getLayoutTemplates() {
        const res = await fetch('/api/layout-templates', { headers: this.authHeaders() });
        return await this.parseResponse(res, 'Não foi possível carregar os templates de layout');
    },

    async exploreData(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/explore`, { 
            method: 'POST', 
            headers: this.authHeaders(),
            body: new URLSearchParams({ prompt }) 
        });
        return await this.parseResponse(res, 'Não foi possível executar a exploração');
    },

    async getPdfObjectUrl(jobId, version) {
        const res = await fetch(`/api/sessions/${jobId}/pdf?v=${version}`, {
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Não foi possível carregar o PDF');
        const blob = await res.blob();
        return URL.createObjectURL(blob);
    },

    async getComponentPreviewObjectUrl(jobId, componentId, version) {
        const res = await fetch(`/api/sessions/${jobId}/components/${encodeURIComponent(componentId)}/preview?v=${version || Date.now()}`, {
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Não foi possível carregar a prévia do componente');
        const blob = await res.blob();
        return URL.createObjectURL(blob);
    }
};
