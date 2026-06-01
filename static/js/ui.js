const UI = {
    escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    renderRecentSessions(sessions) {
        const el = document.getElementById('recent-sessions');
        if (!el) return;

        if (!sessions || sessions.length === 0) {
            el.innerHTML = '<div class="recent-empty">Nenhuma sessão recente.</div>';
            return;
        }

        el.innerHTML = sessions.map(session => {
            const updatedAt = session.updated_at ? new Date(session.updated_at).toLocaleString() : '';
            const prompt = session.initial_prompt || 'Sessão sem objetivo';
            const status = session.status || 'Sem status';
            return `
                <a class="recent-session" href="/session/${encodeURIComponent(session.job_id)}">
                    <div class="recent-session-title">${UI.escapeHtml(prompt)}</div>
                    <div class="recent-session-meta">
                        ${UI.escapeHtml(status)} · ${session.file_count || 0} arquivo(s) · ${UI.escapeHtml(updatedAt)}
                    </div>
                </a>
            `;
        }).join('');
    },

    updateStatus(status, progress) {
        document.getElementById('status-text').innerText = status;
        document.getElementById('progress-bar').style.width = progress + "%";
    },

    renderLogs(logs) {
        document.getElementById('log-container').innerHTML = (logs || []).slice(-5).map(l => `<div>${l}</div>`).join('');
    },

    fileKind(filename) {
        const ext = String(filename || '').split('.').pop().toLowerCase();
        if (['csv', 'xls', 'xlsx', 'ods'].includes(ext)) return 'data';
        if (['pdf'].includes(ext)) return 'pdf';
        if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'image';
        if (['txt', 'md', 'json', 'py', 'js', 'css', 'html', 'log'].includes(ext)) return 'text';
        if (['doc', 'docx', 'ppt', 'pptx'].includes(ext)) return 'doc';
        return 'file';
    },

    fileIcon(kind) {
        return {
            data: 'DB',
            pdf: 'PDF',
            image: 'IMG',
            text: 'TXT',
            doc: 'DOC',
            file: 'FILE'
        }[kind] || 'FILE';
    },

    formatBytes(bytes) {
        const value = Number(bytes);
        if (!value) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = value;
        let unit = 0;
        while (size >= 1024 && unit < units.length - 1) {
            size /= 1024;
            unit += 1;
        }
        return `${size.toFixed(size >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
    },

    renderFiles(files) {
        const tray = document.getElementById('file-tray');
        if (!tray) return;

        const list = files || [];
        const summary = document.getElementById('file-summary');
        if (summary) summary.textContent = `${list.length} arquivo${list.length === 1 ? '' : 's'}`;

        if (!list.length) {
            tray.innerHTML = `
                <div class="file-empty">
                    <strong>Nenhum arquivo na sessão.</strong>
                    <span>Arraste arquivos para cá ou use o botão de adicionar.</span>
                </div>
            `;
            return;
        }

        tray.innerHTML = list.map((f, index) => {
            const filename = f.original_filename || `arquivo-${index + 1}`;
            const kind = UI.fileKind(filename);
            const hasPreprocessed = Boolean(f.preprocessed_path || f.filtered_path);
            const size = UI.formatBytes(f.size_bytes || (f.artifact && f.artifact.size_bytes));
            const metadata = f.artifact && !f.artifact.error
                ? `${f.artifact.rows || f.artifact.pages || ''}${f.artifact.rows ? ' linhas' : f.artifact.pages ? ' paginas' : ''}`
                : (f.artifact && f.artifact.error ? 'nao analisado' : 'aguardando analise');
            const processLabel = hasPreprocessed
                ? `<span class="file-state-badge">Pré-processado</span>`
                : '';
            return `
                <article class="file-card" data-file-index="${index}">
                    <button class="file-open" type="button" title="Visualizar ${UI.escapeHtml(filename)}" onclick="App.previewFile(${index})">
                        <span class="file-type file-type-${kind}">${UI.fileIcon(kind)}</span>
                        <span class="file-title">
                            <span class="file-name">${UI.escapeHtml(filename)}</span>
                            ${processLabel}
                        </span>
                    </button>
                    <div class="file-meta">
                        <span>${UI.escapeHtml(metadata)}</span>
                        ${size ? `<span>${UI.escapeHtml(size)}</span>` : ''}
                    </div>
                    <div class="file-actions">
                        <button type="button" class="file-action-btn" onclick="App.previewFile(${index})">Abrir</button>
                        <button type="button" class="file-action-btn" onclick="App.downloadFile(${index})">Baixar</button>
                        <button type="button" class="file-action-btn danger" onclick="App.deleteFile(${index})">Remover</button>
                    </div>
                </article>
            `;
        }).join('');
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
                ${f.preprocessing_description || f.filter_description ? `<div style="font-size: 0.78rem; color: var(--md-sys-color-on-surface-variant); margin-bottom: 0.5rem;">Pré-processamento: ${UI.escapeHtml(f.preprocessing_description || f.filter_description)}</div>` : ''}
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

    updateSettingsForm(settings, session) {
        if (!settings) return;
        
        const titleEl = document.getElementById('s-title');
        // Only update if not currently focused to avoid interrupting user typing
        if (document.activeElement !== titleEl) titleEl.value = settings.title || "";

        const providerEl = document.getElementById('s-provider');
        const modelEl = document.getElementById('s-model-name');
        if (providerEl && session && document.activeElement !== providerEl) {
            providerEl.value = session.provider || "deepseek";
        }
        if (modelEl && session && session.model_name && document.activeElement !== modelEl) {
            modelEl.value = session.model_name;
        }
        
        document.getElementById('s-font').value = settings.font || "Arial";
        document.getElementById('s-font-size').value = settings.font_size || 10;
        document.getElementById('s-alignment').value = settings.alignment || "J";
        document.getElementById('s-line-height').value = settings.line_height || 5;
        document.getElementById('s-language').value = settings.language || "Portuguese";
        document.getElementById('s-layout-template').value = settings.selected_template_id || "";
        UI.updateSelectedTemplatePreview(settings.selected_template_id || "");
    },

    renderTemplateOptions(templates) {
        const select = document.getElementById('s-layout-template');
        const currentValue = select.value;
        select.innerHTML = (templates || []).map(template => `
            <option value="${template.id}">${template.name}</option>
        `).join('');
        if (currentValue) select.value = currentValue;
        UI.renderTemplatePreviews(templates, select.value);
        select.onchange = () => UI.updateSelectedTemplatePreview(select.value);
    },

    renderTemplatePreviews(templates, selectedId) {
        const container = document.getElementById('layout-template-previews');
        if (!container) return;

        const list = templates || [];
        if (!list.length) {
            container.innerHTML = '<div class="template-preview-empty">Nenhum template disponível.</div>';
            return;
        }

        container.innerHTML = `
            <div class="template-preview-legend" aria-hidden="true">
                <span><i class="template-preview-swatch text"></i>Texto</span>
                <span><i class="template-preview-swatch graphic"></i>Gráfico</span>
            </div>
        ` + list.map(template => `
            <button
                type="button"
                class="template-preview-card${template.id === selectedId ? ' selected' : ''}"
                data-template-id="${UI.escapeHtml(template.id)}"
                onclick="UI.selectTemplatePreview('${UI.escapeHtml(template.id)}')"
                title="${UI.escapeHtml(template.description || template.name)}"
            >
                ${UI.templatePreviewSvg(template)}
                <span class="template-preview-name">${UI.escapeHtml(template.name)}</span>
            </button>
        `).join('');
    },

    selectTemplatePreview(templateId) {
        const select = document.getElementById('s-layout-template');
        if (!select) return;
        select.value = templateId;
        UI.updateSelectedTemplatePreview(templateId);
        select.dispatchEvent(new Event('change', { bubbles: true }));
    },

    updateSelectedTemplatePreview(templateId) {
        document.querySelectorAll('.template-preview-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.templateId === templateId);
        });
    },

    templatePreviewSvg(template) {
        const slots = template.slots || [];
        const pages = slots.map(slot => Number(slot.page || 1)).filter(Boolean);
        const page = pages.length ? Math.min(...pages) : 1;
        const pageSlots = slots.filter(slot => Number(slot.page || 1) === page);
        const width = 74;
        const height = 104;
        const scaleX = width / 210;
        const scaleY = height / 297;
        const blocks = pageSlots.map(slot => {
            const type = slot.type === 'text' ? 'text' : 'graphic';
            const x = Math.max(0, Math.min(width, Number(slot.x || 0) * scaleX));
            const y = Math.max(0, Math.min(height, Number(slot.y || 0) * scaleY));
            const w = Math.max(2, Math.min(width - x, Number(slot.w || 10) * scaleX));
            const h = Math.max(2, Math.min(height - y, Number(slot.h || 10) * scaleY));
            return `<rect class="template-preview-${type}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5"></rect>`;
        }).join('');
        const extraPages = new Set(slots.map(slot => Number(slot.page || 1))).size > 1
            ? '<text x="67" y="99" text-anchor="end" class="template-preview-page-count">+p</text>'
            : '';

        return `
            <svg class="template-preview-svg" viewBox="0 0 ${width} ${height}" role="img" aria-hidden="true">
                <rect class="template-preview-page" x="0.5" y="0.5" width="${width - 1}" height="${height - 1}" rx="2"></rect>
                ${blocks}
                ${extraPages}
            </svg>
        `;
    }
};
