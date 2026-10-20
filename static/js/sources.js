/* Account-owned connections and datasets; secret inputs are never populated. */
const Sources = {
    connections: [], datasets: [],
    el(id) { return document.getElementById(id); },
    value(id) { return this.el(id).value.trim(); },
    notice(message, error = false) {
        this.el('sources-notice').textContent = message; this.el('sources-notice').hidden = !message;
        this.el('sources-notice').classList.toggle('is-error', error);
    },
    async action(button, callback) {
        if (button.disabled) return;
        button.disabled = true;
        this.notice('');
        try { await callback(); } catch (error) { this.notice(error.message, true); }
        finally { button.disabled = false; }
    },
    async init() {
        try { await API.me(); } catch (_) { API.redirectToLogin(); return; }
        this.el('app-shell').style.display = 'block';
        for (const id of ['connection-kind', 'connection-auth', 'connection-sslmode', 'dataset-connection', 'dataset-pagination', 'dataset-mode']) this.el(id).addEventListener('change', () => this.fields());
        this.el('connection-cancel').addEventListener('click', () => this.reset('connection'));
        this.el('dataset-cancel').addEventListener('click', () => this.reset('dataset'));
        this.el('connection-form').addEventListener('submit', event => {
            event.preventDefault();
            this.action(event.submitter, () => this.saveConnection());
        });
        this.el('dataset-form').addEventListener('submit', event => {
            event.preventDefault();
            this.action(event.submitter, () => this.saveDataset());
        });
        await this.load(); this.fields();
    },
    async load() {
        const data = await API.dataSources();
        this.connections = data.connections || []; this.datasets = data.datasets || [];
        this.el('connection-count').textContent = this.connections.length;
        this.el('dataset-count').textContent = this.datasets.length;
        this.renderList('connection', this.connections); this.renderList('dataset', this.datasets);
        const selected = this.value('dataset-connection');
        const select = this.el('dataset-connection'); select.replaceChildren();
        for (const connection of this.connections) { const option = document.createElement('option'); option.value = connection.id; option.textContent = connection.name; select.append(option); }
        if (this.connections.some(item => item.id === selected)) select.value = selected;
        this.fields();
    },
    renderList(kind, items) {
        const list = this.el(`${kind}-list`); list.replaceChildren();
        if (!items.length) {
            const empty = document.createElement('div'); empty.className = 'source-empty';
            const title = document.createElement('strong'); title.textContent = kind === 'connection' ? 'Sua primeira conexão' : 'Transforme uma conexão em dados';
            const hint = document.createElement('p'); hint.textContent = kind === 'connection' ? 'Adicione uma API JSON ou um banco PostgreSQL para começar.' : 'Depois de conectar uma origem, escolha o endpoint, tabela ou consulta que quer usar.';
            empty.append(title, hint); list.append(empty);
        }
        for (const item of items) {
            const card = document.createElement('article'); card.className = 'source-card';
            const top = document.createElement('div'); top.className = 'source-card-heading';
            const heading = document.createElement('strong'); heading.textContent = item.name;
            const connection = kind === 'connection' ? item : this.connections.find(c => c.id === item.connection_id);
            const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = connection?.kind === 'postgres' ? 'PostgreSQL' : 'API JSON';
            top.append(heading, badge); card.append(top);
            const detail = document.createElement('small');
            detail.textContent = kind === 'connection' ? (item.kind === 'api' ? `API · ${item.config.base_url}` : `PostgreSQL · ${item.config.host} / ${item.config.database} · ${item.config.sslmode === 'disable' ? 'Sem TLS' : 'TLS verificado'}`) : (this.connections.find(c => c.id === item.connection_id)?.name || '');
            card.append(detail);
            const actions = document.createElement('div'); actions.className = 'source-actions';
            for (const [label, callback] of [
                ['Editar', () => this.edit(kind, item)],
                [kind === 'connection' ? 'Testar conexão' : 'Prévia', async () => {
                    const result = await API.dataSources(`/${kind === 'connection' ? 'connections' : 'datasets'}/${item.id}/${kind === 'connection' ? 'test' : 'preview'}`, 'POST', kind === 'dataset' ? { dataset_id: item.id, parameters: {} } : undefined);
                    if (kind === 'connection') this.notice(result.message);
                    else this.preview(item, result.rows || []);
                }],
                ['Excluir', async () => {
                    const message = kind === 'connection' ? 'Excluir esta conexão e seus conjuntos? Os snapshots já importados serão preservados.' : 'Excluir este conjunto? Os snapshots já importados serão preservados.';
                    if (!window.confirm(message)) return;
                    await API.dataSources(`/${kind === 'connection' ? 'connections' : 'datasets'}/${item.id}`, 'DELETE'); await this.load();
                }]
            ]) {
                const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline'; button.textContent = label;
                button.addEventListener('click', () => this.action(button, callback)); actions.append(button);
            }
            if (kind === 'connection') {
                const add = document.createElement('button'); add.type = 'button'; add.className = 'text-button'; add.textContent = 'Criar conjunto →';
                add.addEventListener('click', () => {
                    this.reset('dataset'); this.el('dataset-connection').value = item.id; this.fields();
                    this.el('dataset-editor').open = true; this.el('dataset-editor').scrollIntoView({ behavior: 'smooth' }); this.el('dataset-name').focus({ preventScroll: true });
                }); actions.append(add);
            }
            card.append(actions); list.append(card);
        }
    },
    preview(dataset, rows) {
        this.el('source-preview').hidden = false;
        this.el('source-preview').querySelector('h2').textContent = 'Prévia · ' + dataset.name;
        this.el('source-preview-body').textContent = JSON.stringify(rows, null, 2);
        const target = this.el('source-preview-table'); target.replaceChildren();
        const columns = [...new Set(rows.flatMap(row => Object.keys(row)))];
        if (!rows.length) { const message = document.createElement('p'); message.textContent = 'Nenhum registro encontrado. Revise o endpoint e os parâmetros.'; target.append(message); }
        else {
            const table = document.createElement('table'); table.className = 'operations-table';
            const head = table.createTHead().insertRow();
            for (const column of columns) { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = column; head.append(cell); }
            const body = table.createTBody();
            for (const row of rows) {
                const tr = body.insertRow();
                for (const column of columns) { const value = row[column]; tr.insertCell().textContent = value === null || value === undefined ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
            }
            target.append(table);
        }
        this.el('source-preview').scrollIntoView({ behavior: 'smooth' });
    },
    fields() {
        const api = this.value('connection-kind') === 'api';
        const auth = this.value('connection-auth');
        this.el('api-connection-fields').hidden = !api; this.el('postgres-connection-fields').hidden = api;
        this.el('connection-no-tls-notice').hidden = api || this.value('connection-sslmode') !== 'disable';
        this.el('header-field').hidden = auth !== 'api_key'; this.el('api-user-field').hidden = auth !== 'basic'; this.el('secret-field').hidden = api && auth === 'none';
        for (const id of ['connection-url', 'connection-host', 'connection-database', 'connection-db-user']) this.el(id).required = id === 'connection-url' ? api : !api;
        const connection = this.connections.find(item => item.id === this.value('dataset-connection'));
        const apiDataset = connection?.kind !== 'postgres';
        this.el('api-dataset-fields').hidden = !apiDataset; this.el('postgres-dataset-fields').hidden = apiDataset;
        this.el('next-field').hidden = this.value('dataset-pagination') !== 'next'; this.el('page-fields').hidden = this.value('dataset-pagination') !== 'page';
        this.el('table-fields').hidden = this.value('dataset-mode') !== 'table'; this.el('query-field').hidden = this.value('dataset-mode') !== 'query';
    },
    reset(kind) {
        this.el(`${kind}-form`).reset(); this.el(`${kind}-id`).value = '';
        if (kind === 'connection') this.el('connection-kind').disabled = false;
        this.el(`${kind}-editor`).open = false; this.fields();
    },
    edit(kind, item) {
        this.reset(kind); this.el(`${kind}-editor`).open = true;
        this.el(`${kind}-id`).value = item.id; this.el(`${kind}-name`).value = item.name;
        const set = (id, value) => { this.el(id).value = value ?? ''; };
        if (kind === 'connection') {
            set('connection-kind', item.kind); this.el('connection-kind').disabled = true;
            const c = item.config;
            for (const [id, value] of Object.entries({ 'connection-url': c.base_url, 'connection-auth': c.auth_mode || 'bearer', 'connection-header': c.header_name || 'X-API-Key', 'connection-api-user': c.username, 'connection-host': c.host, 'connection-port': c.port || 5432, 'connection-database': c.database, 'connection-db-user': c.username, 'connection-sslmode': c.sslmode || 'verify-full' })) set(id, value);
            this.el('connection-secret').value = '';
        } else {
            const d = item.definition;
            for (const [id, value] of Object.entries({ 'dataset-connection': item.connection_id, 'dataset-endpoint': d.endpoint, 'dataset-records': d.records_path, 'dataset-pagination': d.pagination || 'none', 'dataset-next': d.next_path, 'dataset-page-param': d.page_parameter || 'page', 'dataset-first-page': d.first_page ?? 1, 'dataset-max-pages': d.max_pages || 20, 'dataset-mode': d.mode || 'table', 'dataset-schema': d.schema_name || 'public', 'dataset-table': d.table_name, 'dataset-query': d.query, 'dataset-parameters': JSON.stringify(d.parameters || {}, null, 2) })) set(id, value);
        }
        this.fields(); this.el(`${kind}-editor`).scrollIntoView({ behavior: 'smooth' });
    },
    async saveConnection() {
        const kind = this.value('connection-kind');
        const config = kind === 'api' ? { base_url: this.value('connection-url'), auth_mode: this.value('connection-auth'), header_name: this.value('connection-header'), username: this.value('connection-api-user') } : { host: this.value('connection-host'), port: Number(this.value('connection-port')), database: this.value('connection-database'), username: this.value('connection-db-user'), sslmode: this.value('connection-sslmode') };
        const body = { name: this.value('connection-name'), kind, config };
        const secret = this.el('connection-secret').value;
        if (secret && (kind !== 'api' || config.auth_mode !== 'none')) body.secret = secret;
        const id = this.value('connection-id');
        await API.dataSources(`/connections${id ? `/${id}` : ''}`, id ? 'PUT' : 'POST', body);
        this.reset('connection'); await this.load(); this.notice('Conexão salva. Defina um conjunto de dados para usá-la nos relatórios.');
    },
    async saveDataset() {
        let parameters;
        try { parameters = JSON.parse(this.value('dataset-parameters') || '{}'); }
        catch (_) { throw new Error('Use um objeto JSON válido nos parâmetros. Ex.: {"start_date": "2026-01-01"}'); }
        if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') throw new Error('Os parâmetros precisam ser um objeto JSON com nomes e valores.');
        const connection = this.connections.find(item => item.id === this.value('dataset-connection'));
        if (!connection) throw new Error('Salve uma conexão primeiro.');
        const definition = connection.kind === 'api' ? { endpoint: this.value('dataset-endpoint'), records_path: this.value('dataset-records'), parameters, pagination: this.value('dataset-pagination'), next_path: this.value('dataset-next'), page_parameter: this.value('dataset-page-param'), first_page: Number(this.value('dataset-first-page')), max_pages: Number(this.value('dataset-max-pages')) } : { mode: this.value('dataset-mode'), schema_name: this.value('dataset-schema'), table_name: this.value('dataset-table'), query: this.value('dataset-query'), parameters };
        const id = this.value('dataset-id');
        await API.dataSources(`/datasets${id ? `/${id}` : ''}`, id ? 'PUT' : 'POST', { name: this.value('dataset-name'), connection_id: connection.id, definition });
        this.reset('dataset'); await this.load(); this.notice('Conjunto de dados salvo e disponível nos relatórios.');
    }
};
window.addEventListener('DOMContentLoaded', () => Sources.init().catch(error => Sources.notice(error.message)));
