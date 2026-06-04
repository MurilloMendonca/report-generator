# Plano: Biblioteca de Componentes Visuais e Temas

## Status de implementação em 2026-06-04

Implementado:

- Catálogo explicável de presets, temas e estilos em `app/utils/component_library.py`.
- Previews PNG cacheados por preset/tema/estilo em `workspace/component_previews/`.
- Endpoints `/api/component-library`, `/api/component-library/previews` e `/api/component-library/presets/{preset_id}/preview`.
- UI com previews reais de tema, estilo e preset, incluindo inspetor compacto no modal de componente.
- `data_binding`, `render_mode` e `visual_warnings` nos artefatos visuais.
- Validação de roles contra metadados em `app/utils/artifact_validation.py`.
- Biblioteca real de renderizadores em `app/utils/component_renderers.py`.
- Funções de biblioteca injetadas no script final: `render_visual_component(...)`, `render_bar_chart(...)`, `render_line_chart(...)`, `render_scatter_plot(...)`, `render_donut_chart(...)`, `render_gauge_chart(...)`, `render_summary_table(...)` e `render_detail_table(...)`.
- Pipeline pula geração LLM de gráficos quando `render_mode: "library"` e mantém fallback gerado quando `render_mode: "hybrid"`.
- Casos canônicos passaram usando renderização por biblioteca: resultado salvo em `tests/canonical/results/1780543750/canonical_results.json`.

Ainda falta:

- Controles estruturados de roles/opções no modal, substituindo edição manual de JSON para a maior parte dos casos.
- Preview re-renderizável do componente real ao alterar `data_binding` ou `visual_options`, sem depender de pipeline completa.
- Regras de regeneração seletiva mais explícitas no endpoint de settings para diferenciar re-render, regeneração de componente e replanejamento.
- Checks canônicos específicos para exigir `render_mode: "library"` em pelo menos alguns casos.
- Página de documentação visual dentro do app usando a mesma galeria de previews.

## Avaliação

Sim, é uma boa ideia para este projeto. Hoje o sistema já tem bons contratos para geração de componentes, preview individual e algumas configurações visuais globais (`chart_palette` e `visual_instructions`), mas cada gráfico ainda é quase sempre inventado do zero pelo LLM. Isso gera inconsistência visual, aumenta a chance de gráficos ruins e dificulta forçar tabelas, scatter, linha, radial e barras bem construídos.

A biblioteca deve funcionar como uma camada de contratos reutilizáveis: cada tipo de componente define papéis de dados, regras de agregação, padrões de layout, opções permitidas e instruções específicas para o prompt. Os temas devem ser uma camada separada, aplicada globalmente nas configurações e opcionalmente sobrescrita no modal de edição de componente.

O próximo salto de qualidade não é apenas ter mais IDs de preset. O ganho maior vem de transformar os presets em componentes reais, com renderizadores Python reutilizáveis, previews visuais no frontend e uma camada declarativa de dados que o LLM preenche. Nesse modelo, o LLM escolhe intenção, colunas e agregações; a biblioteca renderiza barras, linhas, tabelas, donuts e scatters com comportamento previsível.

## Objetivos

- Criar uma biblioteca versionada de presets para gráficos e tabelas.
- Tornar tabelas um tipo visual de primeira classe, não um caso raro gerado por sorte.
- Permitir tema visual global nas configurações do relatório.
- Permitir override de tema, estilo e preset por componente no modal de edição.
- Reduzir código visual improvisado pelo LLM usando contratos e helpers compartilhados.
- Dar visibilidade imediata ao usuário sobre o que cada preset, tema e estilo realmente muda.
- Evoluir presets para renderizadores Python reais, por exemplo `render_bar_chart(...)`, `render_table_summary(...)` e `render_scatter_plot(...)`.
- Criar uma galeria de previews com dados sintéticos para comparar preset, tema e estilo antes de gerar o PDF.
- Reduzir o papel do LLM na parte visual para escolha de componente, mapeamento de colunas e configuração declarativa.
- Manter compatibilidade com sessões antigas e artefatos que só possuem `type`, `sub_type`, `columns` e `reason`.
- Melhorar testes para garantir que prompts, validação e regeneração respeitem presets e temas.

