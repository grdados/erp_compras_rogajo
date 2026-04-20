import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests
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
from .pricing import (
    horas_minimas_melhoria,
    valor_anual,
    valor_hora_tecnica,
    valor_mensal,
    valor_mensal_anual,
    valor_minimo_melhoria,
    valor_semestral,
)

stripe.api_key = settings.STRIPE_SECRET_KEY


def _periodo_para_valor(periodo):
    if periodo == 'anual':
        return valor_anual(), Licenca.Plano.ANUAL
    return valor_semestral(), Licenca.Plano.SEMESTRAL


def _enviar_email_confirmacao(request, usuario, token_obj):
    confirmar_url = request.build_absolute_uri(reverse('licencas:confirmar_email', kwargs={'token': token_obj.token}))
    logo_url = request.build_absolute_uri(static('img/logo-grdados-bk.png'))
    assunto = 'Confirme seu cadastro - GR Dados'
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


def _formatar_valor_br(valor):
    txt = f'{valor:,.2f}'
    return txt.replace(',', 'X').replace('.', ',').replace('X', '.')


def _somente_digitos(v):
    return ''.join(ch for ch in (v or '') if ch.isdigit())


def _add_months(base_date, months):
    month_index = (base_date.month - 1) + int(months)
    year = base_date.year + month_index // 12
    month = (month_index % 12) + 1
    # clamp day by month length
    if month in {1, 3, 5, 7, 8, 10, 12}:
        max_day = 31
    elif month in {4, 6, 9, 11}:
        max_day = 30
    else:
        leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
        max_day = 29 if leap else 28
    day = min(base_date.day, max_day)
    return base_date.replace(year=year, month=month, day=day)


def _asaas_headers():
    return {
        'accept': 'application/json',
        'content-type': 'application/json',
        'access_token': settings.ASAAS_API_KEY,
    }


def _asaas_enabled():
    return bool(getattr(settings, 'LICENCA_ASAAS_MODE', False) and getattr(settings, 'ASAAS_API_KEY', ''))


def _asaas_customer_payload(licenca):
    return {
        'name': licenca.cliente,
        'cpfCnpj': _somente_digitos(licenca.cpf_cnpj),
        'email': licenca.email,
        'mobilePhone': _somente_digitos(licenca.contato),
        'postalCode': _somente_digitos(licenca.cep),
        'address': licenca.endereco,
        'addressNumber': licenca.numero,
        'province': licenca.cidade,
    }


