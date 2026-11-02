/* Verified links between tables, shown in the workspace and the shared viewer. */
const Relationships = {
    list(session) {
        return session?.semantic_model?.verified_relationships || [];
    },

    label(relationship) {
        return `${relationship.to.dataset} (${relationship.from.column} → ${relationship.to.column})`;
    },

    section(session) {
        const relationships = this.list(session);
        const section = document.createElement('section');
        section.className = 'relationships-section';
        const title = document.createElement('h4');
        title.textContent = 'Relações entre tabelas';
        const help = document.createElement('p');
        help.className = 'relationships-help';
        help.textContent = relationships.length
            ? 'Chaves conferidas nos dados. Cada linha da tabela de origem corresponde a no máximo uma linha da tabela relacionada, então combinar as duas não duplica totais.'
            : 'Nenhuma relação entre tabelas foi encontrada nos dados deste relatório.';
        section.append(title, help);
        if (!relationships.length) return section;

        const scroll = document.createElement('div');
        scroll.className = 'operations-scroll';
        const table = document.createElement('table');
        table.className = 'operations-table relationships-table';
        const head = table.createTHead().insertRow();
        for (const label of ['Tabela de origem', 'Coluna', 'Tabela relacionada', 'Coluna', 'Tipo', 'Valores encontrados']) {
            const th = document.createElement('th'); th.textContent = label; head.appendChild(th);
        }
        const body = table.createTBody();
        for (const item of relationships) {
            const row = body.insertRow();
            const kind = item.cardinality === 'one_to_one' ? 'Um para um' : 'Muitos para um';
            const coverage = `${Math.round((item.coverage || 0) * 100)}% (${item.matched_values} de ${item.from_distinct})`;
            for (const value of [item.from.dataset, item.from.column, item.to.dataset, item.to.column, kind, coverage]) {
                row.insertCell().textContent = value;
            }
        }
        scroll.appendChild(table);
        section.appendChild(scroll);
        return section;
    },
};
