# Minimizar dados enviados aos LLMs

Prioridade: Alta

Area: privacidade, prompts, dados

## Problema

O pipeline envia metadados e amostras de dados para provedores LLM externos. As amostras podem conter informacoes pessoais, segredos comerciais ou dados regulados. O projeto nao tem classificacao, redacao, mascaramento ou consentimento explicito por dataset/campo.

## Evidencia local

- `app/utils/data_analyzer.py` inclui `sample: df.head(8).to_dict(...)` nos metadados.
- `metadata_json` e `raw_metadata_json` passam esses metadados para prompts.
- Prompts de artifacts, metricas, textos, filtros e exploracao recebem `Data Artifacts`.
- PDF context tambem envia `text_preview`.

## Mudanca a fazer

Adicionar uma camada de minimizacao e redacao:

- detectar possiveis PII/secrets por nome de coluna e padroes de valor;
- mascarar ou omitir samples por padrao em campos sensiveis;
- permitir modo "metadata only" sem amostras;
- registrar no UI quais dados serao enviados ao provider;
- permitir aprovacao/opt-in para incluir amostras;
- documentar os fluxos de dados por provider.

## Criterios de aceite

- Colunas como email, telefone, documento, token, key e endereco nao aparecem em prompts sem mascaramento.
- O usuario consegue gerar relatorio sem enviar linhas de exemplo.
- Testes cobrem mascaramento de colunas sensiveis e preservacao de estatisticas agregadas.
- README inclui nota clara sobre dados enviados a LLMs externos.
