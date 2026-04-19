from django.contrib.auth.views import LoginView
from django.utils import timezone

from accounts.models import User
from licencas.models import ConfirmacaoEmailCadastro, PerfilUsuarioLicenca


class StatusLoginView(LoginView):
    template_name = 'registration/login.html'

    def _build_account_status(self, username: str):
        username = (username or '').strip()
        if not username:
            return None

        user = User.objects.filter(username__iexact=username).first()
        if not user:
            return {
                'existe': False,
                'status': 'Conta nao encontrada',
                'status_class': 'bg-slate-600/20 text-slate-200 border-slate-300/20',
                'detalhe': 'Verifique o usuario informado ou faca o cadastro.',
                'can_resend': False,
            }

        if not user.is_active:
            conf = ConfirmacaoEmailCadastro.objects.filter(usuario=user, usado_em__isnull=True).order_by('-created_at').first()
            detalhe = 'Seu cadastro existe, mas o email ainda nao foi confirmado.'
            if conf and conf.expira_em:
                detalhe = f'Confirme seu email para liberar o primeiro login. Link expira em {timezone.localtime(conf.expira_em).strftime("%d/%m/%Y %H:%M")}. '
            return {
                'existe': True,
                'status': 'Aguardando confirmacao de email',
                'status_class': 'bg-amber-500/20 text-amber-100 border-amber-300/40',
                'detalhe': detalhe,
                'can_resend': True,
            }

        perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=user).first()
        lic = perfil.licenca if perfil else None
        if not lic:
            return {
                'existe': True,
                'status': 'Sem assinatura cadastrada',
                'status_class': 'bg-slate-600/20 text-slate-200 border-slate-300/20',
                'detalhe': 'Acesse Registrar para cadastrar a assinatura.',
                'can_resend': False,
            }

        if lic.status == lic.Status.AGUARDANDO_CONFIRMACAO:
            status = 'Aguardando pagamento'
            status_class = 'bg-rose-500/20 text-rose-100 border-rose-300/40'
            detalhe = 'Pagamento pendente. Apos validacao do pagamento, o acesso sera liberado.'
        elif lic.status == lic.Status.ATIVA and lic.esta_vigente:
            status = 'Ativo'
            status_class = 'bg-emerald-500/20 text-emerald-100 border-emerald-300/40'
            detalhe = 'Conta ativa e pronta para acesso ao sistema.'
        else:
            status = lic.get_status_display()
            status_class = 'bg-amber-500/20 text-amber-100 border-amber-300/40'
            detalhe = 'No momento o acesso completo esta restrito ao painel de licenca.'

        return {
            'existe': True,
            'status': status,
            'status_class': status_class,
            'detalhe': detalhe,
            'can_resend': False,
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        username = ''
        form = ctx.get('form')
        if form is not None and getattr(form, 'data', None):
            username = (form.data.get('username') or '').strip()

        if not username:
            username = (self.request.session.pop('login_status_username', '') or '').strip()

        ctx['status_lookup_username'] = username
        ctx['account_status'] = self._build_account_status(username)
        return ctx
