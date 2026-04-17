from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Cria/atualiza superusuario padrao para ambientes local/render."

    def handle(self, *args, **options):
        User = get_user_model()

        username = (getattr(settings, "ADMIN_DEFAULT_USERNAME", None) or "").strip() or "admin"
        email = (getattr(settings, "ADMIN_DEFAULT_EMAIL", None) or "").strip() or "admin@rogajo.local"
        password = (getattr(settings, "ADMIN_DEFAULT_PASSWORD", None) or "").strip() or "Admin@123"

        user, created = User.objects.get_or_create(username=username, defaults={"email": email})
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        if hasattr(User, "Role"):
            user.role = User.Role.ADMIN
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Superusuario criado: {username}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Superusuario atualizado: {username}"))
