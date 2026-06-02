# Plano: Biblioteca de Componentes Visuais e Temas

## Avaliação

Sim, é uma boa ideia para este projeto. Hoje o sistema já tem bons contratos para geração de componentes, preview individual e algumas configurações visuais globais (`chart_palette` e `visual_instructions`), mas cada gráfico ainda é quase sempre inventado do zero pelo LLM. Isso gera inconsistência visual, aumenta a chance de gráficos ruins e dificulta forçar tabelas, scatter, linha, radial e barras bem construídos.

A biblioteca deve funcionar como uma camada de contratos reutilizáveis: cada tipo de componente define papéis de dados, regras de agregação, padrões de layout, opções permitidas e instruções específicas para o prompt. Os temas devem ser uma camada separada, aplicada globalmente nas configurações e opcionalmente sobrescrita no modal de edição de componente.

## Objetivos

- Criar uma biblioteca versionada de presets para gráficos e tabelas.
- Tornar tabelas um tipo visual de primeira classe, não um caso raro gerado por sorte.
- Permitir tema visual global nas configurações do relatório.
- Permitir override de tema, estilo e preset por componente no modal de edição.
- Reduzir código visual improvisado pelo LLM usando contratos e helpers compartilhados.
- Manter compatibilidade com sessões antigas e artefatos que só possuem `type`, `sub_type`, `columns` e `reason`.
- Melhorar testes para garantir que prompts, validação e regeneração respeitem presets e temas.

## Não Objetivos

- Substituir o LLM por um construtor visual completo.
- Criar gráficos interativos no PDF.
- Criar um editor de propriedades exaustivo com dezenas de controles.
- Refatorar todo o renderer de uma vez.
- Depender de chamadas reais a provedores LLM em testes automatizados.

## Estado Atual Relevante

- `prompts/GENERATE_ARTIFACTS.md` pede componentes `metric`, `graphic` e `text`, com `sub_type` opcional para `bar`, `line`, `pie`, `scatter`, `table`.
- `prompts/GENERATE_COMPONENT_CODE.md` já força matplotlib/fpdf2, tipografia padrão, renderização de tabelas como imagem e regras de segurança de layout.
- `app/services/llm_service.py` já injeta `chart_palette`, `visual_instructions`, constantes de tipografia e `sns.set_theme(style='whitegrid')`.
- `app/main.py` já detecta refinamento visual global por texto livre e atualiza `settings.visual_instructions` e `settings.chart_palette`.
- O modal em `static/index.html` e `static/js/app.js` só edita nome, tipo, colunas, razão e código Python.
- O endpoint de preview de componente já existe em `/api/sessions/{job_id}/components/{component_id}/preview`.

## Modelo de Dados Proposto

### Settings globais

Adicionar campos opcionais em `ReportSettings`:

```json
{
  "visual_theme_id": "executive_clean",
  "chart_style_id": "modern",
  "chart_palette": ["#2563eb", "#16a34a", "#f59e0b"],
  "visual_instructions": ""
}
```

Regras:

- `visual_theme_id` controla paleta, fundo, grid, bordas e estilo de tabela.
- `chart_style_id` controla linguagem gráfica: `modern`, `minimal`, `editorial`, `dense_dashboard`.
- `chart_palette` continua existindo para compatibilidade e override manual.
- `visual_instructions` continua existindo para refinamentos por texto livre.

### Artefato visual

Adicionar campos opcionais aos artefatos:

```json
{
  "id": "g_revenue_by_region",
  "indicator": "Receita por região",
  "type": "graphic",
  "sub_type": "bar",
  "component_preset_id": "bar_ranked",
  "visual_theme_id": null,
  "chart_style_id": null,
  "visual_options": {
    "orientation": "horizontal",
    "top_n": 10,
    "show_values": true
  },
  "columns": ["sales.csv:region", "sales.csv:revenue"],
  "reason": "Comparar regiões por receita total."
}
```

Regras:

- `component_preset_id` define a estrutura visual principal.
- `visual_theme_id: null` significa usar o tema global.
- `chart_style_id: null` significa usar o estilo global.
- `visual_options` deve ser pequeno e validado por preset.
- Sessões antigas sem esses campos continuam válidas.

## Biblioteca de Presets

Criar `app/utils/component_library.py`.

Estrutura sugerida:

```python
COMPONENT_PRESETS = {
    "bar_ranked": {
        "name": "Barras ranqueadas",
        "family": "bar",
        "allowed_types": ["graphic", "bar"],
        "description": "Compara categorias ordenadas por uma métrica agregada.",
        "data_roles": [
            {"name": "category", "kind": "categorical", "required": True},
            {"name": "value", "kind": "numeric", "required": True}
        ],
        "default_options": {
            "orientation": "horizontal",
            "top_n": 10,
            "show_values": True
        },
        "compact_guidance": "Use no máximo 6 categorias e omita rótulos longos.",
        "prompt_contract": "Aggregate by category, sort descending, keep top_n and group the rest as Others."
    }
}
```

