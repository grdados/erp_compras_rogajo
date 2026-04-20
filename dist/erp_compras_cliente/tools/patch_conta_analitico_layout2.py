from pathlib import Path

p = Path('templates/core/relatorios/conta_analitico.html')
t = p.read_text(encoding='utf-8')

# Cliente line: add divider below and prevent overlap (ellipsis)
old_cliente = '<div class="rpt-box p-2 mb-2">\n      <table class="w-full" style="border-collapse:collapse; table-layout:fixed;">'
if old_cliente in t:
    t = t.replace(old_cliente, '<div class="rpt-box p-2 mb-2">\n      <table class="w-full" style="border-collapse:collapse; table-layout:fixed;">', 1)

# Replace cliente row with tighter/ellipsis styles
old_row = '<td style="width:50%; white-space:nowrap;"><strong>Cliente:</strong> {{ c.cliente }}</td>'
new_row = '<td style="width:50%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"><strong>Cliente:</strong> {{ c.cliente }}</td>'
t = t.replace(old_row, new_row)

# Add divider line below cliente box (just after </table>)
t = t.replace('      </table>\n    </div>', '      </table>\n      <div class="mt-1 border-b border-slate-300"></div>\n    </div>', 1)

# Produtor line: fixed layout, 80/20, divider, align total to same column width as cliente (20%)
t = t.replace('<table class="w-full" style="border-collapse:collapse;">', '<table class="w-full" style="border-collapse:collapse; table-layout:fixed;">', 2)

t = t.replace(
    '<td style="width:70%;"><strong>Produtor:</strong> {{ p.produtor }}</td>\n            <td style="width:30%;" class="rpt-right"><strong>Valor Total:</strong> {{ p.total_valor|decimal_br:2 }}</td>',
    '<td style="width:80%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"><strong>Produtor:</strong> {{ p.produtor }}</td>\n            <td style="width:20%; white-space:nowrap;" class="rpt-right"><strong>Valor Total:</strong> {{ p.total_valor|decimal_br:2 }}</td>',
)

# Add divider after produtor table
needle = '</table>\n\n        <div class="mt-1"><strong>Resumo do Fornecedor</strong></div>'
if needle in t:
    t = t.replace(needle, '</table>\n        <div class="mt-1 border-b border-slate-300"></div>\n\n        <div class="mt-1"><strong>Resumo do Fornecedor</strong></div>', 1)

# Wrap resumo section with border and align widths
# Adjust resumo table widths to align Valor Total column (20%)
t = t.replace(
    '<th style="width:52%">Fornecedor</th>\n                <th style="width:16%">Status</th>\n                <th style="width:16%">Vencimento</th>\n                <th style="width:16%" class="rpt-right">Valor Total</th>',
    '<th style="width:50%">Fornecedor</th>\n                <th style="width:15%">Status</th>\n                <th style="width:15%">Vencimento</th>\n                <th style="width:20%" class="rpt-right" style="white-space:nowrap;">Valor Total</th>',
)

# Put border around the resumo table container
# Replace the wrapper div around resumo table to include border
wrap_old = '<div class="mt-1 overflow-x-auto">\n          <table class="rpt-table" style="table-layout:fixed;">'
wrap_new = '<div class="mt-1 overflow-x-auto rounded-md border border-slate-300 p-1">\n          <table class="rpt-table" style="table-layout:fixed;">'
t = t.replace(wrap_old, wrap_new, 1)

# Fornecedor header: align total column (20%)
t = t.replace(
    '<td style="width:70%;"><strong>Fornecedor:</strong> {{ f.fornecedor }}</td>\n              <td style="width:30%;" class="rpt-right"><strong>Valor Total:</strong> {{ f.total_valor|decimal_br:2 }}</td>',
    '<td style="width:80%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"><strong>Fornecedor:</strong> {{ f.fornecedor }}</td>\n              <td style="width:20%; white-space:nowrap;" class="rpt-right"><strong>Valor Total:</strong> {{ f.total_valor|decimal_br:2 }}</td>',
)

# Wrap fornecedor details table with border
wrap2_old = '<div class="mt-1 overflow-x-auto">\n            <table class="rpt-table rpt-small" style="table-layout:fixed;">'
wrap2_new = '<div class="mt-1 overflow-x-auto rounded-md border border-slate-300 p-1">\n            <table class="rpt-table rpt-small" style="table-layout:fixed;">'
t = t.replace(wrap2_old, wrap2_new, 1)

# Prevent Valor Total header breaking
# Add nowrap to last header by replacing the th content
# (template already has width; just ensure no wrap)
t = t.replace('>Valor Total</th>', ' style="white-space:nowrap;">Valor Total</th>')

# Total geral box: top border only + center align
# Replace final rpt-box div
old_total = '<div class="rpt-box p-2">'
new_total = '<div class="rpt-box p-2" style="border-top:2px solid #94a3b8; text-align:center;">'
t = t.replace(old_total, new_total, 1)

# Align the two totals to center columns
# Use fixed table layout with 50/50 and center
old_tbl = '<table class="w-full" style="border-collapse:collapse;">'
new_tbl = '<table class="w-full" style="border-collapse:collapse; table-layout:fixed;">'
t = t.replace(old_tbl, new_tbl, 1)

t = t.replace('<td><strong>Total geral:</strong> {{ total_valor|decimal_br:2 }}</td>', '<td style="width:50%;"><strong>Total geral:</strong> {{ total_valor|decimal_br:2 }}</td>')
t = t.replace('<td class="rpt-right"><strong>Saldo geral:</strong> {{ total_saldo|decimal_br:2 }}</td>', '<td style="width:50%;"><strong>Saldo geral:</strong> {{ total_saldo|decimal_br:2 }}</td>')

p.write_text(t, encoding='utf-8')
print('ok')