## Não Objetivos

- Substituir o LLM por um construtor visual completo.
- Criar gráficos interativos no PDF.
- Criar um editor de propriedades exaustivo com dezenas de controles.
- Refatorar todo o renderer de uma vez.
- Depender de chamadas reais a provedores LLM em testes automatizados.

## Estado Atual Relevante

- `app/utils/component_library.py` concentra presets, temas, estilos, metadados de uso e previews.
- `app/utils/component_renderers.py` concentra os renderizadores determinísticos usados pelo script final.
- `GET /api/component-library` expõe o catálogo para o frontend.
- `GET /api/component-library/previews` e `GET /api/component-library/presets/{preset_id}/preview` expõem miniaturas cacheadas.
- `ReportSettings` e `DEFAULT_SETTINGS` já carregam `visual_theme_id`, `chart_style_id`, `chart_palette` e `visual_instructions`.
- A UI tem seleção global de tema/estilo, previews reais e override por componente.
- `prompts/GENERATE_ARTIFACTS.md` pede `data_binding` e prefere `render_mode: "library"` para presets suportados.
- `prompts/GENERATE_COMPONENT_CODE.md` orienta fallback seguro para `render_mode: "hybrid"`.
- `app/services/llm_service.py` injeta tema, paleta, estilo, helpers compartilhados e renderizadores da biblioteca no script final.
- `app/services/pipeline_service.py` pula chamada LLM visual quando o artifact usa biblioteca suportada.
- O endpoint de preview de componente já existe em `/api/sessions/{job_id}/components/{component_id}/preview`.
- O gargalo restante não é mais a falta de renderizadores, mas a usabilidade de edição: roles e opções ainda dependem demais de JSON manual no modal.

## Modelo de Dados Proposto

### Settings globais

Campos opcionais já existentes ou esperados em `ReportSettings`:

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
  "data_binding": {
    "source": "sales.csv",
    "roles": {
      "category": "region",
      "value": "revenue"
    },
    "aggregation": "sum",
    "sort": "desc",
    "limit": 10
  },
  "render_mode": "library",
  "columns": ["sales.csv:region", "sales.csv:revenue"],
  "reason": "Comparar regiões por receita total."
}
```

Regras:

- `component_preset_id` define a estrutura visual principal.
- `visual_theme_id: null` significa usar o tema global.
- `chart_style_id: null` significa usar o estilo global.
- `visual_options` deve ser pequeno e validado por preset.
- `data_binding` descreve como o componente consome os dados.
- `render_mode` define se o componente usa biblioteca, código gerado ou modo híbrido.
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

`VISUAL_THEMES` já existe em `app/utils/component_library.py`. A próxima melhoria é enriquecer cada tema com metadados de uso e amostras visuais, para que o usuário entenda quando escolher cada opção.

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

Adicionar aos temas:

- `best_for`: lista curta de contextos, por exemplo "board executivo", "impressão", "relatório operacional".
- `avoid_when`: lista curta de casos em que o tema não funciona bem.
- `accessibility_notes`: contraste, legibilidade em impressão e risco para daltonismo.
- `preview_tokens`: mini especificação de como desenhar a amostra do tema no frontend.

## Biblioteca de Renderizadores Python

O plano já evoluiu de "preset como instrução para o LLM" para "preset como componente renderizável". A biblioteca de componentes tem funções Python reais que recebem dados, layout e tema, e inserem a visualização no PDF.

Estrutura atual:

```text
app/utils/component_renderers.py
```

APIs implementadas e injetadas no script final:

```python
def render_visual_component(pdf, dataframes, artifact, x, y, w, h):
    ...

def render_bar_chart(pdf, dataframes, x, y, w, h, *, category, value, source=None, series=None, title=None, options=None):
    ...

def render_line_chart(pdf, dataframes, x, y, w, h, *, date, value, source=None, series=None, title=None, options=None):
    ...

def render_summary_table(pdf, dataframes, x, y, w, h, *, dimensions, metrics, source=None, title=None, options=None):
    ...
