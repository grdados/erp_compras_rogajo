# Atualizacao do ERP Local (Cliente)

## Objetivo
Atualizar o sistema sem perder dados locais.

O script `Atualizar_ERP_Local.bat` faz:
1. Backup de `.env` e `db.sqlite3`.
2. Copia dos arquivos da nova release.
3. Restauracao de `.env` e `db.sqlite3`.
4. Execucao do instalador da release.

---

## Modo recomendado (release em pasta separada)

1. Extraia a nova release em uma pasta, por exemplo:
   `C:\ERP\release_1.0.3`
2. Abra essa pasta da release.
3. Execute:

```bat
Atualizar_ERP_Local.bat "C:\Codex\GR_Dados\erp_compras"
```

4. Ao finalizar, execute no sistema do cliente:

```bat
Abrir_ERP_Local.bat
```

---

## Modo simples (release ja extraida na propria pasta do ERP)

Se a release foi extraida por cima da pasta atual do ERP:

```bat
Atualizar_ERP_Local.bat
```

---

## Observacoes

- Python suportado no Windows: `3.11`, `3.12` ou `3.13`.
- Python `3.14` nao e suportado atualmente por dependencia de imagem (Pillow).
- Os backups ficam em:
  `...\backups\upgrade_YYYYMMDD_HHMMSS`

