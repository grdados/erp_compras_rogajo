from pathlib import Path

p = Path('templates/core/relatorios/base.html')
t = p.read_text(encoding='utf-8')

# Reduce page margins (left/right)
t = t.replace('@page { size: A4 portrait; margin: 12mm; }', '@page { size: A4 portrait; margin: 10mm 6mm 10mm 6mm; }')

# Reduce footer bottom margin and reserve less space
t = t.replace('footer { position: fixed; bottom: 8mm; left: 0; right: 0; }', 'footer { position: fixed; bottom: 5mm; left: 0; right: 0; }')
t = t.replace('main { padding-bottom: 24mm !important; }', 'main { padding-bottom: 18mm !important; }')

# Remove duplicated separator lines (header/footer)
t = t.replace('<div class="mt-2 border-b border-slate-300"></div>', '')
t = t.replace('<div class="border-b border-slate-300"></div>', '')

# Reduce main padding a bit
t = t.replace('<main class="mx-auto max-w-6xl px-3 py-3">', '<main class="mx-auto max-w-6xl px-2 py-2">')

# Add helper classes (only once)
if '.rpt-small' not in t:
    t = t.replace(
        '.rpt-box { border: 1px solid #e2e8f0; border-radius: 8px; background: white; }',
        '.rpt-box { border: 1px solid #e2e8f0; border-radius: 8px; background: white; }\n'
        '      .rpt-small { font-size: 10px; }\n'
        '      .rpt-small th, .rpt-small td { padding: 3px 5px; }\n'
        '      .rpt-total { border: 2px solid #94a3b8; }\n'
    )

p.write_text(t, encoding='utf-8')
print('ok')