```

Onde:

- `pdf`: instância do `FPDF`.
- `dataframes`: mapa de arquivos para `DataFrame`.
- `artifact`: componente planejado, incluindo `data_binding`, `component_preset_id` e `visual_options`.
- `x`, `y`, `w`, `h`: coordenadas e dimensões do slot no PDF.
- Tema, estilo, paleta, tipografia e limites são injetados como constantes/helper functions no script final.

O script final agora chama wrappers determinísticos para artifacts em `render_mode: "library"`:

```python
render_visual_component(
    pdf,
    dataframes,
    artifact,
    x,
    y,
    w,
    h,
)
```

Isso mantém o LLM útil para escolher a análise, mas tira dele a responsabilidade de reinventar desenho, legenda, clipping, tabela e escala toda vez.

### Especificação Declarativa de Dados

Para a biblioteca funcionar, o artefato precisa guardar uma especificação de dados mais precisa que apenas `columns`.

Adicionar `data_binding`:

```json
{
  "component_preset_id": "bar_ranked",
  "data_binding": {
    "source": "sales_pipeline.csv",
    "roles": {
      "category": "region",
      "value": "revenue"
    },
    "aggregation": "sum",
    "sort": "desc",
    "filters": [],
    "limit": 8
  }
}
```

Regras:

- O LLM escolhe `source`, `roles`, `aggregation`, filtros e limites.
- A validação confere se as colunas existem e se os tipos são compatíveis com os papéis do preset.
- O renderizador executa a agregação de forma determinística.
- Se `data_binding` estiver ausente, usar fallback para o código legado do componente.

### Benefícios Esperados

- Menos falhas de renderização e menos autocorreções.
- Temas passam a afetar todos os gráficos de forma consistente.
- O preview de um preset fica fiel ao PDF final.
- Regenerar visual pode dispensar chamada ao LLM quando a análise não mudou.
- Testes unitários conseguem validar gráficos por função, não apenas por script final.

### Ordem de Migração dos Renderizadores

1. `table_summary` e `table_detail`, porque resolvem uma lacuna de usabilidade imediata.
2. `bar_ranked` e `bar_grouped`, porque são os gráficos mais frequentes.
3. `line_timeseries`, por exigir parsing temporal consistente.
4. `scatter_relationship`, por exigir controle de densidade e legenda.
5. `radial_donut` e `radial_gauge`, por terem maior risco de uso indevido.
6. `heatmap_matrix`, `histogram_distribution`, `combo_bar_line` e `small_multiples` como expansão.

## Backend

### 1. Biblioteca e endpoints

Manter e expandir:

- `app/utils/component_library.py`
- `GET /api/component-library`
- `GET /api/component-library/previews`
- `GET /api/component-library/presets/{preset_id}/preview`
- `GET /api/component-library/themes/{theme_id}/preview`

Resposta de `/api/component-library`:

```json
{
  "presets": [...],
  "themes": [...],
  "chart_styles": [...],
  "preview_matrix": [...]
}
```

Cada preset deve incluir dados explicativos para a UI:

```json
{
  "id": "bar_ranked",
  "name": "Barras ranqueadas",
  "description": "Compara categorias ordenadas por uma métrica.",
  "best_for": ["ranking", "comparação categórica", "top N"],
  "avoid_when": ["muitas séries", "relação entre duas métricas"],
  "required_roles": ["category", "value"],
  "common_options": ["orientation", "top_n", "show_values"],
  "preview_image_url": "/api/component-library/presets/bar_ranked/preview?theme=executive_clean&style=modern"
}
```

`/api/component-library/previews` deve gerar ou retornar imagens pequenas com dados sintéticos fixos. O objetivo é mostrar a diferença real entre:

- mesmo preset com temas diferentes;
- mesmo preset com estilos diferentes;
- presets parecidos, por exemplo `bar_ranked` vs `bar_grouped`;
- tabela vs gráfico quando o dado pede leitura exata.

As imagens podem ser geradas sob demanda e cacheadas em `workspace/component_previews/`, com hash de `preset_id`, `theme_id`, `style_id` e versão do catálogo.

### 2. Schema de sessão

Atualizar `app/models/session.py`:

- `ReportSettings.visual_theme_id: str | None = "executive_clean"`
- `ReportSettings.chart_style_id: str | None = "modern"`
- manter `extra="allow"` para compatibilidade.

Adicionar campos opcionais em artefatos:

- `component_preset_id`
- `visual_theme_id`
- `chart_style_id`
- `visual_options`
- `data_binding`
- `render_mode`: `library`, `generated_code` ou `hybrid`

`render_mode` permite migração gradual. Componentes já suportados usam `library`; componentes fora da cobertura continuam com `generated_code`; casos intermediários podem usar `hybrid`.

### 3. Validação de artefatos

Atualizar `app/utils/artifact_validation.py`:

- Incluir `radial`, `donut` e `gauge` nos tipos/subtipos visuais aceitos.
- Validar se `component_preset_id` existe.
- Se `component_preset_id` estiver ausente, inferir por `type`/`sub_type`.
- Normalizar `visual_options` usando `default_options` do preset.
- Se o preset exigir coluna numérica/categórica, emitir warning quando metadados indicarem incompatibilidade.
- Reforçar troca de `pie/donut/radial_donut` para `bar_ranked` quando cardinalidade for alta.
- Validar `data_binding.roles` contra `data_roles` do preset.
- Validar cardinalidade antes de permitir donut, gauge e small multiples.
- Registrar no artefato uma lista `visual_warnings` para a UI exibir decisões de correção.

### 4. Geração de artefatos

Atualizar `prompts/GENERATE_ARTIFACTS.md`:

- Expor lista resumida de presets disponíveis.
- Pedir explicitamente `component_preset_id` para componentes visuais.
- Instruir o LLM a usar `table_summary` quando o insight exigir valores exatos, ranking curto ou comparação tabular.
- Instruir `radial_donut` apenas para poucas categorias e `radial_gauge` apenas para progresso contra meta.

Atualizar `generate_artifacts(...)` para incluir o catálogo no prompt.

Além do `component_preset_id`, o planner deve tentar produzir `data_binding` para presets suportados por renderizadores. O contrato deve pedir:

- fonte de dados;
- mapeamento de papéis do preset para colunas;
- agregação;
- filtro;
- ordenação;
- limite/top N;
- razão analítica da escolha.

Quando o LLM não conseguir preencher `data_binding` com confiança, o artefato deve cair para `render_mode: "generated_code"` e seguir o fluxo atual.

### 5. Geração de código de componente

Atualizar `generate_component_code(...)`:

- Resolver preset efetivo antes do prompt.
- Resolver tema efetivo: override do artefato ou tema global.
- Resolver estilo efetivo: override do artefato ou estilo global.
- Pular geração de código quando `render_mode` for `library` e o preset tiver renderizador estável.
- Incluir no prompt:
  - `Component Preset Contract`
  - `Resolved Visual Theme`
  - `Resolved Chart Style`
  - `Resolved Visual Options`
  - `Data Binding Contract`, quando existir

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

Regra de decisão:

- `library`: usar renderizador Python e não chamar LLM para código visual.
- `generated_code`: usar LLM como hoje.
- `hybrid`: usar renderizador para base visual e permitir ao LLM gerar apenas transformação de dados ou anotação textual.

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

Quando o renderizador Python estiver disponível, `assemble_script(...)` deve preferir chamadas à biblioteca:

```python
render_visual_component(pdf, dataframes, artifact, slot, VISUAL_CONTEXT)
```

O código legado gerado pelo LLM deve continuar disponível como fallback por componente, não como caminho principal para presets já migrados.

### 7. Regeneração e invalidação

Regras sugeridas:

- Mudança de `visual_theme_id` global: reexecutar `run_script_generation`, sem limpar componentes, quando o código usa helpers/constantes globais.
- Mudança de `chart_palette`: reexecutar `run_script_generation`.
- Mudança de `chart_style_id` global: no MVP, limpar componentes visuais e rodar `run_pipeline`, porque alguns estilos podem exigir escolhas de código diferentes.
- Mudança de `component_preset_id` de um componente: limpar apenas aquele componente e regenerar.
- Mudança de `visual_options`: limpar apenas aquele componente.
- Mudança de tema override por componente: tentar re-render se só afetar tema; regenerar componente se afetar estilo/preset.
- Mudança de `data_binding`: re-render se o preset usa biblioteca; regenerar código apenas se o componente estiver em `generated_code`.
- Mudança de `render_mode`: validar se o preset suporta biblioteca antes de aceitar.

Para usabilidade, a UI deve mostrar a consequência antes de aplicar:

- "Re-render rápido": sem chamada LLM.
- "Regenerar componente": chamada LLM limitada ao componente.
- "Replanejar relatório": chamada LLM completa e possível mudança de layout.

## Frontend

### Configurações globais

Adicionar à aba `Configurações`:

- Select de `Tema visual`.
- Select de `Estilo de gráfico`.
- Preview pequeno de paleta com swatches.
- Preview real com mini gráfico usando dados sintéticos.
- Comparação rápida entre o tema atual e 2 alternativas.
- Indicação de uso recomendado, por exemplo "melhor para impressão" ou "melhor para dashboard denso".
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

### Galeria de Componentes

Adicionar uma área acessível a partir das configurações ou do modal de componente, com previews reais dos presets. A galeria deve responder à pergunta prática do usuário: "o que essa escolha faz no PDF?"

Cada card de preset deve mostrar:

- miniatura renderizada com dados sintéticos;
- nome e família;
- quando usar;
- quando evitar;
- papéis de dados exigidos;
- opções disponíveis;
- temas/estilos suportados;
- botão para aplicar ao componente atual, quando aberto a partir do modal.

Os previews devem usar dados fixos e pequenos, por exemplo:

```python
SAMPLE_DATASETS = {
    "category_rank": [
        {"category": "Norte", "value": 142},
        {"category": "Sul", "value": 118},
        {"category": "Leste", "value": 96}
    ],
    "timeseries": [
        {"period": "Jan", "value": 80},
        {"period": "Fev", "value": 92},
        {"period": "Mar", "value": 88}
    ]
}
```

Esses dados não entram no relatório. Eles servem apenas para mostrar a aparência de cada preset/tema/estilo.

### Inspetor de Preset

No modal de componente, ao selecionar um preset, mostrar um painel compacto com:

- preview do preset com o tema efetivo;
- resumo de comportamento, por exemplo "agrega por categoria, ordena decrescente e agrupa excedentes";
- papéis de dados e colunas mapeadas;
- alertas de compatibilidade, por exemplo "esta coluna parece textual, mas o papel `value` exige número";
- impacto da alteração, por exemplo "re-render rápido" ou "regenerar componente".

Esse painel deve evitar texto explicativo longo. A informação deve ser escaneável e ligada diretamente à decisão do usuário.

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

Evolução dos controles:

- `top_n`: stepper numérico.
- `orientation`: segmented control horizontal/vertical.
- `show_values`: toggle.
- `aggregate_others`: toggle.
- `aggregation`: menu `sum`, `mean`, `count`, `median`, `min`, `max`.
- `sort`: menu `desc`, `asc`, `none`.
- `roles`: selects de coluna por papel exigido pelo preset.

Quando o usuário altera esses controles em um componente com `render_mode: "library"`, o preview deve re-renderizar sem chamada LLM.

### Lista de componentes

Mostrar no card:

- Tipo/subtipo.
- Preset efetivo.
- Tema override, quando existir.
- Modo de renderização: biblioteca, código gerado ou híbrido.
- Alertas visuais pendentes.

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
- `/api/component-library/previews` retorna imagens ou URLs cacheadas para combinações principais.
- `data_binding` é validado contra `data_roles`.
- Renderizadores de biblioteca geram PDF/PNG sem chamada LLM.
- `render_visual_component` despacha para o preset correto.
- `generate_component_code` inclui preset, tema e opções no prompt, usando mock de `call_llm`.
- `generate_component_code` é pulado quando `render_mode` é `library`.
- `assemble_script` injeta tema e helpers.
- `assemble_script` injeta chamada a `render_visual_component` para componentes suportados.
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
- Confirmar presença de pelo menos um componente com `render_mode: "library"`.
- Confirmar que o script usa `CHART_PALETTE`/tema resolvido.
- Confirmar que componentes renderizam sem falhas no manifest.
- Confirmar que o PDF final muda quando o mesmo caso é renderizado com outro tema, sem replanejar artefatos.

## Plano de Implementação por Fases

### Fase 1: Catálogo explicável e previews - implementada

- Enriquecer `component_library_payload()` com `best_for`, `avoid_when`, papéis exigidos e opções comuns.
- Criar datasets sintéticos pequenos para previews.
- Adicionar endpoint de preview por preset/tema/estilo.
- Cachear previews em `workspace/component_previews/`.
- Mostrar previews na configuração global e no modal.

Resultado esperado: o usuário entende visualmente o que cada tema, estilo e preset faz antes de aplicar.

### Fase 2: Schema visual e `data_binding` - implementada

- Adicionar `data_binding`, `render_mode` e `visual_warnings` aos artefatos.
- Normalizar `component_preset_id`, `visual_options` e `data_binding`.
- Validar papéis de dados contra os tipos detectados nos metadados.
- Expor alertas de compatibilidade no modal.
- Atualizar prompts para pedir `data_binding` quando o preset suportar biblioteca.

Resultado esperado: a escolha visual passa a ser configurável e validável, não apenas uma descrição textual.

### Fase 3: Renderizadores determinísticos para tabelas e barras - implementada com escopo ampliado

- Criar `app/utils/component_renderers.py`.
- Implementar renderizadores para barras, linhas, scatter, donut, gauge e tabelas.
- Fazer `assemble_script(...)` chamar `render_visual_component(...)` quando `render_mode: "library"`.
- Manter fallback para código gerado em `render_mode: "hybrid"`.
- Adicionar testes unitários dos renderizadores com dados sintéticos e execução real de PDF.

Resultado esperado: os casos mais comuns deixam de depender de código visual improvisado pelo LLM.

### Fase 4: Controles de componente e preview sem LLM - parcialmente implementada

- Adicionar galeria de componentes. Implementado como previews de biblioteca e cards de presets.
- Adicionar inspetor de preset no modal. Implementado.
- Adicionar controles de `top_n`, orientação, agregação, ordenação e roles. Pendente; hoje ainda há JSON para `visual_options` e `data_binding`.
- Re-renderizar preview local/remoto quando mudar opção em componente de biblioteca. Pendente para preview do componente real.
- Mostrar impacto da alteração antes de aplicar. Parcial; o modal mostra contexto, mas ainda não diferencia todos os custos de regeneração.

Resultado esperado: edição visual fica previsível, rápida e compreensível.

### Fase 5: Linha, scatter e radial - implementada

- Implementar `render_line_timeseries`.
- Implementar `render_scatter_relationship`.
- Implementar `render_radial_donut` com limites rígidos de cardinalidade.
- Implementar `render_radial_gauge` apenas para valor/meta.
- Adicionar validação específica para cada preset.

Resultado esperado: a biblioteca cobre os principais tipos visuais do critério de pronto.

### Fase 6: Regeneração seletiva - parcialmente implementada

- Atualizar endpoint de settings para distinguir re-render, regeneração de componente e regeneração total. Pendente.
- Atualizar endpoint de artefatos para invalidar só o necessário quando preset/opções mudarem. Parcialmente implementado para preset, estilo, opções, binding e modo.
- Garantir que `handleRegenerateCurrentIndicator()` preserva override visual e `data_binding`.
- Adicionar testes de invalidação.

Resultado esperado: mudar visual não custa uma nova pipeline completa quando os dados e a análise não mudaram.

### Fase 7: Cobertura canônica e documentação viva - parcialmente implementada

- Adicionar casos canônicos com tabela, scatter, radial e tema específico. Pendente como checks explícitos de playbook.
- Adicionar checks para `render_mode: "library"`. Pendente.
- Confirmar que manifest registra componentes renderizados pela biblioteca. Coberto indiretamente por execução canônica e testes unitários; ainda falta check canônico dedicado.
- Documentar presets em uma página simples do app, usando a mesma galeria de previews. Pendente.
- Atualizar README com a diferença entre tema, estilo, preset e data binding. Pendente.

Resultado esperado: a biblioteca vira parte confiável do fluxo de geração e também uma referência visual para o usuário.

## Ordem Recomendada para o MVP

1. Concluído: enriquecer catálogo com explicações e metadados de uso.
2. Concluído: criar previews com dados sintéticos para presets, temas e estilos.
3. Concluído: adicionar `data_binding`, `render_mode` e validação de roles.
4. Concluído: implementar renderizadores de tabela, barras, linha, scatter e radial.
5. Concluído: fazer o script chamar `render_visual_component` para presets suportados.
6. Parcial: adicionar galeria e inspetor de preset na UI.
7. Pendente: adicionar controles estruturados e preview sem LLM para componentes de biblioteca.
8. Pendente: adicionar checks canônicos explícitos para `render_mode: "library"` e cobertura de tabela/radial/tema.

## Decisões de Design

- Separar tema de preset. O mesmo `bar_ranked` deve funcionar em vários temas.
- Separar estilo de paleta. Um tema define cores; um estilo define densidade, grid, marcadores e bordas.
- Presets devem ser declarativos na camada de planejamento e determinísticos na camada de renderização quando houver renderizador estável.
- O LLM deve escolher intenção, colunas e agregação; a biblioteca deve desenhar o gráfico sempre que possível.
- `generated_code` continua existindo como fallback, não como caminho principal dos presets migrados.
- Tabelas devem ser incentivadas no planejamento, não apenas no prompt de componente.
- Mudanças globais de cor devem ser re-render, não regeneração com LLM, sempre que possível.
- Mudanças de preset devem re-renderizar quando ambos os presets tiverem renderizador de biblioteca e papéis compatíveis; caso contrário, regenerar apenas o componente.
- Previews devem ser gerados pela mesma biblioteca que renderiza o PDF, para evitar divergência entre o que a UI promete e o que sai no relatório.
- A UI deve mostrar impacto operacional de cada alteração antes de aplicar.

## Riscos

- O LLM pode ignorar presets se o contrato for longo demais. Mitigação: enviar apenas o preset resolvido para cada componente.
- Temas podem não afetar código antigo que hardcodou cores. Mitigação: prompts novos + helpers + regeneração de gráficos.
- Muitas opções no modal podem deixar a edição pesada. Mitigação: começar com poucos controles e JSON avançado opcional.
- Radial/gauge pode ser usado em dados inadequados. Mitigação: validação por cardinalidade e papéis de dados.
- Tabelas podem estourar layout. Mitigação: `table_summary` com limite de linhas/colunas e fonte dinâmica.
- Renderizadores determinísticos podem não cobrir todos os insights. Mitigação: usar `render_mode` por componente e manter fallback para código gerado.
- Previews cacheados podem ficar obsoletos após mudança de tema/preset. Mitigação: hash com versão do catálogo e assinatura visual.
- `data_binding` errado pode gerar gráfico tecnicamente válido, mas semanticamente ruim. Mitigação: mostrar roles na UI, validar tipos e registrar warnings.
- Importar renderizadores no script gerado aumenta acoplamento com o app. Mitigação: versionar API pública de `app.renderers` e manter compatibilidade de sessão.

## Critério de Pronto

- Um novo relatório pode gerar pelo menos barra, linha, scatter, radial/donut e tabela usando presets.
- Pelo menos tabela e barras usam `render_mode: "library"` sem código visual gerado pelo LLM.
- A galeria mostra previews reais para presets, temas e estilos usando dados sintéticos.
- O modal mostra preview, papéis de dados, opções e impacto de alteração do preset escolhido.
- O usuário consegue escolher um tema global nas configurações e ver efeito no PDF.
- O usuário consegue alterar o preset/tema de um componente no modal e regenerar só aquele componente.
- Alterar opções de um componente de biblioteca re-renderiza sem chamada LLM.
- Tabelas aparecem quando o objetivo pede ranking, comparação exata ou resumo tabular.
- Testes unitários cobrem catálogo, schema, validação, renderizadores, prompts e script gerado.
- Pelo menos um teste canônico confirma tabela, tema e componente em `render_mode: "library"` em um PDF renderizado.