Presets MVP:

- `bar_ranked`: comparação categórica ordenada.
- `bar_grouped`: comparação de categoria por série.
- `line_timeseries`: tendência temporal com datas ou períodos.
- `scatter_relationship`: relação entre duas métricas, cor opcional por categoria.
- `radial_donut`: participação percentual com poucas categorias.
- `radial_gauge`: progresso contra meta quando houver numerador e denominador.
- `table_summary`: tabela agregada curta, ordenada por métrica.
- `table_detail`: tabela de linhas selecionadas/top N.

Presets pós-MVP:

- `heatmap_matrix`: matriz categoria x categoria.
- `histogram_distribution`: distribuição de métrica numérica.
- `combo_bar_line`: barras com linha secundária.
- `small_multiples`: painéis repetidos por categoria.

## Biblioteca de Temas

Criar `VISUAL_THEMES` no mesmo módulo ou em `app/utils/visual_theme_library.py`.

Temas MVP:

- `executive_clean`: branco, azul forte, grid discreto, ideal para relatórios executivos.
- `finance_dark`: fundo claro com tons sóbrios, verde/vermelho controlados para variações.
- `warm_editorial`: paleta editorial com contraste moderado.
- `high_contrast`: acessível, alto contraste, bom para impressão.
- `minimal_mono`: poucos tons, foco em tabela e linha.

Cada tema deve conter:

```python
{
    "id": "executive_clean",
    "name": "Executivo limpo",
    "palette": ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed"],
    "seaborn_style": "whitegrid",
    "figure_facecolor": "#ffffff",
    "axes_facecolor": "#ffffff",
    "grid_color": "#e5e7eb",
    "text_color": "#111827",
    "muted_text_color": "#6b7280",
    "table": {
        "header_bg": "#111827",
        "header_text": "#ffffff",
        "row_bg": "#ffffff",
        "alt_row_bg": "#f9fafb",
        "border": "#d1d5db"
    }
}
```

## Backend

### 1. Biblioteca e endpoints

Adicionar:

- `app/utils/component_library.py`
- `GET /api/component-library`
- `GET /api/visual-themes`

Resposta de `/api/component-library`:

```json
{
  "presets": [...],
  "themes": [...],
  "chart_styles": [...]
}
```

Pode ser um único endpoint para reduzir chamadas do frontend.

### 2. Schema de sessão

Atualizar `app/models/session.py`:

- `ReportSettings.visual_theme_id: str | None = "executive_clean"`
- `ReportSettings.chart_style_id: str | None = "modern"`
- manter `extra="allow"` para compatibilidade.

Não é necessário criar um modelo Pydantic completo para artefato neste primeiro passo, mas a validação deve normalizar campos visuais.

### 3. Validação de artefatos

Atualizar `app/utils/artifact_validation.py`:

- Incluir `radial`, `donut` e `gauge` nos tipos/subtipos visuais aceitos.
- Validar se `component_preset_id` existe.
- Se `component_preset_id` estiver ausente, inferir por `type`/`sub_type`.
- Normalizar `visual_options` usando `default_options` do preset.
- Se o preset exigir coluna numérica/categórica, emitir warning quando metadados indicarem incompatibilidade.
- Reforçar troca de `pie/donut/radial_donut` para `bar_ranked` quando cardinalidade for alta.

### 4. Geração de artefatos

Atualizar `prompts/GENERATE_ARTIFACTS.md`:

- Expor lista resumida de presets disponíveis.
- Pedir explicitamente `component_preset_id` para componentes visuais.
- Instruir o LLM a usar `table_summary` quando o insight exigir valores exatos, ranking curto ou comparação tabular.
- Instruir `radial_donut` apenas para poucas categorias e `radial_gauge` apenas para progresso contra meta.

Atualizar `generate_artifacts(...)` para incluir o catálogo no prompt.

### 5. Geração de código de componente

Atualizar `generate_component_code(...)`:

- Resolver preset efetivo antes do prompt.
- Resolver tema efetivo: override do artefato ou tema global.
- Resolver estilo efetivo: override do artefato ou estilo global.
- Incluir no prompt:
  - `Component Preset Contract`
  - `Resolved Visual Theme`
  - `Resolved Chart Style`
  - `Resolved Visual Options`

Exemplo de contexto adicional:

```text
Component Preset Contract:
{
  "id": "bar_ranked",
  "prompt_contract": "Aggregate by category, sort descending...",
  "default_options": {...},
  "compact_guidance": "..."
}
```

### 6. Helpers no script final

Atualizar `assemble_script(...)` para injetar helpers que todos os componentes podem usar:

