from django.shortcuts import redirect
from django.urls import reverse

from cadastros.models import PerfilUsuarioCliente

from .models import Licenca


class LicencaAtivaMiddleware:
    EXEMPT_PREFIXES = (
        '/admin/',
        '/accounts/login',
        '/accounts/logout',
        '/licencas/renovar',
        '/licencas/checkout/',
        '/licencas/webhook/',
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        if request.user.role == 'ADMIN':
            return self.get_response(request)

        if request.path == reverse('core:landing_page') or request.path.startswith(self.EXEMPT_PREFIXES):
            return self.get_response(request)

        perfil = PerfilUsuarioCliente.objects.select_related('cliente').filter(usuario=request.user).first()
        cliente = perfil.cliente if perfil else None
        if not cliente:
            return redirect('core:landing_page')

        licenca = Licenca.licenca_vigente_cliente(cliente)
        if not licenca or not licenca.esta_vigente:
            if request.path.startswith('/dashboard'):
                return redirect('licencas:renovar')
            return redirect('licencas:renovar')

        return self.get_response(request)
