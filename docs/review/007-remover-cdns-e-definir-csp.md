# Remover CDNs e definir CSP

Prioridade: Media

Area: frontend, seguranca, operacao

## Problema

A tela principal carrega fontes, HTMX e Highlight.js de CDNs publicos. Isso torna a app dependente de rede externa no browser, dificulta uso offline/interno e impede uma politica CSP restritiva. Tambem ha muitos estilos e handlers inline, o que exigiria `unsafe-inline` em uma CSP.

## Evidencia local

- `static/index.html` carrega Google Fonts, `unpkg.com` e `cdnjs.cloudflare.com`.
- A UI usa atributos `onclick`, `onchange` e muitos `style`.
- Nao ha middleware ou header para `Content-Security-Policy`.
- `templates.html` compartilha a mesma base de autenticacao, mas nao usa um bundle controlado.

## Mudanca a fazer

Vendorizar ou empacotar assets:

- servir fontes, Highlight.js e qualquer dependencia JS a partir de `/static/vendor`;
- remover HTMX se nao estiver sendo usado de fato;
- mover estilos inline para CSS;
- trocar handlers inline por listeners em JS;
- adicionar CSP com `default-src 'self'`, `script-src 'self'`, `style-src 'self'` e ajustes minimos necessarios;
- configurar SRI apenas se algum recurso externo permanecer.

## Criterios de aceite

- A app abre sem acesso a CDNs externas.
- O HTML nao exige `unsafe-inline` para scripts.
- Headers CSP sao enviados nas respostas principais.
- CI ou teste simples verifica ausencia de scripts externos nao aprovados.
