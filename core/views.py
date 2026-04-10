
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Avg, Exists, OuterRef
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import urlencode
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
from compras.models import CotacaoProduto, PedidoCompra, PedidoCompraItem, StatusPedidoCompra
from financeiro.models import (
    ContaPagar,
    Faturamento,
    FaturamentoItem,
    FormaPagamento as FormaPagamentoFinanceiro,
    PagamentoContaPagar,
)
from licencas.models import Licenca, PerfilUsuarioLicenca

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
def dashboard(request):
    user = request.user
    role = getattr(user, 'effective_role', None)

    if role == 'ADMIN':
        cards = [
            {'label': 'Clientes', 'value': Cliente.objects.count()},
            {'label': 'Safras', 'value': Safra.objects.count()},
            {'label': 'Pedidos de Compra', 'value': PedidoCompra.objects.count()},
            {'label': 'Faturamentos', 'value': Faturamento.objects.count()},
            {'label': 'Contas Pendentes', 'value': ContaPagar.objects.pendentes().count()},
            {'label': 'Licencas Ativas', 'value': Licenca.objects.filter(status=Licenca.Status.ATIVA).count()},
        ]
        return render(
            request,
            'core/dashboard.html',
            {
                'titulo': 'Visao Geral do ERP',
                'subtitulo': 'Resumo operacional consolidado',
                'cards': cards,
                'role_display': user.get_effective_role_display(),
                'modulos': [
                    {'titulo': 'Cadastros', 'descricao': 'Dados mestres do sistema', 'url': '/app/cadastros/'},
                    {'titulo': 'Compras', 'descricao': 'Pedidos e cotacoes', 'url': '/app/compras/'},
                    {'titulo': 'Financeiro', 'descricao': 'Faturamento e contas a pagar', 'url': '/app/financeiro/'},
                    {'titulo': 'Licencas', 'descricao': 'Assinatura e vigencia', 'url': '/app/licencas/'},
                ],
            },
        )

    # Supervisor/Usuario: nao depende do cadastro administrativo de 'Cliente'.
    # O controle de acesso e feito por licenca (middleware) e permissoes por perfil.
    perfil_licenca = getattr(user, 'perfil_licenca', None)
    licenca = perfil_licenca.licenca if perfil_licenca else None

    pedidos_qs = PedidoCompra.objects.all()
    faturamentos_qs = Faturamento.objects.all()

    total_compras = pedidos_qs.aggregate(total=Sum('valor_total'))['total'] or 0
    total_faturado = faturamentos_qs.aggregate(total=Sum('valor_total'))['total'] or 0

    cards = [
        {'label': 'Pedidos', 'value': pedidos_qs.count()},
        {'label': 'Faturamentos', 'value': faturamentos_qs.count()},
        {'label': 'Total Compras', 'value': _format_brl(total_compras)},
        {'label': 'Total Faturado', 'value': _format_brl(total_faturado)},
    ]

    subtitulo = 'Acesso ao painel administrativo do sistema'
    if licenca and not licenca.esta_vigente:
        subtitulo = 'Licenca nao vigente: acesse Licenca para renovar'

    return render(
        request,
        'core/dashboard.html',
        {
            'titulo': 'Painel Administrativo',
            'subtitulo': subtitulo,
            'cards': cards,
            'role_display': user.get_effective_role_display(),
            'modulos': [
                {'titulo': 'Cadastros', 'descricao': 'Dados mestres do sistema', 'url': '/app/cadastros/'},
                {'titulo': 'Compras', 'descricao': 'Pedidos e faturamento', 'url': '/app/compras/'},
                {'titulo': 'Financeiro', 'descricao': 'Contas a pagar', 'url': '/app/financeiro/'},
                {'titulo': 'Licencas', 'descricao': 'Assinatura e vigencia', 'url': '/app/licencas/'},
            ],
        },
    )

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
    items = [{'id': p.pk, 'nome': f'{p.produtor} - {p.fazenda}'} for p in qs]
    return JsonResponse({'items': items})


# ------------------------------
# CRUD simples (Cadastros)
# ------------------------------

