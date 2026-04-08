from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render

from cadastros.models import Cliente
from compras.models import PedidoCompra
from financeiro.models import ContaPagar, Faturamento
from licencas.models import Licenca


def landing_page(request):
    return render(request, 'core/landing_page.html')


@login_required
def dashboard(request):
    user = request.user
    role = getattr(user, 'role', None)

    if role == 'ADMIN':
        contexto = {
            'titulo': 'Dashboard do Desenvolvedor',
            'subtitulo': 'Visao global do ERP',
            'cards': [
                {'label': 'Clientes', 'value': Cliente.objects.count()},
                {'label': 'Pedidos de Compra', 'value': PedidoCompra.objects.count()},
                {'label': 'Faturamentos', 'value': Faturamento.objects.count()},
                {'label': 'Contas Pendentes', 'value': ContaPagar.objects.pendentes().count()},
                {'label': 'Licencas Ativas', 'value': Licenca.objects.filter(status=Licenca.Status.ATIVA).count()},
            ],
            'role_display': user.get_role_display(),
        }
        return render(request, 'core/dashboard.html', contexto)

    perfil_cliente = getattr(user, 'perfil_cliente', None)
    if not perfil_cliente or not perfil_cliente.cliente:
        return redirect('core:landing_page')

    cliente = perfil_cliente.cliente
    pedidos = PedidoCompra.objects.filter(cliente=cliente)
    faturamentos = Faturamento.objects.filter(cliente=cliente)
    contas = ContaPagar.objects.filter(cliente=cliente)

    total_compras = pedidos.aggregate(total=Sum('valor_total'))['total'] or 0
    total_faturado = faturamentos.aggregate(total=Sum('valor_total'))['total'] or 0

    contexto = {
        'titulo': 'Dashboard do Cliente' if role == 'SUPERVISOR' else 'Dashboard do Usuario',
        'subtitulo': f'Cliente vinculado: {cliente}',
        'cards': [
            {'label': 'Pedidos', 'value': pedidos.count()},
            {'label': 'Faturamentos', 'value': faturamentos.count()},
            {'label': 'Contas Pendentes', 'value': contas.filter(pago=False).count()},
            {'label': 'Total em Compras', 'value': f'R$ {total_compras:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')},
            {'label': 'Total Faturado', 'value': f'R$ {total_faturado:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')},
        ],
        'role_display': user.get_role_display(),
        'licenca': Licenca.licenca_vigente_cliente(cliente),
        'today': date.today(),
    }
    return render(request, 'core/dashboard.html', contexto)
