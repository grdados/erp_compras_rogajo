import csv
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cadastros.models import Cliente, Fornecedor, Produto, Produtor, Safra, Unidade
from compras.models import PedidoCompra, PedidoCompraItem


def _norm_text(value: str) -> str:
    txt = (value or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return " ".join(txt.split())


def _parse_date_br(value: str):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%d/%m/%Y").date()


def _parse_decimal_br(value: str) -> Decimal:
    raw = (value or "").strip().replace(".", "").replace(",", ".")
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


class Command(BaseCommand):
    help = "Importa pedidos e itens a partir de CSV legado."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Caminho absoluto do CSV")
        parser.add_argument("--dry-run", action="store_true", help="Somente simula, sem gravar")

    def handle(self, *args, **options):
        csv_path = Path(options["path"])
        dry_run = options["dry_run"]

        if not csv_path.exists():
            raise CommandError(f"Arquivo nao encontrado: {csv_path}")

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            self.stdout.write(self.style.WARNING("CSV vazio. Nada a importar."))
            return

        required_cols = {
            "PedidoCompra", "Data", "Pedido", "safraID", "clienteID", "ProdutorID",
            "Fornecedor", "Vencimento", "produtoID", "Produto", "quantidade", "preco", "desconto", "total_item",
        }
        missing = [c for c in required_cols if c not in reader.fieldnames]
        if missing:
            raise CommandError(f"CSV sem colunas obrigatorias: {', '.join(missing)}")

        # caches
        safra_by_id = {s.id: s for s in Safra.objects.all()}
        cliente_by_id = {c.id: c for c in Cliente.objects.all()}
        produtor_by_id = {p.id: p for p in Produtor.objects.all()}
        produto_by_id = {p.id: p for p in Produto.objects.all()}
        unidade_default = Unidade.objects.order_by("id").first()
        if not unidade_default:
            raise CommandError("Nao existe Unidade cadastrada para usar como fallback.")

        fornecedor_all = list(Fornecedor.objects.all())
        forn_by_name = {_norm_text(f.fornecedor): f for f in fornecedor_all}
        forn_by_fantasia = {_norm_text(f.fantasia): f for f in fornecedor_all if (f.fantasia or "").strip()}

        # Agrupa itens por PedidoCompra (chave legada)
        grouped = defaultdict(list)
        for row in rows:
            grouped[(row.get("PedidoCompra") or "").strip()].append(row)

        created_pedidos = 0
        updated_pedidos = 0
        created_items = 0
        skipped_rows = 0
        missing_refs = defaultdict(int)

        @transaction.atomic
        def _run_import():
            nonlocal created_pedidos, updated_pedidos, created_items, skipped_rows
            for _, items in grouped.items():
                base = items[0]
                try:
                    data = _parse_date_br(base.get("Data"))
                    venc = _parse_date_br(base.get("Vencimento"))
                except Exception:
                    skipped_rows += len(items)
                    missing_refs["data_invalida"] += len(items)
                    continue

                pedido_num = (base.get("Pedido") or "").strip()
                if not pedido_num:
                    skipped_rows += len(items)
                    missing_refs["pedido_vazio"] += len(items)
                    continue

                safra_id = int((base.get("safraID") or "0").strip() or 0)
                cliente_id = int((base.get("clienteID") or "0").strip() or 0)
                produtor_id = int((base.get("ProdutorID") or "0").strip() or 0)

                safra = safra_by_id.get(safra_id)
                cliente = cliente_by_id.get(cliente_id)
                produtor = produtor_by_id.get(produtor_id)

                if not safra:
                    missing_refs["safra_nao_encontrada"] += len(items)
                    skipped_rows += len(items)
                    continue
                if not cliente:
                    missing_refs["cliente_nao_encontrado"] += len(items)
                    skipped_rows += len(items)
                    continue
                if not produtor:
                    missing_refs["produtor_nao_encontrado"] += len(items)
                    skipped_rows += len(items)
                    continue

                fornecedor_txt = (base.get("Fornecedor") or "").strip()
                fornecedor = (
                    forn_by_name.get(_norm_text(fornecedor_txt))
                    or forn_by_fantasia.get(_norm_text(fornecedor_txt))
                )
                if not fornecedor:
                    missing_refs["fornecedor_nao_encontrado"] += len(items)
                    skipped_rows += len(items)
                    continue

                valor_total_pedido = sum((_parse_decimal_br(it.get("total_item")) for it in items), Decimal("0"))

                pedido_obj, created = PedidoCompra.objects.get_or_create(
                    pedido=pedido_num,
                    produtor=produtor,
                    fornecedor=fornecedor,
                    defaults={
                        "data": data,
                        "safra": safra,
                        "cliente": cliente,
                        "vencimento": venc or data,
                        "valor_total": valor_total_pedido,
                        "saldo_faturar": valor_total_pedido,
                    },
                )

                if created:
                    created_pedidos += 1
                else:
                    changed = False
                    if pedido_obj.data != data and data:
                        pedido_obj.data = data
                        changed = True
                    if pedido_obj.safra_id != safra.id:
                        pedido_obj.safra = safra
                        changed = True
                    if pedido_obj.cliente_id != cliente.id:
                        pedido_obj.cliente = cliente
                        changed = True
                    if venc and pedido_obj.vencimento != venc:
                        pedido_obj.vencimento = venc
                        changed = True
                    if pedido_obj.valor_total != valor_total_pedido:
                        pedido_obj.valor_total = valor_total_pedido
                        changed = True
                    if pedido_obj.saldo_faturar != valor_total_pedido:
                        pedido_obj.saldo_faturar = valor_total_pedido
                        changed = True
                    if changed:
                        pedido_obj.save()
                        updated_pedidos += 1

                # Recria itens do pedido importado para garantir consistencia com CSV
                PedidoCompraItem.objects.filter(pedido_compra=pedido_obj).delete()

                for it in items:
                    produto_id_txt = (it.get("produtoID") or "").strip()
                    produto_id = int(produto_id_txt) if produto_id_txt.isdigit() else 0
                    produto_cadastro = produto_by_id.get(produto_id)
                    produto_nome = (it.get("Produto") or "").strip()

                    if not produto_cadastro and produto_nome:
                        produto_cadastro = Produto.objects.filter(nome__iexact=produto_nome).first()
                    if not produto_cadastro:
                        missing_refs["produto_nao_encontrado"] += 1
                        skipped_rows += 1
                        continue

                    unidade = unidade_default
                    qtd = _parse_decimal_br(it.get("quantidade"))
                    preco = _parse_decimal_br(it.get("preco"))
                    desconto = _parse_decimal_br(it.get("desconto"))
                    total_item = _parse_decimal_br(it.get("total_item"))

                    PedidoCompraItem.objects.create(
                        pedido_compra=pedido_obj,
                        produto=produto_nome or produto_cadastro.nome,
                        produto_cadastro=produto_cadastro,
                        unidade=unidade,
                        quantidade=qtd,
                        preco=preco,
                        desconto=desconto,
                        total_item=total_item,
                    )
                    created_items += 1

        if dry_run:
            with transaction.atomic():
                _run_import()
                transaction.set_rollback(True)
            mode_txt = "DRY-RUN (sem gravar)"
        else:
            _run_import()
            mode_txt = "IMPORTACAO REAL"

        self.stdout.write(self.style.SUCCESS(f"Resultado {mode_txt}:"))
        self.stdout.write(f"- Pedidos criados: {created_pedidos}")
        self.stdout.write(f"- Pedidos atualizados: {updated_pedidos}")
        self.stdout.write(f"- Itens criados: {created_items}")
        self.stdout.write(f"- Linhas ignoradas: {skipped_rows}")
        if missing_refs:
            self.stdout.write("Detalhes ignorados:")
            for k, v in sorted(missing_refs.items(), key=lambda x: x[0]):
                self.stdout.write(f"  - {k}: {v}")
