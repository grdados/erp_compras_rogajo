import re

from django.core.management.base import BaseCommand
from django.db import transaction

from cadastros.models import Produto


def _titlecase_name(value: str) -> str:
    """
    Uppercase first letter of each word, lowercase the remainder.
    Keeps separators (space, slash, hyphen, dot) intact.
    """
    value = (value or "").strip()
    if not value:
        return value

    # Split preserving separators.
    parts = re.split(r"(\s+|/|-|\.)", value)
    out = []
    for p in parts:
        if not p or re.fullmatch(r"(\s+|/|-|\.)", p):
            out.append(p)
            continue
        out.append(p[:1].upper() + p[1:].lower())
    return "".join(out)


class Command(BaseCommand):
    help = "Normaliza nomes dos produtos para Primeira Letra Maiuscula em cada palavra."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Somente simula sem salvar")

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        updated = 0

        qs = Produto.objects.all().only("id", "nome")

        with transaction.atomic():
            for obj in qs.iterator():
                novo_nome = _titlecase_name(obj.nome)
                if novo_nome and novo_nome != obj.nome:
                    updated += 1
                    if not dry_run:
                        obj.nome = novo_nome
                        obj.save(update_fields=["nome", "updated_at"])

            if dry_run:
                transaction.set_rollback(True)

        mode = "DRY-RUN" if dry_run else "APLICADO"
        self.stdout.write(self.style.SUCCESS(f"[{mode}] Produtos ajustados: {updated}"))

