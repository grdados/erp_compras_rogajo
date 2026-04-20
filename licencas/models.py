from django.db import models
from django.utils import timezone

from core.models import TimestampedModel


class Licenca(TimestampedModel):
    class Plano(models.TextChoices):
        MENSAL = 'MENSAL', 'Mensal'
        SEMESTRAL = 'SEMESTRAL', 'Semestral'
        ANUAL = 'ANUAL', 'Anual'

    class FormaPagamento(models.TextChoices):
        PIX = 'PIX', 'Pix'
        BOLETO = 'BOLETO', 'Boleto'

    class Status(models.TextChoices):
        ATIVA = 'ATIVA', 'Ativa'
        AGUARDANDO_CONFIRMACAO = 'AGUARDANDO_CONFIRMACAO', 'Aguardando confirmacao'
        CANCELAMENTO_REMOTO_PENDENTE = 'CANCELAMENTO_REMOTO_PENDENTE', 'Cancelamento remoto pendente'
        INADIMPLENTE = 'INADIMPLENTE', 'Inadimplente'
        EXPIRADA = 'EXPIRADA', 'Expirada'
        CANCELADA = 'CANCELADA', 'Cancelada'

    # "Cliente" aqui e o titular da licenca (empresa/conta pagante), nao o cadastro administrativo de clientes.
    cliente = models.CharField('Cliente', max_length=150)
    cpf_cnpj = models.CharField('CPF/CNPJ', max_length=18)
    endereco = models.CharField(max_length=200, blank=True, default='')
    numero = models.CharField(max_length=15, blank=True, default='')
    cep = models.CharField(max_length=9, blank=True, default='')
    cidade = models.CharField(max_length=80, blank=True, default='')
    uf = models.CharField(max_length=2, blank=True, default='')
    email = models.EmailField()
    contato = models.CharField(max_length=80)
    logo = models.ImageField(upload_to='licencas/logos/', blank=True, null=True)
    slogan = models.CharField(max_length=200, blank=True)

    plano = models.CharField(max_length=15, choices=Plano.choices, blank=True, default='')
    forma_pagamento = models.CharField(max_length=10, choices=FormaPagamento.choices, blank=True, default='')
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    data_emissao = models.DateField(blank=True, null=True)
    data_vencimento_pagamento = models.DateField(blank=True, null=True)
    data_pagamento = models.DateField(blank=True, null=True)

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.EXPIRADA)
    inicio_vigencia = models.DateField(blank=True, null=True)
    fim_vigencia = models.DateField(blank=True, null=True)
    stripe_customer_id = models.CharField(max_length=80, blank=True)
    stripe_subscription_id = models.CharField(max_length=80, blank=True)
    stripe_price_id = models.CharField(max_length=80, blank=True)
    stripe_price_id_semestral = models.CharField(max_length=80, blank=True)
    stripe_price_id_anual = models.CharField(max_length=80, blank=True)
    asaas_customer_id = models.CharField(max_length=80, blank=True)
    asaas_payment_id = models.CharField(max_length=80, blank=True)
    asaas_invoice_url = models.URLField(blank=True, default='')
    asaas_bank_slip_url = models.URLField(blank=True, default='')
    asaas_pix_qr_code_url = models.URLField(blank=True, default='')

    class Meta:
        ordering = ['cliente', '-updated_at']

    def __str__(self):
        return f'Licenca - {self.cliente}'

    @property
    def esta_vigente(self):
        if self.status != self.Status.ATIVA:
            return False
        if not self.fim_vigencia:
            return False
        return self.fim_vigencia >= timezone.localdate()

    @property
    def pagamento_pendente_expirado(self):
        if self.status != self.Status.AGUARDANDO_CONFIRMACAO:
            return False
        if not self.data_vencimento_pagamento:
            return False
        return self.data_vencimento_pagamento < timezone.localdate()

    @classmethod
    def licenca_vigente(cls):
        licenca = cls.objects.order_by('-fim_vigencia', '-updated_at').first()
        if not licenca:
            return None
        if licenca.status == cls.Status.ATIVA and licenca.fim_vigencia and licenca.fim_vigencia < timezone.localdate():
            licenca.status = cls.Status.EXPIRADA
            licenca.save(update_fields=['status', 'updated_at'])
        return licenca

    @classmethod
    def licenca_vigente_cliente(cls, cliente=None):
        # Mantido por compatibilidade com middleware legado.
        return cls.licenca_vigente()


class PerfilUsuarioLicenca(TimestampedModel):
    from django.conf import settings

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil_licenca')
    licenca = models.ForeignKey(Licenca, on_delete=models.CASCADE, related_name='usuarios')

    class Meta:
        verbose_name = 'Vinculo Usuario-Licenca'
        verbose_name_plural = 'Vinculos Usuario-Licenca'

    def __str__(self):
        return f'{self.usuario} -> {self.licenca}'


class ConfirmacaoEmailCadastro(TimestampedModel):
    usuario = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='confirmacoes_email')
    token = models.CharField(max_length=80, unique=True)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = 'Confirmacao de Email'
        verbose_name_plural = 'Confirmacoes de Email'
        ordering = ['-created_at']

    @property
    def esta_valida(self):
        return self.usado_em is None and self.expira_em >= timezone.now()
