
from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Q, Sum, Avg, Exists, OuterRef, Count, Min, F, ExpressionWrapper, DecimalField
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import urlencode
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, FormView

from cadastros.models import (
    Categoria,
    Cliente,
    Cultura,
    Custo,
    FormaPagamento,
    Fornecedor,
    Operacao,
    Produto,
    Produtor,
    Propriedade,
    Safra,
    Unidade,
)
from compras.models import CotacaoProduto, Planejamento, PlanejamentoItem, PedidoCompra, PedidoCompraItem, StatusPedidoCompra
from financeiro.models import (
    ContaPagar,
    Faturamento,
    FaturamentoItem,
    FormaPagamento as FormaPagamentoFinanceiro,
    PagamentoContaPagar,
)
from licencas.models import Licenca, PerfilUsuarioLicenca

from .models import BackupFile, BackupSettings
from .backup_utils import compute_next_run, create_backup, parse_daily_time, restore_backup_from_zip

from .forms import (
    CategoriaForm,
    ClienteForm,
    ContaPagarForm,
    CotacaoProdutoForm,
    CulturaForm,
    CustoForm,
    FaturamentoForm,
    FaturamentoItemForm,
    FormaPagamentoForm,
    FornecedorForm,
    LicencaForm,
    PerfilUsuarioLicencaForm,
    InviteUsuarioLicencaForm,
    OperacaoForm,
    PlanejamentoForm,
    PlanejamentoItemForm,
    PedidoCompraForm,
    PedidoCompraItemForm,
    ProdutoForm,
    ProdutorForm,
    PropriedadeForm,
    SafraForm,
    UnidadeForm,
)
from .mixins import AdminRequiredMixin, GestorRequiredMixin


def _get_cliente_do_usuario(user):
    perfil_cliente = getattr(user, 'perfil_cliente', None)
    if perfil_cliente and perfil_cliente.cliente:
        return perfil_cliente.cliente
    return None


def _format_brl(valor):
    try:
        v = Decimal(valor or 0)
    except Exception:
        v = Decimal('0')
    return f'R$ {v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _parse_decimal_br(value) -> Decimal:
    if value is None:
        return Decimal('0')
    raw = str(value).strip()
    if not raw:
        return Decimal('0')

    raw = raw.replace(' ', '')
    if ',' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    else:
        raw = raw.replace('.', '')

    try:
        return Decimal(raw)
    except Exception:
        return Decimal('0')


def landing_page(request):
    return render(request, 'core/landing_page.html')


@login_required
def _build_dashboard_context(request):
    """
    Centraliza os cálculos da Dashboard para reutilizar na tela e em relatórios.
    """
    user = request.user
    role = getattr(user, 'effective_role', None)

    # Filtros (conceito "por safra", com defaults amigaveis).
    filtro_cultura = (request.GET.get('cultura') or '').strip()
    filtro_safra = (request.GET.get('safra') or '').strip()
    filtro_categoria = (request.GET.get('categoria') or '').strip()
    filtro_cliente = (request.GET.get('cliente') or '').strip()

    # Default: usar a ultima safra cadastrada (evita dashboard "vazio" em base grande).
    if not filtro_safra:
        s = Safra.objects.order_by('-ano', 'safra').first()
        if s:
            filtro_safra = str(s.pk)

    def _to_decimal(v):
        try:
            return v if isinstance(v, Decimal) else Decimal(str(v or '0'))
        except Exception:
            return Decimal('0')

    def _safe_div(a: Decimal, b: Decimal) -> Decimal:
        if not b:
            return Decimal('0')
        try:
            return a / b
        except Exception:
            return Decimal('0')

    if role == 'ADMIN':
        # Admin usa o mesmo dashboard operacional, com acesso total.
        pass

    # Supervisor/Usuario: nao depende do cadastro administrativo de 'Cliente'.
    # O controle de acesso e feito por licenca (middleware) e permissoes por perfil.
    perfil_licenca = getattr(user, 'perfil_licenca', None)
    licenca = perfil_licenca.licenca if perfil_licenca else None

    # -----------------------------
    # Base QS (Planejado x Realizado)
    # -----------------------------
    pl_itens = PlanejamentoItem.objects.select_related(
        'planejamento',
        'planejamento__safra',
        'planejamento__safra__cultura',
        'planejamento__cliente',
        'produto_cadastro',
        'produto_cadastro__categoria',
    ).filter(produto_cadastro__isnull=False)

    fat_itens = FaturamentoItem.objects.select_related(
        'faturamento',
        'faturamento__safra',
        'faturamento__safra__cultura',
        'faturamento__cliente',
        'produto_cadastro',
        'produto_cadastro__categoria',
    ).filter(produto_cadastro__isnull=False)

    ped_qs = PedidoCompra.objects.select_related('safra', 'cliente', 'produtor', 'fornecedor')

    if filtro_safra:
        pl_itens = pl_itens.filter(planejamento__safra_id=filtro_safra)
        fat_itens = fat_itens.filter(faturamento__safra_id=filtro_safra)
        ped_qs = ped_qs.filter(safra_id=filtro_safra)
    if filtro_cultura:
        pl_itens = pl_itens.filter(planejamento__safra__cultura_id=filtro_cultura)
        fat_itens = fat_itens.filter(faturamento__safra__cultura_id=filtro_cultura)
        ped_qs = ped_qs.filter(safra__cultura_id=filtro_cultura)
    if filtro_cliente:
        pl_itens = pl_itens.filter(planejamento__cliente_id=filtro_cliente)
        fat_itens = fat_itens.filter(faturamento__cliente_id=filtro_cliente)
        ped_qs = ped_qs.filter(cliente_id=filtro_cliente)
    if filtro_categoria:
        pl_itens = pl_itens.filter(produto_cadastro__categoria_id=filtro_categoria)
        fat_itens = fat_itens.filter(produto_cadastro__categoria_id=filtro_categoria)
        ped_qs = ped_qs.filter(itens__produto_cadastro__categoria_id=filtro_categoria).distinct()

    planned_agg = pl_itens.aggregate(
        total=Sum('total_item'),
        area=Sum('area_ha'),
        weighted_price=Sum(
            ExpressionWrapper(
                F('area_ha') * F('planejamento__preco_produto'),
                output_field=DecimalField(max_digits=24, decimal_places=6),
            )
        ),
    )
    planned_total = _to_decimal(planned_agg.get('total'))
    planned_area = _to_decimal(planned_agg.get('area'))
    weighted_price = _to_decimal(planned_agg.get('weighted_price'))
    avg_preco_prod = _safe_div(weighted_price, planned_area) if planned_area > 0 else Decimal('0')
    custo_ha = _safe_div(planned_total, planned_area) if planned_area > 0 else Decimal('0')
    sc_ha = _safe_div(custo_ha, avg_preco_prod) if avg_preco_prod > 0 else Decimal('0')

    realized_total = _to_decimal(fat_itens.aggregate(total=Sum('total_item')).get('total'))

    # KPI cards
    utilizado_pct = Decimal('0')
    if planned_total > 0:
        utilizado_pct = (realized_total / planned_total) * Decimal('100')
    economia = planned_total - realized_total

    # Resumo de pedidos
    pedidos_total = ped_qs.count()
    status_map = {v: l for v, l in StatusPedidoCompra.choices}
    pedidos_por_status = list(
        ped_qs.values('status')
        .annotate(qtd=Count('id'), total=Sum('valor_total'), saldo=Sum('saldo_faturar'))
        .order_by('status')
    )
    for r in pedidos_por_status:
        r['status_label'] = status_map.get(r.get('status'), r.get('status'))

    # Resumo por categoria (Planejado x Realizado)
    planned_cat = {
        r['produto_cadastro__categoria_id']: _to_decimal(r['total'])
        for r in pl_itens.values('produto_cadastro__categoria_id').annotate(total=Sum('total_item'))
    }
    realized_cat = {
        r['produto_cadastro__categoria_id']: _to_decimal(r['total'])
        for r in fat_itens.values('produto_cadastro__categoria_id').annotate(total=Sum('total_item'))
    }
    categorias = list(Categoria.objects.all().order_by('nome'))
    categorias_rows = []
    max_cat = Decimal('0')
    for c in categorias:
        p = planned_cat.get(c.pk, Decimal('0'))
        r = realized_cat.get(c.pk, Decimal('0'))
        # Remove categorias totalmente zeradas (melhora leitura do grafico)
        if (p or Decimal('0')) == 0 and (r or Decimal('0')) == 0:
            continue
        max_cat = max(max_cat, p, r)
        categorias_rows.append({'obj': c, 'planejado': p, 'realizado': r})

    # Percentuais para barras (evita filtros no template)
    for row in categorias_rows:
        if max_cat > 0:
            row['pct_realizado'] = int(min(Decimal('100'), _safe_div(row['realizado'] * Decimal('100'), max_cat)).quantize(Decimal('1')))
            row['pct_planejado'] = int(min(Decimal('100'), _safe_div(row['planejado'] * Decimal('100'), max_cat)).quantize(Decimal('1')))
        else:
            row['pct_realizado'] = 0
            row['pct_planejado'] = 0

        # Percentual do total por categoria (base: total planejado; fallback: total realizado)
        base_total = planned_total if planned_total > 0 else realized_total
        if base_total > 0:
            pct_total = _safe_div(row['planejado'] * Decimal('100'), base_total)
        else:
            pct_total = Decimal('0')
        try:
            row['pct_total'] = pct_total.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        except Exception:
            row['pct_total'] = Decimal('0')

    # Resumo por produto (top)
    top_real = list(
        fat_itens.values('produto_cadastro_id', 'produto_cadastro__nome')
        .annotate(total=Sum('total_item'))
        .order_by('-total')[:8]
    )
    top_plan = {
        r['produto_cadastro_id']: _to_decimal(r['total'])
        for r in pl_itens.values('produto_cadastro_id').annotate(total=Sum('total_item'))
    }
    produtos_rows = []
    max_prod = Decimal('0')
    for r in top_real:
        pid = r['produto_cadastro_id']
        real = _to_decimal(r['total'])
        plan = top_plan.get(pid, Decimal('0'))
        max_prod = max(max_prod, real, plan)
        produtos_rows.append({'nome': r['produto_cadastro__nome'], 'planejado': plan, 'realizado': real})

    for row in produtos_rows:
        if max_prod > 0:
            row['pct_realizado'] = int(min(Decimal('100'), _safe_div(row['realizado'] * Decimal('100'), max_prod)).quantize(Decimal('1')))
            row['pct_planejado'] = int(min(Decimal('100'), _safe_div(row['planejado'] * Decimal('100'), max_prod)).quantize(Decimal('1')))
        else:
            row['pct_realizado'] = 0
            row['pct_planejado'] = 0

    # Ranking por fornecedor (Planejado x Realizado)
    ped_itens = (
        PedidoCompraItem.objects.select_related(
            'pedido_compra',
            'pedido_compra__fornecedor',
            'pedido_compra__safra',
            'pedido_compra__safra__cultura',
            'pedido_compra__cliente',
            'produto_cadastro',
            'produto_cadastro__categoria',
        )
        .filter(produto_cadastro__isnull=False, pedido_compra__fornecedor__isnull=False)
    )
    if filtro_safra:
        ped_itens = ped_itens.filter(pedido_compra__safra_id=filtro_safra)
    if filtro_cultura:
        ped_itens = ped_itens.filter(pedido_compra__safra__cultura_id=filtro_cultura)
    if filtro_cliente:
        ped_itens = ped_itens.filter(pedido_compra__cliente_id=filtro_cliente)
    if filtro_categoria:
        ped_itens = ped_itens.filter(produto_cadastro__categoria_id=filtro_categoria)

    planned_for = {
        r['pedido_compra__fornecedor_id']: _to_decimal(r['total'])
        for r in ped_itens.values('pedido_compra__fornecedor_id').annotate(total=Sum('total_item'))
    }
    realized_for = {
        r['faturamento__fornecedor_id']: _to_decimal(r['total'])
        for r in fat_itens.filter(faturamento__fornecedor__isnull=False).values('faturamento__fornecedor_id').annotate(total=Sum('total_item'))
    }

    forn_ids = sorted(set(planned_for.keys()) | set(realized_for.keys()))
    fornecedores_map = {f.pk: f for f in Fornecedor.objects.filter(pk__in=forn_ids)}
    fornecedores_rows = []
    max_forn = Decimal('0')
    for fid in forn_ids:
        p = planned_for.get(fid, Decimal('0'))
        r = realized_for.get(fid, Decimal('0'))
        if p == 0 and r == 0:
            continue
        max_forn = max(max_forn, p, r)
        fornecedores_rows.append({'obj': fornecedores_map.get(fid), 'planejado': p, 'realizado': r})

    for row in fornecedores_rows:
        if max_forn > 0:
            row['pct_realizado'] = int(min(Decimal('100'), _safe_div(row['realizado'] * Decimal('100'), max_forn)).quantize(Decimal('1')))
            row['pct_planejado'] = int(min(Decimal('100'), _safe_div(row['planejado'] * Decimal('100'), max_forn)).quantize(Decimal('1')))
        else:
            row['pct_realizado'] = 0
            row['pct_planejado'] = 0
        base_total = planned_total if planned_total > 0 else realized_total
        if base_total > 0:
            pct_total = _safe_div(row['planejado'] * Decimal('100'), base_total)
        else:
            pct_total = Decimal('0')
        try:
            row['pct_total'] = pct_total.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        except Exception:
            row['pct_total'] = Decimal('0')

    fornecedores_rows.sort(key=lambda x: x.get('realizado', Decimal('0')), reverse=True)

    subtitulo = 'Gestao de compras - pedidos e faturamentos'
    if licenca and not licenca.esta_vigente:
        subtitulo = 'Licenca nao vigente: acesse Licenca para renovar'

    # Safra selecionada (para exibir sempre visível e nos relatórios)
    safra_obj = None
    if filtro_safra:
        try:
            safra_obj = Safra.objects.select_related('cultura').get(pk=filtro_safra)
        except Exception:
            safra_obj = None

    return {
        'titulo': 'Dashboard',
        'subtitulo': subtitulo,
        'role_display': user.get_effective_role_display(),
        'licenca': licenca,
        # filtros
        'culturas': Cultura.objects.all().order_by('nome'),
        'safras': Safra.objects.select_related('cultura').all().order_by('-ano', 'safra'),
        'categorias': Categoria.objects.all().order_by('nome'),
        'clientes': Cliente.objects.all().order_by('cliente'),
        'filtro_cultura': filtro_cultura,
        'filtro_safra': filtro_safra,
        'filtro_categoria': filtro_categoria,
        'filtro_cliente': filtro_cliente,
        'safra_obj': safra_obj,
        # KPIs
        'planned_total': planned_total,
        'realized_total': realized_total,
        'utilizado_pct': utilizado_pct,
        'sc_ha': sc_ha,
        'economia': economia,
        'planned_area': planned_area,
        'avg_preco_prod': avg_preco_prod,
        # summaries
        'pedidos_total': pedidos_total,
        'pedidos_por_status': pedidos_por_status,
        'categorias_rows': categorias_rows,
        'max_cat': max_cat,
        'produtos_rows': produtos_rows,
        'max_prod': max_prod,
        'fornecedores_rows': fornecedores_rows,
        'max_forn': max_forn,
    }


