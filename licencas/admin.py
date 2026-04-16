from django.contrib import admin

from .models import Licenca


@admin.register(Licenca)
class LicencaAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'cpf_cnpj', 'email', 'status', 'inicio_vigencia', 'fim_vigencia')
    list_filter = ('status', 'uf')
    search_fields = ('cliente', 'cpf_cnpj', 'email', 'stripe_subscription_id')
