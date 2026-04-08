from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class StatusAtivoInativo(models.TextChoices):
    ATIVO = 'ATIVO', 'Ativo'
    INATIVO = 'INATIVO', 'Inativo'


class Cultura(TimestampedModel):
    nome = models.CharField('Cultura', max_length=100, unique=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Cultura'
        verbose_name_plural = 'Culturas'

    def __str__(self):
        return self.nome


class Custo(TimestampedModel):
    nome = models.CharField('Custo', max_length=120, unique=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Categoria(TimestampedModel):
    nome = models.CharField('Categoria', max_length=120, unique=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Unidade(TimestampedModel):
    nome = models.CharField('Unidade', max_length=60)
    volume = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unidade_abreviado = models.CharField('Unidade abreviado', max_length=10)

    class Meta:
        ordering = ['nome']
        unique_together = ('nome', 'unidade_abreviado')

    def __str__(self):
        return f'{self.nome} ({self.unidade_abreviado})'


class FormaPagamento(TimestampedModel):
    pagamento = models.CharField(max_length=120)
    parcelas = models.PositiveIntegerField(default=1)
    prazo = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['pagamento']
        verbose_name = 'Forma de Pagamento'
        verbose_name_plural = 'Formas de Pagamento'

    def __str__(self):
        return self.pagamento


class Operacao(TimestampedModel):
    operacao = models.CharField(max_length=120)
    tipo = models.CharField(max_length=80)

    class Meta:
        ordering = ['operacao']

    def __str__(self):
        return f'{self.operacao} ({self.tipo})'


class Safra(TimestampedModel):
    safra = models.CharField(max_length=120)
    ano = models.PositiveIntegerField()
    cultura = models.ForeignKey(Cultura, on_delete=models.PROTECT, related_name='safras')
    data_inicio = models.DateField()
    data_fim = models.DateField()
    status = models.CharField(max_length=10, choices=StatusAtivoInativo.choices, default=StatusAtivoInativo.ATIVO)

    class Meta:
        ordering = ['-ano', 'safra']
        unique_together = ('safra', 'ano', 'cultura')

    def __str__(self):
        return f'{self.safra}/{self.ano} - {self.cultura}'


class Fornecedor(TimestampedModel):
    fornecedor = models.CharField(max_length=150)
    cnpj = models.CharField(max_length=18, unique=True)
    ie = models.CharField('IE', max_length=30, blank=True)
    endereco = models.CharField(max_length=200)
    numero = models.CharField(max_length=15)
    cep = models.CharField(max_length=9)
    cidade = models.CharField(max_length=80)
    uf = models.CharField(max_length=2)
    status = models.CharField(max_length=10, choices=StatusAtivoInativo.choices, default=StatusAtivoInativo.ATIVO)

    class Meta:
        ordering = ['fornecedor']

    def __str__(self):
        return self.fornecedor


class Cliente(TimestampedModel):
    cliente = models.CharField(max_length=150)
    apelido = models.CharField(max_length=80, blank=True)
    cpf_cnpj = models.CharField('CPF/CNPJ', max_length=18, unique=True)
    status = models.CharField(max_length=10, choices=StatusAtivoInativo.choices, default=StatusAtivoInativo.ATIVO)
    limite_compra = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['cliente']

    def __str__(self):
        return self.cliente


class Produtor(TimestampedModel):
    produtor = models.CharField(max_length=150)
    ie = models.CharField('IE', max_length=30, blank=True)
    cpf = models.CharField(max_length=14, unique=True)
    fazenda = models.CharField(max_length=150)
    cidade = models.CharField(max_length=80)
    uf = models.CharField(max_length=2)
    ha = models.DecimalField('HA', max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=StatusAtivoInativo.choices, default=StatusAtivoInativo.ATIVO)

    class Meta:
        ordering = ['produtor']

    def __str__(self):
        return self.produtor


class Propriedade(TimestampedModel):
    propriedade = models.CharField(max_length=150)
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='propriedades')
    ha = models.DecimalField('HA', max_digits=12, decimal_places=2)
    matricula = models.CharField(max_length=60, blank=True)
    sicar = models.CharField(max_length=60, blank=True)
    localizacao = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['propriedade']

    def __str__(self):
        return f'{self.propriedade} - {self.produtor}'


class PerfilUsuarioCliente(TimestampedModel):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil_cliente')
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='usuarios', null=True, blank=True)

    class Meta:
        verbose_name = 'Vinculo Usuario-Cliente'
        verbose_name_plural = 'Vinculos Usuario-Cliente'

    def __str__(self):
        return f'{self.usuario} -> {self.cliente or "Sem cliente"}'
