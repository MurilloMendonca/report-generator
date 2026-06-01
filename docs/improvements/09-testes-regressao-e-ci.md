# 09 - Testes, regressao visual e CI

## Prioridade
Media-alta

## Problema
O projeto ja possui testes unitarios e runner canonico, mas a cobertura ainda nao fecha o ciclo completo de qualidade. Falta CI declarado, testes de API com LLM mockado, testes de frontend, isolamento de workspace por teste e validacao visual mais forte do PDF.

## Impacto
Alteracoes em prompts, layout, sessao ou sandbox podem degradar o PDF final sem serem detectadas rapidamente. Como a aplicacao mistura LLM, codigo gerado, dados e UI, regressao pequena em uma etapa pode aparecer apenas no arquivo final.

## Evidencias no codigo
- `tests/` cobre schema, parsing de metricas, sandbox, filtro e runner canonico.
- `tests/canonical/run_canonical_sessions.py` existe, mas depende de chaves reais para execucao ponta a ponta.
- Nao ha configuracao de CI versionada.
- O frontend nao possui testes automatizados.

## Proposta
Criar uma matriz de qualidade:

- Migrar ou padronizar comandos com `pytest`, mesmo mantendo compatibilidade com `unittest`.
- Testar endpoints FastAPI com `TestClient` e LLMs mockados.
- Criar fixtures de respostas LLM para pipeline deterministico.
- Isolar `workspace` por teste com variavel de ambiente ou fixture temporaria.
- Adicionar Playwright para fluxos principais: login, upload, preview, regenerate, layout save.
- Expandir runner canonico com score numerico por qualidade: titulo, secoes, metricas, graficos, paginas nao vazias e ausencia de erros.
- Rodar `compileall`, unit tests e subset canonico mockado em CI.
- Guardar artefatos de falha: session JSON, script, stdout/stderr, PDF e screenshot.

## Criterios de aceite
- Um PR executa checks locais equivalentes ao CI.
- Pipeline sem chamada real a LLM consegue gerar um PDF deterministico com fixtures.
- Frontend cobre pelo menos os fluxos criticos sem teste manual.
- PDFs canonicos recebem score comparavel entre execucoes.

## Arquivos envolvidos
- `tests/`
- `tests/canonical/`
- `app/services/llm_service.py`
- `static/`
- `.github/workflows/` ou ferramenta equivalente de CI
