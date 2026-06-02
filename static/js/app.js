const App = {
    currentSession: null,
    pollInterval: null,
    lastPdfVersion: 0,
    pdfObjectUrl: null,
    indicatorPreviewUrl: null,
    indicatorPreviewVersion: 0,
    indicatorPreviewLoading: false,
    filePreviewUrl: null,
    filePreviewIndex: null,
    filePreviewVariant: 'original',
    layoutTemplates: [],
    componentLibrary: {
        presets: [],
        themes: [],
        chart_styles: [],
        default_theme_id: 'executive_clean',
        default_chart_style_id: 'modern'
    },
    models: {
        gemini: [
            { id: "gemini-3.1-flash-lite-preview", label: "Gemini 3.1 Flash Lite Preview" },
            { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite" },
            { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro Preview" },
            { id: "gemini-3-pro-preview", label: "Gemini 3 Pro Preview" },
            { id: "gemini-3-flash-preview", label: "Gemini 3 Flash Preview" },
            { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
            { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
            { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
            { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite" },
            { id: "gemini-2.0-flash", label: "Gemini 2.0 Flash" },
            { id: "gemini-2.0-flash-001", label: "Gemini 2.0 Flash 001" },
            { id: "gemini-2.0-flash-lite", label: "Gemini 2.0 Flash Lite" },
            { id: "gemini-2.0-flash-lite-001", label: "Gemini 2.0 Flash Lite 001" },
            { id: "gemini-pro-latest", label: "Gemini Pro Latest" },
            { id: "gemini-flash-latest", label: "Gemini Flash Latest" },
            { id: "gemini-flash-lite-latest", label: "Gemini Flash Lite Latest" },
            { id: "gemma-4-31b-it", label: "Gemma 4 31B IT" },
            { id: "gemma-4-26b-a4b-it", label: "Gemma 4 26B A4B IT" }
        ],
        deepseek: [
            { id: "deepseek-v4-flash", label: "DeepSeek V4 Flash" },
            { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro" }
        ]
    },

    async init() {
        this.setupEventListeners();
        const isAuthenticated = await this.ensureAuthenticated();
        if (!isAuthenticated) return;
        await this.loadModels();
        this.updateModels();
        await this.loadComponentLibrary();
        await this.loadLayoutTemplates();
        await this.loadRecentSessions();
        await this.loadSessionFromUrl();
    },

    async loadModels() {
        const catalog = await API.getModels();
        if (catalog.providers) {
            this.models = catalog.providers;
            const provider = document.getElementById('provider');
            if (provider && catalog.default_provider) provider.value = catalog.default_provider;
        }
    },

    async loadComponentLibrary() {
        this.componentLibrary = await API.getComponentLibrary();
        UI.renderVisualThemeOptions(this.componentLibrary);
        UI.renderComponentLibraryOptions(this.componentLibrary);
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

    isTerminalSession(session) {
        const status = String(session && session.status ? session.status : '');
        const normalizedStatus = status.toLowerCase();
        return (
            (session && session.current_step === 'completed') ||
            (session && session.current_step === 'failed') ||
            normalizedStatus.includes("concluído") ||
            normalizedStatus.includes("falha") ||
            status === "Concluído"
        );
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

            if (!this.isTerminalSession(session)) {
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

    updateSettingsModels(selectedModel = null) {
        const providerEl = document.getElementById('s-provider');
        const modelEl = document.getElementById('s-model-name');
        if (!providerEl || !modelEl) return;

        const models = this.models[providerEl.value] || [];
        modelEl.innerHTML = models
            .map(m => `<option value="${m.id}">${m.label}</option>`)
            .join('');
        if (selectedModel && models.some(m => m.id === selectedModel)) {
            modelEl.value = selectedModel;
        }
    },

    setupEventListeners() {
        document.getElementById('login-form').onsubmit = (e) => this.handleLogin(e);
        document.getElementById('upload-form').onsubmit = (e) => this.handleStartSession(e);
        document.getElementById('chat-form').onsubmit = (e) => this.handleChat(e);
        document.getElementById('explore-form').onsubmit = (e) => this.handleExplore(e);
        document.getElementById('modal-form').onsubmit = (e) => this.handleSaveIndicator(e);
        document.getElementById('settings-form').onsubmit = (e) => this.handleSaveSettings(e);
        document.getElementById('s-provider').onchange = () => this.updateSettingsModels();
        document.getElementById('m-type').onchange = () => this.updateModalPresetOptions();
        document.getElementById('m-preset').onchange = () => this.populateVisualOptionsFromPreset();
        this.setupFileDropzone();
        document.getElementById('file-preview-modal').addEventListener('close', () => {
            if (this.filePreviewUrl) {
                URL.revokeObjectURL(this.filePreviewUrl);
                this.filePreviewUrl = null;
            }
        });
        document.getElementById('indicator-modal').addEventListener('close', () => {
            this.clearIndicatorPreview();
        });
    },

    setupFileDropzone() {
        const dropzone = document.getElementById('file-dropzone');
        if (!dropzone) return;

        ['dragenter', 'dragover'].forEach(eventName => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropzone.classList.add('drag-over');
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropzone.classList.remove('drag-over');
            });
        });

        dropzone.addEventListener('drop', (event) => {
            const files = event.dataTransfer.files;
            if (files && files.length) this.uploadFiles(files);
        });
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
            await this.loadModels();
            this.updateModels();
            await this.loadComponentLibrary();
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
        document.getElementById('new-session-link').style.display = 'block';
    },

    startPolling() {
        if (this.pollInterval) clearInterval(this.pollInterval);
        this.pollInterval = setInterval(async () => {
            const session = await API.getSession(this.currentSession.job_id);
            this.currentSession = session;
            this.updateUI(session);
            
            if (this.isTerminalSession(session)) {
                clearInterval(this.pollInterval);
                this.pollInterval = null;
            }
        }, 2000);
    },

    updateUI(session) {
        UI.updateStatus(session.status, Number.isFinite(session.progress) ? session.progress : (session.status === "Concluído" ? 100 : 60));
        UI.renderLogs(session.logs);
        UI.renderRefinementChat(session);
        UI.renderFiles(session.files);
        UI.renderArtifacts(session.artifacts);
        UI.renderLayoutCanvas(session);
        UI.renderDataArtifacts(session);
        UI.renderFullScript(session.script);
        UI.renderTemplateOptions(this.layoutTemplates);
        UI.renderVisualThemeOptions(this.componentLibrary);
        UI.renderComponentLibraryOptions(this.componentLibrary);
        const settingsProvider = document.getElementById('s-provider');
        if (settingsProvider) settingsProvider.value = session.provider || 'deepseek';
        this.updateSettingsModels(session.model_name);
        UI.updateSettingsForm(session.settings, session);

        if (session.pdf_version > this.lastPdfVersion) {
            this.refreshPdf(session);
        }

        const indicatorModal = document.getElementById('indicator-modal');
        if (indicatorModal && indicatorModal.open && !this.indicatorPreviewLoading && session.pdf_version > this.indicatorPreviewVersion) {
            this.refreshIndicatorPreview(session);
        } else if (indicatorModal && indicatorModal.open && String(session.status || '').includes('Falha')) {
            const button = document.getElementById('modal-regenerate-btn');
            if (button) {
                button.disabled = false;
                button.textContent = 'Salvar e regerar';
            }
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
        const q = input.value.trim();
        if (!q) return;
        if (!this.currentSession || !this.currentSession.job_id) return;

        const submitButton = e.target.querySelector('button[type="submit"]');
        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = 'Enviando...';
        }

        try {
            input.value = '';
            await API.sendChat(this.currentSession.job_id, q);
            const session = await API.getSession(this.currentSession.job_id);
            this.currentSession = session;
            this.updateUI(session);
            this.startPolling();
        } catch (error) {
            input.value = q;
            const chat = document.getElementById('chat-history');
            chat.innerHTML += `
                <div class="message ai error"><b>Studio:</b> ${UI.escapeHtml(error.message || 'Não foi possível enviar o feedback.')}</div>
            `;
            chat.scrollTop = chat.scrollHeight;
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = 'Enviar Feedback';
            }
        }
    },

    async handleExplore(e) {
        e.preventDefault();
        const input = document.getElementById('explore-input');
        const q = input.value;
        if (!q) return;

        const chat = document.getElementById('explore-chat');
        chat.innerHTML += `<div class="message user"><b>Você:</b> ${UI.escapeHtml(q)}</div>`;
        input.value = '';
        
        const data = await API.exploreData(this.currentSession.job_id, q);
        chat.innerHTML += `<div class="message ai"><b>Analista:</b> ${UI.escapeHtml(data.answer)}</div>`;
        chat.scrollTop = chat.scrollHeight;
    },

    async handleUploadMore(input) {
        if (!input.files.length) return;
        await this.uploadFiles(input.files);
        input.value = '';
    },

    async uploadFiles(files) {
        if (!this.currentSession || !this.currentSession.job_id || !files.length) return;
        const fd = new FormData();
        for (let f of files) fd.append('files', f);
        await API.addFiles(this.currentSession.job_id, fd);
        this.startPolling();
    },

    async previewFile(index) {
        return this.previewFileVariant(index, 'original');
    },

    async previewFileVariant(index, variant = 'original') {
        const file = this.currentSession.files[index];
        if (!file) return;

        const modal = document.getElementById('file-preview-modal');
        const title = document.getElementById('file-preview-title');
        const meta = document.getElementById('file-preview-meta');
        const body = document.getElementById('file-preview-body');
        const downloadBtn = document.getElementById('file-preview-download');
        const filename = file.original_filename || `arquivo-${index + 1}`;
        const isPreprocessed = variant === 'preprocessed';
        const effectiveFilename = isPreprocessed
            ? `${filename.replace(/\.[^.]+$/, '')}_preprocessed.csv`
            : filename;
        const kind = UI.fileKind(effectiveFilename);
        const hasPreprocessed = Boolean(file.preprocessed_path || file.filtered_path);
        this.filePreviewIndex = index;
        this.filePreviewVariant = variant;

        title.textContent = effectiveFilename;
        meta.textContent = [
            isPreprocessed ? 'Pré-processado' : 'Original',
            UI.formatBytes(file.size_bytes || (file.artifact && file.artifact.size_bytes)),
            isPreprocessed ? 'text/csv' : file.mime_type,
            isPreprocessed ? (file.preprocessing_description || file.filter_description) : ''
        ]
            .filter(Boolean)
            .join(' · ');
        body.innerHTML = `
            ${hasPreprocessed ? this.renderFilePreviewTabs(index, variant) : ''}
            <div class="preview-content"><div class="preview-loading">Carregando preview...</div></div>
        `;
        const content = body.querySelector('.preview-content') || body;
        downloadBtn.onclick = () => this.downloadFile(index, variant);
        modal.showModal();

        try {
            const blob = await API.getFileBlob(this.currentSession.job_id, index, 'preview', variant);
            if (this.filePreviewUrl) URL.revokeObjectURL(this.filePreviewUrl);
            this.filePreviewUrl = URL.createObjectURL(blob);

            if (kind === 'image') {
                content.innerHTML = `<img class="preview-image" alt="${UI.escapeHtml(effectiveFilename)}" src="${this.filePreviewUrl}">`;
            } else if (kind === 'pdf') {
                content.innerHTML = `<iframe class="preview-frame" title="${UI.escapeHtml(effectiveFilename)}" src="${this.filePreviewUrl}"></iframe>`;
            } else if (kind === 'text' || kind === 'data') {
                if (effectiveFilename.toLowerCase().match(/\.(xlsx|xls|ods)$/)) {
                    this.renderMetadataPreview(content, file, 'Planilhas podem ser baixadas ou usadas pelo gerador, mas nao abrem como texto no navegador.');
                    return;
                }
                const text = await blob.text();
                content.innerHTML = `<pre class="preview-text">${UI.escapeHtml(text.slice(0, 200000))}</pre>`;
            } else {
                this.renderMetadataPreview(content, file, 'Preview direto indisponivel para este formato.');
            }
        } catch (error) {
            content.innerHTML = '<div class="preview-empty">Nao foi possivel abrir o preview deste arquivo.</div>';
        }
    },

    renderFilePreviewTabs(index, activeVariant) {
        const tab = (variant, label) => `
            <button
                type="button"
                class="file-preview-tab ${activeVariant === variant ? 'active' : ''}"
                onclick="App.previewFileVariant(${index}, '${variant}')"
            >${label}</button>
        `;
        return `
            <div class="file-preview-tabs">
                ${tab('original', 'Original')}
                ${tab('preprocessed', 'Pré-processado')}
            </div>
        `;
    },

    renderMetadataPreview(container, file, message) {
        container.innerHTML = `
            <div class="preview-empty">
                <strong>${UI.escapeHtml(message)}</strong>
                <pre>${UI.escapeHtml(Utils.formatJson(file.artifact || {}))}</pre>
            </div>
        `;
    },

    closeFilePreview() {
        const modal = document.getElementById('file-preview-modal');
        modal.close();
        if (this.filePreviewUrl) {
            URL.revokeObjectURL(this.filePreviewUrl);
            this.filePreviewUrl = null;
        }
        this.filePreviewIndex = null;
        this.filePreviewVariant = 'original';
    },

    async downloadFile(index, variant = 'original') {
        const file = this.currentSession.files[index];
        if (!file) return;

        const blob = await API.getFileBlob(this.currentSession.job_id, index, 'download', variant);
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        const filename = file.original_filename || `arquivo-${index + 1}`;
        link.href = url;
        link.download = variant === 'preprocessed'
            ? `${filename.replace(/\.[^.]+$/, '')}_preprocessed.csv`
            : filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    },

    async deleteFile(index) {
        const file = this.currentSession.files[index];
        if (!file) return;
        if (!confirm(`Excluir "${file.original_filename}" desta sessao?`)) return;

        await API.deleteFile(this.currentSession.job_id, index);
        const session = await API.getSession(this.currentSession.job_id);
        this.currentSession = session;
        this.lastPdfVersion = 0;
        this.updateUI(session);
        if (session.files && session.files.length) this.startPolling();
    },

    handleEditIndicator(index) {
        const art = index === -1 
            ? { indicator: "", type: "bar", columns: [], reason: "" } 
            : this.currentSession.artifacts[index];
        
        document.getElementById('edit-index').value = index;
        document.getElementById('m-name').value = art.indicator;
        document.getElementById('m-type').value = art.type;
        document.getElementById('m-subtype').value = art.sub_type || "";
        document.getElementById('m-preset').value = art.component_preset_id || "";
        document.getElementById('m-visual-theme').value = art.visual_theme_id || "";
        document.getElementById('m-chart-style').value = art.chart_style_id || "";
        document.getElementById('m-visual-options').value = JSON.stringify(art.visual_options || {}, null, 2);
        this.updateModalPresetOptions(art);
        document.getElementById('m-cols').value = (art.columns || []).join(', ');
        document.getElementById('m-reason').value = art.reason || "";
        document.getElementById('m-code').value = index === -1 
            ? "" 
            : (this.currentSession.components[art.id] || "");
        document.getElementById('modal-regenerate-btn').disabled = index === -1;
        this.renderIndicatorPreviewPlaceholder(index);
        
        document.getElementById('indicator-modal').showModal();
        if (index !== -1 && this.currentSession.components[art.id]) {
            this.refreshIndicatorPreview(this.currentSession);
        }
    },

    currentIndicatorPayload() {
        const idx = parseInt(document.getElementById('edit-index').value);
        const id = idx === -1 ? `kpi_${Date.now()}` : this.currentSession.artifacts[idx].id;
        let visualOptions = {};
        const rawOptions = document.getElementById('m-visual-options').value.trim();
        if (rawOptions) {
            try {
                visualOptions = JSON.parse(rawOptions);
            } catch (error) {
                throw new Error('Opções visuais precisam ser um JSON válido.');
            }
        }
        
        const newArt = {
            id,
            indicator: document.getElementById('m-name').value,
            type: document.getElementById('m-type').value,
            sub_type: document.getElementById('m-subtype').value || undefined,
            component_preset_id: document.getElementById('m-preset').value || undefined,
            visual_theme_id: document.getElementById('m-visual-theme').value || undefined,
            chart_style_id: document.getElementById('m-chart-style').value || undefined,
            visual_options: visualOptions,
            columns: document.getElementById('m-cols').value.split(',').map(s => s.trim()),
            reason: document.getElementById('m-reason').value
        };
        return { idx, id, newArt };
    },

    updateModalPresetOptions(art = null) {
        const type = document.getElementById('m-type').value || (art && art.type) || 'graphic';
        const select = document.getElementById('m-preset');
        if (!select) return;
        const presets = (this.componentLibrary.presets || []).filter(p => (p.allowed_types || []).includes(type) || type === 'graphic');
        const currentValue = art ? (art.component_preset_id || "") : (select.value || "");
        select.innerHTML = '<option value="">Automático</option>' + presets.map(p => `
            <option value="${p.id}">${p.name}</option>
        `).join('');
        if (currentValue && Array.from(select.options).some(option => option.value === currentValue)) {
            select.value = currentValue;
        }
    },

    populateVisualOptionsFromPreset() {
        const presetId = document.getElementById('m-preset').value;
        const options = document.getElementById('m-visual-options');
        if (!presetId || !options || options.value.trim()) return;
        const preset = (this.componentLibrary.presets || []).find(item => item.id === presetId);
        if (preset && preset.default_options) {
            options.value = JSON.stringify(preset.default_options, null, 2);
        }
    },

    async saveCurrentIndicator(options = {}) {
        const { idx, id, newArt } = this.currentIndicatorPayload();

        if (idx === -1) {
            this.currentSession.artifacts.push(newArt);
        } else {
            this.currentSession.artifacts[idx] = newArt;
        }

        const overrides = {};
        const code = document.getElementById('m-code').value;
        if (code) overrides[id] = code;

        await API.syncArtifacts(this.currentSession.job_id, this.currentSession.artifacts, overrides, {
            deferPipeline: Boolean(options.deferPipeline)
        });
        return { idx, id };
    },

    async handleSaveIndicator(e) {
        e.preventDefault();
        try {
            await this.saveCurrentIndicator();
            document.getElementById('indicator-modal').close();
            this.startPolling();
        } catch (error) {
            alert(error.message || 'Não foi possível salvar o componente.');
        }
    },

    async handleRegenerateCurrentIndicator() {
        const button = document.getElementById('modal-regenerate-btn');
        const preview = document.getElementById('indicator-preview-body');
        try {
            button.disabled = true;
            button.textContent = 'Regenerando...';
            preview.innerHTML = '<div class="preview-loading">Salvando alterações e regenerando este componente...</div>';
            const { id } = await this.saveCurrentIndicator({ deferPipeline: true });
            await API.regenerate(this.currentSession.job_id, 'component', this.collectSettings(), id);
            this.startPolling();
        } catch (error) {
            preview.innerHTML = `<div class="preview-empty">${UI.escapeHtml(error.message || 'Não foi possível regenerar o componente.')}</div>`;
            button.disabled = false;
            button.textContent = 'Salvar e regerar';
        }
    },

    renderIndicatorPreviewPlaceholder(index) {
        const preview = document.getElementById('indicator-preview-body');
        if (index === -1) {
            preview.innerHTML = '<div class="preview-empty">Salve o componente antes de gerar uma prévia.</div>';
            return;
        }

        const art = this.currentSession.artifacts[index];
        if (!art || !this.currentSession.components[art.id]) {
            preview.innerHTML = '<div class="preview-empty">A prévia aparecerá depois que o componente for gerado.</div>';
        } else {
            preview.innerHTML = '<div class="preview-loading">Carregando prévia...</div>';
        }
    },

    async refreshIndicatorPreview(session) {
        const preview = document.getElementById('indicator-preview-body');
        if (!preview) return;
        if (this.indicatorPreviewLoading) return;
        const idx = parseInt(document.getElementById('edit-index').value);
        const art = idx === -1 ? null : session.artifacts[idx];
        if (!art || !session.components || !session.components[art.id]) {
            preview.innerHTML = '<div class="preview-empty">Aguardando o código renderizável deste componente.</div>';
            return;
        }

        this.indicatorPreviewLoading = true;
        try {
            const url = await API.getComponentPreviewObjectUrl(session.job_id, art.id, session.pdf_version);
            if (this.indicatorPreviewUrl) URL.revokeObjectURL(this.indicatorPreviewUrl);
            this.indicatorPreviewUrl = url;
            this.indicatorPreviewVersion = session.pdf_version;
            preview.innerHTML = `<iframe class="indicator-preview-frame" title="Prévia do componente" src="${url}#toolbar=0&navpanes=0&view=Fit"></iframe>`;
            const button = document.getElementById('modal-regenerate-btn');
            if (button) {
                button.disabled = false;
                button.textContent = 'Salvar e regerar';
            }
        } catch (error) {
            preview.innerHTML = '<div class="preview-empty">Não foi possível carregar a prévia renderizada.</div>';
        } finally {
            this.indicatorPreviewLoading = false;
        }
    },

    clearIndicatorPreview() {
        if (this.indicatorPreviewUrl) {
            URL.revokeObjectURL(this.indicatorPreviewUrl);
            this.indicatorPreviewUrl = null;
        }
        this.indicatorPreviewVersion = 0;
        this.indicatorPreviewLoading = false;
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

    collectSettings() {
        return {
            title: document.getElementById('s-title').value,
            font: document.getElementById('s-font').value,
            font_size: parseInt(document.getElementById('s-font-size').value),
            alignment: document.getElementById('s-alignment').value,
            line_height: parseInt(document.getElementById('s-line-height').value),
            language: document.getElementById('s-language').value,
            selected_template_id: document.getElementById('s-layout-template').value,
            visual_theme_id: document.getElementById('s-visual-theme').value,
            chart_style_id: document.getElementById('s-chart-style').value,
            visual_instructions: document.getElementById('s-visual-instructions').value,
            provider: document.getElementById('s-provider').value,
            model_name: document.getElementById('s-model-name').value
        };
    },

    async handleSaveSettings(e) {
        e.preventDefault();
        const settings = this.collectSettings();
        await API.updateSettings(this.currentSession.job_id, settings);
        this.startPolling();
    },

    async handleRegenerate(scope) {
        const labels = {
            texts: 'atualizar os textos',
            graphics: 'atualizar os gráficos',
            component: 'atualizar este componente',
            all: 'atualizar todo o relatório'
        };
        if (!confirm(`Deseja ${labels[scope] || 'atualizar o relatório'} com o provider e modelo atuais?`)) return;
        await API.regenerate(this.currentSession.job_id, scope, this.collectSettings());
        this.startPolling();
    },

    copyFullScript() {
        const script = document.getElementById('full-script-content').textContent;
        navigator.clipboard.writeText(script);
        alert("Script copiado para a área de transferência!");
    }
};

window.onload = () => App.init();
