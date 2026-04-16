import re
from pathlib import Path

p = Path('licencas/views.py')
s = p.read_text(encoding='utf-8')

# 1) primeiro_acesso: se usuario ja tem licenca, manda pra renovar se nao vigente
pat_existing = re.compile(
    r"(perfil\s*=\s*PerfilUsuarioLicenca\.objects[\s\S]*?\n\s*)if\s+perfil\s+and\s+perfil\.licenca:\n\s*return\s+redirect\('core:dashboard'\)",
    re.M,
)

def repl_existing(m):
    head = m.group(1)
    return (
        head
        + "if perfil and perfil.licenca:\n"
        + "        lic = perfil.licenca\n"
        + "        if getattr(lic, 'esta_vigente', False):\n"
        + "            return redirect('core:dashboard')\n"
        + "        return redirect('licencas:renovar')"
    )

s, n1 = pat_existing.subn(repl_existing, s, count=1)

# 2) renovar_licenca: nunca redireciona pro dashboard em caso de setup incompleto (evita loop)
pat_renovar = re.compile(
    r"@login_required\s+def renovar_licenca\(request\):[\s\S]*?(?=@login_required\s+def checkout_sucesso)",
    re.S,
)

new = """@login_required\ndef renovar_licenca(request):\n    \"\"\"Renovacao via Stripe (sem loop de redirect quando Stripe/Price nao estiver configurado).\"\"\"\n\n    perfil = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=request.user).first()\n    licenca = perfil.licenca if perfil else None\n    if not licenca:\n        return redirect('licencas:primeiro_acesso')\n\n    def resolve_price_id(periodo: str) -> str:\n        periodo = (periodo or 'semestral').strip().lower()\n        if periodo == 'anual':\n            pid = (os.getenv('STRIPE_PRICE_ID_ANUAL', '') or '').strip() or (licenca.stripe_price_id_anual or '').strip()\n        else:\n            pid = (os.getenv('STRIPE_PRICE_ID_SEMESTRAL', '') or '').strip() or (licenca.stripe_price_id_semestral or '').strip()\n\n        if not pid:\n            # fallback legado\n            pid = (os.getenv('STRIPE_PRICE_ID', '') or '').strip() or (licenca.stripe_price_id or '').strip()\n        return pid\n\n    setup_error = None\n    checkout_enabled = True\n\n    if not settings.STRIPE_SECRET_KEY:\n        checkout_enabled = False\n        setup_error = 'Stripe ainda nao esta configurado (STRIPE_SECRET_KEY). Contate o suporte para ativar o checkout.'\n\n    if checkout_enabled and not (resolve_price_id('semestral') or resolve_price_id('anual')):\n        checkout_enabled = False\n        setup_error = 'Price IDs do Stripe nao configurados. Configure STRIPE_PRICE_ID_SEMESTRAL e STRIPE_PRICE_ID_ANUAL no .env (ou no cadastro da licenca).'\n\n    if request.method == 'POST':\n        periodo = (request.POST.get('periodo') or 'semestral').strip().lower()\n        if periodo not in {'semestral', 'anual'}:\n            periodo = 'semestral'\n\n        price_id = resolve_price_id(periodo)\n\n        if not settings.STRIPE_SECRET_KEY:\n            messages.error(request, 'Stripe nao configurado. Contate o suporte para liberar o checkout.')\n        elif not price_id:\n            messages.error(request, 'Price ID do Stripe nao configurado para o periodo selecionado.')\n        else:\n            stripe.api_key = settings.STRIPE_SECRET_KEY\n            success_url = request.build_absolute_uri(reverse('licencas:checkout_sucesso'))\n            cancel_url = request.build_absolute_uri(reverse('licencas:checkout_cancelado'))\n\n            checkout_session = stripe.checkout.Session.create(\n                mode='subscription',\n                customer_email=licenca.email,\n                line_items=[{'price': price_id, 'quantity': 1}],\n                success_url=f'{success_url}?session_id={{CHECKOUT_SESSION_ID}}',\n                cancel_url=cancel_url,\n                metadata={'licenca_id': str(licenca.id), 'periodo': periodo},\n            )\n            return redirect(checkout_session.url, permanent=False)\n\n    return render(\n        request,\n        'licencas/renovar.html',\n        {\n            'stripe_public_key': settings.STRIPE_PUBLIC_KEY,\n            'valor_mensal': valor_mensal(),\n            'valor_semestral': valor_semestral(),\n            'valor_anual': valor_anual(),\n            'checkout_enabled': checkout_enabled,\n            'setup_error': setup_error,\n        },\n    )\n\n\n"""

if not pat_renovar.search(s):
    raise SystemExit('Nao encontrei o bloco renovar_licenca para substituir.')

s = pat_renovar.sub(new, s, count=1)

p.write_text(s, encoding='utf-8')
print('OK: licencas/views.py atualizado')
print('primeiro_acesso_patch:', n1)
