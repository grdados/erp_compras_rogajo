@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras RogaJo - Inicializacao Local
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Ambiente virtual nao encontrado em .venv
  echo Execute primeiro: Instalar_ERP_Local.bat
  echo.
  pause
  exit /b 1
)

echo [1/3] Aplicando migracoes...
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 (
  echo [ERRO] Falha ao aplicar migracoes.
  echo.
  pause
  exit /b 1
)

echo [2/3] Abrindo navegador...
start "" "http://127.0.0.1:8000/accounts/login/"

echo [3/3] Iniciando servidor local...
echo Para encerrar, feche esta janela ou pressione CTRL+C.
echo.
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000

endlocal
