# Corrigir documentacao operacional

Prioridade: Baixa

Area: documentacao, manutencao

## Problema

Alguns documentos estao desalinhados com o codigo atual. Isso confunde quem vai operar ou manter o projeto. O exemplo mais claro e o README afirmar que a persistencia de sessao nao tem locking, enquanto `session_manager.py` ja usa lock de arquivo e escrita atomica. O README tambem descreve `GENERATE_TEMPLATE.md` como prompt ativo, embora o codigo atual use a biblioteca de templates.

## Evidencia local

- `README.md` em "Important Maintenance Notes" diz: `Session persistence is file-based and has no locking`.
- `app/services/session_manager.py` implementa `session_lock`, `write_session_file` atomico com tempfile e `os.replace`.
- `README.md` lista `GENERATE_TEMPLATE.md` em Prompt Contracts.
- `app/services/llm_service.py` nao carrega `GENERATE_TEMPLATE.md` em `generate_template`.
- `AGENTS.md` informado ao agente dizia que nao havia suite de testes, mas o repositorio tem varios testes.

## Mudanca a fazer

Atualizar a documentacao para refletir o estado real:

- corrigir nota sobre locking: ha locking por escrita, mas nao lock de pipeline inteira;
- explicar o fluxo atual de template local;
- atualizar guias internos sobre suite de testes;
- adicionar uma matriz curta de comandos de verificacao;
- separar documentos de estado atual e planos futuros.

## Criterios de aceite

- README nao contradiz `session_manager.py`.
- Prompt contracts listam apenas prompts ativos ou marcam legados.
- Guia do repositorio menciona a suite de testes existente.
- Novos contribuidores conseguem entender o fluxo real sem ler todo o codigo.
