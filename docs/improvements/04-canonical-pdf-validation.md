# Melhoria 04: Fortalecer Sessões Canônicas e Validação de PDF

## Objetivo

Transformar sessões canônicas em uma suíte de regressão visual/semântica para relatórios gerados por LLM.

## Escopo

- Extrair texto do PDF com `pdftotext`.
- Gerar screenshots com `pdftoppm`.
- Salvar artefatos por execução: JSON de sessão, PDF, PNG, texto extraído e logs.
- Validar sinais objetivos de qualidade.
- Adicionar casos canônicos para filtro, sessão sem prompt e atualização seletiva.

## Plano de Execução

1. Estender `tests/canonical/run_canonical_sessions.py`.
2. Adicionar pasta de artefatos por run, por exemplo `tests/canonical/results/{timestamp}/{case_id}/`.
3. Salvar:
   - `session.json`
   - `report.pdf`
   - `page-1.png`
   - `pdftotext.txt`
   - `checks.json`
4. Adicionar checks:
   - PDF não contém reticências de corte (`...`) em blocos.
   - Cabeçalhos de texto têm corpo abaixo.
   - Título existe e não foi truncado.
   - Campos esperados aparecem em metadata ou texto.
   - Quando houver filtro esperado, `filtered_path` existe e número de linhas muda.
5. Adicionar modo `--save-artifacts` se quiser controlar peso no repositório.

## Resultados Esperados

- Regressões visuais óbvias são detectadas sem inspeção manual.
- Revisão manual fica mais rápida por screenshots e texto extraído.
- Canônicos passam a cobrir comportamento de produto, não só sucesso de execução.

## Como Testar Efetividade

Comandos:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --case sales_ops
venv/bin/python tests/canonical/run_canonical_sessions.py --case pokemon_filter
```

Critérios:

- Artefatos de resultado são criados.
- `pdftotext.txt` contém título e seções.
- `page-1.png` não está vazio.
- Checks falham se o PDF tiver título truncado ou bloco textual vazio.

## Riscos

- `pdftotext`/`pdftoppm` são dependências de sistema, não Python.
- Resultados com LLM real podem variar; checks devem ser robustos, não frágeis.

