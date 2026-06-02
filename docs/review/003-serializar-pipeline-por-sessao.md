# Serializar pipeline por sessao

Prioridade: Alta

Area: arquitetura, concorrencia, persistencia

## Problema

Varias rotas disparam `run_pipeline` ou `run_script_generation` em `BackgroundTasks`. A persistencia de sessao ja usa lock para escrita atomica, mas nao ha lock de pipeline por `job_id`, fila, cancelamento ou versao de trabalho. Duas regeneracoes na mesma sessao podem rodar ao mesmo tempo e gravar estados intercalados.

## Evidencia local

- `app/main.py` agenda pipeline em criar sessao, adicionar/remover arquivos, chat, salvar artefatos, trocar settings e regenerar.
- `app/services/pipeline_service.py` le e escreve a sessao em varias etapas longas.
- `session_manager.py` protege cada escrita, mas nao protege a sequencia inteira de uma execucao de pipeline.
- O frontend permite acoes de regeneracao enquanto polling continua.

## Mudanca a fazer

Adicionar controle de execucao por sessao:

- criar um lock/fila por `job_id` para permitir apenas uma pipeline ativa por sessao;
- atribuir `run_id` monotonicamente crescente a cada solicitacao;
- fazer cada etapa verificar se o `run_id` ainda e o mais recente antes de gravar;
- permitir cancelar ou marcar como obsoleto um run antigo quando um novo refinamento chega;
- expor status de fila/execucao para a UI;
- impedir botoes conflitantes enquanto uma etapa critica esta em andamento.

## Criterios de aceite

- Dois pedidos rapidos de regeneracao nao misturam `artifacts`, `components`, `template` ou `pdf_version`.
- A sessao final reflete o ultimo pedido do usuario.
- Testes simulam duas pipelines concorrentes e verificam que apenas a mais recente conclui.
- Logs mostram quando uma execucao foi ignorada por estar obsoleta.
