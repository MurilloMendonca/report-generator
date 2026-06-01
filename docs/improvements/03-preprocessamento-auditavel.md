# 03 - Preprocessamento auditavel e controlavel

## Prioridade
Alta

## Problema
O preprocessamento e gerado por LLM e aplicado automaticamente quando a heuristica detecta termos como filtrar, excluir, apenas, agregar ou criar coluna. A decisao fica salva em `data_preprocessing`, mas nao ha previa do efeito, confirmacao do usuario, diff detalhado, rollback explicito ou validacao semantica do resultado.

## Impacto
Um filtro mal interpretado pode remover registros relevantes e contaminar todo o relatorio. Como as metricas e graficos passam a usar o CSV preprocessado, o erro parece confiavel no PDF final. Isso e especialmente sensivel em pedidos como "focar em", "sem", "apenas" ou "normalizar", que podem significar analise narrativa e nao transformacao fisica dos dados.

## Evidencias no codigo
- `should_consider_data_filter()` em `app/services/pipeline_service.py` usa uma lista de palavras-chave.
- `apply_data_filter()` executa a funcao gerada e substitui `artifact` pelo metadata do arquivo preprocessado.
- A interface mostra a existencia do preprocessamento, mas nao oferece aprovar/rejeitar, comparar linhas ou reverter sem novo refinamento.

## Proposta
Separar o preprocessamento em duas fases:

1. Planejamento: gerar `needs_preprocessing`, descricao, codigo, arquivos afetados e resultado esperado.
2. Aplicacao: executar em modo preview, mostrar diff e aguardar confirmacao ou permitir autoaplicar apenas em cenarios simples.

O preview deve incluir:

- Linhas antes/depois por arquivo.
- Colunas criadas/removidas.
- Top valores impactados.
- Amostra de registros removidos ou alterados.
- Avisos quando mais de um percentual configuravel de linhas for descartado.
- Botao de aceitar, editar codigo, rejeitar ou refazer o plano.

## Criterios de aceite
- Nenhum filtro destrutivo relevante e aplicado sem registro claro do efeito.
- O usuario consegue baixar original e preprocessado e ver o diff no workspace.
- `data_preprocessing` guarda codigo, descricao, estatisticas de impacto, timestamp, modelo usado e decisao do usuario.
- Ha testes cobrindo preview, rejeicao, reaplicacao apos adicionar arquivos e rollback.

## Arquivos envolvidos
- `app/services/pipeline_service.py`
- `app/services/llm_service.py`
- `app/main.py`
- `app/models/session.py`
- `static/js/app.js`
- `static/js/ui.js`
- `prompts/GENERATE_DATA_FILTER.md`
