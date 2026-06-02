# Validar saidas LLM com schemas

Prioridade: Alta

Area: prompts, backend, confiabilidade

## Problema

As respostas dos LLMs sao extraidas com heuristicas simples de code fence e fatias entre o primeiro/ultimo delimitador JSON. Depois disso, parte dos erros e ignorada com retry generico ou fallback silencioso. Isso deixa o pipeline sensivel a pequenas variacoes do modelo e dificulta explicar falhas ao usuario.

## Evidencia local

- `app/services/llm_service.py` usa `strip_code_fence`, `extract_json_payload` e `json.loads`.
- `generate_artifacts`, `generate_all_texts`, `generate_data_filter`, `translate_workspace` e `refine_session_modular` nao compartilham um validador robusto.
- Ha blocos `except:` ou `except: pass` em pontos de parse/refinamento.
- O projeto ja tem modelos Pydantic para sessao, mas nao para cada resposta de prompt.

## Mudanca a fazer

Criar contratos estruturados por tarefa:

- modelos Pydantic para artifact plan, preprocessing plan, textos, metric output, traducao e roteamento de refinamento;
- parser unico que suporte JSON bruto, code fence e erro com diagnostico;
- retry com mensagem de correcao baseada no erro de schema;
- persistencia de `prompt_fingerprint`, provider, modelo, tentativa e erro de validacao na sessao;
- falha explicita quando a saida nao respeita contrato, em vez de fallback silencioso.

## Criterios de aceite

- Cada chamada LLM tem schema validado antes de alterar a sessao.
- Erros de schema aparecem em `session.error` com etapa e campo problematico.
- Testes cobrem JSON invalido, campo ausente, tipo errado, resposta com texto ao redor e retry bem-sucedido.
- Prompts e modelos de schema ficam sincronizados por testes de contrato.
