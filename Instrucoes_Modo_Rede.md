# ERP em Rede Local (Windows)

## 1) Ajustar o `.env`

No arquivo `.env`, configure (exemplo):

```env
ALLOWED_HOSTS=127.0.0.1,localhost,192.168.0.50
CSRF_TRUSTED_ORIGINS=http://192.168.0.50:8000
```

Troque `192.168.0.50` pelo IP real da maquina servidor.

---

## 2) Instalar/atualizar dependencias

```bat
Instalar_ERP_Local_v1.0.3.bat
```

---

## 3) Iniciar em modo rede

```bat
Abrir_ERP_Rede.bat
```

Esse script usa `waitress` e abre:
- URL local: `http://127.0.0.1:8000/accounts/login/`
- URL rede: `http://IP_DA_MAQUINA:8000/accounts/login/`

---

## 4) Liberar firewall (uma unica vez)

No PowerShell **como Administrador**:

```powershell
New-NetFirewallRule -DisplayName "ERP RogaJo 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private
```

---

## 5) Acessar nos outros computadores

No navegador dos clientes:

```text
http://IP_DA_MAQUINA:8000/accounts/login/
```

