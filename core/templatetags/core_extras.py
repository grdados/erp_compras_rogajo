from datetime import date, datetime
from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def attr(obj, field_name):
    value = getattr(obj, field_name)
    if isinstance(value, (datetime, date)):
        return value.strftime('%d/%m/%Y')
    return value


@register.filter
def decimal_br(value, decimals=2):
    """Formats numbers as PT-BR (1.234,56)."""
    try:
        decimals_int = int(decimals)
    except Exception:
        decimals_int = 2

    if value is None or value == '':
        value = Decimal('0')

    try:
        num = Decimal(value)
    except Exception:
        try:
            num = Decimal(str(value).replace('.', '').replace(',', '.'))
        except Exception:
            num = Decimal('0')

    fmt = f"{{0:,.{decimals_int}f}}".format(num)
    return fmt.replace(',', 'X').replace('.', ',').replace('X', '.')

@register.filter
def sum_attr(items, field_name):
    """Sum a numeric attribute from a list/queryset."""
    total = Decimal('0')
    if not items:
        return total
    for obj in items:
        try:
            v = getattr(obj, field_name)
        except Exception:
            v = None
        if v is None or v == '':
            v = Decimal('0')
        try:
            total += Decimal(v)
        except Exception:
            try:
                total += Decimal(str(v).replace('.', '').replace(',', '.'))
            except Exception:
                total += Decimal('0')
    return total