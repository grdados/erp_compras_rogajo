from django import template

from accounts.models import User
from licencas.models import Licenca

register = template.Library()


@register.simple_tag
def empresa_cadastrada():
    return Licenca.objects.exists()


@register.simple_tag
def resumo_login_licenca():
    """
    Dados para o bloco da tela de login:
    - Se existir usuario cliente (SUPERVISOR) + licenca, mostramos resumo.
    - Caso contrario, mostramos orientacao de registro com validador visual de senha.
    """
    tem_cliente = User.objects.filter(role=User.Role.SUPERVISOR).exists()
    lic = Licenca.objects.order_by('-updated_at').first()
    mostrar_resumo = bool(tem_cliente and lic)
    return {
        'mostrar_resumo': mostrar_resumo,
        'cliente': lic.cliente if lic else '',
        'cpf_cnpj': lic.cpf_cnpj if lic else '',
        'status': lic.get_status_display() if lic else '',
    }
