from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Desenvolvedor'
        SUPERVISOR = 'SUPERVISOR', 'Cliente'
        USUARIO = 'USUARIO', 'Usuario'

    role = models.CharField(max_length=15, choices=Role.choices, default=Role.USUARIO)

    @property
    def effective_role(self):
        return self.Role.ADMIN if self.is_superuser else self.role

    def get_effective_role_display(self):
        if self.is_superuser:
            return self.Role.ADMIN.label
        return self.get_role_display()

    def __str__(self):
        return f'{self.get_full_name() or self.username} ({self.get_effective_role_display()})'
