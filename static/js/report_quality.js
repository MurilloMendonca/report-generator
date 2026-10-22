/* Shared by the owner's workspace and the public, read-only report viewer. */
const ReportQuality = {
    signature: null,
    node(tag, text, className = '') {
        const node = document.createElement(tag);
        if (text != null) node.textContent = text;
        if (className) node.className = className;
        return node;
    },
    render(session, readOnly = false) {
        const panel = document.getElementById(readOnly ? 'shared-quality' : 'tab-quality');
        if (!panel) return;
        const review = session.quality_review?.status ? session.quality_review : null;
        const signature = JSON.stringify([session.job_id, review, session.pdf_version, session.current_step]);
        if (signature === this.signature) return;
        this.signature = signature;
        panel.replaceChildren(this.node('h2', 'Revisão do relatório'));
        panel.appendChild(this.node('p', 'Conferimos os valores referenciados, os caracteres e o conteúdo visível no PDF. Interpretações, causas e gráficos personalizados ainda precisam de revisão humana.', 'component-hint'));
        const badge = document.getElementById('quality-count');
        if (badge) badge.textContent = review ? `(${review.error_count + review.warning_count})` : '';
        if (!review) {
            panel.appendChild(this.node('p', 'A revisão é executada automaticamente na próxima renderização do relatório.'));
            return;
        }
        const titles = { passed: 'Verificações concluídas', warning: 'Publicado com pontos para conferir', failed: 'Publicação bloqueada por problemas' };
        const summary = this.node('div', null, `quality-summary quality-${review.status}`);
        summary.setAttribute('role', 'status');
        summary.appendChild(this.node('strong', titles[review.status] || 'Revisão disponível'));
        summary.appendChild(this.node('p', `${review.error_count} problema(s) · ${review.warning_count} aviso(s) · ${new Date(review.checked_at).toLocaleString('pt-BR')}`));
        if (review.status === 'failed') summary.appendChild(this.node('p', session.pdf_version ? `O PDF da versão ${session.pdf_version} continua disponível. Os problemas abaixo pertencem à tentativa de atualização.` : 'Corrija os problemas para disponibilizar o primeiro PDF.'));
        else if (review.published_version !== session.pdf_version || !['completed', 'failed'].includes(session.current_step)) summary.appendChild(this.node('p', 'Estes resultados pertencem à última renderização revisada. Uma nova revisão será feita ao concluir as alterações.'));
        panel.appendChild(summary);
        const ids = new Set([...(review.components || []).map(item => item.id), ...(review.issues || []).map(item => item.component_id)]);
        for (const id of ids) {
            const artifact = session.artifacts?.find(item => item.id === id);
            const component = review.components?.find(item => item.id === id);
            const issues = review.issues.filter(item => item.component_id === id);
            const card = this.node('article', null, 'quality-component');
            card.appendChild(this.node('h3', artifact?.indicator || id || 'Documento completo'));
            if (component?.page) card.appendChild(this.node('p', `Página ${component.page}`, 'component-hint'));
            if (!issues.length) card.appendChild(this.node('p', 'Sem problemas detectados nas verificações disponíveis.', 'quality-ok'));
            const list = this.node('ul');
            for (const issue of issues) {
                const item = this.node('li', `${issue.severity === 'error' ? 'Problema' : 'Conferir'}: ${issue.message}`, `quality-${issue.severity}`);
                list.appendChild(item);
            }
            if (issues.length) card.appendChild(list);
            if (component?.references?.length) {
                const details = this.node('details'); details.appendChild(this.node('summary', `${component.references.length} referência(s) aos dados`));
                for (const reference of component.references) details.appendChild(this.node('p', `${reference.pointer}: ${reference.display}${reference.unit || reference.currency ? ` · ${reference.currency || reference.unit}` : ''}${reference.period ? ` · ${reference.period}` : ''}`));
                card.appendChild(details);
            }
            const actions = this.node('div', null, 'quality-actions');
            if (component?.page && session.pdf_version) {
                const button = this.node('button', review.status === 'failed' ? 'Ver PDF publicado' : 'Ver no PDF', 'btn btn-outline'); button.type = 'button';
                button.addEventListener('click', () => {
                    document.querySelector(readOnly ? '[data-shared-tab="shared-pdf"]' : '[data-tab-target="tab-pdf"]')?.click();
                    const viewer = document.getElementById(readOnly ? 'shared-pdf-viewer' : 'pdf-viewer');
                    const page = review.status === 'failed' ? session.template?.find(slot => slot.id === id)?.page || 1 : component.page;
                    if (viewer?.src) viewer.src = viewer.src.split('#')[0] + `#page=${page}`;
                }); actions.appendChild(button);
            }
            const data = this.node('button', 'Conferir métricas e fontes', 'btn btn-outline'); data.type = 'button';
            data.addEventListener('click', () => document.querySelector(readOnly ? '[data-shared-tab="shared-data"]' : '[data-tab-target="tab-data"]')?.click());
            actions.appendChild(data);
            if (!readOnly && artifact && artifact.type !== 'metric') {
                const edit = this.node('button', 'Editar componente', 'btn btn-secondary'); edit.type = 'button';
                edit.addEventListener('click', () => App.handleEditIndicator(App.currentSession.artifacts.findIndex(item => item.id === id)));
                actions.appendChild(edit);
            }
            card.appendChild(actions); panel.appendChild(card);
        }
    },
};
