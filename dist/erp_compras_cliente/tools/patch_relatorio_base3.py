from pathlib import Path
import re

p = Path('templates/core/relatorios/base.html')
t = p.read_text(encoding='utf-8')

# Ensure single header separator: remove border-b from header tag and add one hr after address.
# Header tag starts with <header class="...">
t = re.sub(r'<header class="([^"]*)">', lambda m: '<header class="' + ' '.join([c for c in m.group(1).split() if c != 'border-b' and not c.startswith('border-')]) + '">', t, count=1)

# Add a single divider under the address line if not present
if 'id="hdr-divider"' not in t:
    t = t.replace('</div>\n        <div class="mt-1 text-slate-700">', '</div>\n        <div class="mt-1 text-slate-700">')
    # Insert divider just before the no-print buttons block
    t = t.replace('<div class="no-print mt-2 flex items-center justify-end gap-2">', '<div id="hdr-divider" class="mt-2 border-b border-slate-300"></div>\n        <div class="no-print mt-2 flex items-center justify-end gap-2">')

# Footer: keep only one line by ensuring no internal divider exists (already removed before)

p.write_text(t, encoding='utf-8')
print('ok')
