const Operations = {
    offset: 0,
    nextBefore: null,
    user: null,
    number(value) { return Number(value || 0).toLocaleString('pt-BR'); },
    element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    },
    row(values) {
        const row = this.element('tr');
        for (const value of values) {
            const cell = this.element('td');
            if (value instanceof Node) cell.append(value);
            else cell.textContent = value;
            row.append(cell);
        }
        return row;
    },
    notice(message, error = false) {
        const el = document.getElementById('operations-notice');
        el.hidden = !message;
        el.textContent = message || '';
        el.classList.toggle('is-error', error);
    },
    async request(method, url, body) {
        const response = await fetch(url, {method, headers: API.authHeaders(body ? {'Content-Type': 'application/json'} : {}),
            body: body ? JSON.stringify(body) : undefined});
        return API.parseResponse(response, 'Não foi possível carregar os dados.');
    },
    async run(action) {
        try { await action(); } catch (error) { this.notice(error.message, true); }
    },
    totals(usage) {
        document.getElementById('usage-totals').textContent =
            `${this.number(usage.calls)} chamadas · ${this.number(usage.input_tokens)} tokens de entrada · ${this.number(usage.output_tokens)} tokens de saída · ${this.number(usage.failed_calls)} falhas · ${this.number(usage.pending_calls)} pendentes · ${this.number(usage.unknown_token_calls)} sem contagem completa`;
    },
    async loadUsage() {
        const [usage, reports] = await Promise.all([this.request('GET', '/api/usage'), this.request('GET', '/api/usage/reports')]);
        this.totals(usage.llm);
        const cap = value => value ? this.number(value) : 'sem limite';
        document.getElementById('usage-limits').textContent = `Limite mensal de tokens: ${cap(usage.llm.monthly_token_limit)}. Chamadas por relatório: ${cap(usage.llm.report_call_limit)}.`;
        document.getElementById('usage-storage').textContent = `Armazenamento: ${(usage.storage_bytes / 1024 ** 3).toFixed(2)} de ${(usage.storage_limit_bytes / 1024 ** 3).toFixed(0)} GB. Relatórios nas últimas 24 h: ${usage.reports_last_24h} de ${usage.reports_per_day_limit}.`;
        document.getElementById('provider-usage').replaceChildren(...usage.llm.by_provider.map(p =>
            this.row([p.provider, this.number(p.calls), this.number(p.input_tokens), this.number(p.output_tokens)])));
        const rows = reports.reports.map(report => {
            const link = this.element('a', report.title || report.job_id);
            link.href = `/session/${encodeURIComponent(report.job_id)}`;
            const u = report.usage;
            return this.row([link, u ? this.number(u.calls) : 'Sem histórico',
                u ? `${this.number(u.input_tokens + u.output_tokens)}${u.unknown_token_calls ? ' (parcial)' : ''}` : '—', u ? this.number(u.failed_calls) : '—']);
        });
        document.getElementById('report-usage').replaceChildren(...(rows.length ? rows : [this.row(['Nenhum relatório.', '', '', ''])]));
    },
    async loadUsers() {
        const data = await this.request('GET', `/api/admin/users?offset=${this.offset}&limit=25`);
        document.getElementById('admin-users').replaceChildren(...data.users.map(user => this.userCard(user)));
        document.getElementById('users-prev').disabled = this.offset === 0;
        document.getElementById('users-next').disabled = this.offset + 25 >= data.total;
        document.getElementById('users-page').textContent = `${Math.min(this.offset + 1, data.total)}–${Math.min(this.offset + 25, data.total)} de ${data.total}`;
    },
    userCard(user) {
        const card = this.element('form', undefined, 'admin-user account-form');
        card.append(this.element('h3', user.email), this.element('p',
            `${user.role} · ${user.status === 'active' ? 'Ativa' : 'Desativada'} · ${this.number(user.reports)} relatórios · ${(user.storage_bytes / 1024 ** 2).toFixed(1)} MB · ${this.number(user.monthly_tokens)} tokens neste mês`));
        const inputs = {};
        for (const [field, label, max] of [['monthly_token_limit', 'Tokens por mês', 1e12], ['report_call_limit', 'Chamadas por relatório', 1e6]]) {
            const group = this.element('label', label, 'form-group');
            const input = this.element('input');
            input.type = 'number'; input.min = '0'; input.max = String(max); input.step = '1';
            input.value = user[field] === null ? '' : String(user[field]);
            input.placeholder = `Padrão: ${user.effective_limits[field] || 'sem limite'}`;
            inputs[field] = input;
            group.append(input); card.append(group);
        }
        const actions = this.element('div', undefined, 'keys-nav');
        const save = this.element('button', 'Salvar limites', 'btn'); save.type = 'submit';
        const toggle = this.element('button', user.status === 'active' ? 'Desativar conta' : 'Reativar conta', 'btn btn-outline');
        toggle.type = 'button'; toggle.disabled = user.id === this.user.id;
        toggle.addEventListener('click', () => this.run(async () => {
            toggle.disabled = true;
            try { await this.request('PATCH', `/api/admin/users/${user.id}`, {status: user.status === 'active' ? 'disabled' : 'active'}); await this.loadUsers(); await this.loadAudit(true); }
            finally { toggle.disabled = user.id === this.user.id; }
        }));
        card.addEventListener('submit', event => {
            event.preventDefault();
            this.run(async () => {
                const limits = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value === '' ? null : Number(input.value)]));
                save.disabled = true;
                try { await this.request('PATCH', `/api/admin/users/${user.id}`, limits); this.notice('Limites salvos.'); await this.loadUsers(); await this.loadAudit(true); }
                finally { save.disabled = false; }
            });
        });
        actions.append(save, toggle); card.append(actions);
        return card;
    },
    async loadAudit(reset = false) {
        const query = !reset && this.nextBefore ? `?before=${this.nextBefore}` : '';
        const data = await this.request('GET', `/api/admin/audit${query}`);
        const rows = data.events.map(event => this.row([new Date(event.created_at).toLocaleString('pt-BR'), event.event,
            `${event.user_id || '—'} / ${event.ip || '—'}`, JSON.stringify(event.details)]));
        const target = document.getElementById('audit-events');
        if (reset) target.replaceChildren(...rows); else target.append(...rows);
        this.nextBefore = data.next_before;
        document.getElementById('audit-more').hidden = !this.nextBefore;
    },
    async loadAdmin() {
        const [usage, workspace] = await Promise.all([this.request('GET', '/api/admin/usage'), this.request('GET', '/api/admin/workspace-usage')]);
        this.totals(usage);
        document.getElementById('workspace-usage').textContent = `Espaço usado: ${workspace.total.human_size}.`;
        await Promise.all([this.loadUsers(), this.loadAudit(true)]);
        document.getElementById('users-prev').addEventListener('click', () => this.run(async () => { this.offset = Math.max(0, this.offset - 25); await this.loadUsers(); }));
        document.getElementById('users-next').addEventListener('click', () => this.run(async () => { this.offset += 25; await this.loadUsers(); }));
        document.getElementById('audit-more').addEventListener('click', () => this.run(() => this.loadAudit()));
        document.getElementById('cleanup-button').addEventListener('click', event => this.run(async () => {
            event.target.disabled = true;
            try { await this.request('POST', '/api/admin/cleanup'); this.notice('Limpeza iniciada. Recarregue a página para atualizar o espaço usado.'); await this.loadAudit(true); }
            finally { event.target.disabled = false; }
        }));
    },
    async init() {
        await this.run(async () => {
            this.user = await API.me();
            if (document.body.dataset.operations === 'admin') await this.loadAdmin();
            else await this.loadUsage();
        });
    },
};
document.addEventListener('DOMContentLoaded', () => Operations.init());
