from django.contrib import admin

from .models import ContaPagar, Faturamento, FaturamentoItem


class FaturamentoItemInline(admin.TabularInline):
    model = FaturamentoItem
    extra = 1


@admin.register(Faturamento)
class FaturamentoAdmin(admin.ModelAdmin):
    list_display = ('nota_fiscal', 'data', 'pedido', 'cliente', 'produtor', 'vencimento', 'valor_total')
    list_filter = ('cliente', 'produtor')
    search_fields = ('nota_fiscal',)
    inlines = [FaturamentoItemInline]


@admin.action(description='Marcar contas selecionadas como pagas (lote)')
def marcar_como_pago(modeladmin, request, queryset):
    queryset.pendentes().pagar_em_lote()


@admin.register(ContaPagar)
class ContaPagarAdmin(admin.ModelAdmin):
    list_display = ('nota_fiscal', 'data', 'cliente', 'produtor', 'vencimento', 'valor_total', 'pago')
    list_filter = ('pago', 'cliente', 'vencimento')
    search_fields = ('nota_fiscal',)
    actions = [marcar_como_pago]
