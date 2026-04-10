import re
from pathlib import Path

p = Path('core/views.py')
s = p.read_text(encoding='utf-8')

pat = re.compile(r"def _make_simple_crud\([\s\S]*?return _List, _Create, _Update, _Delete\n\n", re.M)

new = """def _make_simple_crud(name_prefix, model_cls, form_cls, list_template='core/crud/modal_list.html', search_fields=None, ordering='-id'):\n    search_fields = search_fields or []\n\n    ListKls = type(\n        f'{name_prefix.title().replace(\"_\", \"\")}ListView',\n        (GestorRequiredMixin, CrudListView),\n        {\n            'template_name': list_template,\n            'model': model_cls,\n            'context_title': model_cls._meta.verbose_name_plural.title(),\n            'create_url_name': f'core:{name_prefix}_create',\n            'edit_url_name': f'core:{name_prefix}_update',\n            'delete_url_name': f'core:{name_prefix}_delete',\n            'default_ordering': ordering,\n            'search_fields': search_fields,\n        },\n    )\n\n    CreateKls = type(\n        f'{name_prefix.title().replace(\"_\", \"\")}CreateView',\n        (GestorRequiredMixin, CrudCreateView),\n        {\n            'model': model_cls,\n            'form_class': form_cls,\n            'success_url': reverse_lazy(f'core:{name_prefix}_list'),\n        },\n    )\n\n    UpdateKls = type(\n        f'{name_prefix.title().replace(\"_\", \"\")}UpdateView',\n        (GestorRequiredMixin, CrudUpdateView),\n        {\n            'model': model_cls,\n            'form_class': form_cls,\n            'success_url': reverse_lazy(f'core:{name_prefix}_list'),\n        },\n    )\n\n    DeleteKls = type(\n        f'{name_prefix.title().replace(\"_\", \"\")}DeleteView',\n        (GestorRequiredMixin, CrudDeleteView),\n        {\n            'model': model_cls,\n            'success_url': reverse_lazy(f'core:{name_prefix}_list'),\n        },\n    )\n\n    return ListKls, CreateKls, UpdateKls, DeleteKls\n\n\n"""

s2, n = pat.subn(new, s, count=1)
if n != 1:
    raise SystemExit(f'Nao encontrei _make_simple_crud para substituir (n={n})')

p.write_text(s2, encoding='utf-8')
print('OK: _make_simple_crud corrigido')
