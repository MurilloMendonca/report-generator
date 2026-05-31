# Melhoria 03: Formalizar Schema de Sessão

## Objetivo

Tornar o estado da sessão explícito e validável, reduzindo inconsistências entre campos como `files`, `raw_artifact`, `filtered_path`, `settings`, `data_filter`, `components` e `pdf_version`.

## Escopo

- Criar modelos Pydantic ou dataclasses para sessão.
- Definir defaults centralizados.
- Criar migração leve para sessões antigas.
- Validar campos críticos antes de salvar.
- Documentar versão de schema.

## Plano de Execução

1. Criar `app/models/session.py`.
2. Definir modelos:
   - `SessionFile`
   - `ReportSettings`
   - `DataFilterState`
   - `ReportSession`
3. Adicionar `schema_version`.
4. Atualizar `create_session`, `get_session` e `update_session` para aplicar defaults/migração.
5. Migrar sessões antigas em leitura, sem alterar destrutivamente arquivos originais.
6. Adicionar testes para sessões antigas e novas.

## Resultados Esperados

- Novas sessões sempre têm estrutura completa.
- Sessões antigas continuam compatíveis.
- Erros de campo ausente aparecem cedo.
- Menos condicionais defensivos espalhados pela pipeline.

## Como Testar Efetividade

Comandos:

```bash
venv/bin/python -m unittest discover -s tests
```

Checks:

- Carregar sessão antiga sem `data_filter` deve retornar um estado explícito com `needs_filter: false`.
- Arquivo antigo com apenas `artifact` deve ganhar compatibilidade com `raw_artifact`.
- `settings.alignment` deve defaultar para `J`.
- `provider` e `model_name` devem existir ou receber defaults.

## Riscos

- Pydantic pode introduzir conversões inesperadas se usado de forma agressiva.
- Migração deve ser conservadora e preservar campos desconhecidos.
