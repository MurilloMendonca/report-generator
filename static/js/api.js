const API = {
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
        return await res.json();
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
        return await res.json();
    },

    async sendChat(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/chat`, { 
            method: 'POST', 
            headers: this.authHeaders(),
            body: new URLSearchParams({ prompt }) 
        });
        return await res.json();
    },

    async syncArtifacts(jobId, artifacts, components) {
        const res = await fetch(`/api/sessions/${jobId}/artifacts`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ artifacts, components })
        });
        return await res.json();
    },

    async saveLayout(jobId, template) {
        const res = await fetch(`/api/sessions/${jobId}/template`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(template)
        });
        return await res.json();
    },

    async updateSettings(jobId, settings) {
        const res = await fetch(`/api/sessions/${jobId}/settings`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(settings)
        });
        return await res.json();
    },

    async getLayoutTemplates() {
        const res = await fetch('/api/layout-templates', { headers: this.authHeaders() });
        return await res.json();
    },

    async exploreData(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/explore`, { 
            method: 'POST', 
            headers: this.authHeaders(),
            body: new URLSearchParams({ prompt }) 
        });
        return await res.json();
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
