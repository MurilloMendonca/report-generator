# 11 - Modelo analitico e funcionalidades de produto

## Prioridade
Media

## Problema
O fluxo atual gera um relatorio a partir de metadados e codigo LLM por componente. Para tornar a aplicacao mais completa, falta uma camada analitica intermediaria: entender medidas, dimensoes, chaves, periodo, unidade de negocio, contexto de PDFs e tipo de relatorio desejado antes de planejar os componentes.

## Impacto
Sem modelo analitico, a aplicacao tende a tratar cada dataset como uma tabela isolada. Relatorios melhores precisam saber quais campos sao dimensoes, quais sao medidas, que arquivos se relacionam, qual granularidade temporal existe e quais perguntas executivas fazem sentido para o dominio.

## Evidencias no codigo
- `build_files_map()` carrega dataframes por nome de arquivo, mas nao ha camada de relacoes entre arquivos.
- Prompts recebem metadados, mas nao um semantic model com dimensoes, medidas, datas, joins e restricoes.
- O explorador executa scripts ad-hoc, sem reaproveitar descobertas como memoria analitica da sessao.

## Proposta
Adicionar um `semantic_model` por sessao:

- Classificar colunas como medida, dimensao, data, ID, texto livre, percentual ou moeda.
- Inferir relacoes entre tabelas e sugerir joins com confianca.
- Identificar grao temporal e granularidade dos dados.
- Extrair de PDFs um resumo de regras de negocio, metas, definicoes e contexto.
- Permitir perfis de relatorio: executivo, operacional, diagnostico, exploratorio, financeiro, qualidade.
- Transformar respostas do explorador aprovadas pelo usuario em insights reutilizaveis no relatorio.
- Suportar exportacao adicional em HTML ou pacote com PDF, script, dados preprocessados e manifesto.
- Criar biblioteca de templates analiticos por dominio, nao apenas templates de layout.

## Criterios de aceite
- O planejamento usa um semantic model salvo na sessao.
- Relatorios multi-arquivo conseguem explicar como tabelas foram relacionadas ou por que nao foram.
- O usuario pode escolher o tipo de relatorio antes ou depois do upload.
- Insights descobertos no explorador podem virar componentes do PDF.

## Arquivos envolvidos
- `app/utils/data_analyzer.py`
- `app/models/session.py`
- `app/services/pipeline_service.py`
- `app/services/llm_service.py`
- `prompts/`
- `static/js/app.js`
