@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras RogaJo - Atualizacao Local
echo ==========================================
echo.

set "SOURCE_DIR=%~dp0"
if "%SOURCE_DIR:~-1%"=="\" set "SOURCE_DIR=%SOURCE_DIR:~0,-1%"

if not "%~1"=="" (
  set "TARGET_DIR=%~1"
) else (
  set "TARGET_DIR=%SOURCE_DIR%"
)
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

if not exist "%TARGET_DIR%\manage.py" (
  echo [ERRO] Pasta de destino invalida:
  echo        %TARGET_DIR%
  echo Informe o caminho da instalacao atual do cliente.
  echo Exemplo:
  echo   Atualizar_ERP_Local.bat "C:\ERP\erp_compras"
  echo.
  pause
  exit /b 1
)

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%T"
set "BACKUP_DIR=%TARGET_DIR%\backups\upgrade_%TS%"
mkdir "%BACKUP_DIR%" >nul 2>nul

echo [1/4] Fazendo backup de .env e db.sqlite3...
if exist "%TARGET_DIR%\.env" (
  copy /Y "%TARGET_DIR%\.env" "%BACKUP_DIR%\.env" >nul
  echo       - .env salvo em: %BACKUP_DIR%\.env
) else (
  echo       - .env nao encontrado na pasta de destino.
)

if exist "%TARGET_DIR%\db.sqlite3" (
  copy /Y "%TARGET_DIR%\db.sqlite3" "%BACKUP_DIR%\db.sqlite3" >nul
  echo       - db.sqlite3 salvo em: %BACKUP_DIR%\db.sqlite3
) else (
  echo       - db.sqlite3 nao encontrado na pasta de destino.
)

if /I "%SOURCE_DIR%"=="%TARGET_DIR%" (
  echo [2/4] Origem e destino sao a mesma pasta. Pulando copia de arquivos.
) else (
  echo [2/4] Aplicando arquivos da release na pasta do cliente...
  robocopy "%SOURCE_DIR%" "%TARGET_DIR%" /E /NFL /NDL /NJH /NJS /NP ^
    /XD ".git" ".venv" "backups" "__pycache__" ^
    /XF ".env" "db.sqlite3"
  set "RC=%ERRORLEVEL%"
  if %RC% GEQ 8 (
    echo [ERRO] Falha ao copiar arquivos da release. Codigo Robocopy: %RC%
    echo Verifique permissao da pasta de destino.
    echo Backup preservado em: %BACKUP_DIR%
    pause
    exit /b 1
  )
  echo       - Arquivos da release aplicados.
)

echo [3/4] Restaurando .env e db.sqlite3...
if exist "%BACKUP_DIR%\.env" (
  copy /Y "%BACKUP_DIR%\.env" "%TARGET_DIR%\.env" >nul
  echo       - .env restaurado.
)
if exist "%BACKUP_DIR%\db.sqlite3" (
  copy /Y "%BACKUP_DIR%\db.sqlite3" "%TARGET_DIR%\db.sqlite3" >nul
  echo       - db.sqlite3 restaurado.
)

echo [4/4] Executando instalador da release...
if exist "%TARGET_DIR%\Instalar_ERP_Local_v1.0.3.bat" (
  call "%TARGET_DIR%\Instalar_ERP_Local_v1.0.3.bat"
) else (
  call "%TARGET_DIR%\Instalar_ERP_Local.bat"
)
if errorlevel 1 (
  echo [ERRO] Instalacao/atualizacao retornou erro.
  echo Backup disponivel em: %BACKUP_DIR%
  pause
  exit /b 1
)

echo.
echo Atualizacao concluida com sucesso.
echo Backup salvo em: %BACKUP_DIR%
echo Agora execute: Abrir_ERP_Local.bat
echo.
pause
endlocal

