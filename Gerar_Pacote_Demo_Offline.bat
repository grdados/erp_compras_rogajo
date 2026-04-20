@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras - Pacote DEMO Offline
echo ==========================================
echo.

set "DIST_DIR=dist"
set "PKG_DIR=%DIST_DIR%\erp_compras_demo_offline"
set "ZIP_FILE=%DIST_DIR%\erp_compras_demo_offline.zip"

if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
if exist "%PKG_DIR%" rmdir /s /q "%PKG_DIR%"
if exist "%ZIP_FILE%" del /f /q "%ZIP_FILE%"

echo [1/4] Copiando arquivos do projeto...
robocopy . "%PKG_DIR%" /E /NFL /NDL /NJH /NJS /NC /NS ^
  /XD .git .venv __pycache__ dist backups staticfiles media .pytest_cache .mypy_cache ^
  /XF *.pyc *.pyo *.log .env

if %ERRORLEVEL% GEQ 8 (
  echo [ERRO] Falha ao copiar arquivos.
  pause
  exit /b 1
)

echo [2/4] Configurando .env de demonstracao...
copy /Y ".env.demo" "%PKG_DIR%\.env" >nul

echo [3/4] Incluindo base SQLite atual (demo pronta)...
if exist "db.sqlite3" (
  copy /Y "db.sqlite3" "%PKG_DIR%\db.sqlite3" >nul
) else (
  echo [ALERTA] db.sqlite3 nao encontrado. Pacote seguira sem base pre-carregada.
)

echo [4/4] Compactando pacote...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%PKG_DIR%\*' -DestinationPath '%ZIP_FILE%' -Force"
if errorlevel 1 (
  echo [ERRO] Falha ao gerar ZIP.
  pause
  exit /b 1
)

echo.
echo Pacote DEMO gerado:
echo %ZIP_FILE%
echo.
echo Entregue ao cliente:
echo  - erp_compras_demo_offline.zip
echo  - Instrucoes_Demo_Offline.md
echo.
pause
endlocal
