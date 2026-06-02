# Versionar dependencias e modelos

Prioridade: Media

Area: build, operacao, LLM

## Problema

`requirements.txt` lista dependencias sem versoes fixas. O catalogo de modelos LLM fica hardcoded no backend e duplicado no estado inicial do frontend. Isso aumenta risco de builds nao reprodutiveis e divergencia entre UI, backend e provedores.

## Evidencia local

- `requirements.txt` contem nomes sem `==` ou ranges controlados.
- `app/services/llm_service.py` define `MODEL_CATALOG`.
- `static/js/app.js` inicializa a mesma lista de modelos como fallback.
- `list_models.py` lista modelos dinamicamente, mas esse resultado nao alimenta a configuracao da app.

## Mudanca a fazer

Tornar dependencias e modelos configuraveis/reprodutiveis:

- usar lockfile ou constraints (`pip-tools`, `uv`, Poetry ou equivalente);
- separar dependencias runtime e dev/test;
- expor modelos por configuracao ou cache atualizado via provider;
- remover duplicacao de modelos do frontend, usando apenas `/api/models` e fallback minimo;
- validar provider/model selecionado no backend antes de chamar LLM;
- documentar politica de upgrade de modelo.

## Criterios de aceite

- Instalar dependencias em duas maquinas produz o mesmo conjunto de versoes.
- CI usa o lockfile.
- Modelo invalido retorna erro 400 antes de iniciar pipeline.
- UI sempre reflete o catalogo vindo do backend.
