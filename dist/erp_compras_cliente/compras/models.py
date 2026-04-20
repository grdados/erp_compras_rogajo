from django.db import models

from cadastros.models import Cliente, Custo, Fornecedor, Produto, Produtor, Safra, Unidade
from core.models import TimestampedModel


class Planejamento(TimestampedModel):
    data = models.DateField()
    safra = models.ForeignKey(Safra, on_delete=models.PROTECT, related_name='planejamentos')
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='planejamentos')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='planejamentos')
    preco_produto = models.DecimalField(max_digits=14, decimal_places=5)
    vencimento = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['-data', '-id']

    def __str__(self):
        return f'Planejamento {self.id} - {self.cliente}'


class PlanejamentoItem(TimestampedModel):
    planejamento = models.ForeignKey(Planejamento, on_delete=models.CASCADE, related_name='itens')
    area_ha = models.DecimalField('Area HA', max_digits=12, decimal_places=2)
    # New: prefer selecting from cadastro de produtos (keeps legacy "produto" text for older rows).
    produto_cadastro = models.ForeignKey(Produto, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_planejamento')
    produto = models.CharField(max_length=120, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    preco = models.DecimalField(max_digits=14, decimal_places=5)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=14, decimal_places=2)
    custo_ha = models.DecimalField('Custo HA', max_digits=14, decimal_places=2, default=0)

    def __str__(self):
        nome = ''
        if self.produto_cadastro_id:
            try:
                nome = self.produto_cadastro.nome
            except Exception:
                nome = ''
        if not nome:
            nome = self.produto or '-'
        return f'{nome} - {self.planejamento_id}'


class StatusPedidoCompra(models.TextChoices):
    PENDENTE = 'PENDENTE', 'Pendente'
    PARCIAL = 'PARCIAL', 'Parcial'
    ENTREGUE = 'ENTREGUE', 'Entregue'
    PG_PENDENTE = 'PG_PENDENTE', 'Pg-Pendente'
    PG_PARCIAL = 'PG_PARCIAL', 'Pg-Parcial'
    PG_ENTREGUE = 'PG_ENTREGUE', 'Pg-Entregue'



class PedidoCompra(TimestampedModel):
    data = models.DateField()
    pedido = models.CharField(max_length=40, unique=True)
    safra = models.ForeignKey(Safra, on_delete=models.PROTECT, related_name='pedidos_compra', null=True, blank=True)
    custo = models.ForeignKey(Custo, on_delete=models.PROTECT, related_name='pedidos_compra', null=True, blank=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='pedidos_compra')
    produtor = models.ForeignKey(Produtor, on_delete=models.PROTECT, related_name='pedidos_compra')
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name='pedidos_compra', null=True, blank=True)
    preco = models.DecimalField(max_digits=14, decimal_places=5, default=0)
    produto = models.CharField(max_length=120, blank=True)
    vencimento = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo_faturar = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pedido_pago = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=StatusPedidoCompra.choices, default=StatusPedidoCompra.PENDENTE)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Pedido de Compra'
        verbose_name_plural = 'Pedidos de Compra'

    def __str__(self):
        return self.pedido


class PedidoCompraItem(TimestampedModel):
    pedido_compra = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name='itens')
    produto = models.CharField(max_length=120, blank=True)
    produto_cadastro = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name='itens_pedido', null=True, blank=True)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco = models.DecimalField(max_digits=14, decimal_places=5)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=14, decimal_places=2, default=0)

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
