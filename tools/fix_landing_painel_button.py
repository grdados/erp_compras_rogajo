import re
from pathlib import Path

p = Path('templates/core/landing_page.html')
s = p.read_text(encoding='utf-8')

# Make the user pill clickable and add a Painel button in desktop header when authenticated
# Insert Painel link before Sair link
s = s.replace(
    '<a href="/accounts/logout/" class="hidden rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:border-[var(--brand-300)]/35 hover:bg-white/10 md:inline-flex">Sair</a>',
    '<a href="/dashboard/" class="hidden rounded-xl bg-[var(--brand-300)] px-4 py-2 text-sm font-semibold text-[var(--brand-900)] transition hover:-translate-y-0.5 hover:shadow-[0_18px_45px_rgba(55,164,87,.30)] md:inline-flex">Painel</a>\n            <a href="/accounts/logout/" class="hidden rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:border-[var(--brand-300)]/35 hover:bg-white/10 md:inline-flex">Sair</a>',
)

# Wrap user pill container with link to dashboard (safe replace once)
pat = re.compile(r'(<div class="hidden items-center gap-3 rounded-2xl border border-white/15 bg-white/5 px-3 py-2 text-white md:flex">[\s\S]*?</div>\s*</div>)', re.M)
# The above may be too greedy; do a simpler targeted replace:
needle = '<div class="hidden items-center gap-3 rounded-2xl border border-white/15 bg-white/5 px-3 py-2 text-white md:flex">'
if needle in s:
    s = s.replace(
        needle,
        '<a href="/dashboard/" class="hidden md:flex">\n              ' + needle,
        1,
    )
    # close the <a> after the user pill block
    s = s.replace(
        '</div>\n            </div>',
        '</div>\n            </div>\n            </a>',
        1,
    )

p.write_text(s, encoding='utf-8')
print('OK: landing header Painel + link usuario')
