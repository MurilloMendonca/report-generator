const API = {
    async parseResponse(res, fallbackMessage) {
        if (!res.ok) {
            let detail = fallbackMessage || 'Request failed';
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

    accessToken() {
        return localStorage.getItem('pdf_report_access_secret') || '';
    },

    authHeaders(extra = {}) {
        const token = this.accessToken();
        return token ? { ...extra, 'X-Access-Token': token } : extra;
    },

    async login(secret) {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ secret })
        });
        if (!res.ok) throw new Error('Invalid access secret');
        localStorage.setItem('pdf_report_access_secret', secret);
        return await res.json();
    },

    async checkLogin() {
        const res = await fetch('/api/auth/check', { headers: this.authHeaders() });
        if (!res.ok) throw new Error('Unauthorized');
        return await res.json();
    },

    async startSession(formData) {
        const res = await fetch('/api/sessions', { method: 'POST', headers: this.authHeaders(), body: formData });
        return await this.parseResponse(res, 'Could not start session');
    },

    async getRecentSessions() {
        const res = await fetch('/api/sessions', { headers: this.authHeaders() });
        if (!res.ok) throw new Error('Could not load recent sessions');
        return await res.json();
    },

    async getSession(jobId) {
        const res = await fetch(`/api/sessions/${jobId}`, { headers: this.authHeaders() });
        if (!res.ok) throw new Error(`Session ${jobId} not found`);
        return await res.json();
    },

    async addFiles(jobId, formData) {
        const res = await fetch(`/api/sessions/${jobId}/files`, { method: 'POST', headers: this.authHeaders(), body: formData });
        return await this.parseResponse(res, 'Could not add files');
    },

    async getFileBlob(jobId, fileIndex, mode = 'preview', variant = 'original') {
        const query = variant && variant !== 'original' ? `?variant=${encodeURIComponent(variant)}` : '';
        const res = await fetch(`/api/sessions/${jobId}/files/${fileIndex}/${mode}${query}`, {
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Could not load file');
        return await res.blob();
    },

    async deleteFile(jobId, fileIndex) {
        const res = await fetch(`/api/sessions/${jobId}/files/${fileIndex}`, {
            method: 'DELETE',
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Could not delete file');
        return await res.json();
    },

    async sendChat(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/chat`, { 
            method: 'POST', 
            headers: this.authHeaders(),
            body: new URLSearchParams({ prompt }) 
        });
        return await this.parseResponse(res, 'Could not send feedback');
    },

    async syncArtifacts(jobId, artifacts, components) {
        const res = await fetch(`/api/sessions/${jobId}/artifacts`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ artifacts, components })
        });
        return await this.parseResponse(res, 'Could not update artifacts');
    },

    async saveLayout(jobId, template) {
        const res = await fetch(`/api/sessions/${jobId}/template`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(template)
        });
        return await this.parseResponse(res, 'Could not save layout');
    },

    async updateSettings(jobId, settings) {
        const res = await fetch(`/api/sessions/${jobId}/settings`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(settings)
        });
        return await this.parseResponse(res, 'Could not update settings');
    },

    async regenerate(jobId, scope, settings = null) {
        const res = await fetch(`/api/sessions/${jobId}/regenerate`, {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ scope, settings })
        });
        if (!res.ok) throw new Error('Could not update report');
        return await res.json();
    },

    async getLayoutTemplates() {
        const res = await fetch('/api/layout-templates', { headers: this.authHeaders() });
        return await this.parseResponse(res, 'Could not load layout templates');
    },

    async exploreData(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/explore`, { 
            method: 'POST', 
            headers: this.authHeaders(),
            body: new URLSearchParams({ prompt }) 
        });
        return await this.parseResponse(res, 'Could not run exploration');
    },

    async getPdfObjectUrl(jobId, version) {
        const res = await fetch(`/api/sessions/${jobId}/pdf?v=${version}`, {
            headers: this.authHeaders()
        });
        if (!res.ok) throw new Error('Could not load PDF');
        const blob = await res.blob();
        return URL.createObjectURL(blob);
    }
};
