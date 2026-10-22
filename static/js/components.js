/* Component editing keeps drafts local and sends one explicitly scoped job. */
const Components = {
    selectedId: null, jobId: null, originalCode: '', baseVersion: 0,
    dirty: false, busy: false, requestToken: null, handledToken: null, listSignature: null,

    node(tag, text, className = '') {
        const node = document.createElement(tag);
        if (text != null) node.textContent = text;
        if (className) node.className = className;
        return node;
    },

    init() {
        for (const id of ['component-search', 'component-filter']) {
            document.getElementById(id)?.addEventListener('input', () => this.renderList(App.currentSession?.artifacts || [], true));
        }
        document.getElementById('modal-form').addEventListener('input', event => {
            if (event.target.id !== 'component-instruction') {
                this.dirty = true;
                if (!this.busy) this.status('Modificações locais. Aplique as alterações para atualizar a prévia e o relatório.');
            }
            if (event.target.id === 'component-text') this.wordCount();
        });
        for (const id of ['m-type', 'm-preset']) {
            document.getElementById(id).addEventListener('change', event => {
                this.dirty = true;
                if (event.target.id === 'm-preset') {
                    const preset = App.componentLibrary.presets?.find(item => item.id === event.target.value);
                    if (preset) {
                        document.getElementById('m-visual-options').value = JSON.stringify(preset.default_options || {}, null, 2);
                        document.getElementById('m-render-mode').value = 'library';
                        const binding = this.json('m-data-binding'); binding.roles = {};
                        document.getElementById('m-data-binding').value = JSON.stringify(binding, null, 2);
                        App.updateIndicatorCodeField({ component_preset_id: preset.id, render_mode: 'library' });
                    }
                }
                this.tools();
            });
        }
        document.getElementById('indicator-modal').addEventListener('cancel', event => {
            if (this.dirty && !confirm('Descartar as alterações locais deste componente?')) event.preventDefault();
            else this.dirty = false;
        });
        for (const id of ['m-visual-options', 'm-data-binding']) {
            document.getElementById(id).addEventListener('change', () => this.tools());
        }
        document.getElementById('m-visual-theme').addEventListener('change', () => {
            const options = this.json('m-visual-options');
            for (const key of ['color', 'line_color', 'bar_color']) delete options[key];
            document.getElementById('m-visual-options').value = JSON.stringify(options, null, 2);
            this.dirty = true; this.tools();
        });
        window.addEventListener('beforeunload', event => {
            if (this.dirty && document.getElementById('indicator-modal').open) { event.preventDefault(); event.returnValue = ''; }
        });
    },

    renderList(artifacts, force = false) {
        const search = document.getElementById('component-search')?.value.trim().toLocaleLowerCase() || '';
        const filter = document.getElementById('component-filter')?.value || 'all';
        const signature = JSON.stringify([artifacts, search, filter, App.currentSession?.component_edit]);
        if (!force && signature === this.listSignature) return;
        this.listSignature = signature;
        const container = document.getElementById('artifacts-visual');
        if (!container) return;
        UI.clearElement(container);
        let count = 0;
        artifacts.forEach((artifact, index) => {
            const kind = ['text', 'metric'].includes(artifact.type) ? artifact.type : 'visual';
            if (filter !== 'all' && kind !== filter) return;
            if (search && ![artifact.indicator, artifact.reason, ...(artifact.columns || [])].join(' ').toLocaleLowerCase().includes(search)) return;
            count += 1;
            const card = this.node('article', null, 'artifact-card component-card');
            const header = this.node('div', null, 'artifact-header');
            header.appendChild(this.node('h4', artifact.indicator || 'Componente'));
            const preset = App.componentLibrary.presets?.find(item => item.id === artifact.component_preset_id);
            header.appendChild(this.node('span', kind === 'text' ? 'Texto' : kind === 'metric' ? 'Métrica' : preset?.name || 'Gráfico', 'component-kind'));
            card.appendChild(header);
            card.appendChild(this.node('p', artifact.reason || (kind === 'metric' ? 'Valor calculado usado na análise e nos textos do relatório.' : kind === 'text' ? 'Narrativa e conclusões do relatório.' : 'Edite os dados, a apresentação ou peça uma melhoria.'), 'component-description'));
            if (kind === 'metric') card.appendChild(this.node('p', 'Consulte o resultado em Métricas & Dados. Para revisar o cálculo, peça a alteração no chat do relatório.', 'component-hint'));
            const binding = artifact.data_binding || {};
            if (binding.source) card.appendChild(this.node('p', `Fonte: ${binding.source}`, 'component-hint'));
            if (artifact.visual_warnings?.length) card.appendChild(this.node('p', artifact.visual_warnings.join(' · '), 'artifact-visual-warnings'));
            const operation = App.currentSession?.component_edit;
            if (operation?.component_id === artifact.id) card.appendChild(this.node('p', operation.message, 'component-operation-note'));
            const actions = this.node('div', null, 'artifact-actions');
            const edit = this.node('button', kind === 'metric' ? 'Ver em Métricas & Dados' : 'Editar e melhorar', 'btn btn-outline');
            edit.type = 'button'; edit.dataset.action = kind === 'metric' ? 'view-metric-data' : 'edit-indicator'; edit.dataset.index = index;
            edit.setAttribute('aria-label', kind === 'metric' ? `Ver resultado de ${artifact.indicator || 'métrica'}` : `Editar componente ${artifact.indicator || ''}`);
            actions.appendChild(edit);
            const remove = this.node('button', 'Excluir', 'btn btn-outline');
            remove.type = 'button'; remove.dataset.action = 'delete-indicator'; remove.dataset.index = index;
            remove.setAttribute('aria-label', `Excluir componente ${artifact.indicator || ''}`);
            actions.appendChild(remove); card.appendChild(actions); container.appendChild(card);
        });
        const summary = document.getElementById('component-count');
        if (summary) summary.textContent = `${count} de ${artifacts.length} componentes`;
        if (!count) container.appendChild(this.node('p', artifacts.length ? 'Nenhum componente corresponde à busca.' : 'Os componentes aparecerão aqui após a primeira análise.', 'component-empty'));
    },

    open(artifact, index, code) {
        this.selectedId = index === -1 ? null : artifact.id;
        this.jobId = App.currentSession.job_id; this.baseVersion = App.currentSession.pdf_version || 0;
        this.originalCode = code; this.dirty = false; this.requestToken = null;
        document.getElementById('component-instruction').value = '';
        document.getElementById('component-text').value = App.currentSession.component_texts?.[artifact.id] ?? artifact.text_content ?? '';
        document.getElementById('component-advanced').open = artifact.type === 'metric';
        const slot = App.currentSession.template?.find(item => item.id === artifact.id);
        document.getElementById('component-slot-info').textContent = slot ? `Página ${slot.page || 1} · ${slot.w} × ${slot.h} mm. A posição no relatório será preservada.` : 'Edite o conteúdo e aplique para atualizar o PDF.';
        this.setBusy(false); this.status('', ''); this.tools(); this.wordCount();
        this.update(App.currentSession);
    },

    status(message, state = 'info') {
        const status = document.getElementById('component-editor-status');
        status.textContent = message; status.hidden = !message; status.dataset.state = state;
    },

    setBusy(busy) {
        this.busy = busy;
        for (const field of document.querySelectorAll('.indicator-editor-panel input, .indicator-editor-panel select, .indicator-editor-panel textarea')) field.disabled = busy;
        for (const id of ['component-save-btn', 'component-improve-btn', 'modal-regenerate-btn']) {
            const button = document.getElementById(id);
            if (button) button.disabled = busy || (id !== 'component-save-btn' && !this.selectedId);
        }
    },

    tables() {
        return (App.currentSession?.files || []).flatMap(file => {
            const metadata = file.artifact || {};
            if (['pdf', 'text'].includes(metadata.type)) return [];
            const name = metadata.filename || file.original_filename;
            return metadata.sheets?.length ? metadata.sheets.map((sheet, index) => ({ ...sheet, key: sheet.dataframe_key || (index ? `${name}::${sheet.sheet_name}` : name) })) : [{ ...metadata, key: name }];
        });
    },

    json(id) {
        const value = JSON.parse(document.getElementById(id).value || '{}');
        if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('As opções e os dados precisam ser objetos JSON.');
        return value;
    },

    tools() {
        const type = document.getElementById('m-type').value;
        const text = type === 'text';
        document.getElementById('component-text-tools').hidden = !text;
        document.getElementById('component-advanced').hidden = text;
        document.getElementById('component-visual-tools').hidden = text || type === 'metric';
        document.querySelector('.component-ai-tools').hidden = type === 'metric';
        const quick = document.getElementById('component-quick-actions'); UI.clearElement(quick);
        const suggestions = text ? [
            ['Mais conciso', 'Encurte o texto, preservando números, unidades e conclusões sustentadas.'],
            ['Mais acionável', 'Torne as recomendações mais concretas, ligadas aos achados já sustentados pelos dados.'],
            ['Revisar redação', 'Corrija gramática e melhore a clareza, preservando fatos, números e significado.'],
        ] : [
            ['Mais legível', 'Melhore a legibilidade deste gráfico no espaço atual, com rótulos claros e sem sobreposições.'],
            ['Mostrar valores', 'Mostre os valores nos elementos do gráfico, mantendo boa legibilidade.'],
            ['Nomes claros', 'Use nomes amigáveis para eixos e legendas, sem renomear as colunas da fonte.'],
        ];
        for (const [label, instruction] of suggestions) {
            const button = this.node('button', label, 'btn btn-outline'); button.type = 'button';
            button.addEventListener('click', () => { document.getElementById('component-instruction').value = instruction; document.getElementById('component-instruction').focus(); });
            quick.appendChild(button);
        }
        if (text || type === 'metric') {
            UI.clearElement(document.getElementById('component-data-tools'));
            UI.clearElement(document.getElementById('component-display-tools'));
            return;
        }
        try { this.visualTools(); } catch (error) { this.status(error.message, 'error'); }
    },

    select(container, label, values, selected, change, multiple = false) {
        const wrapper = this.node('label', label); const select = this.node('select');
        select.setAttribute('aria-label', label);
        select.multiple = multiple; if (multiple) select.size = Math.min(4, Math.max(2, values.length));
        if (!multiple) select.appendChild(UI.buildOption('', 'Selecionar...'));
        for (const item of values) {
            const value = typeof item === 'string' ? item : item.value;
            const option = UI.buildOption(value, typeof item === 'string' ? item : item.label);
            option.selected = multiple ? (selected || []).includes(value) : selected === value;
            select.appendChild(option);
        }
        select.addEventListener('change', () => { this.dirty = true; change(multiple ? Array.from(select.selectedOptions, option => option.value) : select.value); });
        wrapper.appendChild(select); container.appendChild(wrapper); return select;
    },

    visualTools() {
        const data = document.getElementById('component-data-tools'); const display = document.getElementById('component-display-tools');
        UI.clearElement(data); UI.clearElement(display);
        const tables = this.tables(); const binding = this.json('m-data-binding'); const options = this.json('m-visual-options');
        const preset = App.componentLibrary.presets?.find(item => item.id === document.getElementById('m-preset').value);
        const writeBinding = () => { document.getElementById('m-data-binding').value = JSON.stringify(binding, null, 2); };
        const writeOptions = () => { document.getElementById('m-visual-options').value = JSON.stringify(options, null, 2); };
        this.select(data, 'Fonte de dados', tables.map(table => table.key), binding.source, value => {
            binding.source = value; binding.roles = {}; document.getElementById('m-cols').value = ''; writeBinding(); this.visualTools();
        });
        const table = tables.find(table => table.key === binding.source);
        const columns = (table?.columns || []).map(column => column.name);
        const roleLabels = { category: 'Categoria', value: 'Valor', series: 'Série / grupo', date: 'Data', x: 'Eixo X', y: 'Eixo Y', color: 'Agrupar por cor', dimensions: 'Agrupar por (selecione uma ou mais)', metrics: 'Valores (selecione uma ou mais)', columns: 'Colunas (selecione uma ou mais)', target: 'Coluna de meta' };
        for (const role of preset?.data_roles || []) {
            let value = binding.roles?.[role.name]; if (role.multiple && !Array.isArray(value)) value = value ? [value] : [];
            const select = this.select(data, `${roleLabels[role.name] || role.name}${role.required ? ' *' : ''}`, columns, value, selected => {
                binding.roles = { ...(binding.roles || {}) };
                if (selected.length) binding.roles[role.name] = selected; else delete binding.roles[role.name];
                writeBinding();
                document.getElementById('m-cols').value = Object.values(binding.roles).flat().filter(Boolean).map(column => `${binding.source}:${column}`).join(', ');
            }, role.multiple);
            select.required = Boolean(role.required && columns.length);
        }
        this.select(data, 'Cálculo', [{ value: 'sum', label: 'Soma' }, { value: 'mean', label: 'Média' }, { value: 'median', label: 'Mediana' }, { value: 'count', label: 'Contagem' }, { value: 'min', label: 'Mínimo' }, { value: 'max', label: 'Máximo' }], binding.aggregation || 'sum', value => { binding.aggregation = value || 'sum'; writeBinding(); });
        this.select(data, 'Ordenação', [{ value: 'desc', label: 'Maior primeiro' }, { value: 'asc', label: 'Menor primeiro' }, { value: 'none', label: 'Ordem original' }], binding.sort || 'none', value => { binding.sort = value || 'none'; writeBinding(); });
        const supported = preset?.options_schema?.properties || {};
        const colorKey = supported.bar_color ? 'bar_color' : supported.line_color ? 'line_color' : supported.color ? 'color' : null;
        if (colorKey) {
            const wrapper = this.node('label', 'Cor do gráfico'); const input = this.node('input'); input.type = 'color';
            input.value = options[colorKey] || options.color || '#2563eb';
            input.addEventListener('change', () => { this.dirty = true; options[colorKey] = input.value; writeOptions(); });
            wrapper.appendChild(input);
            const reset = this.node('button', 'Usar cores do tema', 'btn btn-outline'); reset.type = 'button';
            reset.addEventListener('click', () => { this.dirty = true; for (const key of ['color', 'bar_color', 'line_color']) delete options[key]; writeOptions(); this.visualTools(); });
            wrapper.appendChild(reset); display.appendChild(wrapper);
        }
        if (supported.orientation) this.select(display, 'Orientação', ['horizontal', 'vertical'].map(value => ({ value, label: value === 'horizontal' ? 'Horizontal' : 'Vertical' })), options.orientation || preset.default_options?.orientation, value => { if (value) options.orientation = value; else delete options.orientation; writeOptions(); });
        if (supported.date_granularity) this.select(display, 'Agrupar datas por', ['day', 'week', 'month', 'quarter', 'year'].map((value, i) => ({ value, label: ['Dia', 'Semana', 'Mês', 'Trimestre', 'Ano'][i] })), options.date_granularity || 'day', value => { if (value) options.date_granularity = value; else delete options.date_granularity; writeOptions(); });
        this.select(display, 'Formato dos números', [{ value: 'auto', label: 'Automático' }, { value: 'percent', label: 'Percentual (valores de 0 a 1)' }, { value: 'currency', label: 'Monetário' }, { value: '.0f', label: 'Inteiro' }, { value: '.2f', label: 'Duas casas decimais' }], options.format || 'auto', value => { if (value && value !== 'auto') options.format = value; else delete options.format; writeOptions(); });
        for (const [key, label] of [['top_n', 'Quantidade de itens'], ['rolling_window', 'Janela da média móvel'], ['target', 'Meta'], ['max_points', 'Máximo de pontos']]) {
            if (!supported[key]) continue;
            const wrapper = this.node('label', label); const input = this.node('input'); input.type = 'number'; input.min = '1'; input.max = '10000';
            input.value = options[key] ?? preset.default_options?.[key] ?? '';
            input.addEventListener('change', () => { this.dirty = true; if (input.value) options[key] = Number(input.value); else delete options[key]; writeOptions(); });
            wrapper.appendChild(input); display.appendChild(wrapper);
        }
        for (const [key, label] of [['show_values', 'Mostrar valores'], ['aggregate_others', 'Agrupar itens restantes em Outros'], ['show_markers', 'Marcar pontos'], ['rolling_average', 'Média móvel'], ['show_trendline', 'Linha de tendência'], ['show_totals', 'Mostrar total'], ['striped_rows', 'Alternar cor das linhas'], ['show_percent', 'Mostrar percentuais']]) {
            if (!supported[key]) continue;
            const wrapper = this.node('label', null, 'component-checkbox'); const input = this.node('input'); input.type = 'checkbox';
            input.checked = options[key] ?? preset.default_options?.[key] ?? false;
            input.addEventListener('change', () => { this.dirty = true; options[key] = input.checked; writeOptions(); });
            wrapper.appendChild(input); wrapper.appendChild(this.node('span', label)); display.appendChild(wrapper);
        }
    },

    wordCount() {
        const text = document.getElementById('component-text').value.trim();
        document.getElementById('component-word-count').textContent = `${text ? text.split(/\s+/).length : 0} palavras · Edite livremente ou peça uma revisão à IA.`;
        if (document.getElementById('m-type').value === 'text') this.renderTextPreview(document.getElementById('component-text').value, this.dirty);
    },

    renderTextPreview(text, draft = false) {
        const preview = document.getElementById('indicator-preview-body'); UI.clearElement(preview);
        const block = this.node('article', null, 'component-text-preview');
        block.appendChild(this.node('h3', document.getElementById('m-name').value));
        block.appendChild(this.node('p', text || 'Escreva o texto ou use uma das sugestões de melhoria.'));
        preview.appendChild(block);
        preview.appendChild(this.node('p', 'Prévia de leitura. O texto será distribuído no espaço reservado no PDF.', 'component-hint component-text-preview-note'));
        document.getElementById('component-preview-version').textContent = draft ? '· rascunho local' : `· versão ${App.currentSession?.pdf_version || 0}`;
    },

    async submit({ improve = false, regenerate = false } = {}) {
        if (this.busy || !this.selectedId) return;
        const jobId = this.jobId; const id = this.selectedId;
        try {
            const { newArt } = App.currentIndicatorPayload();
            if (!document.getElementById('modal-form').reportValidity()) return;
            const instruction = document.getElementById('component-instruction').value.trim();
            if (improve && !instruction) throw new Error('Descreva a melhoria ou escolha uma das sugestões.');
            if (!newArt.indicator.trim()) throw new Error('Informe um nome para o componente.');
            const codeField = document.getElementById('m-code');
            const current = App.currentSession.artifacts.find(item => item.id === id);
            if (newArt.id !== id || !current) throw new Error('O componente mudou. Feche o editor e abra-o novamente.');
            for (const key of ['sub_type', 'component_preset_id', 'visual_theme_id', 'chart_style_id', 'render_mode', 'data_binding']) {
                if (newArt[key] === undefined) newArt[key] = null;
            }
            const payload = { artifact: newArt, base_pdf_version: this.baseVersion, regenerate, instruction: improve ? instruction : '' };
            if (newArt.type === 'text') {
                payload.text = document.getElementById('component-text').value;
                if (!improve && !regenerate && !payload.text.trim()) throw new Error('Escreva o texto ou use Recriar com IA.');
            }
            else if (codeField.dataset.placeholderCode !== '1' && codeField.value !== this.originalCode) payload.code = codeField.value;
            this.setBusy(true); this.status('Enviando alterações. A prévia atual permanece disponível enquanto processamos.', 'running');
            const result = await API.editComponent(jobId, id, payload);
            if (App.currentSession?.job_id !== jobId || this.selectedId !== id) return;
            this.requestToken = result.operation.id;
            App.currentSession.component_edit = result.operation;
            this.dirty = false; App.startPolling();
        } catch (error) { this.setBusy(false); this.status(error.message || 'Não foi possível salvar o componente.', 'error'); }
    },

    update(session) {
        if (!document.getElementById('indicator-modal')?.open || this.jobId !== session.job_id) return;
        const operation = session.component_edit;
        if (!operation || operation.component_id !== this.selectedId) return;
        const active = ['queued', 'running', 'rendering'].includes(operation.status);
        this.setBusy(active);
        if (active) { this.status(operation.message, 'running'); return; }
        if (this.requestToken && operation.id === this.requestToken && operation.status === 'completed' && operation.id !== this.handledToken) {
            this.handledToken = operation.id;
            const index = session.artifacts.findIndex(item => item.id === this.selectedId);
            if (index >= 0) App.handleEditIndicator(index);
        }
        this.status(operation.message + (operation.status === 'failed' ? ' A última versão do relatório foi preservada. Revise o pedido e tente novamente.' : ''), operation.status === 'failed' ? 'error' : 'success');
    },
};
