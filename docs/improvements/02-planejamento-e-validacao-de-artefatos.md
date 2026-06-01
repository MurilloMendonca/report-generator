# 02 - Planejamento e validacao deterministica de artefatos

## Prioridade
Alta

## Problema
O plano do relatorio e aceito quase diretamente do LLM. `generate_artifacts()` valida apenas se o retorno e uma lista JSON nao vazia. Depois disso, o pipeline assume que IDs, tipos, colunas, metricas e relacao com o template estao corretos.

## Impacto
O relatorio pode sair com componentes repetidos, colunas inexistentes, tipos inconsistentes, metricas pouco uteis ou graficos inadequados para a cardinalidade dos dados. Como essa etapa alimenta todas as seguintes, um plano ruim multiplica custo e reduz qualidade em metricas, textos, layout e PDF final.

## Evidencias no codigo
- `app/services/llm_service.py::generate_artifacts()` retorna a lista parseada sem schema de dominio.
- `app/services/pipeline_service.py::run_pipeline()` calcula metricas e gera componentes a partir desse plano sem conferir existencia das colunas citadas.
- `fit_artifacts_to_template()` pode limitar componentes visiveis pelo template, mas nao registra quais insights foram descartados nem reequilibra a narrativa.

## Proposta
Adicionar uma etapa `validate_report_plan` entre `generate_artifacts()` e as metricas:

- Definir modelos Pydantic para `MetricArtifact`, `VisualArtifact` e `TextArtifact`.
- Garantir IDs unicos, estaveis e compativeis com nomes de funcoes Python.
- Normalizar `type` e `sub_type` para um contrato unico.
- Validar referencias de colunas contra os metadados disponiveis.
- Rejeitar pie chart com cardinalidade alta e sugerir barras/tabelas agregadas.
- Exigir pelo menos uma metrica conectada a cada texto executivo.
- Medir cobertura do objetivo do usuario: quais perguntas foram respondidas e quais ficaram sem evidencia.
- Quando o plano for invalido, chamar uma etapa curta de reparo com erros estruturados, em vez de reiniciar tudo.

## Criterios de aceite
- Artefatos invalidos nao seguem para calculo ou renderizacao.
- O usuario consegue ver no workspace um plano legivel com alertas de colunas ausentes, duplicidade ou baixa confianca.
- O pipeline registra quais artefatos foram removidos ou ajustados por restricao de template.
- Existem testes unitarios para plano invalido, colunas inexistentes, IDs duplicados e cardinalidade alta.

## Arquivos envolvidos
- `app/services/llm_service.py`
- `app/services/pipeline_service.py`
- `app/models/session.py`
- `app/utils/template_library.py`
- `prompts/GENERATE_ARTIFACTS.md`
