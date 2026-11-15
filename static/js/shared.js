/* Read-only mode for the session workspace; public requests use only the share token. */
const SharedReport = {
    timer: null,
    generation: 0,
    viewActions: new Set(['copy-full-script', 'toggle-layout-focus', 'preview-file', 'preview-file-variant', 'download-file', 'close-file-preview', 'view-metric-data']),

    async init() {
        App.readOnly = true;
        document.body.classList.add('shared-workspace');
        document.getElementById('app-shell').style.display = 'flex';
        App.showWorkspace();
        document.getElementById('shared-access').hidden = false;
        document.getElementById('account-name').textContent = 'Somente leitura';
        document.querySelectorAll('.sidebar a').forEach(link => {
            link.removeAttribute('href');
            link.setAttribute('aria-disabled', 'true');
        });
        document.addEventListener('submit', event => event.preventDefault());
        for (const name of ['dragover', 'drop']) document.addEventListener(name, event => event.preventDefault());
        document.addEventListener('click', event => {
            App.handleDocumentClick(event).catch(error => App.showNotice(error.message));
        });
        for (const id of ['component-search', 'component-filter']) {
            document.getElementById(id).addEventListener('input', () => Components.renderList(App.currentSession?.artifacts || [], true));
        }
        document.getElementById('file-preview-modal').addEventListener('close', () => {
            App.filePreviewRequest += 1;
            if (App.filePreviewUrl) URL.revokeObjectURL(App.filePreviewUrl);
            App.filePreviewUrl = null;
            App.restoreFilePreviewFocus();
        });
        this.disableEditing();
        API.shareToken = window.location.pathname.match(/^\/share\/([A-Za-z0-9_-]{43})$/)?.[1];
        if (!API.shareToken) return this.unavailable('Este link não está disponível.');
        App.showNotice('Carregando o relatório...');
        await this.load();
    },

    disableEditing() {
        // The layout editor owns its own controls so selection and zoom remain available.
        document.querySelectorAll('button, input, select, textarea').forEach(control => {
            if (control.closest('#layout-canvas')) return;
            const canView = control.dataset.tabTarget || this.viewActions.has(control.dataset.action)
                || ['component-search', 'component-filter', 'download-btn', 'file-preview-download'].includes(control.id)
                || control.closest('.quality-actions') && control.dataset.readOnlyAllowed;
            control.disabled = !canView;
            if (!canView) control.title = 'Disponível apenas para o proprietário do relatório.';
        });
        document.getElementById('report-layout-status').textContent = 'Somente leitura. Selecione os blocos para consultar as posições e medidas.';
    },

    async load() {
        clearTimeout(this.timer);
        const generation = ++this.generation;
        try {
            const session = await API.getSession();
            if (generation !== this.generation) return;
            App.currentSession = session;
            // The public API exposes the selected values without account-specific catalogs.
            App.models = { [session.provider]: [{ id: session.model_name, label: session.model_name }] };
            App.layoutTemplates = session.settings?.selected_template_id
                ? [{ id: session.settings.selected_template_id, name: session.settings.selected_template_id, slots: session.template || [] }] : [];
            const theme = session.settings?.visual_theme_id || App.componentLibrary.default_theme_id;
            const style = session.settings?.chart_style_id || App.componentLibrary.default_chart_style_id;
            App.componentLibrary.themes = [{ id: theme, name: theme }];
            App.componentLibrary.chart_styles = [{ id: style, name: style }];
            document.getElementById('shared-title').textContent = session.settings?.title || 'Relatório compartilhado';
            document.title = document.getElementById('shared-title').textContent;
            document.getElementById('shared-expiry').textContent = session.sharing?.expires_at
                ? 'Acesso válido até ' + new Date(session.sharing.expires_at).toLocaleString('pt-BR') : 'Link sem expiração';
            App.updateUI(session);
            this.disableEditing();
        } catch (error) {
            if (error.status === 404) return;
            App.showNotice('Não foi possível carregar o relatório. Tentaremos novamente.');
        }
        if (generation === this.generation) this.timer = setTimeout(() => this.load(), 5000);
    },

    unavailable(message) {
        clearTimeout(this.timer);
        this.generation += 1;
        App.currentSession = null;
        App.filePreviewRequest += 1;
        App.closeFilePreview();
        if (App.pdfObjectUrl) URL.revokeObjectURL(App.pdfObjectUrl);
        App.pdfObjectUrl = null;
        document.getElementById('pdf-viewer')?.removeAttribute('src');
        document.getElementById('shared-title').textContent = 'Relatório indisponível';
        document.title = 'Relatório indisponível';
        document.getElementById('shared-expiry').textContent = '';
        // Remove previously rendered content, including settings, chat and download handlers.
        for (const selector of ['.tab-content', '#chat-history', '#file-workspace', '#header-progress']) {
            document.querySelectorAll(selector).forEach(node => node.replaceChildren());
        }
        document.getElementById('file-preview-body').replaceChildren();
        document.getElementById('file-preview-title').textContent = '';
        document.getElementById('file-preview-meta').textContent = '';
        document.getElementById('file-preview-download').onclick = null;
        App.reportLayoutEditor?.observer?.disconnect();
        App.reportLayoutEditor = null;
        App.showNotice(message);
    },
};
