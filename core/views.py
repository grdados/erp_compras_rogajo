
from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Q, Sum, Avg, Exists, OuterRef, Count, Min, Max, F, ExpressionWrapper, DecimalField, Case, When, Value, CharField
from django.db.models.functions import Coalesce
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import urlencode
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, FormView
from urllib.parse import quote, unquote
import unicodedata

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
from licencas.models import Licenca, LicencaFatura, PerfilUsuarioLicenca
from licencas.pricing import valor_anual, valor_mensal_plano, valor_semestral
from licencas.services import excluir_licenca_sincronizada

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


def _recalcular_licenca_por_faturas(licenca: Licenca):
    """Sincroniza valor_total e status da assinatura com base nas faturas."""
    hoje = timezone.localdate()
    faturas = list(LicencaFatura.objects.filter(licenca=licenca).order_by('vencimento', 'numero'))
    if not faturas:
        return

    mudou_fatura = False
    soma = Decimal('0')
    existe_atraso = False
    todas_pagas = True

    for f in faturas:
        soma += Decimal(f.valor or 0)

        # Converte automaticamente pendente vencida para vencida
        if f.status == LicencaFatura.Status.PENDENTE and f.vencimento and f.vencimento < hoje:
            f.status = LicencaFatura.Status.VENCIDA
            f.save(update_fields=['status', 'updated_at'])
            mudou_fatura = True

        if f.status in {LicencaFatura.Status.VENCIDA}:
            existe_atraso = True
            todas_pagas = False
        elif f.status == LicencaFatura.Status.PENDENTE:
            if f.vencimento and f.vencimento < hoje:
                existe_atraso = True
            todas_pagas = False
        elif f.status == LicencaFatura.Status.CANCELADA:
            todas_pagas = False
        elif f.status != LicencaFatura.Status.PAGA:
            todas_pagas = False

    licenca.valor_total = soma

    # Encerrado: todos pagos e vigencia final ja passou
    if todas_pagas and licenca.fim_vigencia and licenca.fim_vigencia < hoje:
        licenca.status = Licenca.Status.EXPIRADA  # exibido como "Encerrado" na tela
    elif existe_atraso:
        licenca.status = Licenca.Status.INADIMPLENTE  # exibido como "Em atraso"
    else:
        licenca.status = Licenca.Status.ATIVA  # exibido como "Vigente"

    licenca.save(update_fields=['valor_total', 'status', 'updated_at'])
    return mudou_fatura


def _normalize_filter_value(value) -> str:
    raw = (value or '').strip()
    if raw.lower() in {'todos', 'todas', 'all'}:
        return ''
    return raw


def _normalize_multi_values(values) -> list[str]:
    normalized = []
    for value in values or []:
        v = _normalize_filter_value(value)
        if v and v not in normalized:
            normalized.append(v)
    return normalized


def _filters_active(request, keys) -> bool:
    if _normalize_filter_value(request.GET.get('apply')):
        return True
    return any(_normalize_filter_value(request.GET.get(k)) for k in keys)


def _panel_filters_restore_or_save(request, scope: str):
    """
    Persiste/restaura querystring de filtros por painel.
    - Se a requisicao chega sem querystring, restaura ultimo estado salvo.
    - Se chega com querystring, salva (sem `page`) para reutilizacao futura.
    """
    session_key = f'{scope}_last_filters_qs'

    if request.GET:
        params = request.GET.copy()
        params.pop('page', None)
        qs = params.urlencode()
        request.session[session_key] = qs
        return None

    saved_qs = (request.session.get(session_key) or '').strip()
    if not saved_qs:
        return None
    return QueryDict(saved_qs, mutable=False)


def _selected_get_value(request, key: str) -> str:
    """
    Retorna o ultimo valor nao vazio para `key` no QueryDict.
    Resolve cenarios com parametros duplicados (ex.: ?safra=&safra=12),
    mantendo o valor realmente selecionado pelo usuario.
    """
    values = request.GET.getlist(key) or []
    for val in reversed(values):
        norm = _normalize_filter_value(val)
        if norm:
            return norm
    # Se nao houver valor nao-vazio, devolve normalizado do get padrao.
    return _normalize_filter_value(request.GET.get(key))


def _resolve_per_page(request, fallback):
    allowed = {5, 10, 20, 30}
    raw_get = (request.GET.get('per_page') or '').strip()
    if raw_get.isdigit():
        val = int(raw_get)
        if val in allowed:
            return val
    raw_cookie = (request.COOKIES.get('per_page') or '').strip()
    if raw_cookie.isdigit():
        val = int(raw_cookie)
        if val in allowed:
            return val
    return fallback


def _default_cultura_soja_id():
    try:
        soja = Cultura.objects.filter(nome__icontains='soja').order_by('id').only('id').first()
        return str(soja.id) if soja else ''
    except Exception:
        return ''


def _get_topbar_state(request, scope: str, default_cultura: str = ''):
    """
    Estado Cultura/Safra do topo por painel, persistido em sessão.
    """
    culturas_qs = Cultura.objects.all().order_by('nome')
    safras_qs = Safra.objects.select_related('cultura').all().order_by('-ano', 'safra')

    session_key_c = f'{scope}_filtro_cultura'
    session_key_s = f'{scope}_filtro_safra'

    if 'cultura' in request.GET:
        # Permite selecionar "Todas" (valor vazio) sem forcar fallback para cultura padrao.
        filtro_cultura = _selected_get_value(request, 'cultura')
        request.session[session_key_c] = filtro_cultura
    else:
        filtro_cultura = _normalize_filter_value(request.session.get(session_key_c)) or default_cultura

    if 'safra' in request.GET:
        filtro_safra = _selected_get_value(request, 'safra')
        request.session[session_key_s] = filtro_safra
    else:
        filtro_safra = _normalize_filter_value(request.session.get(session_key_s))

    # Se safra não pertence à cultura ativa, limpa.
    if filtro_safra and filtro_cultura:
        try:
            ok = safras_qs.filter(pk=filtro_safra, cultura_id=filtro_cultura).exists()
        except Exception:
            ok = False
        if not ok:
            filtro_safra = ''
            request.session[session_key_s] = ''

    safras_topbar = safras_qs.filter(cultura_id=filtro_cultura) if filtro_cultura else safras_qs
    return {
        'culturas': culturas_qs,
        'safras': safras_qs,
        'safras_topbar': safras_topbar,
        'filtro_cultura': filtro_cultura,
        'filtro_safra': filtro_safra,
    }


def _apply_safra_period_filter(qs, safra_id, field_name: str):
    """
    Aplica janela de período da safra selecionada no campo de data informado.
    Ex.: data do pedido/faturamento/planejamento ou vencimento em contas.
    """
    if not safra_id:
        return qs
    try:
        safra = Safra.objects.only('data_inicio', 'data_fim').filter(pk=safra_id).first()
        if not safra:
            return qs
        filters = {}
        if safra.data_inicio:
            filters[f'{field_name}__gte'] = safra.data_inicio
        if safra.data_fim:
            filters[f'{field_name}__lte'] = safra.data_fim
        return qs.filter(**filters) if filters else qs
    except Exception:
        return qs


def landing_page(request):
    return render(request, 'core/landing_page.html')


