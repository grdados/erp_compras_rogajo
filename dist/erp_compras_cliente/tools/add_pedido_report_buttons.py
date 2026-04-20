from pathlib import Path

p = Path('templates/core/pedidos/list.html')
s = p.read_text(encoding='utf-8')

# add report button next to +Novo
if 'Relatorio analitico' not in s:
    s = s.replace(
        '+ Novo Pedido</a>',
        '+ Novo Pedido</a>\n    <a target="_blank" href="{% url \'core:pedido_report_analitico\' %}{% if report_query %}?{{ report_query }}{% endif %}" class="rounded-lg border border-slate-300 bg-white px-4 py-2 text-[14px] text-slate-700 shadow-sm hover:bg-slate-100">Relatorio analitico</a>',
        1,
    )

# add resumo icon to actions grid (if exists)
if 'pedido_report_resumo' not in s:
    s = s.replace(
        "<button type=\"button\" title=\"Visualizar\"",
        "<a target=\"_blank\" href=\"{% url 'core:pedido_report_resumo' obj.pk %}\" title=\"Relatorio\" class=\"inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-300 text-slate-700 hover:bg-slate-100\">\n      <svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" class=\"h-4 w-4\">\n        <path stroke-linecap=\"round\" stroke-linejoin=\"round\" d=\"M7 3h7l3 3v15a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z\"/>\n        <path stroke-linecap=\"round\" stroke-linejoin=\"round\" d=\"M14 3v4h4\"/>\n        <path stroke-linecap=\"round\" stroke-linejoin=\"round\" d=\"M8 12h8M8 16h8\"/>\n      </svg>\n    </a>\n    <button type=\"button\" title=\"Visualizar\"",
        1,
    )

p.write_text(s, encoding='utf-8')
print('OK: pedidos list buttons updated')
