import re
from pathlib import Path

p = Path('templates/core/landing_page.html')
s = p.read_text(encoding='utf-8')

# contato email + whatsapp
s = s.replace('rnfelisberto@gmail.com', 'RNFELISBERTO@GMAIL.COM')
s = s.replace('GR Dados Business Intelligence | WhatsApp (67) 99869-8159', 'GR Dados Business Intelligence | WhatsApp (67) 99916-8049')

# remover botao secundario no hero (se existir)
s = re.sub(r"\s*<a href=\"#servicos\" class=\"button-secondary\">[^<]*</a>\s*", "\n", s)

# suavizar caixa do hero
s = s.replace(
    ".hero-image-shell {\n        position: relative;\n        overflow: hidden;\n        border: 1px solid rgba(255, 255, 255, 0.08);\n        background: linear-gradient(\n          180deg,\n          rgba(8, 18, 13, 0.84),\n          rgba(8, 18, 13, 0.4)\n        );\n      }",
    ".hero-image-shell {\n        position: relative;\n        overflow: visible;\n        border-radius: 34px;\n        border: 1px solid rgba(255, 255, 255, 0.10);\n        background: transparent;\n        box-shadow: 0 35px 90px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(100, 210, 111, 0.10);\n      }",
)

s = s.replace(
    ".hero-image-shell::before {\n        content: '';\n        position: absolute;\n        inset: 0;\n        background:",
    ".hero-image-shell::before {\n        content: '';\n        position: absolute;\n        inset: -18px;\n        border-radius: 44px;\n        background:",
)

s = s.replace(
    "        pointer-events: none;\n        z-index: 2;\n      }",
    "        pointer-events: none;\n        filter: blur(16px);\n        opacity: 0.7;\n        z-index: -1;\n      }",
)

s = s.replace(
    'class="parallax-smooth block max-h-[620px] w-full object-contain object-center"',
    'class="parallax-smooth block max-h-[620px] w-full rounded-[30px] ring-1 ring-white/10 shadow-[0_26px_70px_rgba(0,0,0,.42)] object-contain object-center"',
)

# header: mostrar usuario logado + logout
pat = re.compile(
    r"<div class=\"flex items-center gap-3\">\s*<a\s+href=\"/accounts/login/\"[\s\S]*?>Login</a\s*>\s*<button\s+id=\"menu-toggle\"",
    re.M,
)
if pat.search(s):
    repl = """<div class=\"flex items-center gap-3\">\n          {% if user.is_authenticated %}\n            <div class=\"hidden items-center gap-3 rounded-2xl border border-white/15 bg-white/5 px-3 py-2 text-white md:flex\">\n              <div class=\"flex h-9 w-9 items-center justify-center rounded-full bg-[var(--brand-300)] text-sm font-bold text-[var(--brand-900)]\">{{ user.username|slice:':1'|upper }}</div>\n              <div class=\"leading-tight\">\n                <div class=\"text-sm font-semibold\">{{ user.get_full_name|default:user.username }}</div>\n                <div class=\"text-xs text-white/60\">{{ user.get_effective_role_display }}</div>\n              </div>\n            </div>\n            <a href=\"/accounts/logout/\" class=\"hidden rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:border-[var(--brand-300)]/35 hover:bg-white/10 md:inline-flex\">Sair</a>\n          {% else %}\n            <a href=\"/accounts/login/\" class=\"hidden rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:border-[var(--brand-300)]/35 hover:bg-white/10 md:inline-flex\">Login</a>\n          {% endif %}\n          <button\n            id=\"menu-toggle\""""
    s = pat.sub(repl, s, count=1)

# mobile menu: painel/sair quando logado
s = s.replace(
    '<a href="/accounts/login/" class="button-primary mt-2 text-sm"\n             >Acessar sistema</a',
    '{% if user.is_authenticated %}\n          <a href="/dashboard/" class="button-primary mt-2 text-sm">Ir para o painel</a>\n          <a href="/accounts/logout/" class="button-secondary mt-2 text-sm">Sair</a>\n          {% else %}\n          <a href="/accounts/login/" class="button-primary mt-2 text-sm">Acessar sistema</a\n          {% endif %}',
)

p.write_text(s, encoding='utf-8')
print('OK: landing_page.html atualizado')
