# Melhoria 05: Prompts Conscientes do Layout Mais Cedo

## Objetivo

Reduzir retrabalho e cortes fazendo a IA saber o espaço disponível antes de gerar textos e componentes.

## Escopo

- Passar dimensões dos blocos de texto para `generate_all_texts`.
- Passar dimensões e restrições mais claras para `generate_component_code`.
- Incluir limite estimado de palavras por texto no prompt inicial.
- Manter a etapa de condensação como fallback, não como caminho comum.

## Plano de Execução

1. Criar helper que mapeia `artifact_id -> slot`.
2. Calcular capacidade estimada de cada bloco textual.
3. Alterar `generate_all_texts` para receber `template`.
4. Atualizar `GENERATE_ALL_TEXTS.md` com `Layout Slots`.
5. Atualizar `GENERATE_COMPONENT_CODE.md` com alertas de densidade visual por tamanho.
6. Medir em canônicos se a etapa de condensação passa a ser menos acionada.

## Resultados Esperados

- Textos já nascem mais curtos e proporcionais ao bloco.
- Menos chamadas extras ao LLM.
- PDFs mais consistentes visualmente.
- Menos risco de fallback local encurtar texto de forma mecânica.

## Como Testar Efetividade

Comandos:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --case education_outcomes
```

Checks:

- Logs devem mostrar menos ocorrências de condensação.
- `pdftotext` não deve conter `...`.
- Blocos textuais devem ter corpo suficiente e não passar do espaço.
- Textos devem respeitar aproximadamente `target_words`.

## Riscos

- Prompt muito carregado pode reduzir qualidade textual.
- Estimativa de palavras por bloco é aproximada; manter fallback é necessário.

