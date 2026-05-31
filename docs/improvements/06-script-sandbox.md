# Melhoria 06: Sandbox Real para Scripts Gerados

## Status

Implementado como sandbox local em `app/services/executor_service.py`.

O executor agora roda scripts gerados em subprocess com:

- ambiente restrito, sem variáveis de API/secrets;
- timeout configurável;
- diretório temporário dedicado por execução;
- bloqueio de sockets de rede;
- allowlist de leitura para uploads, dados filtrados, scripts, relatórios e bibliotecas Python;
- allowlist de escrita para scripts, dados filtrados, relatórios e temporário da execução;
- retorno auditável com `stdout`, `stderr`, `returncode` e duração.

Observação: isto reduz bastante o risco em desenvolvimento/local, mas não substitui isolamento por container/seccomp em produção multiusuário.

## Objetivo

Reduzir risco de executar código Python gerado por LLM diretamente no ambiente da aplicação.

## Escopo

- Isolar execução de scripts de métrica, exploração, filtro e renderização.
- Limitar tempo, filesystem e rede.
- Definir allowlist de diretórios de entrada/saída.
- Registrar stdout/stderr de forma auditável.

## Plano de Execução

1. Avaliar nível necessário:
   - Local/dev: subprocess com diretório temporário e env restrito.
   - Produção: container isolado por execução.
2. Criar `SandboxExecutor` substituindo ou envolvendo `execute_script`.
3. Bloquear rede no ambiente de execução quando possível.
4. Montar apenas:
   - arquivos de upload necessários como read-only
   - diretório temporário de saída
5. Aplicar timeout por tipo de tarefa.
6. Adicionar testes com scripts maliciosos/inadequados:
   - tentar ler `.env`
   - tentar escrever fora do workspace permitido
   - loop infinito
   - import de rede

## Resultados Esperados

- Código gerado não acessa segredos por acidente.
- Falhas são contidas e retornam erro controlado.
- O app fica mais próximo de uso multiusuário ou produção.

## Como Testar Efetividade

Comandos:

```bash
venv/bin/python -m unittest discover -s tests
```

Checks:

- Script com loop infinito falha por timeout.
- Script que tenta ler `.env` falha ou recebe arquivo inexistente.
- Script que tenta escrever fora do diretório permitido falha.
- Script normal de geração de PDF continua funcionando.

## Riscos

- Sandbox por container aumenta complexidade operacional.
- Restringir demais pode quebrar matplotlib/font cache se não configurar `/tmp`.