@login_required
def dashboard(request):
    try:
        ctx = _build_dashboard_context(request)
    except DatabaseError:
        ctx = {
            'filtro_cultura': '',
            'filtro_safra': '',
            'filtro_categoria': '',
            'filtro_cliente': '',
            'culturas': [],
            'safras': [],
            'categorias': [],
            'clientes': [],
            'planned_total': Decimal('0'),
            'realized_total': Decimal('0'),
            'utilizado_pct': Decimal('0'),
            'sc_ha': Decimal('0'),
            'economia': Decimal('0'),
            'planned_area': Decimal('0'),
            'avg_preco_prod': Decimal('0'),
            'pedidos_total': Decimal('0'),
            'pedidos_por_status': [],
            'categorias_rows': [],
            'max_cat': Decimal('0'),
            'produtos_rows': [],
            'max_prod': Decimal('0'),
            'fornecedores_rows': [],
            'max_forn': Decimal('0'),
        }
        messages.warning(request, 'Banco ainda sincronizando. Atualize a página em alguns segundos.')
    return render(request, 'core/dashboard.html', ctx)


@login_required
def dashboard_report_economia(request):
    """
    Relatório (A4 Paisagem) da Dashboard para compartilhar com o cliente.
    Mantém os elementos gráficos e destaca a economia do planejamento.
    """
    try:
        ctx = _build_dashboard_context(request)
    except DatabaseError:
        messages.warning(request, 'Banco ainda sincronizando. Tente novamente em alguns segundos.')
        return redirect('core:dashboard')
    ctx['back_fallback_url'] = '/dashboard/'
    return render(request, 'core/relatorios/dashboard_economia.html', ctx)

# ------------------------------
# CRUD base helpers (restaurado)
# ------------------------------
class CrudListView(ListView):
    template_name = 'core/crud/list.html'
    paginate_by = 15
    model = None

    columns = []
    context_title = ''
    create_url_name = ''
    edit_url_name = ''
    delete_url_name = ''

    search_fields = []
    default_ordering = None

    def get_paginate_by(self, queryset):
        """
        Responsive pagination controlled by cookie `per_page`
        set in base template script:
        - 1024x768: 7
        - 1440x900: 8
        - larger: 10 (default fallback remains view paginate_by)
        """
        raw = (self.request.COOKIES.get('per_page') or '').strip()
        if raw.isdigit():
            val = int(raw)
            if val in {7, 8, 10}:
                return val
        return super().get_paginate_by(queryset)

    def get_queryset(self):
        qs = super().get_queryset()

        q = (self.request.GET.get('q') or '').strip()
        if q and self.search_fields:
            cond = Q()
            for f in self.search_fields:
                cond |= Q(**{f'{f}__icontains': q})
            qs = qs.filter(cond)

        ordering = (self.request.GET.get('o') or '').strip() or self.default_ordering
        if ordering:
            qs = qs.order_by(ordering)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = self.context_title or (self.model._meta.verbose_name_plural.title() if self.model else 'Lista')
        ctx['columns'] = self.columns
        ctx['create_url_name'] = self.create_url_name
        ctx['edit_url_name'] = self.edit_url_name
        ctx['delete_url_name'] = self.delete_url_name
        ctx['q'] = self.request.GET.get('q', '')
        return ctx


class ModalCrudListView(CrudListView):
    """
    Lista + modal (novo/editar) na mesma pagina.

    Templates como core/crud/modal_list.html (e variantes) esperam:
    - columns com {label, field, sort_query, is_active, is_desc}
    - open_modal / editing_id
    - modal_form (ou *_form em templates especificos)
    - total_registros / pagination_query / current_q / current_sort

    E salvam via POST na propria list view.
    """

    modal_form_class = None
    modal_form_context_name = 'modal_form'

    open_param = 'novo'
    edit_param = 'edit'

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)
        base_params.pop(self.open_param, None)
        base_params.pop(self.edit_param, None)

        # columns may be list of tuples: (Label, field_name)
        for col in (self.columns or []):
            if isinstance(col, (list, tuple)) and len(col) >= 2:
                label, field = col[0], col[1]
            else:
                label, field = str(col), str(col)

            params = base_params.copy()
            # toggle ordering for this field
            if active_field == field:
                params['o'] = field if is_desc else f'-{field}'
            else:
                params['o'] = field

            cols.append(
                {
                    'label': label,
                    'field': field,
                    'sort_query': params.urlencode(),
                    'is_active': active_field == field,
                    'is_desc': is_desc if active_field == field else False,
                }
            )
        return cols

    def _get_modal_instance(self, editing_id):
        if not editing_id:
            return None
        try:
            return self.model.objects.filter(pk=int(editing_id)).first()
        except Exception:
            return None

    def _get_modal_form(self, *, instance=None, data=None):
        form_cls = self.modal_form_class or self.form_class
        if not form_cls:
            raise ValueError('modal_form_class/form_class nao configurado')
        if instance is not None:
            return form_cls(data=data, instance=instance)
        return form_cls(data=data)

    def post(self, request, *args, **kwargs):
        if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
            return redirect('core:dashboard')

        editing_id = (request.POST.get('edit_id') or '').strip()
        instance = self._get_modal_instance(editing_id) if editing_id else None
        form = self._get_modal_form(instance=instance, data=request.POST)

        if form.is_valid():
            obj = form.save()
            messages.success(request, 'Registro salvo com sucesso.')

            # If opened as a popup (cadastro rapido), notify opener to refresh selects.
            if (request.GET.get('popup') or '').strip() == '1':
                import json

                payload = {
                    'type': 'crud:saved',
                    'model': getattr(self.model, '_meta', None).model_name if self.model else '',
                    'id': getattr(obj, 'pk', None),
                    'label': str(obj) if obj is not None else '',
                    'select': (request.GET.get('select') or '').strip(),
                }
                return render(
                    request,
                    'core/crud/popup_saved.html',
                    {'payload_json': json.dumps(payload)},
)

            return redirect(request.path)

        # Re-render list with modal open and errors
        self.object_list = self.get_queryset()
        context = self.get_context_data()
        context['open_modal'] = True
        context['editing_id'] = editing_id or None
        context[self.modal_form_context_name] = form
        messages.error(request, 'Nao foi possivel salvar. Verifique os campos.')
        return render(request, self.template_name, context)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Normaliza os nomes usados pelos templates (legado)
        ctx['current_q'] = (self.request.GET.get('q') or '').strip()
        ctx['current_sort'] = (self.request.GET.get('o') or '').strip()

        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()

        try:
            ctx['total_registros'] = self.get_queryset().count()
        except Exception:
            ctx['total_registros'] = 0

        # Columns enriched for sorting UI
        ctx['columns'] = self._build_columns()

        open_modal = (self.request.GET.get(self.open_param) or '').strip() == '1'
        editing_id = (self.request.GET.get(self.edit_param) or '').strip()
        if editing_id and not editing_id.isdigit():
            editing_id = ''

        ctx['open_modal'] = open_modal or bool(editing_id)
        ctx['editing_id'] = editing_id or None

        # Provide modal form instance
        if ctx['open_modal']:
            instance = self._get_modal_instance(editing_id) if editing_id else None
            ctx[self.modal_form_context_name] = self._get_modal_form(instance=instance)
        else:
            ctx[self.modal_form_context_name] = self._get_modal_form()

        # Optional per-view labels/descriptions used by modal_list templates.
        for k in ('create_button_label', 'list_description'):
            if hasattr(self, k):
                ctx[k] = getattr(self, k)

        return ctx


class CrudCreateView(CreateView):
    template_name = 'core/crud/form.html'
    model = None
    form_class = None
    success_url = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = getattr(self, 'context_title', None) or f'Novo {self.model._meta.verbose_name.title()}'
        return ctx


class CrudUpdateView(UpdateView):
    template_name = 'core/crud/form.html'
    model = None
    form_class = None
    success_url = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = getattr(self, 'context_title', None) or f'Editar {self.model._meta.verbose_name.title()}'
        return ctx


class CrudDeleteView(DeleteView):
    template_name = 'core/crud/confirm_delete.html'
    model = None
    success_url = None


