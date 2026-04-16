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

## Deploy no Render (backend completo)
1. Suba este repo no GitHub.
2. No Render: `New +` -> `Web Service` -> conecte o repo.
3. O Render vai ler `render.yaml` automaticamente.
4. Configure variaveis obrigatorias no service:
   - `SECRET_KEY`
   - `DATABASE_URL` (Neon Postgres)
   - `ALLOWED_HOSTS` (inclua seu dominio Render)
   - `CSRF_TRUSTED_ORIGINS` (ex: `https://seu-app.onrender.com`)
   - Stripe: `STRIPE_PUBLIC_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_SEMESTRAL`, `STRIPE_PRICE_ID_ANUAL`
5. Faça deploy e teste:
   - `/`
   - `/accounts/login/`
   - `/dashboard/`

## Deploy no Vercel (teste rapido com Django serverless)
1. No Vercel: `Add New Project` -> selecione este mesmo repo.
2. O projeto ja possui:
   - `vercel.json`
   - `api/index.py`
3. Defina Environment Variables no Vercel:
   - `SECRET_KEY`
   - `DEBUG=False`
   - `DATABASE_URL` (Neon)
   - `ALLOWED_HOSTS=.vercel.app`
   - `CSRF_TRUSTED_ORIGINS=https://*.vercel.app`
   - Stripe (mesmas chaves do Render, se quiser testar checkout)
4. Deploy.

Observacao:
- Para homologacao com mais estabilidade (jobs, uploads, webhook Stripe), prefira o Render.
- Vercel aqui fica como ambiente de teste rapido.
