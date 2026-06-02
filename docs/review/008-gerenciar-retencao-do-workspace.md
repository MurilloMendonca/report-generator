# Gerenciar retencao do workspace

Prioridade: Media

Area: operacao, armazenamento, privacidade

## Problema

Uploads, arquivos preprocessados, scripts gerados, PDFs, manifests, sessoes e suporte de sandbox ficam em `workspace/`. Nao ha politica de retencao, limpeza, quota por sessao ou remocao completa de dados. Em uso real, isso acumula arquivos, dados sensiveis e codigo gerado indefinidamente.

## Evidencia local

- `app/main.py` e `pipeline_service.py` criam diretorios sob `workspace/`.
- Remover arquivo da sessao apaga upload e preprocessado daquele arquivo, mas nao ha limpeza global de sessoes antigas.
- `session_manager.py` lista sessoes recentes, mas nao expira registros.
- `README.md` descreve `workspace/` como runtime persistence.

## Mudanca a fazer

Implementar lifecycle de dados:

- criar configuracoes de retencao por tipo (`uploads`, `filtered`, `scripts`, `reports`, `sessions`);
- adicionar endpoint/servico para apagar uma sessao inteira e todos os artefatos associados;
- aplicar quotas de tamanho por sessao e limite total do workspace;
- registrar manifests de arquivos gerados para limpeza confiavel;
- oferecer comando administrativo de cleanup;
- documentar backup, retencao e privacidade.

## Criterios de aceite

- Uma sessao pode ser excluida completamente pela API.
- Um job de cleanup remove artefatos expirados sem apagar arquivos de sessoes ativas.
- Testes cobrem remocao de upload, preprocessado, script, PDF, manifest e JSON de sessao.
- README descreve a politica padrao de retencao.
