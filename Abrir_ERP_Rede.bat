@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras RogaJo - Modo Rede (Waitress)
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Ambiente virtual nao encontrado em .venv
  echo Execute primeiro: Instalar_ERP_Local_v1.0.3.bat
  echo.
  pause
  exit /b 1
)

set "HOST=0.0.0.0"
set "PORT=8000"

echo [1/3] Aplicando migracoes...
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 (
  echo [ERRO] Falha ao aplicar migracoes.
  echo.
  pause
  exit /b 1
)

echo [2/3] Descobrindo IP local...
for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  set "LOCAL_IP=%%I"
  goto :ip_done
)
:ip_done
set "LOCAL_IP=%LOCAL_IP: =%"
if "%LOCAL_IP%"=="" set "LOCAL_IP=127.0.0.1"
echo       URL local: http://127.0.0.1:%PORT%/accounts/login/
echo       URL rede : http://%LOCAL_IP%:%PORT%/accounts/login/
start "" "http://127.0.0.1:%PORT%/accounts/login/"

echo [3/3] Iniciando servidor Waitress em %HOST%:%PORT%...
echo Para encerrar, feche esta janela ou pressione CTRL+C.
echo.
".venv\Scripts\python.exe" -m waitress --listen=%HOST%:%PORT% config.wsgi:application

endlocal

