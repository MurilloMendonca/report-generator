# 01 - Ingestao e metadados mais ricos

## Prioridade
Alta

## Problema
A qualidade do relatorio depende muito do contexto enviado ao LLM, mas a extracao atual de metadados ainda e rasa. `app/utils/data_analyzer.py` captura shape, tipos, nulos, cardinalidade, estatisticas numericas basicas e as 5 primeiras linhas. CSV e Excel sao lidos com defaults do pandas, PDF e resumido por uma regex leve, e nao ha deteccao de encoding, separador, sheets, datas, distribuicoes, outliers ou relacoes entre arquivos.

## Impacto
O modelo escolhe indicadores com pouca evidencia, pode errar tipos de graficos, ignora padroes temporais e tende a gerar narrativas genericas. Em datasets grandes ou com formatos reais, tambem aumenta o risco de falha de leitura, timeout, memoria alta e conclusoes baseadas em amostras pouco representativas.

## Evidencias no codigo
- `extract_metadata()` em `app/utils/data_analyzer.py` le o arquivo inteiro e usa apenas `df.head(5)` como amostra.
- Excel usa `pd.read_excel(file_path)` sem listar multiplas abas.
- PDF usa busca binaria por textos entre parenteses, insuficiente para muitos PDFs comprimidos ou estruturados.
- `save_uploaded_files()` em `app/main.py` nao aplica limite de tamanho, tipo aceito ou validacao previa antes de persistir.

## Proposta
Criar uma camada de profiling antes da geracao:

- Detectar encoding, separador, decimal, milhares e problemas de parse em CSV.
- Suportar multiplas abas de Excel e registrar sheet names, shapes e amostras por aba.
- Identificar colunas temporais, categoricas, booleanas, IDs, medidas, percentuais e moedas.
- Gerar distribuicoes resumidas: top categorias, quartis, min/max, media, mediana, desvio, nulos por coluna e cardinalidade proporcional.
- Amostrar de forma estratificada quando o dataset for grande, nao apenas as primeiras linhas.
- Detectar candidatos a chaves e relacoes entre arquivos por nome, cardinalidade e valores comuns.
- Substituir a extracao de PDF por uma biblioteca robusta, como `pypdf` ou `pdfplumber`, com fallback leve.
- Salvar um `data_profile` separado do `artifact` atual para manter compatibilidade com sessoes existentes.

## Criterios de aceite
- Uploads CSV com encoding/separador comuns sao analisados sem configuracao manual.
- Excel com multiplas abas aparece no metadata com uma entrada por aba ou uma estrutura equivalente.
- O LLM recebe top valores, intervalos, datas e indicios de relacao entre arquivos.
- Arquivos grandes usam amostragem e limites configuraveis de memoria/linhas.
- Ha testes com CSV separado por ponto e virgula, Excel multi-sheet e PDF textual.

## Arquivos envolvidos
- `app/utils/data_analyzer.py`
- `app/main.py`
- `app/models/session.py`
- `tests/`
