from django.db import models

from cadastros.models import Cliente, Custo, Produtor, Unidade
from compras.models import PedidoCompra
from core.models import TimestampedModel


class ContaPagarQuerySet(models.QuerySet):
    def pendentes(self):
        return self.filter(pago=False)

    def pagas(self):
        return self.filter(pago=True)

    def pagar_em_lote(self):
        return self.pendentes().update(pago=True)


class Faturamento(TimestampedModel):
    data = models.DateField()
    nota_fiscal = models.CharField(max_length=40, unique=True)
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.PROTECT, related_name='faturamentos')
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='faturamentos')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='faturamentos')
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='faturamentos')
    vencimento = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['-data', '-id']

    def __str__(self):
        return self.nota_fiscal


class FaturamentoItem(TimestampedModel):
    faturamento = models.ForeignKey(Faturamento, on_delete=models.CASCADE, related_name='itens')
    produto = models.CharField(max_length=120)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco = models.DecimalField(max_digits=14, decimal_places=2)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=14, decimal_places=2)

    def __str__(self):
        return f'{self.produto} - {self.faturamento}'


class ContaPagar(TimestampedModel):
    data = models.DateField()
    nota_fiscal = models.CharField(max_length=40)
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.PROTECT, related_name='contas_pagar')
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='contas_pagar')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='contas_pagar')
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='contas_pagar')
    vencimento = models.DateField()
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco = models.DecimalField(max_digits=14, decimal_places=2)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    pago = models.BooleanField(default=False)
    objects = ContaPagarQuerySet.as_manager()

    class Meta:
        ordering = ['pago', 'vencimento']
        verbose_name = 'Conta a Pagar'
        verbose_name_plural = 'Contas a Pagar'

    def __str__(self):
        return f'{self.nota_fiscal} - {self.cliente}'

    def pagar(self):
        self.pago = True
        self.save(update_fields=['pago', 'updated_at'])
