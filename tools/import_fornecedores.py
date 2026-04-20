import argparse
import os
import sys
from pathlib import Path

import pandas as pd


def clean(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    if s.lower() in {"nan", "none", "nat"}:
        return ""
    if s.endswith(".0") and s.replace(".", "", 1).isdigit():
        s = s[:-2]
    return s


def take(row, *cols):
    for c in cols:
        if c in row:
            val = clean(row.get(c))
            if val:
                return val
    return ""


def only_digits(v):
    s = clean(v)
    return "".join(ch for ch in s if ch.isdigit())


def main():
    parser = argparse.ArgumentParser(description="Importa fornecedores de planilha XLSX.")
    parser.add_argument("xlsx", help="Caminho da planilha fornecedor.xlsx")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parents[1]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.chdir(base_dir)
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))

    import django
    django.setup()

    from django.db import transaction
    from cadastros.models import Fornecedor

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {xlsx_path}")

    df = pd.read_excel(xlsx_path)

    created = 0
    updated = 0
    skipped = 0

    with transaction.atomic():
        for _, row in df.iterrows():
            fornecedor_id = take(row, "fornecedorID")
            nome = take(row, "fornecedorNome", "fornecedorFantasia")
            cnpj = only_digits(row.get("fornecedorCNPJ"))
            if not cnpj:
                # Gera documento tecnico unico quando CNPJ vier vazio na base origem.
                try:
                    fid = int(float(fornecedor_id))
                except Exception:
                    fid = (_ + 1)
                cnpj = f"99{fid:012d}"
            ie = take(row, "fornecedorIE")
            endereco = take(row, "fornecedorEndereco")
            numero = take(row, "fornecedorNumero")
            cep = only_digits(row.get("FornecedorCEP") or row.get("fornecedorCEP"))[:8]
            cidade = take(row, "fornecedorCidade")
            uf_raw = take(row, "fornecedorUF")
            uf = uf_raw[:2].upper() if uf_raw else ""

            if not nome:
                skipped += 1
                continue

            defaults = {
                "fornecedor": nome[:150],
                "cnpj": cnpj[:18],
                "ie": ie[:30],
                "endereco": endereco[:200],
                "numero": numero[:15],
                "cep": cep[:9],
                "cidade": cidade[:80],
                "uf": uf,
                "status": "ATIVO",
            }

            obj = None
            if cnpj:
                obj = Fornecedor.objects.filter(cnpj=cnpj[:18]).first()
            if obj is None:
                obj = Fornecedor.objects.filter(fornecedor__iexact=nome[:150]).first()

            if obj is None:
                Fornecedor.objects.create(**defaults)
                created += 1
            else:
                changed = False
                for k, v in defaults.items():
                    if getattr(obj, k, "") != v:
                        setattr(obj, k, v)
                        changed = True
                if changed:
                    obj.save()
                    updated += 1
                else:
                    skipped += 1

    total_db = Fornecedor.objects.count()
    print(
        {
            "rows_xlsx": len(df),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "total_fornecedores_db": total_db,
        }
    )


if __name__ == "__main__":
    main()
