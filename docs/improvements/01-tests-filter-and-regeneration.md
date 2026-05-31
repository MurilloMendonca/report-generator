# Melhoria 01: Testes de Filtro e Regeneração Seletiva

## Objetivo

Garantir que filtros de dados e ações de atualização seletiva sejam previsíveis, sem depender de inspeção manual ou de chamadas reais ao LLM.

## Escopo

- Testar a detecção de intenção de filtro.
- Testar aplicação de filtro em arquivos tabulares.
- Testar que `build_files_map` usa `filtered_path` quando existe.
- Testar que os uploads originais continuam preservados.
- Testar que `Atualizar textos`, `Atualizar gráficos` e `Atualizar tudo` invalidam apenas o estado esperado.

## Plano de Execução

1. Criar testes unitários para helpers de filtro em `app/main.py`.
2. Criar fixtures pequenas em memória ou arquivos temporários CSV/XLSX.
3. Mockar chamadas LLM quando necessário, especialmente `generate_data_filter`.
4. Criar testes de endpoint para `/api/sessions/{job_id}/regenerate` usando `TestClient` ou chamando funções diretamente.
5. Verificar que campos de sessão mudam corretamente após cada escopo de atualização.

## Resultados Esperados

- Filtros aplicados uma vez antes de métricas/componentes.
- Sessões com filtro ativo apontam para arquivos filtrados.
- Sessões sem filtro continuam usando arquivos originais.
- Atualização seletiva não remove dados desnecessários.
- Regressões em filtro/regeneração são detectadas por teste automatizado.

## Como Testar Efetividade

Comandos esperados:

```bash
venv/bin/python -m unittest discover -s tests
```

Checks específicos:

- Um CSV com linhas `base` e `regional` deve gerar `filtered_path` sem linhas `regional`.
- `build_files_map()` deve retornar o caminho filtrado.
- `Atualizar textos` deve remover apenas componentes `type == "text"`.
- `Atualizar gráficos` deve remover apenas componentes visuais.
- `Atualizar tudo` deve limpar artefatos, template, componentes, métricas e título.

## Riscos

- Testes podem ficar acoplados demais à implementação atual de `app/main.py`.
- Melhor criar helpers menores antes ou durante os testes se o setup ficar pesado.

