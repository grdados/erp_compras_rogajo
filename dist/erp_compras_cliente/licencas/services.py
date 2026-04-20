from __future__ import annotations

import requests
from django.conf import settings


def _asaas_headers():
    return {
        'accept': 'application/json',
        'content-type': 'application/json',
        'access_token': (getattr(settings, 'ASAAS_API_KEY', '') or '').strip(),
    }


def _asaas_mode_enabled():
    return bool(getattr(settings, 'LICENCA_ASAAS_MODE', False))


def cancelar_cobranca_asaas(licenca):
    """
    Cancela/remocao de cobranca no Asaas para evitar boleto/pix pendente.
    Retorna: (ok: bool, detalhe: str)
    """
    if not _asaas_mode_enabled():
        return True, ''

    payment_id = (getattr(licenca, 'asaas_payment_id', '') or '').strip()
    if not payment_id:
        return True, ''

    base_url = (getattr(settings, 'ASAAS_BASE_URL', '') or '').rstrip('/')
    if not base_url:
        return False, 'ASAAS_BASE_URL nao configurada.'

    try:
        resp = requests.delete(
            f'{base_url}/payments/{payment_id}',
            headers=_asaas_headers(),
            timeout=25,
        )
    except Exception as exc:
        return False, f'Falha de comunicacao com Asaas: {exc}'

    if resp.status_code in (200, 204, 404):
        # 404: cobranca ja nao existe no Asaas (ok para seguir)
        return True, ''

    detalhe = ''
    try:
        data = resp.json() or {}
        erros = data.get('errors') or []
        if erros and isinstance(erros, list):
            detalhe = '; '.join(
                (e.get('description') or e.get('code') or str(e)).strip()
                for e in erros if isinstance(e, dict)
            )
        detalhe = detalhe or (data.get('message') or '')
    except Exception:
        detalhe = (resp.text or '').strip()

    if not detalhe:
        detalhe = f'HTTP {resp.status_code}'
    return False, f'Asaas recusou cancelamento da cobranca: {detalhe}'


def excluir_licenca_sincronizada(licenca):
    """
    Exclui licenca local somente apos cancelar cobranca remota no Asaas (quando aplicavel).
    Retorna: (ok: bool, detalhe: str)
    """
    ok, detalhe = cancelar_cobranca_asaas(licenca)
    if not ok:
        # Mantem registro local para nova tentativa e sinaliza pendencia tecnica.
        try:
            if licenca.status != licenca.Status.ATIVA:
                licenca.status = licenca.Status.CANCELAMENTO_REMOTO_PENDENTE
                licenca.save(update_fields=['status', 'updated_at'])
        except Exception:
            pass
        return False, detalhe

    licenca.delete()
    return True, ''
