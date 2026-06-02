# Revisar artefatos canonicos versionados

Prioridade: Media

Area: testes, repositorio, operacao

## Problema

O repositorio versiona muitos PDFs e resultados gerados em `tests/canonical/generated_pdfs/` e `tests/canonical/results/`. Isso aumenta o tamanho do repositorio, mistura fixtures com outputs de execucao e torna diffs de teste mais caros de revisar. O proprio README canonico diz que esses arquivos sao gerados por runs.

## Evidencia local

- `git ls-files` mostra dezenas de PDFs em `tests/canonical/generated_pdfs/`.
- `git ls-files` mostra varios diretorios timestampados em `tests/canonical/results/`.
- `.dockerignore` ja ignora `tests/canonical/results` e `*.pdf`, mas o historico atual ainda possui artefatos versionados.
- `tests/canonical/generated_pdfs/.gitkeep` existe, sugerindo que a pasta pode permanecer vazia no repositorio.

## Mudanca a fazer

Separar fixtures de outputs:

- manter versionados apenas fixtures de entrada, playbook, runner e `.gitkeep`;
- remover outputs gerados do controle de versao;
- ajustar `.gitignore` para ignorar `tests/canonical/generated_pdfs/*.pdf` e `tests/canonical/results/`;
- publicar PDFs canonicos como artifacts de CI quando necessario;
- documentar como reproduzir os artefatos localmente.

## Criterios de aceite

- `git ls-files tests/canonical/generated_pdfs` mostra apenas `.gitkeep`.
- `git ls-files tests/canonical/results` nao mostra outputs timestampados.
- CI salva resultados canonicos como artifact opcional, nao como arquivos commitados.
- O README canonico explica o novo fluxo de inspecao manual.
