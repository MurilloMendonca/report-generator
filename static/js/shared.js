/* Public report viewer: every request is a token-authorized GET, without account cookies. */
const SharedReport = {
    token: null, session: null, timer: null, signature: null, pdfVersion: null, previewUrl: null, previewRequest: 0,
    node(tag, text, className = '') {
        const node = document.createElement(tag);
        if (text != null) node.textContent = text;
        if (className) node.className = className;
        return node;
    },
    url(path = '') { return `/api/shared-reports/${encodeURIComponent(this.token)}${path}`; },
    async request(path = '') {
        const response = await fetch(this.url(path), { credentials: 'omit', cache: 'no-store' });
        if (!response.ok) {
            const error = new Error(response.status === 404 ? 'Este link expirou, foi revogado ou não está disponível.' : 'Não foi possível carregar o relatório. Tentaremos novamente.');
            error.status = response.status; throw error;
        }
        return response;
    },
    async init() {
        const token = window.location.pathname.match(/^\/share\/([A-Za-z0-9_-]{43})$/)?.[1];
        if (!token) { this.unavailable('Este link não está disponível.'); return; }
        this.token = token;
        document.querySelectorAll('[data-shared-tab]').forEach(button => button.addEventListener('click', () => {
            document.querySelectorAll('.shared-tab, [data-shared-tab]').forEach(node => node.classList.remove('active'));
            button.classList.add('active'); document.getElementById(button.dataset.sharedTab).classList.add('active');
        }));
        document.getElementById('shared-file-close').addEventListener('click', () => document.getElementById('shared-file-modal').close());
        document.getElementById('shared-file-modal').addEventListener('close', () => {
            this.previewRequest += 1;
            if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
            this.previewUrl = null;
        });
        document.getElementById('shared-copy-code').addEventListener('click', async () => {
            try { await navigator.clipboard.writeText(this.session?.script || ''); this.notice('Código copiado.'); }
            catch (_error) { this.notice('Selecione o código para copiá-lo.'); }
        });
        await this.load();
    },
    notice(message) { const node = document.getElementById('shared-notice'); node.textContent = message; node.hidden = !message; },
    unavailable(message) {
        clearTimeout(this.timer);
        this.session = null; this.signature = null; this.previewRequest += 1;
        document.getElementById('shared-content').hidden = true;
        document.getElementById('shared-download').hidden = true;
        document.getElementById('shared-pdf-viewer').removeAttribute('src');
        document.getElementById('shared-goal').textContent = '';
        document.getElementById('shared-title').textContent = 'Relatório indisponível';
        document.getElementById('shared-expiry').textContent = '';
        for (const id of ['shared-components', 'shared-layout', 'shared-script', 'shared-settings', 'shared-data', 'shared-files', 'shared-history']) document.getElementById(id).replaceChildren();
        document.getElementById('shared-file-body').replaceChildren(); document.getElementById('shared-file-modal').close();
        if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
        this.previewUrl = null; this.notice(message);
    },
    async load() {
        try {
            const response = await this.request(); const session = await response.json();
            this.session = session; this.notice('');
            document.getElementById('shared-content').hidden = false;
            if (JSON.stringify(session) !== this.signature) {
                this.signature = JSON.stringify(session); this.render(session);
            }
        } catch (error) {
            if (error.status === 404) { this.unavailable(error.message); return; }
            this.notice(error.message);
        }
        // Recheck even finished reports: links can be revoked and owners can publish updates.
        this.timer = setTimeout(() => this.load(), 15000);
    },
    render(session) {
        const title = session.settings?.title || 'Relatório compartilhado';
        document.getElementById('shared-title').textContent = title; document.title = title;
        document.getElementById('shared-goal').textContent = session.current_goal || session.initial_prompt || '';
        document.getElementById('shared-expiry').textContent = session.sharing?.expires_at ? `Acesso válido até ${new Date(session.sharing.expires_at).toLocaleString('pt-BR')}` : 'Link sem expiração';
        const download = document.getElementById('shared-download'); download.hidden = !session.has_pdf; download.href = this.url('/pdf'); download.download = 'relatorio.pdf';
        document.getElementById('shared-pdf-empty').hidden = Boolean(session.has_pdf);
        if (session.has_pdf && session.pdf_version !== this.pdfVersion) {
            document.getElementById('shared-pdf-viewer').src = `${this.url('/pdf')}?v=${session.pdf_version || 0}`; this.pdfVersion = session.pdf_version;
        }
        this.renderComponents(session); this.renderLayout(session);
        document.getElementById('shared-script').textContent = session.script || 'O código ainda não está disponível.';
        this.jsonPanel('shared-settings', 'Configurações do relatório', { provider: session.provider, model_name: session.model_name, ...session.settings });
        this.jsonPanel('shared-data', 'Métricas calculadas', session.calculated_metrics || {});
        const data = document.getElementById('shared-data');
        for (const file of session.files || []) data.appendChild(this.details(file.original_filename, file.artifact));
        if (Object.keys(session.document_insights || {}).length) data.appendChild(this.details('Achados documentais', session.document_insights));
        if (session.semantic_model) data.appendChild(this.details('Relações entre os dados', session.semantic_model));
        if (session.data_preprocessing || session.data_filter) data.appendChild(this.details('Pré-processamento dos dados', session.data_preprocessing || session.data_filter));
        this.renderFiles(session); this.renderHistory(session);
    },
    details(title, value) {
        const details = this.node('details', null, 'shared-details'); details.appendChild(this.node('summary', title));
        details.appendChild(this.node('pre', typeof value === 'string' ? value : JSON.stringify(value, null, 2))); return details;
    },
    jsonPanel(id, title, data) {
        const panel = document.getElementById(id); panel.replaceChildren(this.node('h2', title), this.node('pre', JSON.stringify(data, null, 2)));
    },
    renderComponents(session) {
        const panel = document.getElementById('shared-components');
        const open = new Set(Array.from(panel.querySelectorAll('details[open]'), node => node.dataset.componentId)); panel.replaceChildren();
        for (const artifact of session.artifacts || []) {
            const card = this.node('article', null, 'artifact-card component-card'); card.appendChild(this.node('h3', artifact.indicator));
            card.appendChild(this.node('p', artifact.type === 'text' ? 'Texto' : artifact.type === 'metric' ? 'Métrica' : 'Gráfico / tabela', 'component-hint'));
            card.appendChild(this.node('p', artifact.reason || ''));
            if (session.component_texts?.[artifact.id]) card.appendChild(this.node('p', session.component_texts[artifact.id], 'shared-narrative'));
            const slot = session.template?.find(item => item.id === artifact.id);
            if (slot && session.has_pdf && artifact.type !== 'metric') {
                const view = this.node('button', `Ver no PDF · página ${slot.page || 1}`, 'btn btn-outline'); view.type = 'button';
                view.addEventListener('click', () => {
                    document.querySelector('[data-shared-tab="shared-pdf"]').click();
                    document.getElementById('shared-pdf-viewer').src = `${this.url('/pdf')}?v=${session.pdf_version || 0}#page=${Number(slot.page) || 1}`;
                });
                card.appendChild(view);
            }
            if (artifact.type === 'metric') card.appendChild(this.node('pre', JSON.stringify(session.calculated_metrics?.[artifact.id] ?? 'Indisponível', null, 2)));
            const detail = this.details('Definição e código do componente', { ...artifact, code: session.components?.[artifact.id] || 'Renderizado pela biblioteca de componentes.' });
            detail.dataset.componentId = artifact.id; detail.open = open.has(artifact.id); card.appendChild(detail); panel.appendChild(card);
        }
        if (!panel.children.length) panel.textContent = 'Nenhum componente disponível.';
    },
    renderLayout(session) {
        const panel = document.getElementById('shared-layout'); panel.replaceChildren(this.node('h2', 'Layout do relatório'));
        const names = new Map((session.artifacts || []).map(item => [item.id, item.indicator]));
        const pages = [...new Set((session.template || []).map(item => item.page || 1))].sort((a, b) => a - b);
        for (const page of pages) {
            const section = this.node('section', null, 'shared-layout-page'); section.appendChild(this.node('h3', `Página ${page}`));
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 210 297'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `Blocos da página ${page}`);
            for (const block of session.template.filter(item => (item.page || 1) === page)) {
                const rect = document.createElementNS(svg.namespaceURI, 'rect');
                for (const [key, value] of Object.entries({ x: block.x, y: block.y, width: block.w, height: block.h })) rect.setAttribute(key, String(Math.max(0, Math.min(297, Number(value) || 0))));
                rect.setAttribute('fill', '#edf4ff'); rect.setAttribute('stroke', '#41608c'); rect.setAttribute('stroke-width', '0.5'); svg.appendChild(rect);
                const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('x', String((Number(block.x) || 0) + 2)); label.setAttribute('y', String((Number(block.y) || 0) + 6)); label.setAttribute('font-size', '3.5'); label.textContent = String(names.get(block.id) || block.id).slice(0, 28); svg.appendChild(label);
            }
            section.appendChild(svg); panel.appendChild(section);
        }
        panel.appendChild(this.details('Posições e dimensões em milímetros', session.template || []));
    },
    renderFiles(session) {
        const panel = document.getElementById('shared-files'); panel.replaceChildren(this.node('h2', 'Arquivos do relatório'));
        (session.files || []).forEach((file, index) => {
            const row = this.node('article', null, 'artifact-card'); row.appendChild(this.node('h3', file.original_filename));
            for (const variant of file.has_preprocessed ? ['original', 'preprocessed'] : ['original']) {
                const group = this.node('div', null, 'shared-file-actions'); group.appendChild(this.node('span', variant === 'original' ? 'Original' : 'Pré-processado'));
                const open = this.node('button', 'Visualizar', 'btn btn-outline'); open.type = 'button'; open.addEventListener('click', () => this.openFile(index, variant)); group.appendChild(open);
                const download = this.node('a', 'Baixar', 'btn btn-outline'); download.href = this.url(`/files/${index}/download?variant=${variant}`); group.appendChild(download); row.appendChild(group);
            }
            panel.appendChild(row);
        });
        if (session.source_snapshot_history?.length) {
            panel.appendChild(this.node('h3', 'Versões anteriores das fontes'));
            for (const snapshot of session.source_snapshot_history) {
                if (!/^[a-f0-9]{32}$/.test(snapshot.snapshot_id)) continue;
                const link = this.node('a', `${snapshot.dataset_name || snapshot.original_filename} · ${snapshot.row_count ?? '?'} linhas`, 'shared-snapshot-link');
                link.href = this.url(`/source-snapshots/${snapshot.snapshot_id}/download`); panel.appendChild(link);
            }
        }
    },
    async openFile(index, variant) {
        const file = this.session.files[index]; const metadata = variant === 'preprocessed' ? file.artifact : file.raw_artifact || file.artifact;
        const modal = document.getElementById('shared-file-modal'); const body = document.getElementById('shared-file-body');
        const request = ++this.previewRequest;
        document.getElementById('shared-file-title').textContent = `${file.original_filename} · ${variant === 'original' ? 'Original' : 'Pré-processado'}`;
        body.replaceChildren(this.node('p', 'Carregando prévia...', 'preview-loading')); modal.showModal();
        if (this.previewUrl) URL.revokeObjectURL(this.previewUrl); this.previewUrl = null;
        if (/\.(xlsx?|ods)$/i.test(file.original_filename)) {
            body.replaceChildren();
            for (const sheet of metadata?.sheets || [metadata || {}]) {
                const section = this.node('section', null, 'spreadsheet-preview'); section.appendChild(this.node('h4', sheet.sheet_name || file.original_filename));
                section.appendChild(this.node('p', `${sheet.shape?.rows ?? '?'} linhas · amostra das primeiras linhas`));
                const rows = (sheet.sample || []).slice(0, 8); const columns = (sheet.columns || []).map(column => column.name).slice(0, 12);
                const table = this.node('table'); const head = this.node('tr'); columns.forEach(column => head.appendChild(this.node('th', column))); table.appendChild(head);
                for (const record of rows) { const row = this.node('tr'); columns.forEach(column => row.appendChild(this.node('td', record[column] == null ? '—' : String(record[column])))); table.appendChild(row); }
                const scroll = this.node('div', null, 'spreadsheet-table-scroll'); scroll.appendChild(table); section.appendChild(scroll); body.appendChild(section);
            }
            return;
        }
        try {
            const response = await this.request(`/files/${index}/preview?variant=${variant}`); const blob = await response.blob();
            if (request !== this.previewRequest || !modal.open) return;
            if (blob.type === 'application/pdf') {
                this.previewUrl = URL.createObjectURL(blob); const frame = this.node('iframe'); frame.title = 'Prévia do arquivo'; frame.className = 'preview-frame'; frame.src = this.previewUrl; body.replaceChildren(frame);
            } else {
                const bytes = await blob.slice(0, 800000).arrayBuffer();
                if (request !== this.previewRequest || !modal.open) return;
                const encoding = variant === 'preprocessed' ? 'utf-8' : metadata?.parser?.encoding || file.validation?.encoding || 'utf-8';
                const text = new TextDecoder(encoding === 'utf-8-sig' ? 'utf-8' : encoding).decode(bytes); body.replaceChildren(this.node('pre', text.slice(0, 200000), 'preview-text'));
            }
        } catch (error) { if (request === this.previewRequest) body.replaceChildren(this.node('p', error.message, 'preview-empty')); }
    },
    renderHistory(session) {
        const panel = document.getElementById('shared-history'); panel.replaceChildren(this.node('h2', 'Histórico e consultas'));
        for (const message of session.refinement_messages || []) {
            const block = this.node('article', null, 'shared-message'); block.appendChild(this.node('strong', message.role === 'user' ? 'Pedido' : 'Resposta')); block.appendChild(this.node('p', message.content)); panel.appendChild(block);
        }
        for (const message of session.explore_history || []) {
            const block = this.node('article', null, 'shared-message'); block.appendChild(this.node('strong', message.role === 'user' ? 'Consulta' : 'Resultado')); block.appendChild(this.node('p', message.content));
            for (const chart of message.charts || []) {
                if (!/^[a-f0-9]{32}$/.test(message.turn_id) || !/^chart_[1-3]$/.test(chart.id)) continue;
                const image = this.node('img'); image.alt = chart.title || 'Gráfico da consulta'; image.src = this.url(`/explore/${message.turn_id}/charts/${chart.id}`); image.className = 'shared-explorer-image'; block.appendChild(image);
                if (chart.caption) block.appendChild(this.node('p', chart.caption));
            }
            panel.appendChild(block);
        }
        panel.appendChild(this.details('Logs do processamento', (session.logs || []).join('\n')));
    }
};
SharedReport.init().catch(() => SharedReport.unavailable('Não foi possível abrir o relatório compartilhado.'));
