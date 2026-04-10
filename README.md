# ERP Compras RogaJo

Base inicial do ERP de Gestao de Compras com:
- Python + Django
- Tailwind CSS (via CDN na landing page e dashboards)
- Docker + Docker Compose
- PostgreSQL local (dev) com suporte a Neon via `DATABASE_URL`
- Stripe Checkout + Webhook para licenciamento

## Perfis de usuario
- `ADMIN` = Desenvolvedor
- `SUPERVISOR` = Cliente
- `USUARIO` = Usuario comum

## Funcionalidades entregues
- Dashboard web fora do Django Admin por perfil de usuario
- Fluxo de licenca com checkout Stripe
- Atualizacao de licenca via webhook Stripe
- Bloqueio de acesso quando licenca expira/inadimplente (exceto renovacao)
- Cadastros e modulos do ERP no Django Admin

## Variaveis de ambiente
Copie `.env.example` para `.env` e preencha:\n- Para rodar local sem Docker, mantenha `DATABASE_URL=sqlite:///db.sqlite3` (padrao).\n- Para rodar local com PostgreSQL no host, use `DATABASE_URL=postgres://postgres:postgres@localhost:5432/erp_compras`.\n- No Docker, o compose ja injeta `DATABASE_URL` com host `db` automaticamente.\n\nPreencha tambem:
- `DATABASE_URL`
- `STRIPE_PUBLIC_KEY`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID`

## Como executar (sem Docker)
1. `python -m venv .venv`
2. `.\.venv\Scripts\activate`
3. `pip install -r requirements.txt`
4. `copy .env.example .env`
5. `python manage.py makemigrations`
6. `python manage.py migrate`
7. `python manage.py createsuperuser`
8. `python manage.py runserver`

## Como executar (Docker)
1. `copy .env.example .env`
2. `docker compose up --build`

## Stripe webhook local
Exemplo com Stripe CLI:
`stripe listen --forward-to localhost:8000/licencas/webhook/stripe/`

## URLs
- Landing page: `http://localhost:8000/`
- Login: `http://localhost:8000/accounts/login/`
- Dashboard: `http://localhost:8000/dashboard/`
- Renovacao de licenca: `http://localhost:8000/licencas/renovar/`
- Admin: `http://localhost:8000/admin/`
