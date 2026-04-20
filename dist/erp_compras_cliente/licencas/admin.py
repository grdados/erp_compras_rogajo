from django.contrib import admin

from .models import AtualizacaoSistema, ConfirmacaoEmailCadastro, Licenca


@admin.register(Licenca)
class LicencaAdmin(admin.ModelAdmin):
    list_display = (
        'cliente',
        'cpf_cnpj',
        'email',
        'plano',
        'forma_pagamento',
        'valor_total',
        'status',
        'data_pagamento',
        'data_vencimento_pagamento',
        'inicio_vigencia',
        'fim_vigencia',
    )
    list_filter = ('status', 'plano', 'forma_pagamento', 'uf')
    search_fields = ('cliente', 'cpf_cnpj', 'email', 'stripe_subscription_id')


@admin.register(ConfirmacaoEmailCadastro)
class ConfirmacaoEmailCadastroAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'token', 'expira_em', 'usado_em', 'created_at')
    search_fields = ('usuario__username', 'usuario__email', 'token')


@admin.register(AtualizacaoSistema)
class AtualizacaoSistemaAdmin(admin.ModelAdmin):
    list_display = ('versao', 'titulo', 'obrigatoria', 'ativa', 'publicada_em', 'updated_at')
    list_filter = ('ativa', 'obrigatoria')
    search_fields = ('versao', 'titulo', 'descricao')
