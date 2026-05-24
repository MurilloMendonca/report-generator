const UI = {
    updateStatus(status, progress) {
        document.getElementById('status-text').innerText = status;
        document.getElementById('progress-bar').style.width = progress + "%";
    },

    renderLogs(logs) {
        document.getElementById('log-container').innerHTML = (logs || []).slice(-5).map(l => `<div>${l}</div>`).join('');
    },

    renderFiles(files) {
        document.getElementById('file-tray').innerHTML = (files || []).map(f => `
            <div class="file-card">
                <b>📄 ${f.original_filename}</b>
            </div>
        `).join('');
    },

    renderArtifacts(artifacts, onEdit, onDelete) {
        document.getElementById('artifacts-visual').innerHTML = (artifacts || []).map((art, i) => {
            const type = art.type || 'graphic';
            const colors = {
                metric: 'var(--md-sys-color-primary)',
                text: 'var(--md-sys-color-secondary)',
                graphic: 'var(--md-sys-color-tertiary, #3498db)'
            };
            const color = colors[type] || colors.graphic;
            
            return `
            <div class="artifact-card" style="border-left: 6px solid ${color}">
                <div class="artifact-header">
                    <b style="color: var(--md-sys-color-on-surface)">${art.indicator}</b>
                    <div class="artifact-actions">
                        <span style="font-size: 0.65rem; background: ${color}; color: white; padding: 2px 8px; border-radius: 10px; margin-right: 8px; font-weight: bold;">${type.toUpperCase()}</span>
                        <button class="btn btn-outline" style="padding: 4px 8px;" onclick="App.handleEditIndicator(${i})">✏️</button>
                        <button class="btn btn-danger" style="padding: 4px 8px;" onclick="App.handleDeleteIndicator(${i})">🗑️</button>
                    </div>
                </div>
                <div style="font-size: 0.75rem; color: var(--md-sys-color-outline);">ID: ${art.id}</div>
                <div style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant); margin-top: 0.5rem;">${art.reason || ""}</div>
            </div>
            `;
        }).join('');
    },

    renderLayoutCanvas(session, onUpdate) {
        const canvas = document.getElementById('layout-canvas');
        const templateSignature = JSON.stringify(session.template || []);
        if (canvas.getAttribute('data-job-id') === session.job_id && 
            canvas.getAttribute('data-template') === templateSignature) return;
        
        canvas.setAttribute('data-job-id', session.job_id);
        canvas.setAttribute('data-template', templateSignature);
        canvas.innerHTML = '';

        const template = session.template || [];
        const pageCount = Math.max(1, ...template.map(item => item.page || 1));
        for (let page = 1; page <= pageCount; page++) {
            canvas.appendChild(UI.createLayoutPage(page));
        }

        template.forEach(item => {
            const art = session.artifacts.find(a => a.id === item.id);
            if (!art) return;
            const page = item.page || 1;
            const pageEl = canvas.querySelector(`.a4-page[data-page="${page}"]`);
            if (!pageEl) return;
            
            const block = document.createElement('div');
            block.className = 'layout-block';
            block.id = `block-${item.id}`;
            block.dataset.page = page;
            block.style.left = (item.x * MM_TO_PX) + 'px';
            block.style.top = (item.y * MM_TO_PX) + 'px';
            block.style.width = (item.w * MM_TO_PX) + 'px';
            block.style.height = (item.h * MM_TO_PX) + 'px';
            block.innerHTML = `
                <span>${art.indicator}</span>
                <button class="del-btn" onclick="App.handleDeleteIndicatorById('${item.id}', event)">✕</button>
                <div class="resizer"></div>
            `;
            Utils.makeDraggable(block);
            pageEl.appendChild(block);
        });
    },

    createLayoutPage(page) {
        const pageEl = document.createElement('div');
        pageEl.className = 'a4-page';
        pageEl.dataset.page = page;
        pageEl.style.setProperty('--page-label', `"Fim da Página ${page}"`);
        return pageEl;
    },

    renderDataArtifacts(session) {
        let html = `
            <div style="margin-bottom: 2rem; background: var(--md-sys-color-primary-container); color: var(--md-sys-color-on-primary-container); padding: 1.5rem; border-radius: 16px;">
                <h4 style="margin-top:0">📊 Métricas Calculadas</h4>
                <pre style="font-family: 'Roboto Mono', monospace; font-size: 0.85rem;">${Utils.formatJson(session.calculated_metrics || {})}</pre>
            </div>
        `;
        html += (session.files || []).map(f => `
            <div style="margin-bottom: 1.5rem; border-bottom: 1px solid var(--md-sys-color-outline); padding-bottom: 1rem;">
                <h4 style="color: var(--md-sys-color-primary)">📄 ${f.original_filename}</h4>
                <pre style="font-size: 0.75rem; background: #f5f5f5; padding: 1rem; border-radius: 8px; overflow-x: auto;">${Utils.formatJson(f.artifact)}</pre>
            </div>
        `).join('');
        document.getElementById('data-artifacts-container').innerHTML = html;
    },

    renderFullScript(script) {
        const el = document.getElementById('full-script-content');
        if (!script) return;
        el.textContent = script;
        hljs.highlightElement(el);
    },

    updateSettingsForm(settings) {
        if (!settings) return;
        
        const titleEl = document.getElementById('s-title');
        // Only update if not currently focused to avoid interrupting user typing
        if (document.activeElement !== titleEl) titleEl.value = settings.title || "";
        
        document.getElementById('s-font').value = settings.font || "Arial";
        document.getElementById('s-font-size').value = settings.font_size || 10;
        document.getElementById('s-alignment').value = settings.alignment || "L";
        document.getElementById('s-line-height').value = settings.line_height || 5;
        document.getElementById('s-language').value = settings.language || "Portuguese";
        document.getElementById('s-layout-template').value = settings.selected_template_id || "";
    },

    renderTemplateOptions(templates) {
        const select = document.getElementById('s-layout-template');
        const currentValue = select.value;
        select.innerHTML = (templates || []).map(template => `
            <option value="${template.id}">${template.name}</option>
        `).join('');
        if (currentValue) select.value = currentValue;
    }
};
