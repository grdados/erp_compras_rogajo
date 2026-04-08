import json
import os
from datetime import datetime

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cadastros.models import PerfilUsuarioCliente

from .models import Licenca

stripe.api_key = settings.STRIPE_SECRET_KEY


@login_required
def renovar_licenca(request):
    if not settings.STRIPE_SECRET_KEY:
        messages.error(request, 'Configure STRIPE_SECRET_KEY para iniciar o checkout.')
        return redirect('core:dashboard')

    perfil = PerfilUsuarioCliente.objects.select_related('cliente').filter(usuario=request.user).first()
    if not perfil or not perfil.cliente:
        messages.error(request, 'Usuario sem cliente vinculado para renovacao de licenca.')
        return redirect('core:dashboard')

    licenca = Licenca.objects.filter(cliente=perfil.cliente).order_by('-updated_at').first()
    if not licenca:
        messages.error(request, 'Cadastre uma licenca para o cliente antes da renovacao.')
        return redirect('core:dashboard')

    if request.method == 'POST':
        price_id = os.getenv('STRIPE_PRICE_ID', '').strip() or licenca.stripe_price_id.strip()
        if not price_id:
            messages.error(request, 'Configure STRIPE_PRICE_ID no ambiente ou na licenca.')
            return redirect('core:dashboard')

        success_url = request.build_absolute_uri(reverse('licencas:checkout_sucesso'))
        cancel_url = request.build_absolute_uri(reverse('licencas:checkout_cancelado'))

        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            customer_email=licenca.email,
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=f'{success_url}?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=cancel_url,
            metadata={'licenca_id': str(licenca.id), 'cliente_id': str(perfil.cliente.id)},
        )
        return redirect(checkout_session.url, permanent=False)

    return render(request, 'licencas/renovar.html', {'stripe_public_key': settings.STRIPE_PUBLIC_KEY})


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
    licenca.save(update_fields=['stripe_customer_id', 'stripe_subscription_id', 'status', 'inicio_vigencia', 'updated_at'])


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
