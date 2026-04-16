from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

PERIODO_SEMESTRAL_MESES = 6
PERIODO_ANUAL_MESES = 12


def _d(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def salario_minimo_vigente() -> Decimal:
    return _d(getattr(settings, 'SALARIO_MINIMO_VIGENTE', '0') or '0')


def percentual_licenca() -> Decimal:
    return _d(getattr(settings, 'LICENCA_PERCENTUAL_SALARIO_MINIMO', '0.29') or '0.29')


def desconto_anual() -> Decimal:
    return _d(getattr(settings, 'LICENCA_DESCONTO_ANUAL', '0.08') or '0.08')


def valor_mensal() -> Decimal:
    # 29% do salario minimo vigente
    v = salario_minimo_vigente() * percentual_licenca()
    return v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def valor_semestral() -> Decimal:
    # 6 meses
    return (valor_mensal() * Decimal(str(PERIODO_SEMESTRAL_MESES))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def valor_anual() -> Decimal:
    # 12 meses com desconto (por padrao 8%)
    base = valor_mensal() * Decimal(str(PERIODO_ANUAL_MESES))
    v = base * (Decimal('1') - desconto_anual())
    return v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)