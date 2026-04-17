import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.management import call_command  # noqa: E402
from django.core.wsgi import get_wsgi_application  # noqa: E402

# Vercel: ensure minimum DB schema to avoid 500 right after deploy/login.
if os.getenv("VERCEL") == "1" and os.getenv("AUTO_MIGRATE", "1") == "1":
    try:
        call_command("migrate", interactive=False, run_syncdb=True, verbosity=0)
    except Exception as exc:
        # Keep app booting, but print cause in Vercel logs for diagnosis.
        print(f"[AUTO_MIGRATE] migrate failed: {exc!r}")

app = get_wsgi_application()