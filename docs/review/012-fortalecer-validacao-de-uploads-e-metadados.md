# Fortalecer validacao de uploads e metadados

Prioridade: Media

Area: backend, dados, seguranca

## Problema

Uploads sao validados principalmente por extensao e tamanho. O conteudo so e analisado depois, durante metadata extraction. Arquivos malformados, zip bombs de planilha, CSVs muito caros de parsear ou PDFs problematicos podem consumir recursos, causar erros tardios ou gerar metadados ruins para o LLM.

## Evidencia local

- `validate_upload` em `app/main.py` checa extensao.
- `copy_upload_with_limit` limita bytes, mas nao valida assinatura/conteudo.
- `extract_metadata` tenta parsear CSV, Excel ou PDF e retorna `{"error": ...}` em falha.
- `read_excel(..., sheet_name=None, nrows=MAX_PROFILE_ROWS)` pode tocar todas as abas.

## Mudanca a fazer

Criar validacao de conteudo antes de aceitar o arquivo na sessao:

- verificar assinatura/MIME real para Excel e PDF;
- limitar numero de abas, colunas, linhas amostradas e tempo de parsing;
- rejeitar arquivos com erro de parse antes de iniciar pipeline, com mensagem amigavel;
- normalizar CSV com detector mais robusto e reportar encoding/separador escolhidos;
- isolar parsing de PDF/Excel com timeout proprio;
- salvar no estado do arquivo um diagnostico estruturado.

## Criterios de aceite

- Uploads malformados retornam erro claro antes de criar ou reprocessar relatorio.
- Excel com abas demais ou conteudo excessivo e rejeitado ou amostrado com limite.
- Testes cobrem extensao falsa, CSV invalido, Excel malformado e PDF sem texto.
- A UI mostra o motivo quando um arquivo nao pode ser usado.