@login_required
def _build_dashboard_context(request):
    """
    Centraliza os cálculos da Dashboard para reutilizar na tela e em relatórios.
    """
    user = request.user
    role = getattr(user, 'effective_role', None)

    culturas_qs = Cultura.objects.all().order_by('nome')
    safras_qs = Safra.objects.select_related('cultura').all().order_by('-ano', 'safra')

    default_cultura = culturas_qs.filter(nome__iexact='Soja').first()
    default_cultura_id = str(default_cultura.pk) if default_cultura else ''
    topbar_state = _get_topbar_state(request, scope='dashboard', default_cultura=default_cultura_id)
    filtro_cultura = topbar_state['filtro_cultura']
    filtro_safra = topbar_state['filtro_safra']

    # Filtros secundários (mantidos para compatibilidade do painel)
    filtros_ativos = _filters_active(request, ['categoria', 'cliente', 'produtor', 'fornecedor'])
    filtro_categoria = _normalize_filter_value(request.GET.get('categoria')) if filtros_ativos else ''
    filtro_cliente = _normalize_filter_value(request.GET.get('cliente')) if filtros_ativos else ''
    filtro_produtor = _normalize_filter_value(request.GET.get('produtor')) if filtros_ativos else ''
    filtro_fornecedor = _normalize_filter_value(request.GET.get('fornecedor')) if filtros_ativos else ''

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
        # Em dashboard, o vinculo pela safra deve prevalecer para planejado.
        # Nao aplicamos corte por data em planejamento para evitar zerar dados
        # validos da safra quando a data do lancamento estiver fora da janela.
        fat_itens = _apply_safra_period_filter(fat_itens, filtro_safra, 'faturamento__data')
    if filtro_cultura:
        pl_itens = pl_itens.filter(planejamento__safra__cultura_id=filtro_cultura)
        fat_itens = fat_itens.filter(faturamento__safra__cultura_id=filtro_cultura)
        ped_qs = ped_qs.filter(safra__cultura_id=filtro_cultura)
    if filtro_cliente:
        pl_itens = pl_itens.filter(planejamento__cliente_id=filtro_cliente)
        fat_itens = fat_itens.filter(faturamento__cliente_id=filtro_cliente)
        ped_qs = ped_qs.filter(cliente_id=filtro_cliente)
    if filtro_produtor:
        pl_itens = pl_itens.filter(planejamento__produtor_id=filtro_produtor)
        fat_itens = fat_itens.filter(faturamento__produtor_id=filtro_produtor)
        ped_qs = ped_qs.filter(produtor_id=filtro_produtor)
    if filtro_fornecedor:
        fat_itens = fat_itens.filter(faturamento__fornecedor_id=filtro_fornecedor)
        ped_qs = ped_qs.filter(fornecedor_id=filtro_fornecedor)
    if filtro_categoria:
        pl_itens = pl_itens.filter(produto_cadastro__categoria_id=filtro_categoria)
        fat_itens = fat_itens.filter(produto_cadastro__categoria_id=filtro_categoria)
        ped_qs = ped_qs.filter(itens__produto_cadastro__categoria_id=filtro_categoria).distinct()

    # KPIs da dashboard alinhados com as regras do painel de Planejamento.
    pl_qs = Planejamento.objects.all()
    if filtro_safra:
        pl_qs = pl_qs.filter(safra_id=filtro_safra)
    if filtro_cultura:
        pl_qs = pl_qs.filter(safra__cultura_id=filtro_cultura)
    if filtro_cliente:
        pl_qs = pl_qs.filter(cliente_id=filtro_cliente)
    if filtro_categoria:
        pl_qs = pl_qs.filter(itens__produto_cadastro__categoria_id=filtro_categoria).distinct()

    planned_total = _to_decimal(pl_qs.aggregate(total=Sum('valor_total')).get('total'))
    avg_preco_prod = _to_decimal(pl_qs.aggregate(media=Avg('preco_produto')).get('media'))

    pl_rows = list(pl_qs.values('id', 'preco_produto'))
    pl_ids = [r['id'] for r in pl_rows]
    preco_map = {r['id']: _to_decimal(r.get('preco_produto')) for r in pl_rows}
    itens_sc_rows = (
        PlanejamentoItem.objects.filter(planejamento_id__in=pl_ids)
        .values('planejamento_id', 'area_ha', 'total_item')
    )
    area_total_por_pl = defaultdict(lambda: Decimal('0'))
    sc_total_por_pl = defaultdict(lambda: Decimal('0'))
    q1 = Decimal('0.1')
    for rsc in itens_sc_rows:
        pid = rsc.get('planejamento_id')
        if not pid:
            continue
        area = _to_decimal(rsc.get('area_ha'))
        total_item = _to_decimal(rsc.get('total_item'))
        preco_ref = _to_decimal(preco_map.get(pid))
        area_total_por_pl[pid] += area
        if area > 0 and preco_ref > 0:
            sc_item = (total_item / area / preco_ref).quantize(q1, rounding=ROUND_HALF_UP)
        else:
            sc_item = Decimal('0')
        sc_total_por_pl[pid] += sc_item

    if area_total_por_pl:
        planned_area = (sum(area_total_por_pl.values()) / Decimal(len(area_total_por_pl))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        planned_area = Decimal('0')

    if sc_total_por_pl:
        sc_ha = (sum(sc_total_por_pl.values()) / Decimal(len(sc_total_por_pl))).quantize(q1, rounding=ROUND_HALF_UP)
    else:
        sc_ha = Decimal('0')

    faturado_total = _to_decimal(fat_itens.aggregate(total=Sum('total_item')).get('total'))
    pedido_aguardando_faturamento = _to_decimal(ped_qs.aggregate(total=Sum('saldo_faturar')).get('total'))
    total_faturado = pedido_aguardando_faturamento + faturado_total
    realized_total = total_faturado

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

    # Pedido aguardando faturamento por item (rateio proporcional pelo total do pedido)
    pedido_itens_qs = (
        PedidoCompraItem.objects.select_related(
            'pedido_compra',
            'pedido_compra__safra',
            'pedido_compra__safra__cultura',
            'pedido_compra__cliente',
            'pedido_compra__produtor',
            'pedido_compra__fornecedor',
            'produto_cadastro',
            'produto_cadastro__categoria',
        )
        .filter(produto_cadastro__isnull=False)
    )
    if filtro_safra:
        pedido_itens_qs = pedido_itens_qs.filter(pedido_compra__safra_id=filtro_safra)
    if filtro_cultura:
        pedido_itens_qs = pedido_itens_qs.filter(pedido_compra__safra__cultura_id=filtro_cultura)
    if filtro_cliente:
        pedido_itens_qs = pedido_itens_qs.filter(pedido_compra__cliente_id=filtro_cliente)
    if filtro_produtor:
        pedido_itens_qs = pedido_itens_qs.filter(pedido_compra__produtor_id=filtro_produtor)
    if filtro_fornecedor:
        pedido_itens_qs = pedido_itens_qs.filter(pedido_compra__fornecedor_id=filtro_fornecedor)
    if filtro_categoria:
        pedido_itens_qs = pedido_itens_qs.filter(produto_cadastro__categoria_id=filtro_categoria)

    pedido_itens_values = list(
        pedido_itens_qs.values(
            'pedido_compra_id',
            'pedido_compra__valor_total',
            'pedido_compra__saldo_faturar',
            'pedido_compra__fornecedor_id',
            'pedido_compra__safra__ano',
            'produto_cadastro_id',
            'produto_cadastro__nome',
            'produto_cadastro__categoria_id',
            'total_item',
        )
    )
    pedido_pendente_por_categoria = defaultdict(lambda: Decimal('0'))
    pedido_pendente_por_produto = defaultdict(lambda: Decimal('0'))
    pedido_pendente_por_fornecedor = defaultdict(lambda: Decimal('0'))
    pedido_pendente_por_ano = defaultdict(lambda: Decimal('0'))
    for it in pedido_itens_values:
        pedido_total = _to_decimal(it.get('pedido_compra__valor_total'))
        saldo_pedido = _to_decimal(it.get('pedido_compra__saldo_faturar'))
        item_total = _to_decimal(it.get('total_item'))
        if pedido_total > 0:
            item_pendente = (item_total * saldo_pedido / pedido_total)
        else:
            item_pendente = Decimal('0')
        if item_pendente <= 0:
            continue
        categoria_id = it.get('produto_cadastro__categoria_id')
        produto_id = it.get('produto_cadastro_id')
        fornecedor_id = it.get('pedido_compra__fornecedor_id')
        safra_ano = it.get('pedido_compra__safra__ano')
        if categoria_id:
            pedido_pendente_por_categoria[categoria_id] += item_pendente
        if produto_id:
            pedido_pendente_por_produto[produto_id] += item_pendente
        if fornecedor_id:
            pedido_pendente_por_fornecedor[fornecedor_id] += item_pendente
        if safra_ano is not None:
            pedido_pendente_por_ano[int(safra_ano)] += item_pendente

    # Resumo por categoria (Planejado x Pedido x Faturado)
    planned_cat = {
        r['produto_cadastro__categoria_id']: _to_decimal(r['total'])
        for r in pl_itens.values('produto_cadastro__categoria_id').annotate(total=Sum('total_item'))
    }
    faturado_cat = {
        r['produto_cadastro__categoria_id']: _to_decimal(r['total'])
        for r in fat_itens.values('produto_cadastro__categoria_id').annotate(total=Sum('total_item'))
    }
    categorias = list(Categoria.objects.all().order_by('nome'))
    categorias_rows = []
    max_cat = Decimal('0')
    for c in categorias:
        p = planned_cat.get(c.pk, Decimal('0'))
        pe = pedido_pendente_por_categoria.get(c.pk, Decimal('0'))
        f = faturado_cat.get(c.pk, Decimal('0'))
        # Remove categorias totalmente zeradas (melhora leitura do grafico)
        if (p or Decimal('0')) == 0 and (pe or Decimal('0')) == 0 and (f or Decimal('0')) == 0:
            continue
        max_cat = max(max_cat, p, pe, f)
        categorias_rows.append({'obj': c, 'planejado': p, 'pedido': pe, 'faturado': f})

    # Percentuais para barras (evita filtros no template)
    for row in categorias_rows:
        if max_cat > 0:
            row['pct_pedido'] = int(min(Decimal('100'), _safe_div(row['pedido'] * Decimal('100'), max_cat)).quantize(Decimal('1')))
            row['pct_faturado'] = int(min(Decimal('100'), _safe_div(row['faturado'] * Decimal('100'), max_cat)).quantize(Decimal('1')))
            row['pct_planejado'] = int(min(Decimal('100'), _safe_div(row['planejado'] * Decimal('100'), max_cat)).quantize(Decimal('1')))
        else:
            row['pct_pedido'] = 0
            row['pct_faturado'] = 0
            row['pct_planejado'] = 0

        row['pct_planejado_total'] = (_safe_div(row['planejado'] * Decimal('100'), planned_total).quantize(Decimal('0.1')) if planned_total > 0 else Decimal('0.0'))
        row['pct_pedido_total'] = (_safe_div(row['pedido'] * Decimal('100'), pedido_aguardando_faturamento).quantize(Decimal('0.1')) if pedido_aguardando_faturamento > 0 else Decimal('0.0'))
        row['pct_faturado_total'] = (_safe_div(row['faturado'] * Decimal('100'), faturado_total).quantize(Decimal('0.1')) if faturado_total > 0 else Decimal('0.0'))
    categorias_rows.sort(key=lambda x: x.get('planejado', Decimal('0')), reverse=True)

    # Resumo por produto (Planejado x Pedido x Faturado)
    top_fat = {
        r['produto_cadastro_id']: _to_decimal(r['total'])
        for r in fat_itens.values('produto_cadastro_id').annotate(total=Sum('total_item'))
    }
    top_plan = {
        r['produto_cadastro_id']: _to_decimal(r['total'])
        for r in pl_itens.values('produto_cadastro_id').annotate(total=Sum('total_item'))
    }
    nomes_produto = {
        r['produto_cadastro_id']: (r.get('produto_cadastro__nome') or '-')
        for r in fat_itens.values('produto_cadastro_id', 'produto_cadastro__nome').distinct()
    }
    nomes_produto.update({
        r['produto_cadastro_id']: (r.get('produto_cadastro__nome') or '-')
        for r in pl_itens.values('produto_cadastro_id', 'produto_cadastro__nome').distinct()
    })
    produtos_rows = []
    max_prod = Decimal('0')
    for pid in sorted(set(top_fat.keys()) | set(top_plan.keys()) | set(pedido_pendente_por_produto.keys())):
        fat = top_fat.get(pid, Decimal('0'))
        plan = top_plan.get(pid, Decimal('0'))
        ped = pedido_pendente_por_produto.get(pid, Decimal('0'))
        if plan == 0 and ped == 0 and fat == 0:
            continue
        max_prod = max(max_prod, fat, plan, ped)
        produtos_rows.append({'nome': nomes_produto.get(pid, '-'), 'planejado': plan, 'pedido': ped, 'faturado': fat})

    produtos_rows.sort(key=lambda x: x.get('planejado', Decimal('0')), reverse=True)

    for row in produtos_rows:
        if max_prod > 0:
            row['pct_pedido'] = int(min(Decimal('100'), _safe_div(row['pedido'] * Decimal('100'), max_prod)).quantize(Decimal('1')))
            row['pct_faturado'] = int(min(Decimal('100'), _safe_div(row['faturado'] * Decimal('100'), max_prod)).quantize(Decimal('1')))
            row['pct_planejado'] = int(min(Decimal('100'), _safe_div(row['planejado'] * Decimal('100'), max_prod)).quantize(Decimal('1')))
        else:
            row['pct_pedido'] = 0
            row['pct_faturado'] = 0
            row['pct_planejado'] = 0
        row['pct_planejado_total'] = (_safe_div(row['planejado'] * Decimal('100'), planned_total).quantize(Decimal('0.1')) if planned_total > 0 else Decimal('0.0'))
        row['pct_pedido_total'] = (_safe_div(row['pedido'] * Decimal('100'), pedido_aguardando_faturamento).quantize(Decimal('0.1')) if pedido_aguardando_faturamento > 0 else Decimal('0.0'))
        row['pct_faturado_total'] = (_safe_div(row['faturado'] * Decimal('100'), faturado_total).quantize(Decimal('0.1')) if faturado_total > 0 else Decimal('0.0'))

    # Ranking por fornecedor (Pedido x Faturado)
    faturado_for = {
        r['faturamento__fornecedor_id']: _to_decimal(r['total'])
        for r in fat_itens.filter(faturamento__fornecedor__isnull=False).values('faturamento__fornecedor_id').annotate(total=Sum('total_item'))
    }

    pedido_for = dict(pedido_pendente_por_fornecedor)
    forn_ids = sorted(set(faturado_for.keys()) | set(pedido_for.keys()))
    fornecedores_map = {f.pk: f for f in Fornecedor.objects.filter(pk__in=forn_ids)}
    fornecedores_rows = []
    max_forn = Decimal('0')
    for fid in forn_ids:
        ped = pedido_for.get(fid, Decimal('0'))
        fat = faturado_for.get(fid, Decimal('0'))
        if ped == 0 and fat == 0:
            continue
        max_forn = max(max_forn, ped, fat)
        fobj = fornecedores_map.get(fid)
        label = ''
        if fobj:
            label = (getattr(fobj, 'fantasia', '') or '').strip() or (getattr(fobj, 'fornecedor', '') or '').strip()
        if not label:
            label = '-'
        fornecedores_rows.append({'obj': fobj, 'label': label, 'pedido': ped, 'faturado': fat})

    for row in fornecedores_rows:
        if max_forn > 0:
            row['pct_pedido'] = int(min(Decimal('100'), _safe_div(row['pedido'] * Decimal('100'), max_forn)).quantize(Decimal('1')))
            row['pct_faturado'] = int(min(Decimal('100'), _safe_div(row['faturado'] * Decimal('100'), max_forn)).quantize(Decimal('1')))
        else:
            row['pct_pedido'] = 0
            row['pct_faturado'] = 0
        row['pct_pedido_total'] = (_safe_div(row['pedido'] * Decimal('100'), pedido_aguardando_faturamento).quantize(Decimal('0.1')) if pedido_aguardando_faturamento > 0 else Decimal('0.0'))
        row['pct_faturado_total'] = (_safe_div(row['faturado'] * Decimal('100'), faturado_total).quantize(Decimal('0.1')) if faturado_total > 0 else Decimal('0.0'))

    fornecedores_rows.sort(key=lambda x: x.get('faturado', Decimal('0')), reverse=True)

    # Faturamento das safras por cultura (ultimos 4 anos + ano atual)
    ano_atual = timezone.localdate().year
    ano_referencia = ano_atual
    if filtro_safra:
        try:
            safra_ref = Safra.objects.only('ano').filter(pk=filtro_safra).first()
            if safra_ref and safra_ref.ano:
                ano_referencia = max(ano_atual, int(safra_ref.ano))
        except Exception:
            ano_referencia = ano_atual
    anos_janela = [ano_referencia - 4, ano_referencia - 3, ano_referencia - 2, ano_referencia - 1, ano_referencia]
    fat_por_ano_raw = (
        fat_itens.values('faturamento__safra__ano')
        .annotate(total=Sum('total_item'))
        .order_by('faturamento__safra__ano')
    )
    fat_por_ano_map = {}
    pl_por_ano_raw = (
        pl_itens.values('planejamento__safra__ano')
        .annotate(total=Sum('total_item'))
        .order_by('planejamento__safra__ano')
    )
    pl_por_ano_map = {}
    for r in fat_por_ano_raw:
        ano = r.get('faturamento__safra__ano')
        if ano is None:
            continue
        fat_por_ano_map[int(ano)] = _to_decimal(r.get('total'))
    for r in pl_por_ano_raw:
        ano = r.get('planejamento__safra__ano')
        if ano is None:
            continue
        pl_por_ano_map[int(ano)] = _to_decimal(r.get('total'))
    faturamento_anos_rows = [
        {
            'ano': a,
            'planejado': pl_por_ano_map.get(a, Decimal('0')),
            'pedido': pedido_pendente_por_ano.get(a, Decimal('0')),
            'faturado': fat_por_ano_map.get(a, Decimal('0')),
        }
        for a in anos_janela
    ]

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

    safras_topbar = topbar_state['safras_topbar']

    return {
        'titulo': 'Dashboard',
        'subtitulo': subtitulo,
        'role_display': user.get_effective_role_display(),
        'licenca': licenca,
        # filtros
        'culturas': culturas_qs,
        'safras': safras_qs,
        'safras_topbar': safras_topbar,
        'categorias': Categoria.objects.all().order_by('nome'),
        'clientes': Cliente.objects.all().order_by('cliente'),
        'produtores': Produtor.objects.all().order_by('produtor', 'fazenda'),
        'fornecedores': Fornecedor.objects.all().order_by('fornecedor'),
        'filtro_cultura': filtro_cultura,
        'filtro_safra': filtro_safra,
        'filtro_categoria': filtro_categoria,
        'filtro_cliente': filtro_cliente,
        'filtro_produtor': filtro_produtor,
        'filtro_fornecedor': filtro_fornecedor,
        'safra_obj': safra_obj,
        # KPIs
        'planned_total': planned_total,
        'realized_total': realized_total,
        'pedido_total': pedido_aguardando_faturamento,
        'faturado_total': faturado_total,
        'total_faturado': total_faturado,
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
        'faturamento_anos_rows': faturamento_anos_rows,
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
            'filtro_produtor': '',
            'filtro_fornecedor': '',
            'culturas': [],
            'safras': [],
            'safras_topbar': [],
            'categorias': [],
            'clientes': [],
            'produtores': [],
            'fornecedores': [],
            'planned_total': Decimal('0'),
            'realized_total': Decimal('0'),
            'pedido_total': Decimal('0'),
            'faturado_total': Decimal('0'),
            'total_faturado': Decimal('0'),
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
            'faturamento_anos_rows': [],
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
        return _resolve_per_page(self.request, super().get_paginate_by(queryset))

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

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)

        for col in (self.columns or []):
            if isinstance(col, (list, tuple)) and len(col) >= 2:
                label, field = col[0], col[1]
            else:
                label, field = str(col), str(col)

            params = base_params.copy()
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = self.context_title or (self.model._meta.verbose_name_plural.title() if self.model else 'Lista')
        ctx['columns'] = self._build_columns()
        ctx['create_url_name'] = self.create_url_name
        ctx['edit_url_name'] = self.edit_url_name
        ctx['delete_url_name'] = self.delete_url_name
        ctx['q'] = self.request.GET.get('q', '')
        ctx['current_q'] = (self.request.GET.get('q') or '').strip()
        ctx['current_sort'] = (self.request.GET.get('o') or '').strip()
        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()
        try:
            ctx['total_registros'] = self.get_queryset().count()
        except Exception:
            ctx['total_registros'] = 0
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

    def get_success_url(self):
        raw_next = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        if raw_next and url_has_allowed_host_and_scheme(raw_next, allowed_hosts={self.request.get_host()}):
            return raw_next
        return str(self.success_url or '/')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = getattr(self, 'context_title', None) or f'Novo {self.model._meta.verbose_name.title()}'
        ctx['next_url'] = (self.request.GET.get('next') or self.request.POST.get('next') or '').strip()
        return ctx


class CrudUpdateView(UpdateView):
    template_name = 'core/crud/form.html'
    model = None
    form_class = None
    success_url = None

    def get_success_url(self):
        raw_next = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        if raw_next and url_has_allowed_host_and_scheme(raw_next, allowed_hosts={self.request.get_host()}):
            return raw_next
        return str(self.success_url or '/')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = getattr(self, 'context_title', None) or f'Editar {self.model._meta.verbose_name.title()}'
        ctx['next_url'] = (self.request.GET.get('next') or self.request.POST.get('next') or '').strip()
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

    is_admin = getattr(request.user, 'effective_role', '') == 'ADMIN'
    if request.method == 'POST':
        if not is_admin:
            messages.error(request, 'Apenas administrador pode alterar assinaturas e faturas.')
            return redirect('core:licencas_page')

        acao = (request.POST.get('acao') or '').strip().lower()
        try:
            if acao == 'editar_licenca':
                lic = get_object_or_404(Licenca, pk=request.POST.get('licenca_id'))
                valor_total = _parse_decimal_br(request.POST.get('valor_total') or '0')
                inicio_vigencia_raw = (request.POST.get('inicio_vigencia') or '').strip()
                fim_vigencia_raw = (request.POST.get('fim_vigencia') or '').strip()
                inicio_vigencia = datetime.strptime(inicio_vigencia_raw, '%Y-%m-%d').date() if inicio_vigencia_raw else None
                fim_vigencia = datetime.strptime(fim_vigencia_raw, '%Y-%m-%d').date() if fim_vigencia_raw else None
                lic.valor_total = valor_total
                lic.inicio_vigencia = inicio_vigencia
                lic.fim_vigencia = fim_vigencia
                lic.save(update_fields=['valor_total', 'inicio_vigencia', 'fim_vigencia', 'updated_at'])
                messages.success(request, 'Assinatura atualizada com sucesso.')
                return redirect('core:licencas_page')

            if acao == 'nova_fatura':
                lic = get_object_or_404(Licenca, pk=request.POST.get('licenca_id'))
                numero = int((request.POST.get('numero') or '1').strip() or '1')
                total_parcelas = int((request.POST.get('total_parcelas') or '1').strip() or '1')
                vencimento = datetime.strptime((request.POST.get('vencimento') or '').strip(), '%Y-%m-%d').date()
                valor = _parse_decimal_br(request.POST.get('valor') or '0')
                forma = (request.POST.get('forma_pagamento') or Licenca.FormaPagamento.PIX).strip().upper()
                if forma not in {Licenca.FormaPagamento.PIX, Licenca.FormaPagamento.BOLETO}:
                    forma = Licenca.FormaPagamento.PIX
                LicencaFatura.objects.create(
                    licenca=lic,
                    numero=max(1, numero),
                    total_parcelas=max(1, total_parcelas),
                    vencimento=vencimento,
                    valor=valor,
                    forma_pagamento=forma,
                    status=LicencaFatura.Status.PENDENTE,
                )
                _recalcular_licenca_por_faturas(lic)
                messages.success(request, 'Fatura cadastrada com sucesso.')
                return redirect('core:licencas_page')

            if acao == 'editar_fatura':
                f = get_object_or_404(LicencaFatura, pk=request.POST.get('fatura_id'))
                numero = int((request.POST.get('numero') or str(f.numero)).strip() or str(f.numero))
                total_parcelas = int((request.POST.get('total_parcelas') or str(f.total_parcelas)).strip() or str(f.total_parcelas))
                vencimento = datetime.strptime((request.POST.get('vencimento') or '').strip(), '%Y-%m-%d').date()
                valor = _parse_decimal_br(request.POST.get('valor') or '0')
                forma = (request.POST.get('forma_pagamento') or f.forma_pagamento).strip().upper()
                status = (request.POST.get('status') or f.status).strip().upper()
                data_pagamento_raw = (request.POST.get('data_pagamento') or '').strip()
                data_pagamento = datetime.strptime(data_pagamento_raw, '%Y-%m-%d').date() if data_pagamento_raw else None
                if forma not in {Licenca.FormaPagamento.PIX, Licenca.FormaPagamento.BOLETO}:
                    forma = Licenca.FormaPagamento.PIX
                if status not in {LicencaFatura.Status.PENDENTE, LicencaFatura.Status.PAGA, LicencaFatura.Status.VENCIDA, LicencaFatura.Status.CANCELADA}:
                    status = LicencaFatura.Status.PENDENTE
                f.numero = max(1, numero)
                f.total_parcelas = max(1, total_parcelas)
                f.vencimento = vencimento
                f.valor = valor
                f.forma_pagamento = forma
                f.status = status
                f.data_pagamento = data_pagamento
                f.save(update_fields=['numero', 'total_parcelas', 'vencimento', 'valor', 'forma_pagamento', 'status', 'data_pagamento', 'updated_at'])
                _recalcular_licenca_por_faturas(f.licenca)
                messages.success(request, 'Fatura atualizada com sucesso.')
                return redirect('core:licencas_page')

            if acao == 'excluir_fatura':
                f = get_object_or_404(LicencaFatura, pk=request.POST.get('fatura_id'))
                lic_ref = f.licenca
                f.delete()
                _recalcular_licenca_por_faturas(lic_ref)
                messages.success(request, 'Fatura excluida com sucesso.')
                return redirect('core:licencas_page')
        except Exception as exc:
            messages.error(request, f'Nao foi possivel processar a operacao: {exc}')
            return redirect('core:licencas_page')

    lic = None
    perfil = getattr(request.user, 'perfil_licenca', None)
    if perfil:
        lic = perfil.licenca

    if is_admin:
        historico_qs = Licenca.objects.all().order_by('-updated_at')[:50]
    else:
        historico_qs = Licenca.objects.filter(pk=lic.pk).order_by('-updated_at') if lic else Licenca.objects.none()

    historico_list = list(historico_qs)
    for _lic in historico_list:
        _recalcular_licenca_por_faturas(_lic)
    # Recarrega apos possiveis ajustes automáticos de status/valor
    historico_list = list(historico_qs)
    licenca_ref = lic or (historico_list[0] if (is_admin and historico_list) else None)
    licenca_ids = [x.pk for x in historico_list]
    if is_admin:
        faturas_qs = LicencaFatura.objects.select_related('licenca').filter(licenca_id__in=licenca_ids).order_by('-vencimento', '-numero', '-updated_at')[:300]
    else:
        faturas_qs = LicencaFatura.objects.select_related('licenca').filter(licenca=licenca_ref).order_by('-vencimento', '-numero', '-updated_at')[:120] if licenca_ref else LicencaFatura.objects.none()
    faturas_list = list(faturas_qs)

    hoje = timezone.localdate()
    qtd_alerta_renovacao = 0
    for item in historico_list:
        item.alerta_renovacao = False
        item.dias_para_vencer = None
        if item.status == Licenca.Status.ATIVA and item.fim_vigencia:
            dias = (item.fim_vigencia - hoje).days
            item.dias_para_vencer = dias
            if 0 <= dias <= 30:
                item.alerta_renovacao = True
                qtd_alerta_renovacao += 1

    if qtd_alerta_renovacao:
        messages.warning(
            request,
            f'{qtd_alerta_renovacao} assinatura(s) em janela de renovacao (ate 30 dias para vencer).',
        )

    can_register_nova_assinatura = False
    register_hint = ''
    if licenca_ref:
        faturas_ref = list(LicencaFatura.objects.filter(licenca=licenca_ref))
        if not faturas_ref:
            can_register_nova_assinatura = True
        else:
            todas_pagas = all(f.status == LicencaFatura.Status.PAGA for f in faturas_ref)
            can_register_nova_assinatura = todas_pagas
            if not todas_pagas:
                register_hint = 'Libera apos pagamento da ultima parcela da assinatura atual.'
        if licenca_ref.status == Licenca.Status.ATIVA:
            can_register_nova_assinatura = False
            register_hint = 'Assinatura vigente: aguarde finalizar o ciclo atual para registrar nova assinatura.'

    if not licenca_ref:
        licenca_action_label = 'Registrar'
        licenca_action_url = '/licencas/registrar/'
    else:
        licenca_action_label = 'Registrar nova assinatura'
        licenca_action_url = '/licencas/renovar/' if can_register_nova_assinatura else ''
        if not register_hint and not can_register_nova_assinatura:
            register_hint = 'A assinatura atual ainda possui parcelas pendentes.'

    pode_editar_excluir = bool(licenca_ref and licenca_ref.status != Licenca.Status.ATIVA)
    if is_admin:
        pode_editar_excluir = bool(licenca_ref)

    return render(
        request,
        'core/licencas.html',
        {
            'titulo': 'Licenca de Uso, Suporte Tecnico e Manutencao',
            'licenca': licenca_ref,
            'licenca_atual': licenca_ref,
            'licencas': historico_list,
            'faturas_geradas': faturas_list,
            'historico': historico_list,
            'licenca_status_efetivo': lic.status if lic else '',
            'licenca_status_label': lic.get_status_display() if lic else 'Sem assinatura',
            'licenca_action_label': licenca_action_label,
            'licenca_action_url': licenca_action_url,
            'can_register_nova_assinatura': can_register_nova_assinatura,
            'register_hint': register_hint,
            'pode_editar_excluir': pode_editar_excluir,
            'is_admin': is_admin,
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
                messages.info(request, "Restauracao executada em modo seguro (normalizacao de codificacao e protecao contra conflito de usuario).")
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
    if getattr(request.user, 'effective_role', '') not in {'ADMIN', 'SUPERVISOR', 'CLIENTE'}:
        return JsonResponse({'items': []})

    cliente_id = (request.GET.get('cliente') or request.GET.get('cliente_id') or '').strip()
    if not cliente_id.isdigit():
        return JsonResponse({'items': []})

    qs = Produtor.objects.filter(cliente_id=int(cliente_id)).order_by('produtor', 'fazenda')
    if getattr(request.user, 'effective_role', '') == 'CLIENTE':
        cliente_usuario = _get_cliente_do_usuario(request.user)
        if not cliente_usuario or int(cliente_id) != int(cliente_usuario.pk):
            return JsonResponse({'items': []})
    items = []
    for p in qs:
        produtor_nome = ((p.apelido or p.produtor) or '').strip()
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
    items = [{'id': f.pk, 'label': (f.fantasia or f.fornecedor)} for f in qs]
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
    list_template='core/crud/list.html',
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
SafraListView.context_title = 'Safras'

CulturaListView, CulturaCreateView, CulturaUpdateView, CulturaDeleteView = _make_simple_crud(
    'cultura',
    Cultura,
    CulturaForm,
    list_template='core/crud/list.html',
    search_fields=['nome'],
    ordering='nome',
)
CulturaListView.columns = [('Cultura', 'nome')]
CulturaListView.modal_form_context_name = 'cultura_form'
CulturaListView.paginate_by = 10
CulturaListView.context_title = 'Culturas'

CustoListView, CustoCreateView, CustoUpdateView, CustoDeleteView = _make_simple_crud(
    'custo',
    Custo,
    CustoForm,
    list_template='core/crud/list.html',
    search_fields=['nome'],
    ordering='nome',
)
CustoListView.columns = [('Custo', 'nome')]
CustoListView.paginate_by = 10
CustoListView.context_title = 'Custos'

CategoriaListView, CategoriaCreateView, CategoriaUpdateView, CategoriaDeleteView = _make_simple_crud(
    'categoria',
    Categoria,
    CategoriaForm,
    list_template='core/crud/list.html',
    search_fields=['nome'],
    ordering='nome',
)
CategoriaListView.columns = [('Categoria', 'nome')]
CategoriaListView.paginate_by = 10
CategoriaListView.context_title = 'Categorias'

UnidadeListView, UnidadeCreateView, UnidadeUpdateView, UnidadeDeleteView = _make_simple_crud(
    'unidade',
    Unidade,
    UnidadeForm,
    list_template='core/crud/list.html',
    search_fields=['nome', 'unidade_abreviado'],
    ordering='nome',
)
UnidadeListView.columns = [('Unidade', 'nome'), ('Volume', 'volume'), ('Abrev', 'unidade_abreviado')]
UnidadeListView.paginate_by = 10
UnidadeListView.context_title = 'Unidades'

FormaPagamentoListView, FormaPagamentoCreateView, FormaPagamentoUpdateView, FormaPagamentoDeleteView = _make_simple_crud(
    'forma_pagamento',
    FormaPagamento,
    FormaPagamentoForm,
    list_template='core/crud/list.html',
    search_fields=['pagamento'],
    ordering='pagamento',
)
FormaPagamentoListView.columns = [('Pagamento', 'pagamento'), ('Parcelas', 'parcelas'), ('Prazo', 'prazo')]
FormaPagamentoListView.paginate_by = 10
FormaPagamentoListView.context_title = 'Formas de Pagamento'

OperacaoListView, OperacaoCreateView, OperacaoUpdateView, OperacaoDeleteView = _make_simple_crud(
    'operacao',
    Operacao,
    OperacaoForm,
    list_template='core/crud/list.html',
    search_fields=['operacao'],
    ordering='operacao',
)
OperacaoListView.columns = [('Operacao', 'operacao'), ('Tipo', 'tipo')]
OperacaoListView.paginate_by = 10
OperacaoListView.context_title = 'Operacoes'

FornecedorListView, FornecedorCreateView, FornecedorUpdateView, FornecedorDeleteView = _make_simple_crud(
    'fornecedor',
    Fornecedor,
    FornecedorForm,
    list_template='core/fornecedores/list.html',
    search_fields=['fornecedor', 'fantasia', 'cnpj', 'cidade'],
    ordering='fornecedor',
)
FornecedorListView.columns = [
    ('Fornecedor', 'fornecedor'),
    ('Fantasia', 'fantasia'),
    ('CNPJ', 'cnpj'),
    ('Cidade', 'cidade'),
    ('UF', 'uf'),
    ('Status', 'status'),
]
FornecedorListView.paginate_by = 10
FornecedorListView.context_title = 'Fornecedores'
ClienteListView, ClienteCreateView, ClienteUpdateView, ClienteDeleteView = _make_simple_crud(
    'cliente',
    Cliente,
    ClienteForm,
    list_template='core/crud/list.html',
    search_fields=['cliente', 'apelido', 'cpf_cnpj'],
    ordering='cliente',
)
ClienteListView.columns = [
    ('Cliente', 'cliente'),
    ('Apelido', 'apelido'),
    ('CPF/CNPJ', 'cpf_cnpj'),
    ('Status', 'status'),
    ('Limite', 'limite_compra'),
]
ClienteListView.paginate_by = 10
ClienteListView.context_title = 'Clientes'
ProdutorListView, ProdutorCreateView, ProdutorUpdateView, ProdutorDeleteView = _make_simple_crud(
    'produtor',
    Produtor,
    ProdutorForm,
    list_template='core/crud/list.html',
    search_fields=['produtor', 'apelido', 'cpf', 'fazenda', 'cidade'],
    ordering='produtor',
)
ProdutorListView.columns = [
    ('Produtor', 'produtor'),
    ('Apelido', 'apelido'),
    ('Fazenda', 'fazenda'),
    ('Inscricao', 'ie'),
    ('CPF', 'cpf'),
    ('HA', 'ha'),
    ('Cliente', 'cliente'),
    ('Status', 'status'),
]
ProdutorListView.paginate_by = 10
ProdutorListView.context_title = 'Produtores'
PropriedadeListView, PropriedadeCreateView, PropriedadeUpdateView, PropriedadeDeleteView = _make_simple_crud(
    'propriedade',
    Propriedade,
    PropriedadeForm,
    list_template='core/crud/list.html',
    search_fields=['propriedade', 'matricula', 'sicar'],
    ordering='propriedade',
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
    'produto',
    Produto,
    ProdutoForm,
    list_template='core/crud/produto_modal_list.html',
    search_fields=['nome', 'nome_abreviado', 'npk'],
    ordering='nome',
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
PropriedadeListView.context_title = 'Propriedades'
ProdutoListView.paginate_by = 10

def _produto_get_queryset(self):
    qs = ModalCrudListView.get_queryset(self).select_related('custo', 'categoria')

    raw_custo = (self.request.GET.get('custo') or '').strip()
    raw_categoria = (self.request.GET.get('categoria') or '').strip()
    raw_status = (self.request.GET.get('status') or '').strip()

    filtro_custo = '' if raw_custo in {'', '__all__'} else _normalize_filter_value(raw_custo)
    filtro_categoria = '' if raw_categoria in {'', '__all__'} else _normalize_filter_value(raw_categoria)
    filtro_status = '' if raw_status in {'', '__all__'} else _normalize_filter_value(raw_status)

    if filtro_custo:
        qs = qs.filter(custo_id=filtro_custo)
    if filtro_categoria:
        qs = qs.filter(categoria_id=filtro_categoria)
    if filtro_status:
        qs = qs.filter(status=filtro_status)

    return qs

def _produto_get_context_data(self, **kwargs):
    ctx = ModalCrudListView.get_context_data(self, **kwargs)
    ctx['custos'] = Custo.objects.all().order_by('nome')
    ctx['categorias'] = Categoria.objects.all().order_by('nome')
    raw_custo = (self.request.GET.get('custo') or '').strip()
    raw_categoria = (self.request.GET.get('categoria') or '').strip()
    raw_status = (self.request.GET.get('status') or '').strip()
    ctx['filtro_custo'] = '' if raw_custo in {'', '__all__'} else _normalize_filter_value(raw_custo)
    ctx['filtro_categoria'] = '' if raw_categoria in {'', '__all__'} else _normalize_filter_value(raw_categoria)
    ctx['filtro_status'] = '' if raw_status in {'', '__all__'} else _normalize_filter_value(raw_status)
    return ctx

ProdutoListView.get_queryset = _produto_get_queryset
ProdutoListView.get_context_data = _produto_get_context_data

def _produto_get(self, request, *args, **kwargs):
    if _normalize_filter_value(request.GET.get('clear')):
        request.session.pop('produtos_last_filters_qs', None)
        return redirect(request.path)
    # Produtos: abrir painel sempre com filtros explicitos em "Todos" para evitar
    # reaproveitamento visual de selecao anterior pelo navegador.
    if not any(k in request.GET for k in ('custo', 'categoria', 'status')):
        base_qs = request.GET.copy()
        base_qs['custo'] = '__all__'
        base_qs['categoria'] = '__all__'
        base_qs['status'] = '__all__'
        return redirect(f'{request.path}?{base_qs.urlencode()}')

    # A manutencao de filtros ocorre na propria URL apos "Filtrar"/paginacao.
    return ModalCrudListView.get(self, request, *args, **kwargs)

ProdutoListView.get = _produto_get

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

    def _default_cultura_soja_id(self):
        try:
            soja = Cultura.objects.filter(nome__icontains='soja').order_by('id').only('id').first()
            return str(soja.id) if soja else ''
        except Exception:
            return ''

    def get_paginate_by(self, queryset):
        return _resolve_per_page(self.request, super().get_paginate_by(queryset))

    def get(self, request, *args, **kwargs):
        restored = _panel_filters_restore_or_save(request, 'planejamento')
        if restored is not None:
            return redirect(f'{request.path}?{restored.urlencode()}')
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'custo', 'cliente')
            .prefetch_related('itens__produto_cadastro')
            .order_by('-data', '-id')
        )

        topbar = _get_topbar_state(self.request, scope='planejamento', default_cultura=self._default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']

        if topbar_cultura:
            qs = qs.filter(safra__cultura_id=topbar_cultura)
        if topbar_safra:
            qs = qs.filter(safra_id=topbar_safra)

        filtro_cliente = _normalize_filter_value(self.request.GET.get('cliente'))
        filtro_categoria = _normalize_filter_value(self.request.GET.get('categoria'))
        filtro_produto = _normalize_filter_value(self.request.GET.get('produto'))
        filtro_venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        filtro_venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))

        if filtro_cliente:
            qs = qs.filter(cliente_id=filtro_cliente)
        if filtro_categoria:
            qs = qs.filter(itens__produto_cadastro__categoria_id=filtro_categoria)
        if filtro_produto:
            qs = qs.filter(itens__produto_cadastro_id=filtro_produto)
        if filtro_venc_ini:
            qs = qs.filter(vencimento__gte=filtro_venc_ini)
        if filtro_venc_fim:
            qs = qs.filter(vencimento__lte=filtro_venc_fim)

        if filtro_categoria or filtro_produto:
            qs = qs.distinct()
        return qs

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)

        for col in (self.columns or []):
            if isinstance(col, (list, tuple)) and len(col) >= 2:
                label, field = col[0], col[1]
            else:
                label, field = str(col), str(col)

            params = base_params.copy()
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['create_url_name'] = 'core:planejamento_create'
        ctx['edit_url_name'] = 'core:planejamento_update'
        ctx['delete_url_name'] = 'core:planejamento_delete'
        ctx['detail_url_name'] = 'core:planejamento_detail_modal'
        ctx['report_resumo_url_name'] = 'core:planejamento_report_resumo'
        ctx['report_analitico_url_name'] = 'core:planejamento_report_analitico'

        topbar = _get_topbar_state(self.request, scope='planejamento', default_cultura=self._default_cultura_soja_id())

        # Estado do topo (Cultura/Safra)
        ctx['culturas'] = topbar['culturas']
        safras_qs = topbar['safras']
        ctx['safras_all'] = safras_qs
        ctx['safras_topbar'] = topbar['safras_topbar']
        filtro_cultura = topbar['filtro_cultura']
        ctx['filtro_cultura'] = filtro_cultura
        safra_selected = topbar['filtro_safra']
        ctx['filtro_safra'] = safra_selected
        ctx['filtro_cliente'] = _normalize_filter_value(self.request.GET.get('cliente'))
        ctx['filtro_categoria'] = _normalize_filter_value(self.request.GET.get('categoria'))
        ctx['filtro_produto'] = _normalize_filter_value(self.request.GET.get('produto'))
        ctx['filtro_venc_ini'] = _normalize_filter_value(self.request.GET.get('venc_ini'))
        ctx['filtro_venc_fim'] = _normalize_filter_value(self.request.GET.get('venc_fim'))
        ctx['clientes'] = Cliente.objects.all().order_by('cliente')
        ctx['categorias'] = Categoria.objects.all().order_by('nome')
        ctx['produtos'] = Produto.objects.all().order_by('nome')

        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()
        ctx['report_query'] = params.urlencode()
        qs = self.get_queryset()

        # KPIs sempre por Cultura + ultima Safra da Cultura (ou Safra escolhida).
        kpi_qs = qs.none()
        if filtro_cultura:
            if safra_selected:
                kpi_qs = qs.filter(safra_id=safra_selected)
            else:
                ultima_safra = (
                    Safra.objects.filter(cultura_id=filtro_cultura)
                    .order_by('-ano', '-id')
                    .only('id')
                    .first()
                )
                if ultima_safra:
                    kpi_qs = qs.filter(safra_id=ultima_safra.id)

        # Cards
        try:
            itens_all = PlanejamentoItem.objects.filter(planejamento__in=kpi_qs)
            card_quantidade = itens_all.aggregate(total=Sum('quantidade'))['total'] or Decimal('0')
            card_valor_total = kpi_qs.aggregate(total=Sum('valor_total'))['total'] or Decimal('0')
            card_preco_medio_produto = kpi_qs.aggregate(media=Avg('preco_produto'))['media'] or Decimal('0')
            area_media_rows = (
                itens_all.values('planejamento_id')
                .annotate(area_total=Sum('area_ha'))
            )
            if area_media_rows:
                soma_areas = sum((r.get('area_total') or Decimal('0')) for r in area_media_rows)
                card_media_area_plantada = (Decimal(soma_areas) / Decimal(len(area_media_rows))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            else:
                card_media_area_plantada = Decimal('0')
            # SC/HA do card: media de todos os planejamentos filtrados (incluindo safra selecionada),
            # usando a mesma regra do relatorio (soma de sc/ha por item em cada planejamento).
            pl_rows = list(kpi_qs.values('id', 'preco_produto'))
            preco_map = {r['id']: (r.get('preco_produto') or Decimal('0')) for r in pl_rows}
            ids_all = [r['id'] for r in pl_rows]
            itens_sc_rows_all = (
                PlanejamentoItem.objects.filter(planejamento_id__in=ids_all)
                .values('planejamento_id', 'area_ha', 'total_item')
            )
            q1 = Decimal('0.1')
            sc_total_por_pl = {}
            for rsc in itens_sc_rows_all:
                pid = rsc.get('planejamento_id')
                if not pid:
                    continue
                area = rsc.get('area_ha') or Decimal('0')
                total_item = rsc.get('total_item') or Decimal('0')
                preco_ref = preco_map.get(pid) or Decimal('0')
                if area > 0 and preco_ref > 0:
                    sc_item = (Decimal(total_item) / Decimal(area) / Decimal(preco_ref)).quantize(q1, rounding=ROUND_HALF_UP)
                else:
                    sc_item = Decimal('0')
                sc_total_por_pl[pid] = sc_total_por_pl.get(pid, Decimal('0')) + sc_item

            if sc_total_por_pl:
                card_sacas_ha = (sum(sc_total_por_pl.values()) / Decimal(len(sc_total_por_pl))).quantize(q1, rounding=ROUND_HALF_UP)
            else:
                card_sacas_ha = Decimal('0')
        except Exception:
            card_quantidade = Decimal('0')
            card_valor_total = Decimal('0')
            card_preco_medio_produto = Decimal('0')
            card_sacas_ha = Decimal('0')
            card_media_area_plantada = Decimal('0')

        ctx['card_quantidade'] = card_quantidade
        ctx['card_media_area_plantada'] = card_media_area_plantada
        ctx['card_preco_medio_produto'] = card_preco_medio_produto
        ctx['card_valor_total'] = card_valor_total
        ctx['card_sacas_ha'] = card_sacas_ha

        # Graficos por categoria (usam o mesmo filtro da lista)
        try:
            itens_chart = PlanejamentoItem.objects.filter(planejamento__in=qs)
            valor_rows = (
                itens_chart.values('produto_cadastro__categoria__nome')
                .annotate(total=Sum('total_item'))
                .order_by('-total')
            )
            qtd_rows = (
                itens_chart.values('produto_cadastro__categoria__nome')
                .annotate(total=Sum('quantidade'))
                .order_by('-total')
            )

            valor_chart = []
            qtd_chart = []
            max_valor = Decimal('0')
            max_qtd = Decimal('0')
            total_valor_cat = Decimal('0')
            total_qtd_cat = Decimal('0')

            for row in valor_rows:
                nome = (row.get('produto_cadastro__categoria__nome') or 'Sem categoria').strip()
                total = row.get('total') or Decimal('0')
                valor_chart.append({'categoria': nome, 'valor': total})
                total_valor_cat += Decimal(total)
                if total > max_valor:
                    max_valor = total

            for row in qtd_rows:
                nome = (row.get('produto_cadastro__categoria__nome') or 'Sem categoria').strip()
                total = row.get('total') or Decimal('0')
                qtd_chart.append({'categoria': nome, 'valor': total})
                total_qtd_cat += Decimal(total)
                if total > max_qtd:
                    max_qtd = total

            valor_por_categoria = {item['categoria']: Decimal(item['valor']) for item in valor_chart}

            if max_valor > 0:
                for item in valor_chart:
                    item['pct'] = int(((item['valor'] / max_valor) * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                    if total_valor_cat > 0:
                        item['pct_total'] = ((Decimal(item['valor']) / total_valor_cat) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
                    else:
                        item['pct_total'] = Decimal('0')
            else:
                for item in valor_chart:
                    item['pct'] = 0
                    item['pct_total'] = Decimal('0')

            if max_qtd > 0:
                for item in qtd_chart:
                    item['pct'] = int(((item['valor'] / max_qtd) * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                    if total_qtd_cat > 0:
                        item['pct_total'] = ((Decimal(item['valor']) / total_qtd_cat) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
                    else:
                        item['pct_total'] = Decimal('0')
                    item['valor_total_ref'] = valor_por_categoria.get(item['categoria'], Decimal('0'))
            else:
                for item in qtd_chart:
                    item['pct'] = 0
                    item['pct_total'] = Decimal('0')
                    item['valor_total_ref'] = valor_por_categoria.get(item['categoria'], Decimal('0'))

            ctx['chart_valor_categoria'] = valor_chart[:12]
            ctx['chart_qtd_categoria'] = qtd_chart[:12]

            # Grafico de safra removido por solicitacao do usuario.
            ctx['chart_valor_safra'] = []
        except Exception:
            ctx['chart_valor_categoria'] = []
            ctx['chart_qtd_categoria'] = []
            ctx['chart_valor_safra'] = []

        # Valores por linha (pagina atual)
        try:
            rows = list(ctx.get('object_list') or [])
            ids = [o.pk for o in rows if getattr(o, 'pk', None)]
            itens_rows = (
                PlanejamentoItem.objects.filter(planejamento_id__in=ids)
                .values('planejamento_id')
                .annotate(qtd=Sum('quantidade'), valor=Sum('total_item'), area=Sum('area_ha'))
            )
            itens_map = {r['planejamento_id']: r for r in itens_rows}
            itens_sc_rows = (
                PlanejamentoItem.objects.filter(planejamento_id__in=ids)
                .values('planejamento_id', 'area_ha', 'total_item')
            )
            sc_items_map = {}
            for rsc in itens_sc_rows:
                pid = rsc.get('planejamento_id')
                if not pid:
                    continue
                area = rsc.get('area_ha') or Decimal('0')
                total_item = rsc.get('total_item') or Decimal('0')
                sc_items_map.setdefault(pid, []).append((Decimal(area), Decimal(total_item)))
            for o in rows:
                r = itens_map.get(o.pk, {}) if o.pk else {}
                row_qtd = r.get('qtd') or Decimal('0')
                row_valor = r.get('valor') or Decimal('0')
                row_area = r.get('area') or Decimal('0')
                row_preco_produto = o.preco_produto or Decimal('0')
                if row_area and row_preco_produto:
                    row_sacas_ha = (Decimal(row_valor) / Decimal(row_area)) / Decimal(row_preco_produto)
                else:
                    row_sacas_ha = Decimal('0')
                # Total de sacas da linha deve seguir o relatorio:
                # soma( total_item / area_ha / preco_produto ) por item.
                q1 = Decimal('0.1')
                row_sacas_total = Decimal('0')
                if row_preco_produto and o.pk:
                    for area, total_item in sc_items_map.get(o.pk, []):
                        if area > 0:
                            sc_item = (Decimal(total_item) / Decimal(area) / Decimal(row_preco_produto)).quantize(q1, rounding=ROUND_HALF_UP)
                            row_sacas_total += sc_item
                    row_sacas_total = row_sacas_total.quantize(q1, rounding=ROUND_HALF_UP)
                setattr(o, 'row_qtd', row_qtd)
                setattr(o, 'row_preco_produto', row_preco_produto)
                setattr(o, 'row_sacas_ha', row_sacas_ha)
                setattr(o, 'row_sacas_total', row_sacas_total)

            # Agregado por safra (para linha de grupo da tabela atual)
            safra_grp = {}
            cliente_grp = {}
            for o in rows:
                sid = getattr(o, 'safra_id', None)
                if not sid:
                    continue
                grp = safra_grp.setdefault(
                    sid,
                    {
                        'sum_preco': Decimal('0'),
                        'count_preco': 0,
                        'sum_sacas': Decimal('0'),
                        'count_sacas': 0,
                        'sum_total': Decimal('0'),
                    },
                )
                preco = getattr(o, 'row_preco_produto', Decimal('0')) or Decimal('0')
                sacas = getattr(o, 'row_sacas_total', Decimal('0')) or Decimal('0')
                total = getattr(o, 'valor_total', Decimal('0')) or Decimal('0')
                grp['sum_preco'] += Decimal(preco)
                grp['count_preco'] += 1
                grp['sum_sacas'] += Decimal(sacas)
                grp['count_sacas'] += 1
                grp['sum_total'] += Decimal(total)

                cid = getattr(o, 'cliente_id', None)
                ckey = (sid, cid)
                cgrp = cliente_grp.setdefault(
                    ckey,
                    {
                        'sum_preco': Decimal('0'),
                        'count_preco': 0,
                        'sum_sacas': Decimal('0'),
                        'count_sacas': 0,
                        'sum_total': Decimal('0'),
                    },
                )
                cgrp['sum_preco'] += Decimal(preco)
                cgrp['count_preco'] += 1
                cgrp['sum_sacas'] += Decimal(sacas)
                cgrp['count_sacas'] += 1
                cgrp['sum_total'] += Decimal(total)

            for sid, grp in safra_grp.items():
                if grp['count_preco'] > 0:
                    grp['avg_preco'] = grp['sum_preco'] / Decimal(grp['count_preco'])
                else:
                    grp['avg_preco'] = Decimal('0')
                if grp['count_sacas'] > 0:
                    grp['avg_sacas'] = grp['sum_sacas'] / Decimal(grp['count_sacas'])
                else:
                    grp['avg_sacas'] = Decimal('0')

            for ckey, grp in cliente_grp.items():
                if grp['count_preco'] > 0:
                    grp['avg_preco'] = grp['sum_preco'] / Decimal(grp['count_preco'])
                else:
                    grp['avg_preco'] = Decimal('0')
                if grp['count_sacas'] > 0:
                    grp['avg_sacas'] = grp['sum_sacas'] / Decimal(grp['count_sacas'])
                else:
                    grp['avg_sacas'] = Decimal('0')
            for o in rows:
                sid = getattr(o, 'safra_id', None)
                grp = safra_grp.get(sid, {})
                setattr(o, 'grp_avg_preco', grp.get('avg_preco', Decimal('0')))
                setattr(o, 'grp_avg_sacas', grp.get('avg_sacas', Decimal('0')))
                setattr(o, 'grp_sum_sacas', grp.get('sum_sacas', Decimal('0')))
                setattr(o, 'grp_sum_total', grp.get('sum_total', Decimal('0')))

                cid = getattr(o, 'cliente_id', None)
                cgrp = cliente_grp.get((sid, cid), {})
                setattr(o, 'grp_cliente_avg_preco', cgrp.get('avg_preco', Decimal('0')))
                setattr(o, 'grp_cliente_avg_sacas', cgrp.get('avg_sacas', Decimal('0')))
                setattr(o, 'grp_cliente_sum_sacas', cgrp.get('sum_sacas', Decimal('0')))
                setattr(o, 'grp_cliente_sum_total', cgrp.get('sum_total', Decimal('0')))
        except Exception:
            pass
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

        # Totals for footer row
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
        ctx['total_sc_ha'] = Decimal('0')

        # Per-row SC/HA is derived from totals and header price to be consistent even for legacy rows.
        q1 = Decimal('0.1')
        soma_sacas = Decimal('0')
        for it in itens:
            try:
                area = (it.area_ha or Decimal('0'))
                total_item = (it.total_item or Decimal('0'))
                if area > 0 and pl.preco_produto and pl.preco_produto > 0:
                    sc = (total_item / area / pl.preco_produto)
                else:
                    sc = Decimal('0')
                sc_q = sc.quantize(q1, rounding=ROUND_HALF_UP)
                setattr(it, 'sc_ha_report', sc_q)
                soma_sacas += sc_q
            except Exception:
                setattr(it, 'sc_ha_report', Decimal('0'))
        ctx['total_sc_ha'] = soma_sacas.quantize(q1, rounding=ROUND_HALF_UP)
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

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)

        for col in (self.columns or []):
            if isinstance(col, (list, tuple)) and len(col) >= 2:
                label, field = col[0], col[1]
            else:
                label, field = str(col), str(col)

            params = base_params.copy()
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


class PlanejamentoReportResumidoView(GestorRequiredMixin, ListView):
    model = Planejamento
    template_name = 'core/relatorios/planejamento_resumido.html'
    context_object_name = 'linhas'

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'custo', 'cliente')
            .order_by('safra__safra', 'vencimento')
        )
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'cultura', 'safra', 'categoria', 'cliente', 'custo', 'venc_ini', 'venc_fim'],
        )
        if not filtros_ativos:
            return qs.order_by(*order_by)

        q = _normalize_filter_value(self.request.GET.get('q'))
        cultura_id = _selected_get_value(self.request, 'cultura')
        safra_id = _selected_get_value(self.request, 'safra')
        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        custo_id = _normalize_filter_value(self.request.GET.get('custo'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))

        if q:
            qs = qs.filter(
                Q(cliente__cliente__icontains=q)
                | Q(safra__safra__icontains=q)
                | Q(custo__nome__icontains=q)
            )
        if cultura_id:
            qs = qs.filter(safra__cultura_id=cultura_id)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if categoria_id:
            qs = qs.filter(itens__produto_cadastro__categoria_id=categoria_id).distinct()
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if custo_id:
            qs = qs.filter(custo_id=custo_id)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        return qs.order_by(*order_by)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/planejamento/'
        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        # Estrutura resumida por Safra / Vencimento (modelo da lista)
        q1 = Decimal('0.1')
        grupos = OrderedDict()
        total_geral = Decimal('0')
        total_qtd = 0

        rows = list(self.object_list)
        ids = [o.pk for o in rows if getattr(o, 'pk', None)]
        itens_rows = (
            PlanejamentoItem.objects.filter(planejamento_id__in=ids)
            .values('planejamento_id', 'area_ha', 'total_item')
        )
        itens_map = defaultdict(list)
        for r in itens_rows:
            itens_map[r['planejamento_id']].append(r)

        for pl in rows:
            sc_total = Decimal('0')
            preco_ref = pl.preco_produto or Decimal('0')
            for it in itens_map.get(pl.pk, []):
                area = it.get('area_ha') or Decimal('0')
                total_item = it.get('total_item') or Decimal('0')
                if area > 0 and preco_ref > 0:
                    sc_total += (Decimal(total_item) / Decimal(area) / Decimal(preco_ref)).quantize(q1, rounding=ROUND_HALF_UP)
            sc_total = sc_total.quantize(q1, rounding=ROUND_HALF_UP)
            pl.sc_total_report = sc_total

            sid = pl.safra_id or 0
            grp = grupos.setdefault(
                sid,
                {
                    'safra': pl.safra.safra if pl.safra_id else 'Nao informada',
                    'linhas': [],
                    'sum_total': Decimal('0'),
                    'sum_preco': Decimal('0'),
                    'sum_sacas': Decimal('0'),
                    'count': 0,
                },
            )
            grp['linhas'].append(pl)
            grp['sum_total'] += Decimal(pl.valor_total or 0)
            grp['sum_preco'] += Decimal(pl.preco_produto or 0)
            grp['sum_sacas'] += Decimal(sc_total or 0)
            grp['count'] += 1
            total_geral += Decimal(pl.valor_total or 0)
            total_qtd += 1

        grupos_out = []
        for _, g in grupos.items():
            count = Decimal(g['count'] or 1)
            g['avg_preco'] = (g['sum_preco'] / count) if g['count'] else Decimal('0')
            g['avg_sacas'] = (g['sum_sacas'] / count) if g['count'] else Decimal('0')
            grupos_out.append(g)

        ctx['grupos'] = grupos_out
        ctx['total_geral'] = total_geral
        ctx['total_qtd'] = total_qtd
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

    def _resolve_next_url(self, request):
        raw = (
            request.POST.get('next')
            or request.GET.get('next')
            or str(self.success_url)
        )
        decoded = unquote((raw or '').strip())
        if not decoded:
            return str(self.success_url)
        if url_has_allowed_host_and_scheme(decoded, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return decoded
        if decoded.startswith('/'):
            return decoded
        return str(self.success_url)

    def _resolve_edit_url(self, request, planejamento_id):
        base = reverse('core:planejamento_update', kwargs={'pk': planejamento_id})
        next_url = self._resolve_next_url(request)
        if next_url:
            return f'{base}?next={quote(next_url, safe="")}'
        return base

    def get(self, request, pk=None):
        instance = Planejamento.objects.filter(pk=pk).first() if pk else Planejamento()
        form = PlanejamentoForm(instance=instance)
        formset = self.get_item_formset(instance)
        next_url = self._resolve_next_url(request)
        return render(
            request,
            self.template_name,
            {
                'form': form,
                'formset': formset,
                'titulo': 'Planejamento',
                'modo_edicao': bool(getattr(instance, 'pk', None)),
                'next_url': next_url,
            },
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
            return redirect(self._resolve_edit_url(request, planejamento.pk))

        messages.error(request, 'Nao foi possivel salvar. Verifique os campos.')
        next_url = self._resolve_next_url(request)
        return render(
            request,
            self.template_name,
            {
                'form': form,
                'formset': formset,
                'titulo': 'Planejamento',
                'modo_edicao': bool(getattr(instance, 'pk', None)),
                'next_url': next_url,
            },
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
        return _resolve_per_page(self.request, super().get_paginate_by(queryset))

    def get(self, request, *args, **kwargs):
        restored = _panel_filters_restore_or_save(request, 'pedidos')
        if restored is not None:
            return redirect(f'{request.path}?{restored.urlencode()}')
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        def _normalize_multi_values(values):
            out = []
            seen = set()
            for value in values or []:
                v = _normalize_filter_value(value)
                if not v:
                    continue
                if v in seen:
                    continue
                seen.add(v)
                out.append(v)
            return out

        default_order = [
            'cliente__cliente',
            'safra__safra',
            'produtor__produtor',
            'produtor__fazenda',
            'fornecedor__fornecedor',
            'vencimento',
            'id',
        ]
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'cliente', 'produtor', 'fornecedor')
            .order_by(*default_order)
        )

        topbar = _get_topbar_state(self.request, scope='pedidos', default_cultura=_default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']

        if topbar_cultura:
            qs = qs.filter(safra__cultura_id=topbar_cultura)
        if topbar_safra:
            qs = qs.filter(safra_id=topbar_safra)

        # Padrao: quando abrir a tela, nao "grudar" filtros antigos.
        # Os filtros so entram em vigor quando o usuario clica em "Aplicar filtros" (apply=1).
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'pedido', 'cultura', 'safra', 'categoria', 'cliente', 'custo', 'produtor', 'fornecedor', 'status', 'venc_ini', 'venc_fim', 'produto', 'data_ini', 'data_fim'],
        )
        sort_value = (self.request.GET.get('o') or '').strip()
        sort_map = {
            'status': 'status',
            'data': 'data',
            'pedido': 'pedido',
            'safra': 'safra__safra',
            'vencimento': 'vencimento',
            'produtor': 'produtor__produtor',
            'fornecedor': 'fornecedor__fantasia',
            'valor_total': 'valor_total',
            'a_faturar': 'saldo_faturar',
        }
        order_fields = list(default_order)
        if sort_value:
            desc = sort_value.startswith('-')
            key = sort_value[1:] if desc else sort_value
            field = sort_map.get(key)
            if field:
                order_fields = [f"-{field}" if desc else field, 'id']
        if not filtros_ativos:
            return qs.order_by(*order_fields)

        q = _normalize_filter_value(self.request.GET.get('q'))
        pedido_num = _normalize_filter_value(self.request.GET.get('pedido'))
        cultura_id = _selected_get_value(self.request, 'cultura') or topbar_cultura
        safra_id = _selected_get_value(self.request, 'safra') or topbar_safra
        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        custo_id = _normalize_filter_value(self.request.GET.get('custo'))
        produtor_ids = _normalize_multi_values(self.request.GET.getlist('produtor'))
        if not produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                produtor_ids = [single_produtor]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        produto_id = _normalize_filter_value(self.request.GET.get('produto'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))
        data_ini = _normalize_filter_value(self.request.GET.get('data_ini'))
        data_fim = _normalize_filter_value(self.request.GET.get('data_fim'))

        if q:
            qs = qs.filter(
                Q(pedido__icontains=q)
                | Q(cliente__cliente__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
            )
        if pedido_num:
            qs = qs.filter(pedido__icontains=pedido_num)
        if cultura_id:
            qs = qs.filter(safra__cultura_id=cultura_id)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if categoria_id:
            qs = qs.filter(itens__produto_cadastro__categoria_id=categoria_id).distinct()
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if custo_id:
            qs = qs.filter(custo_id=custo_id)
        if produtor_ids:
            qs = qs.filter(produtor_id__in=produtor_ids)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if produto_id:
            qs = qs.filter(itens__produto_cadastro_id=produto_id).distinct()
        if status:
            qs = qs.filter(status=status)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        if data_ini:
            qs = qs.filter(data__gte=data_ini)
        if data_fim:
            qs = qs.filter(data__lte=data_fim)
        return qs.order_by(*order_fields)


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        def _normalize_multi_values(values):
            out = []
            seen = set()
            for value in values or []:
                v = _normalize_filter_value(value)
                if not v:
                    continue
                if v in seen:
                    continue
                seen.add(v)
                out.append(v)
            return out

        topbar = _get_topbar_state(self.request, scope='pedidos', default_cultura=_default_cultura_soja_id())
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'pedido', 'cultura', 'safra', 'categoria', 'cliente', 'custo', 'produtor', 'fornecedor', 'status', 'venc_ini', 'venc_fim', 'produto', 'data_ini', 'data_fim'],
        )
        qs = self.get_queryset()

        # Cards (padrao igual Faturamento)
        itens_qs = PedidoCompraItem.objects.filter(pedido_compra__in=qs)
        qtd_total = itens_qs.aggregate(total=Sum('quantidade'))['total'] or Decimal('0')
        total_itens = itens_qs.aggregate(total=Sum('total_item'))['total'] or Decimal('0')
        total_pedidos = qs.aggregate(total=Sum('valor_total'))['total'] or Decimal('0')
        card_a_pagar = (
            ContaPagar.objects
            .filter((Q(pedido__in=qs) | Q(faturamento__pedido__in=qs)), pago=False, saldo_aberto__gt=0)
            .aggregate(total=Sum('saldo_aberto'))
            .get('total')
            or Decimal('0')
        )
        total_a_faturar = qs.aggregate(total=Sum('saldo_faturar'))['total'] or Decimal('0')

        try:
            qtd_total = Decimal(qtd_total)
        except Exception:
            qtd_total = Decimal('0')
        try:
            total_itens = Decimal(total_itens)
        except Exception:
            total_itens = Decimal('0')
        try:
            total_pedidos = Decimal(total_pedidos)
        except Exception:
            total_pedidos = Decimal('0')
        try:
            total_a_faturar = Decimal(total_a_faturar)
        except Exception:
            total_a_faturar = Decimal('0')

        preco_medio = Decimal('0')
        if qtd_total > 0:
            try:
                preco_medio = total_itens / qtd_total
            except Exception:
                preco_medio = Decimal('0')

        # Valores por linha (pagina atual)
        try:
            rows = list(ctx.get('object_list') or [])
            ids = [o.pk for o in rows if getattr(o, 'pk', None)]
            itens_rows = (
                PedidoCompraItem.objects.filter(pedido_compra_id__in=ids)
                .values('pedido_compra_id')
                .annotate(qtd=Sum('quantidade'), total=Sum('total_item'))
            )
            itens_map = {r['pedido_compra_id']: r for r in itens_rows}
            itens_nome_map = defaultdict(list)
            fat_qtd_by_prod = {}
            for r in (
                FaturamentoItem.objects.filter(faturamento__pedido_id__in=ids)
                .values('faturamento__pedido_id', 'produto_cadastro_id', 'produto')
                .annotate(qtd=Sum('quantidade'))
            ):
                pid = r['faturamento__pedido_id']
                cad_id = r['produto_cadastro_id']
                if cad_id:
                    key = ('cad', pid, cad_id)
                else:
                    key = ('txt', pid, (r.get('produto') or '').strip().upper())
                fat_qtd_by_prod[key] = (fat_qtd_by_prod.get(key) or Decimal('0')) + (r['qtd'] or Decimal('0'))
            itens_by_pedido = defaultdict(list)
            for it in (
                PedidoCompraItem.objects.filter(pedido_compra_id__in=ids)
                .select_related('produto_cadastro')
                .order_by('id')
            ):
                nome = ''
                if it.produto_cadastro_id:
                    nome = (it.produto_cadastro.nome or '').strip()
                if not nome:
                    nome = (it.produto or '').strip()
                if nome and nome not in itens_nome_map[it.pedido_compra_id]:
                    itens_nome_map[it.pedido_compra_id].append(nome)
                qtd = it.quantidade or Decimal('0')
                total_item = it.total_item or Decimal('0')
                preco = (total_item / qtd) if qtd else Decimal('0')
                if it.produto_cadastro_id:
                    key = ('cad', it.pedido_compra_id, it.produto_cadastro_id)
                else:
                    key = ('txt', it.pedido_compra_id, (it.produto or '').strip().upper())
                qtd_faturada = fat_qtd_by_prod.get(key) or Decimal('0')
                qtd_a_faturar = max(Decimal('0'), qtd - qtd_faturada)
                itens_by_pedido[it.pedido_compra_id].append({
                    'produto': nome or '-',
                    'quantidade': qtd,
                    'preco': preco,
                    'valor': total_item,
                    'a_faturar': qtd_a_faturar,
                })
            for o in rows:
                r = itens_map.get(o.pk, {}) if o.pk else {}
                row_qtd = r.get('qtd') or Decimal('0')
                row_total = r.get('total') or Decimal('0')
                if row_qtd:
                    row_preco = Decimal(row_total) / Decimal(row_qtd)
                else:
                    row_preco = Decimal('0')
                setattr(o, 'row_qtd', row_qtd)
                setattr(o, 'row_preco', row_preco)
                nomes = itens_nome_map.get(o.pk, [])
                if not nomes:
                    row_produto = '-'
                    row_produtos = []
                elif len(nomes) == 1:
                    row_produto = nomes[0]
                    row_produtos = [nomes[0]]
                else:
                    row_produto = f"{nomes[0]} +{len(nomes)-1}"
                    row_produtos = nomes
                setattr(o, 'row_produto', row_produto)
                setattr(o, 'row_produtos', row_produtos)
                setattr(o, 'row_itens', itens_by_pedido.get(o.pk, []))
        except Exception:
            pass

        # Links usados pelo template
        ctx['create_url_name'] = 'core:pedido_create'
        ctx['edit_url_name'] = 'core:pedido_update'
        ctx['delete_url_name'] = 'core:pedido_delete'

        ctx['current_q'] = _normalize_filter_value(self.request.GET.get('q'))
        ctx['current_sort'] = (self.request.GET.get('o') or '').strip()

        # Filtros (igual Faturamento)
        ctx['culturas'] = topbar['culturas']
        ctx['safras'] = topbar['safras']
        ctx['safras_topbar'] = topbar['safras_topbar']
        ctx['categorias'] = Categoria.objects.all().order_by('nome')
        ctx['clientes'] = Cliente.objects.all().order_by('cliente')
        ctx['custos'] = Custo.objects.all().order_by('nome')
        # Mantemos todos os produtores no drawer e filtramos no frontend por cliente.
        # Isso evita lista vazia quando o usuario troca cliente dentro do proprio drawer.
        ctx['produtores'] = Produtor.objects.all().order_by('produtor', 'fazenda')
        ctx['fornecedores'] = Fornecedor.objects.all().order_by('fornecedor')
        ctx['produtos'] = Produto.objects.all().order_by('nome')
        ctx['status_choices'] = StatusPedidoCompra.choices

        ctx['filtro_cultura'] = _selected_get_value(self.request, 'cultura') or topbar['filtro_cultura']
        ctx['filtro_safra'] = _selected_get_value(self.request, 'safra') or topbar['filtro_safra']
        ctx['filtro_categoria'] = _normalize_filter_value(self.request.GET.get('categoria'))
        ctx['filtro_cliente'] = _normalize_filter_value(self.request.GET.get('cliente'))
        ctx['filtro_custo'] = _normalize_filter_value(self.request.GET.get('custo'))
        filtro_produtor_ids = _normalize_multi_values(self.request.GET.getlist('produtor'))
        if not filtro_produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                filtro_produtor_ids = [single_produtor]
        ctx['filtro_produtor_ids'] = filtro_produtor_ids
        ctx['filtro_produtor'] = filtro_produtor_ids[0] if len(filtro_produtor_ids) == 1 else ''
        ctx['filtro_fornecedor'] = _normalize_filter_value(self.request.GET.get('fornecedor'))
        ctx['filtro_produto'] = _normalize_filter_value(self.request.GET.get('produto'))
        ctx['filtro_status'] = _normalize_filter_value(self.request.GET.get('status'))
        ctx['filtro_pedido'] = _normalize_filter_value(self.request.GET.get('pedido'))
        ctx['filtro_venc_ini'] = _normalize_filter_value(self.request.GET.get('venc_ini'))
        ctx['filtro_venc_fim'] = _normalize_filter_value(self.request.GET.get('venc_fim'))
        ctx['filtro_data_ini'] = _normalize_filter_value(self.request.GET.get('data_ini'))
        ctx['filtro_data_fim'] = _normalize_filter_value(self.request.GET.get('data_fim'))
        # Labels resolvidos para exibir "Filtro: valor" no topo da tela
        try:
            ctx['filtro_categoria_label'] = (
                Categoria.objects.filter(pk=ctx['filtro_categoria']).values_list('nome', flat=True).first()
                if ctx['filtro_categoria'] else ''
            ) or ''
            ctx['filtro_cliente_label'] = (
                Cliente.objects.filter(pk=ctx['filtro_cliente']).values_list('cliente', flat=True).first()
                if ctx['filtro_cliente'] else ''
            ) or ''
            produtores_sel = list(
                Produtor.objects.filter(pk__in=filtro_produtor_ids).order_by('produtor', 'fazenda')
            ) if filtro_produtor_ids else []
            ctx['filtro_produtor_labels'] = [str(p) for p in produtores_sel if str(p).strip()]
            ctx['filtro_produtor_label'] = ', '.join(ctx['filtro_produtor_labels'])
            ctx['filtro_fornecedor_label'] = (
                Fornecedor.objects.filter(pk=ctx['filtro_fornecedor']).values_list('fornecedor', flat=True).first()
                if ctx['filtro_fornecedor'] else ''
            ) or ''
            ctx['filtro_produto_label'] = (
                Produto.objects.filter(pk=ctx['filtro_produto']).values_list('nome', flat=True).first()
                if ctx['filtro_produto'] else ''
            ) or ''
        except Exception:
            ctx['filtro_categoria_label'] = ''
            ctx['filtro_cliente_label'] = ''
            ctx['filtro_produtor_labels'] = []
            ctx['filtro_produtor_label'] = ''
            ctx['filtro_fornecedor_label'] = ''
            ctx['filtro_produto_label'] = ''
        try:
            palette = [
                'emerald', 'sky', 'amber', 'violet', 'rose', 'indigo', 'teal', 'orange',
            ]
            safra_group = (
                qs.values('safra__safra')
                .annotate(total=Sum('saldo_faturar'))
                .order_by('-total', 'safra__safra')
            )
            legend = []
            for idx, row in enumerate(safra_group):
                label = row.get('safra__safra') or '-'
                valor = row.get('total') or Decimal('0')
                if valor <= 0:
                    continue
                legend.append(
                    {
                        'label': label,
                        'valor': valor,
                        'tone': palette[idx % len(palette)],
                    }
                )
            ctx['pedido_chart_safra_legend'] = legend
            ctx['pedido_chart_safra_tone_map'] = {str(l.get('label') or ''): (l.get('tone') or 'slate') for l in legend}
        except Exception:
            ctx['pedido_chart_safra_legend'] = []
            ctx['pedido_chart_safra_tone_map'] = {}

        # Para paginacao / relatorios manter filtros sem page
        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['pagination_query'] = params.urlencode()
        ctx['report_query'] = params.urlencode()

        try:
            ctx['total_registros'] = self.get_queryset().count()
        except Exception:
            ctx['total_registros'] = 0

        # Totais por grupo (Safra/Cliente/Produtor) pelos itens filtrados, para
        # manter coerencia com as linhas exibidas na lista.
        try:
            base_itens = PedidoCompraItem.objects.filter(pedido_compra__in=qs)
            safra_totais_map = {
                (r.get('pedido_compra__safra_id') or 0): (r.get('total') or Decimal('0'))
                for r in base_itens.values('pedido_compra__safra_id').annotate(total=Sum('total_item'))
            }
            cliente_totais_map = {
                (
                    (r.get('pedido_compra__safra_id') or 0),
                    (r.get('pedido_compra__cliente_id') or 0),
                ): (r.get('total') or Decimal('0'))
                for r in base_itens.values(
                    'pedido_compra__safra_id',
                    'pedido_compra__cliente_id',
                ).annotate(total=Sum('total_item'))
            }
            produtor_totais_map = {
                (
                    (r.get('pedido_compra__safra_id') or 0),
                    (r.get('pedido_compra__cliente_id') or 0),
                    (r.get('pedido_compra__produtor_id') or 0),
                ): (r.get('total') or Decimal('0'))
                for r in base_itens.values(
                    'pedido_compra__safra_id',
                    'pedido_compra__cliente_id',
                    'pedido_compra__produtor_id',
                ).annotate(total=Sum('total_item'))
            }
            fornecedor_totais_map = {
                (
                    (r.get('pedido_compra__safra_id') or 0),
                    (r.get('pedido_compra__cliente_id') or 0),
                    (r.get('pedido_compra__produtor_id') or 0),
                    (r.get('pedido_compra__fornecedor_id') or 0),
                ): (r.get('total') or Decimal('0'))
                for r in base_itens.values(
                    'pedido_compra__safra_id',
                    'pedido_compra__cliente_id',
                    'pedido_compra__produtor_id',
                    'pedido_compra__fornecedor_id',
                ).annotate(total=Sum('total_item'))
            }
            for o in list(ctx.get('object_list') or []):
                sid = getattr(o, 'safra_id', None) or 0
                cid = getattr(o, 'cliente_id', None) or 0
                pid = getattr(o, 'produtor_id', None) or 0
                fid = getattr(o, 'fornecedor_id', None) or 0
                setattr(o, 'grupo_safra_total', safra_totais_map.get(sid, Decimal('0')))
                setattr(o, 'grupo_cliente_total', cliente_totais_map.get((sid, cid), Decimal('0')))
                setattr(o, 'grupo_produtor_total', produtor_totais_map.get((sid, cid, pid), Decimal('0')))
                setattr(o, 'grupo_fornecedor_total', fornecedor_totais_map.get((sid, cid, pid, fid), Decimal('0')))
        except Exception:
            pass

        ctx['card_pedidos'] = qs.count()
        ctx['card_quantidade'] = qtd_total
        ctx['card_preco_medio'] = preco_medio
        ctx['card_a_pagar'] = card_a_pagar
        ctx['card_total_pedidos'] = total_pedidos
        card_valor_faturado = total_pedidos - total_a_faturar
        if card_valor_faturado < 0:
            card_valor_faturado = Decimal('0')
        ctx['card_valor_faturado'] = card_valor_faturado
        ctx['card_valor_a_faturar'] = total_a_faturar
        try:
            cultura_label = 'Todas'
            fc = ctx.get('filtro_cultura')
            if fc:
                cultura_obj = Cultura.objects.filter(pk=fc).only('cultura').first()
                if cultura_obj and cultura_obj.cultura:
                    cultura_label = cultura_obj.cultura
            pedido_safra_rows_qs = (
                PedidoCompraItem.objects.filter(pedido_compra__in=qs)
                .values('pedido_compra__safra__safra')
                .annotate(total=Sum('total_item'))
                .order_by('pedido_compra__safra__safra')
            )
            pedido_safra_rows = []
            max_safra_v = Decimal('0')
            for r in pedido_safra_rows_qs:
                label = (r.get('pedido_compra__safra__safra') or 'Sem safra').strip() or 'Sem safra'
                valor = r.get('total') or Decimal('0')
                if valor <= 0:
                    continue
                pedido_safra_rows.append({'label': label, 'valor': valor})
                if valor > max_safra_v:
                    max_safra_v = valor

            # Fallback: quando itens nao retornarem (ex.: dados antigos/importados incompletos),
            # usa total dos pedidos filtrados por safra.
            if not pedido_safra_rows:
                pedido_safra_rows_qs = (
                    qs.values('safra__safra')
                    .annotate(total=Sum('saldo_faturar'))
                    .order_by('safra__safra')
                )
                for r in pedido_safra_rows_qs:
                    label = (r.get('safra__safra') or 'Sem safra').strip() or 'Sem safra'
                    valor = r.get('total') or Decimal('0')
                    if valor <= 0:
                        continue
                    pedido_safra_rows.append({'label': label, 'valor': valor})
                    if valor > max_safra_v:
                        max_safra_v = valor
            for r in pedido_safra_rows:
                r['pct'] = int((r['valor'] * Decimal('100') / max_safra_v).quantize(Decimal('1'))) if max_safra_v > 0 else 0

            # Serie para grafico de area (SVG)
            area_chart = None
            if pedido_safra_rows:
                chart_w = 1200
                chart_h = 320
                pad_l = 64
                pad_r = 28
                pad_t = 18
                pad_b = 48
                plot_w = chart_w - pad_l - pad_r
                plot_h = chart_h - pad_t - pad_b
                n = len(pedido_safra_rows)
                max_v = max((r['valor'] for r in pedido_safra_rows), default=Decimal('0'))
                if max_v <= 0:
                    max_v = Decimal('1')

                def _x(i: int) -> float:
                    if n <= 1:
                        return float(pad_l + (plot_w / 2))
                    return float(pad_l + (plot_w * i / (n - 1)))

                def _y(v: Decimal) -> float:
                    ratio = float(v / max_v) if max_v > 0 else 0.0
                    return float(pad_t + (plot_h * (1 - ratio)))

                pts = []
                for i, row in enumerate(pedido_safra_rows):
                    x = _x(i)
                    y = _y(row['valor'])
                    pts.append(
                        {
                            'x': round(x, 2),
                            'y': round(y, 2),
                            'label': row['label'],
                            'valor': row['valor'],
                        }
                    )
                polyline = " ".join([f"{p['x']},{p['y']}" for p in pts])
                area_path = (
                    f"M {pts[0]['x']} {pad_t + plot_h} "
                    + " ".join([f"L {p['x']} {p['y']}" for p in pts])
                    + f" L {pts[-1]['x']} {pad_t + plot_h} Z"
                )
                grid_lines = []
                for i in range(1, 5):
                    gy = pad_t + (plot_h * i / 5)
                    grid_lines.append(round(float(gy), 2))
                area_chart = {
                    'w': chart_w,
                    'h': chart_h,
                    'base_y': round(float(pad_t + plot_h), 2),
                    'points': pts,
                    'polyline': polyline,
                    'area_path': area_path,
                    'grid_lines': grid_lines,
                }
            ctx['pedido_chart_safra'] = pedido_safra_rows
            ctx['pedido_chart_safra_cultura'] = cultura_label
            ctx['pedido_chart_safra_area'] = area_chart
        except Exception:
            ctx['pedido_chart_safra'] = []
            ctx['pedido_chart_safra_cultura'] = 'Todas'
            ctx['pedido_chart_safra_area'] = None

        # Graficos "A Faturar" (Quantidade ou Valor), por Categoria / Produto / Produtor
        chart_mode = (self.request.GET.get('chart_mode') or 'valor').strip().lower()
        if chart_mode not in {'valor', 'quantidade'}:
            chart_mode = 'valor'
        ctx['chart_mode'] = chart_mode

        pedido_ids = list(qs.values_list('id', flat=True))
        chart_categoria = defaultdict(lambda: Decimal('0'))
        chart_produtor = defaultdict(lambda: Decimal('0'))
        chart_fornecedor = defaultdict(lambda: Decimal('0'))
        chart_produto = defaultdict(lambda: Decimal('0'))
        chart_categoria_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        chart_produtor_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        chart_fornecedor_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        chart_produto_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))

        def _produtor_label(produtor_obj):
            if not produtor_obj:
                return 'Sem produtor'
            nome = (getattr(produtor_obj, 'apelido', '') or getattr(produtor_obj, 'produtor', '') or '').strip()
            fazenda = (getattr(produtor_obj, 'fazenda', '') or '').strip()
            if nome and fazenda:
                return f"{nome} - {fazenda}"
            return nome or 'Sem produtor'

        if pedido_ids:
            # Saldo de quantidade por pedido (itens - faturado)
            item_qtd_by_pedido = {
                r['pedido_compra_id']: (r['qtd'] or Decimal('0'))
                for r in PedidoCompraItem.objects.filter(pedido_compra_id__in=pedido_ids)
                .values('pedido_compra_id')
                .annotate(qtd=Sum('quantidade'))
            }
            fat_qtd_by_pedido = {
                r['faturamento__pedido_id']: (r['qtd'] or Decimal('0'))
                for r in FaturamentoItem.objects.filter(faturamento__pedido_id__in=pedido_ids)
                .values('faturamento__pedido_id')
                .annotate(qtd=Sum('quantidade'))
            }
            saldo_qtd_by_pedido = {}
            for pid in pedido_ids:
                saldo_qtd_by_pedido[pid] = max(
                    Decimal('0'),
                    (item_qtd_by_pedido.get(pid) or Decimal('0')) - (fat_qtd_by_pedido.get(pid) or Decimal('0')),
                )

            # Categoria: saldo por item/produto (quantidade ou valor)
            fat_qtd_by_prod = {
                (r['faturamento__pedido_id'], r['produto_cadastro_id']): (r['qtd'] or Decimal('0'))
                for r in FaturamentoItem.objects.filter(
                    faturamento__pedido_id__in=pedido_ids,
                    produto_cadastro_id__isnull=False,
                )
                .values('faturamento__pedido_id', 'produto_cadastro_id')
                .annotate(qtd=Sum('quantidade'))
            }
            itens_cat_qs = (
                PedidoCompraItem.objects.filter(pedido_compra_id__in=pedido_ids)
                .select_related('produto_cadastro__categoria', 'pedido_compra__safra')
            )
            for it in itens_cat_qs:
                safra_nome = 'Sem safra'
                if getattr(it, 'pedido_compra', None) and getattr(it.pedido_compra, 'safra', None):
                    safra_nome = it.pedido_compra.safra.safra
                cat_nome = 'Sem categoria'
                if it.produto_cadastro_id and getattr(it.produto_cadastro, 'categoria', None):
                    cat_nome = it.produto_cadastro.categoria.nome
                prod_nome = ''
                if it.produto_cadastro_id:
                    prod_nome = (it.produto_cadastro.nome or '').strip()
                if not prod_nome:
                    prod_nome = (it.produto or '').strip() or 'Sem produto'

                saldo_qtd_item = max(
                    Decimal('0'),
                    (it.quantidade or Decimal('0')) - (fat_qtd_by_prod.get((it.pedido_compra_id, it.produto_cadastro_id)) or Decimal('0')),
                )
                if saldo_qtd_item <= 0:
                    continue

                if chart_mode == 'quantidade':
                    chart_categoria[cat_nome] += saldo_qtd_item
                    chart_produto[prod_nome] += saldo_qtd_item
                    chart_categoria_safra[cat_nome][safra_nome] += saldo_qtd_item
                    chart_produto_safra[prod_nome][safra_nome] += saldo_qtd_item
                else:
                    unit_value = Decimal('0')
                    if (it.quantidade or Decimal('0')) > 0:
                        try:
                            unit_value = (it.total_item or Decimal('0')) / (it.quantidade or Decimal('1'))
                        except Exception:
                            unit_value = Decimal('0')
                    saldo_valor_item = (saldo_qtd_item * unit_value)
                    chart_categoria[cat_nome] += saldo_valor_item
                    chart_produto[prod_nome] += saldo_valor_item
                    chart_categoria_safra[cat_nome][safra_nome] += saldo_valor_item
                    chart_produto_safra[prod_nome][safra_nome] += saldo_valor_item

            # Produtor por pedido
            for p in qs:
                safra_nome = 'Sem safra'
                if getattr(p, 'safra', None):
                    safra_nome = p.safra.safra
                prod_nome = _produtor_label(getattr(p, 'produtor', None))
                forn_nome = str(p.fornecedor) if p.fornecedor_id else 'Sem fornecedor'
                if chart_mode == 'quantidade':
                    v = saldo_qtd_by_pedido.get(p.id) or Decimal('0')
                else:
                    v = p.saldo_faturar or Decimal('0')
                chart_produtor[prod_nome] += v
                chart_fornecedor[forn_nome] += v
                chart_produtor_safra[prod_nome][safra_nome] += v
                chart_fornecedor_safra[forn_nome][safra_nome] += v

        def _to_rows(dct):
            rows = [{'label': k, 'valor': (v or Decimal('0'))} for k, v in dct.items() if (v or Decimal('0')) > 0]
            rows.sort(key=lambda x: x['valor'], reverse=True)
            rows = rows[:6]
            max_v = max([r['valor'] for r in rows], default=Decimal('0'))
            for r in rows:
                r['pct'] = int((r['valor'] * Decimal('100') / max_v).quantize(Decimal('1'))) if max_v > 0 else 0
            return rows

        rows_categoria = _to_rows(chart_categoria)
        rows_produtor = _to_rows(chart_produtor)
        rows_fornecedor = _to_rows(chart_fornecedor)
        rows_produto = _to_rows(chart_produto)

        # Fallback: se nao houver "a faturar", mostra os mesmos graficos como "faturado"
        if not (rows_categoria or rows_produtor or rows_produto):
            fat_categoria = defaultdict(lambda: Decimal('0'))
            fat_produtor = defaultdict(lambda: Decimal('0'))
            fat_fornecedor = defaultdict(lambda: Decimal('0'))
            fat_produto = defaultdict(lambda: Decimal('0'))

            if pedido_ids:
                fat_items = (
                    FaturamentoItem.objects.filter(faturamento__pedido_id__in=pedido_ids)
                    .select_related(
                        'produto_cadastro__categoria',
                        'faturamento__pedido__fornecedor',
                        'faturamento__pedido__produtor',
                        'faturamento__pedido__safra',
                    )
                )
                for it in fat_items:
                    cat_nome = 'Sem categoria'
                    if it.produto_cadastro_id and getattr(it.produto_cadastro, 'categoria', None):
                        cat_nome = it.produto_cadastro.categoria.nome
                    prod_nome = ''
                    if it.produto_cadastro_id:
                        prod_nome = (it.produto_cadastro.nome or '').strip()
                    if not prod_nome:
                        prod_nome = (it.produto or '').strip() or 'Sem produto'
                    produtor_nome = 'Sem produtor'
                    fornecedor_nome = 'Sem fornecedor'
                    if getattr(it.faturamento, 'pedido', None):
                        p = it.faturamento.pedido
                        safra_nome = p.safra.safra if getattr(p, 'safra', None) else 'Sem safra'
                        if getattr(p, 'produtor', None):
                            produtor_nome = p.produtor.produtor
                        if getattr(p, 'fornecedor', None):
                            fornecedor_nome = str(p.fornecedor)
                    else:
                        safra_nome = 'Sem safra'

                    if chart_mode == 'quantidade':
                        v = it.quantidade or Decimal('0')
                    else:
                        v = it.total_item or Decimal('0')
                    if v <= 0:
                        continue
                    fat_categoria[cat_nome] += v
                    fat_produtor[produtor_nome] += v
                    fat_fornecedor[fornecedor_nome] += v
                    fat_produto[prod_nome] += v
                    chart_categoria_safra[cat_nome][safra_nome] += v
                    chart_produtor_safra[produtor_nome][safra_nome] += v
                    chart_fornecedor_safra[fornecedor_nome][safra_nome] += v
                    chart_produto_safra[prod_nome][safra_nome] += v

            rows_categoria = _to_rows(fat_categoria)
            rows_produtor = _to_rows(fat_produtor)
            rows_fornecedor = _to_rows(fat_fornecedor)
            rows_produto = _to_rows(fat_produto)
            ctx['pedido_chart_base_label'] = 'Faturado'
        else:
            ctx['pedido_chart_base_label'] = 'A Faturar'

        ctx['pedido_chart_categoria'] = rows_categoria
        ctx['pedido_chart_produtor'] = rows_produtor
        ctx['pedido_chart_fornecedor'] = rows_fornecedor
        ctx['pedido_chart_produto'] = rows_produto

        # Novos graficos triplos (Valor Pedido, Valor Faturado, Valor Faturar)
        try:
            cat_pedido = defaultdict(lambda: Decimal('0'))
            cat_faturado = defaultdict(lambda: Decimal('0'))
            cat_faturar = defaultdict(lambda: Decimal('0'))
            prod_pedido = defaultdict(lambda: Decimal('0'))
            prod_faturado = defaultdict(lambda: Decimal('0'))
            prod_faturar = defaultdict(lambda: Decimal('0'))
            produtor_pedido = defaultdict(lambda: Decimal('0'))
            produtor_faturado = defaultdict(lambda: Decimal('0'))
            produtor_faturar = defaultdict(lambda: Decimal('0'))

            if pedido_ids:
                pedido_itens = (
                    PedidoCompraItem.objects.filter(pedido_compra_id__in=pedido_ids)
                    .select_related('produto_cadastro__categoria', 'pedido_compra__produtor')
                )
                for it in pedido_itens:
                    cat_nome = 'Sem categoria'
                    if it.produto_cadastro_id and getattr(it.produto_cadastro, 'categoria', None):
                        cat_nome = it.produto_cadastro.categoria.nome
                    prod_nome = ''
                    if it.produto_cadastro_id:
                        prod_nome = (it.produto_cadastro.nome or '').strip()
                    if not prod_nome:
                        prod_nome = (it.produto or '').strip() or 'Sem produto'
                    valor = it.total_item or Decimal('0')
                    if valor <= 0:
                        continue
                    cat_pedido[cat_nome] += valor
                    prod_pedido[prod_nome] += valor
                    produtor_nome = 'Sem produtor'
                    try:
                        if it.pedido_compra_id:
                            pcomp = getattr(it, 'pedido_compra', None)
                        if pcomp and getattr(pcomp, 'produtor', None):
                            produtor_nome = _produtor_label(pcomp.produtor)
                    except Exception:
                        pass
                    produtor_pedido[produtor_nome] += valor

                    # "A faturar/Faturado" por dimensão segue o saldo real do pedido.
                    # Faz rateio proporcional do saldo do pedido entre os itens para evitar
                    # distorções quando categoria do faturamento difere da categoria do pedido.
                    pend_valor = Decimal('0')
                    try:
                        pedido_ref = getattr(it, 'pedido_compra', None)
                        if pedido_ref:
                            pedido_total = Decimal(getattr(pedido_ref, 'valor_total', 0) or 0)
                            pedido_saldo = Decimal(getattr(pedido_ref, 'saldo_faturar', 0) or 0)
                            if pedido_total > 0 and pedido_saldo > 0:
                                if pedido_saldo >= pedido_total:
                                    pend_valor = valor
                                else:
                                    pend_valor = (valor * pedido_saldo / pedido_total)
                    except Exception:
                        pend_valor = Decimal('0')
                    if pend_valor < 0:
                        pend_valor = Decimal('0')
                    fat_valor = valor - pend_valor
                    if fat_valor < 0:
                        fat_valor = Decimal('0')

                    cat_faturado[cat_nome] += fat_valor
                    prod_faturado[prod_nome] += fat_valor
                    produtor_faturado[produtor_nome] += fat_valor

                    if pend_valor > 0:
                        cat_faturar[cat_nome] += pend_valor
                        prod_faturar[prod_nome] += pend_valor
                        produtor_faturar[produtor_nome] += pend_valor

            def _build_triplo_rows(base_pedido, base_faturado, base_faturar=None):
                labels = set(list(base_pedido.keys()) + list(base_faturado.keys()))
                if base_faturar:
                    labels.update(list(base_faturar.keys()))
                labels = sorted(labels)
                rows = []
                for lbl in labels:
                    vp = base_pedido.get(lbl) or Decimal('0')
                    vf = base_faturado.get(lbl) or Decimal('0')
                    if base_faturar is not None:
                        vr = base_faturar.get(lbl) or Decimal('0')
                    else:
                        vr = vp - vf
                        if vr < 0:
                            vr = Decimal('0')
                    if (vp <= 0) and (vf <= 0) and (vr <= 0):
                        continue
                    rows.append({
                        'label': lbl,
                        'valor_pedido': vp,
                        'valor_faturado': vf,
                        'valor_faturar': vr,
                    })
                rows.sort(key=lambda x: x['valor_pedido'], reverse=True)
                max_v = max(
                    [r['valor_pedido'] for r in rows] + [r['valor_faturado'] for r in rows] + [r['valor_faturar'] for r in rows],
                    default=Decimal('0'),
                )
                for r in rows:
                    r['pct_pedido'] = int((r['valor_pedido'] * Decimal('100') / max_v).quantize(Decimal('1'))) if max_v > 0 else 0
                    r['pct_faturado'] = int((r['valor_faturado'] * Decimal('100') / max_v).quantize(Decimal('1'))) if max_v > 0 else 0
                    r['pct_faturar'] = int((r['valor_faturar'] * Decimal('100') / max_v).quantize(Decimal('1'))) if max_v > 0 else 0
                return rows

            ctx['pedido_chart_categoria_triplo'] = _build_triplo_rows(cat_pedido, cat_faturado, cat_faturar)
            ctx['pedido_chart_produto_triplo'] = _build_triplo_rows(prod_pedido, prod_faturado, prod_faturar)
            ctx['pedido_chart_produtor_triplo'] = _build_triplo_rows(produtor_pedido, produtor_faturado, produtor_faturar)
        except Exception:
            ctx['pedido_chart_categoria_triplo'] = []
            ctx['pedido_chart_produto_triplo'] = []
            ctx['pedido_chart_produtor_triplo'] = []

        def _matrix_from_safra(dim_map):
            tone_map = ctx.get('pedido_chart_safra_tone_map') or {}
            safra_order = []
            try:
                for s in qs.values_list('safra__safra', flat=True).distinct():
                    if s and s not in safra_order:
                        safra_order.append(s)
            except Exception:
                pass
            if not safra_order:
                safra_order = sorted(
                    {sk for v in dim_map.values() for sk in v.keys() if sk},
                    key=lambda x: x or '',
                )
            rows = []
            for label, sval in dim_map.items():
                total = sum((sval.get(s, Decimal('0')) for s in safra_order), Decimal('0'))
                if total <= 0:
                    continue
                values = [sval.get(s, Decimal('0')) for s in safra_order]
                rows.append({'label': label, 'values': values, 'total': total})
            rows.sort(key=lambda x: x['total'], reverse=True)
            rows = rows[:12]
            col_totals = []
            for i, _s in enumerate(safra_order):
                col_totals.append(sum((r['values'][i] for r in rows), Decimal('0')))
            # Exibe somente safras que realmente tenham dados no resultado atual
            keep_idx = [i for i, t in enumerate(col_totals) if (t or Decimal('0')) > 0]
            if keep_idx:
                safra_order = [safra_order[i] for i in keep_idx]
                col_totals = [col_totals[i] for i in keep_idx]
                for r in rows:
                    r['values'] = [r['values'][i] for i in keep_idx]
            grand_total = sum(col_totals, Decimal('0'))
            max_cell = max(col_totals + [v for r in rows for v in r['values']], default=Decimal('0'))
            for r in rows:
                heat = []
                for v in r['values']:
                    pct = int((v * Decimal('100') / max_cell).quantize(Decimal('1'))) if max_cell > 0 else 0
                    heat.append(pct)
                r['heat'] = heat
            return {
                'safras': safra_order,
                'rows': rows,
                'totais': col_totals,
                'grand_total': grand_total,
                'tones': [tone_map.get(str(s), 'slate') for s in safra_order],
            }

        show_multi = not bool(ctx.get('filtro_safra'))
        ctx['pedido_chart_multi_safra'] = show_multi
        if show_multi:
            ctx['pedido_chart_categoria_matrix'] = _matrix_from_safra(chart_categoria_safra)
            ctx['pedido_chart_produto_matrix'] = _matrix_from_safra(chart_produto_safra)
            ctx['pedido_chart_produtor_matrix'] = _matrix_from_safra(chart_produtor_safra)
            ctx['pedido_chart_fornecedor_matrix'] = _matrix_from_safra(chart_fornecedor_safra)

        # Subtitulo com resumo dos filtros ativos
        filtros_resumo = []
        try:
            if ctx.get('filtro_cliente'):
                c = Cliente.objects.filter(pk=ctx['filtro_cliente']).only('cliente').first()
                filtros_resumo.append(f"Cliente: {c.cliente if c else ctx['filtro_cliente']}")
            if ctx.get('filtro_produtor'):
                p = Produtor.objects.filter(pk=ctx['filtro_produtor']).only('produtor', 'fazenda').first()
                if p:
                    nome = (p.apelido or p.produtor) + (f" - {p.fazenda}" if p.fazenda else '')
                else:
                    nome = ctx['filtro_produtor']
                filtros_resumo.append(f"Produtor: {nome}")
            if ctx.get('filtro_fornecedor'):
                f = Fornecedor.objects.filter(pk=ctx['filtro_fornecedor']).only('fornecedor').first()
                filtros_resumo.append(f"Fornecedor: {str(f) if f else ctx['filtro_fornecedor']}")
            if ctx.get('filtro_pedido'):
                filtros_resumo.append(f"Pedido: {ctx['filtro_pedido']}")
            if ctx.get('filtro_venc_ini') or ctx.get('filtro_venc_fim'):
                de = ctx.get('filtro_venc_ini') or '-'
                ate = ctx.get('filtro_venc_fim') or '-'
                filtros_resumo.append(f"Periodo Vencimento: {de} ate {ate}")
        except Exception:
            pass
        ctx['filtros_resumo'] = filtros_resumo

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
        group_order = [
            'safra__safra',
            'cliente__cliente',
            'produtor__produtor',
            'produtor__fazenda',
            'fornecedor__fornecedor',
            '-data',
            '-id',
        ]
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
        topbar = _get_topbar_state(self.request, scope='pedidos', default_cultura=_default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']

        if topbar_cultura:
            qs = qs.filter(safra__cultura_id=topbar_cultura)
        if topbar_safra:
            qs = qs.filter(safra_id=topbar_safra)

        q = _normalize_filter_value(self.request.GET.get('q'))
        pedido_num = _normalize_filter_value(self.request.GET.get('pedido'))
        cultura_id = _selected_get_value(self.request, 'cultura') or topbar_cultura
        safra_id = _selected_get_value(self.request, 'safra') or topbar_safra
        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        custo_id = _normalize_filter_value(self.request.GET.get('custo'))
        produtor_ids = [v for v in (_normalize_filter_value(x) for x in self.request.GET.getlist('produtor')) if v]
        if not produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                produtor_ids = [single_produtor]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))
        if q:
            qs = qs.filter(
                Q(pedido__icontains=q)
                | Q(cliente__cliente__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
            )
        if pedido_num:
            qs = qs.filter(pedido__icontains=pedido_num)
        if cultura_id:
            qs = qs.filter(safra__cultura_id=cultura_id)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if categoria_id:
            qs = qs.filter(itens__produto_cadastro__categoria_id=categoria_id).distinct()
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if custo_id:
            qs = qs.filter(custo_id=custo_id)
        if produtor_ids:
            qs = qs.filter(produtor_id__in=produtor_ids)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if status:
            if status == 'VENCIDO':
                qs = qs.filter(
                    conta_pagar__vencimento__lt=date.today(),
                    conta_pagar__pago=False,
                    conta_pagar__saldo_aberto__gt=0,
                )
            else:
                qs = qs.filter(status=status)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        return qs.order_by(*group_order)

    @staticmethod
    def _produtor_label(produtor: Produtor | None) -> str:
        if not produtor:
            return '-'
        try:
            base = (produtor.apelido or produtor.produtor)
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
                forn_lbl = str(p.fornecedor) if p.fornecedor else '-'
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
                .order_by('produtor__produtor', 'fornecedor__fornecedor', 'vencimento', 'data', 'nota_fiscal', 'id')
            )

            prod_map = OrderedDict()
            for nf in faturamentos:
                prod_lbl = self._produtor_label(nf.produtor)
                if prod_lbl not in prod_map:
                    prod_map[prod_lbl] = {'produtor_label': prod_lbl, 'total': Decimal('0'), 'fornecedores': OrderedDict()}

                pnode = prod_map[prod_lbl]
                forn_lbl = '-'
                try:
                    forn_lbl = str(nf.fornecedor) if nf.fornecedor else '-'
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
                            'vencimento': nf.vencimento,
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

        # Matriz de cabecalho: Cliente -> (Safras em colunas) x (Produtores em linhas)
        try:
            matrix_by_cliente = OrderedDict()
            for p in pedidos:
                cliente_lbl = self._cliente_label(p.cliente)
                safra_lbl = self._safra_label(p.safra)
                produtor_lbl = self._produtor_label(p.produtor)
                valor = p.valor_total or Decimal('0')

                if cliente_lbl not in matrix_by_cliente:
                    matrix_by_cliente[cliente_lbl] = {
                        'cliente_label': cliente_lbl,
                        'total': Decimal('0'),
                        'safras_set': set(),
                        'prod_map': OrderedDict(),
                    }
                cnode = matrix_by_cliente[cliente_lbl]
                cnode['total'] += valor
                cnode['safras_set'].add(safra_lbl)

                if produtor_lbl not in cnode['prod_map']:
                    cnode['prod_map'][produtor_lbl] = defaultdict(lambda: Decimal('0'))
                cnode['prod_map'][produtor_lbl][safra_lbl] += valor

            header_matrix = []
            for cnode in matrix_by_cliente.values():
                safras_cols = sorted(list(cnode['safras_set']))
                rows = []
                for produtor_lbl, safra_vals in cnode['prod_map'].items():
                    vals = [safra_vals.get(s, Decimal('0')) for s in safras_cols]
                    if all(v == 0 for v in vals):
                        continue
                    rows.append({'produtor_label': produtor_lbl, 'values': vals})
                total_cols = [sum((r['values'][i] for r in rows), Decimal('0')) for i in range(len(safras_cols))]
                header_matrix.append(
                    {
                        'cliente_label': cnode['cliente_label'],
                        'total': cnode['total'],
                        'safras_cols': safras_cols,
                        'rows': rows,
                        'totals': total_cols,
                    }
                )
            ctx['header_matrix'] = header_matrix
        except Exception:
            ctx['header_matrix'] = []

        # Novo grupo analitico: Safra > Produtor > Fornecedor > Vencimento > Linhas
        try:
            pedido_ids = [p.pk for p in pedidos if getattr(p, 'pk', None)]
            nfs = (
                Faturamento.objects.filter(pedido_id__in=pedido_ids)
                .select_related('safra', 'produtor', 'fornecedor', 'pedido', 'conta_pagar')
                .prefetch_related('itens__produto_cadastro__categoria')
                .order_by('safra__safra', 'produtor__produtor', 'fornecedor__fornecedor', 'vencimento', 'data', 'id')
            )

            root = OrderedDict()
            for nf in nfs:
                safra_lbl = self._safra_label(nf.safra or getattr(nf.pedido, 'safra', None))
                produtor_lbl = self._produtor_label(nf.produtor or getattr(nf.pedido, 'produtor', None))

                if nf.fornecedor_id:
                    try:
                        forn_lbl = (nf.fornecedor.fantasia or '').strip() or (nf.fornecedor.fornecedor or '-')
                    except Exception:
                        forn_lbl = str(nf.fornecedor)
                else:
                    forn_lbl = '-'

                venc = nf.vencimento
                venc_lbl = venc.strftime('%d/%m/%Y') if venc else '-'
                total_nf = nf.valor_total or Decimal('0')
                saldo_nf = Decimal('0')
                try:
                    if getattr(nf, 'conta_pagar', None):
                        saldo_nf = nf.conta_pagar.saldo_aberto or Decimal('0')
                except Exception:
                    saldo_nf = Decimal('0')

                cat_names = []
                for it in nf.itens.all():
                    try:
                        cat = getattr(getattr(it, 'produto_cadastro', None), 'categoria', None)
                        nome_cat = (cat.nome if cat else '').strip()
                    except Exception:
                        nome_cat = ''
                    if nome_cat and nome_cat not in cat_names:
                        cat_names.append(nome_cat)
                categoria_lbl = ', '.join(cat_names) if cat_names else '-'

                snode = root.setdefault(safra_lbl, {'label': safra_lbl, 'total': Decimal('0'), 'saldo': Decimal('0'), 'produtores': OrderedDict()})
                pnode = snode['produtores'].setdefault(produtor_lbl, {'label': produtor_lbl, 'total': Decimal('0'), 'saldo': Decimal('0'), 'fornecedores': OrderedDict()})
                fnode = pnode['fornecedores'].setdefault(forn_lbl, {'label': forn_lbl, 'total': Decimal('0'), 'saldo': Decimal('0'), 'vencimentos': OrderedDict()})
                vnode = fnode['vencimentos'].setdefault(venc_lbl, {'label': venc_lbl, 'total': Decimal('0'), 'saldo': Decimal('0'), 'linhas': []})

                line = {
                    'data': nf.data,
                    'nota_fiscal': nf.nota_fiscal,
                    'serie': nf.serie,
                    'pedido': getattr(nf.pedido, 'pedido', ''),
                    'categoria': categoria_lbl,
                    'total': total_nf,
                    'saldo': saldo_nf,
                }
                vnode['linhas'].append(line)
                vnode['total'] += total_nf
                vnode['saldo'] += saldo_nf
                fnode['total'] += total_nf
                fnode['saldo'] += saldo_nf
                pnode['total'] += total_nf
                pnode['saldo'] += saldo_nf
                snode['total'] += total_nf
                snode['saldo'] += saldo_nf

            grouped = []
            for sn in root.values():
                produtores = []
                for pn in sn['produtores'].values():
                    fornecedores = []
                    for fn in pn['fornecedores'].values():
                        vencimentos = list(fn['vencimentos'].values())
                        fornecedores.append({**fn, 'vencimentos': vencimentos})
                    produtores.append({**pn, 'fornecedores': fornecedores})
                grouped.append({**sn, 'produtores': produtores})
            ctx['report_groups_hier'] = grouped
        except Exception:
            ctx['report_groups_hier'] = []

        return ctx


