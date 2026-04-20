# DEMO Offline Imediata (Banco ja carregado)

## Conteudo do pacote
- Projeto ERP Compras completo
- `db.sqlite3` ja carregado para demonstracao
- `.env` de demo (modo manual)

## Passos no computador do cliente (Windows)
1. Extrair `erp_compras_demo_offline.zip` para uma pasta, ex.: `C:\ERP\erp_compras_demo`.
2. Abrir a pasta extraida.
3. Executar `Instalar_ERP_Local.bat` (primeira vez).
4. Executar `Abrir_ERP_Local.bat`.

## Acesso
- Sistema: `http://127.0.0.1:8000/accounts/login/`

## Observacoes
- Esta versao e para demonstracao local/offline.
- O banco (`db.sqlite3`) ja vem com os dados atuais da maquina que gerou o pacote.
- Para usar Asaas/Stripe em producao, configurar `.env` especifico do ambiente.
