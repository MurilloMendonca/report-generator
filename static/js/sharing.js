const Sharing = {
    jobId: null, busy: false, created: new Map(),
    init() {
        document.getElementById('share-link-form')?.addEventListener('submit', event => { event.preventDefault(); this.create(); });
        document.getElementById('share-report-modal')?.addEventListener('click', event => {
            const button = event.target.closest('[data-share-action]');
            if (!button) return;
            if (button.dataset.shareAction === 'close') document.getElementById('share-report-modal').close();
            if (button.dataset.shareAction === 'copy') this.copy(document.getElementById('share-link-url').value);
            if (button.dataset.shareAction === 'revoke') this.revoke(button.dataset.linkId, button);
        });
    },
    async request(path = '', method = 'GET', body) {
        const response = await fetch(`/api/sessions/${encodeURIComponent(this.jobId)}/share-links${path}`, {
            method, headers: API.authHeaders(body ? { 'Content-Type': 'application/json' } : {}), ...(body ? { body: JSON.stringify(body) } : {})
        });
        return API.parseResponse(response, 'Não foi possível atualizar o compartilhamento.');
    },
    status(message) { document.getElementById('share-link-status').textContent = message; },
    async open() {
        if (!App.currentSession?.job_id) return;
        this.jobId = App.currentSession.job_id;
        document.getElementById('share-link-created').hidden = true;
        this.status('Carregando links...');
        document.getElementById('share-report-modal').showModal();
        try { await this.load(); this.status('Você pode revogar o acesso a qualquer momento.'); }
        catch (error) { this.status(error.message); }
    },
    async load() {
        const data = await this.request();
        const list = document.getElementById('share-link-list'); list.replaceChildren();
        for (const link of data.links || []) {
            const row = document.createElement('div'); row.className = 'share-link-row';
            const info = document.createElement('div'); const title = document.createElement('strong'); title.textContent = link.label || 'Link de leitura'; info.appendChild(title);
            const detail = document.createElement('p');
            detail.textContent = link.revoked_at ? 'Acesso revogado' : !link.active ? 'Expirado' : link.expires_at ? `Expira em ${new Date(link.expires_at).toLocaleString('pt-BR')}` : 'Sem expiração';
            info.appendChild(detail); row.appendChild(info);
            if (link.active) {
                const known = this.created.get(link.id);
                if (known) {
                    const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'btn btn-outline'; copy.textContent = 'Copiar'; copy.addEventListener('click', () => this.copy(known)); row.appendChild(copy);
                }
                const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline'; button.textContent = 'Revogar'; button.dataset.shareAction = 'revoke'; button.dataset.linkId = link.id; row.appendChild(button);
            }
            list.appendChild(row);
        }
        if (!list.children.length) list.textContent = 'Nenhum link criado para este relatório.';
    },
    async create() {
        if (this.busy) return;
        const button = document.getElementById('share-link-create'); this.busy = true; button.disabled = true;
        try {
            const value = document.getElementById('share-link-expiry').value;
            const result = await this.request('', 'POST', { label: document.getElementById('share-link-label').value.trim(), expires_in_hours: value === 'none' ? null : Number(value) });
            const url = new URL(result.path, window.location.origin).href;
            this.created.set(result.id, url); document.getElementById('share-link-url').value = url;
            document.getElementById('share-link-created').hidden = false;
            await this.load(); this.status('Link criado. Quem o receber poderá visualizar o relatório sem fazer login.');
        } catch (error) { this.status(error.message); }
        finally { this.busy = false; button.disabled = false; }
    },
    async copy(url) {
        document.getElementById('share-link-url').value = url;
        document.getElementById('share-link-created').hidden = false;
        try { await navigator.clipboard.writeText(url); this.status('Link copiado.'); }
        catch (_error) { document.getElementById('share-link-url').select(); this.status('Selecione e copie o endereço exibido acima.'); }
    },
    async revoke(id, button) {
        button.disabled = true;
        try {
            await this.request(`/${encodeURIComponent(id)}`, 'DELETE');
            if (document.getElementById('share-link-url').value === this.created.get(id)) document.getElementById('share-link-created').hidden = true;
            this.created.delete(id);
            await this.load(); this.status('Link revogado. Novos acessos por esse endereço estão bloqueados.');
        }
        catch (error) { this.status(error.message); button.disabled = false; }
    }
};
