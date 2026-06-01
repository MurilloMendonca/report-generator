# 08 - Contratos LLM, modelos e versionamento de prompts

## Prioridade
Media-alta

## Problema
A integracao com provedores esta concentrada em `call_llm()` e os prompts sao arquivos Markdown sem versao registrada na sessao. Os retornos JSON sao extraidos por cortes simples de texto. Modelos disponiveis estao hardcoded no backend e no frontend, o que tende a ficar desatualizado.

## Impacto
Mudancas de modelo, prompt ou provider podem alterar a qualidade sem rastreabilidade. Erros de parse geram retries caros e pouco informativos. Fica dificil comparar resultados, reproduzir um PDF antigo ou escolher modelos diferentes por etapa.

## Evidencias no codigo
- `DEFAULT_MODEL_BY_PROVIDER` em `app/services/llm_service.py` define modelos padrao.
- `App.models` em `static/js/app.js` replica listas de modelos no frontend.
- `extract_json_payload()` corta entre o primeiro delimitador de abertura e o ultimo de fechamento.
- A sessao salva `provider` e `model_name`, mas nao salva versao do prompt, parametros, latencia ou payload validado por etapa.

## Proposta
Formalizar o contrato LLM:

- Criar adapters por provider com timeout, retries, parametros e capacidades.
- Quando suportado pelo provider, usar modo JSON/schema ou validação estruturada.
- Registrar `prompt_version`, hash do prompt, provider, modelo, parametros e uso por chamada.
- Centralizar catalogo de modelos no backend e servir ao frontend por endpoint.
- Permitir escolher modelos por etapa: planejamento, metricas, graficos, textos, reparo.
- Adicionar cache opcional para chamadas puramente deterministicas com mesmo prompt/contexto.
- Criar suite de regressao de prompts com fixtures e saidas esperadas.
- Separar prompts de decisao, codigo e texto em contratos menores e mais testaveis.

## Criterios de aceite
- Um PDF antigo pode ser auditado com modelo e hash de prompt usados em cada etapa.
- O frontend nao precisa ser editado para adicionar/remover modelos.
- Saidas JSON invalidas produzem erro estruturado e reparo direcionado.
- Mudancas de prompt rodam contra fixtures antes de serem aceitas.

## Arquivos envolvidos
- `app/services/llm_service.py`
- `app/models/session.py`
- `static/js/app.js`
- `prompts/`
- `tests/canonical/`