- `_resolved_chart_theme()`
- `_apply_chart_theme(fig, ax, theme=None)`
- `_chart_colors(n)`
- `_top_n_with_others(df, category_col, value_col, top_n, others_label)`
- `_save_chart_to_pdf(fig, pdf, x, y, w, h)`
- `_render_table_image(pdf, dataframe, x, y, w, h, title=None, theme=None)`
- `_format_compact_number(value)`

Esses helpers reduzem repetição e deixam mudanças de tema mais baratas. O prompt deve recomendar os helpers, mas manter compatibilidade com código antigo que não os usa.

### 7. Regeneração e invalidação

Regras sugeridas:

- Mudança de `visual_theme_id` global: reexecutar `run_script_generation`, sem limpar componentes, quando o código usa helpers/constantes globais.
- Mudança de `chart_palette`: reexecutar `run_script_generation`.
- Mudança de `chart_style_id` global: no MVP, limpar componentes visuais e rodar `run_pipeline`, porque alguns estilos podem exigir escolhas de código diferentes.
- Mudança de `component_preset_id` de um componente: limpar apenas aquele componente e regenerar.
- Mudança de `visual_options`: limpar apenas aquele componente.
- Mudança de tema override por componente: tentar re-render se só afetar tema; regenerar componente se afetar estilo/preset.

## Frontend

### Configurações globais

Adicionar à aba `Configurações`:

- Select de `Tema visual`.
- Select de `Estilo de gráfico`.
- Preview pequeno de paleta com swatches.
- Campo avançado opcional para `visual_instructions`, mantendo compatibilidade com refinamento por texto.

Atualizar:

- `static/index.html`
- `static/js/api.js`
- `static/js/app.js`
- `static/js/ui.js`
- `static/css/styles.css`

`App.collectSettings()` deve incluir:

```js
visual_theme_id: document.getElementById('s-visual-theme').value,
chart_style_id: document.getElementById('s-chart-style').value,
visual_instructions: document.getElementById('s-visual-instructions').value
```

### Modal de componente

Adicionar campos no modal:

- Tipo visual: incluir `scatter`, `table`, `radial`, `donut`, `gauge`.
- Preset do componente: filtrado pelo tipo visual.
- Tema: `Usar tema global` + temas disponíveis.
- Estilo: `Usar estilo global` + estilos disponíveis.
- Opções compactas:
  - Top N.
  - Orientação.
  - Mostrar valores.
  - Agregar outros.

No MVP, opções avançadas podem ser um pequeno JSON em `visual_options`, mas os campos mais comuns devem virar controles simples.

### Lista de componentes

Mostrar no card:

- Tipo/subtipo.
- Preset efetivo.
- Tema override, quando existir.

Isso ajuda a entender por que dois gráficos parecidos renderizam diferente.

## Prompts

### `GENERATE_ARTIFACTS.md`

Adicionar seção:

- `Available Visual Component Presets`
- Regras de escolha de preset.
- Preferência explícita por tabelas quando a informação precisa ser lida com valores exatos.

### `GENERATE_COMPONENT_CODE.md`

Adicionar seção:

- `Preset Compliance`
- `Theme Compliance`
- `Use Shared Helpers When Available`

Exigir:

- O gráfico deve seguir `component_preset_id`.
- Não criar paleta própria se `CHART_PALETTE` ou tema resolvido existir.
- Tabelas devem usar `_render_table_image(...)` quando possível.
- Radial/gauge deve evitar excesso de categorias e sempre ter texto central legível.

## Testes

### Unitários

Adicionar testes para:

- Catálogo de presets e temas tem IDs únicos.
- Todos os presets têm `family`, `data_roles`, `default_options` e `prompt_contract`.
- `validate_report_plan` normaliza `component_preset_id`.
- `validate_report_plan` rejeita ou corrige preset incompatível.
- `ReportSettings` aceita novos campos e migra sessões antigas.
- `/api/component-library` retorna presets, temas e estilos.
- `generate_component_code` inclui preset, tema e opções no prompt, usando mock de `call_llm`.
- `assemble_script` injeta tema e helpers.
- Alterar preset no endpoint de artefatos invalida só o componente afetado.
- Alterar tema global chama re-render sem apagar componentes quando possível.

### Testes canônicos

Atualizar `tests/canonical/playbook.json` com pelo menos um caso que peça:

- Uma tabela resumida.
- Um scatter.
- Um gráfico radial/donut.
- Um tema específico.

Adicionar checks em `tests/canonical/run_canonical_sessions.py` para:

- Confirmar presença de `type: table` ou `component_preset_id: table_summary`.
- Confirmar que o script usa `CHART_PALETTE`/tema resolvido.
- Confirmar que componentes renderizam sem falhas no manifest.

