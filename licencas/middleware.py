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
        '/licencas/renovar',
        '/licencas/checkout/',
        '/licencas/webhook/',
        '/licencas/primeiro-acesso/',
        '/app/licencas/',
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

        if request.path == reverse('core:landing_page') or request.path.startswith(self.EXEMPT_PREFIXES):
            return self.get_response(request)

        try:
            perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()
            licenca = perfil.licenca if perfil else None
        except DatabaseError:
            # Em ambientes recém-publicados (ex.: Vercel sem migração concluída),
            # não derruba o login com erro 500.
            return self.get_response(request)

        if not licenca:
            return redirect('licencas:primeiro_acesso')

        if not licenca.esta_vigente:
            return redirect('licencas:renovar')

        return self.get_response(request)
