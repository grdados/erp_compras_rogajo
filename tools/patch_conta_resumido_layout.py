from pathlib import Path

p = Path('templates/core/relatorios/conta_resumido.html')
t = p.read_text(encoding='utf-8')

old = "<table class=\"w-full\" style=\"border-collapse:collapse;\">\n        <tr>\n          <td style=\"width:55%;\"><strong>Cliente:</strong> {{ c.cliente }}</td>\n          <td style=\"width:15%;\"><strong>Cultura:</strong> {{ c.cultura_label }}</td>\n          <td style=\"width:15%;\"><strong>Safra:</strong> {{ c.safra_label }}</td>\n          <td style=\"width:15%;\" class=\"rpt-right\"><strong>Valor Total:</strong> {{ c.total_valor|decimal_br:2 }}</td>\n        </tr>\n      </table>"
new = "<table class=\"w-full\" style=\"border-collapse:collapse; table-layout:fixed;\">\n        <tr>\n          <td style=\"width:50%; white-space:nowrap;\"><strong>Cliente:</strong> {{ c.cliente }}</td>\n          <td style=\"width:18%; white-space:nowrap;\"><strong>Cultura:</strong> {{ c.cultura_label }}</td>\n          <td style=\"width:12%; white-space:nowrap;\"><strong>Safra:</strong> {{ c.safra_label }}</td>\n          <td style=\"width:20%; white-space:nowrap;\" class=\"rpt-right\"><strong>Valor Total:</strong> {{ c.total_valor|decimal_br:2 }}</td>\n        </tr>\n      </table>"
if old in t:
    t = t.replace(old, new)

# Resumo table: fixed layout and distribution (first occurrence)
t = t.replace('<table class="rpt-table">', '<table class="rpt-table" style="table-layout:fixed;">', 1)
t = t.replace(
    '<th>Fornecedor</th>\n                <th>Status</th>\n                <th>Vencimento</th>\n                <th class="rpt-right">Valor Total</th>',
    '<th style="width:52%">Fornecedor</th>\n                <th style="width:16%">Status</th>\n                <th style="width:16%">Vencimento</th>\n                <th style="width:16%" class="rpt-right">Valor Total</th>',
)

# Total box: stronger border (first occurrence)
t = t.replace('<div class="rpt-box p-2">', '<div class="rpt-box rpt-total p-2">', 1)

p.write_text(t, encoding='utf-8')
print('ok')
