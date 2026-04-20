from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import zipfile

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .models import BackupFile, BackupSettings


def backup_dir() -> Path:
    d = Path(getattr(settings, "BASE_DIR", Path.cwd())) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_next_run(settings_obj: BackupSettings) -> str:
    if not settings_obj.enabled:
        return "-"

    now = timezone.localtime()
    today_run = now.replace(
        hour=settings_obj.daily_time.hour,
        minute=settings_obj.daily_time.minute,
        second=0,
        microsecond=0,
    )
    if now <= today_run:
        return today_run.strftime("%d/%m/%Y %H:%M")
    return (today_run + timedelta(days=1)).strftime("%d/%m/%Y %H:%M")


def apply_retention(settings_obj: BackupSettings) -> None:
    cutoff = timezone.localtime() - timedelta(days=int(settings_obj.retention_days))
    old = BackupFile.objects.filter(created_at__lt=cutoff)
    for obj in old:
        try:
            p = Path(obj.file_path)
            if p.exists():
                p.unlink()
        except Exception:
            pass
        obj.delete()


def create_backup(*, kind: str) -> BackupFile:
    """
    Create a backup zip containing a JSON dumpdata export.

    The zip contains: dumpdata.json
    """
    settings_obj, _ = BackupSettings.objects.get_or_create(pk=1)

    now = timezone.localtime()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{stamp}.zip"
    backup_path = backup_dir() / backup_name

    excludes = [
        "contenttypes",
        "auth.permission",
        "admin.logentry",
        "sessions.session",
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "dumpdata.json"
        call_command("dumpdata", indent=2, exclude=excludes, output=str(json_path))

        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(json_path, arcname="dumpdata.json")

    size_bytes = backup_path.stat().st_size if backup_path.exists() else 0

    rec = BackupFile.objects.create(
        kind=kind,
        file_name=backup_name,
        file_path=str(backup_path),
        size_bytes=size_bytes,
        status=BackupFile.STATUS_OK,
        log="",
    )

    settings_obj.last_run_at = now
    settings_obj.save(update_fields=["last_run_at", "updated_at"])

    apply_retention(settings_obj)
    return rec


def restore_backup_from_zip(uploaded_file) -> None:
    """
    Destructive restore: flush database and load dumpdata.json from the zip.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "backup.zip"
        with open(zip_path, "wb") as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if "dumpdata.json" not in names:
                raise ValueError("Backup invalido (dumpdata.json nao encontrado).")
            zf.extract("dumpdata.json", path=tmpdir)

        dump_path = Path(tmpdir) / "dumpdata.json"

        call_command("flush", interactive=False)
        call_command("loaddata", str(dump_path))


def parse_daily_time(value: str):
    value = (value or "").strip() or "02:00"
    return datetime.strptime(value, "%H:%M").time()

