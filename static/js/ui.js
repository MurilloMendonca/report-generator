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
        el.textContent = '';

        if (!sessions || sessions.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'recent-empty';
            empty.textContent = 'Nenhuma sessão recente.';
            el.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        sessions.forEach(session => {
            const updatedAt = session.updated_at ? new Date(session.updated_at).toLocaleString() : '';
            const prompt = session.initial_prompt || 'Sessão sem objetivo';
            const status = session.status || 'Sem status';
            const link = document.createElement('a');
            link.className = 'recent-session';
            link.href = `/session/${encodeURIComponent(session.job_id)}`;

            const title = document.createElement('div');
            title.className = 'recent-session-title';
            title.textContent = prompt;

            const meta = document.createElement('div');
            meta.className = 'recent-session-meta';
            meta.textContent = `${status} · ${session.file_count || 0} arquivo(s) · ${updatedAt}`;

            link.appendChild(title);
            link.appendChild(meta);
            fragment.appendChild(link);
        });
        el.appendChild(fragment);
    },

    updateStatus(status, progress) {
        document.getElementById('status-text').innerText = status;
        document.getElementById('progress-bar').style.width = progress + "%";
    },

    renderLogs(logs) {
        const container = document.getElementById('log-container');
        if (!container) return;
        container.textContent = '';
        const fragment = document.createDocumentFragment();
        (logs || []).slice(-5).forEach(line => {
            const item = document.createElement('div');
            item.textContent = line;
            fragment.appendChild(item);
        });
        container.appendChild(fragment);
    },

    renderMessageText(value) {
        return UI.escapeHtml(value).replace(/\n/g, '<br>');
    },

    renderRefinementChat(session) {
        const el = document.getElementById('chat-history');
        if (!el || !session) return;

        let messages = (session.refinement_messages || [])
            .filter(item => item && item.content)
            .map(item => ({
                role: item.role || 'assistant',
                content: item.content,
                kind: item.kind || 'message'
            }));

        if (!messages.length && session.refinement_history && session.refinement_history.length) {
            messages = session.refinement_history
                .filter(content => String(content || '').trim())
                .map(content => ({ role: 'user', content, kind: 'legacy' }));
        }

        const initialPrompt = session.initial_prompt || 'Relatório exploratório dos dados';
        const signature = JSON.stringify({
            initialPrompt,
            messages
        });
        if (el.dataset.signature === signature) return;

        const shouldStickToBottom = !el.dataset.signature || (el.scrollTop + el.clientHeight >= el.scrollHeight - 16);
        const allowedKinds = ['status', 'initial', 'error', 'legacy', 'clarification'];
        const fragment = document.createDocumentFragment();

        const appendMessage = (roleClass, kindClass, label, content, initial = false) => {
            const message = document.createElement('div');
            message.className = `message ${roleClass} ${kindClass}${initial ? ' initial' : ''}`;
            const strong = document.createElement('b');
            strong.textContent = `${label}:`;
            message.appendChild(strong);
            const body = document.createElement('span');
            body.textContent = content;
            body.style.whiteSpace = 'pre-wrap';
            if (initial) {
                message.appendChild(document.createElement('br'));
            } else {
                message.appendChild(document.createTextNode(' '));
            }
            message.appendChild(body);
            fragment.appendChild(message);
        };

        appendMessage('ai', 'initial', 'Objetivo inicial', initialPrompt, true);

        if (!messages.length) {
            appendMessage('ai', 'status', 'Studio', 'Peça uma mudança global ou ajuste fino para melhorar o relatório.');
        } else {
            messages.forEach(message => {
                const isUser = message.role === 'user';
                const roleClass = isUser ? 'user' : 'ai';
                const kindClass = allowedKinds.includes(message.kind) ? message.kind : 'message';
                const label = isUser ? 'Você' : 'Studio';
                appendMessage(roleClass, kindClass, label, message.content || '');
            });
        }

        el.appendChild(fragment);
        el.dataset.signature = signature;
        if (shouldStickToBottom) el.scrollTop = el.scrollHeight;
    },

    renderExploreChat(session, pending = null) {
        const chat = document.getElementById('explore-chat');
        if (!chat || !session) return;
        const history = Array.isArray(session.explore_history) ? session.explore_history : [];
        const busy = Boolean(pending) || history.some(item => item && item.role === 'assistant' && item.status === 'pending');
        const submit = document.getElementById('explore-submit');
        if (submit) {
            submit.disabled = busy;
            submit.textContent = busy ? 'Consultando...' : 'Consultar';
        }
        chat.setAttribute('aria-busy', busy ? 'true' : 'false');
        const signature = JSON.stringify([session.job_id, history, pending?.prompt || null]);
        if (chat.dataset.signature === signature) return;
        const stickToBottom = !chat.dataset.signature || chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 48;
        chat.textContent = '';
        const fragment = document.createDocumentFragment();
        const messages = [...history];
        if (pending && history.length <= (pending.historyLength ?? history.length)) messages.push(
            { role: 'user', content: pending.prompt },
            { role: 'assistant', content: 'Analisando os dados e preparando a resposta...', status: 'pending' }
        );
        if (!messages.length) {
            const empty = document.createElement('p');
            empty.className = 'explore-empty';
            empty.textContent = 'Faça uma pergunta sobre os dados ou peça um gráfico. Por exemplo: “Mostre um gráfico de barras com as 10 categorias mais frequentes”.';
            fragment.appendChild(empty);
        }
        let previousQuestion = '';
        messages.forEach(item => {
            const user = item.role === 'user';
            if (user) previousQuestion = String(item.content || '');
            const message = document.createElement('div');
            message.className = `message ${user ? 'user' : 'ai'} explorer-message${item.status === 'error' ? ' error' : ''}${item.status === 'pending' ? ' pending' : ''}`;
            const label = document.createElement('b');
            label.textContent = user ? 'Você:' : 'Analista:';
            message.appendChild(label);
            const content = document.createElement('div');
            content.className = 'explore-answer';
            content.textContent = String(item.content || '');
            message.appendChild(content);
            if (['missing_provider_key', 'invalid_provider_key'].includes(item.error?.code)) {
                const link = document.createElement('a');
                link.href = '/keys';
                link.textContent = 'Abrir Chaves';
                message.appendChild(link);
            }
            if (!user) (Array.isArray(item.charts) ? item.charts : []).forEach(chart => {
                if (!chart || typeof chart !== 'object') return;
                const url = API.explorerChartUrl(session.job_id, item.turn_id, chart.id);
                if (!url) return;
                const figure = document.createElement('figure');
                figure.className = 'explore-chart';
                const heading = document.createElement('h4');
                heading.textContent = String(chart.title || 'Gráfico');
                figure.appendChild(heading);
                const preview = document.createElement('a');
                preview.href = url;
                preview.target = '_blank';
                preview.rel = 'noopener noreferrer';
                preview.setAttribute('aria-label', `Abrir gráfico: ${chart.title || 'Gráfico'}`);
                const image = document.createElement('img');
                image.src = url;
                image.alt = String(chart.title || 'Gráfico da consulta');
                image.loading = 'lazy';
                image.decoding = 'async';
                if (Number.isFinite(chart.width) && Number.isFinite(chart.height)) {
                    image.width = chart.width;
                    image.height = chart.height;
                }
                preview.appendChild(image);
                figure.appendChild(preview);
                const unavailable = document.createElement('p');
                unavailable.className = 'explore-chart-unavailable';
                unavailable.textContent = 'Gráfico indisponível. Entre novamente ou repita a consulta para gerar outra imagem.';
                unavailable.hidden = true;
                figure.appendChild(unavailable);
                if (chart.caption) {
                    const caption = document.createElement('figcaption');
                    caption.textContent = String(chart.caption);
                    figure.appendChild(caption);
                }
                const download = document.createElement('a');
                download.className = 'explore-chart-download';
                download.href = API.explorerChartUrl(session.job_id, item.turn_id, chart.id, true);
                download.download = `grafico-${chart.id}.png`;
                download.textContent = 'Baixar PNG';
                const enlarge = document.createElement('a');
                enlarge.className = 'explore-chart-open';
                enlarge.href = url;
                enlarge.target = '_blank';
                enlarge.rel = 'noopener noreferrer';
                enlarge.textContent = 'Ampliar gráfico';
                figure.appendChild(enlarge);
                figure.appendChild(download);
                image.addEventListener('error', () => {
                    image.hidden = true;
                    unavailable.hidden = false;
                    preview.removeAttribute('href');
                    enlarge.removeAttribute('href');
                    download.removeAttribute('href');
                    enlarge.setAttribute('aria-disabled', 'true');
                    download.setAttribute('aria-disabled', 'true');
                });
                message.appendChild(figure);
            });
            if (item.status === 'error' && previousQuestion) {
                const retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'btn btn-secondary explore-retry';
                retry.dataset.action = 'explore-retry';
                retry.dataset.prompt = previousQuestion;
                retry.textContent = 'Tentar novamente';
                message.appendChild(retry);
            }
            fragment.appendChild(message);
        });
        chat.appendChild(fragment);
        chat.dataset.signature = signature;
        if (stickToBottom || pending) chat.scrollTop = chat.scrollHeight;
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

    clearElement(el) {
        if (!el) return;
        while (el.firstChild) el.removeChild(el.firstChild);
    },

    buildOption(value, label) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        return option;
    },

    renderFiles(files) {
        const tray = document.getElementById('file-tray');
        if (!tray) return;

        const list = files || [];
        const summary = document.getElementById('file-summary');
        if (summary) summary.textContent = `${list.length} arquivo${list.length === 1 ? '' : 's'}`;

        UI.clearElement(tray);
        if (!list.length) {
            const empty = document.createElement('div');
            empty.className = 'file-empty';
            const strong = document.createElement('strong');
            strong.textContent = 'Nenhum arquivo na sessão.';
            const span = document.createElement('span');
            span.textContent = 'Arraste arquivos para cá ou use o botão de adicionar.';
            empty.appendChild(strong);
            empty.appendChild(span);
            tray.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        list.forEach((f, index) => {
            const filename = f.original_filename || `arquivo-${index + 1}`;
            const kind = UI.fileKind(filename);
            const hasPreprocessed = Boolean(f.preprocessed_path || f.filtered_path);
            const size = UI.formatBytes(f.size_bytes || (f.artifact && f.artifact.size_bytes));
            const rows = f.artifact && f.artifact.shape && f.artifact.shape.rows;
            const metadata = f.artifact && !f.artifact.error
                ? (Number.isFinite(rows) ? `${rows.toLocaleString('pt-BR')} linhas` : f.artifact.pages ? `${f.artifact.pages} páginas` : 'analisado')
                : (f.artifact && f.artifact.error ? 'nao analisado' : 'aguardando analise');
            const card = document.createElement('article');
            card.className = 'file-card';
            card.dataset.fileIndex = index;

            const openBtn = document.createElement('button');
            openBtn.className = 'file-open';
            openBtn.type = 'button';
            openBtn.title = `Visualizar ${filename}`;
            openBtn.setAttribute('aria-label', `Visualizar ${filename}`);
            openBtn.dataset.action = 'preview-file';
            openBtn.dataset.fileIndex = index;

            const type = document.createElement('span');
            type.className = `file-type file-type-${kind}`;
            type.textContent = UI.fileIcon(kind);
            const title = document.createElement('span');
            title.className = 'file-title';
            const name = document.createElement('span');
            name.className = 'file-name';
            name.textContent = filename;
            title.appendChild(name);
            if (hasPreprocessed) {
                const badge = document.createElement('span');
                badge.className = 'file-state-badge';
                badge.textContent = 'Pré-processado';
                title.appendChild(badge);
            }
            openBtn.appendChild(type);
            openBtn.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'file-meta';
            const metaText = document.createElement('span');
            metaText.textContent = metadata;
            meta.appendChild(metaText);
            if (size) {
                const sizeEl = document.createElement('span');
                sizeEl.textContent = size;
                meta.appendChild(sizeEl);
            }

            const actions = document.createElement('div');
            actions.className = 'file-actions';
            [
                ['Abrir', 'preview-file'],
                ['Baixar', 'download-file'],
                ['Remover', 'delete-file'],
            ].forEach(([label, action]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = `file-action-btn${action === 'delete-file' ? ' danger' : ''}`;
                button.dataset.action = action;
                button.dataset.fileIndex = index;
                button.setAttribute('aria-label', `${label} ${filename}`);
                button.textContent = label;
                actions.appendChild(button);
            });

            card.appendChild(openBtn);
            card.appendChild(meta);
            card.appendChild(actions);
            fragment.appendChild(card);
        });
        tray.appendChild(fragment);
    },

    renderArtifacts(artifacts, onEdit, onDelete) {
        const container = document.getElementById('artifacts-visual');
        if (!container) return;
        UI.clearElement(container);
        const fragment = document.createDocumentFragment();
        (artifacts || []).forEach((art, i) => {
            const type = art.type || 'graphic';
            const colors = {
                metric: 'var(--md-sys-color-primary)',
                text: 'var(--md-sys-color-secondary)',
                graphic: 'var(--md-sys-color-tertiary, #3498db)'
            };
            const color = colors[type] || colors.graphic;

            const card = document.createElement('div');
            card.className = 'artifact-card';
            card.style.borderLeft = `6px solid ${color}`;

            const header = document.createElement('div');
            header.className = 'artifact-header';
            const name = document.createElement('b');
            name.style.color = 'var(--md-sys-color-on-surface)';
            name.textContent = art.indicator || '';
            header.appendChild(name);

            const actions = document.createElement('div');
            actions.className = 'artifact-actions';
            const badge = document.createElement('span');
            badge.style.cssText = `font-size: 0.65rem; background: ${color}; color: white; padding: 2px 8px; border-radius: 10px; margin-right: 8px; font-weight: bold;`;
            badge.textContent = type.toUpperCase();
            actions.appendChild(badge);

            const editBtn = document.createElement('button');
            editBtn.className = 'btn btn-outline';
            editBtn.style.padding = '4px 8px';
            editBtn.type = 'button';
            editBtn.dataset.action = 'edit-indicator';
            editBtn.dataset.index = i;
            editBtn.setAttribute('aria-label', `Editar componente ${art.indicator || ''}`);
            editBtn.textContent = '✏️';
            actions.appendChild(editBtn);

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'btn btn-danger';
            deleteBtn.style.padding = '4px 8px';
            deleteBtn.type = 'button';
            deleteBtn.dataset.action = 'delete-indicator';
            deleteBtn.dataset.index = i;
            deleteBtn.setAttribute('aria-label', `Excluir componente ${art.indicator || ''}`);
            deleteBtn.textContent = '🗑️';
            actions.appendChild(deleteBtn);

            header.appendChild(actions);
            card.appendChild(header);

            const id = document.createElement('div');
            id.style.fontSize = '0.75rem';
            id.style.color = 'var(--md-sys-color-outline)';
            id.textContent = `ID: ${art.id || ''}`;
            card.appendChild(id);

            if (art.component_preset_id || art.visual_theme_id || art.chart_style_id || art.render_mode) {
                const meta = document.createElement('div');
                meta.className = 'artifact-visual-meta';
                if (art.component_preset_id) {
                    const span = document.createElement('span');
                    span.textContent = art.component_preset_id;
                    meta.appendChild(span);
                }
                if (art.visual_theme_id) {
                    const span = document.createElement('span');
                    span.textContent = `Tema: ${art.visual_theme_id}`;
                    meta.appendChild(span);
                }
                if (art.chart_style_id) {
                    const span = document.createElement('span');
                    span.textContent = `Estilo: ${art.chart_style_id}`;
                    meta.appendChild(span);
                }
                if (art.render_mode) {
                    const span = document.createElement('span');
                    span.textContent = `Modo: ${art.render_mode}`;
                    meta.appendChild(span);
                }
                card.appendChild(meta);
            }

            if (Array.isArray(art.visual_warnings) && art.visual_warnings.length) {
                const warnings = document.createElement('div');
                warnings.className = 'artifact-visual-warnings';
                warnings.textContent = art.visual_warnings.slice(0, 2).join(' · ');
                card.appendChild(warnings);
            }

            const reason = document.createElement('div');
            reason.style.fontSize = '0.8rem';
            reason.style.color = 'var(--md-sys-color-on-surface-variant)';
            reason.style.marginTop = '0.5rem';
            reason.textContent = art.reason || '';
            card.appendChild(reason);

            fragment.appendChild(card);
        });
        container.appendChild(fragment);
    },

    renderLayoutCanvas(session) {
        App.renderReportLayout(session);
    },

    createLayoutPage(page) {
        const pageEl = document.createElement('div');
        pageEl.className = 'a4-page';
        pageEl.dataset.page = page;
        pageEl.style.setProperty('--page-label', `"Fim da Página ${page}"`);
        return pageEl;
    },

    renderDataArtifacts(session) {
        const container = document.getElementById('data-artifacts-container');
        if (!container) return;
        UI.clearElement(container);

        const metrics = document.createElement('div');
        metrics.style.marginBottom = '2rem';
        metrics.style.background = 'var(--md-sys-color-primary-container)';
        metrics.style.color = 'var(--md-sys-color-on-primary-container)';
        metrics.style.padding = '1.5rem';
        metrics.style.borderRadius = '16px';
        const metricsTitle = document.createElement('h4');
        metricsTitle.style.marginTop = '0';
        metricsTitle.textContent = '📊 Métricas Calculadas';
        const metricsPre = document.createElement('pre');
        metricsPre.style.fontFamily = "'Roboto Mono', monospace";
        metricsPre.style.fontSize = '0.85rem';
        metricsPre.textContent = Utils.formatJson(session.calculated_metrics || {});
        metrics.appendChild(metricsTitle);
        metrics.appendChild(metricsPre);
        container.appendChild(metrics);

        (session.files || []).forEach(f => {
            const section = document.createElement('div');
            section.style.marginBottom = '1.5rem';
            section.style.borderBottom = '1px solid var(--md-sys-color-outline)';
            section.style.paddingBottom = '1rem';
            const title = document.createElement('h4');
            title.style.color = 'var(--md-sys-color-primary)';
            title.textContent = `📄 ${f.original_filename || ''}`;
            section.appendChild(title);
            if (f.preprocessing_description || f.filter_description) {
                const desc = document.createElement('div');
                desc.style.fontSize = '0.78rem';
                desc.style.color = 'var(--md-sys-color-on-surface-variant)';
                desc.style.marginBottom = '0.5rem';
                desc.textContent = `Pré-processamento: ${f.preprocessing_description || f.filter_description}`;
                section.appendChild(desc);
            }
            const pre = document.createElement('pre');
            pre.style.fontSize = '0.75rem';
            pre.style.background = '#f5f5f5';
            pre.style.padding = '1rem';
            pre.style.borderRadius = '8px';
            pre.style.overflowX = 'auto';
            pre.textContent = Utils.formatJson(f.artifact);
            section.appendChild(pre);
            container.appendChild(section);
        });
    },

    renderFullScript(script) {
        const el = document.getElementById('full-script-content');
        if (!script) return;
        el.textContent = script;
        if (window.hljs && typeof window.hljs.highlightElement === 'function') {
            window.hljs.highlightElement(el);
        }
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
        const includeSamplesEl = document.getElementById('s-include-data-samples');
        if (includeSamplesEl) includeSamplesEl.checked = Boolean(settings.include_data_samples);
        document.getElementById('s-layout-template').value = settings.selected_template_id || "";
        const themeEl = document.getElementById('s-visual-theme');
        const styleEl = document.getElementById('s-chart-style');
        const visualInstructionsEl = document.getElementById('s-visual-instructions');
        if (styleEl) styleEl.value = settings.chart_style_id || "modern";
        if (themeEl) {
            themeEl.value = settings.visual_theme_id || "executive_clean";
            themeEl.dispatchEvent(new Event('change'));
        }
        if (visualInstructionsEl && document.activeElement !== visualInstructionsEl) {
            visualInstructionsEl.value = settings.visual_instructions || "";
        }
        UI.updateSelectedTemplatePreview(settings.selected_template_id || "");
    },

    renderVisualThemeOptions(library) {
        const themeSelect = document.getElementById('s-visual-theme');
        const styleSelect = document.getElementById('s-chart-style');
        const themePreview = document.getElementById('visual-theme-preview');
        const themeInspector = document.getElementById('visual-theme-inspector');
        if (!themeSelect || !styleSelect) return;

        const currentTheme = themeSelect.value;
        const currentStyle = styleSelect.value;
        const themes = library.themes || [];
        const styles = library.chart_styles || [];
        UI.clearElement(themeSelect);
        UI.clearElement(styleSelect);
        themes.forEach(theme => themeSelect.appendChild(UI.buildOption(theme.id, theme.name)));
        styles.forEach(style => styleSelect.appendChild(UI.buildOption(style.id, style.name)));
        themeSelect.value = currentTheme || library.default_theme_id || "executive_clean";
        styleSelect.value = currentStyle || library.default_chart_style_id || "modern";
        const renderPreview = () => {
            const theme = themes.find(item => item.id === themeSelect.value);
            const style = styles.find(item => item.id === styleSelect.value);
            const palette = theme && theme.palette ? theme.palette : [];
            if (themePreview) {
                UI.clearElement(themePreview);
                palette.forEach(color => {
                    const swatch = document.createElement('span');
                    swatch.className = 'palette-swatch';
                    swatch.style.background = color;
                    swatch.title = color;
                    themePreview.appendChild(swatch);
                });
            }
            if (themeInspector && theme) {
                UI.renderThemeInspector(themeInspector, theme, style, 'bar_ranked');
            }
        };
        themeSelect.onchange = renderPreview;
        styleSelect.onchange = renderPreview;
        renderPreview();
    },

    renderComponentLibraryOptions(library) {
        const themeSelect = document.getElementById('m-visual-theme');
        const styleSelect = document.getElementById('m-chart-style');
        if (!themeSelect || !styleSelect) return;

        const currentTheme = themeSelect.value;
        const currentStyle = styleSelect.value;
        UI.clearElement(themeSelect);
        UI.clearElement(styleSelect);
        themeSelect.appendChild(UI.buildOption('', 'Usar tema global'));
        styleSelect.appendChild(UI.buildOption('', 'Usar estilo global'));
        (library.themes || []).forEach(theme => themeSelect.appendChild(UI.buildOption(theme.id, theme.name)));
        (library.chart_styles || []).forEach(style => styleSelect.appendChild(UI.buildOption(style.id, style.name)));
        if (currentTheme) themeSelect.value = currentTheme;
        if (currentStyle) styleSelect.value = currentStyle;
    },

    previewUrl(presetId, themeId, styleId) {
        const params = new URLSearchParams({
            theme: themeId || 'executive_clean',
            style: styleId || 'modern'
        });
        return `/api/component-library/presets/${encodeURIComponent(presetId)}/preview?${params.toString()}`;
    },

    appendInfoList(container, title, items) {
        if (!items || !items.length) return;
        const group = document.createElement('div');
        group.className = 'visual-info-group';
        const label = document.createElement('span');
        label.className = 'visual-info-label';
        label.textContent = title;
        group.appendChild(label);
        const tags = document.createElement('div');
        tags.className = 'visual-info-tags';
        items.forEach(item => {
            const tag = document.createElement('span');
            tag.className = 'visual-info-tag';
            tag.textContent = item;
            tags.appendChild(tag);
        });
        group.appendChild(tags);
        container.appendChild(group);
    },

    renderPreviewCard(container, imageUrl, title, description) {
        const media = document.createElement('div');
        media.className = 'visual-preview-media';
        const image = document.createElement('img');
        image.src = imageUrl;
        image.alt = title;
        image.loading = 'lazy';
        media.appendChild(image);
        container.appendChild(media);

        const body = document.createElement('div');
        body.className = 'visual-preview-body';
        const heading = document.createElement('div');
        heading.className = 'visual-preview-heading';
        heading.textContent = title;
        body.appendChild(heading);
        if (description) {
            const text = document.createElement('p');
            text.textContent = description;
            body.appendChild(text);
        }
        container.appendChild(body);
        return body;
    },

    renderThemeInspector(container, theme, style, presetId = 'bar_ranked') {
        UI.clearElement(container);
        container.className = 'visual-choice-inspector';
        const card = document.createElement('div');
        card.className = 'visual-preview-card';
        const imageUrl = UI.previewUrl(presetId, theme.id, style && style.id);
        const body = UI.renderPreviewCard(card, imageUrl, `${theme.name} + ${(style && style.name) || 'Moderno'}`, theme.description);
        UI.appendInfoList(body, 'Bom para', theme.best_for || []);
        UI.appendInfoList(body, 'Evitar em', theme.avoid_when || []);
        if (theme.accessibility_notes) {
            const note = document.createElement('p');
            note.className = 'visual-accessibility-note';
            note.textContent = theme.accessibility_notes;
            body.appendChild(note);
        }
        container.appendChild(card);
    },

    renderPresetInspector(library) {
        const container = document.getElementById('preset-inspector');
        if (!container) return;
        UI.clearElement(container);

        const type = document.getElementById('m-type').value || 'graphic';
        const presetSelect = document.getElementById('m-preset');
        const themeSelect = document.getElementById('m-visual-theme');
        const styleSelect = document.getElementById('m-chart-style');
        const renderModeSelect = document.getElementById('m-render-mode');
        const dataBindingInput = document.getElementById('m-data-binding');
        const globalTheme = document.getElementById('s-visual-theme');
        const globalStyle = document.getElementById('s-chart-style');
        const allowedPresets = (library.presets || []).filter(p => (p.allowed_types || []).includes(type) || type === 'graphic');
        const selectedPresetId = presetSelect && presetSelect.value;
        const preset = (library.presets || []).find(item => item.id === selectedPresetId) || allowedPresets[0];
        if (!preset) {
            const empty = document.createElement('div');
            empty.className = 'visual-inspector-empty';
            empty.textContent = 'Nenhum preset visual disponível para este tipo.';
            container.appendChild(empty);
            return;
        }

        const themeId = (themeSelect && themeSelect.value) || (globalTheme && globalTheme.value) || library.default_theme_id || 'executive_clean';
        const styleId = (styleSelect && styleSelect.value) || (globalStyle && globalStyle.value) || library.default_chart_style_id || 'modern';
        const theme = (library.themes || []).find(item => item.id === themeId);
        const style = (library.chart_styles || []).find(item => item.id === styleId);

        const card = document.createElement('div');
        card.className = 'visual-preview-card preset-preview-card';
        const prefix = selectedPresetId ? '' : 'Automático: ';
        const body = UI.renderPreviewCard(
            card,
            UI.previewUrl(preset.id, themeId, styleId),
            `${prefix}${preset.name}`,
            preset.preview_description || preset.description
        );
        UI.appendInfoList(body, 'Usar para', preset.best_for || []);
        UI.appendInfoList(body, 'Evitar em', preset.avoid_when || []);
        UI.appendInfoList(body, 'Papéis exigidos', preset.required_roles || []);
        UI.appendInfoList(body, 'Opções', preset.common_options || []);
        if (theme || style) {
            const context = document.createElement('p');
            context.className = 'visual-context-note';
            context.textContent = `Tema efetivo: ${(theme && theme.name) || themeId}. Estilo: ${(style && style.name) || styleId}.`;
            body.appendChild(context);
        }
        if (renderModeSelect) {
            const mode = document.createElement('p');
            mode.className = 'visual-context-note';
            mode.textContent = `Modo de renderização: ${renderModeSelect.value || 'generated_code'}.`;
            body.appendChild(mode);
        }
        if (dataBindingInput && dataBindingInput.value.trim()) {
            try {
                const binding = JSON.parse(dataBindingInput.value);
                const roles = binding && binding.roles ? Object.entries(binding.roles).map(([role, column]) => `${role}: ${column}`) : [];
                UI.appendInfoList(body, 'Data binding', roles);
            } catch (error) {
                const warning = document.createElement('p');
                warning.className = 'visual-binding-warning';
                warning.textContent = 'Data binding ainda não é um JSON válido.';
                body.appendChild(warning);
            }
        }
        container.appendChild(card);
    },

    renderTemplateOptions(templates) {
        const select = document.getElementById('s-layout-template');
        const currentValue = select.value;
        UI.clearElement(select);
        (templates || []).forEach(template => select.appendChild(UI.buildOption(template.id, template.name)));
        if (currentValue) select.value = currentValue;
        UI.renderTemplatePreviews(templates, select.value);
        select.onchange = () => UI.updateSelectedTemplatePreview(select.value);
    },

    renderTemplatePreviews(templates, selectedId) {
        const container = document.getElementById('layout-template-previews');
        if (!container) return;

        const list = templates || [];
        if (!list.length) {
            UI.clearElement(container);
            const empty = document.createElement('div');
            empty.className = 'template-preview-empty';
            empty.textContent = 'Nenhum template disponível.';
            container.appendChild(empty);
            return;
        }

        UI.clearElement(container);
        const legend = document.createElement('div');
        legend.className = 'template-preview-legend';
        legend.setAttribute('aria-hidden', 'true');
        const legendText = ['Texto', 'Gráfico'];
        legendText.forEach((label, index) => {
            const span = document.createElement('span');
            const swatch = document.createElement('i');
            swatch.className = `template-preview-swatch ${index === 0 ? 'text' : 'graphic'}`;
            span.appendChild(swatch);
            span.appendChild(document.createTextNode(label));
            legend.appendChild(span);
        });
        container.appendChild(legend);
        list.forEach(template => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `template-preview-card${template.id === selectedId ? ' selected' : ''}`;
            button.dataset.templateId = template.id;
            button.dataset.action = 'select-template-preview';
            button.title = template.description || template.name;
            button.setAttribute('aria-label', `Selecionar template ${template.name}`);
            button.appendChild(UI.templatePreviewSvgElement(template));
            const name = document.createElement('span');
            name.className = 'template-preview-name';
            name.textContent = template.name;
            button.appendChild(name);
            container.appendChild(button);
        });
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
        const svg = UI.templatePreviewSvgElement(template);
        return svg.outerHTML;
    },

    templatePreviewSvgElement(template) {
        const slots = template.slots || [];
        const pages = slots.map(slot => Number(slot.page || 1)).filter(Boolean);
        const page = pages.length ? Math.min(...pages) : 1;
        const pageSlots = slots.filter(slot => Number(slot.page || 1) === page);
        const width = 74;
        const height = 104;
        const scaleX = width / 210;
        const scaleY = height / 297;
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', 'template-preview-svg');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-hidden', 'true');

        const pageRect = document.createElementNS(svgNS, 'rect');
        pageRect.setAttribute('class', 'template-preview-page');
        pageRect.setAttribute('x', '0.5');
        pageRect.setAttribute('y', '0.5');
        pageRect.setAttribute('width', `${width - 1}`);
        pageRect.setAttribute('height', `${height - 1}`);
        pageRect.setAttribute('rx', '2');
        svg.appendChild(pageRect);

        pageSlots.forEach(slot => {
            const type = slot.type === 'text' ? 'text' : 'graphic';
            const x = Math.max(0, Math.min(width, Number(slot.x || 0) * scaleX));
            const y = Math.max(0, Math.min(height, Number(slot.y || 0) * scaleY));
            const w = Math.max(2, Math.min(width - x, Number(slot.w || 10) * scaleX));
            const h = Math.max(2, Math.min(height - y, Number(slot.h || 10) * scaleY));
            const rect = document.createElementNS(svgNS, 'rect');
            rect.setAttribute('class', `template-preview-${type}`);
            rect.setAttribute('x', x.toFixed(1));
            rect.setAttribute('y', y.toFixed(1));
            rect.setAttribute('width', w.toFixed(1));
            rect.setAttribute('height', h.toFixed(1));
            rect.setAttribute('rx', '1.5');
            svg.appendChild(rect);
        });

        if (new Set(slots.map(slot => Number(slot.page || 1))).size > 1) {
            const count = document.createElementNS(svgNS, 'text');
            count.setAttribute('x', '67');
            count.setAttribute('y', '99');
            count.setAttribute('text-anchor', 'end');
            count.setAttribute('class', 'template-preview-page-count');
            count.textContent = '+p';
            svg.appendChild(count);
        }

        return svg;
    }
};
