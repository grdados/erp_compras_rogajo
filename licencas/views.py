import json
import os
from datetime import datetime, timedelta

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Licenca, PerfilUsuarioLicenca
from .forms import PrimeiroAcessoLicencaForm, PrimeiroAcessoPublicForm
from .pricing import valor_anual, valor_mensal, valor_semestral

stripe.api_key = settings.STRIPE_SECRET_KEY
def primeiro_acesso(request):
    """Tela de ativacao de licenca (primeiro acesso).

    - Se o usuario ja estiver logado: cria a licenca e vincula ao usuario atual.
    - Se estiver anonimo (fluxo do login): cria usuario (SUPERVISOR) + licenca e loga automaticamente.
    """

    if request.user.is_authenticated:
        if getattr(request.user, 'effective_role', '') == 'ADMIN':
            return redirect('core:dashboard')

        perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()
        if perfil and perfil.licenca:
            lic = perfil.licenca
            if getattr(lic, 'esta_vigente', False):
                return redirect('core:dashboard')
            return redirect('licencas:renovar')

        form = PrimeiroAcessoLicencaForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            lic = Licenca.objects.create(
                cliente=form.cleaned_data['cliente'],
                cpf_cnpj=form.cleaned_data['cpf_cnpj'],
                email=form.cleaned_data['email'],
                contato=form.cleaned_data['contato'],
                status=Licenca.Status.EXPIRADA,
            )
            PerfilUsuarioLicenca.objects.create(usuario=request.user, licenca=lic)
            return redirect('licencas:renovar')

        return render(request, 'licencas/primeiro_acesso.html', {'form': form, 'modo_publico': False})

    form = PrimeiroAcessoPublicForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        User = get_user_model()
        with transaction.atomic():
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                password=form.cleaned_data['password1'],
                email=form.cleaned_data['email'],
                role=User.Role.SUPERVISOR,
            )
            lic = Licenca.objects.create(
                cliente=form.cleaned_data['cliente'],
                cpf_cnpj=form.cleaned_data['cpf_cnpj'],
                email=form.cleaned_data['email'],
                contato=form.cleaned_data['contato'],
                status=Licenca.Status.EXPIRADA,
            )
            PerfilUsuarioLicenca.objects.create(usuario=user, licenca=lic)

        login(request, user)
        return redirect('licencas:renovar')

    return render(request, 'licencas/primeiro_acesso.html', {'form': form, 'modo_publico': True})




@login_required
def renovar_licenca(request):
    """Renovacao via Stripe (sem loop de redirect quando Stripe/Price nao estiver configurado)."""

    perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()
    licenca = perfil.licenca if perfil else None
    if not licenca:
        return redirect('licencas:primeiro_acesso')

    def resolve_price_id(periodo: str) -> str:
        periodo = (periodo or 'semestral').strip().lower()
        if periodo == 'anual':
            pid = (os.getenv('STRIPE_PRICE_ID_ANUAL', '') or '').strip() or (licenca.stripe_price_id_anual or '').strip()
        else:
            pid = (os.getenv('STRIPE_PRICE_ID_SEMESTRAL', '') or '').strip() or (licenca.stripe_price_id_semestral or '').strip()

        if not pid:
            # fallback legado
            pid = (os.getenv('STRIPE_PRICE_ID', '') or '').strip() or (licenca.stripe_price_id or '').strip()
        return pid

    setup_error = None
    checkout_enabled = True

    if not settings.STRIPE_SECRET_KEY:
        checkout_enabled = False
        setup_error = 'Stripe ainda nao esta configurado (STRIPE_SECRET_KEY). Contate o suporte para ativar o checkout.'

    if checkout_enabled and not (resolve_price_id('semestral') or resolve_price_id('anual')):
        checkout_enabled = False
        setup_error = 'Price IDs do Stripe nao configurados. Configure STRIPE_PRICE_ID_SEMESTRAL e STRIPE_PRICE_ID_ANUAL no .env (ou no cadastro da licenca).'

    if request.method == 'POST':
        periodo = (request.POST.get('periodo') or 'semestral').strip().lower()
        if periodo not in {'semestral', 'anual'}:
            periodo = 'semestral'

        price_id = resolve_price_id(periodo)

        if not settings.STRIPE_SECRET_KEY:
            messages.error(request, 'Stripe nao configurado. Contate o suporte para liberar o checkout.')
        elif not price_id:
            messages.error(request, 'Price ID do Stripe nao configurado para o periodo selecionado.')
        else:
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

    # fallback: se por algum motivo o webhook de subscription nao chegar, damos 30 dias.
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
