from decimal import Decimal

from compras.models import PedidoCompra, StatusPedidoCompra

from .models import ContaPagar, Faturamento, PagamentoContaPagar


def _to_decimal(value):
    return Decimal(value or 0)


def _classificar_status_pedido(pedido):
    total = _to_decimal(pedido.valor_total)
    saldo = _to_decimal(pedido.saldo_faturar)

    # Regra: um pedido so pode ser ENTREGUE quando houver faturamento (ou saldo efetivamente zerado por faturamento).
    # Pedido novo normalmente inicia com saldo = total (PENDENTE).
    if total <= 0:
        base = StatusPedidoCompra.PENDENTE
    elif saldo <= 0:
        base = StatusPedidoCompra.ENTREGUE if pedido.faturamentos.exists() else StatusPedidoCompra.PENDENTE
    elif saldo >= total:
        base = StatusPedidoCompra.PENDENTE
    else:
        base = StatusPedidoCompra.PARCIAL

    if pedido.pedido_pago:
        mapping = {
            StatusPedidoCompra.PENDENTE: StatusPedidoCompra.PG_PENDENTE,
            StatusPedidoCompra.PARCIAL: StatusPedidoCompra.PG_PARCIAL,
            StatusPedidoCompra.ENTREGUE: StatusPedidoCompra.PG_ENTREGUE,
        }
        return mapping[base]
    return base


def sincronizar_fatura_origem_pedido(pedido):
    total = _to_decimal(pedido.valor_total)
    saldo = max(_to_decimal(pedido.saldo_faturar), Decimal('0'))

    # Se o pedido teve o saldo a faturar zerado, a fatura de origem PEDIDO deve ser removida.
    # Isso evita duplicidade com as notas fiscais (origem FATURAMENTO).
    if total <= 0 or saldo <= 0:
        ContaPagar.objects.filter(origem=ContaPagar.Origem.PEDIDO, pedido=pedido, faturamento=None).delete()
        return

    conta, _ = ContaPagar.objects.get_or_create(
        origem=ContaPagar.Origem.PEDIDO,
        pedido=pedido,
        faturamento=None,
        defaults={
            'data': pedido.data,
            'nota_fiscal': f'PED-{pedido.pedido}',
            'custo': pedido.custo,
            'cliente': pedido.cliente,
            'produtor': pedido.produtor,
            'safra': getattr(pedido, 'safra', None),
            'fornecedor': pedido.fornecedor,
            'vencimento': pedido.vencimento,
            'quantidade': Decimal('1'),
            'preco': total,
            'valor_total': total,
            'saldo_aberto': saldo,
            'pago': saldo <= 0,
            'status': ContaPagar.Status.PAGO if saldo <= 0 else ContaPagar.Status.A_PAGAR,
            'detalhes': 'Fatura de origem do pedido de compra.',
        },
    )

    conta.data = pedido.data
    conta.nota_fiscal = f'PED-{pedido.pedido}'
    conta.custo = pedido.custo
    conta.cliente = pedido.cliente
    conta.produtor = pedido.produtor
    conta.safra = getattr(pedido, 'safra', None)
    conta.fornecedor = pedido.fornecedor
    conta.safra = getattr(pedido, 'safra', None)
    conta.vencimento = pedido.vencimento
    conta.quantidade = Decimal('1')
    conta.preco = total
    conta.valor_total = total
    conta.saldo_aberto = saldo
    conta.pago = saldo <= 0

    if conta.pago:
        conta.status = ContaPagar.Status.PAGO
    elif saldo < total:
        conta.status = ContaPagar.Status.PARCIAL
    else:
        conta.status = ContaPagar.Status.A_PAGAR

    conta.save()


