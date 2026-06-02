# Sanitizar renderizacao HTML

Prioridade: Alta

Area: frontend, seguranca, interface

## Problema

A interface usa `innerHTML` em muitos pontos. Alguns valores vindos de sessao, artefatos, logs, templates e respostas de LLM sao escapados, mas outros entram diretamente no DOM. Como esses valores podem vir de uploads, prompts, LLMs ou codigo gerado, isso cria risco de XSS na propria workspace.

## Evidencia local

- `static/js/ui.js` renderiza logs com `innerHTML` sem `escapeHtml`.
- `renderArtifacts` injeta `art.indicator`, `art.id` e `art.reason` em HTML sem escape consistente.
- `static/js/templates.js` injeta nomes de templates/slots em `innerHTML`.
- `static/index.html` e handlers inline aumentam a superficie de execucao de script.

## Mudanca a fazer

Padronizar renderizacao segura:

- evitar `innerHTML` para conteudo dinamico sempre que possivel;
- usar `textContent` e criacao de elementos para dados de usuario/modelo;
- quando `innerHTML` for inevitavel, aplicar escaping em todos os campos dinamicos;
- remover handlers inline (`onclick`) e registrar eventos por JavaScript;
- adicionar testes ou checagens com payloads contendo `<script>`, atributos `onerror` e HTML malicioso.

## Criterios de aceite

- Artefatos, logs, nomes de arquivo, templates e respostas do explorador renderizam texto malicioso como texto literal.
- Nenhum campo vindo de sessao/LLM e interpolado em `innerHTML` sem escaping.
- Teste automatizado cobre ao menos um payload XSS em logs, artifacts e template editor.
