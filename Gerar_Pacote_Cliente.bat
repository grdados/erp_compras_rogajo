@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo  ERP Compras - Geracao de Pacote Cliente
echo ==========================================
echo.

set "DIST_DIR=dist"
set "PKG_DIR=%DIST_DIR%\erp_compras_cliente"
set "ZIP_FILE=%DIST_DIR%\erp_compras_cliente.zip"

if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
if exist "%PKG_DIR%" rmdir /s /q "%PKG_DIR%"
if exist "%ZIP_FILE%" del /f /q "%ZIP_FILE%"

echo [1/3] Copiando arquivos do projeto...
robocopy . "%PKG_DIR%" /E /NFL /NDL /NJH /NJS /NC /NS ^
  /XD .git .venv __pycache__ dist backups staticfiles media .pytest_cache .mypy_cache ^
  /XF db.sqlite3 *.pyc *.pyo *.log .env

if %ERRORLEVEL% GEQ 8 (
  echo [ERRO] Falha ao copiar arquivos.
  pause
  exit /b 1
)

echo [2/3] Compactando pacote...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%PKG_DIR%\*' -DestinationPath '%ZIP_FILE%' -Force"
if errorlevel 1 (
  echo [ERRO] Falha ao gerar ZIP.
  pause
  exit /b 1
)

echo [3/3] Pacote gerado com sucesso:
echo %ZIP_FILE%
echo.
echo Entregue ao cliente:
echo  - erp_compras_cliente.zip
echo  - Instrucoes_Instalacao_Cliente.md
echo.
pause
endlocal
