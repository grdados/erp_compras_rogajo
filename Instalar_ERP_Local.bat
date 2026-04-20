@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras RogaJo - Instalacao Local
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python nao encontrado no PATH.
  echo Instale o Python 3.11+ e marque a opcao "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Criando ambiente virtual...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERRO] Falha ao criar .venv
    pause
    exit /b 1
  )
) else (
  echo [1/5] Ambiente virtual ja existe.
)

echo [2/5] Atualizando pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/5] Instalando dependencias...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERRO] Falha ao instalar dependencias.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [4/5] Criando .env a partir de .env.example...
  copy /Y ".env.example" ".env" >nul
) else (
  echo [4/5] .env ja existe.
)

echo [5/5] Aplicando migracoes...
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 (
  echo [ERRO] Falha ao executar migracoes.
  pause
  exit /b 1
)

echo.
echo Instalacao concluida com sucesso.
echo Agora execute: Abrir_ERP_Local.bat
echo.
pause
endlocal
