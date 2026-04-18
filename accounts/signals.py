from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def garantir_superusuario_padrao(sender, **kwargs):
    User = get_user_model()
    username = getattr(settings, 'ADMIN_DEFAULT_USERNAME', '').strip()
    email = getattr(settings, 'ADMIN_DEFAULT_EMAIL', '').strip() or username
    password = getattr(settings, 'ADMIN_DEFAULT_PASSWORD', '').strip()
    if not username or not password:
        return

    user = User.objects.filter(username=username).first()
    if user:
        changed = False
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if email and user.email != email:
            user.email = email
            changed = True
        if changed:
            user.save()
        return

    User.objects.create_superuser(
        username=username,
        email=email,
        password=password,
    )
