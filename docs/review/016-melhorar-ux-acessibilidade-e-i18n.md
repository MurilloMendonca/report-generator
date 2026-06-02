# Melhorar UX, acessibilidade e i18n

Prioridade: Media

Area: interface

## Problema

A interface mistura Portugues e Ingles, usa emojis/inline text em botoes, tem textos com erros como `regerar`, e depende de varias mensagens em strings soltas. Acessibilidade tambem e limitada: botoes iconicos nem sempre tem nome claro, modais precisam de foco/teclado robusto e o layout canvas e essencialmente mouse-only.

## Evidencia local

- `static/index.html` esta em `pt-br`, mas `static/templates.html` esta em `lang="en"` e textos em Ingles.
- Botoes e tabs usam emojis como parte do label.
- `static/js/app.js` usa mensagens como `Salvar e regerar`.
- Drag/resize em `Utils.makeDraggable` depende de `mousedown` e nao contempla teclado/touch.
- Muitos estilos inline dificultam consistencia visual.

## Mudanca a fazer

Padronizar UI e acessibilidade:

- centralizar strings em um dicionario i18n, com Portugues como default se esse for o publico principal;
- corrigir copy e terminologia (`regenerar`, `relatorio`, `template`, `componente`);
- adicionar labels/aria para botoes, dialogs, iframe previews e controles;
- implementar foco inicial e retorno de foco nos dialogs;
- oferecer edicao numerica alternativa ao drag/resize, ja existente parcialmente no template editor, tambem no layout do relatorio;
- mover estilos inline para CSS.

## Criterios de aceite

- Interface principal e template editor usam idioma consistente.
- Fluxos principais funcionam por teclado.
- Botoes iconicos tem nome acessivel.
- Teste Playwright ou axe cobre ao menos a tela principal e o modal de componente.
