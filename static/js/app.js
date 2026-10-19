const App = {
    currentSession: null,
    pollInterval: null,
    pollGeneration: 0,
    pdfLoading: false,
    settingsDirty: false,
    settingsSignature: null,
    lastPdfVersion: 0,
    pdfObjectUrl: null,
    indicatorPreviewUrl: null,
    indicatorPreviewVersion: 0,
    indicatorPreviewLoading: false,
    indicatorPreviewRequest: 0,
    explorePending: null,
    indicatorModalFocusTarget: null,
    filePreviewUrl: null,
    filePreviewIndex: null,
    filePreviewVariant: 'original',
    filePreviewFocusTarget: null,
    layoutTemplates: [],
    reportLayoutEditor: null,
    reportLayoutJobId: null,
    layoutFocused: false,
    componentLibrary: {
        presets: [],
        themes: [],
        chart_styles: [],
        default_theme_id: 'executive_clean',
        default_chart_style_id: 'modern'
    },
    models: {},
    defaultProvider: 'deepseek',
    defaultModelName: 'deepseek-flash',
    modelDefaults: {},

    async init() {
        this.setupEventListeners();
        const isAuthenticated = await this.ensureAuthenticated();
        if (!isAuthenticated) return;
        await this.loadModels();
        this.updateModels();
        await this.loadComponentLibrary();
        await this.loadLayoutTemplates();
        await this.loadRecentSessions();
        if (typeof SavedSources !== 'undefined') {
            try { await SavedSources.init(); } catch (error) { this.showNotice(error.message); }
        }
        await this.loadSessionFromUrl();
    },

    async loadModels() {
        const catalog = await API.getModels();
        if (catalog.providers) {
            this.models = catalog.providers;
            this.modelDefaults = catalog.defaults || {};
            this.defaultProvider = catalog.default_provider || this.defaultProvider;
            this.defaultModelName = catalog.default_model || this.defaultModelName;
            const provider = document.getElementById('provider');
            for (const id of ['provider', 's-provider']) {
                const select = document.getElementById(id);
                if (!select) continue;
                const selected = select.value;
                UI.clearElement(select);
                for (const [providerId, models] of Object.entries(catalog.providers)) {
                    const status = catalog.credential_status?.[providerId];
                    const option = UI.buildOption(providerId, status?.label || providerId);
                    option.disabled = status ? !status.enabled : false;
                    select.appendChild(option);
                }
                select.value = selected || this.defaultProvider;
            }
            if (provider && catalog.default_provider) provider.value = catalog.default_provider;
            if (catalog.credential_status && !Object.values(catalog.credential_status).some(status => status.enabled)) {
                this.showNotice('Adicione sua chave de API para gerar relatórios.', { code: 'missing_provider_key' });
            }
        }
    },

    async loadComponentLibrary() {
        this.componentLibrary = await API.getComponentLibrary();
        UI.renderVisualThemeOptions(this.componentLibrary);
        UI.renderComponentLibraryOptions(this.componentLibrary);
    },

    async ensureAuthenticated() {
        try {
            this.user = await API.me();
        } catch (error) {
            API.redirectToLogin();
            return false;
        }
        document.getElementById('app-shell').style.display = 'flex';
        Utils.setupAccountBar(this.user);
        this.renderVerifyBanner();
        return true;
    },

    renderVerifyBanner() {
        const banner = document.getElementById('verify-banner');
        if (!banner) return;
        banner.hidden = Boolean(this.user?.email_verified);
        document.getElementById('verify-email-address').textContent = this.user?.email || '';
        document.getElementById('resend-verification-btn').onclick = async () => {
            const status = document.getElementById('verify-banner-status');
            try {
                status.textContent = (await API.resendVerification()).message || 'Link enviado.';
            } catch (error) {
                status.textContent = error.message;
            }
        };
    },

    getSessionIdFromUrl() {
        const match = window.location.pathname.match(/^\/session\/([^/]+)$/);
        return match ? decodeURIComponent(match[1]) : null;
    },

    hasPendingExploration(session) {
        return (session?.explore_history || []).some(item => item && item.role === 'assistant' && item.status === 'pending');
    },

    isTerminalSession(session) {
        // Explorer answers arrive in the background; keep polling until they land.
        if (this.hasPendingExploration(session)) return false;
        if (['queued', 'running'].includes(session?.source_import?.status)) return false;
        if (session && session.current_step) {
            return ['completed', 'failed', 'awaiting_clarification', 'translation_failed'].includes(session.current_step);
        }
        const status = String(session && session.status ? session.status : '');
        const normalizedStatus = status.toLowerCase();
        return (
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
            const chat = document.getElementById('chat-history');
            const message = document.createElement('div');
            message.className = 'message ai';
            const label = document.createElement('b');
            label.textContent = 'Estúdio:';
            const text = document.createElement('span');
            text.textContent = ` Sessão não encontrada: ${jobId}`;
            message.appendChild(label);
            message.appendChild(text);
            chat.appendChild(message);
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
        const providerEl = document.getElementById('provider');
        const select = document.getElementById('model_name');
        if (!providerEl || !select) return;

        const models = this.models[providerEl.value] || [];
        UI.clearElement(select);
        models.forEach(m => select.appendChild(UI.buildOption(m.id, m.label)));
        const submit = document.querySelector('#upload-form button[type="submit"]');
        if (submit) submit.disabled = !models.length;
        const defaultModel = this.modelDefaults[providerEl.value] || this.defaultModelName;
        if (models.some(m => m.id === defaultModel)) {
            select.value = defaultModel;
        }
        this.updateModelDefaultNotice('provider', 'model_name', 'model-default-notice');
    },

    updateSettingsModels(selectedModel = null) {
        const providerEl = document.getElementById('s-provider');
        const modelEl = document.getElementById('s-model-name');
        if (!providerEl || !modelEl) return;

        const models = this.models[providerEl.value] || [];
        UI.clearElement(modelEl);
        models.forEach(m => modelEl.appendChild(UI.buildOption(m.id, m.label)));
        if (selectedModel && models.some(m => m.id === selectedModel)) {
            modelEl.value = selectedModel;
        } else if (models.some(m => m.id === (this.modelDefaults[providerEl.value] || this.defaultModelName))) {
            modelEl.value = this.modelDefaults[providerEl.value] || this.defaultModelName;
        }
        this.updateModelDefaultNotice('s-provider', 's-model-name', 'settings-model-default-notice');
    },

    getDefaultModelLabel() {
        const defaultModels = this.models[this.defaultProvider] || [];
        const defaultModel = defaultModels.find(m => m.id === this.defaultModelName);
        return defaultModel ? defaultModel.label : this.defaultModelName;
    },

    updateModelDefaultNotice(providerId, modelId, noticeId) {
        const providerEl = document.getElementById(providerId);
        const modelEl = document.getElementById(modelId);
        const noticeEl = document.getElementById(noticeId);
        if (!providerEl || !modelEl || !noticeEl) return;

        const usesDefault = providerEl.value === this.defaultProvider && modelEl.value === this.defaultModelName;
        noticeEl.textContent = `Recomendado: use o padrão ${this.getDefaultModelLabel()} para melhores resultados.`;
        noticeEl.hidden = usesDefault;
    },

    setupEventListeners() {
        document.getElementById('upload-form').onsubmit = (e) => this.handleStartSession(e);
        document.getElementById('api-source-form').onsubmit = (e) => this.handleApiImport(e);
        document.getElementById('chat-form').onsubmit = (e) => this.handleChat(e);
        document.getElementById('explore-form').onsubmit = (e) => this.handleExplore(e);
        document.getElementById('modal-form').onsubmit = (e) => this.handleSaveIndicator(e);
        document.getElementById('settings-form').onsubmit = (e) => this.handleSaveSettings(e);
        document.getElementById('settings-form').addEventListener('input', () => { this.settingsDirty = true; });
        document.getElementById('settings-form').addEventListener('change', () => { this.settingsDirty = true; });
        window.addEventListener('beforeunload', event => {
            if (this.reportLayoutEditor?.dirty) { event.preventDefault(); event.returnValue = ''; }
        });
        document.getElementById('provider').addEventListener('change', () => {
            this.updateModels();
        });
        document.getElementById('model_name').addEventListener('change', () => {
            this.updateModelDefaultNotice('provider', 'model_name', 'model-default-notice');
        });
        document.getElementById('s-provider').addEventListener('change', () => {
            this.updateSettingsModels();
        });
        document.getElementById('s-model-name').addEventListener('change', () => {
            this.updateModelDefaultNotice('s-provider', 's-model-name', 'settings-model-default-notice');
        });
        document.getElementById('m-type').addEventListener('change', () => {
            this.updateModalPresetOptions();
            UI.renderPresetInspector(this.componentLibrary);
        });
        document.getElementById('m-preset').addEventListener('change', () => {
            this.populateVisualOptionsFromPreset();
            UI.renderPresetInspector(this.componentLibrary);
        });
        document.getElementById('m-visual-theme').addEventListener('change', () => UI.renderPresetInspector(this.componentLibrary));
        document.getElementById('m-chart-style').addEventListener('change', () => UI.renderPresetInspector(this.componentLibrary));
        document.getElementById('m-render-mode').addEventListener('change', () => {
            const idx = parseInt(document.getElementById('edit-index').value);
            const art = idx === -1 ? {} : (this.currentSession.artifacts[idx] || {});
            this.updateIndicatorCodeField(
                { ...art, render_mode: document.getElementById('m-render-mode').value },
                this.currentSession.components && art.id ? (this.currentSession.components[art.id] || '') : ''
            );
            UI.renderPresetInspector(this.componentLibrary);
        });
        document.getElementById('m-data-binding').addEventListener('input', () => UI.renderPresetInspector(this.componentLibrary));
        document.getElementById('add-file-input').addEventListener('change', (event) => this.handleUploadMore(event.target));
        document.addEventListener('click', (event) => {
            this.handleDocumentClick(event).catch(error => this.showNotice(error.message));
        });
        this.setupFileDropzone();
        document.getElementById('file-preview-modal').addEventListener('close', () => {
            if (this.filePreviewUrl) {
                URL.revokeObjectURL(this.filePreviewUrl);
                this.filePreviewUrl = null;
            }
            this.restoreFilePreviewFocus();
        });
        document.getElementById('indicator-modal').addEventListener('close', () => {
            this.clearIndicatorPreview();
            this.restoreIndicatorModalFocus();
        });
    },

    rememberIndicatorModalFocus() {
        this.indicatorModalFocusTarget = document.activeElement || null;
    },

    restoreIndicatorModalFocus() {
        const target = this.indicatorModalFocusTarget;
        this.indicatorModalFocusTarget = null;
        if (target && typeof target.focus === 'function') {
            target.focus();
        }
    },

    openIndicatorModal() {
        this.rememberIndicatorModalFocus();
        document.getElementById('indicator-modal').showModal();
    },

    closeIndicatorModal() {
        document.getElementById('indicator-modal').close();
    },

    rememberFilePreviewFocus() {
        this.filePreviewFocusTarget = document.activeElement || null;
    },

    restoreFilePreviewFocus() {
        const target = this.filePreviewFocusTarget;
        this.filePreviewFocusTarget = null;
        if (target && typeof target.focus === 'function') {
            target.focus();
        }
    },

    async handleDocumentClick(event) {
        const target = event.target.closest('[data-action], [data-tab-target]');
        if (!target) return;

        if (target.dataset.tabTarget) {
            Utils.openTab(target.dataset.tabTarget, event);
            if (target.dataset.tabTarget !== 'tab-design') this.setLayoutFocus(false);
            else if (window.innerWidth < 1050) this.setLayoutFocus(true);
            if (target.dataset.tabTarget === 'tab-design') requestAnimationFrame(() => {
                if (this.reportLayoutEditor?.fit) this.reportLayoutEditor.fitPage();
            });
            return;
        }

        switch (target.dataset.action) {
            case 'explore-retry':
                if (this.explorePending?.jobId === this.currentSession?.job_id) break;
                document.getElementById('explore-input').value = target.dataset.prompt || '';
                document.getElementById('explore-form').requestSubmit();
                break;
            case 'trigger-add-file':
                document.getElementById('add-file-input').click();
                break;
            case 'add-layout-page':
                this.addLayoutPage();
                break;
            case 'save-visual-layout':
                await this.handleSaveVisualLayout();
                break;
            case 'toggle-layout-focus':
                this.setLayoutFocus(!this.layoutFocused);
                break;
            case 'edit-indicator-new':
                this.handleEditIndicator(-1);
                break;
            case 'copy-full-script':
                this.copyFullScript();
                break;
            case 'regenerate-texts':
                await this.handleRegenerate('texts');
                break;
            case 'regenerate-graphics':
                await this.handleRegenerate('graphics');
                break;
            case 'regenerate-all':
                await this.handleRegenerate('all');
                break;
            case 'close-indicator-modal':
                this.closeIndicatorModal();
                break;
            case 'regenerate-current-indicator':
                await this.handleRegenerateCurrentIndicator();
                break;
            case 'close-file-preview':
                this.closeFilePreview();
                break;
            case 'preview-file':
                await this.previewFile(parseInt(target.dataset.fileIndex, 10));
                break;
            case 'preview-file-variant':
                await this.previewFileVariant(parseInt(target.dataset.fileIndex, 10), target.dataset.variant);
                break;
            case 'download-file':
                await this.downloadFile(parseInt(target.dataset.fileIndex, 10));
                break;
            case 'delete-file':
                await this.deleteFile(parseInt(target.dataset.fileIndex, 10));
                break;
            case 'edit-indicator':
                this.handleEditIndicator(parseInt(target.dataset.index, 10));
                break;
            case 'delete-indicator':
                await this.handleDeleteIndicator(parseInt(target.dataset.index, 10));
                break;
            case 'delete-layout-block':
                await this.handleDeleteIndicatorById(target.dataset.componentId, event);
                break;
            case 'select-template-preview':
                UI.selectTemplatePreview(target.dataset.templateId);
                break;
            default:
                break;
        }
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
            if (files && files.length) this.uploadFiles(files).catch(error => this.showNotice(error.message));
        });
    },

    async handleStartSession(e) {
        e.preventDefault();
        const button = e.target.querySelector('button[type="submit"]');
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = 'Enviando arquivos...';
        this.showNotice('');
        try {
            const files = document.getElementById('file').files;
            const apiUrl = document.getElementById('api-url').value.trim();
            const sources = typeof SavedSources !== 'undefined' ? SavedSources.selections('initial-source-list') : [];
            if (!files.length && !apiUrl && !sources.length) throw new Error('Envie um arquivo, informe uma API JSON ou selecione um conjunto salvo.');
            const formData = new FormData(e.target);
            formData.set('source_datasets', JSON.stringify(sources));
            const data = await API.startSession(formData);
            this.currentSession = { job_id: data.job_id };
            window.history.replaceState({}, '', `/session/${data.job_id}`);
            this.showWorkspace();
            this.startPolling();
        } catch (error) {
            this.showNotice(error.message);
        } finally {
            button.disabled = false;
            button.textContent = 'Iniciar Geração';
        }
    },

    showNotice(message, error = null) {
        const notice = document.getElementById('workspace-notice');
        if (!notice) return;
        notice.textContent = message || '';
        notice.hidden = !message;
        if (['missing_provider_key', 'invalid_provider_key'].includes(error?.code)) {
            const link = document.createElement('a');
            link.href = '/keys';
            link.textContent = 'Abrir Chaves';
            notice.append(' ', link);
        }
    },

    showWorkspace() {
        document.getElementById('initial-form').style.display = 'none';
        document.getElementById('file-workspace').style.display = 'block';
        document.getElementById('header-progress').style.display = 'block';
        document.getElementById('chat-form').style.display = 'block';
        document.getElementById('new-session-link').style.display = 'block';
    },

    startPolling() {
        if (this.pollInterval) clearTimeout(this.pollInterval);
        const generation = ++this.pollGeneration;
        const jobId = this.currentSession && this.currentSession.job_id;
        if (!jobId) return;
        let failures = 0;
        const poll = async () => {
            this.pollInterval = null;
            try {
                const session = await API.getSession(jobId);
                if (generation !== this.pollGeneration || this.currentSession.job_id !== jobId) return;
                failures = 0;
                this.currentSession = session;
                this.updateUI(session);
                if (this.isTerminalSession(session)) return;
            } catch (error) {
                if (generation !== this.pollGeneration) return;
                failures += 1;
                this.showNotice(error.message);
                if (error.status === 401 || error.status === 404) return;
            }
            if (generation === this.pollGeneration) {
                this.pollInterval = setTimeout(poll, Math.min(15000, 2000 * (failures + 1)));
            }
        };
        // Fetch immediately, then wait for each response before scheduling another.
        poll();
    },

    updateUI(session) {
        UI.updateStatus(session.status, Number.isFinite(session.progress) ? session.progress : (session.status === "Concluído" ? 100 : 60));
        UI.renderLogs(session.logs);
        UI.renderRefinementChat(session);
        UI.renderExploreChat(session, this.explorePending?.jobId === session.job_id ? this.explorePending : null);
        UI.renderFiles(session.files);
        if (typeof SavedSources !== 'undefined') SavedSources.update(session);
        UI.renderArtifacts(session.artifacts);
        UI.renderLayoutCanvas(session);
        UI.renderDataArtifacts(session);
        UI.renderFullScript(session.script);
        const signature = JSON.stringify([session.job_id, session.settings, session.provider, session.model_name]);
        if (!this.settingsDirty && signature !== this.settingsSignature) {
            UI.renderTemplateOptions(this.layoutTemplates);
            UI.renderVisualThemeOptions(this.componentLibrary);
            UI.renderComponentLibraryOptions(this.componentLibrary);
            const settingsProvider = document.getElementById('s-provider');
            if (settingsProvider) settingsProvider.value = session.provider || this.defaultProvider;
            this.updateSettingsModels(session.model_name);
            UI.updateSettingsForm(session.settings, session);
            this.settingsSignature = signature;
            this.updateModelDefaultNotice('s-provider', 's-model-name', 'settings-model-default-notice');
        }
        const fallback = session.llm_usage?.last_fallback;
        const fallbackNotice = fallback?.served_model ? `Modelo alternativo utilizado: ${fallback.provider} · ${fallback.served_model}.` : '';
        this.showNotice(session.error?.message || fallbackNotice, session.error);

        if (session.pdf_version > this.lastPdfVersion && !this.pdfLoading) {
            this.refreshPdf(session);
        }

        const indicatorModal = document.getElementById('indicator-modal');
        if (indicatorModal && indicatorModal.open && !this.indicatorPreviewLoading && session.pdf_version > this.indicatorPreviewVersion) {
            this.refreshIndicatorPreview(session);
        } else if (indicatorModal && indicatorModal.open && String(session.status || '').includes('Falha')) {
            const button = document.getElementById('modal-regenerate-btn');
            if (button) {
                button.disabled = false;
                button.textContent = 'Salvar e gerar novamente';
            }
        }
    },

    async refreshPdf(session) {
        this.pdfLoading = true;
        try {
            const url = await API.getPdfObjectUrl(session.job_id, session.pdf_version);
            if (!this.currentSession || this.currentSession.job_id !== session.job_id) {
                URL.revokeObjectURL(url);
                return;
            }
            if (this.pdfObjectUrl) URL.revokeObjectURL(this.pdfObjectUrl);
            this.pdfObjectUrl = url;
            document.getElementById('pdf-viewer').src = url;
            const dlBtn = document.getElementById('download-btn');
            dlBtn.style.display = 'block';
            dlBtn.onclick = () => {
                const link = document.createElement('a');
                link.href = url;
                link.download = `relatorio-${session.job_id}.pdf`;
                link.click();
            };
            this.lastPdfVersion = session.pdf_version;
        } catch (error) {
            this.showNotice(error.message);
        } finally {
            this.pdfLoading = false;
        }
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
            const message = document.createElement('div');
            message.className = 'message ai error';
            const label = document.createElement('b');
            label.textContent = 'Estúdio:';
            const text = document.createElement('span');
            text.textContent = ` ${error.message || 'Não foi possível enviar o feedback.'}`;
            message.appendChild(label);
            message.appendChild(text);
            chat.appendChild(message);
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
        const q = input.value.trim();
        const jobId = this.currentSession?.job_id;
        if (!q || !jobId || this.explorePending?.jobId === jobId || this.hasPendingExploration(this.currentSession)) return;
        const request = { jobId, prompt: q, historyLength: this.currentSession.explore_history?.length || 0 };
        this.explorePending = request;
        input.value = '';
        UI.renderExploreChat(this.currentSession, request);
        try {
            // The server stores the question with a pending answer and finishes it in
            // the background; polling picks up the result.
            const data = await API.exploreData(jobId, q);
            if (this.currentSession?.job_id !== jobId || this.explorePending !== request) return;
            if (Array.isArray(data.history)) this.currentSession.explore_history = data.history;
            this.startPolling();
        } catch (error) {
            if (this.currentSession?.job_id !== jobId || this.explorePending !== request) return;
            this.currentSession.explore_history = Array.isArray(error.details?.history) ? error.details.history : [
                ...(this.currentSession.explore_history || []),
                { role: 'user', content: q },
                { role: 'assistant', content: error.message || 'Não foi possível executar a consulta.', status: 'error' }
            ];
            if (!input.value.trim()) input.value = q;
        } finally {
            if (this.explorePending === request) {
                this.explorePending = null;
                if (this.currentSession) UI.renderExploreChat(this.currentSession);
            }
        }
    },

    async handleUploadMore(input) {
        if (!input.files.length) return;
        try {
            await this.uploadFiles(input.files);
        } catch (error) {
            this.showNotice(error.message);
        } finally {
            input.value = '';
        }
    },

    async uploadFiles(files) {
        if (!this.currentSession || !this.currentSession.job_id || !files.length) return;
        const fd = new FormData();
        for (let f of files) fd.append('files', f);
        await API.addFiles(this.currentSession.job_id, fd);
        this.startPolling();
    },

    async handleApiImport(event) {
        event.preventDefault();
        if (!this.currentSession?.job_id) return;
        const form = event.target;
        const button = form.querySelector('button[type="submit"]');
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = 'Importando...';
        this.showNotice('');
        try {
            await API.addFiles(this.currentSession.job_id, new FormData(form));
            form.reset();
            this.startPolling();
        } catch (error) {
            this.showNotice(error.message);
        } finally {
            button.disabled = false;
            button.textContent = 'Importar dados';
        }
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
        UI.clearElement(body);
        if (hasPreprocessed) {
            body.appendChild(this.renderFilePreviewTabs(index, variant));
        }
        const previewContent = document.createElement('div');
        previewContent.className = 'preview-content';
        const loading = document.createElement('div');
        loading.className = 'preview-loading';
        loading.textContent = 'Carregando preview...';
        previewContent.appendChild(loading);
        body.appendChild(previewContent);
        const content = body.querySelector('.preview-content') || body;
        downloadBtn.onclick = null;
        downloadBtn.addEventListener('click', () => this.downloadFile(index, variant), { once: true });
        this.rememberFilePreviewFocus();
        modal.showModal();

        try {
            const blob = await API.getFileBlob(this.currentSession.job_id, index, 'preview', variant);
            if (this.filePreviewUrl) URL.revokeObjectURL(this.filePreviewUrl);
            this.filePreviewUrl = URL.createObjectURL(blob);

            if (kind === 'image') {
                UI.clearElement(content);
                const img = document.createElement('img');
                img.className = 'preview-image';
                img.alt = effectiveFilename;
                img.src = this.filePreviewUrl;
                content.appendChild(img);
            } else if (kind === 'pdf') {
                UI.clearElement(content);
                const iframe = document.createElement('iframe');
                iframe.className = 'preview-frame';
                iframe.title = effectiveFilename;
                iframe.src = this.filePreviewUrl;
                content.appendChild(iframe);
            } else if (kind === 'text' || kind === 'data') {
                if (effectiveFilename.toLowerCase().match(/\.(xlsx|xls|ods)$/)) {
                    this.renderMetadataPreview(content, file, 'Planilhas podem ser baixadas ou usadas pelo gerador, mas nao abrem como texto no navegador.');
                    return;
                }
                const sourceEncoding = isPreprocessed ? 'utf-8' : (file.validation?.encoding || file.artifact?.encoding || 'utf-8');
                const encoding = sourceEncoding === 'utf-8-sig' ? 'utf-8' : sourceEncoding;
                const text = encoding === 'utf-8' ? await blob.text() : new TextDecoder(encoding).decode(await blob.arrayBuffer());
                UI.clearElement(content);
                const pre = document.createElement('pre');
                pre.className = 'preview-text';
                pre.textContent = text.slice(0, 200000);
                content.appendChild(pre);
            } else {
                this.renderMetadataPreview(content, file, 'Preview direto indisponivel para este formato.');
            }
        } catch (error) {
            content.textContent = '';
            const empty = document.createElement('div');
            empty.className = 'preview-empty';
            empty.textContent = 'Nao foi possivel abrir o preview deste arquivo.';
            content.appendChild(empty);
        }
    },

    renderFilePreviewTabs(index, activeVariant) {
        const wrapper = document.createElement('div');
        wrapper.className = 'file-preview-tabs';
        [
            ['original', 'Original'],
            ['preprocessed', 'Pré-processado'],
        ].forEach(([variant, label]) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `file-preview-tab ${activeVariant === variant ? 'active' : ''}`;
            button.dataset.action = 'preview-file-variant';
            button.dataset.fileIndex = index;
            button.dataset.variant = variant;
            button.textContent = label;
            wrapper.appendChild(button);
        });
        return wrapper;
    },

    renderMetadataPreview(container, file, message) {
        UI.clearElement(container);
        const empty = document.createElement('div');
        empty.className = 'preview-empty';
        const strong = document.createElement('strong');
        strong.textContent = message;
        const pre = document.createElement('pre');
        pre.textContent = Utils.formatJson(file.artifact || {});
        empty.appendChild(strong);
        empty.appendChild(pre);
        container.appendChild(empty);
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
        const customCode = index === -1 ? "" : ((this.currentSession.components || {})[art.id] || "");

        document.getElementById('edit-index').value = index;
        document.getElementById('m-name').value = art.indicator;
        document.getElementById('m-type').value = art.type;
        document.getElementById('m-subtype').value = art.sub_type || "";
        document.getElementById('m-preset').value = art.component_preset_id || "";
        document.getElementById('m-visual-theme').value = art.visual_theme_id || "";
        document.getElementById('m-chart-style').value = art.chart_style_id || "";
        document.getElementById('m-visual-options').value = JSON.stringify(art.visual_options || {}, null, 2);
        document.getElementById('m-render-mode').value = art.render_mode || "";
        document.getElementById('m-data-binding').value = JSON.stringify(art.data_binding || {}, null, 2);
        this.updateModalPresetOptions(art);
        document.getElementById('m-cols').value = (art.columns || []).join(', ');
        document.getElementById('m-reason').value = art.reason || "";
        this.updateIndicatorCodeField(art, customCode);
        document.getElementById('modal-regenerate-btn').disabled = index === -1;
        UI.renderPresetInspector(this.componentLibrary);
        this.renderIndicatorPreviewPlaceholder(index);
        
        this.openIndicatorModal();
        if (index !== -1 && this.isRenderableIndicator(art, this.currentSession)) {
            this.refreshIndicatorPreview(this.currentSession);
        }
    },

    isLibraryRenderedArtifact(art) {
        if (!art) return false;
        const mode = art.render_mode || 'generated_code';
        return ['library', 'hybrid'].includes(mode) && Boolean(art.component_preset_id);
    },

    isRenderableIndicator(art, session = this.currentSession) {
        if (!art || !session) return false;
        return Boolean((session.components || {})[art.id]) || this.isLibraryRenderedArtifact(art);
    },

    libraryCodePreview(art) {
        const suffix = String((art && art.id) || 'component').replace(/[^a-zA-Z0-9_]/g, '_');
        return [
            '# Renderizado pela biblioteca de componentes.',
            '# Altere "Modo de renderização" para "Código gerado" para editar Python customizado.',
            `def render_${suffix}(pdf, dataframes, x, y, w, h, df=None):`,
            '    return render_visual_component(pdf, dataframes, artifact, x, y, w, h)'
        ].join('\n');
    },

    updateIndicatorCodeField(art, customCode = '') {
        const field = document.getElementById('m-code');
        if (!field) return;
        const mode = document.getElementById('m-render-mode').value || (art && art.render_mode) || 'generated_code';
        const libraryOnly = mode === 'library' && this.isLibraryRenderedArtifact({ ...art, render_mode: mode });
        field.readOnly = libraryOnly;
        field.dataset.placeholderCode = libraryOnly ? '1' : '';
        field.value = libraryOnly ? this.libraryCodePreview({ ...art, render_mode: mode }) : customCode;
    },

    currentIndicatorPayload() {
        const idx = parseInt(document.getElementById('edit-index').value);
        const id = idx === -1 ? `kpi_${Date.now()}` : this.currentSession.artifacts[idx].id;
        let visualOptions = {};
        let dataBinding = {};
        const rawOptions = document.getElementById('m-visual-options').value.trim();
        if (rawOptions) {
            try {
                visualOptions = JSON.parse(rawOptions);
            } catch (error) {
                throw new Error('Opções visuais precisam ser um JSON válido.');
            }
        }
        const rawDataBinding = document.getElementById('m-data-binding').value.trim();
        if (rawDataBinding) {
            try {
                dataBinding = JSON.parse(rawDataBinding);
            } catch (error) {
                throw new Error('Data binding precisa ser um JSON válido.');
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
            data_binding: Object.keys(dataBinding).length ? dataBinding : undefined,
            render_mode: document.getElementById('m-render-mode').value || undefined,
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
        UI.clearElement(select);
        select.appendChild(UI.buildOption('', 'Automático'));
        presets.forEach(p => select.appendChild(UI.buildOption(p.id, p.name)));
        if (currentValue && Array.from(select.options).some(option => option.value === currentValue)) {
            select.value = currentValue;
        }
        UI.renderPresetInspector(this.componentLibrary);
    },

    populateVisualOptionsFromPreset() {
        const presetId = document.getElementById('m-preset').value;
        const options = document.getElementById('m-visual-options');
        if (!presetId || !options || options.value.trim()) return;
        const preset = (this.componentLibrary.presets || []).find(item => item.id === presetId);
        if (preset && preset.default_options) {
            options.value = JSON.stringify(preset.default_options, null, 2);
        }
        UI.renderPresetInspector(this.componentLibrary);
    },

    async saveCurrentIndicator(options = {}) {
        const { idx, id, newArt } = this.currentIndicatorPayload();
        const artifacts = [...this.currentSession.artifacts];
        if (idx === -1) {
            artifacts.push(newArt);
        } else {
            artifacts[idx] = newArt;
        }

        const overrides = {};
        const code = document.getElementById('m-code').value;
        const isPlaceholderCode = document.getElementById('m-code').dataset.placeholderCode === '1';
        if (code && !isPlaceholderCode) overrides[id] = code;

        await API.syncArtifacts(this.currentSession.job_id, artifacts, overrides, {
            deferPipeline: Boolean(options.deferPipeline)
        });
        this.currentSession.artifacts = artifacts;
        return { idx, id };
    },

    async handleSaveIndicator(e) {
        e.preventDefault();
        try {
            await this.saveCurrentIndicator();
            this.closeIndicatorModal();
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
            button.textContent = 'Gerando novamente...';
            UI.clearElement(preview);
            const loading = document.createElement('div');
            loading.className = 'preview-loading';
            loading.textContent = 'Salvando alterações e regenerando este componente...';
            preview.appendChild(loading);
            const { id } = await this.saveCurrentIndicator({ deferPipeline: true });
            await API.regenerate(this.currentSession.job_id, 'component', this.collectSettings(), id);
            this.settingsDirty = false;
            this.settingsSignature = null;
            this.startPolling();
        } catch (error) {
            UI.clearElement(preview);
            const empty = document.createElement('div');
            empty.className = 'preview-empty';
            empty.textContent = error.message || 'Não foi possível regenerar o componente.';
            preview.appendChild(empty);
            button.disabled = false;
            button.textContent = 'Salvar e gerar novamente';
        }
    },

    renderIndicatorPreviewPlaceholder(index) {
        const preview = document.getElementById('indicator-preview-body');
        if (index === -1) {
            UI.clearElement(preview);
            const empty = document.createElement('div');
            empty.className = 'preview-empty';
            empty.textContent = 'Salve o componente antes de gerar uma prévia.';
            preview.appendChild(empty);
            return;
        }

        const art = this.currentSession.artifacts[index];
        if (!this.isRenderableIndicator(art, this.currentSession)) {
            UI.clearElement(preview);
            const empty = document.createElement('div');
            empty.className = 'preview-empty';
            empty.textContent = 'A prévia aparecerá depois que o componente for gerado.';
            preview.appendChild(empty);
        } else {
            UI.clearElement(preview);
            const loading = document.createElement('div');
            loading.className = 'preview-loading';
            loading.textContent = 'Carregando prévia...';
            preview.appendChild(loading);
        }
    },

    async refreshIndicatorPreview(session) {
        const preview = document.getElementById('indicator-preview-body');
        if (!preview) return;
        if (this.indicatorPreviewLoading) return;
        const idx = parseInt(document.getElementById('edit-index').value);
        const art = idx === -1 ? null : session.artifacts[idx];
        if (!this.isRenderableIndicator(art, session)) {
            UI.clearElement(preview);
            const empty = document.createElement('div');
            empty.className = 'preview-empty';
            empty.textContent = 'Aguardando o código renderizável deste componente.';
            preview.appendChild(empty);
            return;
        }

        this.indicatorPreviewLoading = true;
        const request = ++this.indicatorPreviewRequest;
        try {
            const url = await API.getComponentPreviewObjectUrl(session.job_id, art.id, session.pdf_version);
            if (request !== this.indicatorPreviewRequest || !document.getElementById('indicator-modal').open) {
                URL.revokeObjectURL(url);
                return;
            }
            if (this.indicatorPreviewUrl) URL.revokeObjectURL(this.indicatorPreviewUrl);
            this.indicatorPreviewUrl = url;
            this.indicatorPreviewVersion = session.pdf_version;
            UI.clearElement(preview);
            const iframe = document.createElement('iframe');
            iframe.className = 'indicator-preview-frame';
            iframe.title = 'Prévia do componente';
            iframe.src = `${url}#toolbar=0&navpanes=0&view=Fit`;
            preview.appendChild(iframe);
            const button = document.getElementById('modal-regenerate-btn');
            if (button) {
                button.disabled = false;
                button.textContent = 'Salvar e gerar novamente';
            }
        } catch (error) {
            if (request !== this.indicatorPreviewRequest) return;
            UI.clearElement(preview);
            const empty = document.createElement('div');
            empty.className = 'preview-empty';
            empty.textContent = 'Não foi possível carregar a prévia renderizada.';
            preview.appendChild(empty);
        } finally {
            if (request === this.indicatorPreviewRequest) this.indicatorPreviewLoading = false;
        }
    },

    clearIndicatorPreview() {
        this.indicatorPreviewRequest += 1;
        if (this.indicatorPreviewUrl) {
            URL.revokeObjectURL(this.indicatorPreviewUrl);
            this.indicatorPreviewUrl = null;
        }
        this.indicatorPreviewVersion = 0;
        this.indicatorPreviewLoading = false;
    },

    async handleDeleteIndicator(index) {
        if (!confirm("Excluir este componente?")) return;
        const artifacts = this.currentSession.artifacts.filter((_artifact, currentIndex) => currentIndex !== index);
        await API.syncArtifacts(this.currentSession.job_id, artifacts, {});
        this.currentSession.artifacts = artifacts;
        this.startPolling();
    },

    async handleDeleteIndicatorById(id, event) {
        if (event) event.stopPropagation();
        const index = this.currentSession.artifacts.findIndex(a => a.id === id);
        if (index !== -1) await this.handleDeleteIndicator(index);
    },

    setLayoutFocus(enabled) {
        this.layoutFocused = enabled;
        document.getElementById('app-shell').classList.toggle('layout-focus', enabled);
        const button = document.getElementById('layout-focus-btn');
        if (button) {
            button.textContent = enabled ? 'Restaurar painel' : 'Expandir editor';
            button.setAttribute('aria-pressed', String(enabled));
        }
    },

    renderReportLayout(session) {
        const container = document.getElementById('layout-canvas');
        if (!container) return;
        if (!this.reportLayoutEditor) {
            this.reportLayoutEditor = new LayoutEditor(container, {
                onChange: (_items, dirty) => {
                    document.getElementById('report-layout-status').textContent = dirty
                        ? 'Alterações não salvas. Aplique para atualizar o PDF.'
                        : 'O layout está atualizado.';
                },
                onEdit: id => {
                    const index = this.currentSession.artifacts.findIndex(artifact => artifact.id === id);
                    if (index !== -1) this.handleEditIndicator(index);
                },
            });
        }
        const artifacts = session.artifacts || [];
        const items = (session.template || []).map(item => {
            const artifact = artifacts.find(artifact => artifact.id === item.id);
            return { ...item, label: artifact?.indicator || item.id, type: artifact?.type || 'graphic' };
        });
        const available = artifacts.filter(artifact => artifact.type !== 'metric').map(artifact => ({ id: artifact.id, label: artifact.indicator, type: artifact.type }));
        this.reportLayoutEditor.setItems(items, { force: this.reportLayoutJobId !== session.job_id, available });
        this.reportLayoutJobId = session.job_id;
    },

    async handleSaveVisualLayout() {
        if (!this.reportLayoutEditor || !this.currentSession) return;
        const button = document.getElementById('apply-layout-btn');
        if (button.disabled) return;
        const template = this.reportLayoutEditor.getItems().map(({ id, page, x, y, w, h }) => ({ id, page, x, y, w, h }));
        button.disabled = true;
        button.textContent = 'Aplicando...';
        this.reportLayoutEditor.container.inert = true;
        try {
            await API.saveLayout(this.currentSession.job_id, template);
            this.currentSession.template = template;
            this.reportLayoutEditor.markSaved();
            document.getElementById('report-layout-status').textContent = 'Layout aplicado. Atualizando o PDF...';
            this.startPolling();
        } catch (error) {
            document.getElementById('report-layout-status').textContent = error.message;
        } finally {
            button.disabled = false;
            button.textContent = 'Aplicar ao PDF';
            this.reportLayoutEditor.container.inert = false;
        }
    },

    addLayoutPage() {
        this.reportLayoutEditor?.addPage();
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
            include_data_samples: document.getElementById('s-include-data-samples').checked,
            provider: document.getElementById('s-provider').value,
            model_name: document.getElementById('s-model-name').value
        };
    },

    async handleSaveSettings(e) {
        e.preventDefault();
        const settings = this.collectSettings();
        const button = e.target.querySelector('button[type="submit"]');
        if (button) button.disabled = true;
        try {
            await API.updateSettings(this.currentSession.job_id, settings);
            this.settingsDirty = false;
            this.settingsSignature = null;
            this.startPolling();
        } catch (error) {
            this.showNotice(error.message);
        } finally {
            if (button) button.disabled = false;
        }
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
        this.settingsDirty = false;
        this.settingsSignature = null;
        this.startPolling();
    },

    copyFullScript() {
        const script = document.getElementById('full-script-content').textContent;
        navigator.clipboard.writeText(script);
        alert("Script copiado para a área de transferência!");
    }
};

window.onload = () => App.init().catch(error => App.showNotice(error.message));
