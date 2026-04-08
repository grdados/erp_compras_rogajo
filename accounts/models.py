from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Desenvolvedor'
        SUPERVISOR = 'SUPERVISOR', 'Cliente'
        USUARIO = 'USUARIO', 'Usuario'

    role = models.CharField(max_length=15, choices=Role.choices, default=Role.USUARIO)

    def __str__(self):
        return f'{self.get_full_name() or self.username} ({self.get_role_display()})'
