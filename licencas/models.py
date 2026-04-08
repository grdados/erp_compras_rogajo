from django.db import models
from django.utils import timezone

from cadastros.models import Cliente
from core.models import TimestampedModel


class Licenca(TimestampedModel):
    class Status(models.TextChoices):
        ATIVA = 'ATIVA', 'Ativa'
        INADIMPLENTE = 'INADIMPLENTE', 'Inadimplente'
        EXPIRADA = 'EXPIRADA', 'Expirada'
        CANCELADA = 'CANCELADA', 'Cancelada'

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='licencas')
    cpf_cnpj = models.CharField('CPF/CNPJ', max_length=18)
    endereco = models.CharField(max_length=200)
    numero = models.CharField(max_length=15)
    cep = models.CharField(max_length=9)
    cidade = models.CharField(max_length=80)
    uf = models.CharField(max_length=2)
    email = models.EmailField()
    contato = models.CharField(max_length=80)
    logo = models.ImageField(upload_to='licencas/logos/', blank=True, null=True)
    slogan = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.EXPIRADA)
    inicio_vigencia = models.DateField(blank=True, null=True)
    fim_vigencia = models.DateField(blank=True, null=True)
    stripe_customer_id = models.CharField(max_length=80, blank=True)
    stripe_subscription_id = models.CharField(max_length=80, blank=True)
    stripe_price_id = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ['cliente__cliente', '-updated_at']

    def __str__(self):
        return f'Licenca - {self.cliente}'

    @property
    def esta_vigente(self):
        if self.status != self.Status.ATIVA:
            return False
        if not self.fim_vigencia:
            return False
        return self.fim_vigencia >= timezone.localdate()

    @classmethod
    def licenca_vigente_cliente(cls, cliente):
        licenca = cls.objects.filter(cliente=cliente).order_by('-fim_vigencia', '-updated_at').first()
        if not licenca:
            return None
        if licenca.status == cls.Status.ATIVA and licenca.fim_vigencia and licenca.fim_vigencia < timezone.localdate():
            licenca.status = cls.Status.EXPIRADA
            licenca.save(update_fields=['status', 'updated_at'])
        return licenca
