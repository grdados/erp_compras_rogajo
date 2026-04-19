import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import DatabaseError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import CompletarCadastroLicencaForm, RegistroContaForm
from .models import ConfirmacaoEmailCadastro, Licenca, PerfilUsuarioLicenca
from .pricing import valor_anual, valor_mensal, valor_semestral

stripe.api_key = settings.STRIPE_SECRET_KEY


def _periodo_para_valor(periodo):
    if periodo == 'anual':
        return valor_anual(), Licenca.Plano.ANUAL
    return valor_semestral(), Licenca.Plano.SEMESTRAL


def _enviar_email_confirmacao(request, usuario, token_obj):
    confirmar_url = request.build_absolute_uri(reverse('licencas:confirmar_email', kwargs={'token': token_obj.token}))
    logo_url = request.build_absolute_uri(static('img/logo-grdados.png'))
    assunto = 'Confirme seu cadastro - Rogajo'
    contexto = {
        'username': usuario.username,
        'confirmar_url': confirmar_url,
        'logo_url': logo_url,
    }
    mensagem = render_to_string('licencas/emails/confirmacao_email.txt', contexto)
    mensagem_html = render_to_string('licencas/emails/confirmacao_email.html', contexto)
    remetente = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@rogajo.local')
    email = EmailMultiAlternatives(
        subject=assunto,
        body=mensagem,
        from_email=remetente,
        to=[usuario.email],
    )
    email.attach_alternative(mensagem_html, 'text/html')
    politica_path = Path(settings.BASE_DIR) / 'static' / 'docs' / 'politica_privacidade.docx'
    if politica_path.exists():
        email.attach_file(str(politica_path))
    email.send(fail_silently=False)


def registrar(request):
    """Fluxo unico:
    - Anonimo: cria conta e envia confirmacao de email.
    - Logado: completa dados da empresa/pessoa e cria licenca.
    """

    if request.user.is_authenticated:
        if getattr(request.user, 'effective_role', '') == 'ADMIN':
            return redirect('core:dashboard')

        perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()
        if perfil and perfil.licenca:
            if perfil.licenca.pagamento_pendente_expirado:
                perfil.licenca.delete()
                messages.warning(
                    request,
                    'Sua solicitacao expirou por falta de pagamento em 7 dias. Cadastre novamente sua assinatura.',
                )
            else:
                if perfil.licenca.esta_vigente:
                    return redirect('core:dashboard')
                return redirect('licencas:renovar')

        initial = {'email': request.user.email or ''}
        form = CompletarCadastroLicencaForm(request.POST or None, initial=initial)
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    lic = Licenca.objects.create(
                        cliente=form.cleaned_data['cliente'],
                        cpf_cnpj=form.cleaned_data['cpf_cnpj'],
                        endereco=form.cleaned_data['endereco'],
                        numero=form.cleaned_data['numero'],
                        cep=form.cleaned_data['cep'],
                        cidade=form.cleaned_data['cidade'],
                        uf=(form.cleaned_data['uf'] or '').upper(),
                        email=form.cleaned_data['email'],
                        contato=form.cleaned_data['contato'],
                        status=Licenca.Status.EXPIRADA,
                    )
                    PerfilUsuarioLicenca.objects.create(usuario=request.user, licenca=lic)
                messages.success(request, 'Dados da empresa salvos. Agora escolha o plano da assinatura.')
                return redirect('licencas:renovar')
            except DatabaseError:
                messages.error(request, 'Nao foi possivel salvar agora. Tente novamente em instantes.')

        return render(request, 'licencas/registrar.html', {'form': form, 'modo_publico': False})

    form = RegistroContaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        User = get_user_model()
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    password=form.cleaned_data['password1'],
                    email=form.cleaned_data['email'],
                    role=User.Role.SUPERVISOR,
                    is_active=False,
                )
                ConfirmacaoEmailCadastro.objects.filter(usuario=user, usado_em__isnull=True).delete()
                token_obj = ConfirmacaoEmailCadastro.objects.create(
                    usuario=user,
                    token=uuid.uuid4().hex,
                    expira_em=timezone.now() + timedelta(hours=24),
                )
            email_enviado = False
            try:
                _enviar_email_confirmacao(request, user, token_obj)
                email_enviado = True
            except Exception as exc:
                # Em ambientes sem SMTP, evita bloquear o cadastro.
                confirmar_url = request.build_absolute_uri(
                    reverse('licencas:confirmar_email', kwargs={'token': token_obj.token})
                )
                messages.warning(
                    request,
                    'Conta criada, mas o email de confirmacao nao foi enviado. '
                    f'Motivo tecnico: {exc}. Link de confirmacao: {confirmar_url}',
                )
            if email_enviado:
                messages.success(
                    request,
                    'Cadastro criado! Enviamos um email de confirmacao. Confirme para liberar o primeiro login.',
                )
            else:
                messages.info(
                    request,
                    'Cadastro criado com sucesso. Assim que o SMTP estiver liberado, reenvie a confirmacao por email.',
                )
            request.session['login_status_username'] = user.username
            return redirect('/accounts/login/')
        except DatabaseError:
            messages.error(request, 'Nao foi possivel concluir o cadastro agora. Tente novamente.')

    return render(request, 'licencas/registrar.html', {'form': form, 'modo_publico': True})


