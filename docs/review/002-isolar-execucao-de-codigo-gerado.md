# Isolar execucao de codigo gerado

Prioridade: Critica

Area: seguranca, executor, arquitetura

## Problema

O projeto executa Python gerado por LLM para preprocessamento, metricas, exploracao e renderizacao do PDF. O executor atual aplica uma guarda via `sitecustomize.py`, monkey patch de `open` e bloqueio de sockets Python. Isso reduz risco em scripts simples, mas nao e isolamento forte: codigo gerado ainda pode tentar usar `subprocess`, extensoes nativas, bibliotecas que fazem I/O fora dos wrappers de Python ou tecnicas de bypass do runtime.

## Evidencia local

- `app/services/executor_service.py` cria `SANDBOX_GUARD` em Python e injeta `PYTHONPATH`.
- O sandbox limita `open`, `pathlib.Path.open`, `os.open` e chamadas de socket.
- Nao ha bloqueio explicito de `subprocess`, `os.system`, `ctypes`, carregamento de bibliotecas nativas ou execucao de binarios externos.
- O proprio README reconhece que producao multiusuario deve preferir isolamento mais forte.

## Mudanca a fazer

Migrar a execucao de codigo gerado para isolamento por processo/ambiente:

- executar cada job em container efemero, microVM ou sandbox com seccomp/AppArmor;
- montar apenas uploads/filtered/reports/scripts daquele job;
- remover rede no nivel do sistema, nao so no socket Python;
- aplicar usuario sem privilegios, filesystem read-only por padrao e limites de CPU/memoria/tempo;
- bloquear ou controlar `subprocess` mesmo dentro do processo Python;
- registrar manifesto de arquivos lidos/escritos por job quando possivel.

## Criterios de aceite

- Um script com `subprocess.run(["cat", ".env"])` nao consegue ler segredos.
- Um script com `os.system`, `ctypes` ou binario externo nao consegue acessar fora dos mounts permitidos.
- Cada job so escreve no proprio diretorio de saida.
- Testes de sandbox incluem tentativas de bypass por `subprocess`, `os.system`, `ctypes` e paths relativos.
