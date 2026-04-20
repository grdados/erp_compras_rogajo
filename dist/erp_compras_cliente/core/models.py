from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BackupSettings(TimestampedModel):
    """
    Simple daily backup scheduler settings.

    Scheduling runs via `python manage.py backup_tick` (Windows Task Scheduler / cron).
    """

    enabled = models.BooleanField(default=True)
    daily_time = models.TimeField(default="02:00")
    retention_days = models.PositiveIntegerField(default=14)
    last_run_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Backup (config)"
        verbose_name_plural = "Backups (config)"

    def __str__(self) -> str:  # pragma: no cover
        return "BackupSettings"


class BackupFile(TimestampedModel):
    STATUS_OK = "OK"
    STATUS_ERROR = "ERROR"

    STATUS_CHOICES = [
        (STATUS_OK, "OK"),
        (STATUS_ERROR, "Erro"),
    ]

    KIND_MANUAL = "MANUAL"
    KIND_AUTO = "AUTO"

    KIND_CHOICES = [
        (KIND_MANUAL, "Manual"),
        (KIND_AUTO, "Agendado"),
    ]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_MANUAL)
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=1024)
    size_bytes = models.BigIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OK)
    log = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Backup (arquivo)"
        verbose_name_plural = "Backups (arquivos)"

    def __str__(self) -> str:  # pragma: no cover
        return self.file_name