def primeiro_acesso(request):
    # Compatibilidade com URL antiga
    return redirect('licencas:registrar')


def confirmar_email(request, token):
    confirmacao = get_object_or_404(ConfirmacaoEmailCadastro, token=token)
    if not confirmacao.esta_valida:
        messages.error(request, 'Link de confirmacao invalido ou expirado. Faca um novo cadastro.')
        return redirect('licencas:registrar')

    user = confirmacao.usuario
    user.is_active = True
    user.save(update_fields=['is_active'])
    confirmacao.usado_em = timezone.now()
    confirmacao.save(update_fields=['usado_em', 'updated_at'])
    request.session['force_registrar_user_id'] = str(user.id)
    request.session['login_status_username'] = user.username

    messages.success(request, 'Email confirmado com sucesso. Agora faca login para continuar.')
    return redirect('/accounts/login/')


@require_POST
def reenviar_confirmacao_email(request):
    username = (request.POST.get('username') or '').strip()
    if username:
        request.session['login_status_username'] = username

    if not username:
        messages.warning(request, 'Informe o usuario para reenviar o email de verificacao.')
        return redirect('/accounts/login/')

    User = get_user_model()
    user = User.objects.filter(username__iexact=username).first()
    if not user or user.is_active:
        # Resposta neutra para nao expor existencia de conta.
        messages.info(request, 'Se a conta estiver pendente, enviaremos um novo email de verificacao.')
        return redirect('/accounts/login/')

    ConfirmacaoEmailCadastro.objects.filter(usuario=user, usado_em__isnull=True).delete()
    token_obj = ConfirmacaoEmailCadastro.objects.create(
        usuario=user,
        token=uuid.uuid4().hex,
        expira_em=timezone.now() + timedelta(hours=24),
    )
    try:
        _enviar_email_confirmacao(request, user, token_obj)
        messages.success(request, 'Reenviamos o email de verificacao. Confira sua caixa de entrada e spam.')
    except Exception as exc:
        confirmar_url = request.build_absolute_uri(
            reverse('licencas:confirmar_email', kwargs={'token': token_obj.token})
        )
        messages.warning(
            request,
            'Nao foi possivel enviar o email agora. '
            f'Motivo tecnico: {exc}. Link de confirmacao: {confirmar_url}',
        )
    return redirect('/accounts/login/')


def politica_privacidade(request):
    return render(request, 'licencas/politica_privacidade.html')


