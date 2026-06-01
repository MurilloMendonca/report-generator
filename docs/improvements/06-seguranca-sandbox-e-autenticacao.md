# 06 - Seguranca da execucao, uploads e autenticacao

## Prioridade
Alta

## Problema
A aplicacao executa Python gerado por LLM, aceita uploads e expõe artefatos de sessao. Ja existe um sandbox local conservador em `executor_service.py`, mas ele nao substitui isolamento de processo/container em producao. Alem disso, o segredo de acesso tem fallback `change-me`, o token e salvo em `localStorage` e nao ha limites claros de upload, rate limiting ou escopo por usuario/sessao.

## Impacto
Em um ambiente compartilhado, um erro de configuracao ou uma evasao do sandbox pode expor arquivos, consumir recursos ou permitir acesso indevido a sessoes. Mesmo em uso interno, uploads grandes ou maliciosos podem derrubar o processo.

## Evidencias no codigo
- `APP_ACCESS_SECRET` em `app/main.py` usa `"change-me"` se nenhuma variavel for configurada.
- `save_uploaded_files()` persiste arquivos sem validar tamanho, extensao permitida, MIME real ou numero maximo de arquivos.
- `executor_service.py` usa monkeypatch via `sitecustomize`, limites de recurso e subprocesso, mas sem namespace/container/seccomp.
- Dependencias em `requirements.txt` nao estao pinadas.

## Proposta
Endurecer o perfil operacional:

- Falhar startup em ambiente nao-dev quando `APP_ACCESS_SECRET` estiver ausente ou for `change-me`.
- Trocar segredo compartilhado por sessao autenticada, cookie `HttpOnly`, expiracao e logout.
- Adicionar limite de tamanho por arquivo, total por sessao e tipos aceitos.
- Validar conteudo real de arquivo antes de salvar como dado analisavel.
- Aplicar rate limit por IP/usuario em login, upload e geracao.
- Executar scripts gerados em container efemero sem rede, com filesystem montado por job e limites de CPU/memoria.
- Remover acesso a `PATH` amplo dentro do sandbox quando possivel.
- Pinar dependencias e criar rotina de atualizacao/auditoria.

## Criterios de aceite
- A app nao sobe com segredo default fora do modo desenvolvimento.
- Uploads fora de limite retornam erro claro antes de salvar arquivo.
- Scripts gerados rodam em isolamento forte configuravel.
- Testes cobrem leitura de segredo, escrita fora do workspace, rede, timeout, upload grande e extensao bloqueada.

## Arquivos envolvidos
- `app/main.py`
- `app/services/executor_service.py`
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`
- `tests/test_sandbox_executor.py`
