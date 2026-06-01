# 07 - Observabilidade, erros estruturados e progresso real

## Prioridade
Media-alta

## Problema
O feedback operacional ainda e baseado em strings de log e polling. A UI exibe poucos logs, varios wrappers de API nao validam `res.ok`, e falhas assíncronas podem chegar ao usuario apenas como status textual. Tambem ha insercoes com `innerHTML` para mensagens de chat/explorador que deveriam ser escapadas.

## Impacto
Quando algo falha, o usuario nao sabe se deve esperar, tentar novamente, trocar modelo ou editar um componente. Para desenvolvimento, fica dificil medir gargalos por etapa, custo do LLM, retries, erro por provider e regressao de qualidade.

## Evidencias no codigo
- `add_log()` em `app/services/session_manager.py` grava strings em `logs` e tambem usa a mensagem como `status`.
- `static/js/app.js::startPolling()` assume que `API.getSession()` sempre retorna sucesso.
- `static/js/api.js` em algumas funcoes retorna `res.json()` sem checar `res.ok`.
- `handleChat()` e `handleExplore()` montam mensagens com template string e `innerHTML`.

## Proposta
Criar telemetria e contrato de erro:

- Separar `status`, `current_step`, `progress`, `logs` e `error`.
- Padronizar erros da API: `code`, `message`, `details`, `retryable`, `stage`.
- Guardar duracao, provider, modelo, tentativa, tokens e resultado por etapa LLM.
- Usar Server-Sent Events ou WebSocket para progresso em tempo real; manter polling como fallback.
- Mostrar historico completo de eventos por sessao, com filtros por etapa.
- Escapar todo conteudo vindo do usuario ou do LLM antes de inserir no DOM.
- Registrar stdout/stderr do sandbox como artefato de debug, com redacao de dados sensiveis.

## Criterios de aceite
- Falhas mostram etapa, causa resumida e acao sugerida.
- O frontend nao quebra quando uma chamada retorna 4xx/5xx.
- Chat e explorador nao injetam HTML arbitrario.
- Cada geracao tem timeline com duracao por etapa e contagem de retries.

## Arquivos envolvidos
- `app/services/session_manager.py`
- `app/services/pipeline_service.py`
- `app/main.py`
- `static/js/api.js`
- `static/js/app.js`
- `static/js/ui.js`