@login_required
def renovar_licenca(request):
    """Selecao de plano e pagamento (manual por enquanto; Stripe opcional)."""

    perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()
    licenca = perfil.licenca if perfil else None
    if not licenca:
        return redirect('licencas:registrar')

    if licenca.pagamento_pendente_expirado:
        licenca.delete()
        messages.warning(
            request,
            'Sua solicitacao expirou por falta de pagamento em 7 dias. Faça um novo cadastro de assinatura.',
        )
        return redirect('licencas:registrar')

    def resolve_price_id(periodo: str) -> str:
        periodo = (periodo or 'semestral').strip().lower()
        if periodo == 'anual':
            pid = (os.getenv('STRIPE_PRICE_ID_ANUAL', '') or '').strip() or (licenca.stripe_price_id_anual or '').strip()
        else:
            pid = (os.getenv('STRIPE_PRICE_ID_SEMESTRAL', '') or '').strip() or (licenca.stripe_price_id_semestral or '').strip()
        if not pid:
            pid = (os.getenv('STRIPE_PRICE_ID', '') or '').strip() or (licenca.stripe_price_id or '').strip()
        return pid

    setup_error = None
    manual_pix_key = '67998698159'
    beneficiario = {
        'nome': 'Elvis Correia dos Santos',
        'endereco': 'Av. 22 de Abril, Laguna Carapa/MS',
        'contato': '(67) 99869-8159',
        'pix': manual_pix_key,
    }
    checkout_enabled = True

    if settings.LICENCA_MANUAL_MODE:
        checkout_enabled = False
        setup_error = 'Licenciamento manual ativo. Selecione plano + forma de pagamento para gerar sua solicitacao.'
    elif not settings.STRIPE_SECRET_KEY:
        checkout_enabled = False
        setup_error = 'Stripe ainda nao esta configurado (STRIPE_SECRET_KEY). Contate o suporte para ativar o checkout.'

    if checkout_enabled and not (resolve_price_id('semestral') or resolve_price_id('anual')):
        checkout_enabled = False
        setup_error = 'Price IDs do Stripe nao configurados. Configure STRIPE_PRICE_ID_SEMESTRAL e STRIPE_PRICE_ID_ANUAL.'

    if request.method == 'POST':
        periodo = (request.POST.get('periodo') or 'semestral').strip().lower()
        if periodo not in {'semestral', 'anual'}:
            periodo = 'semestral'

        forma_pagamento = (request.POST.get('forma_pagamento') or 'PIX').strip().upper()
        if forma_pagamento not in {'PIX', 'BOLETO'}:
            forma_pagamento = 'PIX'

        valor_total, plano = _periodo_para_valor(periodo)
        hoje = timezone.localdate()
        venc = hoje + timedelta(days=7)

        if settings.LICENCA_MANUAL_MODE:
            licenca.plano = plano
            licenca.forma_pagamento = forma_pagamento
            licenca.valor_total = valor_total
            licenca.data_emissao = hoje
            licenca.data_vencimento_pagamento = venc
            licenca.status = Licenca.Status.AGUARDANDO_CONFIRMACAO
            licenca.save(
                update_fields=[
                    'plano',
                    'forma_pagamento',
                    'valor_total',
                    'data_emissao',
                    'data_vencimento_pagamento',
                    'status',
                    'updated_at',
                ]
            )

            detalhes_pagamento = (
                f'Empresa/Pessoa: {licenca.cliente}\n'
                f'Plano: {licenca.get_plano_display()}\n'
                f'Forma de pagamento: {licenca.get_forma_pagamento_display()}\n'
                f'Valor total: R$ {valor_total}\n'
                f'Data de emissao: {hoje.strftime("%d/%m/%Y")}\n'
                f'Vencimento para confirmacao: {venc.strftime("%d/%m/%Y")}\n'
            )
            if forma_pagamento == 'PIX':
                detalhes_pagamento += f'\nPIX: {manual_pix_key}\n'
            else:
                detalhes_pagamento += '\nBoleto: sera enviado por email ou WhatsApp.\n'

            try:
                send_mail(
                    'Dados para pagamento da assinatura - Rogajo',
                    detalhes_pagamento,
                    getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@rogajo.local'),
                    [licenca.email],
                    fail_silently=False,
                )
            except Exception:
                messages.warning(request, 'Nao foi possivel enviar o email de pagamento neste ambiente.')

            if forma_pagamento == 'PIX':
                messages.success(
                    request,
                    f'Solicitacao registrada! Status: pagamento pendente ate {venc.strftime("%d/%m/%Y")}. '
                    f'Pague via PIX (chave: {manual_pix_key}).',
                )
            else:
                messages.success(
                    request,
                    f'Solicitacao registrada! Status: pagamento pendente ate {venc.strftime("%d/%m/%Y")}. '
                    'O boleto sera enviado por email ou WhatsApp.',
                )
            return redirect('core:licencas_page')

        price_id = resolve_price_id(periodo)
        if not settings.STRIPE_SECRET_KEY:
            messages.error(request, 'Stripe nao configurado. Contate o suporte para liberar o checkout.')
        elif not price_id:
            messages.error(request, 'Price ID do Stripe nao configurado para o periodo selecionado.')
        else:
            licenca.plano = plano
            licenca.forma_pagamento = forma_pagamento
            licenca.valor_total = valor_total
            licenca.data_emissao = hoje
            licenca.data_vencimento_pagamento = venc
            licenca.status = Licenca.Status.AGUARDANDO_CONFIRMACAO
            licenca.save(
                update_fields=[
                    'plano',
                    'forma_pagamento',
                    'valor_total',
                    'data_emissao',
                    'data_vencimento_pagamento',
                    'status',
                    'updated_at',
                ]
            )

            stripe.api_key = settings.STRIPE_SECRET_KEY
            success_url = request.build_absolute_uri(reverse('licencas:checkout_sucesso'))
            cancel_url = request.build_absolute_uri(reverse('licencas:checkout_cancelado'))
            checkout_session = stripe.checkout.Session.create(
                mode='subscription',
                customer_email=licenca.email,
                line_items=[{'price': price_id, 'quantity': 1}],
                success_url=f'{success_url}?session_id={{CHECKOUT_SESSION_ID}}',
                cancel_url=cancel_url,
                metadata={'licenca_id': str(licenca.id), 'periodo': periodo},
            )
            return redirect(checkout_session.url, permanent=False)

    return render(
        request,
        'licencas/renovar.html',
        {
            'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
            'valor_mensal': valor_mensal(),
            'valor_semestral': valor_semestral(),
            'valor_anual': valor_anual(),
            'checkout_enabled': checkout_enabled,
            'setup_error': setup_error,
            'beneficiario': beneficiario,
            'manual_pix_key': manual_pix_key,
            'manual_mode': settings.LICENCA_MANUAL_MODE,
            'licenca': licenca,
        },
    )


