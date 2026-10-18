const Keys = {
    catalog: null,
    user: null,

    async request(method, url, body) {
        const response = await fetch(url, {
            method, headers: API.authHeaders(body ? { 'Content-Type': 'application/json' } : {}),
            body: body ? JSON.stringify(body) : undefined,
        });
        return API.parseResponse(response, 'Não foi possível concluir. Tente novamente.');
    },

    notice(message, error = false) {
        const el = document.getElementById('keys-notice');
        el.textContent = message || '';
        el.hidden = !message;
        el.classList.toggle('is-error', error);
    },

    async init() {
        try {
            this.user = await API.me();
            document.getElementById('app-shell').style.display = 'block';
            document.getElementById('keys-unverified').hidden = this.user.email_verified;
            await this.load();
            for (const prefix of ['default', 'fallback']) {
                document.getElementById(`${prefix}-provider`).addEventListener('change', () => this.models(prefix));
            }
            document.getElementById('model-preferences-form').addEventListener('submit', event => this.savePreferences(event));
        } catch (error) { this.notice(error.message, true); }
    },

    async load() {
        const [data, catalog, preferences] = await Promise.all([
            this.request('GET', '/api/credentials'), API.getModels(), this.request('GET', '/api/model-preferences'),
        ]);
        this.catalog = catalog;
        const rows = new Map(data.credentials.map(row => [row.provider, row]));
        document.getElementById('credential-cards').replaceChildren(...Object.entries(catalog.credential_status).map(
            ([provider, status]) => this.card(provider, status, rows.get(provider))
        ));
        for (const prefix of ['default', 'fallback']) {
            const select = document.getElementById(`${prefix}-provider`);
            select.replaceChildren(new Option(prefix === 'default' ? 'Escolher automaticamente' : 'Desativado', ''));
            for (const [provider, status] of Object.entries(catalog.credential_status)) {
                if (status.enabled && catalog.providers[provider].length) select.add(new Option(status.label, provider));
            }
            select.value = preferences[prefix === 'default' ? 'default_provider' : 'llm_fallback_provider'] || '';
            this.models(prefix, preferences[prefix === 'default' ? 'default_model' : 'llm_fallback_model_name']);
        }
    },

    card(provider, status, row) {
        const card = document.createElement('section');
        card.className = 'account-section credential-card';
        card.dataset.provider = provider;
        const heading = document.createElement('h2');
        heading.textContent = status.label;
        const badge = document.createElement('span');
        badge.className = `badge ${row?.status === 'valid' ? '' : 'badge-warning'}`;
        badge.textContent = ({ valid: 'Válida', invalid: 'Inválida', unchecked: 'Não verificada' })[row?.status] || 'Sem chave';
        heading.append(' ', badge);
        const details = document.createElement('p');
        details.className = 'auth-hint';
        details.textContent = row
            ? `Final ••••${row.key_last4} · ${row.model_count} ${row.model_count === 1 ? 'modelo' : 'modelos'} · Última validação: ${row.last_validated_at ? new Date(row.last_validated_at).toLocaleString('pt-BR') : 'nunca'}`
            : 'Adicione sua chave para usar este provedor.';
        const form = document.createElement('form');
        form.className = 'account-form';
        const fieldset = document.createElement('fieldset');
        fieldset.className = 'credential-controls';
        const label = document.createElement('label');
        label.htmlFor = `key-${provider}`;
        label.textContent = row ? 'Nova chave' : 'Chave de API';
        const input = document.createElement('input');
        Object.assign(input, { id: `key-${provider}`, type: 'password', required: true, maxLength: 4096, autocomplete: 'off' });
        input.disabled = !this.user.email_verified;
        const actions = document.createElement('div');
        actions.className = 'credential-actions';
        const save = this.button(row ? 'Substituir chave' : 'Salvar chave');
        save.type = 'submit';
        save.disabled = !this.user.email_verified;
        actions.append(save);
        if (row) {
            const test = this.button('Testar');
            test.disabled = !this.user.email_verified;
            test.addEventListener('click', () => this.mutate(fieldset, 'POST', provider, undefined, 'Chave validada.'));
            const remove = this.button('Excluir');
            remove.addEventListener('click', () => this.mutate(fieldset, 'DELETE', provider, undefined, 'Chave excluída.'));
            actions.append(test, remove);
        }
        fieldset.append(label, input, actions);
        form.append(fieldset);
        form.addEventListener('submit', event => {
            event.preventDefault();
            const key = input.value;
            input.value = '';
            this.mutate(fieldset, 'PUT', provider, { api_key: key }, 'Chave salva.');
        });
        card.append(heading, details);
        if (row?.last_error) {
            const error = document.createElement('p');
            error.textContent = row.last_error;
            error.className = 'auth-message is-error';
            card.append(error);
        }
        card.append(form);
        return card;
    },

    button(label) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-outline';
        button.textContent = label;
        return button;
    },

    async mutate(fieldset, method, provider, body, success) {
        fieldset.disabled = true;
        this.notice('Validando...');
        try {
            await this.request(method, `/api/credentials/${encodeURIComponent(provider)}${method === 'POST' ? '/test' : ''}`, body);
            await this.load();
            this.notice(success);
        } catch (error) {
            await this.load().catch(() => {});
            this.notice(error.message, true);
        } finally { fieldset.disabled = false; }
    },

    models(prefix, selected) {
        const provider = document.getElementById(`${prefix}-provider`).value;
        const select = document.getElementById(`${prefix}-model`);
        select.replaceChildren();
        for (const model of this.catalog.providers[provider] || []) select.add(new Option(model.label, model.id));
        select.disabled = !provider;
        if (selected && [...select.options].some(option => option.value === selected)) select.value = selected;
        else if (provider) select.value = this.catalog.defaults[provider];
    },

    async savePreferences(event) {
        event.preventDefault();
        const button = event.target.querySelector('button');
        button.disabled = true;
        try {
            await this.request('PUT', '/api/model-preferences', {
                default_provider: document.getElementById('default-provider').value || null,
                default_model: document.getElementById('default-model').value || null,
                llm_fallback_provider: document.getElementById('fallback-provider').value || null,
                llm_fallback_model_name: document.getElementById('fallback-model').value || null,
            });
            this.notice('Preferências salvas.');
        } catch (error) { this.notice(error.message, true); }
        finally { button.disabled = false; }
    },
};

window.addEventListener('DOMContentLoaded', () => Keys.init());
