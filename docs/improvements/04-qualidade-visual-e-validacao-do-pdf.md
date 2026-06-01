# 04 - Qualidade visual e validacao do PDF final

## Prioridade
Alta

## Problema
A renderizacao final depende de funcoes Python geradas pelo LLM. O sistema possui orientacoes de layout e algumas heuristicas para riscos de legenda/anotacao, mas ainda nao valida se o PDF contem todos os componentes, se graficos ficaram legiveis, se textos foram truncados ou se blocos se sobrepoem.

## Impacto
O pipeline pode terminar como `Concluido` mesmo quando um componente falhou internamente, porque o script montado captura excecoes por componente, imprime o erro e continua gerando o PDF. O usuario recebe um arquivo possivelmente incompleto sem uma falha clara.

## Evidencias no codigo
- `assemble_script()` em `app/services/llm_service.py` envolve cada `render_{id}` em `try/except` e apenas imprime o erro.
- `run_execution()` em `app/services/pipeline_service.py` considera sucesso quando o processo retorna codigo 0.
- `normalize_template()` mantem blocos dentro da pagina, mas nao detecta colisao entre blocos.
- `render_text_block()` tenta ajustar texto ao box, mas pode truncar conteudo sem marcar overflow para revisao.

## Proposta
Adicionar uma etapa de validacao pos-render:

- Fazer o script gerar um manifesto de componentes renderizados com status por ID.
- Falhar a execucao quando um componente obrigatorio nao renderizar.
- Validar template antes da execucao: limites, sobreposicao, area minima por tipo e paginas vazias.
- Extrair texto do PDF e verificar titulo, secoes textuais e presenca minima de conteudo.
- Rasterizar paginas em baixa resolucao para detectar paginas em branco e blocos visualmente vazios.
- Marcar textos truncados e oferecer condensacao/regeneracao direcionada.
- Evoluir para uma biblioteca de componentes deterministica para graficos comuns, usando o LLM mais para escolher configuracoes do que para escrever todo o codigo.

## Criterios de aceite
- Se um componente falhar, a sessao termina em estado de erro ou regeneracao, nao em sucesso silencioso.
- O workspace mostra quais componentes renderizaram, quais falharam e qual foi a causa.
- Layouts com overlap ou blocos pequenos demais sao barrados antes de gerar PDF.
- PDFs canônicos verificam conteudo textual, paginas nao vazias e ausencia de erros impressos no stdout.

## Arquivos envolvidos
- `app/services/llm_service.py`
- `app/services/pipeline_service.py`
- `app/utils/layout.py`
- `tests/canonical/run_canonical_sessions.py`
- `prompts/GENERATE_COMPONENT_CODE.md`
