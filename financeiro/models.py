from django.db import models

from cadastros.models import Cliente, Custo, Fornecedor, Produto, Produtor, Safra, Unidade
from compras.models import PedidoCompra
from core.models import TimestampedModel


class ContaPagarQuerySet(models.QuerySet):
    def pendentes(self):
        return self.filter(pago=False)

    def pagas(self):
        return self.filter(pago=True)

    def pagar_em_lote(self):
        return self.pendentes().update(pago=True, status=ContaPagar.Status.PAGO, saldo_aberto=0)


class Faturamento(TimestampedModel):
    class Status(models.TextChoices):
        A_RECEBER = 'A_RECEBER', 'A Receber'
        PAGO = 'PAGO', 'Pago'

    data = models.DateField()
    nota_fiscal = models.CharField(max_length=40, unique=True)
    serie = models.CharField(max_length=20, blank=True)
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.PROTECT, related_name='faturamentos', null=True, blank=True)
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='faturamentos', null=True, blank=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='faturamentos')
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='faturamentos', null=True, blank=True)
    safra = models.ForeignKey(Safra, on_delete=models.PROTECT, related_name='faturamentos', null=True, blank=True)
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name='faturamentos', null=True, blank=True)
    vencimento = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.A_RECEBER)

    class Meta:
        ordering = ['-data', '-id']

    def __str__(self):
        return self.nota_fiscal


class FaturamentoItem(TimestampedModel):
    faturamento = models.ForeignKey(Faturamento, on_delete=models.CASCADE, related_name='itens')
    produto = models.CharField(max_length=120, blank=True)
    produto_cadastro = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name='itens_faturamento', null=True, blank=True)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco = models.DecimalField(max_digits=14, decimal_places=5)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=14, decimal_places=2)

    def __str__(self):
        return f'{self.produto} - {self.faturamento}'


class ContaPagar(TimestampedModel):
    class Origem(models.TextChoices):
        PEDIDO = 'PEDIDO', 'Pedido'
        FATURAMENTO = 'FATURAMENTO', 'Faturamento'

    class Status(models.TextChoices):
        A_PAGAR = 'A_PAGAR', 'A Pagar'
        PARCIAL = 'PARCIAL', 'Parcial'
        PAGO = 'PAGO', 'Pago'

    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.PEDIDO)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.A_PAGAR)
    data = models.DateField()
    nota_fiscal = models.CharField(max_length=40)
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.PROTECT, related_name='contas_pagar', null=True, blank=True)
    faturamento = models.OneToOneField(Faturamento, on_delete=models.PROTECT, related_name='conta_pagar', null=True, blank=True)
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='contas_pagar', null=True, blank=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='contas_pagar')
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='contas_pagar', null=True, blank=True)
    safra = models.ForeignKey(Safra, on_delete=models.PROTECT, related_name='contas_pagar', null=True, blank=True)
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name='contas_pagar', null=True, blank=True)
    vencimento = models.DateField()
    quantidade = models.DecimalField(max_digits=14, decimal_places=3, default=1)
    preco = models.DecimalField(max_digits=14, decimal_places=5, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo_aberto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pago = models.BooleanField(default=False)
    detalhes = models.TextField(blank=True)

    objects = ContaPagarQuerySet.as_manager()

    class Meta:
        ordering = ['pago', 'vencimento']
        verbose_name = 'Conta a Pagar'
        verbose_name_plural = 'Contas a Pagar'

    def __str__(self):
        return f'{self.nota_fiscal} - {self.cliente}'

    @property
    def status_efetivo(self):
        """Status considerando vencimento (VENCIDO) sem gravar em banco."""
        from datetime import date

        if not self.pago and (self.saldo_aberto or 0) > 0 and self.vencimento and self.vencimento < date.today():
            return 'VENCIDO'
        return self.status

    @property
    def status_label(self):
        mapping = {
            'A_PAGAR': 'A Pagar',
            'PARCIAL': 'Parcial',
            'PAGO': 'Pago',
            'VENCIDO': 'Vencido',
        }
        return mapping.get(self.status_efetivo, str(self.status_efetivo))

    @property
    def dias_atraso(self):
        from datetime import date

        if self.pago or (self.saldo_aberto or 0) <= 0 or not self.vencimento:
            return 0
        if self.vencimento >= date.today():
            return 0
        return (date.today() - self.vencimento).days
    def pagar(self):
        self.pago = True
        self.saldo_aberto = 0
        self.status = self.Status.PAGO
        self.save(update_fields=['pago', 'saldo_aberto', 'status', 'updated_at'])


class FormaPagamento(models.TextChoices):
    PIX = 'PIX', 'Pix'
    TRANSFERENCIA = 'TRANSFERENCIA', 'Transferencia'
    BOLETO = 'BOLETO', 'Boleto'
    CARTAO = 'CARTAO', 'Cartao'


class PagamentoContaPagar(TimestampedModel):
    conta = models.ForeignKey(ContaPagar, on_delete=models.CASCADE, related_name='pagamentos')
    data = models.DateField()
    forma_pagamento = models.CharField(max_length=20, choices=FormaPagamento.choices)
    valor_bruto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    acrescimo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_liquido = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Pagamento'
        verbose_name_plural = 'Pagamentos'

    def __str__(self):
        return f'Pagamento {self.id} - {self.conta_id}'
