from django.contrib import admin

from .models import (
    Categoria,
    Cliente,
    Cultura,
    Custo,
    FormaPagamento,
    Fornecedor,
    Operacao,
    PerfilUsuarioCliente,
    Produto,
    Produtor,
    Propriedade,
    Safra,
    Unidade,
)


@admin.register(Safra)
class SafraAdmin(admin.ModelAdmin):
    list_display = ('safra', 'ano', 'cultura', 'data_inicio', 'data_fim', 'status')
    list_filter = ('status', 'ano', 'cultura')
    search_fields = ('safra',)


admin.site.register([Cultura, Custo, Categoria, Unidade, FormaPagamento, Operacao, Produto])


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ('fornecedor', 'cnpj', 'cidade', 'uf', 'status')
    list_filter = ('status', 'uf')
    search_fields = ('fornecedor', 'cnpj')


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'apelido', 'cpf_cnpj', 'status', 'limite_compra')
    list_filter = ('status',)
    search_fields = ('cliente', 'cpf_cnpj', 'apelido')


@admin.register(Produtor)
class ProdutorAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'produtor', 'cpf', 'cidade', 'uf', 'ha', 'status')
    list_filter = ('cliente', 'status', 'uf')
    search_fields = ('cliente__cliente', 'produtor', 'cpf')


@admin.register(Propriedade)
class PropriedadeAdmin(admin.ModelAdmin):
    list_display = ('propriedade', 'produtor', 'ha', 'matricula', 'sicar')
    list_filter = ('produtor',)
    search_fields = ('propriedade', 'matricula', 'sicar')


@admin.register(PerfilUsuarioCliente)
class PerfilUsuarioClienteAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'cliente')
    search_fields = ('usuario__username', 'cliente__cliente')
