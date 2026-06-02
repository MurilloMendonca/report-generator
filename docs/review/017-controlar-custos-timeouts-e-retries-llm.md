# Controlar custos, timeouts e retries LLM

Prioridade: Media

Area: LLM, operacao, confiabilidade

## Problema

O pipeline faz varias chamadas LLM por relatorio: planejamento, filtro, titulo, metricas, componentes, textos, condensacao, traducao e autocorrecao. Retries existem, mas nao ha budget por sessao, timeout explicito por chamada, contagem de tokens/custo, cache de respostas ou politica de degradacao quando um provider fica lento.

## Evidencia local

- `call_llm` tem retry simples para 429 e delay exponencial parcial.
- `calculate_metric_value` tenta ate 3 vezes por metrica.
- `run_execution` pode chamar `fix_script` ate 5 tentativas totais.
- O usuario nao ve custo estimado, numero de chamadas, tempo por etapa ou fallback de provider.

## Mudanca a fazer

Adicionar controle operacional de LLM:

- timeout por chamada e por etapa;
- budget maximo de chamadas/tokens por sessao;
- medicao de duracao, tentativas e provider/modelo em cada chamada;
- cache por fingerprint de prompt + metadata quando apropriado;
- estrategia de fallback configuravel entre modelos/provedores;
- UI com status claro para etapas longas e falhas por limite.

## Criterios de aceite

- Uma sessao nao ultrapassa o budget configurado sem erro explicito.
- Cada chamada LLM registra duracao, tentativa e modelo.
- Provider lento ou indisponivel gera erro compreensivel ou fallback configurado.
- Testes simulam 429, timeout e budget excedido.
