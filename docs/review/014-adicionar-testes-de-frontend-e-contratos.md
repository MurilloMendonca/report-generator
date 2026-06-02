# Adicionar testes de frontend e contratos

Prioridade: Media

Area: testes, frontend, qualidade

## Problema

O backend tem boa cobertura unitarias para varias funcoes, mas a interface estatica nao tem testes automatizados. Fluxos criticos como login, upload, polling, preview, edicao de componente, layout drag/resize, settings e explorador dependem de testes manuais. Tambem faltam testes offline de contratos LLM que simulem respostas completas de cada etapa.

## Evidencia local

- `tests/` cobre sandbox, schema, filtros, validacao, contratos parciais e runner canonico.
- Nao ha Playwright, jsdom, Vitest ou testes de UI.
- `static/js/app.js` e `static/js/ui.js` tem muito estado global e manipulacao direta de DOM.
- Casos canonicos chamam provedores reais quando executados, o que nao substitui testes deterministas.

## Mudanca a fazer

Adicionar dois blocos de testes:

- testes frontend com Playwright ou ferramenta equivalente para login, criacao de sessao mockada, tabs, modal de arquivo, editor de componente e layout;
- testes de contrato offline da pipeline com LLM mockado retornando fixtures para artifacts, metricas, textos, componentes, filtro e fix_script;
- fixtures pequenas e deterministicamente validaveis;
- verificacao de XSS/renderizacao segura na UI.

## Criterios de aceite

- CI roda ao menos um fluxo frontend sem provider LLM real.
- Pipeline completa com respostas LLM mockadas gera PDF ou manifesto em teste deterministico.
- Falhas de parsing/contrato LLM sao cobertas por testes unitarios.
- Alteracoes de DOM relevantes deixam de depender exclusivamente de inspecao manual.
