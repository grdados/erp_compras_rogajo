from pathlib import Path

p = Path('templates/licencas/renovar.html')
s = p.read_text(encoding='utf-8')

if '{% if setup_error %}' not in s:
    s = s.replace(
        '<p class="mt-2 text-[14px] font-normal text-slate-300">',
        '{% if setup_error %}\n  <div class="mt-4 rounded-xl border border-amber-300/30 bg-amber-500/10 px-4 py-3 text-[14px] font-normal text-amber-100">{{ setup_error }}</div>\n{% endif %}\n\n  <p class="mt-2 text-[14px] font-normal text-slate-300">',
        1,
    )

s = s.replace(
    'Ir para checkout Stripe',
    '{% if checkout_enabled %}Ir para checkout Stripe{% else %}Checkout indisponivel{% endif %}',
)

needle = 'type="submit" class="rounded-lg bg-emerald-400 px-5 py-3 text-[14px] font-bold text-slate-900"'
if needle in s:
    s = s.replace(
        needle,
        'type="submit" {% if not checkout_enabled %}disabled{% endif %} class="rounded-lg bg-emerald-400 px-5 py-3 text-[14px] font-bold text-slate-900 {% if not checkout_enabled %}opacity-60 cursor-not-allowed{% endif %}"',
        1,
    )

p.write_text(s, encoding='utf-8')
print('OK: renovar.html atualizado')
