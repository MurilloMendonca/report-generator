# Fortalecer autenticacao e segredos

Prioridade: Alta

Area: seguranca, backend, frontend

## Problema

O acesso ao app usa um segredo compartilhado enviado em `X-Access-Token` e armazenado no `localStorage` do navegador. Em desenvolvimento o segredo padrao `change-me` e aceito, e o `docker-compose.yml` tambem define `APP_ACCESS_SECRET` com fallback para `change-me`. Isso e suficiente para uso local controlado, mas e fraco para qualquer ambiente compartilhado ou exposto.

## Evidencia local

- `app/main.py` define `APP_ACCESS_SECRET` com fallback `change-me`.
- `docker-compose.yml` injeta `APP_ACCESS_SECRET: ${APP_ACCESS_SECRET:-change-me}`.
- `static/js/api.js` persiste o segredo em `localStorage` e o envia em todas as chamadas.
- Nao ha logout, expiracao de sessao, rate limiting, bloqueio por tentativas ou trilha de auditoria de login.

## Mudanca a fazer

Substituir o segredo estatico por um fluxo de sessao mais defensivo:

- exigir segredo real sempre que o app rodar por Docker ou fora de `APP_ENV=development`;
- gerar token de sessao curto no login e armazenar em cookie `HttpOnly`, `Secure` e `SameSite`;
- remover o segredo bruto do `localStorage`;
- adicionar logout, expiracao de sessao e rate limit para `/api/auth/login`;
- registrar eventos basicos de autenticacao sem expor o segredo;
- documentar claramente que `change-me` so pode existir em uso local.

## Criterios de aceite

- `docker compose up` falha quando `APP_ACCESS_SECRET` nao esta configurado.
- O navegador nao guarda o segredo em `localStorage`.
- Todas as rotas `/api/*` continuam protegidas.
- Testes cobrem login valido, login invalido, expiracao/logout e rejeicao do segredo padrao fora de desenvolvimento.