@login_required
def checkout_sucesso(request):
    session_id = request.GET.get('session_id')
    return render(request, 'licencas/sucesso.html', {'session_id': session_id})


@login_required
def checkout_cancelado(request):
    return render(request, 'licencas/cancelado.html')


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if settings.LICENCA_MANUAL_MODE:
        return JsonResponse({'status': 'disabled', 'provider': 'manual'})

    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET', '')

    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        else:
            event = json.loads(payload.decode('utf-8'))
    except Exception:
        return HttpResponse(status=400)

    event_type = event.get('type')
    data = event.get('data', {}).get('object', {})

    if event_type == 'checkout.session.completed':
        _handle_checkout_completed(data)
    elif event_type in ('customer.subscription.updated', 'customer.subscription.created'):
        _handle_subscription_updated(data)
    elif event_type in ('customer.subscription.deleted',):
        _handle_subscription_deleted(data)
    elif event_type in ('invoice.payment_failed',):
        _handle_payment_failed(data)
    elif event_type in ('invoice.paid',):
        _handle_invoice_paid(data)

    return JsonResponse({'status': 'ok'})


def _unix_to_date(ts_value):
    if not ts_value:
        return None
    return datetime.fromtimestamp(ts_value, tz=timezone.get_current_timezone()).date()


def _get_licenca_by_metadata(obj):
    metadata = obj.get('metadata', {}) if isinstance(obj, dict) else {}

    licenca_id = metadata.get('licenca_id')
    if licenca_id:
        return Licenca.objects.filter(id=licenca_id).first()

    subscription_id = obj.get('subscription') or obj.get('id')
    if subscription_id:
        return Licenca.objects.filter(stripe_subscription_id=subscription_id).first()

    customer_id = obj.get('customer')
    if customer_id:
        return Licenca.objects.filter(stripe_customer_id=customer_id).first()

    return None


def _handle_checkout_completed(session):
    licenca = _get_licenca_by_metadata(session)
    if not licenca:
        return

    licenca.stripe_customer_id = session.get('customer', '') or licenca.stripe_customer_id
    licenca.stripe_subscription_id = session.get('subscription', '') or licenca.stripe_subscription_id
    licenca.status = Licenca.Status.ATIVA
    licenca.inicio_vigencia = timezone.localdate()

    if not licenca.fim_vigencia:
        licenca.fim_vigencia = timezone.localdate() + timedelta(days=30)

    licenca.save(
        update_fields=[
            'stripe_customer_id',
            'stripe_subscription_id',
            'status',
            'inicio_vigencia',
            'fim_vigencia',
            'updated_at',
        ]
    )


def _handle_subscription_updated(subscription):
    licenca = _get_licenca_by_metadata(subscription)
    if not licenca:
        return

    licenca.stripe_customer_id = subscription.get('customer', '') or licenca.stripe_customer_id
    licenca.stripe_subscription_id = subscription.get('id', '') or licenca.stripe_subscription_id

    itens = subscription.get('items', {}).get('data', [])
    if itens:
        licenca.stripe_price_id = itens[0].get('price', {}).get('id', '') or licenca.stripe_price_id

    current_period_start = _unix_to_date(subscription.get('current_period_start'))
    current_period_end = _unix_to_date(subscription.get('current_period_end'))

    if current_period_start:
        licenca.inicio_vigencia = current_period_start
    if current_period_end:
        licenca.fim_vigencia = current_period_end

    status = subscription.get('status')
    licenca.status = Licenca.Status.ATIVA if status in ('active', 'trialing') else Licenca.Status.INADIMPLENTE

    licenca.save(
        update_fields=[
            'stripe_customer_id',
            'stripe_subscription_id',
            'stripe_price_id',
            'inicio_vigencia',
            'fim_vigencia',
            'status',
            'updated_at',
        ]
    )


def _handle_subscription_deleted(subscription):
    licenca = _get_licenca_by_metadata(subscription)
    if not licenca:
        return

    licenca.status = Licenca.Status.CANCELADA
    licenca.fim_vigencia = timezone.localdate()
    licenca.save(update_fields=['status', 'fim_vigencia', 'updated_at'])


def _handle_payment_failed(invoice):
    licenca = _get_licenca_by_metadata(invoice)
    if not licenca:
        return

    licenca.status = Licenca.Status.INADIMPLENTE
    licenca.save(update_fields=['status', 'updated_at'])


def _handle_invoice_paid(invoice):
    licenca = _get_licenca_by_metadata(invoice)
    if not licenca:
        return

    licenca.status = Licenca.Status.ATIVA
    licenca.save(update_fields=['status', 'updated_at'])