class PedidoCompraReportResumidoView(GestorRequiredMixin, ListView):
    model = PedidoCompra
    template_name = 'core/relatorios/pedido_resumido.html'
    context_object_name = 'rows'

    def get_queryset(self):
        qs = super().get_queryset().select_related('safra', 'cliente', 'produtor', 'fornecedor')
        q = _normalize_filter_value(self.request.GET.get('q'))
        pedido_num = _normalize_filter_value(self.request.GET.get('pedido'))
        safra_id = _selected_get_value(self.request, 'safra')
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        produtor_ids = _normalize_multi_values(self.request.GET.getlist('produtor'))
        if not produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                produtor_ids = [single_produtor]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))
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
        if produtor_ids:
            qs = qs.filter(produtor_id__in=produtor_ids)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if status:
            qs = qs.filter(status=status)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)

        # Aggregate: Cliente, Produtor, Safra, Fornecedor(Fantasia), Vencimento
        return (
            qs.annotate(
                fornecedor_label=Case(
                    When(Q(fornecedor__fantasia__isnull=True) | Q(fornecedor__fantasia=''), then=F('fornecedor__fornecedor')),
                    default=F('fornecedor__fantasia'),
                ),
                nota_fiscal_ref=Value('-', output_field=CharField()),
            ).values(
                'cliente__cliente',
                'produtor__produtor',
                'produtor__fazenda',
                'safra__safra',
                'fornecedor_label',
                'vencimento',
                'nota_fiscal_ref',
            )
            .annotate(
                pedidos=Count('id'),
                data_ref=Min('data'),
                pedido_ref=Min('pedido'),
                total=Sum('valor_total'),
                saldo=Sum('saldo_faturar'),
            )
            .order_by('safra__safra', 'produtor__produtor', 'fornecedor_label', 'vencimento', 'data_ref')
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
        resumo_map = OrderedDict()
        matrix_by_cliente = OrderedDict()
        for r in ctx.get('rows') or []:
            try:
                total_geral += (r.get('total') or Decimal('0'))
            except Exception:
                pass
            try:
                saldo_geral += (r.get('saldo') or Decimal('0'))
            except Exception:
                pass
            try:
                safra_lbl = (r.get('safra__safra') or '-')
                forn_lbl = (r.get('fornecedor_label') or '-')
                rk = (safra_lbl, forn_lbl)
                if rk not in resumo_map:
                    resumo_map[rk] = {
                        'safra_label': safra_lbl,
                        'fornecedor_label': forn_lbl,
                        'pedidos': 0,
                        'saldo': Decimal('0'),
                        'total': Decimal('0'),
                    }
                resumo_map[rk]['pedidos'] += int(r.get('pedidos') or 0)
                resumo_map[rk]['saldo'] += (r.get('saldo') or Decimal('0'))
                resumo_map[rk]['total'] += (r.get('total') or Decimal('0'))
            except Exception:
                pass
            try:
                cliente_lbl = (r.get('cliente__cliente') or '-')
                produtor_lbl = ((r.get('produtor__produtor') or '-') + (f" - {r.get('produtor__fazenda')}" if r.get('produtor__fazenda') else ''))
                safra_lbl = (r.get('safra__safra') or '-')
                valor = (r.get('total') or Decimal('0'))
                if cliente_lbl not in matrix_by_cliente:
                    matrix_by_cliente[cliente_lbl] = {
                        'cliente_label': cliente_lbl,
                        'total': Decimal('0'),
                        'safras_set': set(),
                        'prod_map': OrderedDict(),
                    }
                cnode = matrix_by_cliente[cliente_lbl]
                cnode['total'] += valor
                cnode['safras_set'].add(safra_lbl)
                if produtor_lbl not in cnode['prod_map']:
                    cnode['prod_map'][produtor_lbl] = defaultdict(lambda: Decimal('0'))
                cnode['prod_map'][produtor_lbl][safra_lbl] += valor
            except Exception:
                pass
        ctx['total_geral'] = total_geral
        ctx['saldo_geral'] = saldo_geral
        ctx['resumo_safra_fornecedor'] = list(resumo_map.values())
        header_matrix = []
        for cnode in matrix_by_cliente.values():
            safras_cols = sorted(list(cnode['safras_set']))
            rows = []
            for produtor_lbl, vals_map in cnode['prod_map'].items():
                vals = [vals_map.get(s, Decimal('0')) for s in safras_cols]
                rows.append({'produtor_label': produtor_lbl, 'values': vals})
            totals = [sum((r['values'][i] for r in rows), Decimal('0')) for i in range(len(safras_cols))]
            header_matrix.append(
                {
                    'cliente_label': cnode['cliente_label'],
                    'total': cnode['total'],
                    'safras_cols': safras_cols,
                    'rows': rows,
                    'totals': totals,
                }
            )
        ctx['header_matrix'] = header_matrix
        return ctx


class PedidoCompraReportPendentesView(GestorRequiredMixin, ListView):
    model = PedidoCompraItem
    template_name = 'core/relatorios/pedido_pendentes.html'
    context_object_name = 'rows'

    def get_queryset(self):
        qs = (
            PedidoCompraItem.objects.select_related(
                'pedido_compra',
                'pedido_compra__cliente',
                'pedido_compra__safra',
                'pedido_compra__produtor',
                'produto_cadastro',
            )
            .order_by(
                'pedido_compra__cliente__cliente',
                'pedido_compra__safra__safra',
                'pedido_compra__produtor__produtor',
                'pedido_compra__pedido',
                'pedido_compra__data',
                'id',
            )
        )

        topbar = _get_topbar_state(self.request, scope='pedidos', default_cultura=_default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']
        if topbar_cultura:
            qs = qs.filter(pedido_compra__safra__cultura_id=topbar_cultura)
        if topbar_safra:
            qs = qs.filter(pedido_compra__safra_id=topbar_safra)

        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        produtor_ids = [v for v in (_normalize_filter_value(x) for x in self.request.GET.getlist('produtor')) if v]
        if not produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                produtor_ids = [single_produtor]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        pedido_num = _normalize_filter_value(self.request.GET.get('pedido'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))
        data_ini = _normalize_filter_value(self.request.GET.get('data_ini'))
        data_fim = _normalize_filter_value(self.request.GET.get('data_fim'))
        produto_txt = _normalize_filter_value(self.request.GET.get('produto'))

        if categoria_id:
            qs = qs.filter(produto_cadastro__categoria_id=categoria_id)
        if cliente_id:
            qs = qs.filter(pedido_compra__cliente_id=cliente_id)
        if produtor_id:
            qs = qs.filter(pedido_compra__produtor_id=produtor_id)
        if fornecedor_id:
            qs = qs.filter(pedido_compra__fornecedor_id=fornecedor_id)
        if pedido_num:
            qs = qs.filter(pedido_compra__pedido__icontains=pedido_num)
        if status:
            qs = qs.filter(pedido_compra__status=status)
        if venc_ini:
            qs = qs.filter(pedido_compra__vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(pedido_compra__vencimento__lte=venc_fim)
        if data_ini:
            qs = qs.filter(pedido_compra__data__gte=data_ini)
        if data_fim:
            qs = qs.filter(pedido_compra__data__lte=data_fim)
        if produto_txt:
            qs = qs.filter(
                Q(produto__icontains=produto_txt)
                | Q(produto_cadastro__nome__icontains=produto_txt)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/pedidos/'

        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        items = list(ctx.get('rows') or [])
        pedido_ids = [it.pedido_compra_id for it in items if getattr(it, 'pedido_compra_id', None)]

        entregue_map = defaultdict(lambda: Decimal('0'))
        if pedido_ids:
            fat_itens = (
                FaturamentoItem.objects.filter(faturamento__pedido_id__in=pedido_ids)
                .select_related('faturamento', 'produto_cadastro', 'unidade')
            )
            for fi in fat_itens:
                if fi.produto_cadastro_id:
                    pkey = f'cad:{fi.produto_cadastro_id}'
                else:
                    pkey = f'txt:{(fi.produto or "").strip().upper()}'
                ukey = fi.unidade_id or 0
                key = (fi.faturamento.pedido_id, pkey, ukey)
                entregue_map[key] += (fi.quantidade or Decimal('0'))

        report_rows = []
        total_pendente = Decimal('0')
        for it in items:
            ped = it.pedido_compra
            if it.produto_cadastro_id:
                pkey = f'cad:{it.produto_cadastro_id}'
            else:
                pkey = f'txt:{(it.produto or "").strip().upper()}'
            ukey = it.unidade_id or 0
            key = (ped.id, pkey, ukey)

            qtd = it.quantidade or Decimal('0')
            entregue = entregue_map.get(key, Decimal('0'))
            pendente = qtd - entregue
            if pendente < 0:
                pendente = Decimal('0')
            if pendente <= 0:
                continue

            produto_nome = ''
            try:
                produto_nome = it.produto_cadastro.nome if it.produto_cadastro else (it.produto or '')
            except Exception:
                produto_nome = it.produto or ''

            report_rows.append(
                {
                    'cliente': getattr(ped.cliente, 'cliente', '-') if getattr(ped, 'cliente_id', None) else '-',
                    'safra': getattr(ped.safra, 'safra', '-') if getattr(ped, 'safra_id', None) else '-',
                    'produtor': str(ped.produtor) if getattr(ped, 'produtor_id', None) else '-',
                    'pedido': ped.pedido,
                    'data': ped.data,
                    'vencimento': ped.vencimento,
                    'produto': produto_nome or '-',
                    'quantidade': qtd,
                    'preco': it.preco or Decimal('0'),
                    'entregue': entregue,
                    'pendente': pendente,
                }
            )
            total_pendente += pendente

        ctx['rows'] = report_rows
        ctx['total_rows'] = len(report_rows)
        ctx['total_pendente'] = total_pendente
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
            .select_related('pedido', 'safra', 'produtor', 'fornecedor', 'cliente')
            .prefetch_related('itens', 'itens__produto_cadastro')
            .order_by('safra__safra', 'cliente__cliente', 'produtor__produtor', 'fornecedor__fornecedor', '-data', '-id')
        )

        topbar = _get_topbar_state(self.request, scope='faturamentos', default_cultura=_default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']

        q = _normalize_filter_value(self.request.GET.get('q'))
        cultura_id = _selected_get_value(self.request, 'cultura') or topbar_cultura
        safra_id = _selected_get_value(self.request, 'safra') or topbar_safra
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        produtor_ids = []
        for raw_val in self.request.GET.getlist('produtor'):
            for part in str(raw_val or '').split(','):
                v = _normalize_filter_value(part)
                if v and v not in produtor_ids:
                    produtor_ids.append(v)
        if not produtor_ids:
            produtor_single = _normalize_filter_value(self.request.GET.get('produtor'))
            if produtor_single:
                produtor_ids = [produtor_single]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        custo_id = _normalize_filter_value(self.request.GET.get('custo'))
        pedido_txt = _normalize_filter_value(self.request.GET.get('pedido'))
        nota_fiscal = _normalize_filter_value(self.request.GET.get('nota_fiscal'))
        produto_txt = _normalize_filter_value(self.request.GET.get('produto'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))

        if q:
            qs = qs.filter(
                Q(nota_fiscal__icontains=q)
                | Q(pedido__pedido__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
                | Q(cliente__cliente__icontains=q)
            )
        if cultura_id:
            qs = qs.filter(safra__cultura_id=cultura_id)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
            qs = _apply_safra_period_filter(qs, safra_id, 'data')
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_ids:
            qs = qs.filter(produtor_id__in=produtor_ids)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if categoria_id:
            qs = qs.filter(itens__produto_cadastro__categoria_id=categoria_id).distinct()
        if custo_id:
            qs = qs.filter(custo_id=custo_id)
        if status:
            qs = qs.filter(status=status)
        if pedido_txt:
            qs = qs.filter(pedido__pedido__icontains=pedido_txt)
        if nota_fiscal:
            qs = qs.filter(nota_fiscal__icontains=nota_fiscal)
        if produto_txt:
            qs = qs.filter(
                Q(itens__produto_cadastro__nome__icontains=produto_txt)
                | Q(itens__produto__icontains=produto_txt)
            ).distinct()
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/faturamentos/'

        lic = None
        try:
            lic = PerfilUsuarioLicenca.objects.select_related('licenca').filter(usuario=self.request.user).first()
        except Exception:
            lic = None
        ctx['licenca'] = lic.licenca if lic else None

        def _safra_label(nf):
            try:
                return nf.safra.safra if nf.safra_id else 'Sem safra'
            except Exception:
                return 'Sem safra'

        def _cliente_label(nf):
            try:
                return nf.cliente.cliente if nf.cliente_id else 'Sem cliente'
            except Exception:
                return 'Sem cliente'

        def _produtor_label(nf):
            try:
                if not nf.produtor_id:
                    return 'Sem produtor'
                base = (nf.produtor.apelido or nf.produtor.produtor or '')
                faz = nf.produtor.fazenda or ''
                return f"{base} - {faz}" if faz else base
            except Exception:
                return 'Sem produtor'

        def _fornecedor_label(nf):
            try:
                return str(nf.fornecedor) if nf.fornecedor_id else 'Sem fornecedor'
            except Exception:
                return 'Sem fornecedor'

        groups = OrderedDict()
        for nf in self.get_queryset():
            sk = (nf.safra_id or 0, _safra_label(nf))
            ck = (nf.cliente_id or 0, _cliente_label(nf))
            pk = (nf.produtor_id or 0, _produtor_label(nf))
            fk = (nf.fornecedor_id or 0, _fornecedor_label(nf))
            key = (sk, ck, pk)

            if key not in groups:
                groups[key] = {
                    'safra_label': sk[1],
                    'cliente_label': ck[1],
                    'produtor_label': pk[1],
                    'total': Decimal('0'),
                    'fornecedores': OrderedDict(),
                }
            g = groups[key]
            g['total'] += (nf.valor_total or Decimal('0'))

            if fk not in g['fornecedores']:
                g['fornecedores'][fk] = {
                    'fornecedor_label': fk[1],
                    'total': Decimal('0'),
                    'linhas': [],
                }
            fnode = g['fornecedores'][fk]
            fnode['total'] += (nf.valor_total or Decimal('0'))

            status_lbl = nf.get_status_display()
            pedido_txt = ''
            try:
                pedido_txt = nf.pedido.pedido if nf.pedido_id else '-'
            except Exception:
                pedido_txt = '-'

            for it in nf.itens.all():
                produto_nome = ''
                try:
                    produto_nome = it.produto_cadastro.nome if it.produto_cadastro_id else (it.produto or '')
                except Exception:
                    produto_nome = it.produto or ''
                fnode['linhas'].append(
                    {
                        'data': nf.data,
                        'nota': nf.nota_fiscal,
                        'pedido': pedido_txt or '-',
                        'produto': produto_nome or '-',
                        'quantidade': it.quantidade or Decimal('0'),
                        'preco': it.preco or Decimal('0'),
                        'total': it.total_item or Decimal('0'),
                        'status': status_lbl,
                    }
                )

            if not nf.itens.all():
                fnode['linhas'].append(
                    {
                        'data': nf.data,
                        'nota': nf.nota_fiscal,
                        'pedido': pedido_txt or '-',
                        'produto': '-',
                        'quantidade': Decimal('0'),
                        'preco': Decimal('0'),
                        'total': nf.valor_total or Decimal('0'),
                        'status': status_lbl,
                    }
                )

        report_groups = []
        total_geral = Decimal('0')
        for g in groups.values():
            fornecedores = list(g['fornecedores'].values())
            report_groups.append(
                {
                    'safra_label': g['safra_label'],
                    'cliente_label': g['cliente_label'],
                    'produtor_label': g['produtor_label'],
                    'total': g['total'],
                    'fornecedores': fornecedores,
                }
            )
            total_geral += (g['total'] or Decimal('0'))

        ctx['report_groups'] = report_groups
        ctx['total_geral'] = total_geral
        return ctx


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
        safra_id = _selected_get_value(self.request, 'safra')
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
                    'fornecedor': str(conta.fornecedor) if conta.fornecedor_id else '-',
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

    def _resolve_next_url(self, request):
        raw = (
            request.POST.get('next')
            or request.GET.get('next')
            or str(self.success_url)
        )
        decoded = unquote((raw or '').strip())
        if not decoded:
            return str(self.success_url)
        if url_has_allowed_host_and_scheme(decoded, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return decoded
        if decoded.startswith('/'):
            return decoded
        return str(self.success_url)

    def _resolve_edit_url(self, request, pedido_id):
        base = reverse('core:pedido_update', kwargs={'pk': pedido_id})
        next_url = self._resolve_next_url(request)
        if next_url:
            return f'{base}?next={quote(next_url, safe="")}'
        return base

    def _normalize_existing_totais(self, pedido):
        if not pedido or not getattr(pedido, 'pk', None):
            return
        mudou = False
        total_pedido = Decimal('0')
        for item in pedido.itens.all():
            qtd = item.quantidade or Decimal('0')
            preco = item.preco or Decimal('0')
            desc = item.desconto or Decimal('0')
            total_item = (qtd * preco) - desc
            if total_item < 0:
                total_item = Decimal('0')
            total_pedido += total_item
            if item.total_item != total_item:
                item.total_item = total_item
                item.save(update_fields=['total_item'])
                mudou = True
        if pedido.valor_total != total_pedido or pedido.saldo_faturar != total_pedido:
            pedido.valor_total = total_pedido
            pedido.saldo_faturar = total_pedido
            pedido.save(update_fields=['valor_total', 'saldo_faturar'])
            mudou = True
        return mudou

    def get(self, request, pk=None):
        instance = PedidoCompra.objects.filter(pk=pk).first() if pk else PedidoCompra()
        if instance and getattr(instance, 'pk', None):
            self._normalize_existing_totais(instance)
        form = PedidoCompraForm(instance=instance)
        formset = self.get_item_formset(instance)
        next_url = self._resolve_next_url(request)
        return render(
            request,
            self.template_name,
            {
                'form': form,
                'formset': formset,
                'titulo': 'Pedido de Compra',
                'next_url': next_url,
                'modo_edicao': bool(getattr(instance, 'pk', None)),
            },
        )

    def post(self, request, pk=None):
        instance = PedidoCompra.objects.filter(pk=pk).first() if pk else PedidoCompra()
        form = PedidoCompraForm(request.POST, instance=instance)
        formset = self.get_item_formset(instance, data=request.POST)
        next_url = self._resolve_next_url(request)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                pedido = form.save(commit=False)
                # valor_total e calculado
                self._calc_totais(pedido, formset)
                pedido.save()
                formset.instance = pedido
                formset.save()
                messages.success(request, 'Registro salvo com sucesso.')
            return redirect(self._resolve_edit_url(request, pedido.pk))

        messages.error(request, 'Nao foi possivel salvar. Verifique os campos.')
        return render(
            request,
            self.template_name,
            {
                'form': form,
                'formset': formset,
                'titulo': 'Pedido de Compra',
                'next_url': next_url,
                'modo_edicao': bool(getattr(instance, 'pk', None)),
            },
        )


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
    paginate_by = 10

    def get_paginate_by(self, queryset):
        return _resolve_per_page(self.request, super().get_paginate_by(queryset))

    def get(self, request, *args, **kwargs):
        restored = _panel_filters_restore_or_save(request, 'faturamentos')
        if restored is not None:
            return redirect(f'{request.path}?{restored.urlencode()}')
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        default_order = ['-data', '-id']
        sort_map = {
            'data': 'data',
            'pedido': 'pedido__pedido',
            'safra': 'safra__safra',
            'vencimento': 'vencimento',
            'produtor': 'produtor__produtor',
            'fornecedor': 'fornecedor__fornecedor',
            'valor_total': 'valor_total',
            'saldo': 'conta_pagar__saldo_aberto',
        }
        current_sort = (self.request.GET.get('o') or '').strip()
        sort_field = current_sort[1:] if current_sort.startswith('-') else current_sort
        sort_desc = current_sort.startswith('-')
        if sort_field in sort_map:
            ord_field = sort_map[sort_field]
            order_by = [f'-{ord_field}' if sort_desc else ord_field, '-id']
        else:
            order_by = default_order
        qs = (
            super()
            .get_queryset()
            .select_related('safra', 'cliente', 'produtor', 'fornecedor', 'pedido')
            .prefetch_related('itens__produto_cadastro')
            .order_by(*order_by)
        )

        topbar = _get_topbar_state(self.request, scope='faturamentos', default_cultura=_default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']
        if topbar_safra:
            try:
                safra_ref = Safra.objects.only('cultura_id').filter(pk=topbar_safra).first()
                if safra_ref and safra_ref.cultura_id:
                    topbar_cultura = str(safra_ref.cultura_id)
            except Exception:
                pass
        if topbar_cultura:
            qs = qs.filter(safra__cultura_id=topbar_cultura)
        if topbar_safra:
            qs = qs.filter(safra_id=topbar_safra)

        # Padrao: filtros somente quando clicar em "Aplicar filtros" (apply=1)
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'cultura', 'safra', 'cliente', 'produtor', 'fornecedor', 'categoria', 'custo', 'pedido', 'nota_fiscal', 'produto', 'status', 'data_ini', 'data_fim', 'venc_ini', 'venc_fim'],
        )
        if not filtros_ativos:
            return qs.order_by(*order_by)

        q = _normalize_filter_value(self.request.GET.get('q'))
        cultura_id = _selected_get_value(self.request, 'cultura') or topbar_cultura
        safra_id = _selected_get_value(self.request, 'safra') or topbar_safra
        if safra_id:
            try:
                safra_ref = Safra.objects.only('cultura_id').filter(pk=safra_id).first()
                if safra_ref and safra_ref.cultura_id:
                    cultura_id = str(safra_ref.cultura_id)
            except Exception:
                pass
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        produtor_ids = []
        for raw_val in self.request.GET.getlist('produtor'):
            for part in str(raw_val or '').split(','):
                v = _normalize_filter_value(part)
                if v and v not in produtor_ids:
                    produtor_ids.append(v)
        if not produtor_ids:
            produtor_single = _normalize_filter_value(self.request.GET.get('produtor'))
            if produtor_single:
                produtor_ids = [produtor_single]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        custo_id = _normalize_filter_value(self.request.GET.get('custo'))
        pedido_txt = _normalize_filter_value(self.request.GET.get('pedido'))
        nota_fiscal = _normalize_filter_value(self.request.GET.get('nota_fiscal'))
        produto_txt = _normalize_filter_value(self.request.GET.get('produto'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        data_ini = _normalize_filter_value(self.request.GET.get('data_ini'))
        data_fim = _normalize_filter_value(self.request.GET.get('data_fim'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))

        if q:
            qs = qs.filter(
                Q(nota_fiscal__icontains=q)
                | Q(pedido__pedido__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
                | Q(cliente__cliente__icontains=q)
            )
        if cultura_id:
            qs = qs.filter(safra__cultura_id=cultura_id)
        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_ids:
            qs = qs.filter(produtor_id__in=produtor_ids)
        if fornecedor_id:
            qs = qs.filter(fornecedor_id=fornecedor_id)
        if categoria_id:
            qs = qs.filter(itens__produto_cadastro__categoria_id=categoria_id).distinct()
        if custo_id:
            qs = qs.filter(custo_id=custo_id)
        if status:
            qs = qs.filter(status=status)
        if pedido_txt:
            qs = qs.filter(pedido__pedido__icontains=pedido_txt)
        if nota_fiscal:
            qs = qs.filter(nota_fiscal__icontains=nota_fiscal)
        if produto_txt:
            qs = qs.filter(
                Q(itens__produto_cadastro__nome__icontains=produto_txt)
                | Q(itens__produto__icontains=produto_txt)
            ).distinct()
        if data_ini:
            qs = qs.filter(data__gte=data_ini)
        if data_fim:
            qs = qs.filter(data__lte=data_fim)
        if venc_ini:
            qs = qs.filter(vencimento__gte=venc_ini)
        if venc_fim:
            qs = qs.filter(vencimento__lte=venc_fim)
        return qs.order_by(*order_by)

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)

        for col in (self.columns or []):
            if isinstance(col, (list, tuple)) and len(col) >= 2:
                label, field = col[0], col[1]
            else:
                label, field = str(col), str(col)

            params = base_params.copy()
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            self.request.session['faturamentos_last_list_url'] = self.request.get_full_path()
        except Exception:
            pass
        topbar = _get_topbar_state(self.request, scope='faturamentos', default_cultura=_default_cultura_soja_id())
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'cultura', 'safra', 'cliente', 'produtor', 'fornecedor', 'categoria', 'custo', 'pedido', 'nota_fiscal', 'produto', 'status', 'data_ini', 'data_fim', 'venc_ini', 'venc_fim'],
        )
        qs = self.get_queryset()
        fat_ids_base = list(qs.values_list('id', flat=True).distinct())
        qs_base = Faturamento.objects.filter(pk__in=fat_ids_base)
        itens_qs = FaturamentoItem.objects.filter(faturamento__in=qs)
        qtd_total = itens_qs.aggregate(total=Sum('quantidade'))['total'] or 0
        total_fat = qs.aggregate(total=Sum('valor_total'))['total'] or 0
        total_a_pagar = (
            ContaPagar.objects
            .filter(faturamento__in=qs, pago=False, saldo_aberto__gt=0)
            .aggregate(total=Sum('saldo_aberto'))
            .get('total')
            or Decimal('0')
        )
        preco_medio = 0
        if qtd_total:
            try:
                preco_medio = Decimal(total_fat or 0) / Decimal(qtd_total or 1)
            except Exception:
                preco_medio = 0

        context['create_url_name'] = 'core:faturamento_create'
        context['edit_url_name'] = 'core:faturamento_update'
        context['delete_url_name'] = 'core:faturamento_delete'

        context['current_q'] = _normalize_filter_value(self.request.GET.get('q'))
        context['current_sort'] = (self.request.GET.get('o') or '').strip()
        context['current_sort'] = (self.request.GET.get('o') or '').strip()
        context['filtro_cultura'] = _selected_get_value(self.request, 'cultura') or topbar['filtro_cultura']
        context['filtro_safra'] = _selected_get_value(self.request, 'safra') or topbar['filtro_safra']
        if context['filtro_safra']:
            try:
                safra_ref = Safra.objects.only('cultura_id').filter(pk=context['filtro_safra']).first()
                if safra_ref and safra_ref.cultura_id:
                    context['filtro_cultura'] = str(safra_ref.cultura_id)
            except Exception:
                pass
        context['filtro_cliente'] = _normalize_filter_value(self.request.GET.get('cliente'))
        filtro_produtor_ids = []
        for raw_val in self.request.GET.getlist('produtor'):
            for part in str(raw_val or '').split(','):
                v = _normalize_filter_value(part)
                if v and v not in filtro_produtor_ids:
                    filtro_produtor_ids.append(v)
        if not filtro_produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                filtro_produtor_ids = [single_produtor]
        context['filtro_produtor_ids'] = filtro_produtor_ids
        context['filtro_produtor'] = filtro_produtor_ids[0] if len(filtro_produtor_ids) == 1 else ''
        context['filtro_fornecedor'] = _normalize_filter_value(self.request.GET.get('fornecedor'))
        context['filtro_categoria'] = _normalize_filter_value(self.request.GET.get('categoria'))
        context['filtro_custo'] = _normalize_filter_value(self.request.GET.get('custo'))
        context['filtro_pedido'] = _normalize_filter_value(self.request.GET.get('pedido'))
        context['filtro_nota_fiscal'] = _normalize_filter_value(self.request.GET.get('nota_fiscal'))
        context['filtro_produto'] = _normalize_filter_value(self.request.GET.get('produto'))
        context['filtro_status'] = _normalize_filter_value(self.request.GET.get('status'))
        context['filtro_data_ini'] = _normalize_filter_value(self.request.GET.get('data_ini'))
        context['filtro_data_fim'] = _normalize_filter_value(self.request.GET.get('data_fim'))
        context['filtro_venc_ini'] = _normalize_filter_value(self.request.GET.get('venc_ini'))
        context['filtro_venc_fim'] = _normalize_filter_value(self.request.GET.get('venc_fim'))
        try:
            context['filtro_cliente_label'] = (
                Cliente.objects.filter(pk=context['filtro_cliente']).values_list('cliente', flat=True).first()
                if context.get('filtro_cliente') else ''
            ) or ''
            context['filtro_fornecedor_label'] = (
                Fornecedor.objects.filter(pk=context['filtro_fornecedor']).values_list('fornecedor', flat=True).first()
                if context.get('filtro_fornecedor') else ''
            ) or ''
            produtores_sel = list(
                Produtor.objects.filter(pk__in=filtro_produtor_ids).only('produtor', 'fazenda').order_by('produtor', 'fazenda')
            ) if filtro_produtor_ids else []
            context['filtro_produtor_labels'] = [
                ((p.apelido or p.produtor) + (f" - {p.fazenda}" if p.fazenda else '')).strip()
                for p in produtores_sel
                if (p.produtor or '').strip()
            ]
        except Exception:
            context['filtro_cliente_label'] = ''
            context['filtro_fornecedor_label'] = ''
            context['filtro_produtor_labels'] = []
        context['culturas'] = topbar['culturas']
        context['safras'] = topbar['safras']
        context['safras_topbar'] = topbar['safras_topbar']
        context['clientes'] = Cliente.objects.all().order_by('cliente')
        context['produtores'] = Produtor.objects.all().order_by('produtor', 'fazenda')
        context['fornecedores'] = Fornecedor.objects.all().order_by('fornecedor')
        context['categorias'] = Categoria.objects.all().order_by('nome')
        context['custos'] = Custo.objects.all().order_by('nome')
        context['status_choices'] = [
            ('', 'Todos'),
            ('VENCIDO', 'Vencido'),
            (Faturamento.Status.A_RECEBER, 'A Pagar'),
            (Faturamento.Status.PAGO, 'Pago'),
        ]

        params = self.request.GET.copy()
        params.pop('page', None)
        context['pagination_query'] = params.urlencode()
        context['report_query'] = params.urlencode()
        context['current_sort'] = (self.request.GET.get('o') or '').strip()

        context['card_notas'] = qs.count()
        context['card_quantidade'] = qtd_total
        context['card_preco_medio'] = preco_medio
        context['card_a_pagar'] = total_a_pagar
        context['card_total_faturado'] = total_fat
        context['total_registros'] = qs.count()

        # Graficos: Faturamento por Categoria e por Safra (todas as safras da cultura)
        # Base de PEDIDOS deve respeitar os filtros do contexto (nao apenas os que ja faturaram).
        pedidos_ctx_qs = (
            PedidoCompra.objects
            .select_related('safra', 'cliente', 'produtor', 'fornecedor')
            .prefetch_related('itens__produto_cadastro')
        )
        try:
            cultura_id_ctx = context.get('filtro_cultura')
            safra_id_ctx = context.get('filtro_safra')
            q_ctx = context.get('current_q')
            cliente_id_ctx = context.get('filtro_cliente')
            produtor_ids_ctx = context.get('filtro_produtor_ids') or []
            fornecedor_id_ctx = context.get('filtro_fornecedor')
            categoria_id_ctx = context.get('filtro_categoria')
            custo_id_ctx = context.get('filtro_custo')
            pedido_txt_ctx = context.get('filtro_pedido')
            produto_txt_ctx = context.get('filtro_produto')
            data_ini_ctx = context.get('filtro_data_ini')
            data_fim_ctx = context.get('filtro_data_fim')
            venc_ini_ctx = context.get('filtro_venc_ini')
            venc_fim_ctx = context.get('filtro_venc_fim')

            if cultura_id_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(safra__cultura_id=cultura_id_ctx)
            if safra_id_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(safra_id=safra_id_ctx)
            if q_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(
                    Q(pedido__icontains=q_ctx)
                    | Q(cliente__cliente__icontains=q_ctx)
                    | Q(produtor__produtor__icontains=q_ctx)
                    | Q(fornecedor__fornecedor__icontains=q_ctx)
                )
            if cliente_id_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(cliente_id=cliente_id_ctx)
            if produtor_ids_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(produtor_id__in=produtor_ids_ctx)
            if fornecedor_id_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(fornecedor_id=fornecedor_id_ctx)
            if categoria_id_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(itens__produto_cadastro__categoria_id=categoria_id_ctx)
            if custo_id_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(custo_id=custo_id_ctx)
            if pedido_txt_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(pedido__icontains=pedido_txt_ctx)
            if produto_txt_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(
                    Q(itens__produto_cadastro__nome__icontains=produto_txt_ctx)
                    | Q(itens__produto__icontains=produto_txt_ctx)
                )
            if data_ini_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(data__gte=data_ini_ctx)
            if data_fim_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(data__lte=data_fim_ctx)
            if venc_ini_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(vencimento__gte=venc_ini_ctx)
            if venc_fim_ctx:
                pedidos_ctx_qs = pedidos_ctx_qs.filter(vencimento__lte=venc_fim_ctx)
        except Exception:
            pass
        pedido_ids_qs = list(pedidos_ctx_qs.values_list('id', flat=True).distinct())
        pedido_total_global = (
            PedidoCompraItem.objects.filter(pedido_compra_id__in=pedido_ids_qs).aggregate(total=Sum('total_item')).get('total')
            or Decimal('0')
        )

        def _to_rows(dct):
            rows = [{'label': k, 'valor': (v or Decimal('0'))} for k, v in dct.items() if (v or Decimal('0')) > 0]
            rows.sort(key=lambda x: x['valor'], reverse=True)
            rows = rows[:12]
            max_v = max([r['valor'] for r in rows], default=Decimal('0'))
            for r in rows:
                r['pct'] = int((r['valor'] * Decimal('100') / max_v).quantize(Decimal('1'))) if max_v > 0 else 0
            return rows

        def _build_dual_rows(fat_map, pedido_map, limit=12):
            labels = set(list(fat_map.keys()) + list(pedido_map.keys()))
            rows = []
            total_f = sum([(Decimal(v or 0)) for v in fat_map.values()], Decimal('0'))
            total_p = sum([(Decimal(v or 0)) for v in pedido_map.values()], Decimal('0'))
            for label in labels:
                f = Decimal(fat_map.get(label) or 0)
                p = Decimal(pedido_map.get(label) or 0)
                if f <= 0 and p <= 0:
                    continue
                rows.append({'label': label, 'faturado_valor': f, 'pedido_valor': p, 'valor': f})
            rows.sort(key=lambda x: (x['faturado_valor'] + x['pedido_valor']), reverse=True)
            rows = rows[:limit]
            max_dual = max([max(r['faturado_valor'], r['pedido_valor']) for r in rows], default=Decimal('0'))
            for r in rows:
                if max_dual > 0:
                    r['pct_fat_scale'] = int((r['faturado_valor'] * Decimal('100') / max_dual).quantize(Decimal('1')))
                    r['pct_ped_scale'] = int((r['pedido_valor'] * Decimal('100') / max_dual).quantize(Decimal('1')))
                else:
                    r['pct_fat_scale'] = 0
                    r['pct_ped_scale'] = 0
                r['pct_fat_total'] = (r['faturado_valor'] * Decimal('100') / total_f).quantize(Decimal('1')) if total_f > 0 else Decimal('0')
                r['pct_ped_total'] = (r['pedido_valor'] * Decimal('100') / total_p).quantize(Decimal('1')) if total_p > 0 else Decimal('0')
                r['pct_pf'] = (r['faturado_valor'] * Decimal('100') / r['pedido_valor']).quantize(Decimal('0.1')) if r['pedido_valor'] > 0 else Decimal('0.0')
            return rows

        def _attach_pedido_vs_faturado(rows, pedido_map):
            for r in rows:
                pedido_val = Decimal(pedido_map.get(r['label']) or 0)
                fat_val = Decimal(r.get('valor') or 0)
                r['pedido_valor'] = pedido_val
                r['faturado_valor'] = fat_val
                if pedido_val > 0:
                    r['pct_pf'] = (fat_val * Decimal('100') / pedido_val).quantize(Decimal('0.1'))
                else:
                    r['pct_pf'] = Decimal('0.0')
            return rows

        def _norm_chart_key(val: str) -> str:
            txt = str(val or '').strip().lower()
            if not txt:
                return ''
            txt = ''.join(ch for ch in unicodedata.normalize('NFKD', txt) if not unicodedata.combining(ch))
            return ' '.join(txt.split())

        def _norm_chart_key_loose(val: str) -> str:
            base = _norm_chart_key(val)
            if not base:
                return ''
            return ''.join(ch for ch in base if ch.isalnum())

        def _canon_categoria_nome(val: str) -> str:
            nk = _norm_chart_key(val)
            if not nk:
                return 'Sem categoria'
            alias = {
                'adubo': 'adubos',
                'adubos': 'adubos',
            }
            nk = alias.get(nk, nk)
            return ' '.join(p.capitalize() for p in nk.split()) or 'Sem categoria'

        chart_categoria = defaultdict(lambda: Decimal('0'))
        chart_categoria_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        pedido_item_categoria_por_nome = defaultdict(dict)
        pedido_item_categoria_single = {}
        pedido_categoria_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        try:
            produto_categoria_por_nome = {}
            produto_categoria_por_nome_loose = {}
            for p in (
                Produto.objects
                .select_related('categoria')
                .only('nome', 'categoria__nome')
            ):
                if not getattr(p, 'categoria', None):
                    continue
                nk = _norm_chart_key(getattr(p, 'nome', ''))
                if nk:
                    produto_categoria_por_nome[nk] = p.categoria.nome
                nk_loose = _norm_chart_key_loose(getattr(p, 'nome', ''))
                if nk_loose:
                    produto_categoria_por_nome_loose[nk_loose] = p.categoria.nome

            pedido_item_categoria_set = defaultdict(set)
            for pi in (
                PedidoCompraItem.objects
                .filter(pedido_compra_id__in=pedido_ids_qs)
                .select_related('produto_cadastro__categoria')
                .only('pedido_compra_id', 'produto', 'produto_cadastro__categoria__nome')
            ):
                cat_pi = ''
                if pi.produto_cadastro_id and getattr(pi.produto_cadastro, 'categoria', None):
                    cat_pi = pi.produto_cadastro.categoria.nome
                if not cat_pi:
                    cat_pi = produto_categoria_por_nome.get(_norm_chart_key(getattr(pi, 'produto', '')), '')
                if not cat_pi:
                    cat_pi = produto_categoria_por_nome_loose.get(_norm_chart_key_loose(getattr(pi, 'produto', '')), '')
                nk_pi = _norm_chart_key(getattr(pi, 'produto', ''))
                nk_pi_loose = _norm_chart_key_loose(getattr(pi, 'produto', ''))
                if nk_pi and cat_pi:
                    pedido_item_categoria_por_nome[pi.pedido_compra_id][nk_pi] = cat_pi
                if nk_pi_loose and cat_pi:
                    pedido_item_categoria_por_nome[pi.pedido_compra_id][nk_pi_loose] = cat_pi
                if cat_pi:
                    pedido_item_categoria_set[pi.pedido_compra_id].add(cat_pi)

            for pedido_id, cats in pedido_item_categoria_set.items():
                if len(cats) == 1:
                    pedido_item_categoria_single[pedido_id] = next(iter(cats))

            for it in (
                FaturamentoItem.objects
                .filter(faturamento__in=qs)
                .select_related('produto_cadastro__categoria', 'faturamento__safra', 'faturamento__pedido')
            ):
                cat_nome = ''
                # Prioriza a categoria do item no pedido relacionado para manter
                # consistencia entre Pedido x Faturamento no dashboard.
                if getattr(it, 'faturamento', None) and getattr(it.faturamento, 'pedido_id', None):
                    cat_nome = pedido_item_categoria_por_nome.get(it.faturamento.pedido_id, {}).get(
                        _norm_chart_key(getattr(it, 'produto', '')),
                        ''
                    )
                if not cat_nome and getattr(it, 'faturamento', None) and getattr(it.faturamento, 'pedido_id', None):
                    cat_nome = pedido_item_categoria_por_nome.get(it.faturamento.pedido_id, {}).get(
                        _norm_chart_key_loose(getattr(it, 'produto', '')),
                        ''
                    )
                if not cat_nome and getattr(it, 'faturamento', None) and getattr(it.faturamento, 'pedido_id', None):
                    cat_nome = pedido_item_categoria_single.get(it.faturamento.pedido_id, '')
                if not cat_nome and it.produto_cadastro_id and getattr(it.produto_cadastro, 'categoria', None):
                    cat_nome = it.produto_cadastro.categoria.nome
                if not cat_nome:
                    cat_nome = produto_categoria_por_nome.get(_norm_chart_key(getattr(it, 'produto', '')), '')
                if not cat_nome:
                    cat_nome = produto_categoria_por_nome_loose.get(_norm_chart_key_loose(getattr(it, 'produto', '')), '')
                cat_label = _canon_categoria_nome(cat_nome)
                chart_categoria[cat_label] += (it.total_item or Decimal('0'))
                safra_lbl = getattr(getattr(it, 'faturamento', None), 'safra', None)
                safra_nome = (getattr(safra_lbl, 'safra', '') or '').strip() or 'Sem safra'
                chart_categoria_safra[cat_label][safra_nome] += (it.total_item or Decimal('0'))
        except Exception:
            pass
        pedido_categoria = defaultdict(lambda: Decimal('0'))
        try:
            for pi in (
                PedidoCompraItem.objects
                .filter(pedido_compra_id__in=pedido_ids_qs)
                .select_related('produto_cadastro__categoria', 'pedido_compra__safra')
            ):
                cat_pi = ''
                if pi.produto_cadastro_id and getattr(pi.produto_cadastro, 'categoria', None):
                    cat_pi = pi.produto_cadastro.categoria.nome
                if not cat_pi:
                    cat_pi = produto_categoria_por_nome.get(_norm_chart_key(getattr(pi, 'produto', '')), '')
                if not cat_pi:
                    cat_pi = produto_categoria_por_nome_loose.get(_norm_chart_key_loose(getattr(pi, 'produto', '')), '')
                label = _canon_categoria_nome(cat_pi)
                val_total = (pi.total_item or Decimal('0'))
                pedido_categoria[label] += val_total
                safra_nome = (getattr(getattr(pi, 'pedido_compra', None), 'safra', None) and getattr(pi.pedido_compra.safra, 'safra', '')) or 'Sem safra'
                pedido_categoria_safra[label][safra_nome] += val_total
        except Exception:
            pass
        rows_categoria = _build_dual_rows(chart_categoria, pedido_categoria)
        for r in rows_categoria:
            safra_map = chart_categoria_safra.get(r['label'], {})
            ped_safra_map = pedido_categoria_safra.get(r['label'], {})
            fat_safras = [
                {'safra': sn, 'valor': (sv or Decimal('0'))}
                for sn, sv in safra_map.items()
                if (sv or Decimal('0')) > 0
            ]
            ped_safras = [
                {'safra': sn, 'valor': (sv or Decimal('0'))}
                for sn, sv in ped_safra_map.items()
                if (sv or Decimal('0')) > 0
            ]
            fat_safras.sort(key=lambda x: x['valor'], reverse=True)
            ped_safras.sort(key=lambda x: x['valor'], reverse=True)
            r['fat_safras'] = fat_safras
            r['ped_safras'] = ped_safras
        context['chart_faturamento_categoria'] = rows_categoria

        chart_fornecedor = defaultdict(lambda: Decimal('0'))
        chart_fornecedor_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        pedido_fornecedor_safra = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
        try:
            for r in (
                qs.values('fornecedor__fantasia', 'fornecedor__fornecedor', 'safra__safra')
                .annotate(total=Sum('valor_total'))
                .order_by('-total')
            ):
                label = (r.get('fornecedor__fantasia') or '').strip() or (r.get('fornecedor__fornecedor') or 'Sem fornecedor')
                total_r = (r.get('total') or Decimal('0'))
                chart_fornecedor[label] += total_r
                safra_nome = (r.get('safra__safra') or '').strip() or 'Sem safra'
                chart_fornecedor_safra[label][safra_nome] += total_r
        except Exception:
            pass
        pedido_fornecedor = defaultdict(lambda: Decimal('0'))
        try:
            for r in (
                PedidoCompra.objects
                .filter(pk__in=pedido_ids_qs)
                .values('fornecedor__fantasia', 'fornecedor__fornecedor', 'safra__safra')
                .annotate(total=Sum('valor_total'))
            ):
                label = (r.get('fornecedor__fantasia') or '').strip() or (r.get('fornecedor__fornecedor') or 'Sem fornecedor')
                total_r = (r.get('total') or Decimal('0'))
                pedido_fornecedor[label] += total_r
                safra_nome = (r.get('safra__safra') or '').strip() or 'Sem safra'
                pedido_fornecedor_safra[label][safra_nome] += total_r
        except Exception:
            pass
        rows_fornecedor = _build_dual_rows(chart_fornecedor, pedido_fornecedor)
        total_faturado_base = Decimal(total_fat or 0)
        for r in rows_fornecedor:
            if total_faturado_base > 0:
                r['pct_total'] = (Decimal(r['faturado_valor']) * Decimal('100') / total_faturado_base).quantize(Decimal('0.1'))
            else:
                r['pct_total'] = Decimal('0.0')
            safra_map = chart_fornecedor_safra.get(r['label'], {})
            fat_safras = [
                {'safra': sn, 'valor': (sv or Decimal('0'))}
                for sn, sv in safra_map.items()
                if (sv or Decimal('0')) > 0
            ]
            ped_safra_map = pedido_fornecedor_safra.get(r['label'], {})
            ped_safras = [
                {'safra': sn, 'valor': (sv or Decimal('0'))}
                for sn, sv in ped_safra_map.items()
                if (sv or Decimal('0')) > 0
            ]
            fat_safras.sort(key=lambda x: x['valor'], reverse=True)
            ped_safras.sort(key=lambda x: x['valor'], reverse=True)
            r['fat_safras'] = fat_safras
            r['ped_safras'] = ped_safras
        context['chart_faturamento_fornecedor'] = rows_fornecedor

        chart_safra_cultura = defaultdict(lambda: Decimal('0'))
        try:
            # Base para "todas as safras da cultura": respeita filtros ativos,
            # mas nao restringe pela safra selecionada.
            base_qs = (
                Faturamento.objects
                .select_related('safra', 'cliente', 'produtor', 'fornecedor', 'pedido')
                .prefetch_related('itens__produto_cadastro')
            )

            cultura_id = context.get('filtro_cultura')
            if cultura_id:
                base_qs = base_qs.filter(safra__cultura_id=cultura_id)

            q = context.get('current_q')
            cliente_id = context.get('filtro_cliente')
            produtor_ids = context.get('filtro_produtor_ids') or []
            fornecedor_id = context.get('filtro_fornecedor')
            categoria_id = context.get('filtro_categoria')
            custo_id = context.get('filtro_custo')
            pedido_txt = context.get('filtro_pedido')
            nota_fiscal = context.get('filtro_nota_fiscal')
            produto_txt = context.get('filtro_produto')
            status = context.get('filtro_status')
            data_ini = context.get('filtro_data_ini')
            data_fim = context.get('filtro_data_fim')
            venc_ini = context.get('filtro_venc_ini')
            venc_fim = context.get('filtro_venc_fim')

            if q:
                base_qs = base_qs.filter(
                    Q(nota_fiscal__icontains=q)
                    | Q(pedido__pedido__icontains=q)
                    | Q(produtor__produtor__icontains=q)
                    | Q(fornecedor__fornecedor__icontains=q)
                    | Q(cliente__cliente__icontains=q)
                )
            if cliente_id:
                base_qs = base_qs.filter(cliente_id=cliente_id)
            if produtor_ids:
                base_qs = base_qs.filter(produtor_id__in=produtor_ids)
            if fornecedor_id:
                base_qs = base_qs.filter(fornecedor_id=fornecedor_id)
            if categoria_id:
                base_qs = base_qs.filter(itens__produto_cadastro__categoria_id=categoria_id).distinct()
            if custo_id:
                base_qs = base_qs.filter(custo_id=custo_id)
            if status:
                if status == 'VENCIDO':
                    base_qs = base_qs.filter(
                        conta_pagar__vencimento__lt=date.today(),
                        conta_pagar__pago=False,
                        conta_pagar__saldo_aberto__gt=0,
                    )
                else:
                    base_qs = base_qs.filter(status=status)
            if pedido_txt:
                base_qs = base_qs.filter(pedido__pedido__icontains=pedido_txt)
            if nota_fiscal:
                base_qs = base_qs.filter(nota_fiscal__icontains=nota_fiscal)
            if produto_txt:
                base_qs = base_qs.filter(
                    Q(itens__produto_cadastro__nome__icontains=produto_txt)
                    | Q(itens__produto__icontains=produto_txt)
                ).distinct()
            if data_ini:
                base_qs = base_qs.filter(data__gte=data_ini)
            if data_fim:
                base_qs = base_qs.filter(data__lte=data_fim)
            if venc_ini:
                base_qs = base_qs.filter(vencimento__gte=venc_ini)
            if venc_fim:
                base_qs = base_qs.filter(vencimento__lte=venc_fim)

            for r in (
                base_qs.values('safra__safra')
                .annotate(total=Sum('valor_total'))
                .order_by('safra__safra')
            ):
                label = r.get('safra__safra') or 'Sem safra'
                chart_safra_cultura[label] += (r.get('total') or Decimal('0'))
        except Exception:
            pass
        rows_safra = []
        try:
            safra_fat_rows = list(
                base_qs.values('safra_id', 'safra__safra', 'safra__ano')
                .annotate(total=Sum('valor_total'))
                .order_by('-safra__ano', '-safra_id')
            )[:4]
            pedido_ids_base = list(base_qs.exclude(pedido_id__isnull=True).values_list('pedido_id', flat=True).distinct())
            pedido_safra_map = {
                (r.get('safra_id') or 0): (r.get('total') or Decimal('0'))
                for r in (
                    PedidoCompra.objects
                    .filter(pk__in=pedido_ids_base)
                    .values('safra_id')
                    .annotate(total=Sum('valor_total'))
                )
            }
            safra_fat_rows = list(reversed(safra_fat_rows))
            max_fat = max([(r.get('total') or Decimal('0')) for r in safra_fat_rows], default=Decimal('0'))
            for r in safra_fat_rows:
                sid = r.get('safra_id') or 0
                fat = Decimal(r.get('total') or 0)
                ped = Decimal(pedido_safra_map.get(sid) or 0)
                pct = int((fat * Decimal('100') / max_fat).quantize(Decimal('1'))) if max_fat > 0 else 0
                pct_pf = (fat * Decimal('100') / ped).quantize(Decimal('0.1')) if ped > 0 else Decimal('0.0')
                rows_safra.append(
                    {
                        'label': r.get('safra__safra') or 'Sem safra',
                        'ano': r.get('safra__ano'),
                        'valor': fat,
                        'faturado_valor': fat,
                        'pedido_valor': ped,
                        'pct': pct,
                        'pct_pf': pct_pf,
                    }
                )
        except Exception:
            rows_safra = _to_rows(chart_safra_cultura)[:4]
        context['chart_faturamento_safra_cultura'] = rows_safra

        # Subtitulo com resumo dos filtros ativos
        filtros_resumo = []
        try:
            if context.get('filtro_cliente'):
                c = Cliente.objects.filter(pk=context['filtro_cliente']).only('cliente').first()
                filtros_resumo.append(f"Cliente: {c.cliente if c else context['filtro_cliente']}")
            if context.get('filtro_produtor_ids'):
                produtores_sel = list(
                    Produtor.objects.filter(pk__in=context['filtro_produtor_ids']).only('produtor', 'fazenda').order_by('produtor', 'fazenda')
                )
                nomes = [
                    ((p.apelido or p.produtor) + (f" - {p.fazenda}" if p.fazenda else ''))
                    for p in produtores_sel
                ]
                filtros_resumo.append(f"Produtor: {', '.join(nomes)}")
            if context.get('filtro_fornecedor'):
                f = Fornecedor.objects.filter(pk=context['filtro_fornecedor']).only('fornecedor').first()
                filtros_resumo.append(f"Fornecedor: {str(f) if f else context['filtro_fornecedor']}")
            if context.get('filtro_pedido'):
                filtros_resumo.append(f"Pedido: {context['filtro_pedido']}")
            if context.get('filtro_nota_fiscal'):
                filtros_resumo.append(f"Nota Fiscal: {context['filtro_nota_fiscal']}")
            if context.get('filtro_produto'):
                filtros_resumo.append(f"Produto: {context['filtro_produto']}")
            if context.get('filtro_status'):
                filtros_resumo.append(f"Status: {context['filtro_status']}")
            if context.get('filtro_data_ini') or context.get('filtro_data_fim'):
                de = context.get('filtro_data_ini') or '-'
                ate = context.get('filtro_data_fim') or '-'
                filtros_resumo.append(f"Periodo Data: {de} ate {ate}")
            if context.get('filtro_venc_ini') or context.get('filtro_venc_fim'):
                de = context.get('filtro_venc_ini') or '-'
                ate = context.get('filtro_venc_fim') or '-'
                filtros_resumo.append(f"Periodo Vencimento: {de} ate {ate}")
        except Exception:
            pass
        context['filtros_resumo'] = filtros_resumo

        # Totais por grupo (Safra e Safra+Cliente) para cabecalhos da lista.
        try:
            safra_totais_map = {
                (r.get('safra_id') or 0): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id').annotate(total=Sum('valor_total'))
            }
            cliente_totais_map = {
                ((r.get('safra_id') or 0), (r.get('cliente_id') or 0)): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id', 'cliente_id').annotate(total=Sum('valor_total'))
            }
            produtor_totais_map = {
                (
                    (r.get('safra_id') or 0),
                    (r.get('cliente_id') or 0),
                    (r.get('produtor_id') or 0),
                ): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id', 'cliente_id', 'produtor_id').annotate(total=Sum('valor_total'))
            }
            fornecedor_totais_map = {
                (
                    (r.get('safra_id') or 0),
                    (r.get('cliente_id') or 0),
                    (r.get('produtor_id') or 0),
                    (r.get('fornecedor_id') or 0),
                ): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id', 'cliente_id', 'produtor_id', 'fornecedor_id').annotate(total=Sum('valor_total'))
            }
            for o in list(context.get('object_list') or []):
                sid = getattr(o, 'safra_id', None) or 0
                cid = getattr(o, 'cliente_id', None) or 0
                pid = getattr(o, 'produtor_id', None) or 0
                fid = getattr(o, 'fornecedor_id', None) or 0
                setattr(o, 'grupo_safra_total', safra_totais_map.get(sid, Decimal('0')))
                setattr(o, 'grupo_cliente_total', cliente_totais_map.get((sid, cid), Decimal('0')))
                setattr(o, 'grupo_produtor_total', produtor_totais_map.get((sid, cid, pid), Decimal('0')))
                setattr(o, 'grupo_fornecedor_total', fornecedor_totais_map.get((sid, cid, pid, fid), Decimal('0')))
        except Exception:
            pass

        # Linhas por produto (lista estilo Pedido: 1 linha por item da nota)
        try:
            rows = list(context.get('object_list') or [])
            fat_ids = [o.pk for o in rows if getattr(o, 'pk', None)]
            itens_map = defaultdict(list)
            if fat_ids:
                for it in (
                    FaturamentoItem.objects
                    .filter(faturamento_id__in=fat_ids)
                    .select_related('produto_cadastro')
                    .order_by('id')
                ):
                    nome_prod = ''
                    if it.produto_cadastro_id:
                        nome_prod = (it.produto_cadastro.nome or '').strip()
                    if not nome_prod:
                        nome_prod = (it.produto or '').strip() or '-'
                    itens_map[it.faturamento_id].append(
                        {
                            'produto': nome_prod,
                            'quantidade': it.quantidade or Decimal('0'),
                            'preco': it.preco or Decimal('0'),
                            'valor': it.total_item or Decimal('0'),
                        }
                    )
            for o in rows:
                linhas = itens_map.get(o.pk) or []
                if not linhas:
                    linhas = [{'produto': '-', 'quantidade': Decimal('0'), 'preco': Decimal('0'), 'valor': Decimal('0')}]
                setattr(o, 'row_itens', linhas)
        except Exception:
            pass

        # Grafico em linha com labels: faturamento por dia/mes/ano no periodo real de faturamento
        chart_mensal = []
        chart_line_points = ''
        chart_periodo = None
        chart_total_periodo = Decimal('0')
        chart_total_pedido_periodo = Decimal('0')
        chart_periodo_escala = (self.request.GET.get('periodo_escala') or 'mes').strip().lower()
        if chart_periodo_escala not in ('dia', 'mes', 'ano'):
            chart_periodo_escala = 'mes'
        try:
            # Janela dinamica: primeira/ultima data da base filtrada
            # (usa data da nota e, se vazia, vencimento).
            faixa = qs_base.aggregate(
                ini=Min(Coalesce('data', 'vencimento')),
                fim=Max(Coalesce('data', 'vencimento')),
            )
            ini = faixa.get('ini')
            fim = faixa.get('fim')
            if ini and fim:
                chart_periodo = {'inicio': ini, 'fim': fim}
                base_qs = qs_base.filter(
                    Q(data__gte=ini, data__lte=fim) | Q(data__isnull=True, vencimento__gte=ini, vencimento__lte=fim)
                )
                totais_map = defaultdict(lambda: Decimal('0'))
                ped_map = defaultdict(lambda: Decimal('0'))
                forn_totals = defaultdict(lambda: defaultdict(lambda: Decimal('0')))

                for r in base_qs.values(
                    'data',
                    'vencimento',
                    'valor_total',
                    'pedido__valor_total',
                    'fornecedor__fornecedor',
                ):
                    ev = r.get('data') or r.get('vencimento')
                    if not ev:
                        continue
                    if chart_periodo_escala == 'dia':
                        key = ev
                    elif chart_periodo_escala == 'ano':
                        key = int(ev.year)
                    else:
                        key = (int(ev.year), int(ev.month))

                    fat_val = Decimal(r.get('valor_total') or 0)
                    ped_val = Decimal(r.get('pedido__valor_total') or 0)
                    forn_nome = (r.get('fornecedor__fornecedor') or 'Sem fornecedor')

                    totais_map[key] += fat_val
                    ped_map[key] += ped_val
                    forn_totals[key][forn_nome] += fat_val

                forn_map = defaultdict(list)
                for key, by_forn in forn_totals.items():
                    rows = sorted(by_forn.items(), key=lambda x: x[0])
                    for nome, total_f in rows:
                        total_txt = f"{total_f:.2f}".replace('.', ',')
                        forn_map[key].append(f"{nome}: {total_txt}")

                meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
                serie = []
                if chart_periodo_escala == 'dia':
                    cursor = ini
                    end_cursor = fim
                elif chart_periodo_escala == 'ano':
                    cursor = date(ini.year, 1, 1)
                    end_cursor = date(fim.year, 1, 1)
                else:
                    cursor = date(ini.year, ini.month, 1)
                    end_cursor = date(fim.year, fim.month, 1)

                while cursor <= end_cursor:
                    if chart_periodo_escala == 'dia':
                        key = cursor
                        label = cursor.strftime('%d/%m')
                        cursor = cursor + timedelta(days=1)
                    elif chart_periodo_escala == 'ano':
                        key = cursor.year
                        label = str(cursor.year)
                        cursor = date(cursor.year + 1, 1, 1)
                    else:
                        key = (cursor.year, cursor.month)
                        label = f"{meses_pt[cursor.month - 1]}/{str(cursor.year)[-2:]}"
                        if cursor.month == 12:
                            cursor = date(cursor.year + 1, 1, 1)
                        else:
                            cursor = date(cursor.year, cursor.month + 1, 1)

                    valor = totais_map.get(key, Decimal('0'))
                    valor_pedido = ped_map.get(key, Decimal('0'))
                    chart_total_periodo += valor
                    chart_total_pedido_periodo += valor_pedido
                    forn_rows = forn_map.get(key, [])
                    tooltip = f"Total Faturado por Fornecedor ({label}): "
                    tooltip += " | ".join(forn_rows) if forn_rows else "Sem fornecedores no periodo"
                    if valor_pedido > 0:
                        pct_pf = (valor * Decimal('100') / valor_pedido).quantize(Decimal('0.1'))
                    else:
                        pct_pf = Decimal('0.0')
                    serie.append(
                        {
                            'label': label,
                            'valor': valor,
                            'pedido_valor': valor_pedido,
                            'pct_pf': pct_pf,
                            'tooltip': tooltip,
                        }
                    )

                if serie:
                    # Exibe apenas periodos com movimentacao:
                    # - Dia: somente dias com movimento
                    # - Mes/Ano: do primeiro ao ultimo periodo com movimento
                    non_zero_idx = [
                        i for i, row in enumerate(serie)
                        if (row.get('valor') or Decimal('0')) > 0 or (row.get('pedido_valor') or Decimal('0')) > 0
                    ]
                    if non_zero_idx:
                        if chart_periodo_escala == 'dia':
                            serie = [
                                row for row in serie
                                if (row.get('valor') or Decimal('0')) > 0 or (row.get('pedido_valor') or Decimal('0')) > 0
                            ]
                        else:
                            i0 = non_zero_idx[0]
                            i1 = non_zero_idx[-1]
                            serie = serie[i0:i1 + 1]

                    total_periodo_tmp = sum([(r.get('valor') or Decimal('0')) for r in serie], Decimal('0'))
                    for r in serie:
                        r['pct_total_periodo'] = ((r.get('valor') or Decimal('0')) * Decimal('100') / total_periodo_tmp).quantize(Decimal('0.1')) if total_periodo_tmp > 0 else Decimal('0.0')
                    plot_w = Decimal('960')
                    plot_h = Decimal('280')
                    pad_x = Decimal('48')
                    pad_y_top = Decimal('26')
                    pad_y_bottom = Decimal('44')
                    inner_w = plot_w - (pad_x * 2)
                    inner_h = plot_h - (pad_y_top + pad_y_bottom)
                    max_val = max([(r.get('valor') or Decimal('0')) for r in serie], default=Decimal('0'))
                    count = len(serie)
                    step_x = (inner_w / Decimal(count - 1)) if count > 1 else Decimal('0')

                    pts = []
                    for i, r in enumerate(serie):
                        valor = (r.get('valor') or Decimal('0'))
                        if max_val > 0:
                            y = pad_y_top + (inner_h * (Decimal('1') - (valor / max_val)))
                        else:
                            y = pad_y_top + inner_h
                        x = pad_x + (step_x * Decimal(i))
                        x_i = int(x.quantize(Decimal('1')))
                        y_i = int(y.quantize(Decimal('1')))
                        pts.append(f'{x_i},{y_i}')
                        chart_mensal.append(
                            {
                                'label': r['label'],
                                'valor': valor,
                                'pedido_valor': r.get('pedido_valor') or Decimal('0'),
                                'pct_pf': r.get('pct_pf') or Decimal('0'),
                                'tooltip': r.get('tooltip') or '',
                                'x': x_i,
                                'y': y_i,
                            }
                        )
                    chart_line_points = ' '.join(pts)
        except Exception:
            chart_mensal = []
            chart_line_points = ''
            # Mantem o card visivel quando houver safra valida, mesmo em falha de agregacao.
            if not chart_periodo:
                try:
                    safra_id = context.get('filtro_safra') or topbar.get('filtro_safra')
                    safra_obj = Safra.objects.filter(pk=safra_id).only('data_inicio', 'data_fim').first() if safra_id else None
                    if safra_obj and safra_obj.data_inicio and safra_obj.data_fim:
                        chart_periodo = {'inicio': safra_obj.data_inicio, 'fim': safra_obj.data_fim}
                except Exception:
                    chart_periodo = None
            chart_total_periodo = Decimal('0')
            chart_total_pedido_periodo = Decimal('0')
        context['chart_faturamento_mensal'] = chart_mensal
        context['chart_faturamento_mensal_points'] = chart_line_points
        context['chart_faturamento_periodo'] = chart_periodo
        context['chart_periodo_escala'] = chart_periodo_escala
        context['chart_faturamento_total_periodo'] = chart_total_periodo
        context['chart_faturamento_total_pedido_periodo'] = chart_total_pedido_periodo
        if chart_total_pedido_periodo > 0:
            context['chart_faturamento_pct_periodo'] = (
                (chart_total_periodo * Decimal('100') / chart_total_pedido_periodo).quantize(Decimal('0.1'))
            )
        else:
            context['chart_faturamento_pct_periodo'] = Decimal('0.0')
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
            produto_field = prefix + 'produto_cadastro'
            produto_raw = (post.get(produto_field) or '').strip()
            if ':' in produto_raw:
                # UI uses composite key "pedido_item_id:produto_id" to allow
                # duplicated product entries with different unidade/preco.
                _, produto_id = produto_raw.split(':', 1)
                if produto_id.isdigit():
                    post[produto_field] = produto_id
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

    def _resolve_next_url(self, request):
        raw = request.POST.get('next') or request.GET.get('next')
        if not raw:
            raw = request.session.get('faturamentos_last_list_url') or str(self.success_url)
        decoded = unquote((raw or '').strip())
        if not decoded:
            return str(self.success_url)
        if url_has_allowed_host_and_scheme(decoded, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return decoded
        if decoded.startswith('/'):
            return decoded
        return str(self.success_url)

    def get_context_data(self, form, formset):
        return {
            'form': form,
            'formset': formset,
            'titulo': 'Faturamento',
            'modo_edicao': bool(getattr(self, 'object', None)),
            'next_url': self._resolve_next_url(self.request),
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
        next_url = self._resolve_next_url(request)

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
            return redirect(next_url)

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
        next_url = self._resolve_next_url(request)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                faturamento = form.save(commit=False)
                if faturamento.valor_total is None:
                    faturamento.valor_total = 0
                faturamento.save()
                formset.save()
                self._recalcular_totais(faturamento)
            messages.success(request, 'Faturamento atualizado com sucesso.')
            return redirect(next_url)

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

    base_qs = PedidoCompra.objects.filter(safra_id=int(safra_id), produtor_id=int(produtor_id))
    qs = base_qs.filter(saldo_faturar__gt=0)

    if getattr(request.user, 'effective_role', '') != 'ADMIN':
        cliente = _get_cliente_do_usuario(request.user)
        if cliente:
            qs = qs.filter(cliente_id=cliente.pk)
            base_qs = base_qs.filter(cliente_id=cliente.pk)

    # Em edicao de faturamento, manter o pedido ja vinculado mesmo com saldo zerado.
    current_pedido_id = (request.GET.get('current_pedido_id') or '').strip()
    if current_pedido_id.isdigit():
        qs = (qs | base_qs.filter(pk=int(current_pedido_id))).distinct()

    qs = qs.order_by('-data', '-id')
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
    restante_por_produto = dict(faturado_map)

    itens_payload = []
    for it in pedido.itens.select_related('produto_cadastro', 'unidade').all():
        if not it.produto_cadastro_id:
            continue
        ja_prod = restante_por_produto.get(it.produto_cadastro_id) or Decimal('0')
        qtd_item = (it.quantidade or Decimal('0'))
        saldo = qtd_item - ja_prod
        if saldo <= 0:
            restante_por_produto[it.produto_cadastro_id] = max(Decimal('0'), ja_prod - qtd_item)
            continue
        restante_por_produto[it.produto_cadastro_id] = Decimal('0')
        itens_payload.append(
            {
                'item_key': f"{it.pk}:{it.produto_cadastro_id}",
                'pedido_item_id': it.pk,
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

    def get(self, request, *args, **kwargs):
        restored = _panel_filters_restore_or_save(request, 'contas')
        if restored is not None:
            return redirect(f'{request.path}?{restored.urlencode()}')
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        # Remove faturas de origem PEDIDO quando o saldo esta zerado.
        # Isso evita manter o documento do pedido quando ele ja foi totalmente substituido por notas fiscais.
        ContaPagar.objects.filter(origem=ContaPagar.Origem.PEDIDO, saldo_aberto__lte=0).delete()

        sort_map = {
            'status': 'status',
            'data': 'data',
            'nota_fiscal': 'nota_fiscal',
            'pedido': 'pedido__pedido',
            'safra': 'safra__safra',
            'atraso': 'vencimento',
            'produtor': 'produtor__produtor',
            'fornecedor': 'fornecedor__fornecedor',
            'quantidade': 'quantidade',
            'preco': 'preco',
            'valor_total': 'valor_total',
        }
        current_sort = (self.request.GET.get('o') or '').strip()
        sort_field = current_sort[1:] if current_sort.startswith('-') else current_sort
        sort_desc = current_sort.startswith('-')
        if sort_field in sort_map:
            _ord = sort_map[sort_field]
            order_by = [f'-{_ord}' if sort_desc else _ord, '-id']
        else:
            order_by = ['vencimento', 'id']

        qs = (
            super()
            .get_queryset()
            .select_related('cliente', 'produtor', 'pedido', 'safra', 'fornecedor', 'custo', 'faturamento')
            .prefetch_related('faturamento__itens__produto_cadastro')
            .order_by(*order_by)
        )
        topbar = _get_topbar_state(self.request, scope='contas', default_cultura=_default_cultura_soja_id())
        topbar_cultura = topbar['filtro_cultura']
        topbar_safra = topbar['filtro_safra']
        if topbar_cultura:
            qs = qs.filter(safra__cultura_id=topbar_cultura)
        if topbar_safra:
            qs = qs.filter(safra_id=topbar_safra)
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'cultura', 'safra', 'categoria', 'cliente', 'produtor', 'fornecedor', 'pedido', 'nota_fiscal', 'status', 'venc_ini', 'venc_fim'],
        )
        if not filtros_ativos:
            return qs

        q = _normalize_filter_value(self.request.GET.get('q'))
        status = _normalize_filter_value(self.request.GET.get('status'))
        safra_id = _selected_get_value(self.request, 'safra') or topbar_safra
        cultura_id = _selected_get_value(self.request, 'cultura') or topbar_cultura
        categoria_id = _normalize_filter_value(self.request.GET.get('categoria'))
        cliente_id = _normalize_filter_value(self.request.GET.get('cliente'))
        produtor_ids = []
        for raw_val in self.request.GET.getlist('produtor'):
            v = _normalize_filter_value(raw_val)
            if v and v not in produtor_ids:
                produtor_ids.append(v)
        if not produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                produtor_ids = [single_produtor]
        fornecedor_id = _normalize_filter_value(self.request.GET.get('fornecedor'))
        pedido_txt = _normalize_filter_value(self.request.GET.get('pedido'))
        nota_fiscal = _normalize_filter_value(self.request.GET.get('nota_fiscal'))
        venc_ini = _normalize_filter_value(self.request.GET.get('venc_ini'))
        venc_fim = _normalize_filter_value(self.request.GET.get('venc_fim'))

        today = date.today()

        if q:
            qs = qs.filter(
                Q(nota_fiscal__icontains=q)
                | Q(cliente__cliente__icontains=q)
                | Q(produtor__produtor__icontains=q)
                | Q(fornecedor__fornecedor__icontains=q)
                | Q(pedido__pedido__icontains=q)
                | Q(origem__icontains=q)
            )

        if status:
            if status == 'VENCIDO':
                qs = qs.filter(pago=False, saldo_aberto__gt=0, vencimento__lt=today)
            elif status == 'PAGO':
                qs = qs.filter(status=ContaPagar.Status.PAGO)
            elif status in {ContaPagar.Status.A_PAGAR, ContaPagar.Status.PARCIAL}:
                qs = qs.filter(status=status)

        if safra_id:
            qs = qs.filter(safra_id=safra_id)
        if cultura_id:
            qs = qs.filter(safra__cultura_id=cultura_id)
        if categoria_id:
            qs = qs.filter(
                Q(pedido__itens__produto_cadastro__categoria_id=categoria_id) |
                Q(faturamento__itens__produto_cadastro__categoria_id=categoria_id)
            ).distinct()
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if produtor_ids:
            qs = qs.filter(produtor_id__in=produtor_ids)
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

    def _build_columns(self):
        cols = []
        req_order = (self.request.GET.get('o') or '').strip()
        active_field = req_order[1:] if req_order.startswith('-') else req_order
        is_desc = req_order.startswith('-')

        base_params = self.request.GET.copy()
        base_params.pop('page', None)

        for col in (self.columns or []):
            if isinstance(col, (list, tuple)) and len(col) >= 2:
                label, field = col[0], col[1]
            else:
                label, field = str(col), str(col)

            params = base_params.copy()
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        topbar = _get_topbar_state(self.request, scope='contas', default_cultura=_default_cultura_soja_id())
        filtros_ativos = _filters_active(
            self.request,
            ['q', 'cultura', 'safra', 'categoria', 'cliente', 'produtor', 'fornecedor', 'pedido', 'nota_fiscal', 'status', 'venc_ini', 'venc_fim'],
        )
        qs = self.get_queryset()
        today = date.today()

        def _sum(q):
            return q.aggregate(total=Sum('saldo_aberto'))['total'] or 0

        total_faturado = qs.filter(origem=ContaPagar.Origem.FATURAMENTO).aggregate(total=Sum('valor_total'))['total'] or 0
        total_pago = qs.filter(status=ContaPagar.Status.PAGO).aggregate(total=Sum('valor_total'))['total'] or 0
        total_vencido = _sum(qs.filter(pago=False, saldo_aberto__gt=0, vencimento__lt=today))
        # "A pagar" precisa refletir todo saldo em aberto (pendente + vencido).
        total_a_pagar = _sum(qs.filter(pago=False, saldo_aberto__gt=0))

        context['filtro_status'] = _normalize_filter_value(self.request.GET.get('status'))
        context['filtro_cultura'] = _selected_get_value(self.request, 'cultura') or topbar['filtro_cultura']
        context['filtro_safra'] = _selected_get_value(self.request, 'safra') or topbar['filtro_safra']
        context['filtro_categoria'] = _normalize_filter_value(self.request.GET.get('categoria'))
        context['filtro_cliente'] = _normalize_filter_value(self.request.GET.get('cliente'))
        filtro_produtor_ids = []
        for raw_val in self.request.GET.getlist('produtor'):
            v = _normalize_filter_value(raw_val)
            if v and v not in filtro_produtor_ids:
                filtro_produtor_ids.append(v)
        if not filtro_produtor_ids:
            single_produtor = _normalize_filter_value(self.request.GET.get('produtor'))
            if single_produtor:
                filtro_produtor_ids = [single_produtor]
        context['filtro_produtor_ids'] = filtro_produtor_ids
        context['filtro_produtor'] = filtro_produtor_ids[0] if len(filtro_produtor_ids) == 1 else ''
        context['filtro_fornecedor'] = _normalize_filter_value(self.request.GET.get('fornecedor'))
        context['filtro_pedido'] = _normalize_filter_value(self.request.GET.get('pedido'))
        context['filtro_nota_fiscal'] = _normalize_filter_value(self.request.GET.get('nota_fiscal'))
        context['filtro_venc_ini'] = _normalize_filter_value(self.request.GET.get('venc_ini'))
        context['filtro_venc_fim'] = _normalize_filter_value(self.request.GET.get('venc_fim'))
        try:
            context['filtro_cliente_label'] = (
                Cliente.objects.filter(pk=context['filtro_cliente']).values_list('cliente', flat=True).first()
                if context.get('filtro_cliente') else ''
            ) or ''
            context['filtro_fornecedor_label'] = (
                Fornecedor.objects.filter(pk=context['filtro_fornecedor']).values_list('fornecedor', flat=True).first()
                if context.get('filtro_fornecedor') else ''
            ) or ''
            produtores_sel = list(
                Produtor.objects.filter(pk__in=filtro_produtor_ids).only('produtor', 'fazenda').order_by('produtor', 'fazenda')
            ) if filtro_produtor_ids else []
            context['filtro_produtor_labels'] = [
                ((p.apelido or p.produtor) + (f" - {p.fazenda}" if p.fazenda else '')).strip()
                for p in produtores_sel
                if (p.produtor or '').strip()
            ]
        except Exception:
            context['filtro_cliente_label'] = ''
            context['filtro_fornecedor_label'] = ''
            context['filtro_produtor_labels'] = []

        context['current_q'] = _normalize_filter_value(self.request.GET.get('q'))
        context['current_sort'] = (self.request.GET.get('o') or '').strip()

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
        context['culturas'] = topbar['culturas']
        context['safras'] = topbar['safras']
        context['safras_topbar'] = topbar['safras_topbar']
        context['clientes'] = Cliente.objects.all().order_by('cliente')
        context['produtores'] = Produtor.objects.select_related('cliente').all().order_by('produtor', 'fazenda')
        context['fornecedores'] = Fornecedor.objects.all().order_by('fornecedor')
        context['categorias'] = Categoria.objects.all().order_by('nome')

        context['card_total_faturado'] = total_faturado
        context['card_total_a_pagar'] = total_a_pagar
        context['card_total_vencido'] = total_vencido
        context['card_total_pago'] = total_pago
        context['total_registros'] = qs.count()

        # Graficos - Contas/Faturas
        try:
            ids_base = list(qs.values_list('id', flat=True).distinct())
            qs_base = ContaPagar.objects.filter(pk__in=ids_base).select_related('safra', 'fornecedor')
            today = date.today()
            escala = (_normalize_filter_value(self.request.GET.get('periodo_escala')) or 'mes').lower()
            if escala not in {'dia', 'mes', 'ano'}:
                escala = 'mes'
            context['chart_periodo_escala'] = escala

            def _status_bucket(conta):
                if (not conta.pago) and (conta.saldo_aberto or 0) > 0 and conta.vencimento and conta.vencimento < today:
                    return 'vencido'
                if conta.pago or conta.status == ContaPagar.Status.PAGO or (conta.saldo_aberto or 0) <= 0:
                    return 'pago'
                return 'pendente'

            # Periodo por Vencimento (usa vencimento real, sem limitar ao periodo da safra)
            periodo_rows = []
            venc_rows = list(qs_base.filter(vencimento__isnull=False).values_list('vencimento', flat=True))
            if venc_rows:
                ini = min(venc_rows)
                fim = max(venc_rows)
                context['chart_conta_periodo'] = {'inicio': ini, 'fim': fim}
                periodo_map = defaultdict(lambda: {'pendente': Decimal('0'), 'vencido': Decimal('0'), 'pago': Decimal('0')})
                for c in qs_base.filter(vencimento__isnull=False):
                    if escala == 'dia':
                        key = c.vencimento
                        label = c.vencimento.strftime('%d/%m')
                    elif escala == 'ano':
                        key = c.vencimento.year
                        label = str(c.vencimento.year)
                    else:
                        key = (c.vencimento.year, c.vencimento.month)
                        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
                        label = f"{meses_pt[c.vencimento.month - 1]}/{str(c.vencimento.year)[-2:]}"
                    bucket = _status_bucket(c)
                    periodo_map[key]['label'] = label
                    periodo_map[key][bucket] += Decimal(c.valor_total or 0)
                for key, vals in periodo_map.items():
                    if (vals['pendente'] + vals['vencido'] + vals['pago']) <= 0:
                        continue
                    periodo_rows.append({
                        'label': vals['label'],
                        'pendente': vals['pendente'],
                        'vencido': vals['vencido'],
                        'pago': vals['pago'],
                        'total': vals['pendente'] + vals['vencido'] + vals['pago'],
                    })
                if escala == 'ano':
                    periodo_rows.sort(key=lambda r: int(r['label']))
                elif escala == 'mes':
                    def _mes_idx(lbl):
                        try:
                            m = lbl.split('/')[0]
                            mapa = {'Jan':1,'Fev':2,'Mar':3,'Abr':4,'Mai':5,'Jun':6,'Jul':7,'Ago':8,'Set':9,'Out':10,'Nov':11,'Dez':12}
                            a = int(lbl.split('/')[1])
                            return (a, mapa.get(m, 0))
                        except Exception:
                            return (0, 0)
                    periodo_rows.sort(key=lambda r: _mes_idx(r['label']))
                else:
                    periodo_rows.sort(key=lambda r: r['label'])

                # Plot (Line, Column & Area): pago (linha), pendente (area), vencido (coluna)
                if periodo_rows:
                    total_pendente_periodo = sum([Decimal(r.get('pendente') or 0) for r in periodo_rows], Decimal('0'))
                    total_vencido_periodo = sum([Decimal(r.get('vencido') or 0) for r in periodo_rows], Decimal('0'))
                    total_pago_periodo = sum([Decimal(r.get('pago') or 0) for r in periodo_rows], Decimal('0'))
                    plot_w = Decimal('960')
                    plot_h = Decimal('280')
                    pad_x = Decimal('48')
                    pad_y_top = Decimal('24')
                    pad_y_bottom = Decimal('44')
                    inner_w = plot_w - (pad_x * 2)
                    inner_h = plot_h - (pad_y_top + pad_y_bottom)
                    max_val = max(
                        [
                            max(
                                Decimal(r.get('pendente') or 0),
                                Decimal(r.get('vencido') or 0),
                                Decimal(r.get('pago') or 0),
                            )
                            for r in periodo_rows
                        ],
                        default=Decimal('0'),
                    )
                    count = len(periodo_rows)
                    step_x = (inner_w / Decimal(max(count - 1, 1)))
                    bar_w = Decimal('20') if count <= 24 else Decimal('12')

                    area_pts = []
                    line_pts = []
                    bars = []
                    labels = []
                    pendente_points = []
                    pago_points = []
                    total_points = []
                    total_geral_periodo = total_pendente_periodo + total_vencido_periodo + total_pago_periodo

                    for i, r in enumerate(periodo_rows):
                        x = pad_x + (step_x * Decimal(i))
                        pend = Decimal(r.get('pendente') or 0)
                        venc = Decimal(r.get('vencido') or 0)
                        pago = Decimal(r.get('pago') or 0)

                        if max_val > 0:
                            y_pend = pad_y_top + (inner_h * (Decimal('1') - (pend / max_val)))
                            y_pago = pad_y_top + (inner_h * (Decimal('1') - (pago / max_val)))
                            y_venc_top = pad_y_top + (inner_h * (Decimal('1') - (venc / max_val)))
                        else:
                            y_pend = y_pago = y_venc_top = pad_y_top + inner_h

                        x_i = int(x.quantize(Decimal('1')))
                        y_pend_i = int(y_pend.quantize(Decimal('1')))
                        y_pago_i = int(y_pago.quantize(Decimal('1')))
                        y_venc_top_i = int(y_venc_top.quantize(Decimal('1')))
                        base_y_i = int((pad_y_top + inner_h).quantize(Decimal('1')))
                        bar_h_i = max(0, base_y_i - y_venc_top_i)

                        area_pts.append(f"{x_i},{y_pend_i}")
                        line_pts.append(f"{x_i},{y_pago_i}")
                        bars.append(
                            {
                                'x': int((x - (bar_w / 2)).quantize(Decimal('1'))),
                                'y': y_venc_top_i,
                                'w': int(bar_w),
                                'h': bar_h_i,
                                'label': r.get('label'),
                                'valor': venc,
                                'pct': ((venc * Decimal('100') / total_vencido_periodo).quantize(Decimal('0.1')) if total_vencido_periodo > 0 else Decimal('0.0')),
                            }
                        )
                        labels.append({'x': x_i, 'label': r.get('label')})
                        total_pt = pend + venc + pago
                        y_total_anchor_i = min(y_pend_i, y_pago_i, y_venc_top_i)
                        total_points.append({
                            'x': x_i,
                            'y': max(int(pad_y_top), y_total_anchor_i - 10),
                            'label': r.get('label'),
                            'valor': total_pt,
                            'pct': ((total_pt * Decimal('100') / total_geral_periodo).quantize(Decimal('0.1')) if total_geral_periodo > 0 else Decimal('0.0')),
                        })
                        pendente_points.append({
                            'x': x_i,
                            'y': y_pend_i,
                            'label': r.get('label'),
                            'valor': pend,
                            'pct': ((pend * Decimal('100') / total_pendente_periodo).quantize(Decimal('0.1')) if total_pendente_periodo > 0 else Decimal('0.0')),
                        })
                        pago_points.append({
                            'x': x_i,
                            'y': y_pago_i,
                            'label': r.get('label'),
                            'valor': pago,
                            'pct': ((pago * Decimal('100') / total_pago_periodo).quantize(Decimal('0.1')) if total_pago_periodo > 0 else Decimal('0.0')),
                        })

                    base_y = int((pad_y_top + inner_h).quantize(Decimal('1')))
                    area_points = f"{int(pad_x)},{base_y} " + " ".join(area_pts) + f" {int((pad_x + inner_w).quantize(Decimal('1')))},{base_y}"

                    context['chart_conta_periodo_plot'] = {
                        'area_points': area_points,
                        'line_points': " ".join(line_pts),
                        'bars': bars,
                        'labels': labels,
                        'pendente_points': pendente_points,
                        'pago_points': pago_points,
                        'total_points': total_points,
                    }
            else:
                context['chart_conta_periodo'] = None
                context['chart_conta_periodo_plot'] = None

            # Fornecedor (barras por status)
            chart_fornecedor = []
            rows_f = (
                qs_base.values('fornecedor__fantasia', 'fornecedor__fornecedor')
                .annotate(
                    pendente=Sum(Case(
                        When(pago=False, saldo_aberto__gt=0, vencimento__gte=today, then=F('valor_total')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )),
                    vencido=Sum(Case(
                        When(pago=False, saldo_aberto__gt=0, vencimento__lt=today, then=F('valor_total')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )),
                    pago=Sum(Case(
                        When(Q(pago=True) | Q(status=ContaPagar.Status.PAGO) | Q(saldo_aberto__lte=0), then=F('valor_total')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )),
                )
            )
            max_forn = Decimal('0')
            for r in rows_f:
                label = (r.get('fornecedor__fantasia') or '').strip() or (r.get('fornecedor__fornecedor') or 'Sem fornecedor')
                p = Decimal(r.get('pendente') or 0)
                v = Decimal(r.get('vencido') or 0)
                g = Decimal(r.get('pago') or 0)
                t = p + v + g
                if t <= 0:
                    continue
                max_forn = max(max_forn, p, v, g)
                chart_fornecedor.append({'label': label, 'pendente': p, 'vencido': v, 'pago': g, 'total': t})
            chart_fornecedor.sort(key=lambda x: x['total'], reverse=True)
            chart_fornecedor = chart_fornecedor[:12]
            for r in chart_fornecedor:
                if max_forn > 0:
                    r['pct_pendente'] = int((r['pendente'] * Decimal('100') / max_forn).quantize(Decimal('1')))
                    r['pct_vencido'] = int((r['vencido'] * Decimal('100') / max_forn).quantize(Decimal('1')))
                    r['pct_pago'] = int((r['pago'] * Decimal('100') / max_forn).quantize(Decimal('1')))
                else:
                    r['pct_pendente'] = r['pct_vencido'] = r['pct_pago'] = 0

            # Safra (pendente e vencido)
            chart_safra = []
            rows_s = (
                qs_base.values('safra__safra')
                .annotate(
                    pendente=Sum(Case(
                        When(pago=False, saldo_aberto__gt=0, vencimento__gte=today, then=F('valor_total')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )),
                    vencido=Sum(Case(
                        When(pago=False, saldo_aberto__gt=0, vencimento__lt=today, then=F('valor_total')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )),
                )
            )
            max_saf = Decimal('0')
            for r in rows_s:
                label = r.get('safra__safra') or 'Sem safra'
                p = Decimal(r.get('pendente') or 0)
                v = Decimal(r.get('vencido') or 0)
                t = p + v
                if t <= 0:
                    continue
                max_saf = max(max_saf, p, v)
                chart_safra.append({'label': label, 'pendente': p, 'vencido': v, 'total': t})
            chart_safra.sort(key=lambda x: x['total'], reverse=True)
            chart_safra = chart_safra[:6]
            for r in chart_safra:
                if max_saf > 0:
                    r['pct_pendente'] = int((r['pendente'] * Decimal('100') / max_saf).quantize(Decimal('1')))
                    r['pct_vencido'] = int((r['vencido'] * Decimal('100') / max_saf).quantize(Decimal('1')))
                else:
                    r['pct_pendente'] = r['pct_vencido'] = 0

            # Categoria (barras por status)
            fat_ids = list(qs_base.filter(origem=ContaPagar.Origem.FATURAMENTO, faturamento_id__isnull=False).values_list('faturamento_id', flat=True))
            ped_ids = list(qs_base.filter(origem=ContaPagar.Origem.PEDIDO, pedido_id__isnull=False).values_list('pedido_id', flat=True))
            conta_by_fat = {c.faturamento_id: c for c in qs_base.filter(faturamento_id__in=fat_ids)}
            conta_by_ped = {c.pedido_id: c for c in qs_base.filter(pedido_id__in=ped_ids)}
            fat_rows = (
                FaturamentoItem.objects.filter(faturamento_id__in=fat_ids)
                .values('faturamento_id', 'produto_cadastro__categoria__nome')
                .annotate(total=Sum('total_item'))
            )
            ped_rows = (
                PedidoCompraItem.objects.filter(pedido_compra_id__in=ped_ids)
                .values('pedido_compra_id', 'produto_cadastro__categoria__nome')
                .annotate(total=Sum('total_item'))
            )
            fat_map = defaultdict(list)
            for r in fat_rows:
                fat_map[r['faturamento_id']].append((r.get('produto_cadastro__categoria__nome') or 'Sem categoria', Decimal(r.get('total') or 0)))
            ped_map = defaultdict(list)
            for r in ped_rows:
                ped_map[r['pedido_compra_id']].append((r.get('produto_cadastro__categoria__nome') or 'Sem categoria', Decimal(r.get('total') or 0)))
            cat_acc = defaultdict(lambda: {'pendente': Decimal('0'), 'vencido': Decimal('0'), 'pago': Decimal('0')})

            for fid, conta in conta_by_fat.items():
                itens = fat_map.get(fid) or [('Sem categoria', Decimal(conta.valor_total or 0))]
                soma = sum([v for _, v in itens], Decimal('0')) or Decimal(conta.valor_total or 0) or Decimal('1')
                bucket = _status_bucket(conta)
                total_conta = Decimal(conta.valor_total or 0)
                for cat, val in itens:
                    frac = (Decimal(val or 0) / soma) if soma > 0 else Decimal('0')
                    cat_acc[cat][bucket] += (total_conta * frac)

            for pid, conta in conta_by_ped.items():
                itens = ped_map.get(pid) or [('Sem categoria', Decimal(conta.valor_total or 0))]
                soma = sum([v for _, v in itens], Decimal('0')) or Decimal(conta.valor_total or 0) or Decimal('1')
                bucket = _status_bucket(conta)
                total_conta = Decimal(conta.valor_total or 0)
                for cat, val in itens:
                    frac = (Decimal(val or 0) / soma) if soma > 0 else Decimal('0')
                    cat_acc[cat][bucket] += (total_conta * frac)

            chart_categoria = []
            max_cat = Decimal('0')
            for cat, vals in cat_acc.items():
                p, v, g = vals['pendente'], vals['vencido'], vals['pago']
                t = p + v + g
                if t <= 0:
                    continue
                max_cat = max(max_cat, p, v, g)
                chart_categoria.append({'label': cat, 'pendente': p, 'vencido': v, 'pago': g, 'total': t})
            chart_categoria.sort(key=lambda x: x['total'], reverse=True)
            chart_categoria = chart_categoria[:12]
            for r in chart_categoria:
                if max_cat > 0:
                    r['pct_pendente'] = int((r['pendente'] * Decimal('100') / max_cat).quantize(Decimal('1')))
                    r['pct_vencido'] = int((r['vencido'] * Decimal('100') / max_cat).quantize(Decimal('1')))
                    r['pct_pago'] = int((r['pago'] * Decimal('100') / max_cat).quantize(Decimal('1')))
                else:
                    r['pct_pendente'] = r['pct_vencido'] = r['pct_pago'] = 0

            context['chart_conta_periodo_rows'] = periodo_rows
            context['chart_conta_categoria'] = chart_categoria
            context['chart_conta_fornecedor'] = chart_fornecedor
            context['chart_conta_safra'] = chart_safra
        except Exception:
            context['chart_conta_periodo'] = None
            context['chart_conta_periodo_rows'] = []
            context['chart_conta_periodo_plot'] = None
            context['chart_conta_categoria'] = []
            context['chart_conta_fornecedor'] = []
            context['chart_conta_safra'] = []

        # Totais por grupo para cabecalhos da lista (Safra/Cliente/Fornecedor/Vencimento)
        try:
            ids_base = list(qs.values_list('id', flat=True).distinct())
            qs_base = ContaPagar.objects.filter(pk__in=ids_base)

            safra_totais_map = {
                (r.get('safra_id') or 0): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id').annotate(total=Sum('valor_total'))
            }
            cliente_totais_map = {
                ((r.get('safra_id') or 0), (r.get('cliente_id') or 0)): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id', 'cliente_id').annotate(total=Sum('valor_total'))
            }
            fornecedor_totais_map = {
                ((r.get('safra_id') or 0), (r.get('cliente_id') or 0), (r.get('fornecedor_id') or 0)): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id', 'cliente_id', 'fornecedor_id').annotate(total=Sum('valor_total'))
            }
            venc_totais_map = {
                (
                    (r.get('safra_id') or 0),
                    (r.get('cliente_id') or 0),
                    (r.get('fornecedor_id') or 0),
                    r.get('vencimento'),
                ): (r.get('total') or Decimal('0'))
                for r in qs_base.values('safra_id', 'cliente_id', 'fornecedor_id', 'vencimento').annotate(total=Sum('valor_total'))
            }

            for o in list(context.get('object_list') or []):
                sid = getattr(o, 'safra_id', None) or 0
                cid = getattr(o, 'cliente_id', None) or 0
                fid = getattr(o, 'fornecedor_id', None) or 0
                ven = getattr(o, 'vencimento', None)
                setattr(o, 'grupo_safra_total', safra_totais_map.get(sid, Decimal('0')))
                setattr(o, 'grupo_cliente_total', cliente_totais_map.get((sid, cid), Decimal('0')))
                setattr(o, 'grupo_fornecedor_total', fornecedor_totais_map.get((sid, cid, fid), Decimal('0')))
                setattr(o, 'grupo_vencimento_total', venc_totais_map.get((sid, cid, fid, ven), Decimal('0')))
        except Exception:
            pass
        return context


class ContaPagarDetailView(GestorRequiredMixin, DetailView):
    model = ContaPagar
    template_name = 'core/contas/detail.html'


class ContaPagarCreateView(GestorRequiredMixin, CrudCreateView):
    model = ContaPagar
    form_class = ContaPagarForm
    template_name = 'core/contas/form.html'
    success_url = reverse_lazy('core:conta_list')


class ContaPagarUpdateView(GestorRequiredMixin, CrudUpdateView):
    model = ContaPagar
    form_class = ContaPagarForm
    template_name = 'core/contas/form.html'
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
    template_name = 'core/licencas/crud_list.html'
    context_title = 'Licenca de Uso, Suporte Tecnico e Manutencao'
    columns = [('Cliente', 'cliente'), ('Status', 'status'), ('Pagamento', 'data_pagamento'), ('Inicio', 'inicio_vigencia'), ('Fim', 'fim_vigencia')]
    create_url_name = 'core:licenca_create'
    edit_url_name = 'core:licenca_update'
    delete_url_name = 'core:licenca_delete'
    search_fields = ['cliente', 'cpf_cnpj', 'email']
    default_ordering = '-created_at'

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            perfil = getattr(self.request.user, 'perfil_licenca', None)
            lic = perfil.licenca if perfil else None
            if lic:
                qs = qs.filter(pk=lic.pk)
            else:
                qs = qs.none()
        return qs

class LicencaCreateView(GestorRequiredMixin, CrudCreateView):
    template_name = 'core/licencas/form_wizard.html'
    model = Licenca
    form_class = LicencaForm
    success_url = reverse_lazy('core:licenca_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['plano_cards'] = [
            {'value': Licenca.Plano.MENSAL, 'label': 'Mensal', 'descricao': '1 mes', 'valor': valor_mensal_plano()},
            {'value': Licenca.Plano.SEMESTRAL, 'label': 'Semestral', 'descricao': '6 meses', 'valor': valor_semestral()},
            {'value': Licenca.Plano.ANUAL, 'label': 'Anual', 'descricao': '12 meses', 'valor': valor_anual()},
        ]
        return ctx


class LicencaUpdateView(GestorRequiredMixin, CrudUpdateView):
    template_name = 'core/licencas/form_wizard.html'
    model = Licenca
    form_class = LicencaForm
    success_url = reverse_lazy('core:licenca_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['plano_cards'] = [
            {'value': Licenca.Plano.MENSAL, 'label': 'Mensal', 'descricao': '1 mes', 'valor': valor_mensal_plano()},
            {'value': Licenca.Plano.SEMESTRAL, 'label': 'Semestral', 'descricao': '6 meses', 'valor': valor_semestral()},
            {'value': Licenca.Plano.ANUAL, 'label': 'Anual', 'descricao': '12 meses', 'valor': valor_anual()},
        ]
        return ctx

    def dispatch(self, request, *args, **kwargs):
        if getattr(request.user, 'effective_role', '') != 'ADMIN':
            messages.error(request, 'Apenas o administrador pode editar assinaturas/faturas.')
            return redirect('core:licencas_page')
        lic = self.get_object()
        return super().dispatch(request, *args, **kwargs)


class LicencaDeleteView(GestorRequiredMixin, CrudDeleteView):
    template_name = 'core/licencas/confirm_delete_modal.html'
    model = Licenca
    success_url = reverse_lazy('core:licenca_list')

    def dispatch(self, request, *args, **kwargs):
        if getattr(request.user, 'effective_role', '') != 'ADMIN':
            messages.error(request, 'Apenas o administrador pode excluir assinaturas/faturas.')
            return redirect('core:licencas_page')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        ok, detalhe = excluir_licenca_sincronizada(self.object)
        if not ok:
            messages.error(request, f'Nao foi possivel excluir a assinatura. {detalhe}')
            return redirect('core:licencas_page')
        messages.success(request, 'Assinatura removida com sucesso.')
        return redirect(self.get_success_url())

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


