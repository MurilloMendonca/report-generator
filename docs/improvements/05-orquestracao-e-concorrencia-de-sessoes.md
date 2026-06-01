# 05 - Orquestracao de jobs, concorrencia e persistencia

## Prioridade
Alta

## Problema
As sessoes sao arquivos JSON em `workspace/sessions`, atualizados por leitura-modificacao-escrita sem lock. O pipeline roda como `BackgroundTasks` do FastAPI, e varias acoes do usuario podem disparar novas tarefas enquanto uma geracao anterior ainda esta em andamento.

## Impacto
Atualizacoes podem se perder, um job pode sobrescrever o resultado de outro, o status pode ficar incoerente e a aplicacao nao tem retomada confiavel apos restart. Isso limita o uso com multiplos usuarios, arquivos maiores e refinamentos frequentes.

## Evidencias no codigo
- `update_session()` em `app/services/session_manager.py` le o JSON, aplica `session.update()` e regrava o arquivo.
- `add_files_to_session()`, `chat_refinement()`, `update_settings()`, `regenerate_session()` e `update_template_manually()` podem agendar novas execucoes.
- `run_execution()` usa chamadas recursivas para retry.
- O frontend decide parar polling por strings de status como `Concluido`, `concluido` ou `Falha`.

## Proposta
Introduzir uma camada de job management:

- Escrita atomica de sessoes com arquivo temporario e rename.
- Lock por `job_id` para updates concorrentes.
- Fila de jobs por sessao, com cancelamento ou supersedencia de jobs antigos.
- Estado estruturado: `queued`, `running`, `waiting_user`, `completed`, `failed`, `cancelled`.
- Campo `active_job_id` para evitar que tarefas antigas publiquem PDF apos uma regeneracao mais nova.
- Tabela/arquivo separado de eventos para logs append-only.
- Opção de backend persistente, como SQLite, antes de migrar para Postgres/Redis.
- Substituir retry recursivo por loop iterativo com historico de tentativas.

## Criterios de aceite
- Duas acoes seguidas no mesmo workspace nao corrompem a sessao.
- Ao reiniciar o servidor, a UI mostra jobs incompletos como interrompidos ou retomaveis.
- O frontend usa estado estruturado e progresso numerico, nao strings livres.
- Testes simulam updates concorrentes e jobs obsoletos tentando gravar resultados.

## Arquivos envolvidos
- `app/services/session_manager.py`
- `app/services/pipeline_service.py`
- `app/main.py`
- `app/models/session.py`
- `static/js/app.js`