def _asaas_get_or_create_customer(licenca):
    if licenca.asaas_customer_id:
        return licenca.asaas_customer_id

    resp = requests.post(
        f'{settings.ASAAS_BASE_URL}/customers',
        headers=_asaas_headers(),
        json=_asaas_customer_payload(licenca),
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    customer_id = data.get('id')
    if not customer_id:
        raise ValueError('Asaas nao retornou customer id.')
    licenca.asaas_customer_id = customer_id
    licenca.save(update_fields=['asaas_customer_id', 'updated_at'])
    return customer_id


def _asaas_create_payment(licenca, due_date):
    customer_id = _asaas_get_or_create_customer(licenca)
    billing_type = 'PIX' if licenca.forma_pagamento == Licenca.FormaPagamento.PIX else 'BOLETO'
    payload = {
        'customer': customer_id,
        'billingType': billing_type,
        'value': float(licenca.valor_total or 0),
        'dueDate': due_date.strftime('%Y-%m-%d'),
        'description': f'Licenca {licenca.get_plano_display()} - ERP Compras Rogajo',
        'externalReference': f'LIC-{licenca.id}',
    }
    resp = requests.post(
        f'{settings.ASAAS_BASE_URL}/payments',
        headers=_asaas_headers(),
        json=payload,
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    payment_id = data.get('id')
    if not payment_id:
        raise ValueError('Asaas nao retornou payment id.')

    pix_qr_url = ''
    if billing_type == 'PIX':
        try:
            pix_resp = requests.get(
                f'{settings.ASAAS_BASE_URL}/payments/{payment_id}/pixQrCode',
                headers=_asaas_headers(),
                timeout=25,
            )
            if pix_resp.ok:
                pix_data = pix_resp.json() or {}
                pix_qr_url = pix_data.get('encodedImage') or pix_data.get('payload') or ''
        except Exception:
            pix_qr_url = ''

    licenca.asaas_payment_id = payment_id
    licenca.asaas_invoice_url = data.get('invoiceUrl') or ''
    licenca.asaas_bank_slip_url = data.get('bankSlipUrl') or ''
    licenca.asaas_pix_qr_code_url = pix_qr_url
    licenca.save(
        update_fields=[
            'asaas_payment_id',
            'asaas_invoice_url',
            'asaas_bank_slip_url',
            'asaas_pix_qr_code_url',
            'updated_at',
        ]
    )
    return data


def _enviar_email_pagamento_manual(request, licenca, vencimento, chave_pix):
    logo_url = request.build_absolute_uri(static('img/logo-grdados-bk.png'))
    assunto = 'Dados para pagamento da assinatura - GR Dados'
    contexto = {
        'cliente': licenca.cliente,
        'plano': licenca.get_plano_display(),
        'forma_pagamento': licenca.get_forma_pagamento_display(),
        'valor_total': _formatar_valor_br(licenca.valor_total or 0),
        'data_emissao': (licenca.data_emissao or timezone.localdate()).strftime('%d/%m/%Y'),
        'vencimento': vencimento.strftime('%d/%m/%Y'),
        'pix_key': chave_pix,
        'is_pix': licenca.forma_pagamento == Licenca.FormaPagamento.PIX,
        'logo_url': logo_url,
    }
    texto = render_to_string('licencas/emails/pagamento_licenca.txt', contexto)
    html = render_to_string('licencas/emails/pagamento_licenca.html', contexto)
    remetente = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@rogajo.local')
    email = EmailMultiAlternatives(
        subject=assunto,
        body=texto,
        from_email=remetente,
        to=[licenca.email],
    )
    email.attach_alternative(html, 'text/html')
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
    billing_provider = 'manual'

    if getattr(settings, 'LICENCA_ASAAS_MODE', False):
        billing_provider = 'asaas'
        if not _asaas_enabled():
            checkout_enabled = False
            setup_error = 'Asaas nao configurado. Defina ASAAS_API_KEY e ASAAS_BASE_URL.'
    elif getattr(settings, 'LICENCA_STRIPE_MODE', False):
        billing_provider = 'stripe'
        if not settings.STRIPE_SECRET_KEY:
            checkout_enabled = False
            setup_error = 'Stripe ainda nao esta configurado (STRIPE_SECRET_KEY). Contate o suporte para ativar o checkout.'
        elif not (resolve_price_id('semestral') or resolve_price_id('anual')):
            checkout_enabled = False
            setup_error = 'Price IDs do Stripe nao configurados. Configure STRIPE_PRICE_ID_SEMESTRAL e STRIPE_PRICE_ID_ANUAL.'
    else:
        billing_provider = 'manual'
        checkout_enabled = False
        setup_error = 'Licenciamento manual ativo. Selecione plano + forma de pagamento para gerar sua solicitacao.'

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

        if billing_provider == 'manual':
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

            try:
                _enviar_email_pagamento_manual(request, licenca, venc, manual_pix_key)
            except Exception as exc:
                messages.warning(request, f'Nao foi possivel enviar o email de pagamento neste ambiente. Motivo: {exc}')

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

        if billing_provider == 'asaas':
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
            try:
                payment_data = _asaas_create_payment(licenca, venc)
                _enviar_email_pagamento_manual(request, licenca, venc, manual_pix_key)
                invoice_url = payment_data.get('invoiceUrl') or licenca.asaas_invoice_url
                if invoice_url:
                    return redirect(invoice_url)
                messages.success(request, 'Cobranca Asaas gerada com sucesso. Consulte o painel de licenca para pagamento.')
                return redirect('core:licencas_page')
            except Exception as exc:
                messages.error(request, f'Nao foi possivel gerar cobranca no Asaas: {exc}')
                return redirect('licencas:renovar')

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
            'valor_mensal_anual': valor_mensal_anual(),
            'valor_semestral': valor_semestral(),
            'valor_anual': valor_anual(),
            'valor_hora_tecnica': valor_hora_tecnica(),
            'horas_minimas_melhoria': horas_minimas_melhoria(),
            'valor_minimo_melhoria': valor_minimo_melhoria(),
            'checkout_enabled': checkout_enabled,
            'setup_error': setup_error,
            'beneficiario': beneficiario,
            'manual_pix_key': manual_pix_key,
            'manual_mode': billing_provider == 'manual',
            'billing_provider': billing_provider,
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
    if not getattr(settings, 'LICENCA_STRIPE_MODE', False):
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


def _date_from_iso(v):
    if not v:
        return None
    try:
        # Asaas uses YYYY-MM-DD on most payment dates
        return datetime.strptime(v[:10], '%Y-%m-%d').date()
    except Exception:
        return None


def _ativar_licenca_por_pagamento(licenca, pagamento_data=None):
    inicio = pagamento_data or timezone.localdate()
    meses = 12 if licenca.plano == Licenca.Plano.ANUAL else 6
    fim = _add_months(inicio, meses)
    licenca.data_pagamento = inicio
    licenca.inicio_vigencia = inicio
    licenca.fim_vigencia = fim
    licenca.status = Licenca.Status.ATIVA
    licenca.save(
        update_fields=[
            'data_pagamento',
            'inicio_vigencia',
            'fim_vigencia',
            'status',
            'updated_at',
        ]
    )


@csrf_exempt
@require_POST
def asaas_webhook(request):
    if not getattr(settings, 'LICENCA_ASAAS_MODE', False):
        return JsonResponse({'status': 'disabled', 'provider': 'not-asaas'})

    token_expected = (getattr(settings, 'ASAAS_WEBHOOK_TOKEN', '') or '').strip()
    token_received = (
        request.META.get('HTTP_ASAAS_ACCESS_TOKEN')
        or request.META.get('HTTP_ACCESS_TOKEN')
        or ''
    ).strip()
    if token_expected and token_received != token_expected:
        return HttpResponse(status=401)

    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except Exception:
        return HttpResponse(status=400)

    event = payload.get('event') or ''
    payment = payload.get('payment') or {}
    payment_id = payment.get('id') or ''
    external_reference = (payment.get('externalReference') or '').strip()

    licenca = None
    if payment_id:
        licenca = Licenca.objects.filter(asaas_payment_id=payment_id).first()
    if not licenca and external_reference.startswith('LIC-'):
        licenca_id = external_reference.replace('LIC-', '').strip()
        licenca = Licenca.objects.filter(id=licenca_id).first()
    if not licenca:
        return JsonResponse({'status': 'ignored'})

    pay_date = _date_from_iso(payment.get('confirmedDate')) or _date_from_iso(payment.get('paymentDate'))

    if event in {'PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED_IN_CASH'}:
        _ativar_licenca_por_pagamento(licenca, pay_date)
    elif event in {'PAYMENT_OVERDUE'}:
        licenca.status = Licenca.Status.INADIMPLENTE
        licenca.save(update_fields=['status', 'updated_at'])
    elif event in {'PAYMENT_DELETED', 'PAYMENT_REFUNDED', 'PAYMENT_REFUND_IN_PROGRESS', 'PAYMENT_CHARGEBACK_REQUESTED', 'PAYMENT_CHARGEBACK_DISPUTE'}:
        licenca.status = Licenca.Status.CANCELADA
        licenca.save(update_fields=['status', 'updated_at'])
    elif event in {'PAYMENT_AWAITING_RISK_ANALYSIS'}:
        licenca.status = Licenca.Status.AGUARDANDO_CONFIRMACAO
        licenca.save(update_fields=['status', 'updated_at'])

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
