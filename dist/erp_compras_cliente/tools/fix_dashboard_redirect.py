import re
from pathlib import Path

p = Path('core/views.py')
s = p.read_text(encoding='utf-8')

# Replace the non-admin dashboard block that depends on _get_cliente_do_usuario
pat = re.compile(
    r"\n\s*cliente = _get_cliente_do_usuario\(user\)[\s\S]*?return render\([\s\S]*?\)\s*\n\s*\)\s*\n",
    re.M,
)

replacement = """

    # Supervisor/Usuario: nao depende do cadastro administrativo de 'Cliente'.
    # O controle de acesso e feito por licenca (middleware) e permissoes por perfil.
    perfil_licenca = getattr(user, 'perfil_licenca', None)
    licenca = perfil_licenca.licenca if perfil_licenca else None

    pedidos_qs = PedidoCompra.objects.all()
    faturamentos_qs = Faturamento.objects.all()

    total_compras = pedidos_qs.aggregate(total=Sum('valor_total'))['total'] or 0
    total_faturado = faturamentos_qs.aggregate(total=Sum('valor_total'))['total'] or 0

    cards = [
        {'label': 'Pedidos', 'value': pedidos_qs.count()},
        {'label': 'Faturamentos', 'value': faturamentos_qs.count()},
        {'label': 'Total Compras', 'value': _format_brl(total_compras)},
        {'label': 'Total Faturado', 'value': _format_brl(total_faturado)},
    ]

    subtitulo = 'Acesso ao painel administrativo do sistema'
    if licenca and not licenca.esta_vigente:
        subtitulo = 'Licenca nao vigente: acesse Licenca para renovar'

    return render(
        request,
        'core/dashboard.html',
        {
            'titulo': 'Painel Administrativo',
            'subtitulo': subtitulo,
            'cards': cards,
            'role_display': user.get_effective_role_display(),
            'modulos': [
                {'titulo': 'Cadastros', 'descricao': 'Dados mestres do sistema', 'url': '/app/cadastros/'},
                {'titulo': 'Compras', 'descricao': 'Pedidos e faturamento', 'url': '/app/compras/'},
                {'titulo': 'Financeiro', 'descricao': 'Contas a pagar', 'url': '/app/financeiro/'},
                {'titulo': 'Licencas', 'descricao': 'Assinatura e vigencia', 'url': '/app/licencas/'},
            ],
        },
    )
"""

s2, n = pat.subn(replacement, s, count=1)
if n != 1:
    raise SystemExit(f'Nao consegui substituir bloco do dashboard (n={n}).')

p.write_text(s2, encoding='utf-8')
print('OK: core/views.py dashboard ajustado')
