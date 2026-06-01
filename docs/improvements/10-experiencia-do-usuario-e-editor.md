# 10 - Experiencia do usuario, editor e controle manual

## Prioridade
Media

## Problema
A interface ja permite upload, chat, edicao de componentes, layout visual, configuracoes e explorador. Ainda assim, o usuario tem pouco controle sobre etapas intermediarias e pouca protecao contra acoes destrutivas ou erros de edicao.

## Impacto
Quando o resultado nao fica bom, o usuario precisa descobrir se deve editar prompt, componente, layout, codigo ou modelo. Isso reduz confianca no fluxo e aumenta a chance de regenerar tudo quando um ajuste local bastaria.

## Evidencias no codigo
- `static/js/app.js` possui regeneracao por escopo, mas nao ha historico/undo das mudancas.
- `Utils.makeDraggable()` permite mover blocos sem limites, snap ou validacao visual durante o drag.
- O editor de indicador aceita codigo Python livre, mas nao valida assinatura antes de salvar.
- Recent sessions sao listadas, mas nao ha renomear, duplicar, arquivar, excluir ou comparar sessoes.

## Proposta
Evoluir o workspace para um editor assistido:

- Mostrar uma timeline do pipeline com acoes possiveis por etapa.
- Adicionar undo/redo para layout, componentes e settings.
- Implementar snap-to-grid, limites de pagina, avisos de overlap e preview de area util no canvas.
- Validar assinatura e sintaxe do codigo manual antes de salvar.
- Adicionar modo "comparar versoes" entre PDFs gerados.
- Permitir renomear, duplicar, excluir e arquivar sessoes.
- Mostrar sugestoes de melhoria geradas pelo proprio validador: "texto longo", "grafico denso", "coluna ausente".
- Criar estados vazios e erros especificos para upload, provider/modelo e sandbox.

## Criterios de aceite
- O usuario consegue corrigir um grafico ou texto sem regenerar todo o relatorio.
- Layout invalido e sinalizado antes de salvar.
- Codigo manual invalido nao quebra a proxima geracao sem feedback.
- Sessoes antigas podem ser organizadas e reaproveitadas como base.

## Arquivos envolvidos
- `static/js/app.js`
- `static/js/ui.js`
- `static/js/utils.js`
- `static/css/styles.css`
- `app/main.py`