def _make_simple_crud(name_prefix, model_cls, form_cls, list_template='core/crud/modal_list.html', search_fields=None, ordering='-id'):
    search_fields = search_fields or []

    ListKls = type(
        f'{name_prefix.title().replace("_", "")}ListView',
        (GestorRequiredMixin, CrudListView),
        {
            'template_name': list_template,
            'model': model_cls,
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
    'safra', Safra, SafraForm, list_template='core/crud/safra_modal_list.html', search_fields=['safra', 'cultura__nome'], ordering='-ano'
)
CulturaListView, CulturaCreateView, CulturaUpdateView, CulturaDeleteView = _make_simple_crud(
    'cultura', Cultura, CulturaForm, list_template='core/crud/cultura_modal_list.html', search_fields=['nome'], ordering='nome'
)
CustoListView, CustoCreateView, CustoUpdateView, CustoDeleteView = _make_simple_crud(
    'custo', Custo, CustoForm, search_fields=['nome'], ordering='nome'
)
CategoriaListView, CategoriaCreateView, CategoriaUpdateView, CategoriaDeleteView = _make_simple_crud(
    'categoria', Categoria, CategoriaForm, search_fields=['nome'], ordering='nome'
)
UnidadeListView, UnidadeCreateView, UnidadeUpdateView, UnidadeDeleteView = _make_simple_crud(
    'unidade', Unidade, UnidadeForm, search_fields=['nome', 'unidade_abreviado'], ordering='nome'
)
FormaPagamentoListView, FormaPagamentoCreateView, FormaPagamentoUpdateView, FormaPagamentoDeleteView = _make_simple_crud(
    'forma_pagamento', FormaPagamento, FormaPagamentoForm, search_fields=['pagamento'], ordering='pagamento'
)
OperacaoListView, OperacaoCreateView, OperacaoUpdateView, OperacaoDeleteView = _make_simple_crud(
    'operacao', Operacao, OperacaoForm, search_fields=['operacao'], ordering='operacao'
)

FornecedorListView, FornecedorCreateView, FornecedorUpdateView, FornecedorDeleteView = _make_simple_crud(
    'fornecedor', Fornecedor, FornecedorForm, search_fields=['fornecedor', 'cnpj', 'cidade'], ordering='fornecedor'
)
ClienteListView, ClienteCreateView, ClienteUpdateView, ClienteDeleteView = _make_simple_crud(
    'cliente', Cliente, ClienteForm, search_fields=['cliente', 'apelido', 'cpf_cnpj'], ordering='cliente'
)
ProdutorListView, ProdutorCreateView, ProdutorUpdateView, ProdutorDeleteView = _make_simple_crud(
    'produtor', Produtor, ProdutorForm, search_fields=['produtor', 'cpf', 'fazenda', 'cidade'], ordering='produtor'
)
PropriedadeListView, PropriedadeCreateView, PropriedadeUpdateView, PropriedadeDeleteView = _make_simple_crud(
    'propriedade', Propriedade, PropriedadeForm, search_fields=['propriedade', 'matricula', 'sicar'], ordering='propriedade'
)
ProdutoListView, ProdutoCreateView, ProdutoUpdateView, ProdutoDeleteView = _make_simple_crud(
    'produto', Produto, ProdutoForm, search_fields=['nome', 'nome_abreviado', 'npk'], ordering='nome'
)

CotacaoListView, CotacaoCreateView, CotacaoUpdateView, CotacaoDeleteView = _make_simple_crud(
    'cotacao', CotacaoProduto, CotacaoProdutoForm, search_fields=['produto', 'fornecedor__fornecedor'], ordering='-data'
)


# ------------------------------
# Pedidos (CRUD com itens)
# ------------------------------
class PedidoCompraListView(GestorRequiredMixin, ListView):
    model = PedidoCompra
    template_name = 'core/pedidos/list.html'
    paginate_by = 15

    def get_queryset(self):
        qs = super().get_queryset().select_related('safra', 'cliente', 'produtor', 'fornecedor').order_by('-data', '-id')
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(pedido__icontains=q) | Q(cliente__cliente__icontains=q) | Q(produtor__produtor__icontains=q))
        return qs


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
            .prefetch_related('itens', 'faturamentos')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['back_fallback_url'] = '/app/pedidos/'
        pedido = self.object
        ctx['pedido'] = pedido
        ctx['itens'] = pedido.itens.select_related('produto_cadastro', 'unidade').all()
        ctx['faturamentos'] = pedido.faturamentos.select_related('fornecedor', 'safra', 'produtor', 'pedido').all()
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
            .prefetch_related('itens', 'faturamentos')
            .order_by('-data', '-id')
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(pedido__icontains=q) | Q(cliente__cliente__icontains=q) | Q(produtor__produtor__icontains=q))
        return qs


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


# ------------------------------
# Faturamento (ListView restaurada)
# ------------------------------
# ------------------------------
# Faturamento (ListView)
# ------------------------------
class FaturamentoListView(GestorRequiredMixin, ListView):
    model = Faturamento
    template_name = 'core/faturamentos/list.html'
    paginate_by = 15

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
        tem_pag = PagamentoContaPagar.objects.filter(conta__faturamento_id=self.object.pk).exists()
        if tem_pag or self.object.status == Faturamento.Status.PAGO:
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
        tem_pag = PagamentoContaPagar.objects.filter(conta__faturamento_id=fat.pk).exists()
        if tem_pag or fat.status == Faturamento.Status.PAGO:
            messages.error(request, 'Nota fiscal com pagamento. Para excluir, estorne o pagamento primeiro.')
            return redirect('core:faturamento_list')
        return super().dispatch(request, *args, **kwargs)


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
    columns = [('usuario', 'Usuario'), ('licenca', 'Licenca'), ('created_at', 'Criado')]
    create_url_name = 'core:licenca_vinculo_create'
    edit_url_name = 'core:licenca_vinculo_update'
    delete_url_name = 'core:licenca_vinculo_delete'
    search_fields = ['usuario__username', 'usuario__first_name', 'usuario__last_name', 'licenca__cliente', 'licenca__cpf_cnpj', 'licenca__email']
    default_ordering = '-created_at'
    paginate_by = 15

    def get_queryset(self):
        qs = super().get_queryset().select_related('usuario', 'licenca')

        # Supervisor so enxerga vinculos da propria licenca
        if getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            perfil = getattr(self.request.user, 'perfil_licenca', None)
            lic = perfil.licenca if perfil else None
            if lic:
                qs = qs.filter(licenca_id=lic.pk)
        return qs


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
