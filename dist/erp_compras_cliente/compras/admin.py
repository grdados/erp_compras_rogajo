from django.contrib import admin

from .models import CotacaoProduto, PedidoCompra, PedidoCompraItem, Planejamento, PlanejamentoItem


class PlanejamentoItemInline(admin.TabularInline):
    model = PlanejamentoItem
    extra = 1


@admin.register(Planejamento)
class PlanejamentoAdmin(admin.ModelAdmin):
    list_display = ('id', 'data', 'safra', 'custo', 'cliente', 'vencimento', 'valor_total')
    list_filter = ('safra', 'cliente')
    inlines = [PlanejamentoItemInline]


class PedidoCompraItemInline(admin.TabularInline):
    model = PedidoCompraItem
    extra = 1


@admin.register(PedidoCompra)
class PedidoCompraAdmin(admin.ModelAdmin):
    list_display = ('pedido', 'data', 'cliente', 'produtor', 'vencimento', 'valor_total', 'saldo_faturar', 'status', 'pedido_pago')
    list_filter = ('cliente', 'produtor', 'status', 'pedido_pago')
    search_fields = ('pedido',)
    inlines = [PedidoCompraItemInline]


@admin.register(CotacaoProduto)
class CotacaoProdutoAdmin(admin.ModelAdmin):
    list_display = ('id', 'data', 'safra', 'fornecedor', 'produto', 'vencimento', 'valor_total')
    list_filter = ('safra', 'fornecedor')