def processar_pedido_compra(pedido, created=False):
    total = _to_decimal(pedido.valor_total)
    saldo = _to_decimal(pedido.saldo_faturar)

    if (created or saldo <= 0) and total > 0 and not pedido.faturamentos.exists():
        saldo = total

    if saldo < 0:
        saldo = Decimal('0')

    novo_status = _classificar_status_pedido(pedido)

    fields_to_update = []
    if pedido.saldo_faturar != saldo:
        pedido.saldo_faturar = saldo
        fields_to_update.append('saldo_faturar')

    if pedido.status != novo_status:
        pedido.status = novo_status
        fields_to_update.append('status')

    if fields_to_update:
        PedidoCompra.objects.filter(pk=pedido.pk).update(**{f: getattr(pedido, f) for f in fields_to_update})

    sincronizar_fatura_origem_pedido(pedido)


def _criar_ou_atualizar_fatura_faturamento(faturamento):
    valor = _to_decimal(faturamento.valor_total)

    # Se a nota estiver vinculada a um pedido ja pago, nao gera fatura para evitar duplicidade.
    if faturamento.pedido_id and getattr(faturamento.pedido, 'pedido_pago', False):
        ContaPagar.objects.filter(faturamento=faturamento).delete()
        if getattr(faturamento, 'status', None) != 'PAGO':
            faturamento.status = getattr(type(faturamento), 'Status', None).PAGO if hasattr(type(faturamento), 'Status') else 'PAGO'
            faturamento.save(update_fields=['status'])
        return

    # Caso contrario, mantem como A Receber
    if getattr(faturamento, 'status', None) != 'A_RECEBER':
        faturamento.status = getattr(type(faturamento), 'Status', None).A_RECEBER if hasattr(type(faturamento), 'Status') else 'A_RECEBER'
        faturamento.save(update_fields=['status'])
    conta, _ = ContaPagar.objects.get_or_create(
        faturamento=faturamento,
        defaults={
            'origem': ContaPagar.Origem.FATURAMENTO,
            'pedido': faturamento.pedido,
            'data': faturamento.data,
            'nota_fiscal': faturamento.nota_fiscal,
            'custo': faturamento.custo,
            'cliente': faturamento.cliente,
            'produtor': faturamento.produtor,
            'fornecedor': faturamento.fornecedor,
            'safra': faturamento.safra or (faturamento.pedido.safra if faturamento.pedido_id and getattr(faturamento.pedido, 'safra_id', None) else None),
            'vencimento': faturamento.vencimento,
            'quantidade': Decimal('1'),
            'preco': valor,
            'valor_total': valor,
            'saldo_aberto': valor,
            'status': ContaPagar.Status.A_PAGAR,
            'detalhes': 'Fatura gerada a partir do faturamento.',
        },
    )

    conta.origem = ContaPagar.Origem.FATURAMENTO
    conta.pedido = faturamento.pedido
    conta.data = faturamento.data
    conta.nota_fiscal = faturamento.nota_fiscal
    conta.custo = faturamento.custo
    conta.cliente = faturamento.cliente
    conta.produtor = faturamento.produtor
    conta.safra = faturamento.safra or (faturamento.pedido.safra if faturamento.pedido_id and getattr(faturamento.pedido, 'safra_id', None) else None)
    conta.vencimento = faturamento.vencimento
    conta.quantidade = Decimal('1')
    conta.preco = valor
    conta.valor_total = valor
    if not conta.pago:
        conta.saldo_aberto = valor
        conta.status = ContaPagar.Status.A_PAGAR
    conta.save()


def processar_faturamento(faturamento):
    if faturamento.pedido_id:
        pedido = faturamento.pedido
        saldo_atual = _to_decimal(pedido.saldo_faturar)
        valor_fat = _to_decimal(faturamento.valor_total)

        if saldo_atual > 0 and valor_fat > 0:
            novo_saldo = max(Decimal('0'), saldo_atual - valor_fat)
            PedidoCompra.objects.filter(pk=pedido.pk).update(saldo_faturar=novo_saldo)
            pedido.saldo_faturar = novo_saldo

        # Quando pedido nao eh pago, gera fatura por nota fiscal.
        # Quando pedido ja esta em fluxo PG_*, nao gera para evitar duplicidade.
        if not pedido.pedido_pago and not str(pedido.status).startswith('PG_'):
            _criar_ou_atualizar_fatura_faturamento(faturamento)

        processar_pedido_compra(pedido)
        return

    _criar_ou_atualizar_fatura_faturamento(faturamento)
