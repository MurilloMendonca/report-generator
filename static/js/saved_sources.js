/* Reports use immutable snapshots; reads only occur on an explicit import. */
const SavedSources = {
    datasets: [], signature: '',
    async init() {
        const catalog = await API.dataSources(); this.datasets = catalog.datasets || [];
        this.renderChooser('initial-source-list');
        const form = document.getElementById('saved-source-form');
        form?.addEventListener('submit', event => { event.preventDefault(); this.importSelected(event.submitter); });
        document.getElementById('refresh-sources-btn')?.addEventListener('click', event => this.importSelected(event.currentTarget, true));
        for (const id of ['reload-sources-btn', 'reload-initial-sources-btn']) document.getElementById(id)?.addEventListener('click', async () => {
            try { const data = await API.dataSources(); this.datasets = data.datasets || []; this.signature = ''; this.renderChooser('initial-source-list'); this.update(App.currentSession); }
            catch (error) { App.showNotice(error.message); }
        });
    },
    renderChooser(id, session = null) {
        const list = document.getElementById(id); if (!list) return;
        list.replaceChildren();
        if (!this.datasets.length) { const message = document.createElement('p'); message.textContent = 'Nenhum conjunto salvo. Configure uma fonte na sua conta.'; list.append(message); }
        for (const dataset of this.datasets) {
            const active = (session?.files || []).find(file => file.source?.dataset_id === dataset.id);
            const row = document.createElement('div'); row.className = 'saved-source-choice';
            const label = document.createElement('label'); label.className = 'saved-source-label';
            const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = dataset.id;
            checkbox.dataset.datasetId = dataset.id;
            const title = document.createElement('span'); title.textContent = dataset.name + (active ? ' · importado' : '');
            label.append(checkbox, title); row.append(label);
            const fields = document.createElement('div'); fields.className = 'source-parameter-fields'; fields.hidden = true;
            const parameters = { ...(dataset.definition?.parameters || {}), ...(active?.source?.parameters || {}) };
            for (const [name, value] of Object.entries(parameters)) {
                const fieldLabel = document.createElement('label'); fieldLabel.textContent = name;
                let input;
                if (typeof value === 'boolean') {
                    input = document.createElement('select');
                    for (const bool of [true, false]) { const option = document.createElement('option'); option.value = String(bool); option.textContent = bool ? 'Sim' : 'Não'; input.append(option); }
                } else {
                    input = document.createElement('input'); input.type = typeof value === 'number' ? 'number' : 'text';
                    if (input.type === 'number') input.step = 'any';
                }
                input.value = value === null ? 'null' : String(value); input.dataset.parameter = name; input.dataset.valueType = value === null ? 'null' : typeof value;
                fieldLabel.append(input); fields.append(fieldLabel);
            }
            checkbox.addEventListener('change', () => { fields.hidden = !checkbox.checked; }); row.append(fields); list.append(row);
        }
    },
    selections(id) {
        const list = document.getElementById(id); if (!list) return [];
        return Array.from(list.querySelectorAll('input[data-dataset-id]:checked')).map(checkbox => {
            const parameters = {};
            for (const field of checkbox.closest('.saved-source-choice').querySelectorAll('[data-parameter]')) {
                let value = field.value;
                if (field.dataset.valueType === 'number') { if (!value.trim() || !Number.isFinite(Number(value))) throw new Error('Informe um número válido para ' + field.dataset.parameter); value = Number(value); }
                if (field.dataset.valueType === 'boolean') value = value === 'true';
                if (field.dataset.valueType === 'null' && value === 'null') value = null;
                parameters[field.dataset.parameter] = value;
            }
            return { dataset_id: checkbox.value, parameters };
        });
    },
    async importSelected(button, refresh = false) {
        if (button.disabled || !App.currentSession?.job_id) return;
        button.disabled = true;
        try {
            const sources = this.selections('report-source-list');
            if (!sources.length) throw new Error('Escolha pelo menos um conjunto de dados.');
            const active = new Set((App.currentSession.files || []).map(file => file.source?.dataset_id));
            if (sources.some(source => refresh ? !active.has(source.dataset_id) : active.has(source.dataset_id))) throw new Error(refresh ? 'Selecione apenas fontes já importadas para atualizar.' : 'Use Atualizar selecionados para fontes já importadas.');
            if (refresh) await API.refreshSources(App.currentSession.job_id, sources);
            else { const data = new FormData(); data.set('source_datasets', JSON.stringify(sources)); await API.addFiles(App.currentSession.job_id, data); }
            App.showNotice('Importação agendada. O relatório será atualizado quando os dados estiverem prontos.'); App.startPolling();
        } catch (error) { App.showNotice(error.message); }
        finally { button.disabled = false; }
    },
    update(session) {
        if (!session) return;
        const signature = JSON.stringify([session.job_id, (session.files || []).map(file => file.source), session.source_snapshot_history]);
        if (signature !== this.signature) {
            this.signature = signature; this.renderChooser('report-source-list', session); this.renderHistory(session);
        }
        const busy = ['queued', 'running'].includes(session.source_import?.status);
        for (const id of ['import-sources-btn', 'refresh-sources-btn']) { const button = document.getElementById(id); if (button) button.disabled = busy; }
        const status = document.getElementById('source-import-status');
        if (status) status.textContent = busy ? 'Importando dados…' : session.source_import?.status === 'failed' ? (session.source_import.error?.message || 'A importação falhou. Tente novamente.') : '';
    },
    renderHistory(session) {
        const list = document.getElementById('source-snapshot-list'); if (!list) return;
        list.replaceChildren();
        const active = new Set((session.files || []).map(file => file.source?.snapshot_id));
        for (const file of [...(session.source_snapshot_history || [])].reverse()) {
            const source = file.source || {}; if (!source.snapshot_id) continue;
            const item = document.createElement('li'); const link = document.createElement('a');
            link.href = `/api/sessions/${encodeURIComponent(session.job_id)}/source-snapshots/${encodeURIComponent(source.snapshot_id)}/download`;
            const date = new Date(source.fetched_at); const when = Number.isNaN(date.getTime()) ? '' : date.toLocaleString('pt-BR');
            link.textContent = `${source.dataset_name || 'Conjunto'} · ${source.row_count ?? '?'} linhas · ${when}${active.has(source.snapshot_id) ? ' · em uso' : ''}`;
            item.append(link); list.append(item);
        }
        document.getElementById('source-snapshot-history').hidden = !list.children.length;
    }
};
