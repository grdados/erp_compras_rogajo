@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras RogaJo - Instalacao Local
echo ==========================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.13 --version >nul 2>nul && set "PYTHON_CMD=py -3.13"
  if not defined PYTHON_CMD py -3.12 --version >nul 2>nul && set "PYTHON_CMD=py -3.12"
  if not defined PYTHON_CMD py -3.11 --version >nul 2>nul && set "PYTHON_CMD=py -3.11"
  if not defined PYTHON_CMD set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERRO] Python nao encontrado no PATH.
  echo Instale o Python 3.11+ e marque a opcao "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

for /f "delims=" %%V in ('%PYTHON_CMD% --version 2^>^&1') do set "PYVER=%%V"
echo [INFO] Python detectado: %PYVER%

if exist ".venv" if not exist ".venv\Scripts\python.exe" (
  echo [AVISO] Ambiente .venv incompleto/corrompido. Recriando...
  rmdir /s /q ".venv"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Criando ambiente virtual...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo [ERRO] Falha ao criar .venv
    echo Tente manualmente no terminal:
    echo   %PYTHON_CMD% -m venv .venv
    echo Se continuar falhando, execute o instalador como Administrador.
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
