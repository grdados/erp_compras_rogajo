import re
from pathlib import Path

p = Path('core/views.py')
s = p.read_text(encoding='utf-8')

marker = 'class FaturamentoFormMixin'
idx = s.find(marker)
if idx < 0:
    raise SystemExit('Marker class FaturamentoFormMixin not found')

prefix = s[:idx]
suffix = s[idx:]

# Remove orphaned stray block left after dashboard (filters/get_context_data without class)
# Anything starting with an indented "safra_id = (self.request.GET..." up to just before marker.
prefix = re.sub(r"\n\s+safra_id\s*=\s*\(self\.request\.GET\.get\('safra'\)[\s\S]*$", "\n", prefix)

# If there is an orphaned "def get_context_data" at module indentation, remove it too.
prefix = re.sub(r"\n\s+def get_context_data\(self, \*\*kwargs\):[\s\S]*$", "\n", prefix)

# Insert missing views and CRUD helpers just before marker (end of prefix)
insert = r'''

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

def _make_simple_crud(name_prefix, model, form_class, list_template='core/crud/modal_list.html', search_fields=None, ordering='-id'):
    search_fields = search_fields or []

    class _List(GestorRequiredMixin, CrudListView):
        template_name = list_template
        model = model
        context_title = model._meta.verbose_name_plural.title()
        create_url_name = f'core:{name_prefix}_create'
        edit_url_name = f'core:{name_prefix}_update'
        delete_url_name = f'core:{name_prefix}_delete'
        default_ordering = ordering
        search_fields = search_fields

    class _Create(GestorRequiredMixin, CrudCreateView):
        model = model
        form_class = form_class
        success_url = reverse_lazy(f'core:{name_prefix}_list')

    class _Update(GestorRequiredMixin, CrudUpdateView):
        model = model
        form_class = form_class
        success_url = reverse_lazy(f'core:{name_prefix}_list')

    class _Delete(GestorRequiredMixin, CrudDeleteView):
        model = model
        success_url = reverse_lazy(f'core:{name_prefix}_list')

    return _List, _Create, _Update, _Delete


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


class PedidoCompraModalDetailView(GestorRequiredMixin, DetailView):
    model = PedidoCompra
    template_name = 'core/pedidos/detail_modal.html'


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
class FaturamentoListView(GestorRequiredMixin, ListView):
    model = Faturamento
    template_name = 'core/faturamentos/list.html'
    paginate_by = 15

    def get_queryset(self):
        qs = super().get_queryset().select_related('safra', 'produtor', 'fornecedor', 'pedido').order_by('-data', '-id')
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

        context['card_notas'] = qs.count()
        context['card_quantidade'] = qtd_total
        context['card_preco_medio'] = preco_medio
        context['card_total_faturado'] = total_fat
        context['total_registros'] = qs.count()
        return context

'''

prefix = prefix.rstrip() + insert + "\n\n"

new_text = prefix + suffix
p.write_text(new_text, encoding='utf-8')
print('OK: core/views.py missing views restored block inserted')
