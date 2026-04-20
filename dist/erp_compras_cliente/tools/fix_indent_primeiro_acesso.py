import re
from pathlib import Path

p = Path('licencas/views.py')
s = p.read_text(encoding='utf-8')

bad = re.compile(
    r"if perfil and perfil\.licenca:\n\s*lic = perfil\.licenca\n\s*if getattr\(lic, 'esta_vigente', False\):\n\s*return redirect\('core:dashboard'\)\n\s*return redirect\('licencas:renovar'\)",
    re.M,
)

def fix(m):
    return (
        "if perfil and perfil.licenca:\n"
        "            lic = perfil.licenca\n"
        "            if getattr(lic, 'esta_vigente', False):\n"
        "                return redirect('core:dashboard')\n"
        "            return redirect('licencas:renovar')"
    )

s2, n = bad.subn(fix, s, count=1)
if n != 1:
    raise SystemExit(f'Nao encontrei bloco ruim para corrigir (n={n}).')

p.write_text(s2, encoding='utf-8')
print('OK: indent do primeiro_acesso corrigido')