# ------------------------------
# Paginas "hub" do painel
# ------------------------------
@login_required
def cadastros_page(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    return render(
        request,
        'core/cadastros.html',
        {
            'titulo': 'Cadastros Gerais e Administrativos',
            'safras': Safra.objects.all().order_by('-ano', 'safra')[:10],
            'clientes': Cliente.objects.all().order_by('cliente')[:10],
            'fornecedores': Fornecedor.objects.all().order_by('fornecedor')[:10],
            'produtores': Produtor.objects.all().order_by('produtor', 'fazenda')[:10],
        },
    )


@login_required
def compras_page(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    return render(
        request,
        'core/compras.html',
        {
            'titulo': 'Modulo Compras',
            'pedidos': PedidoCompra.objects.select_related('cliente', 'produtor').order_by('-data')[:15],
            'cotacoes': CotacaoProduto.objects.select_related('safra', 'fornecedor').order_by('-data')[:15],
        },
    )


@login_required
def financeiro_page(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    return render(
        request,
        'core/financeiro.html',
        {
            'titulo': 'Financeiro',
            'contas': ContaPagar.objects.select_related('cliente').order_by('-vencimento')[:15],
            'faturamentos': Faturamento.objects.select_related('produtor').order_by('-data')[:15],
        },
    )


@login_required
def licencas_page(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    lic = None
    perfil = getattr(request.user, 'perfil_licenca', None)
    if perfil:
        lic = perfil.licenca

    return render(
        request,
        'core/licencas.html',
        {
            'titulo': 'Licenca',
            'licenca': lic,
            'historico': Licenca.objects.all().order_by('-updated_at')[:20],
        },
    )


@login_required
def backup_page(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    settings_obj, _ = BackupSettings.objects.get_or_create(pk=1)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'save_settings':
            enabled = (request.POST.get('enabled') or '') == '1'
            daily_time = (request.POST.get('daily_time') or '').strip() or "02:00"
            retention_days = int((request.POST.get('retention_days') or '14') or 14)

            try:
                # HTML time input returns "HH:MM"
                dt = parse_daily_time(daily_time)
            except Exception:
                messages.error(request, "Horario invalido. Use HH:MM (ex.: 02:00).")
                return redirect('core:backup_page')

            settings_obj.enabled = enabled
            settings_obj.daily_time = dt
            settings_obj.retention_days = max(1, min(365, retention_days))
            settings_obj.save()
            messages.success(request, "Configuracao de backup salva com sucesso.")
            return redirect('core:backup_page')

        if action == 'run_now':
            try:
                create_backup(kind=BackupFile.KIND_MANUAL)
                messages.success(request, "Backup gerado com sucesso.")
            except Exception as e:
                messages.error(request, f"Falha ao gerar backup: {e}")
            return redirect('core:backup_page')

        if action == 'restore':
            uploaded = request.FILES.get('backup_file')
            confirm = (request.POST.get('confirm') or '').strip().upper()
            if not uploaded:
                messages.error(request, "Selecione um arquivo .zip de backup.")
                return redirect('core:backup_page')
            if not uploaded.name.lower().endswith('.zip'):
                messages.error(request, "Arquivo invalido. Envie um .zip gerado pelo sistema.")
                return redirect('core:backup_page')
            if confirm != "RESTORE":
                messages.error(request, 'Confirmacao invalida. Digite "RESTORE" para restaurar.')
                return redirect('core:backup_page')

            try:
                restore_backup_from_zip(uploaded)
                messages.success(request, "Backup restaurado. Faca login novamente.")
                return redirect('/accounts/logout/')
            except Exception as e:
                messages.error(request, f"Falha ao restaurar backup: {e}")
                return redirect('core:backup_page')

        return HttpResponseBadRequest("Acao invalida.")

    backups = BackupFile.objects.all()[:50]
    next_run = compute_next_run(settings_obj)

    return render(
        request,
        'core/backup.html',
        {
            'titulo': 'Backup',
            'backup_settings': settings_obj,
            'backups': backups,
            'next_run': next_run,
        },
    )


@login_required
def backup_download(request, pk: int):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    obj = get_object_or_404(BackupFile, pk=pk)
    from pathlib import Path

    p = Path(obj.file_path)
    if not p.exists():
        messages.error(request, "Arquivo nao encontrado no servidor.")
        return redirect('core:backup_page')

    return FileResponse(open(p, "rb"), as_attachment=True, filename=obj.file_name)


# ------------------------------
# API auxiliares
# ------------------------------
@login_required
def produtores_por_cliente(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    cliente_id = (request.GET.get('cliente') or request.GET.get('cliente_id') or '').strip()
    if not cliente_id.isdigit():
        return JsonResponse({'items': []})

    qs = Produtor.objects.filter(cliente_id=int(cliente_id)).order_by('produtor', 'fazenda')
    items = []
    for p in qs:
        produtor_nome = (p.produtor or '').strip()
        fazenda_nome = (p.fazenda or '').strip()
        label = f'{produtor_nome} - {fazenda_nome}' if fazenda_nome else produtor_nome
        items.append(
            {
                'id': p.pk,
                'nome': label,  # compatibilidade legada
                'label': label,
                'produtor': produtor_nome,
                'fazenda': fazenda_nome,
            }
        )
    return JsonResponse({'items': items})


@login_required
def options_produtos(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    qs = Produto.objects.all().order_by('nome')
    items = [{'id': p.pk, 'label': p.nome} for p in qs]
    return JsonResponse({'items': items})


@login_required
def options_unidades(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    qs = Unidade.objects.all().order_by('nome')
    # Prefer abbreviated label in selects (more compact in tables/forms).
    items = [{'id': u.pk, 'label': (u.unidade_abreviado or u.nome)} for u in qs]
    return JsonResponse({'items': items})


@login_required
def options_safras(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    qs = Safra.objects.all().order_by('-ano', 'safra')
    items = [{'id': s.pk, 'label': s.safra} for s in qs]
    return JsonResponse({'items': items})


@login_required
def options_custos(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    qs = Custo.objects.all().order_by('nome')
    items = [{'id': c.pk, 'label': c.nome} for c in qs]
    return JsonResponse({'items': items})


@login_required
def options_clientes(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    qs = Cliente.objects.all().order_by('cliente')
    items = [{'id': c.pk, 'label': c.cliente} for c in qs]
    return JsonResponse({'items': items})


@login_required
def options_fornecedores(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    qs = Fornecedor.objects.all().order_by('fornecedor')
    items = [{'id': f.pk, 'label': f.fornecedor} for f in qs]
    return JsonResponse({'items': items})


# ------------------------------
# CRUD simples (Cadastros)
# ------------------------------

def _make_simple_crud(name_prefix, model_cls, form_cls, list_template='core/crud/modal_list.html', search_fields=None, ordering='-id'):
    search_fields = search_fields or []

    ListKls = type(
        f'{name_prefix.title().replace("_", "")}ListView',
        (GestorRequiredMixin, ModalCrudListView),
        {
            'template_name': list_template,
            'model': model_cls,
            'modal_form_class': form_cls,
            'context_title': model_cls._meta.verbose_name_plural.title(),
            'create_url_name': f'core:{name_prefix}_create',
            'edit_url_name': f'core:{name_prefix}_update',
            'delete_url_name': f'core:{name_prefix}_delete',
            'default_ordering': ordering,
            'search_fields': search_fields,
        },
    )

    CreateKls = type(
        f'{name_prefix.title().replace("_", "")}CreateView',
        (GestorRequiredMixin, CrudCreateView),
        {
            'model': model_cls,
            'form_class': form_cls,
            'success_url': reverse_lazy(f'core:{name_prefix}_list'),
        },
    )

    UpdateKls = type(
        f'{name_prefix.title().replace("_", "")}UpdateView',
        (GestorRequiredMixin, CrudUpdateView),
        {
            'model': model_cls,
            'form_class': form_cls,
            'success_url': reverse_lazy(f'core:{name_prefix}_list'),
        },
    )

    DeleteKls = type(
        f'{name_prefix.title().replace("_", "")}DeleteView',
        (GestorRequiredMixin, CrudDeleteView),
        {
            'model': model_cls,
            'success_url': reverse_lazy(f'core:{name_prefix}_list'),
        },
    )

    return ListKls, CreateKls, UpdateKls, DeleteKls



SafraListView, SafraCreateView, SafraUpdateView, SafraDeleteView = _make_simple_crud(
    'safra',
    Safra,
    SafraForm,
    list_template='core/crud/safra_modal_list.html',
    search_fields=['safra', 'cultura__nome'],
    ordering='-ano',
)
SafraListView.columns = [
    ('Safra', 'safra'),
    ('Ano', 'ano'),
    ('Cultura', 'cultura'),
    ('Data Inicio', 'data_inicio'),
    ('Data Fim', 'data_fim'),
    ('Status', 'status'),
]
SafraListView.modal_form_context_name = 'safra_form'
SafraListView.paginate_by = 10

CulturaListView, CulturaCreateView, CulturaUpdateView, CulturaDeleteView = _make_simple_crud(
    'cultura',
    Cultura,
    CulturaForm,
    list_template='core/crud/cultura_modal_list.html',
    search_fields=['nome'],
    ordering='nome',
)
CulturaListView.columns = [('Cultura', 'nome')]
CulturaListView.modal_form_context_name = 'cultura_form'
CulturaListView.paginate_by = 10

CustoListView, CustoCreateView, CustoUpdateView, CustoDeleteView = _make_simple_crud(
    'custo', Custo, CustoForm, search_fields=['nome'], ordering='nome'
)
CustoListView.columns = [('Custo', 'nome')]
CustoListView.paginate_by = 10

CategoriaListView, CategoriaCreateView, CategoriaUpdateView, CategoriaDeleteView = _make_simple_crud(
    'categoria', Categoria, CategoriaForm, search_fields=['nome'], ordering='nome'
)
CategoriaListView.columns = [('Categoria', 'nome')]
CategoriaListView.paginate_by = 10

UnidadeListView, UnidadeCreateView, UnidadeUpdateView, UnidadeDeleteView = _make_simple_crud(
    'unidade', Unidade, UnidadeForm, search_fields=['nome', 'unidade_abreviado'], ordering='nome'
)
UnidadeListView.columns = [('Unidade', 'nome'), ('Volume', 'volume'), ('Abrev', 'unidade_abreviado')]
UnidadeListView.paginate_by = 10

FormaPagamentoListView, FormaPagamentoCreateView, FormaPagamentoUpdateView, FormaPagamentoDeleteView = _make_simple_crud(
    'forma_pagamento', FormaPagamento, FormaPagamentoForm, search_fields=['pagamento'], ordering='pagamento'
)
FormaPagamentoListView.columns = [('Pagamento', 'pagamento'), ('Parcelas', 'parcelas'), ('Prazo', 'prazo')]
FormaPagamentoListView.paginate_by = 10

OperacaoListView, OperacaoCreateView, OperacaoUpdateView, OperacaoDeleteView = _make_simple_crud(
    'operacao', Operacao, OperacaoForm, search_fields=['operacao'], ordering='operacao'
)
OperacaoListView.columns = [('Operacao', 'operacao'), ('Tipo', 'tipo')]
OperacaoListView.paginate_by = 10

FornecedorListView, FornecedorCreateView, FornecedorUpdateView, FornecedorDeleteView = _make_simple_crud(
    'fornecedor', Fornecedor, FornecedorForm, search_fields=['fornecedor', 'cnpj', 'cidade'], ordering='fornecedor'
)
FornecedorListView.columns = [
    ('Fornecedor', 'fornecedor'),
    ('CNPJ', 'cnpj'),
    ('Cidade', 'cidade'),
    ('UF', 'uf'),
    ('Status', 'status'),
]
FornecedorListView.paginate_by = 10
ClienteListView, ClienteCreateView, ClienteUpdateView, ClienteDeleteView = _make_simple_crud(
    'cliente', Cliente, ClienteForm, search_fields=['cliente', 'apelido', 'cpf_cnpj'], ordering='cliente'
)
ClienteListView.columns = [
    ('Cliente', 'cliente'),
    ('Apelido', 'apelido'),
    ('CPF/CNPJ', 'cpf_cnpj'),
    ('Status', 'status'),
    ('Limite', 'limite_compra'),
]
ClienteListView.paginate_by = 10
ProdutorListView, ProdutorCreateView, ProdutorUpdateView, ProdutorDeleteView = _make_simple_crud(
    'produtor', Produtor, ProdutorForm, search_fields=['produtor', 'cpf', 'fazenda', 'cidade'], ordering='produtor'
)
ProdutorListView.columns = [
    ('Produtor', 'produtor'),
    ('Fazenda', 'fazenda'),
    ('Inscricao', 'ie'),
    ('CPF', 'cpf'),
    ('HA', 'ha'),
    ('Cliente', 'cliente'),
    ('Status', 'status'),
]
ProdutorListView.paginate_by = 10
PropriedadeListView, PropriedadeCreateView, PropriedadeUpdateView, PropriedadeDeleteView = _make_simple_crud(
    'propriedade', Propriedade, PropriedadeForm, search_fields=['propriedade', 'matricula', 'sicar'], ordering='propriedade'
)
PropriedadeListView.columns = [
    ('Propriedade', 'propriedade'),
    ('Produtor', 'produtor'),
    ('HA', 'ha'),
    ('Matricula', 'matricula'),
    ('Sicar', 'sicar'),
    ('Localizacao', 'localizacao'),
]
ProdutoListView, ProdutoCreateView, ProdutoUpdateView, ProdutoDeleteView = _make_simple_crud(
    'produto', Produto, ProdutoForm, search_fields=['nome', 'nome_abreviado', 'npk'], ordering='nome'
)
ProdutoListView.columns = [
    ('Produto', 'nome'),
    ('Abreviado', 'nome_abreviado'),
    ('NPK', 'npk'),
    ('Variedade', 'variedade'),
    ('Custo', 'custo'),
    ('Categoria', 'categoria'),
    ('Status', 'status'),
]
ProdutoListView.paginate_by = 10

CotacaoListView, CotacaoCreateView, CotacaoUpdateView, CotacaoDeleteView = _make_simple_crud(
    'cotacao', CotacaoProduto, CotacaoProdutoForm, search_fields=['produto', 'fornecedor__fornecedor'], ordering='-data'
)
CotacaoListView.columns = [
    ('Data', 'data'),
    ('Safra', 'safra'),
    ('Fornecedor', 'fornecedor'),
    ('Vencimento', 'vencimento'),
    ('Produto', 'produto'),
    ('Preco', 'valor_total'),
]
CotacaoListView.create_button_label = '+ Nova Cotacao'
CotacaoListView.list_description = 'Gestao de cotacoes de preco por safra e fornecedor'


# ------------------------------
# Planejamento (CRUD com itens)
# ------------------------------
class PlanejamentoListView(GestorRequiredMixin, ListView):
    model = Planejamento
    template_name = 'core/planejamento/list.html'
    paginate_by = 15

    def get_paginate_by(self, queryset):
        raw = (self.request.COOKIES.get('per_page') or '').strip()
        if raw.isdigit():
            val = int(raw)
            if val in {7, 8, 10}:
                return val
        return super().get_paginate_by(queryset)

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'custo', 'cliente')
            .order_by('-data', '-id')
        )

        apply_filters = (self.request.GET.get('apply') or '').strip()
        if not apply_filters:
            return qs

        q = (self.request.GET.get('q') or '').strip()
        safra_id = (self.request.GET.get('safra') or '').strip()
        cliente_id = (self.request.GET.get('cliente') or '').strip()
        custo_id = (self.request.GET.get('custo') or '').strip()
        venc_ini = (self.request.GET.get('venc_ini') or '').strip()
        venc_fim = (self.request.GET.get('venc_fim') or '').strip()

        if q:
            qs = qs.filter(
                Q(cliente__cliente__icontains=q)
                | Q(safra__safra__icontains=q)
                | Q(custo__nome__icontains=q)
            )
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if custo_id:
            qs = qs.filter(custo_id=custo_id)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['create_url_name'] = 'core:planejamento_create'
        ctx['edit_url_name'] = 'core:planejamento_update'
        ctx['delete_url_name'] = 'core:planejamento_delete'
        ctx['detail_url_name'] = 'core:planejamento_detail_modal'
        ctx['report_resumo_url_name'] = 'core:planejamento_report_resumo'
        ctx['report_analitico_url_name'] = 'core:planejamento_report_analitico'

        apply_filters = (self.request.GET.get('apply') or '').strip()

        # Filtros (padrao igual Faturamento/Pedidos)
        ctx['safras'] = Safra.objects.all().order_by('-ano', 'safra')
        ctx['clientes'] = Cliente.objects.all().order_by('cliente')
        ctx['custos'] = Custo.objects.all().order_by('nome')

        ctx['current_q'] = self.request.GET.get('q', '') if apply_filters else ''
        ctx['filtro_safra'] = self.request.GET.get('safra', '') if apply_filters else ''
        ctx['filtro_cliente'] = self.request.GET.get('cliente', '') if apply_filters else ''
        ctx['filtro_custo'] = self.request.GET.get('custo', '') if apply_filters else ''
        ctx['filtro_venc_ini'] = self.request.GET.get('venc_ini', '') if apply_filters else ''
        ctx['filtro_venc_fim'] = self.request.GET.get('venc_fim', '') if apply_filters else ''

        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()
        ctx['report_query'] = params.urlencode()
        try:
            ctx['total_registros'] = self.get_queryset().count()
        except Exception:
            ctx['total_registros'] = 0
        return ctx


class PlanejamentoModalDetailView(GestorRequiredMixin, DetailView):
    model = Planejamento
    template_name = 'core/planejamento/detail_modal.html'

    def get_queryset(self):
        return super().get_queryset().select_related('safra', 'custo', 'cliente').prefetch_related('itens__unidade')


class PlanejamentoReportResumoView(GestorRequiredMixin, DetailView):
    model = Planejamento
    template_name = 'core/relatorios/planejamento_resumo.html'

    def get_queryset(self):
        return super().get_queryset().select_related('safra', 'custo', 'cliente').prefetch_related('itens__unidade')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/planejamento/'
        pl = self.object
        ctx['planejamento'] = pl
        itens = list(pl.itens.select_related('unidade', 'produto_cadastro').all())
        ctx['itens'] = itens
        # Licenca (para cabecalho)
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        # Totals for footer row (SC/HA total based on total area)
        total_area = Decimal('0')
        total_valor = Decimal('0')
        for it in itens:
            try:
                total_area += (it.area_ha or Decimal('0'))
            except Exception:
                pass
            try:
                total_valor += (it.total_item or Decimal('0'))
            except Exception:
                pass
        ctx['total_area'] = total_area
        ctx['total_valor'] = total_valor
        try:
            if total_area and total_area > 0 and pl.preco_produto and pl.preco_produto > 0:
                ctx['total_sc_ha'] = (total_valor / total_area / pl.preco_produto)
            else:
                ctx['total_sc_ha'] = Decimal('0')
        except Exception:
            ctx['total_sc_ha'] = Decimal('0')

        # Per-row SC/HA is derived from totals and header price to be consistent even for legacy rows.
        q1 = Decimal('0.1')
        for it in itens:
            try:
                area = (it.area_ha or Decimal('0'))
                total_item = (it.total_item or Decimal('0'))
                if area > 0 and pl.preco_produto and pl.preco_produto > 0:
                    sc = (total_item / area / pl.preco_produto)
                else:
                    sc = Decimal('0')
                setattr(it, 'sc_ha_report', sc.quantize(q1, rounding=ROUND_HALF_UP))
            except Exception:
                setattr(it, 'sc_ha_report', Decimal('0'))
        return ctx


class PlanejamentoReportAnaliticoView(GestorRequiredMixin, ListView):
    model = Planejamento
    template_name = 'core/relatorios/planejamento_analitico.html'
    context_object_name = 'planejamentos'

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'custo', 'cliente')
            .prefetch_related('itens__unidade')
            .order_by('-data', '-id')
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(cliente__cliente__icontains=q) | Q(safra__safra__icontains=q) | Q(custo__nome__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/planejamento/'
        # Licenca (para cabecalho)
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None
        return ctx


class PlanejamentoFormMixin(GestorRequiredMixin, View):
    template_name = 'core/planejamento/form.html'
    success_url = reverse_lazy('core:planejamento_list')

    def get_item_formset(self, instance, data=None):
        FormSet = inlineformset_factory(
            Planejamento,
            PlanejamentoItem,
            form=PlanejamentoItemForm,
            extra=1,
            can_delete=True,
        )
        return FormSet(data=data, instance=instance)

    def _calc_totais(self, planejamento, formset):
        total = Decimal('0')
        q2 = Decimal('0.01')
        for f in formset.forms:
            if not hasattr(f, 'cleaned_data'):
                continue
            if f.cleaned_data.get('DELETE'):
                continue
            area = f.cleaned_data.get('area_ha') or Decimal('0')
            qtd = f.cleaned_data.get('quantidade') or Decimal('0')
            preco = f.cleaned_data.get('preco') or Decimal('0')
            desc = f.cleaned_data.get('desconto') or Decimal('0')
            # Keep totals with 2 decimal places to match DB field precision and avoid
            # Decimal quantize errors with large/over-precise intermediate values.
            item_total = (qtd * preco) - desc
            try:
                item_total = item_total.quantize(q2, rounding=ROUND_HALF_UP)
            except Exception:
                item_total = Decimal('0')
            if item_total < 0:
                item_total = Decimal('0')
            f.instance.total_item = item_total
            if area and area > 0:
                try:
                    # Store SC/HA (sacas por hectare) using the header price (preco_produto).
                    # custo_por_ha = item_total / area; sc_ha = custo_por_ha / preco_produto
                    custo_por_ha = (item_total / area)
                    if planejamento.preco_produto and planejamento.preco_produto > 0:
                        sc_ha = (custo_por_ha / planejamento.preco_produto)
                    else:
                        sc_ha = Decimal('0')
                    f.instance.custo_ha = sc_ha.quantize(q2, rounding=ROUND_HALF_UP)
                except Exception:
                    f.instance.custo_ha = Decimal('0')
            else:
                f.instance.custo_ha = Decimal('0')
            total += item_total
        try:
            total = total.quantize(q2, rounding=ROUND_HALF_UP)
        except Exception:
            total = Decimal('0')
        planejamento.valor_total = total

    def get(self, request, pk=None):
        instance = Planejamento.objects.filter(pk=pk).first() if pk else Planejamento()
        form = PlanejamentoForm(instance=instance)
        formset = self.get_item_formset(instance)
        return render(
            request,
            self.template_name,
            {'form': form, 'formset': formset, 'titulo': 'Planejamento', 'modo_edicao': bool(pk)},
        )

    def post(self, request, pk=None):
        instance = Planejamento.objects.filter(pk=pk).first() if pk else Planejamento()
        form = PlanejamentoForm(request.POST, instance=instance)
        formset = self.get_item_formset(instance, data=request.POST)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                planejamento = form.save(commit=False)
                self._calc_totais(planejamento, formset)
                planejamento.save()
                formset.instance = planejamento
                formset.save()
                messages.success(request, 'Registro salvo com sucesso.')
            return redirect(self.success_url)

        messages.error(request, 'Nao foi possivel salvar. Verifique os campos.')
        return render(
            request,
            self.template_name,
            {'form': form, 'formset': formset, 'titulo': 'Planejamento', 'modo_edicao': bool(pk)},
        )


class PlanejamentoCreateView(PlanejamentoFormMixin):
    pass


class PlanejamentoUpdateView(PlanejamentoFormMixin):
    def get(self, request, pk):
        return super().get(request, pk=pk)

    def post(self, request, pk):
        return super().post(request, pk=pk)


class PlanejamentoDeleteView(GestorRequiredMixin, CrudDeleteView):
    model = Planejamento
    success_url = reverse_lazy('core:planejamento_list')
    template_name = 'core/crud/confirm_delete.html'


# ------------------------------
# Pedidos (CRUD com itens)
# ------------------------------
class PedidoCompraListView(GestorRequiredMixin, ListView):
    model = PedidoCompra
    template_name = 'core/pedidos/list.html'
    paginate_by = 8

    def get_paginate_by(self, queryset):
        raw = (self.request.COOKIES.get('per_page') or '').strip()
        if raw.isdigit():
            val = int(raw)
            if val in {7, 8, 10}:
                return val
        return super().get_paginate_by(queryset)

    def get_queryset(self):
        qs = super().get_queryset().select_related('safra', 'cliente', 'produtor', 'fornecedor').order_by('-data', '-id')

        # Padrao: quando abrir a tela, nao "grudar" filtros antigos.
        # Os filtros so entram em vigor quando o usuario clica em "Aplicar filtros" (apply=1).
        apply_filters = (self.request.GET.get('apply') or '').strip()
        if not apply_filters:
            return qs

        q = (self.request.GET.get('q') or '').strip()
        pedido_num = (self.request.GET.get('pedido') or '').strip()
        safra_id = (self.request.GET.get('safra') or '').strip()
        cliente_id = (self.request.GET.get('cliente') or '').strip()
        produtor_id = (self.request.GET.get('produtor') or '').strip()
        fornecedor_id = (self.request.GET.get('fornecedor') or '').strip()
        status = (self.request.GET.get('status') or '').strip()
        venc_ini = (self.request.GET.get('venc_ini') or '').strip()
        venc_fim = (self.request.GET.get('venc_fim') or '').strip()

        if q:
            qs = qs.filter(
                Q(pedido__icontains=q)
                | Q(cliente__cliente__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
            )
        if pedido_num:
            qs = qs.filter(pedido__icontains=pedido_num)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_id:
            qs = qs.filter(produtor_id=produtor_id)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if status:
            qs = qs.filter(status=status)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        return qs


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        apply_filters = (self.request.GET.get('apply') or '').strip()
        qs = self.get_queryset()

        # Cards (padrao igual Faturamento)
        try:
            itens_qs = PedidoCompraItem.objects.filter(pedido__in=qs)
            qtd_total = itens_qs.aggregate(total=Sum('quantidade'))['total'] or 0
        except Exception:
            qtd_total = 0

        try:
            total_pedidos = qs.aggregate(total=Sum('valor_total'))['total'] or 0
        except Exception:
            total_pedidos = 0

        preco_medio = 0
        if qtd_total:
            try:
                preco_medio = Decimal(total_pedidos or 0) / Decimal(qtd_total or 1)
            except Exception:
                preco_medio = 0

        # Links usados pelo template
        ctx['create_url_name'] = 'core:pedido_create'
        ctx['edit_url_name'] = 'core:pedido_update'
        ctx['delete_url_name'] = 'core:pedido_delete'

        ctx['current_q'] = self.request.GET.get('q', '') if apply_filters else ''
        ctx['current_sort'] = self.request.GET.get('sort', '')

        # Filtros (igual Faturamento)
        ctx['safras'] = Safra.objects.all().order_by('-ano', 'safra')
        ctx['clientes'] = Cliente.objects.all().order_by('cliente')
        ctx['produtores'] = Produtor.objects.all().order_by('produtor', 'fazenda')
        ctx['fornecedores'] = Fornecedor.objects.all().order_by('fornecedor')
        ctx['status_choices'] = StatusPedidoCompra.choices

        ctx['filtro_safra'] = self.request.GET.get('safra', '') if apply_filters else ''
        ctx['filtro_cliente'] = self.request.GET.get('cliente', '') if apply_filters else ''
        ctx['filtro_produtor'] = self.request.GET.get('produtor', '') if apply_filters else ''
        ctx['filtro_fornecedor'] = self.request.GET.get('fornecedor', '') if apply_filters else ''
        ctx['filtro_status'] = self.request.GET.get('status', '') if apply_filters else ''
        ctx['filtro_pedido'] = self.request.GET.get('pedido', '') if apply_filters else ''
        ctx['filtro_venc_ini'] = self.request.GET.get('venc_ini', '') if apply_filters else ''
        ctx['filtro_venc_fim'] = self.request.GET.get('venc_fim', '') if apply_filters else ''

        # Para paginacao / relatorios manter filtros sem page
        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()
        ctx['report_query'] = params.urlencode()

        try:
            ctx['total_registros'] = self.get_queryset().count()
        except Exception:
            ctx['total_registros'] = 0

        ctx['card_pedidos'] = qs.count()
        ctx['card_quantidade'] = qtd_total
        ctx['card_preco_medio'] = preco_medio
        ctx['card_total_pedidos'] = total_pedidos

        return ctx


class PedidoCompraModalDetailView(GestorRequiredMixin, DetailView):
    model = PedidoCompra
    template_name = 'core/pedidos/detail_modal.html'

# ------------------------------
# Relatorios (Pedidos / Faturamento / Contas)
# ------------------------------
class PedidoCompraReportResumoView(GestorRequiredMixin, DetailView):
    model = PedidoCompra
    template_name = 'core/relatorios/pedido_resumo.html'

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('safra', 'cliente', 'produtor', 'fornecedor')
            .prefetch_related('itens', 'faturamentos', 'faturamentos__itens')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/pedidos/'
        pedido = self.object
        ctx['pedido'] = pedido
        itens = list(pedido.itens.select_related('produto_cadastro', 'unidade').all())
        # Build saldo por item (quantidade a receber) a partir das notas (faturamentos) vinculadas ao pedido.
        faturamentos = list(pedido.faturamentos.prefetch_related('itens').all())
        faturado_por_produto = {}
        for nf in faturamentos:
            for it in nf.itens.all():
                key = None
                if it.produto_cadastro_id:
                    key = ('cad', it.produto_cadastro_id)
                else:
                    key = ('txt', (it.produto or '').strip().upper())
                faturado_por_produto[key] = faturado_por_produto.get(key, Decimal('0')) + (it.quantidade or Decimal('0'))

        for it in itens:
            if it.produto_cadastro_id:
                key = ('cad', it.produto_cadastro_id)
            else:
                key = ('txt', (it.produto or '').strip().upper())
            faturado = faturado_por_produto.get(key, Decimal('0'))
            try:
                saldo = (it.quantidade or Decimal('0')) - faturado
            except Exception:
                saldo = Decimal('0')
            if saldo < 0:
                saldo = Decimal('0')
            setattr(it, 'saldo_qtd', saldo)
        ctx['itens'] = itens
        ctx['faturamentos'] = pedido.faturamentos.select_related('fornecedor', 'safra', 'produtor', 'pedido').all()
        # Licenca (para cabecalho)
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None
        ctx['contas'] = (
            ContaPagar.objects.filter(pedido_id=pedido.pk)
            .select_related('cliente', 'produtor', 'safra', 'fornecedor', 'pedido', 'faturamento')
            .order_by('-vencimento', '-id')
        )
        return ctx


class PedidoCompraReportAnaliticoView(GestorRequiredMixin, ListView):
    model = PedidoCompra
    template_name = 'core/relatorios/pedido_analitico.html'
    context_object_name = 'pedidos'

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'cliente', 'produtor', 'fornecedor')
            .prefetch_related(
                'itens__unidade',
                'itens__produto_cadastro',
                'faturamentos__fornecedor',
                'faturamentos__produtor',
                'faturamentos__itens__unidade',
                'faturamentos__itens__produto_cadastro',
            )
            .order_by('-data', '-id')
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(pedido__icontains=q) | Q(cliente__cliente__icontains=q) | Q(produtor__produtor__icontains=q))
        return qs

    @staticmethod
    def _produtor_label(produtor: Produtor | None) -> str:
        if not produtor:
            return '-'
        try:
            base = produtor.produtor
        except Exception:
            base = str(produtor)
        faz = ''
        try:
            faz = (produtor.fazenda or '').strip()
        except Exception:
            faz = ''
        return f'{base} - {faz}' if faz else base

    @staticmethod
    def _cliente_label(cliente: Cliente | None) -> str:
        if not cliente:
            return '-'
        try:
            return cliente.cliente
        except Exception:
            return str(cliente)

    @staticmethod
    def _safra_label(safra: Safra | None) -> str:
        if not safra:
            return '-'
        try:
            return safra.safra
        except Exception:
            return str(safra)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/pedidos/'

        # Licenca (cabecalho)
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        pedidos = list(ctx.get('pedidos') or [])
        groups = OrderedDict()

        for p in pedidos:
            gkey = (p.cliente_id, p.safra_id)
            if gkey not in groups:
                groups[gkey] = {
                    'cliente': p.cliente,
                    'safra': p.safra,
                    'total': Decimal('0'),
                    'pedido_ids': [],
                    'prod_totals': defaultdict(lambda: Decimal('0')),
                    'emp_totals': defaultdict(lambda: Decimal('0')),
                    'nf_prod_groups': [],  # computed below
                }
            g = groups[gkey]
            g['pedido_ids'].append(p.pk)
            try:
                g['total'] += (p.valor_total or Decimal('0'))
            except Exception:
                pass

            prod_lbl = self._produtor_label(p.produtor)
            forn_lbl = '-'
            try:
                forn_lbl = p.fornecedor.fornecedor if p.fornecedor else '-'
            except Exception:
                forn_lbl = str(p.fornecedor) if p.fornecedor else '-'

            try:
                g['prod_totals'][prod_lbl] += (p.valor_total or Decimal('0'))
            except Exception:
                pass
            try:
                g['emp_totals'][forn_lbl] += (p.valor_total or Decimal('0'))
            except Exception:
                pass

        # Build NF groups per (cliente,safra)
        for g in groups.values():
            pedido_ids = g['pedido_ids']
            faturamentos = (
                Faturamento.objects.filter(pedido_id__in=pedido_ids)
                .select_related('fornecedor', 'produtor')
                .prefetch_related('itens__unidade', 'itens__produto_cadastro')
                .order_by('produtor__produtor', 'fornecedor__fornecedor', 'data', 'nota_fiscal', 'id')
            )

            prod_map = OrderedDict()
            for nf in faturamentos:
                prod_lbl = self._produtor_label(nf.produtor)
                if prod_lbl not in prod_map:
                    prod_map[prod_lbl] = {'produtor_label': prod_lbl, 'total': Decimal('0'), 'fornecedores': OrderedDict()}

                pnode = prod_map[prod_lbl]
                forn_lbl = '-'
                try:
                    forn_lbl = nf.fornecedor.fornecedor if nf.fornecedor else '-'
                except Exception:
                    forn_lbl = str(nf.fornecedor) if nf.fornecedor else '-'

                if forn_lbl not in pnode['fornecedores']:
                    pnode['fornecedores'][forn_lbl] = {'fornecedor_label': forn_lbl, 'total': Decimal('0'), 'linhas': []}
                fnode = pnode['fornecedores'][forn_lbl]

                for it in nf.itens.all():
                    produto_nome = ''
                    try:
                        produto_nome = it.produto_cadastro.nome if it.produto_cadastro else (it.produto or '')
                    except Exception:
                        produto_nome = it.produto or ''
                    un_lbl = ''
                    try:
                        un_lbl = it.unidade.unidade_abreviado or str(it.unidade)
                    except Exception:
                        un_lbl = str(it.unidade) if it.unidade_id else ''

                    qtd = it.quantidade or Decimal('0')
                    preco = it.preco or Decimal('0')
                    desc = it.desconto or Decimal('0')
                    val = it.total_item or Decimal('0')

                    fnode['linhas'].append(
                        {
                            'pedido': nf.pedido.pedido if nf.pedido_id else '',
                            'nota': nf.nota_fiscal,
                            'data': nf.data,
                            'status': nf.get_status_display(),
                            'produto': produto_nome,
                            'un': un_lbl,
                            'quantidade': qtd,
                            'preco': preco,
                            'desconto': desc,
                            'valor_total': val,
                        }
                    )

                    try:
                        fnode['total'] += val
                        pnode['total'] += val
                    except Exception:
                        pass

            # finalize structures as lists for template
            nf_prod_groups = []
            for pnode in prod_map.values():
                fornecedores_list = list(pnode['fornecedores'].values())
                nf_prod_groups.append(
                    {
                        'produtor_label': pnode['produtor_label'],
                        'total': pnode['total'],
                        'fornecedores': fornecedores_list,
                    }
                )
            g['nf_prod_groups'] = nf_prod_groups

        # Human-friendly structures for template
        ctx['report_groups'] = [
            {
                'cliente_label': self._cliente_label(g['cliente']),
                'safra_label': self._safra_label(g['safra']),
                'total': g['total'],
                'produtores': [{'label': k, 'total': v} for k, v in sorted(g['prod_totals'].items(), key=lambda x: (-x[1], x[0]))],
                'empresas': [{'label': k, 'total': v} for k, v in sorted(g['emp_totals'].items(), key=lambda x: (-x[1], x[0]))],
                'nf_prod_groups': g['nf_prod_groups'],
            }
            for g in groups.values()
        ]

        return ctx


class PedidoCompraReportResumidoView(GestorRequiredMixin, ListView):
    model = PedidoCompra
    template_name = 'core/relatorios/pedido_resumido.html'
    context_object_name = 'rows'

    def get_queryset(self):
        qs = super().get_queryset().select_related('safra', 'cliente', 'produtor', 'fornecedor')
        q = (self.request.GET.get('q') or '').strip()
        pedido_num = (self.request.GET.get('pedido') or '').strip()
        safra_id = (self.request.GET.get('safra') or '').strip()
        cliente_id = (self.request.GET.get('cliente') or '').strip()
        produtor_id = (self.request.GET.get('produtor') or '').strip()
        fornecedor_id = (self.request.GET.get('fornecedor') or '').strip()
        status = (self.request.GET.get('status') or '').strip()
        venc_ini = (self.request.GET.get('venc_ini') or '').strip()
        venc_fim = (self.request.GET.get('venc_fim') or '').strip()
        if q:
            qs = qs.filter(
                Q(pedido__icontains=q)
                | Q(cliente__cliente__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
            )
        if pedido_num:
            qs = qs.filter(pedido__icontains=pedido_num)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_id:
            qs = qs.filter(produtor_id=produtor_id)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if status:
            qs = qs.filter(status=status)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)

        # Aggregate: Safra, Cliente, Produtor, Fornecedor
        return (
            qs.values(
                'safra__safra',
                'cliente__cliente',
                'produtor__produtor',
                'produtor__fazenda',
                'fornecedor__fornecedor',
            )
            .annotate(
                pedidos=Count('id'),
                vencimento_min=Min('vencimento'),
                total=Sum('valor_total'),
                saldo=Sum('saldo_faturar'),
            )
            .order_by('safra__safra', 'cliente__cliente', 'produtor__produtor', 'fornecedor__fornecedor')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/pedidos/'

        # Licenca (cabecalho)
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        total_geral = Decimal('0')
        saldo_geral = Decimal('0')
        for r in ctx.get('rows') or []:
            try:
                total_geral += (r.get('total') or Decimal('0'))
            except Exception:
                pass
            try:
                saldo_geral += (r.get('saldo') or Decimal('0'))
            except Exception:
                pass
        ctx['total_geral'] = total_geral
        ctx['saldo_geral'] = saldo_geral
        return ctx


class FaturamentoReportResumoView(GestorRequiredMixin, DetailView):
    model = Faturamento
    template_name = 'core/relatorios/faturamento_resumo.html'

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('pedido', 'safra', 'produtor', 'fornecedor')
            .prefetch_related('itens', 'conta_pagar__pagamentos')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/pedidos/'
        nf = self.object
        ctx['nf'] = nf
        ctx['itens'] = nf.itens.select_related('produto_cadastro', 'unidade').all()
        ctx['conta'] = getattr(nf, 'conta_pagar', None)
        ctx['pagamentos'] = []
        if ctx['conta']:
            ctx['pagamentos'] = ctx['conta'].pagamentos.all()
        return ctx


class FaturamentoReportAnaliticoView(GestorRequiredMixin, ListView):
    model = Faturamento
    template_name = 'core/relatorios/faturamento_analitico.html'
    context_object_name = 'notas'

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related('pedido', 'safra', 'produtor', 'fornecedor')
            .prefetch_related('itens')
            .order_by('-data', '-id')
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(nota_fiscal__icontains=q) | Q(pedido__pedido__icontains=q) | Q(produtor__produtor__icontains=q))
        safra_id = (self.request.GET.get('safra') or '').strip()
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        return qs


class ContaPagarReportResumoView(GestorRequiredMixin, DetailView):
    model = ContaPagar
    template_name = 'core/relatorios/conta_resumo.html'

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('pedido', 'faturamento', 'cliente', 'produtor', 'safra', 'fornecedor')
            .prefetch_related('pagamentos')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/faturamentos/'
        conta = self.object
        ctx['conta'] = conta
        ctx['pagamentos'] = conta.pagamentos.all()
        return ctx


class ContaPagarReportAnaliticoView(GestorRequiredMixin, ListView):
    model = ContaPagar
    template_name = 'core/relatorios/conta_analitico.html'
    context_object_name = 'contas'

    def get_queryset(self):
        # Remove faturas de origem PEDIDO quando o saldo esta zerado.
        ContaPagar.objects.filter(origem=ContaPagar.Origem.PEDIDO, saldo_aberto__lte=0).delete()

        qs = (
            super()
            .get_queryset()
            .select_related('pedido', 'faturamento', 'cliente', 'produtor', 'safra', 'fornecedor')
            .prefetch_related('pagamentos', 'faturamento__itens__produto_cadastro', 'faturamento__itens__unidade', 'pedido__itens__produto_cadastro', 'pedido__itens__unidade')
        )

        status = (self.request.GET.get('status') or '').strip()
        safra_id = (self.request.GET.get('safra') or '').strip()
        cliente_id = (self.request.GET.get('cliente') or '').strip()
        produtor_id = (self.request.GET.get('produtor') or '').strip()
        fornecedor_id = (self.request.GET.get('fornecedor') or '').strip()
        pedido_txt = (self.request.GET.get('pedido') or '').strip()
        nota_fiscal = (self.request.GET.get('nota_fiscal') or '').strip()
        venc_ini = (self.request.GET.get('venc_ini') or '').strip()
        venc_fim = (self.request.GET.get('venc_fim') or '').strip()
        q = (self.request.GET.get('q') or '').strip()

        today = date.today()

        if status:
            if status == 'VENCIDO':
                qs = qs.filter(pago=False, saldo_aberto__gt=0, vencimento__lt=today)
            elif status == 'PAGO':
                qs = qs.filter(status=ContaPagar.Status.PAGO)
            elif status in {ContaPagar.Status.A_PAGAR, ContaPagar.Status.PARCIAL}:
                qs = qs.filter(status=status)

        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_id:
            qs = qs.filter(produtor_id=produtor_id)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if pedido_txt:
            qs = qs.filter(pedido__pedido__icontains=pedido_txt)
        if nota_fiscal:
            qs = qs.filter(nota_fiscal__icontains=nota_fiscal)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        if q:
            qs = qs.filter(Q(nota_fiscal__icontains=q) | Q(cliente__cliente__icontains=q) | Q(pedido__pedido__icontains=q) | Q(fornecedor__fornecedor__icontains=q))

        # Agrupar por Cliente, Produtor, Safra, Fornecedor, Vencimento
        # e classificar por Status e Data de emissao.
        qs = qs.order_by(
            'cliente__cliente',
            'produtor__produtor',
            'produtor__fazenda',
            'safra__safra',
            'fornecedor__fornecedor',
            'vencimento',
            'status',
            'data',
            'id',
        )
        return qs
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/contas/'

        qs = self.get_queryset()
        ctx['total_registros'] = qs.count()
        ctx['total_valor'] = qs.aggregate(total=Sum('valor_total'))['total'] or 0
        ctx['total_saldo'] = qs.aggregate(total=Sum('saldo_aberto'))['total'] or 0

        # Licenca (para cabecalho)
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        clientes_map = {}

        for conta in qs:
            ck = conta.cliente_id or 0
            cdata = clientes_map.get(ck)
            if not cdata:
                cdata = {
                    'cliente': str(conta.cliente) if conta.cliente_id else '-',
                    'culturas': set(),
                    'safras': set(),
                    'total_valor': Decimal('0'),
                    'produtores': {},
                }
                clientes_map[ck] = cdata

            cdata['total_valor'] += (conta.valor_total or Decimal('0'))

            if conta.safra_id:
                cdata['safras'].add(conta.safra.safra)
                try:
                    if getattr(conta.safra, 'cultura_id', None) and getattr(conta.safra, 'cultura', None):
                        cdata['culturas'].add(conta.safra.cultura.nome)
                except Exception:
                    pass

            pk = conta.produtor_id or 0
            pdata = cdata['produtores'].get(pk)
            if not pdata:
                pdata = {
                    'produtor': str(conta.produtor) if conta.produtor_id else '-',
                    'total_valor': Decimal('0'),
                    'resumo': {},
                    'fornecedores': {},
                }
                cdata['produtores'][pk] = pdata

            pdata['total_valor'] += (conta.valor_total or Decimal('0'))

            fk = conta.fornecedor_id or 0
            fdata = pdata['fornecedores'].get(fk)
            if not fdata:
                fdata = {
                    'fornecedor': conta.fornecedor.fornecedor if conta.fornecedor_id else '-',
                    'total_valor': Decimal('0'),
                    'itens': [],
                }
                pdata['fornecedores'][fk] = fdata

            fdata['total_valor'] += (conta.valor_total or Decimal('0'))

            status_lbl = getattr(conta, 'status_label', None) or str(conta.status)
            venc = conta.vencimento
            resumo_key = (fdata['fornecedor'], status_lbl, venc)
            pdata['resumo'][resumo_key] = pdata['resumo'].get(resumo_key, Decimal('0')) + (conta.valor_total or Decimal('0'))

            itens_src = []
            if conta.faturamento_id and getattr(conta, 'faturamento', None):
                try:
                    itens_src = list(conta.faturamento.itens.all())
                except Exception:
                    itens_src = []
            elif conta.pedido_id and getattr(conta, 'pedido', None):
                try:
                    itens_src = list(conta.pedido.itens.all())
                except Exception:
                    itens_src = []

            if not itens_src:
                fdata['itens'].append({
                    'status': status_lbl,
                    'nota_fiscal': conta.nota_fiscal,
                    'vencimento': conta.vencimento,
                    'produto': '-',
                    'quantidade': Decimal('0'),
                    'preco': Decimal('0'),
                    'total_item': Decimal('0'),
                })
            else:
                for it in itens_src:
                    try:
                        prod_nome = it.produto_cadastro.nome if getattr(it, 'produto_cadastro_id', None) else (getattr(it, 'produto', '') or '')
                    except Exception:
                        prod_nome = getattr(it, 'produto', '') or ''
                    qtd = getattr(it, 'quantidade', None) or Decimal('0')
                    preco = getattr(it, 'preco', None) or Decimal('0')
                    desc = getattr(it, 'desconto', None) or Decimal('0')
                    total_item = getattr(it, 'total_item', None)
                    if total_item is None:
                        try:
                            total_item = (qtd * preco) - desc
                        except Exception:
                            total_item = Decimal('0')
                    fdata['itens'].append({
                        'status': status_lbl,
                        'nota_fiscal': conta.nota_fiscal,
                        'vencimento': conta.vencimento,
                        'produto': prod_nome,
                        'quantidade': qtd,
                        'preco': preco,
                        'total_item': total_item,
                    })

        clientes = []
        for _ck, cdata in sorted(clientes_map.items(), key=lambda x: x[1]['cliente']):
            culturas = sorted(cdata['culturas'])
            safras = sorted(cdata['safras'])
            cdata['cultura_label'] = culturas[0] if len(culturas) == 1 else ('Varias' if culturas else '-')
            cdata['safra_label'] = safras[0] if len(safras) == 1 else ('Varias' if safras else '-')

            produtores_out = []
            for _pk, pdata in sorted(cdata['produtores'].items(), key=lambda x: x[1]['produtor']):
                resumo_rows = [
                    {'fornecedor': k[0], 'status': k[1], 'vencimento': k[2], 'valor_total': v}
                    for k, v in pdata['resumo'].items()
                ]
                resumo_rows.sort(key=lambda r: (r['fornecedor'], r['status'], r['vencimento'] or date.min))

                fornecedores_out = []
                for _fk, fdata in sorted(pdata['fornecedores'].items(), key=lambda x: x[1]['fornecedor']):
                    fdata['itens'].sort(key=lambda it: (it['nota_fiscal'], it['vencimento'] or date.min, it['produto']))
                    fornecedores_out.append(fdata)

                pdata_out = dict(pdata)
                pdata_out['resumo_rows'] = resumo_rows
                pdata_out['fornecedores_list'] = fornecedores_out
                produtores_out.append(pdata_out)

            cdata_out = dict(cdata)
            cdata_out['produtores_list'] = produtores_out
            clientes.append(cdata_out)

        ctx['report_clientes'] = clientes

        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['report_query'] = params.urlencode()
        ctx['pagination_query'] = params.urlencode()
        return ctx


class ContaPagarReportResumidoView(ContaPagarReportAnaliticoView):
    template_name = 'core/relatorios/conta_resumido.html'
    context_object_name = 'contas'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Ajusta titulos do relatorio resumido
        ctx['report_kind'] = 'RESUMIDO'
        return ctx

class PedidoCompraFormMixin(GestorRequiredMixin, View):
    template_name = 'core/pedidos/form.html'
    success_url = reverse_lazy('core:pedido_list')

    def get_item_formset(self, instance, data=None):
        FormSet = inlineformset_factory(
            PedidoCompra,
            PedidoCompraItem,
            form=PedidoCompraItemForm,
            extra=1,
            can_delete=True,
        )
        return FormSet(data=data, instance=instance)

    def _calc_totais(self, pedido, formset):
        total = Decimal('0')
        for f in formset.forms:
            if not hasattr(f, 'cleaned_data'):
                continue
            if f.cleaned_data.get('DELETE'):
                continue
            qtd = f.cleaned_data.get('quantidade') or Decimal('0')
            preco = f.cleaned_data.get('preco') or Decimal('0')
            desc = f.cleaned_data.get('desconto') or Decimal('0')
            item_total = (qtd * preco) - desc
            if item_total < 0:
                item_total = Decimal('0')
            f.instance.total_item = item_total
            total += item_total
        pedido.valor_total = total
        pedido.saldo_faturar = total

    def get(self, request, pk=None):
        instance = PedidoCompra.objects.filter(pk=pk).first() if pk else PedidoCompra()
        form = PedidoCompraForm(instance=instance)
        formset = self.get_item_formset(instance)
        return render(request, self.template_name, {'form': form, 'formset': formset, 'titulo': 'Pedido de Compra'})

    def post(self, request, pk=None):
        instance = PedidoCompra.objects.filter(pk=pk).first() if pk else PedidoCompra()
        form = PedidoCompraForm(request.POST, instance=instance)
        formset = self.get_item_formset(instance, data=request.POST)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                pedido = form.save(commit=False)
                # valor_total e calculado
                self._calc_totais(pedido, formset)
                pedido.save()
                formset.instance = pedido
                formset.save()
                messages.success(request, 'Registro salvo com sucesso.')
            return redirect(self.success_url)

        messages.error(request, 'Nao foi possivel salvar. Verifique os campos.')
        return render(request, self.template_name, {'form': form, 'formset': formset, 'titulo': 'Pedido de Compra'})


class PedidoCompraCreateView(PedidoCompraFormMixin):
    pass


class PedidoCompraUpdateView(PedidoCompraFormMixin):
    def get(self, request, pk):
        return super().get(request, pk=pk)

    def post(self, request, pk):
        return super().post(request, pk=pk)


class PedidoCompraDeleteView(GestorRequiredMixin, CrudDeleteView):
    model = PedidoCompra
    success_url = reverse_lazy('core:pedido_list')
    template_name = 'core/crud/confirm_delete.html'

    def post(self, request, *args, **kwargs):
        """
        Excluir Pedido deve remover dependencias (Faturamentos + Contas a pagar),
        senao o FK PROTECT do financeiro impede a exclusao.

        Regras:
        - Se existir qualquer pagamento associado a NF/conta, bloqueia (deve estornar).
        """
        from django.db import transaction
        from financeiro.models import ContaPagar, Faturamento, PagamentoContaPagar

        self.object = self.get_object()

        # Hard guard: qualquer pagamento existente bloqueia a exclusao.
        contas_relacionadas = ContaPagar.objects.filter(Q(pedido=self.object) | Q(faturamento__pedido=self.object))
        if PagamentoContaPagar.objects.filter(conta__in=contas_relacionadas).exists():
            messages.error(request, 'Nao e possivel excluir: existem pagamentos registrados. Estorne o pagamento antes de excluir.')
            return redirect(self.success_url)

        with transaction.atomic():
            # 1) Apaga contas a pagar originadas de faturamentos do pedido
            faturas_nf = ContaPagar.objects.filter(faturamento__pedido=self.object)
            faturas_nf.delete()

            # 2) Apaga faturamentos vinculados ao pedido (itens em cascade)
            Faturamento.objects.filter(pedido=self.object).delete()

            # 3) Apaga contas a pagar originadas do proprio pedido
            ContaPagar.objects.filter(pedido=self.object).delete()

            # 4) Agora sim apaga o pedido
            self.object.delete()

        messages.success(request, 'Registro excluido com sucesso.')
        return redirect(self.success_url)


# ------------------------------
# Faturamento (ListView restaurada)
# ------------------------------
# ------------------------------
# Faturamento (ListView)
# ------------------------------
class FaturamentoListView(GestorRequiredMixin, ListView):
    model = Faturamento
    template_name = 'core/faturamentos/list.html'
    paginate_by = 8

    def get_paginate_by(self, queryset):
        raw = (self.request.COOKIES.get('per_page') or '').strip()
        if raw.isdigit():
            val = int(raw)
            if val in {7, 8, 10}:
                return val
        return super().get_paginate_by(queryset)

    def get_queryset(self):
        qs = super().get_queryset().select_related('safra', 'produtor', 'fornecedor', 'pedido').order_by('-data', '-id')
        q = (self.request.GET.get('q') or '').strip()
        safra_id = (self.request.GET.get('safra') or '').strip()
        if q:
            qs = qs.filter(Q(nota_fiscal__icontains=q) | Q(pedido__pedido__icontains=q) | Q(produtor__produtor__icontains=q) | Q(fornecedor__fornecedor__icontains=q))
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        itens_qs = FaturamentoItem.objects.filter(faturamento__in=qs)
        qtd_total = itens_qs.aggregate(total=Sum('quantidade'))['total'] or 0
        total_fat = qs.aggregate(total=Sum('valor_total'))['total'] or 0
        preco_medio = 0
        if qtd_total:
            try:
                preco_medio = Decimal(total_fat or 0) / Decimal(qtd_total or 1)
            except Exception:
                preco_medio = 0

        context['create_url_name'] = 'core:faturamento_create'
        context['edit_url_name'] = 'core:faturamento_update'
        context['delete_url_name'] = 'core:faturamento_delete'

        context['current_q'] = self.request.GET.get('q', '')
        context['filtro_safra'] = self.request.GET.get('safra', '')
        context['safras'] = Safra.objects.all().order_by('-ano', 'safra')

        params = self.request.GET.copy()
        params.pop('page', None)
        context['pagination_query'] = params.urlencode()
        context['report_query'] = params.urlencode()

        context['card_notas'] = qs.count()
        context['card_quantidade'] = qtd_total
        context['card_preco_medio'] = preco_medio
        context['card_total_faturado'] = total_fat
        context['total_registros'] = qs.count()
        return context

class FaturamentoFormMixin(GestorRequiredMixin):
    template_name = 'core/faturamentos/form.html'
    success_url = reverse_lazy('core:faturamento_list')

    def get_item_formset_class(self):
        class ItensFaturamentoFormSet(BaseInlineFormSet):
            def clean(self):
                super().clean()
                fat = self.instance
                if not fat or not fat.pedido_id:
                    return

                pedido = fat.pedido

                fat_itens = FaturamentoItem.objects.filter(
                    faturamento__pedido_id=pedido.pk,
                    produto_cadastro_id__isnull=False,
                )
                if fat.pk:
                    fat_itens = fat_itens.exclude(faturamento_id=fat.pk)

                faturado_map = {
                    row['produto_cadastro_id']: (row['qtd'] or Decimal('0'))
                    for row in fat_itens.values('produto_cadastro_id').annotate(qtd=Sum('quantidade'))
                }

                pedido_map = {}
                for it in pedido.itens.select_related('produto_cadastro').all():
                    if it.produto_cadastro_id:
                        pedido_map[it.produto_cadastro_id] = pedido_map.get(it.produto_cadastro_id, Decimal('0')) + (it.quantidade or 0)

                usados = {}
                for form in self.forms:
                    if not getattr(form, 'cleaned_data', None):
                        continue
                    if form.cleaned_data.get('DELETE'):
                        continue

                    prod = form.cleaned_data.get('produto_cadastro')
                    qtd = form.cleaned_data.get('quantidade') or Decimal('0')
                    if not prod:
                        continue

                    if prod.pk not in pedido_map:
                        raise ValidationError(f'Produto "{prod}" nao pertence ao pedido selecionado.')

                    usados[prod.pk] = usados.get(prod.pk, Decimal('0')) + qtd

                for prod_id, qtd_usada in usados.items():
                    pedido_qtd = pedido_map.get(prod_id) or Decimal('0')
                    ja = faturado_map.get(prod_id) or Decimal('0')
                    saldo = pedido_qtd - ja
                    if saldo < 0:
                        saldo = Decimal('0')

                    if qtd_usada > saldo:
                        raise ValidationError('Quantidade maior que o saldo a faturar do pedido para um ou mais produtos.')

            def _calc_total_item(self, form):
                qtd = form.cleaned_data.get('quantidade') or Decimal('0')
                preco = form.cleaned_data.get('preco') or Decimal('0')
                desconto = form.cleaned_data.get('desconto') or Decimal('0')
                return (qtd * preco) - desconto

            def save_new(self, form, commit=True):
                obj = super().save_new(form, commit=False)
                if getattr(form, 'cleaned_data', None) and not form.cleaned_data.get('DELETE'):
                    obj.total_item = self._calc_total_item(form)
                else:
                    obj.total_item = obj.total_item or Decimal('0')
                if commit:
                    obj.save()
                return obj

            def save_existing(self, form, instance, commit=True):
                obj = super().save_existing(form, instance, commit=False)
                if getattr(form, 'cleaned_data', None) and not form.cleaned_data.get('DELETE'):
                    obj.total_item = self._calc_total_item(form)
                else:
                    obj.total_item = obj.total_item or Decimal('0')
                if commit:
                    obj.save()
                return obj

        return inlineformset_factory(
            Faturamento,
            FaturamentoItem,
            form=FaturamentoItemForm,
            formset=ItensFaturamentoFormSet,
            extra=1,
            can_delete=True,
        )

    def _normalize_itens_post(self, post):
        try:
            total = int(post.get('itens-TOTAL_FORMS') or '0')
        except Exception:
            return post

        for i in range(total):
            prefix = f'itens-{i}-'
            if post.get(prefix + 'DELETE'):
                continue
            fields = [
                prefix + 'produto_cadastro',
                prefix + 'unidade',
                prefix + 'quantidade',
                prefix + 'preco',
                prefix + 'desconto',
            ]
            values = [(post.get(f) or '').strip() for f in fields]
            if all(v in {'', '0', '0,00', '0,00000'} for v in values):
                post[prefix + 'DELETE'] = 'on'
        return post

    def _recalcular_totais(self, faturamento):
        total = Decimal('0')
        for item in faturamento.itens.all():
            subtotal = (item.quantidade or 0) * (item.preco or 0)
            desconto = item.desconto or 0
            item.total_item = subtotal - desconto
            item.produto = item.produto_cadastro.nome if item.produto_cadastro else item.produto
            item.save(update_fields=['total_item', 'produto'])
            total += item.total_item

        faturamento.valor_total = total
        faturamento.save(update_fields=['valor_total'])

    def get_context_data(self, form, formset):
        return {
            'form': form,
            'formset': formset,
            'titulo': 'Faturamento',
            'modo_edicao': bool(getattr(self, 'object', None)),
        }


class FaturamentoCreateView(FaturamentoFormMixin, View):
    def get(self, request, *args, **kwargs):
        form = FaturamentoForm()
        formset = self.get_item_formset_class()(instance=Faturamento())
        return render(request, self.template_name, self.get_context_data(form, formset))

    def post(self, request, *args, **kwargs):
        post = request.POST.copy()
        post = self._normalize_itens_post(post)
        form = FaturamentoForm(post)
        formset = self.get_item_formset_class()(post, instance=Faturamento())

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                faturamento = form.save(commit=False)
                if faturamento.valor_total is None:
                    faturamento.valor_total = 0
                faturamento.save()
                formset.instance = faturamento
                formset.save()
                self._recalcular_totais(faturamento)
            messages.success(request, 'Faturamento salvo com sucesso.')
            return redirect(self.success_url)

        messages.error(request, 'Nao foi possivel salvar o faturamento. Verifique os campos e itens.')
        return render(request, self.template_name, self.get_context_data(form, formset))


class FaturamentoUpdateView(FaturamentoFormMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(Faturamento, pk=kwargs['pk'])
        conta = getattr(self.object, 'conta_pagar', None)
        tem_pag = False
        if conta:
            tem_pag = PagamentoContaPagar.objects.filter(conta_id=conta.pk).exists()
        # Bloqueia edicao quando houver pagamento (parcial/total) ou quando a fatura estiver marcada como paga.
        # Usamos o status efetivo da Conta a Pagar (quando existir) para evitar inconsistencias.
        bloqueado = (
            tem_pag
            or (conta and (conta.status_efetivo == 'PAGO' or conta.status == ContaPagar.Status.PARCIAL or conta.pago))
            or (not conta and self.object.status == Faturamento.Status.PAGO)
        )
        if bloqueado:
            messages.error(request, 'Nota fiscal com pagamento. Para editar, estorne o pagamento primeiro.')
            return redirect('core:faturamento_list')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        form = FaturamentoForm(instance=self.object)
        formset = self.get_item_formset_class()(instance=self.object)
        return render(request, self.template_name, self.get_context_data(form, formset))

    def post(self, request, *args, **kwargs):
        post = request.POST.copy()
        post = self._normalize_itens_post(post)
        form = FaturamentoForm(post, instance=self.object)
        formset = self.get_item_formset_class()(post, instance=self.object)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                faturamento = form.save(commit=False)
                if faturamento.valor_total is None:
                    faturamento.valor_total = 0
                faturamento.save()
                formset.save()
                self._recalcular_totais(faturamento)
            messages.success(request, 'Faturamento atualizado com sucesso.')
            return redirect(self.success_url)

        messages.error(request, 'Nao foi possivel atualizar o faturamento. Verifique os campos e itens.')
        return render(request, self.template_name, self.get_context_data(form, formset))


class FaturamentoDeleteView(GestorRequiredMixin, CrudDeleteView):
    model = Faturamento
    success_url = reverse_lazy('core:faturamento_list')

    def dispatch(self, request, *args, **kwargs):
        fat = get_object_or_404(Faturamento, pk=kwargs.get('pk'))
        conta = getattr(fat, 'conta_pagar', None)
        tem_pag = False
        if conta:
            tem_pag = PagamentoContaPagar.objects.filter(conta_id=conta.pk).exists()
        bloqueado = (
            tem_pag
            or (conta and (conta.status_efetivo == 'PAGO' or conta.status == ContaPagar.Status.PARCIAL or conta.pago))
            or (not conta and fat.status == Faturamento.Status.PAGO)
        )
        if bloqueado:
            messages.error(request, 'Nota fiscal com pagamento. Para excluir, estorne o pagamento primeiro.')
            return redirect('core:faturamento_list')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """
        Exclui a NF e remove a Conta a Pagar vinculada (quando existir).

        Importante: ContaPagar.faturamento usa on_delete=PROTECT, entao precisamos remover a fatura antes
        para permitir excluir a NF.
        """
        from django.db.models.deletion import ProtectedError

        fat = get_object_or_404(Faturamento, pk=kwargs.get('pk'))
        pedido_id = fat.pedido_id
        conta = getattr(fat, 'conta_pagar', None)
        if conta:
            tem_pag = PagamentoContaPagar.objects.filter(conta_id=conta.pk).exists()
            if tem_pag:
                messages.error(request, 'Nota fiscal com pagamento. Para excluir, estorne o pagamento primeiro.')
                return redirect('core:faturamento_list')

        try:
            with transaction.atomic():
                if conta:
                    conta.delete()
                fat.delete()
                # Recalcula saldo/status do pedido quando a NF estava vinculada a ele.
                # Isso evita o pedido ficar "ENTREGUE" depois que uma nota foi removida.
                if pedido_id:
                    from financeiro.services import processar_pedido_compra

                    pedido = PedidoCompra.objects.filter(pk=pedido_id).first()
                    if pedido:
                        # Recalcula saldo/status com base no que restou de faturamentos.
                        processar_pedido_compra(pedido)
        except ProtectedError:
            messages.error(request, 'Nao foi possivel excluir porque existe um vinculo protegido. Verifique Contas a Pagar.')
            return redirect('core:faturamento_list')

        messages.success(request, 'Nota fiscal excluida com sucesso.')
        return redirect(self.success_url)


@login_required
def pedidos_por_safra_produtor(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    safra_id = (request.GET.get('safra_id') or '').strip()
    produtor_id = (request.GET.get('produtor_id') or '').strip()
    if not (safra_id.isdigit() and produtor_id.isdigit()):
        return JsonResponse({'items': []})

    qs = (
        PedidoCompra.objects.filter(safra_id=int(safra_id), produtor_id=int(produtor_id))
        .filter(saldo_faturar__gt=0)
        .order_by('-data', '-id')
    )

    if getattr(request.user, 'effective_role', '') != 'ADMIN':
        cliente = _get_cliente_do_usuario(request.user)
        if cliente:
            qs = qs.filter(cliente_id=cliente.pk)

    return JsonResponse({'items': [{'id': p.id, 'label': str(p.pedido)} for p in qs]})


@login_required
def pedido_detalhes_para_faturamento(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'pedido': None, 'itens': []})

    pedido_id = (request.GET.get('pedido_id') or '').strip()
    if not pedido_id.isdigit():
        return JsonResponse({'pedido': None, 'itens': []})

    exclude_fat = (request.GET.get('exclude_faturamento_id') or '').strip()

    pedido = get_object_or_404(
        PedidoCompra.objects.select_related('fornecedor', 'safra', 'produtor', 'cliente'),
        pk=int(pedido_id),
    )

    if getattr(request.user, 'effective_role', '') != 'ADMIN':
        cliente = _get_cliente_do_usuario(request.user)
        if cliente and pedido.cliente_id != cliente.pk:
            return JsonResponse({'pedido': None, 'itens': []})

    fat_itens = FaturamentoItem.objects.filter(faturamento__pedido_id=pedido.pk, produto_cadastro_id__isnull=False)
    if exclude_fat.isdigit():
        fat_itens = fat_itens.exclude(faturamento_id=int(exclude_fat))

    faturado_map = {
        row['produto_cadastro_id']: (row['qtd'] or Decimal('0'))
        for row in fat_itens.values('produto_cadastro_id').annotate(qtd=Sum('quantidade'))
    }

    itens_payload = []
    for it in pedido.itens.select_related('produto_cadastro', 'unidade').all():
        if not it.produto_cadastro_id:
            continue
        ja = faturado_map.get(it.produto_cadastro_id) or Decimal('0')
        saldo = (it.quantidade or Decimal('0')) - ja
        if saldo <= 0:
            continue
        itens_payload.append(
            {
                'produto_id': it.produto_cadastro_id,
                'produto_label': it.produto_cadastro.nome,
                'saldo_qtd': str(saldo),
                'unidade_id': it.unidade_id,
                'unidade_label': str(it.unidade),
                'preco': str(it.preco or 0),
                'desconto': str(it.desconto or 0),
            }
        )

    pedido_payload = {
        'id': pedido.pk,
        'pedido': str(pedido.pedido),
        'fornecedor_id': pedido.fornecedor_id,
        'fornecedor_label': str(pedido.fornecedor) if pedido.fornecedor_id else '',
        'vencimento': pedido.vencimento.isoformat() if pedido.vencimento else '',
        'safra_id': pedido.safra_id,
        'produtor_id': pedido.produtor_id,
    }

    return JsonResponse({'pedido': pedido_payload, 'itens': itens_payload})

# Contas a Pagar
class ContaPagarListView(GestorRequiredMixin, CrudListView):
    model = ContaPagar
    template_name = 'core/contas/list.html'
    context_title = 'Contas a Pagar'
    paginate_by = 15
    columns = [
        ('nota_fiscal', 'Nota Fiscal'),
        ('origem', 'Origem'),
        ('cliente', 'Cliente'),
        ('produtor', 'Produtor'),
        ('fornecedor', 'Fornecedor'),
        ('pedido', 'Pedido'),
        ('vencimento', 'Vencimento'),
        ('valor_total', 'Valor Total'),
        ('saldo_aberto', 'Saldo'),
        ('status', 'Status'),
    ]
    create_url_name = 'core:conta_create'
    edit_url_name = 'core:conta_update'
    delete_url_name = 'core:conta_delete'
    search_fields = ['nota_fiscal', 'cliente__cliente', 'pedido__pedido', 'status', 'origem']
    default_ordering = 'vencimento'

    def get_queryset(self):
        # Remove faturas de origem PEDIDO quando o saldo esta zerado.
        # Isso evita manter o documento do pedido quando ele ja foi totalmente substituido por notas fiscais.
        ContaPagar.objects.filter(origem=ContaPagar.Origem.PEDIDO, saldo_aberto__lte=0).delete()

        qs = super().get_queryset().select_related('cliente', 'produtor', 'pedido', 'safra', 'fornecedor')
        status = (self.request.GET.get('status') or '').strip()
        safra_id = (self.request.GET.get('safra') or '').strip()
        cliente_id = (self.request.GET.get('cliente') or '').strip()
        produtor_id = (self.request.GET.get('produtor') or '').strip()
        fornecedor_id = (self.request.GET.get('fornecedor') or '').strip()
        pedido_txt = (self.request.GET.get('pedido') or '').strip()
        nota_fiscal = (self.request.GET.get('nota_fiscal') or '').strip()
        venc_ini = (self.request.GET.get('venc_ini') or '').strip()
        venc_fim = (self.request.GET.get('venc_fim') or '').strip()

        today = date.today()

        if status:
            if status == 'VENCIDO':
                qs = qs.filter(pago=False, saldo_aberto__gt=0, vencimento__lt=today)
            elif status == 'PAGO':
                qs = qs.filter(status=ContaPagar.Status.PAGO)
            elif status in {ContaPagar.Status.A_PAGAR, ContaPagar.Status.PARCIAL}:
                qs = qs.filter(status=status)

        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_id:
            qs = qs.filter(produtor_id=produtor_id)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if pedido_txt:
            qs = qs.filter(pedido__pedido__icontains=pedido_txt)
        if nota_fiscal:
            qs = qs.filter(nota_fiscal__icontains=nota_fiscal)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        today = date.today()

        def _sum(q):
            return q.aggregate(total=Sum('saldo_aberto'))['total'] or 0

        total_faturado = qs.filter(origem=ContaPagar.Origem.FATURAMENTO).aggregate(total=Sum('valor_total'))['total'] or 0
        total_pago = qs.filter(status=ContaPagar.Status.PAGO).aggregate(total=Sum('valor_total'))['total'] or 0
        total_vencido = _sum(qs.filter(pago=False, saldo_aberto__gt=0, vencimento__lt=today))
        total_a_pagar = _sum(qs.filter(pago=False, saldo_aberto__gt=0, vencimento__gte=today))

        context['filtro_status'] = self.request.GET.get('status', '')
        context['filtro_safra'] = self.request.GET.get('safra', '')
        context['filtro_cliente'] = self.request.GET.get('cliente', '')
        context['filtro_produtor'] = self.request.GET.get('produtor', '')
        context['filtro_fornecedor'] = self.request.GET.get('fornecedor', '')
        context['filtro_pedido'] = self.request.GET.get('pedido', '')
        context['filtro_nota_fiscal'] = self.request.GET.get('nota_fiscal', '')
        context['filtro_venc_ini'] = self.request.GET.get('venc_ini', '')
        context['filtro_venc_fim'] = self.request.GET.get('venc_fim', '')

        context['current_q'] = self.request.GET.get('q', '')

        params = self.request.GET.copy()
        params.pop('page', None)
        context['pagination_query'] = params.urlencode()
        context['report_query'] = params.urlencode()

        context['op_status'] = [
            ('', 'Todos'),
            (ContaPagar.Status.A_PAGAR, 'A Pagar'),
            (ContaPagar.Status.PARCIAL, 'Parcial'),
            ('VENCIDO', 'Vencido'),
            (ContaPagar.Status.PAGO, 'Pago'),
        ]
        context['safras'] = Safra.objects.all().order_by('-ano', 'safra')
        context['clientes'] = Cliente.objects.all().order_by('cliente')
        context['produtores'] = Produtor.objects.all().order_by('produtor', 'fazenda')
        context['fornecedores'] = Fornecedor.objects.all().order_by('fornecedor')

        context['card_total_faturado'] = total_faturado
        context['card_total_a_pagar'] = total_a_pagar
        context['card_total_vencido'] = total_vencido
        context['card_total_pago'] = total_pago
        context['total_registros'] = qs.count()
        return context


class ContaPagarDetailView(GestorRequiredMixin, DetailView):
    model = ContaPagar
    template_name = 'core/contas/detail.html'


class ContaPagarCreateView(GestorRequiredMixin, CrudCreateView):
    model = ContaPagar
    form_class = ContaPagarForm
    success_url = reverse_lazy('core:conta_list')


class ContaPagarUpdateView(GestorRequiredMixin, CrudUpdateView):
    model = ContaPagar
    form_class = ContaPagarForm
    success_url = reverse_lazy('core:conta_list')


class ContaPagarDeleteView(GestorRequiredMixin, CrudDeleteView):
    model = ContaPagar
    success_url = reverse_lazy('core:conta_list')


@login_required
def contas_selecionadas(request):
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return JsonResponse({'items': []})

    ids_raw = (request.GET.get('ids') or '').strip()
    ids = [int(i) for i in ids_raw.split(',') if i.isdigit()]
    if not ids:
        return JsonResponse({'items': []})

    contas = ContaPagar.objects.filter(pk__in=ids).select_related('cliente', 'pedido', 'produtor').order_by('vencimento')

    items = []
    for c in contas:
        items.append(
            {
                'id': c.pk,
                'nota_fiscal': c.nota_fiscal,
                'pedido': c.pedido.pedido if c.pedido_id else '',
                'cliente': str(c.cliente),
                'produtor': str(c.produtor) if c.produtor_id else '',
                'vencimento': c.vencimento.strftime('%d/%m/%Y') if c.vencimento else '',
                'saldo': str(c.saldo_aberto or 0),
                'status': c.status_efetivo,
                'status_label': c.status_label,
            }
        )

    return JsonResponse({'items': items})


@login_required
def conta_pagar_pagar_lote(request):
    if request.method != 'POST':
        return redirect('core:conta_list')

    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    conta_ids = request.POST.getlist('conta_ids')
    if not conta_ids:
        messages.warning(request, 'Selecione ao menos uma fatura para registrar pagamento.')
        return redirect('core:conta_list')

    data_pag = request.POST.get('pag_data') or ''
    forma = request.POST.get('pag_forma') or FormaPagamentoFinanceiro.PIX
    obs = (request.POST.get('pag_obs') or '').strip()

    if forma not in dict(FormaPagamentoFinanceiro.choices):
        forma = FormaPagamentoFinanceiro.PIX

    contas = ContaPagar.objects.filter(pk__in=conta_ids).select_related('pedido', 'faturamento')
    atualizadas = 0
    pagamentos = 0

    with transaction.atomic():
        for conta in contas:
            if (conta.saldo_aberto or 0) <= 0:
                continue

            v_bruto = _parse_decimal_br(request.POST.get(f'valor_bruto_{conta.pk}'))
            v_desc = _parse_decimal_br(request.POST.get(f'desconto_{conta.pk}'))
            v_acr = _parse_decimal_br(request.POST.get(f'acrescimo_{conta.pk}'))
            liquido = v_bruto - v_desc + v_acr
            if liquido <= 0:
                continue

            if liquido > (conta.saldo_aberto or 0):
                liquido = conta.saldo_aberto

            PagamentoContaPagar.objects.create(
                conta=conta,
                data=data_pag or date.today(),
                forma_pagamento=forma,
                valor_bruto=v_bruto,
                desconto=v_desc,
                acrescimo=v_acr,
                valor_liquido=liquido,
                observacao=obs,
            )
            pagamentos += 1

            conta.saldo_aberto = max(Decimal('0'), (conta.saldo_aberto or 0) - liquido)
            conta.pago = conta.saldo_aberto <= 0
            if conta.pago:
                conta.status = ContaPagar.Status.PAGO
            else:
                conta.status = ContaPagar.Status.PARCIAL
            conta.save(update_fields=['saldo_aberto', 'pago', 'status', 'updated_at'])
            atualizadas += 1

            if conta.faturamento_id and conta.pago:
                Faturamento.objects.filter(pk=conta.faturamento_id).update(status=Faturamento.Status.PAGO)

    messages.success(request, f'Pagamentos registrados: {pagamentos}. Contas atualizadas: {atualizadas}.')
    return redirect('core:conta_list')


@login_required
def conta_pagar_estornar_lote(request):
    if request.method != 'POST':
        return redirect('core:conta_list')

    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR'}:
        return redirect('core:dashboard')

    conta_ids = request.POST.getlist('conta_ids')
    if not conta_ids:
        messages.warning(request, 'Selecione ao menos uma fatura para estornar.')
        return redirect('core:conta_list')

    contas = ContaPagar.objects.filter(pk__in=conta_ids).select_related('faturamento')

    with transaction.atomic():
        for conta in contas:
            PagamentoContaPagar.objects.filter(conta=conta).delete()
            conta.pago = False
            conta.saldo_aberto = conta.valor_total or 0
            conta.status = ContaPagar.Status.A_PAGAR
            conta.save(update_fields=['pago', 'saldo_aberto', 'status', 'updated_at'])
            if conta.faturamento_id:
                Faturamento.objects.filter(pk=conta.faturamento_id).update(status=Faturamento.Status.A_RECEBER)

    messages.success(request, 'Estorno realizado com sucesso.')
    return redirect('core:conta_list')


# Licencas (CRUD)
class LicencaListView(GestorRequiredMixin, CrudListView):
    paginate_by = 15
    model = Licenca
    context_title = 'Licencas'
    columns = [('cliente', 'Cliente'), ('status', 'Status'), ('inicio_vigencia', 'Inicio'), ('fim_vigencia', 'Fim')]
    create_url_name = 'core:licenca_create'
    edit_url_name = 'core:licenca_update'
    delete_url_name = 'core:licenca_delete'
    search_fields = ['cliente', 'cpf_cnpj', 'email']
    default_ordering = '-created_at'

class LicencaCreateView(GestorRequiredMixin, CrudCreateView):
    model = Licenca
    form_class = LicencaForm
    success_url = reverse_lazy('core:licenca_list')


class LicencaUpdateView(GestorRequiredMixin, CrudUpdateView):
    model = Licenca
    form_class = LicencaForm
    success_url = reverse_lazy('core:licenca_list')


class LicencaDeleteView(GestorRequiredMixin, CrudDeleteView):
    model = Licenca
    success_url = reverse_lazy('core:licenca_list')

# Vinculos Usuario-Licenca (CRUD)
class PerfilUsuarioLicencaListView(GestorRequiredMixin, CrudListView):
    template_name = 'core/licencas/vinculos_list.html'
    model = PerfilUsuarioLicenca
    context_title = 'Vinculos Usuario-Licenca'
    columns = [('Usuario', 'usuario'), ('Licenca', 'licenca'), ('Criado', 'created_at')]
    create_url_name = 'core:licenca_vinculo_create'
    edit_url_name = 'core:licenca_vinculo_update'
    delete_url_name = 'core:licenca_vinculo_delete'
    search_fields = ['usuario__username', 'usuario__first_name', 'usuario__last_name', 'licenca__cliente', 'licenca__cpf_cnpj', 'licenca__email']
    default_ordering = '-created_at'
    paginate_by = 15

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)

        for col in (self.columns or []):
            label, field = col[0], col[1]
            col_params = base_params.copy()
            if active_field == field and not is_desc:
                col_params['o'] = f'-{field}'
            else:
                col_params['o'] = field

            cols.append(
                {
                    'label': label,
                    'field': field,
                    'is_active': active_field == field,
                    'is_desc': active_field == field and is_desc,
                    'sort_query': col_params.urlencode(),
                }
            )
        return cols

    def get_queryset(self):
        qs = super().get_queryset().select_related('usuario', 'licenca')

        # Supervisor so enxerga vinculos da propria licenca
        if getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            perfil = getattr(self.request.user, 'perfil_licenca', None)
            lic = perfil.licenca if perfil else None
            if lic:
                qs = qs.filter(licenca_id=lic.pk)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        params = self.request.GET.copy()
        params.pop('page', None)

        ctx['columns'] = self._build_columns()
        ctx['current_q'] = self.request.GET.get('q', '')
        ctx['current_sort'] = self.request.GET.get('o', '')
        ctx['pagination_query'] = params.urlencode()
        ctx['total_registros'] = qs.count()
        return ctx


class PerfilUsuarioLicencaCreateView(GestorRequiredMixin, CrudCreateView):
    model = PerfilUsuarioLicenca
    form_class = PerfilUsuarioLicencaForm
    success_url = reverse_lazy('core:licenca_vinculo_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def form_valid(self, form):
        # Supervisor so pode salvar na propria licenca
        if getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            perfil = getattr(self.request.user, 'perfil_licenca', None)
            lic = perfil.licenca if perfil else None
            if lic:
                form.instance.licenca = lic
        return super().form_valid(form)


class PerfilUsuarioLicencaUpdateView(GestorRequiredMixin, CrudUpdateView):
    model = PerfilUsuarioLicenca
    form_class = PerfilUsuarioLicencaForm
    success_url = reverse_lazy('core:licenca_vinculo_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs


class PerfilUsuarioLicencaDeleteView(GestorRequiredMixin, CrudDeleteView):
    model = PerfilUsuarioLicenca
    success_url = reverse_lazy('core:licenca_vinculo_list')
class InviteUsuarioLicencaView(GestorRequiredMixin, FormView):
    template_name = 'core/crud/form.html'
    form_class = InviteUsuarioLicencaForm

    def get_success_url(self):
        return reverse('core:licenca_vinculo_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = 'Convidar Usuario'
        return ctx

    def form_valid(self, form):
        User = get_user_model()

        lic = form.cleaned_data['licenca']
        role = form.cleaned_data['role']

        # Supervisor so pode convidar usuario comum
        if getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            role = User.Role.USUARIO

        user = User(
            username=form.cleaned_data['username'],
            first_name=form.cleaned_data.get('first_name') or '',
            last_name=form.cleaned_data.get('last_name') or '',
            email=form.cleaned_data.get('email') or '',
            role=role,
            is_active=True,
        )
        user.set_password(form.cleaned_data['password1'])
        user.save()

        PerfilUsuarioLicenca.objects.create(usuario=user, licenca=lic)

        canal = (form.cleaned_data.get('canal') or 'WHATSAPP').strip().upper()
        nome = (user.get_full_name() or user.username).strip()
        login_url = self.request.build_absolute_uri('/accounts/login/')

        mensagem = (
            f"Ola {nome}, seu acesso ao ERP Rogajo foi criado.\n\n"
            f"Link: {login_url}\n"
            f"Usuario: {user.username}\n"
            f"Senha: {form.cleaned_data['password1']}\n\n"
            f"Suporte incluso: correcao de bugs e atualizacoes.\n"
            f"Melhorias (fora da licenca): R$ 120,00/h (minimo 4h).\n"
        )

        whatsapp_url = None
        if canal == 'WHATSAPP':
            whatsapp_url = 'https://wa.me/?text=' + quote(mensagem)

        return render(
            self.request,
            'core/licencas/invite_success.html',
            {
                'username': user.username,
                'mensagem': mensagem,
                'whatsapp_url': whatsapp_url,
            },
        )
