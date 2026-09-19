# Validação da implementação

Verificado localmente com Python 3.12 e PostgreSQL 17:

- **68 testes passaram**, incluindo API HTTP, autenticação, RBAC, máquina de estados, pagamentos, fotos e transações.
- Cinco testes em PostgreSQL verificaram concorrência de estoque, entrega, idempotência, limite de pagamento e precisão do resultado financeiro.
- Migrações Alembic aplicadas; `alembic check` confirmou ausência de diferenças entre modelos e banco.
- Compilação dos módulos Python concluída sem erros.
- YAML do Compose e da CI, SVG do logo, exemplos de entrada e rotas documentadas verificados.

Comando para incluir o PostgreSQL na suíte:

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://USUARIO:SENHA@localhost:5432/BANCO_DE_TESTES'
.\.venv\Scripts\python.exe -m pytest -q
```

O banco de testes precisa permitir criar schemas. Cada teste PostgreSQL usa um schema temporário próprio; os testes HTTP usam SQLite em memória. A CI também aplica as migrações no PostgreSQL antes dos testes.

Limites da verificação local: Docker indisponível para executar o build; nenhum navegador conectado para inspeção visual do portal. O HTML, os arquivos estáticos e as decisões do cliente foram verificados por HTTP. Há dois avisos de descontinuação das dependências Starlette/AnyIO na suíte, sem falhas de teste.
