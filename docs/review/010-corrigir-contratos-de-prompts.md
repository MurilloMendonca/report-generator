# Corrigir contratos de prompts

Prioridade: Alta

Area: prompts, backend

## Problema

Alguns prompts estao desalinhados com o codigo atual. Isso aumenta falhas de roteamento e confunde manutencao. O caso mais direto e `REFINE_MODULAR.md`: ele descreve a categoria `SETTINGS`, mas o formato de saida final omite `SETTINGS`, enquanto `refine_session_modular` espera esse valor. Outro exemplo e `GENERATE_TEMPLATE.md`, que permanece no diretorio de prompts, mas `generate_template` usa a biblioteca local de templates.

## Evidencia local

- `prompts/REFINE_MODULAR.md` lista `Settings (SETTINGS)`, mas termina com `Return ONLY the target keyword (ID, LAYOUT, or ALL)`.
- `app/services/llm_service.py` trata `target == "SETTINGS"`.
- `prompts/GENERATE_TEMPLATE.md` ainda descreve um prompt de layout.
- `app/services/llm_service.py` implementa `generate_template` chamando `build_layout_from_template`, sem carregar `GENERATE_TEMPLATE.md`.
- `README.md` ainda lista `GENERATE_TEMPLATE.md` como prompt usado.

## Mudanca a fazer

Sincronizar prompts, codigo e README:

- corrigir `REFINE_MODULAR.md` para incluir `SETTINGS` no formato de saida;
- remover ou arquivar `GENERATE_TEMPLATE.md` se ele nao for mais usado;
- atualizar README com o fluxo real de template local;
- criar teste que verifica se todo prompt listado no README e realmente carregado, ou esta marcado como legado;
- criar snapshots/fingerprints de prompt ligados aos testes de contrato.

## Criterios de aceite

- O roteador modular pode retornar `SETTINGS` por contrato explicito.
- Nao ha prompt ativo documentado que o codigo nao use.
- Testes falham quando prompt e backend divergem em valores aceitos.
