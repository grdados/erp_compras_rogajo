from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backup_utils import create_backup
from core.models import BackupFile, BackupSettings


class Command(BaseCommand):
    help = "Executa o backup diario se estiver no horario configurado (uma vez por dia)."

    def handle(self, *args, **options):
        settings_obj, _ = BackupSettings.objects.get_or_create(pk=1)
        if not settings_obj.enabled:
            self.stdout.write("Backup desativado.")
            return

        now = timezone.localtime()
        scheduled = now.replace(
            hour=settings_obj.daily_time.hour,
            minute=settings_obj.daily_time.minute,
            second=0,
            microsecond=0,
        )

        last = settings_obj.last_run_at
        already_ran_today = bool(last and timezone.localtime(last).date() == now.date())

        if already_ran_today:
            self.stdout.write("Backup ja executado hoje.")
            return

        if now < scheduled:
            self.stdout.write("Ainda nao chegou o horario do backup.")
            return

        rec = create_backup(kind=BackupFile.KIND_AUTO)
        self.stdout.write(f"Backup criado: {rec.file_name}")

