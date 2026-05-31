# Melhoria 07: Atualizar Documentação do Projeto

## Objetivo

Manter README e documentação alinhados com as features reais: sessões sem prompt, arquivos, preview/download/delete, filtro de dados, provider/model por sessão e atualização seletiva.

## Escopo

- Atualizar `README.md`.
- Atualizar `tests/canonical/README.md`.
- Documentar schema de sessão novo quando existir.
- Documentar fluxo de filtro de dados.
- Documentar endpoints adicionados.
- Documentar estratégia de testes.

## Plano de Execução

1. Revisar README atual e marcar seções defasadas.
2. Atualizar `What It Does` com:
   - objetivo opcional
   - relatório exploratório
   - filtro opcional de dados
   - workspace de arquivos
   - atualização seletiva
3. Atualizar endpoints:
   - download/preview/delete de arquivos
   - regenerate
   - settings com provider/model
4. Atualizar sessão shape.
5. Atualizar canônicos com novos checks.
6. Adicionar uma seção de segurança mais explícita sobre execução de código LLM.

## Resultados Esperados

- Novo desenvolvedor entende fluxo e riscos sem ler todo o código.
- Features visíveis na UI aparecem na documentação.
- Testes/canônicos têm instruções claras.

## Como Testar Efetividade

Checks manuais:

- Seguir README em ambiente limpo deve permitir rodar a aplicação.
- Lista de endpoints deve bater com `app/main.py`.
- Descrição da pipeline deve incluir filtro antes de métricas/componentes.
- `tests/canonical/README.md` deve explicar artefatos gerados e critérios.

## Riscos

- Documentação pode ficar longa demais.
- Melhor priorizar documentação operacional e contratos, não detalhes óbvios de UI.