## Plano de Implementação por Fases

### Fase 1: Catálogo e schema

- Criar `app/utils/component_library.py`.
- Adicionar presets MVP e temas MVP.
- Adicionar endpoint `/api/component-library`.
- Adicionar campos em `ReportSettings`.
- Atualizar `DEFAULT_SETTINGS`.
- Criar testes unitários do catálogo e schema.

Resultado esperado: frontend e backend conseguem listar presets/temas, sem mudar ainda a geração.

### Fase 2: Validação e prompts

- Atualizar `GENERATE_ARTIFACTS.md` para pedir `component_preset_id`.
- Atualizar `generate_artifacts(...)` para enviar resumo da biblioteca.
- Atualizar `artifact_validation.py` para normalizar presets e opções.
- Atualizar `GENERATE_COMPONENT_CODE.md` e `generate_component_code(...)`.
- Criar testes com mock para garantir que o prompt recebe preset/tema/opções.

Resultado esperado: novos relatórios já passam a carregar intenção visual estruturada nos artefatos.

### Fase 3: Renderer helpers e temas reais

- Atualizar `assemble_script(...)` com `CHART_THEME`, `CHART_STYLE` e helpers compartilhados.
- Aplicar tema em rcParams, seaborn e tabela.
- Manter fallback para `chart_palette`.
- Adicionar testes de script gerado.

Resultado esperado: mudar tema global afeta a renderização sem depender totalmente do LLM.

### Fase 4: UI global e modal

- Carregar catálogo no `App.init()`.
- Renderizar selects de tema/estilo na aba de configurações.
- Renderizar seletor de preset/tema/estilo no modal.
- Atualizar `currentIndicatorPayload()` para persistir campos visuais.
- Mostrar preset/tema no card do componente.
- Ajustar CSS do modal para manter preview e campos organizados.

Resultado esperado: usuário consegue escolher estilo geral e override por componente sem escrever prompt.

### Fase 5: Regeneração seletiva

- Atualizar endpoint de settings para distinguir re-render, regeneração de gráficos e regeneração total.
- Atualizar endpoint de artefatos para invalidar componente quando preset/opções mudarem.
- Garantir que `handleRegenerateCurrentIndicator()` preserva override visual.
- Adicionar testes de invalidação.

Resultado esperado: edição visual fica rápida e previsível.

### Fase 6: Cobertura canônica e polimento

- Adicionar casos canônicos com tabela, scatter e radial.
- Verificar PDFs gerados e manifests.
- Ajustar prompts se tabelas ainda aparecerem pouco.
- Documentar presets no README ou em uma página simples no app.

Resultado esperado: biblioteca vira parte confiável do fluxo de geração.

## Ordem Recomendada para o MVP

1. Catálogo + endpoint.
2. Campos de settings e artefatos.
3. Prompt de planejamento gerando `component_preset_id`.
4. Validação normalizando presets.
5. Helpers de tema e tabela no script.
6. UI de tema global.
7. UI de preset no modal.
8. Testes unitários e um caso canônico com tabela.

## Decisões de Design

- Separar tema de preset. O mesmo `bar_ranked` deve funcionar em vários temas.
- Separar estilo de paleta. Um tema define cores; um estilo define densidade, grid, marcadores e bordas.
- Presets devem ser declarativos. O LLM ainda escreve o código, mas recebe um contrato mais estreito.
- Tabelas devem ser incentivadas no planejamento, não apenas no prompt de componente.
- Mudanças globais de cor devem ser re-render, não regeneração com LLM, sempre que possível.
- Mudanças de preset devem regenerar código do componente, porque mudam a estrutura visual.

## Riscos

- O LLM pode ignorar presets se o contrato for longo demais. Mitigação: enviar apenas o preset resolvido para cada componente.
- Temas podem não afetar código antigo que hardcodou cores. Mitigação: prompts novos + helpers + regeneração de gráficos.
- Muitas opções no modal podem deixar a edição pesada. Mitigação: começar com poucos controles e JSON avançado opcional.
- Radial/gauge pode ser usado em dados inadequados. Mitigação: validação por cardinalidade e papéis de dados.
- Tabelas podem estourar layout. Mitigação: `table_summary` com limite de linhas/colunas e fonte dinâmica.

## Critério de Pronto

- Um novo relatório pode gerar pelo menos barra, linha, scatter, radial/donut e tabela usando presets.
- O usuário consegue escolher um tema global nas configurações e ver efeito no PDF.
- O usuário consegue alterar o preset/tema de um componente no modal e regenerar só aquele componente.
- Tabelas aparecem quando o objetivo pede ranking, comparação exata ou resumo tabular.
- Testes unitários cobrem catálogo, schema, validação, prompt e script gerado.
- Pelo menos um teste canônico confirma tabela e tema em um PDF renderizado.
