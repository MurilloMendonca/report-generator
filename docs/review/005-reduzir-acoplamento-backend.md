# Reduzir acoplamento do backend

Prioridade: Media

Area: arquitetura, manutencao

## Problema

`app/main.py`, `app/services/llm_service.py` e `app/services/pipeline_service.py` concentram muitas responsabilidades. Rotas, validacao de entrada, estado de sessao, refinamento, preview, montagem de script, chamadas LLM e regras de negocio ficam espalhadas em arquivos grandes. Isso aumenta o custo de mudancas e torna dificil testar fluxos pequenos isoladamente.

## Evidencia local

- `app/main.py` possui rotas, autenticacao, helpers de upload, refinamento, preview de componente, settings e manipulacao manual de artefatos.
- `app/services/llm_service.py` mistura catalogo de modelos, chamadas provider, parse de respostas, geracao de codigo, montagem do script final e refinamento.
- `pipeline_service.py` orquestra todas as etapas com atualizacoes diretas de sessao.

## Mudanca a fazer

Separar por limites de responsabilidade:

- mover rotas para routers (`auth`, `sessions`, `files`, `artifacts`, `settings`, `templates`, `explore`);
- criar servicos pequenos para upload, refinamento, preview, componentes e layout;
- separar client/provider LLM de parsing/contratos;
- extrair `assemble_script` para um modulo de renderer/compiler;
- representar etapas da pipeline como funcoes ou classes com entrada/saida testavel;
- manter `main.py` apenas como composicao da app.

## Criterios de aceite

- `app/main.py` fica majoritariamente com configuracao e inclusao de routers.
- Cada endpoint chama um servico dedicado.
- `llm_service.py` nao monta script final nem manipula detalhes de layout.
- Testes conseguem exercitar cada etapa da pipeline sem criar uma sessao completa.
