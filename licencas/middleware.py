from django.db import DatabaseError
from django.shortcuts import redirect
from django.urls import reverse

from .models import PerfilUsuarioLicenca


class LicencaAtivaMiddleware:
    # Permitimos o acesso ao painel de licencas mesmo expirado/inadimplente
    # para o usuario conseguir ver o status e renovar sem ficar preso em redirect.
    EXEMPT_PREFIXES = (
        '/admin/',
        '/accounts/login',
        '/accounts/logout',
        '/licencas/registrar/',
        '/licencas/confirmar-email/',
        '/licencas/politica-privacidade/',
        '/licencas/renovar',
        '/licencas/checkout/',
        '/licencas/webhook/',
        '/licencas/primeiro-acesso/',
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        if getattr(request.user, 'effective_role', '') == 'ADMIN':
            return self.get_response(request)

        try:
            perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()
            licenca = perfil.licenca if perfil else None
        except DatabaseError:
            # Em ambientes recém-publicados (ex.: Vercel sem migração concluída),
            # não derruba o login com erro 500.
            return self.get_response(request)

        if request.path == reverse('core:landing_page'):
            return self.get_response(request)

        # Tela de licencas so pode abrir apos cadastro inicial da empresa/licenca.
        # No primeiro login, sem licenca, força o usuario para o formulario de registro.
        if request.path.startswith('/app/licencas/'):
            if not licenca:
                return redirect('licencas:registrar')
            return self.get_response(request)

        if request.path.startswith(self.EXEMPT_PREFIXES):
            return self.get_response(request)

        if licenca and licenca.pagamento_pendente_expirado:
            licenca.delete()
            return redirect('licencas:registrar')

        if not licenca:
            return redirect('licencas:registrar')

        if not licenca.esta_vigente:
            # Qualquer status diferente de ATIVA:
            # usuario fica restrito ao painel de Licenca ate validacao.
            return redirect('core:licencas_page')

        return self.get_response(request)
