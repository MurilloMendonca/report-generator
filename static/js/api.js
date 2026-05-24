const API = {
    async startSession(formData) {
        const res = await fetch('/api/sessions', { method: 'POST', body: formData });
        return await res.json();
    },

    async getSession(jobId) {
        const res = await fetch(`/api/sessions/${jobId}`);
        if (!res.ok) throw new Error(`Session ${jobId} not found`);
        return await res.json();
    },

    async addFiles(jobId, formData) {
        const res = await fetch(`/api/sessions/${jobId}/files`, { method: 'POST', body: formData });
        return await res.json();
    },

    async sendChat(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/chat`, { 
            method: 'POST', 
            body: new URLSearchParams({ prompt }) 
        });
        return await res.json();
    },

    async syncArtifacts(jobId, artifacts, components) {
        const res = await fetch(`/api/sessions/${jobId}/artifacts`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artifacts, components })
        });
        return await res.json();
    },

    async saveLayout(jobId, template) {
        const res = await fetch(`/api/sessions/${jobId}/template`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(template)
        });
        return await res.json();
    },

    async updateSettings(jobId, settings) {
        const res = await fetch(`/api/sessions/${jobId}/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        return await res.json();
    },

    async getLayoutTemplates() {
        const res = await fetch('/api/layout-templates');
        return await res.json();
    },

    async exploreData(jobId, prompt) {
        const res = await fetch(`/api/sessions/${jobId}/explore`, { 
            method: 'POST', 
            body: new URLSearchParams({ prompt }) 
        });
        return await res.json();
    }
};
