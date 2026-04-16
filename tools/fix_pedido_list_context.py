import re
from pathlib import Path

p = Path('core/views.py')
s = p.read_text(encoding='utf-8')

# Insert get_context_data in PedidoCompraListView if missing
pat = re.compile(r"class PedidoCompraListView\(GestorRequiredMixin, ListView\):[\s\S]*?def get_queryset\(self\):[\s\S]*?return qs\n", re.M)
m = pat.search(s)
if not m:
    raise SystemExit('Nao achei bloco PedidoCompraListView/get_queryset.')
block = m.group(0)
if 'def get_context_data' in block:
    print('get_context_data ja existe, pulando')
else:
    insert = """

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Links usados pelo template
        ctx['create_url_name'] = 'core:pedido_create'
        ctx['edit_url_name'] = 'core:pedido_update'
        ctx['delete_url_name'] = 'core:pedido_delete'

        ctx['current_q'] = self.request.GET.get('q', '')
        ctx['current_sort'] = self.request.GET.get('sort', '')

        # Para paginacao / relatorios manter filtros sem page
        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()
        ctx['report_query'] = params.urlencode()

        try:
            ctx['total_registros'] = self.get_queryset().count()
        except Exception:
            ctx['total_registros'] = 0

        return ctx
"""
    new_block = block + insert
    s = s.replace(block, new_block, 1)
    p.write_text(s, encoding='utf-8')
    print('OK: PedidoCompraListView context adicionado')
