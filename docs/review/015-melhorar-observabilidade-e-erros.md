# Melhorar observabilidade e erros

Prioridade: Media

Area: backend, operacao, UX

## Problema

O estado da sessao guarda logs textuais e erro estruturado basico, mas varios pontos usam `print`, `traceback.print_exc`, mensagens curtas ou fallback silencioso. Isso dificulta diagnosticar falhas de LLM, parse, execucao, preprocessamento e renderizacao em producao.

## Evidencia local

- `pipeline_service.py` grava etapas em `status`, `current_step`, `progress` e `error`.
- `llm_service.py` usa `print("DEBUG...")` em varias falhas.
- Alguns `except` ignoram erros de parse/refinamento.
- O frontend mostra apenas os ultimos 5 logs e muitas acoes nao exibem erro estruturado.

## Mudanca a fazer

Padronizar logging e diagnosticos:

- usar logger estruturado com `job_id`, `run_id`, etapa, provider, modelo e duracao;
- persistir erro por etapa com codigo, mensagem curta e detalhes tecnicos truncados;
- separar mensagem para usuario de detalhe tecnico;
- adicionar metricas de tempo/custo/tentativas por chamada LLM;
- mostrar na UI historico de etapas e erros recuperaveis;
- substituir `print` e `traceback.print_exc` por logger.

## Criterios de aceite

- Cada etapa da pipeline tem inicio, fim, duracao e resultado registrados.
- Erros de provider, schema e sandbox sao distinguiveis.
- A UI mostra uma mensagem clara e permite abrir detalhes tecnicos.
- Testes validam formato de `session.error` em falhas representativas.
