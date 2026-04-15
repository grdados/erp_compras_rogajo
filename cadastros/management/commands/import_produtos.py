import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cadastros.models import Categoria, Custo, Produto


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def _as_str(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _as_status(v: str, default: str) -> str:
    raw = _norm(v)
    if not raw:
        return default
    if raw in {"ativo", "a", "1", "true", "sim"}:
        return "ATIVO"
    if raw in {"inativo", "i", "0", "false", "nao", "não"}:
        return "INATIVO"
    # Try passing through already-valid values
    if raw.upper() in {"ATIVO", "INATIVO"}:
        return raw.upper()
    return default


@dataclass
class ImportRow:
    nome: str
    nome_abreviado: str
    npk: str
    variedade: str
    custo_nome: str
    categoria_nome: str
    status: str


class Command(BaseCommand):
    help = "Importa produtos a partir de CSV/XLSX (upsert por (nome,categoria,variedade))."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Caminho do arquivo (.csv/.xlsx)")
        parser.add_argument("--sheet", default=None, help="Nome da aba (apenas xlsx). Default: primeira.")
        parser.add_argument("--delimiter", default=None, help="Delimitador CSV (default: tenta ';' depois ',').")
        parser.add_argument("--default-categoria", default="Geral", help="Categoria default se coluna nao existir.")
        parser.add_argument("--default-custo", default="Geral", help="Custo default se coluna nao existir.")
        parser.add_argument("--default-status", default="ATIVO", choices=["ATIVO", "INATIVO"])
        parser.add_argument("--dry-run", action="store_true", help="Nao grava no banco (so valida e mostra resumo).")

    def handle(self, *args, **opts):
        path = Path(opts["file"]).expanduser()
        if not path.exists():
            raise CommandError(f"Arquivo nao encontrado: {path}")

        rows = self._load_rows(
            path=path,
            sheet=opts["sheet"],
            delimiter=opts["delimiter"],
            default_categoria=opts["default_categoria"],
            default_custo=opts["default_custo"],
            default_status=opts["default_status"],
        )
        if not rows:
            self.stdout.write(self.style.WARNING("Nenhuma linha valida encontrada."))
            return

        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"[DRY-RUN] Linhas prontas para importar: {len(rows)}"))
            self.stdout.write("Exemplo (primeira linha):")
            r0 = rows[0]
            self.stdout.write(
                f"- nome={r0.nome!r} categoria={r0.categoria_nome!r} custo={r0.custo_nome!r} status={r0.status!r}"
            )
            return

        created_prod = 0
        updated_prod = 0
        created_cat = 0
        created_custo = 0

        with transaction.atomic():
            for r in rows:
                cat, cat_created = Categoria.objects.get_or_create(nome=r.categoria_nome)
                if cat_created:
                    created_cat += 1

                custo, custo_created = Custo.objects.get_or_create(nome=r.custo_nome)
                if custo_created:
                    created_custo += 1

                obj, created = Produto.objects.get_or_create(
                    nome=r.nome,
                    categoria=cat,
                    variedade=r.variedade or "",
                    defaults={
                        "nome_abreviado": r.nome_abreviado,
                        "npk": r.npk,
                        "custo": custo,
                        "status": r.status,
                    },
                )
                if created:
                    created_prod += 1
                else:
                    changed = False
                    for field, val in {
                        "nome_abreviado": r.nome_abreviado,
                        "npk": r.npk,
                        "custo": custo,
                        "status": r.status,
                    }.items():
                        if getattr(obj, field) != val:
                            setattr(obj, field, val)
                            changed = True
                    # Allow updating variedade only if provided and differs
                    if r.variedade is not None and obj.variedade != (r.variedade or ""):
                        obj.variedade = r.variedade or ""
                        changed = True
                    if changed:
                        obj.save(update_fields=["nome_abreviado", "npk", "custo", "status", "variedade", "updated_at"])
                        updated_prod += 1

        self.stdout.write(self.style.SUCCESS("Importacao concluida."))
        self.stdout.write(f"- Produtos criados: {created_prod}")
        self.stdout.write(f"- Produtos atualizados: {updated_prod}")
        self.stdout.write(f"- Categorias criadas: {created_cat}")
        self.stdout.write(f"- Custos criados: {created_custo}")

    def _load_rows(
        self,
        *,
        path: Path,
        sheet: str | None,
        delimiter: str | None,
        default_categoria: str,
        default_custo: str,
        default_status: str,
    ) -> list[ImportRow]:
        suffix = path.suffix.lower()
        if suffix not in {".csv", ".xlsx", ".xls"}:
            raise CommandError("Formato suportado: .csv, .xlsx, .xls")

        try:
            import pandas as pd
        except Exception as e:  # pragma: no cover
            raise CommandError(f"Pandas nao disponivel para leitura: {e}")

        if suffix == ".csv":
            # Try delimiter auto-detect when not provided.
            if delimiter:
                df = pd.read_csv(path, delimiter=delimiter, dtype=str, keep_default_na=False)
            else:
                try:
                    df = pd.read_csv(path, delimiter=";", dtype=str, keep_default_na=False)
                except Exception:
                    df = pd.read_csv(path, delimiter=",", dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(path, sheet_name=sheet or 0, dtype=str, keep_default_na=False)

        # Map columns by normalized name
        cols = { _norm(c): c for c in df.columns }

        def pick(*names: str) -> str | None:
            for n in names:
                k = _norm(n)
                if k in cols:
                    return cols[k]
            return None

        col_nome = pick("produto", "nome", "descricao", "descrição", "item")
        col_abrev = pick("nome abreviado", "abreviado", "abreviacao", "abreviação", "sigla")
        col_npk = pick("npk")
        col_var = pick("variedade")
        col_categoria = pick("categoria")
        col_custo = pick("custo")
        col_status = pick("status")

        if not col_nome:
            raise CommandError(
                "Nao encontrei a coluna do nome do produto. Colunas aceitas: Produto/Nome/Descricao/Item."
            )

        out: list[ImportRow] = []
        for _, row in df.iterrows():
            nome = _as_str(row.get(col_nome, "")).strip()
            if not nome:
                continue

            categoria_nome = _as_str(row.get(col_categoria, "")) or default_categoria
            custo_nome = _as_str(row.get(col_custo, "")) or default_custo

            out.append(
                ImportRow(
                    nome=nome,
                    nome_abreviado=_as_str(row.get(col_abrev, "")),
                    npk=_as_str(row.get(col_npk, "")),
                    variedade=_as_str(row.get(col_var, "")),
                    custo_nome=custo_nome.strip() or default_custo,
                    categoria_nome=categoria_nome.strip() or default_categoria,
                    status=_as_status(_as_str(row.get(col_status, "")), default_status),
                )
            )

        return out
