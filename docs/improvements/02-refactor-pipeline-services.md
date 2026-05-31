# Melhoria 02: Refatorar Pipeline em Serviços Menores

## Objetivo

Reduzir o tamanho e a responsabilidade de `app/main.py`, deixando a pipeline mais legível, testável e fácil de evoluir.

## Escopo

Extrair responsabilidades para módulos de serviço:

- `PipelineService`: orquestra o fluxo principal.
- `DataFilterService`: detecta e aplica filtros.
- `MetadataService`: extrai e sincroniza metadados raw/filtered.
- `GenerationService`: gera artefatos, métricas, textos e componentes.
- `RenderService`: monta e executa scripts/PDF.
- `SessionRepository` ou extensão do `session_manager`: encapsula persistência.

## Plano de Execução

1. Mapear funções atuais de `app/main.py` por responsabilidade.
2. Mover uma responsabilidade por vez, preservando assinatura e comportamento.
3. Manter endpoints em `app/main.py`, mas delegar para serviços.
4. Adicionar testes mínimos para cada serviço extraído.
5. Rodar sessões canônicas após cada etapa maior de refatoração.

## Resultados Esperados

- `app/main.py` fica focado em rotas HTTP e validação superficial.
- A pipeline pode ser testada sem subir FastAPI.
- Melhor isolamento para filtro, renderização e regeneração.
- Menos risco de regressão ao adicionar novas etapas.

## Como Testar Efetividade

Comandos:

```bash
venv/bin/python -m compileall app
venv/bin/python -m unittest discover -s tests
venv/bin/python tests/canonical/run_canonical_sessions.py --case sales_ops
```

Critérios:

- Nenhuma mudança no formato público dos endpoints.
- Sessões existentes continuam carregando.
- Sessão canônica gera PDF com sucesso.
- Diffs funcionais mínimos em session JSON, exceto logs/campos novos.

## Riscos

- Refatoração ampla pode misturar mudança estrutural com mudança de comportamento.
- Deve ser feita em PR/commit separado das melhorias de produto.

