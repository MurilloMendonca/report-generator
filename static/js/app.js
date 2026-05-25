const App = {
    currentSession: null,
    pollInterval: null,
    lastPdfVersion: 0,
    pdfObjectUrl: null,
    layoutTemplates: [],
    models: {
        gemini: [{ id: "models/gemini-3.1-flash-lite-preview", label: "Gemini 3.1 Flash Lite" }],
        deepseek: [{ id: "deepseek-chat", label: "DeepSeek-V3" }]
    },

    async init() {
        this.updateModels();
        this.setupEventListeners();
        const isAuthenticated = await this.ensureAuthenticated();
        if (!isAuthenticated) return;
        await this.loadLayoutTemplates();
        await this.loadRecentSessions();
        await this.loadSessionFromUrl();
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

    getSessionIdFromUrl() {
        const match = window.location.pathname.match(/^\/session\/([^/]+)$/);
        return match ? decodeURIComponent(match[1]) : null;
    },

    async loadSessionFromUrl() {
        const jobId = this.getSessionIdFromUrl();
        if (!jobId) return;

        try {
            const session = await API.getSession(jobId);
            this.currentSession = session;
            this.showWorkspace();
            this.lastPdfVersion = 0;
            this.updateUI(session);

            if (session.status && !session.status.includes("concluído") && !session.status.includes("Falha") && session.status !== "Concluído") {
                this.startPolling();
            }
        } catch (error) {
            document.getElementById('chat-history').innerHTML += `
                <div class="message ai"><b>Studio:</b> Sessão não encontrada: ${jobId}</div>
            `;
        }
    },

    async loadLayoutTemplates() {
        const data = await API.getLayoutTemplates();
        this.layoutTemplates = data.templates || [];
        UI.renderTemplateOptions(this.layoutTemplates);
    },

    async loadRecentSessions() {
        const data = await API.getRecentSessions();
        UI.renderRecentSessions(data.sessions || []);
    },

    updateModels() {
        const p = document.getElementById('provider').value;
        document.getElementById('model_name').innerHTML = this.models[p]
            .map(m => `<option value="${m.id}">${m.label}</option>`)
            .join('');
    },

    setupEventListeners() {
        document.getElementById('login-form').onsubmit = (e) => this.handleLogin(e);
        document.getElementById('upload-form').onsubmit = (e) => this.handleStartSession(e);
        document.getElementById('chat-form').onsubmit = (e) => this.handleChat(e);
        document.getElementById('explore-form').onsubmit = (e) => this.handleExplore(e);
        document.getElementById('modal-form').onsubmit = (e) => this.handleSaveIndicator(e);
        document.getElementById('settings-form').onsubmit = (e) => this.handleSaveSettings(e);
    },

    async handleLogin(e) {
        e.preventDefault();
        const input = document.getElementById('access-secret');
        const errorEl = document.getElementById('login-error');
        errorEl.textContent = '';

        try {
            await API.login(input.value);
            input.value = '';
            await this.ensureAuthenticated();
            await this.loadLayoutTemplates();
            await this.loadRecentSessions();
            await this.loadSessionFromUrl();
        } catch (error) {
            errorEl.textContent = 'Segredo inválido.';
        }
    },

    async handleStartSession(e) {
        e.preventDefault();
        const data = await API.startSession(new FormData(e.target));
        this.currentSession = { job_id: data.job_id };
        window.history.replaceState({}, '', `/session/${data.job_id}`);
        this.showWorkspace();
        
        this.startPolling();
    },

    showWorkspace() {
        document.getElementById('initial-form').style.display = 'none';
        document.getElementById('file-workspace').style.display = 'block';
        document.getElementById('header-progress').style.display = 'block';
        document.getElementById('chat-form').style.display = 'block';
    },

    startPolling() {
        if (this.pollInterval) clearInterval(this.pollInterval);
        this.pollInterval = setInterval(async () => {
            const session = await API.getSession(this.currentSession.job_id);
            this.currentSession = session;
            this.updateUI(session);
            
            if (session.status.includes("concluído") || session.status.includes("Falha") || session.status === "Concluído") {
                clearInterval(this.pollInterval);
                this.pollInterval = null;
            }
        }, 2000);
    },

    updateUI(session) {
        UI.updateStatus(session.status, session.status === "Concluído" ? 100 : 60);
        UI.renderLogs(session.logs);
        UI.renderFiles(session.files);
        UI.renderArtifacts(session.artifacts);
        UI.renderLayoutCanvas(session);
        UI.renderDataArtifacts(session);
        UI.renderFullScript(session.script);
        UI.renderTemplateOptions(this.layoutTemplates);
        UI.updateSettingsForm(session.settings);

        if (session.pdf_version > this.lastPdfVersion) {
            this.refreshPdf(session);
        }
    },

    async refreshPdf(session) {
        const url = await API.getPdfObjectUrl(session.job_id, session.pdf_version);
        if (this.pdfObjectUrl) URL.revokeObjectURL(this.pdfObjectUrl);
        this.pdfObjectUrl = url;
        document.getElementById('pdf-viewer').src = url;
        const dlBtn = document.getElementById('download-btn');
        dlBtn.style.display = 'block';
        dlBtn.onclick = () => window.open(url);
        this.lastPdfVersion = session.pdf_version;
    },

    async handleChat(e) {
        e.preventDefault();
        const input = document.getElementById('chat-input');
        const q = input.value;
        if (!q) return;

        document.getElementById('chat-history').innerHTML += `
            <div class="message user"><b>Você:</b> ${q}</div>
        `;
        input.value = '';
        
        await API.sendChat(this.currentSession.job_id, q);
        this.startPolling();
    },

    async handleExplore(e) {
        e.preventDefault();
        const input = document.getElementById('explore-input');
        const q = input.value;
        if (!q) return;

        const chat = document.getElementById('explore-chat');
        chat.innerHTML += `<div class="message user"><b>Você:</b> ${q}</div>`;
        input.value = '';
        
        const data = await API.exploreData(this.currentSession.job_id, q);
        chat.innerHTML += `<div class="message ai"><b>Analista:</b> ${data.answer}</div>`;
        chat.scrollTop = chat.scrollHeight;
    },

    async handleUploadMore(input) {
        if (!input.files.length) return;
        const fd = new FormData();
        for (let f of input.files) fd.append('files', f);
        await API.addFiles(this.currentSession.job_id, fd);
        this.startPolling();
    },

    handleEditIndicator(index) {
        const art = index === -1 
            ? { indicator: "", type: "bar", columns: [], reason: "" } 
            : this.currentSession.artifacts[index];
        
        document.getElementById('edit-index').value = index;
        document.getElementById('m-name').value = art.indicator;
        document.getElementById('m-type').value = art.type;
        document.getElementById('m-cols').value = (art.columns || []).join(', ');
        document.getElementById('m-reason').value = art.reason || "";
        document.getElementById('m-code').value = index === -1 
            ? "" 
            : (this.currentSession.components[art.id] || "");
        
        document.getElementById('indicator-modal').showModal();
    },

    async handleSaveIndicator(e) {
        e.preventDefault();
        const idx = parseInt(document.getElementById('edit-index').value);
        const id = idx === -1 ? `kpi_${Date.now()}` : this.currentSession.artifacts[idx].id;
        
        const newArt = {
            id,
            indicator: document.getElementById('m-name').value,
            type: document.getElementById('m-type').value,
            columns: document.getElementById('m-cols').value.split(',').map(s => s.trim()),
            reason: document.getElementById('m-reason').value
        };

        if (idx === -1) {
            this.currentSession.artifacts.push(newArt);
        } else {
            this.currentSession.artifacts[idx] = newArt;
        }

        const overrides = {};
        const code = document.getElementById('m-code').value;
        if (code) overrides[id] = code;

        await API.syncArtifacts(this.currentSession.job_id, this.currentSession.artifacts, overrides);
        document.getElementById('indicator-modal').close();
        this.startPolling();
    },

    async handleDeleteIndicator(index) {
        if (!confirm("Excluir este componente?")) return;
        this.currentSession.artifacts.splice(index, 1);
        await API.syncArtifacts(this.currentSession.job_id, this.currentSession.artifacts, {});
        this.startPolling();
    },

    async handleDeleteIndicatorById(id, event) {
        if (event) event.stopPropagation();
        const index = this.currentSession.artifacts.findIndex(a => a.id === id);
        if (index !== -1) this.handleDeleteIndicator(index);
    },

    async handleSaveVisualLayout() {
        const template = [];
        const pages = Array.from(document.querySelectorAll('.a4-page'));
        document.querySelectorAll('.layout-block').forEach(el => {
            const blockRect = el.getBoundingClientRect();
            const blockCenterY = blockRect.top + blockRect.height / 2;
            const pageEl = pages.find(page => {
                const pageRect = page.getBoundingClientRect();
                return blockCenterY >= pageRect.top && blockCenterY <= pageRect.bottom;
            }) || el.closest('.a4-page');
            const pageRect = pageEl.getBoundingClientRect();
            template.push({
                id: el.id.replace('block-', ''),
                page: parseInt(pageEl.dataset.page || "1"),
                x: Math.round((blockRect.left - pageRect.left) / MM_TO_PX),
                y: Math.round((blockRect.top - pageRect.top) / MM_TO_PX),
                w: Math.round(el.offsetWidth / MM_TO_PX),
                h: Math.round(el.offsetHeight / MM_TO_PX)
            });
        });
        await API.saveLayout(this.currentSession.job_id, template);
        this.startPolling();
    },

    addLayoutPage() {
        const canvas = document.getElementById('layout-canvas');
        const pageCount = canvas.querySelectorAll('.a4-page').length;
        canvas.appendChild(UI.createLayoutPage(pageCount + 1));
    },

    async handleSaveSettings(e) {
        e.preventDefault();
        const settings = {
            title: document.getElementById('s-title').value,
            font: document.getElementById('s-font').value,
            font_size: parseInt(document.getElementById('s-font-size').value),
            alignment: document.getElementById('s-alignment').value,
            line_height: parseInt(document.getElementById('s-line-height').value),
            language: document.getElementById('s-language').value,
            selected_template_id: document.getElementById('s-layout-template').value
        };
        await API.updateSettings(this.currentSession.job_id, settings);
        this.startPolling();
    },

    copyFullScript() {
        const script = document.getElementById('full-script-content').textContent;
        navigator.clipboard.writeText(script);
        alert("Script copiado para a área de transferência!");
    }
};

window.onload = () => App.init();
