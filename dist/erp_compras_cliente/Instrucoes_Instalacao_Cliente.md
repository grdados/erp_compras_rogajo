# Instalacao no Computador do Cliente (Windows)

## 1) Descompactar
- Extraia `erp_compras_cliente.zip` para uma pasta, por exemplo:
  - `C:\ERP\erp_compras`

## 2) Requisitos
- Python 3.11+ instalado
- Marcar opcao `Add Python to PATH` na instalacao

## 3) Configurar `.env`
- Dentro da pasta do projeto:
  - copie `.env.example` para `.env`
- Preencha no minimo:
  - `SECRET_KEY`
  - `DEBUG=True`
  - `ALLOWED_HOSTS=127.0.0.1,localhost`
  - `DATABASE_URL=sqlite:///db.sqlite3`
  - `LICENCA_BILLING_MODE=manual` (ou `asaas`, se for usar Asaas)
  - Email SMTP (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, etc.)

## 4) Instalar e abrir (modo simples)
- Execute `Instalar_ERP_Local.bat` (primeira vez)
- Depois execute `Abrir_ERP_Local.bat`

## 5) Acesso
- Login: `http://127.0.0.1:8000/accounts/login/`
- Admin (se habilitado): `http://127.0.0.1:8000/admin/`

## 6) Atualizacao futura
- Substituir arquivos pelo pacote novo
- Executar novamente:
  - `python manage.py migrate`

