from django.db import models

from cadastros.models import Cliente, Custo, Fornecedor, Produtor, Safra, Unidade
from core.models import TimestampedModel


class Planejamento(TimestampedModel):
    data = models.DateField()
    safra = models.ForeignKey(Safra, on_delete=models.PROTECT, related_name='planejamentos')
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='planejamentos')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='planejamentos')
    preco_produto = models.DecimalField(max_digits=14, decimal_places=2)
    vencimento = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['-data', '-id']

    def __str__(self):
        return f'Planejamento {self.id} - {self.cliente}'


class PlanejamentoItem(TimestampedModel):
    planejamento = models.ForeignKey(Planejamento, on_delete=models.CASCADE, related_name='itens')
    area_ha = models.DecimalField('Area HA', max_digits=12, decimal_places=2)
    produto = models.CharField(max_length=120)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    preco = models.DecimalField(max_digits=14, decimal_places=2)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=14, decimal_places=2)
    custo_ha = models.DecimalField('Custo HA', max_digits=14, decimal_places=2, default=0)

    def __str__(self):
        return f'{self.produto} - {self.planejamento_id}'


class PedidoCompra(TimestampedModel):
    data = models.DateField()
    pedido = models.CharField(max_length=40, unique=True)
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='pedidos_compra')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='pedidos_compra')
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='pedidos_compra')
    preco = models.DecimalField(max_digits=14, decimal_places=2)
    produto = models.CharField(max_length=120)
    vencimento = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Pedido de Compra'
        verbose_name_plural = 'Pedidos de Compra'

    def __str__(self):
        return self.pedido


class PedidoCompraItem(TimestampedModel):
    pedido_compra = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name='itens')
    produto = models.CharField(max_length=120)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco = models.DecimalField(max_digits=14, decimal_places=2)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=14, decimal_places=2)

    def __str__(self):
        return f'{self.produto} - {self.pedido_compra}'


class CotacaoProduto(TimestampedModel):
    data = models.DateField()
    safra = models.ForeignKey(Safra, on_delete=models.PROTECT, related_name='cotacoes')
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name='cotacoes')
    vencimento = models.DateField()
    produto = models.CharField(max_length=120)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Cotacao de Produto'
        verbose_name_plural = 'Cotacoes de Produtos'

    def __str__(self):
        return f'Cotacao {self.id} - {self.produto}'
