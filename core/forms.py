import unicodedata

from django import forms
from django.contrib.auth import get_user_model

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
from compras.models import CotacaoProduto, PedidoCompra, PedidoCompraItem
from financeiro.models import ContaPagar, Faturamento, FaturamentoItem
from licencas.models import Licenca, PerfilUsuarioLicenca


class StyledModelForm(forms.ModelForm):
    normalize_excluded_fields = {
        'cpf',
        'cnpj',
        'cpf_cnpj',
        'cep',
        'contato',
        'email',
        'uf',
        'ie',
        'npk',
        'sicar',
        'stripe_customer_id',
        'stripe_subscription_id',
        'stripe_price_id',
    }

    @staticmethod
    def _remove_accents(value):
        normalized = unicodedata.normalize('NFKD', value)
        return ''.join(ch for ch in normalized if not unicodedata.combining(ch))

    @staticmethod
    def _capitalize_words(value):
        parts = value.split(' ')
        return ' '.join(part[:1].upper() + part[1:] if part else '' for part in parts)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        mask_config = {
            'cpf': {'mask': 'cpf', 'maxlength': 14, 'placeholder': '000.000.000-00'},
            'cnpj': {'mask': 'cnpj', 'maxlength': 18, 'placeholder': '00.000.000/0000-00'},
            'cpf_cnpj': {'mask': 'cpf_cnpj', 'maxlength': 18, 'placeholder': 'CPF ou CNPJ'},
            'cep': {'mask': 'cep', 'maxlength': 9, 'placeholder': '00000-000'},
            'contato': {'mask': 'contato', 'maxlength': 15, 'placeholder': '(00) 00000-0000'},
        }

        for field_name, field in self.fields.items():
            css = 'w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-800'
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'h-4 w-4 rounded border-slate-300 bg-white'})
            else:
                field.widget.attrs.update({'class': css})

            if isinstance(field.widget, (forms.DateInput, forms.DateTimeInput)):
                field.widget.input_type = 'date'
                # HTML date inputs require ISO format (YYYY-MM-DD), otherwise browsers show blank on edit.
                try:
                    field.widget.format = '%Y-%m-%d'
                    field.widget.is_localized = False
                except Exception:
                    pass

            if field_name in mask_config:
                field.widget.attrs.update(
                    {
                        'data-mask': mask_config[field_name]['mask'],
                        'maxlength': mask_config[field_name]['maxlength'],
                        'placeholder': mask_config[field_name]['placeholder'],
                        'inputmode': 'numeric',
                        'autocomplete': 'off',
                    }
                )

            if isinstance(field, forms.DecimalField) and not isinstance(field.widget, forms.CheckboxInput):
                # Decimal inputs must be text so we can type/format PT-BR values (1.234,56).
                # type="number" rejects commas and breaks our masks/calc.
                try:
                    field.widget.input_type = 'text'
                except Exception:
                    pass
                field.widget.attrs.update(
                    {
                        'data-decimal-br': '1',
                        'data-decimals': str(getattr(field, 'decimal_places', 2)),
                        'inputmode': 'numeric',
                        'autocomplete': 'off',
                    }
                )

            is_text_input = isinstance(field.widget, (forms.TextInput, forms.Textarea))
            is_char = isinstance(field, forms.CharField)
            if is_char and is_text_input and field_name not in self.normalize_excluded_fields:
                field.widget.attrs.update({'data-normalize': 'text'})

    def clean(self):
        cleaned_data = super().clean()

        for field_name, field in self.fields.items():
            if field_name in self.normalize_excluded_fields:
                continue
            if not isinstance(field, forms.CharField):
                continue

            value = cleaned_data.get(field_name)
            if not isinstance(value, str):
                continue

            value = self._remove_accents(value)
            value = self._capitalize_words(value)
            cleaned_data[field_name] = value

        return cleaned_data


class CulturaForm(StyledModelForm):
    class Meta:
        model = Cultura
        fields = ['nome']


class CustoForm(StyledModelForm):
    class Meta:
        model = Custo
        fields = ['nome']


class CategoriaForm(StyledModelForm):
    class Meta:
        model = Categoria
        fields = ['nome']


class UnidadeForm(StyledModelForm):
    class Meta:
        model = Unidade
        fields = ['nome', 'volume', 'unidade_abreviado']


class FormaPagamentoForm(StyledModelForm):
    class Meta:
        model = FormaPagamento
        fields = ['pagamento', 'parcelas', 'prazo']


class OperacaoForm(StyledModelForm):
    class Meta:
        model = Operacao
        fields = ['operacao', 'tipo']


class SafraForm(StyledModelForm):
    class Meta:
        model = Safra
        fields = ['safra', 'ano', 'cultura', 'data_inicio', 'data_fim', 'status']
        widgets = {'data_inicio': forms.DateInput(), 'data_fim': forms.DateInput()}


class ClienteForm(StyledModelForm):
    class Meta:
        model = Cliente
        fields = ['cliente', 'apelido', 'cpf_cnpj', 'status', 'limite_compra']


class FornecedorForm(StyledModelForm):
    class Meta:
        model = Fornecedor
        fields = ['fornecedor', 'cnpj', 'ie', 'endereco', 'numero', 'cep', 'cidade', 'uf', 'status']


class ProdutorForm(StyledModelForm):
    class Meta:
        model = Produtor
        fields = ['cliente', 'produtor', 'ie', 'cpf', 'fazenda', 'cidade', 'uf', 'ha', 'status']


class PropriedadeForm(StyledModelForm):
    class Meta:
        model = Propriedade
        fields = ['propriedade', 'produtor', 'ha', 'matricula', 'sicar', 'localizacao']


class ProdutoForm(StyledModelForm):
    class Meta:
        model = Produto
        fields = ['nome', 'nome_abreviado', 'npk', 'variedade', 'custo', 'categoria', 'status']


class PedidoCompraForm(StyledModelForm):
    class Meta:
        model = PedidoCompra
        fields = ['data', 'pedido', 'safra', 'cliente', 'produtor', 'fornecedor', 'vencimento', 'pedido_pago', 'valor_total']
        widgets = {'data': forms.DateInput(), 'vencimento': forms.DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valor_total'].required = False
        self.fields['valor_total'].initial = 0
        self.initial.setdefault('valor_total', 0)
        # Lock this field: it is calculated from items.
        self.fields['valor_total'].disabled = True
        self.fields['valor_total'].widget.attrs.update({'readonly': 'readonly'})

        cliente_id = None
        if self.data.get('cliente'):
            cliente_id = self.data.get('cliente')
        elif self.instance and self.instance.pk and self.instance.cliente_id:
            cliente_id = self.instance.cliente_id

        self.fields['safra'].label_from_instance = lambda obj: obj.safra
        self.fields['produtor'].label_from_instance = lambda obj: f'{obj.produtor} - {obj.fazenda}'

        if cliente_id:
            self.fields['produtor'].queryset = Produtor.objects.filter(cliente_id=cliente_id).order_by('produtor', 'fazenda')
        else:
            self.fields['produtor'].queryset = Produtor.objects.none()

        self.fields['valor_total'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2'})

    def clean_valor_total(self):
        # When disabled, this may come as None; keep database-safe default.
        return self.cleaned_data.get('valor_total') or 0


class PedidoCompraItemForm(StyledModelForm):
    class Meta:
        model = PedidoCompraItem
        fields = ['produto_cadastro', 'unidade', 'quantidade', 'preco', 'desconto', 'total_item']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['produto_cadastro'].label = 'Produto'
        self.fields['total_item'].required = False
        # Total item is calculated; keep it locked and avoid submit/validation issues.
        self.fields['total_item'].disabled = True
        self.fields['total_item'].widget.attrs.update({'readonly': 'readonly'})

        self.fields['quantidade'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '0', 'placeholder': '0'})
        self.fields['preco'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '5', 'placeholder': '0,00000'})
        self.fields['desconto'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})
        self.fields['total_item'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})

        self.fields['preco'].widget.attrs.pop('readonly', None)
        self.fields['desconto'].widget.attrs.pop('readonly', None)
        self.fields['preco'].widget.attrs.pop('disabled', None)
        self.fields['desconto'].widget.attrs.pop('disabled', None)

class CotacaoProdutoForm(StyledModelForm):
    class Meta:
        model = CotacaoProduto
        fields = ['data', 'safra', 'fornecedor', 'vencimento', 'produto', 'unidade', 'valor_total']
        widgets = {'data': forms.DateInput(), 'vencimento': forms.DateInput()}


class FaturamentoForm(StyledModelForm):
    class Meta:
        model = Faturamento
        fields = ['data', 'nota_fiscal', 'serie', 'pedido', 'custo', 'produtor', 'safra', 'fornecedor', 'vencimento', 'valor_total']
        widgets = {'data': forms.DateInput(), 'vencimento': forms.DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pedido'].required = False
        self.fields['custo'].required = False
        self.fields['produtor'].required = False
        self.fields['safra'].required = False
        self.fields['fornecedor'].required = False

        self.fields['valor_total'].required = False
        self.fields['valor_total'].initial = 0
        self.initial.setdefault('valor_total', 0)
        self.fields['valor_total'].disabled = True
        self.fields['valor_total'].widget.attrs.update({'readonly': 'readonly'})

        self.fields['safra'].label_from_instance = lambda obj: obj.safra
        self.fields['produtor'].label_from_instance = lambda obj: f'{obj.produtor} - {obj.fazenda}'

        self.fields['valor_total'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2'})

    def clean(self):
        cleaned = super().clean()
        pedido = cleaned.get('pedido')
        produtor = cleaned.get('produtor')

        cliente = None
        if pedido and getattr(pedido, 'cliente_id', None):
            cliente = pedido.cliente
            # quando vem do pedido, completa campos auxiliares se nao informados
            if not cleaned.get('produtor') and getattr(pedido, 'produtor_id', None):
                cleaned['produtor'] = pedido.produtor
            if not cleaned.get('safra') and getattr(pedido, 'safra_id', None):
                cleaned['safra'] = pedido.safra
            if not cleaned.get('fornecedor') and getattr(pedido, 'fornecedor_id', None):
                cleaned['fornecedor'] = pedido.fornecedor
        elif produtor and getattr(produtor, 'cliente_id', None):
            cliente = produtor.cliente

        if not cliente:
            raise forms.ValidationError('Informe um Pedido ou um Produtor vinculado a um Cliente.')

        self._cliente_resolvido = cliente
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.cliente = getattr(self, '_cliente_resolvido', None)

        # status: se o pedido ja esta pago, marca a nota como paga e nao gera fatura
        if obj.pedido_id and getattr(obj.pedido, 'pedido_pago', False):
            obj.status = Faturamento.Status.PAGO
        else:
            obj.status = Faturamento.Status.A_RECEBER

        if commit:
            obj.save()
            self.save_m2m()
        return obj


class FaturamentoItemForm(StyledModelForm):
    class Meta:
        model = FaturamentoItem
        fields = ['produto_cadastro', 'unidade', 'quantidade', 'preco', 'desconto', 'total_item']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['produto_cadastro'].label = 'Produto'
        self.fields['total_item'].required = False
        self.fields['total_item'].disabled = True
        self.fields['total_item'].widget.attrs.update({'readonly': 'readonly'})

        self.fields['quantidade'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '0', 'placeholder': '0'})
        self.fields['preco'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '5', 'placeholder': '0,00000'})
        self.fields['desconto'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})
        self.fields['total_item'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})

        self.fields['preco'].widget.attrs.pop('readonly', None)
        self.fields['desconto'].widget.attrs.pop('readonly', None)
        self.fields['preco'].widget.attrs.pop('disabled', None)
        self.fields['desconto'].widget.attrs.pop('disabled', None)

class ContaPagarForm(StyledModelForm):
    class Meta:
        model = ContaPagar
        fields = [
            'origem',
            'status',
            'data',
            'nota_fiscal',
            'pedido',
            'faturamento',
            'custo',
            'cliente',
            'produtor',
            'vencimento',
            'quantidade',
            'preco',
            'valor_total',
            'saldo_aberto',
            'pago',
            'detalhes',
        ]
        widgets = {'data': forms.DateInput(), 'vencimento': forms.DateInput()}



class PerfilUsuarioLicencaForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        User = get_user_model()

        # Usuarios nao-admin para vinculo
        if 'usuario' in self.fields:
            self.fields['usuario'].queryset = User.objects.filter(is_superuser=False).order_by('username')

        if 'licenca' in self.fields:
            self.fields['licenca'].queryset = Licenca.objects.order_by('-updated_at')

        # Supervisor so pode vincular usuarios na propria licenca
        if self.request and getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            perfil = getattr(self.request.user, 'perfil_licenca', None)
            lic = perfil.licenca if perfil else None
            if lic and 'licenca' in self.fields:
                self.fields['licenca'].queryset = Licenca.objects.filter(pk=lic.pk)
                self.fields['licenca'].initial = lic
                self.fields['licenca'].disabled = True

    class Meta:
        model = PerfilUsuarioLicenca
        fields = ['usuario', 'licenca']

class InviteUsuarioLicencaForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        User = get_user_model()

        role_choices = [(User.Role.USUARIO, User.Role.USUARIO.label)]
        if self.request and getattr(self.request.user, 'effective_role', '') == 'ADMIN':
            role_choices.append((User.Role.SUPERVISOR, User.Role.SUPERVISOR.label))

        self.fields['role'].choices = role_choices

        if 'canal' in self.fields:
            self.fields['canal'].initial = 'WHATSAPP'

        css = 'w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-800'
        for fn in ('username', 'first_name', 'last_name', 'email', 'canal'):
            self.fields[fn].widget.attrs.update({'class': css})

        for fn in ('password1', 'password2'):
            self.fields[fn].widget.attrs.update({'class': css, 'autocomplete': 'new-password'})

        self.fields['username'].widget.attrs.update({'placeholder': 'usuario (login)'} )
        self.fields['email'].widget.attrs.update({'placeholder': 'email@empresa.com'})

        # Licenca: ADMIN escolhe, SUPERVISOR fixa na propria licenca
        self.fields['licenca'].queryset = Licenca.objects.order_by('-updated_at')
        if self.request and getattr(self.request.user, 'effective_role', '') == 'SUPERVISOR':
            perfil = getattr(self.request.user, 'perfil_licenca', None)
            lic = perfil.licenca if perfil else None
            if lic:
                self.fields['licenca'].queryset = Licenca.objects.filter(pk=lic.pk)
                self.fields['licenca'].initial = lic
                self.fields['licenca'].disabled = True

        self.fields['licenca'].widget.attrs.update({'class': css})

    licenca = forms.ModelChoiceField(label='Licenca', queryset=Licenca.objects.none())
    role = forms.ChoiceField(label='Perfil', choices=())
    canal = forms.ChoiceField(label='Enviar por', choices=(('WHATSAPP','WhatsApp'),('EMAIL','Email')),)

    username = forms.CharField(label='Usuario (login)', max_length=150)
    first_name = forms.CharField(label='Nome', max_length=150, required=False)
    last_name = forms.CharField(label='Sobrenome', max_length=150, required=False)
    email = forms.EmailField(label='Email', required=False)

    password1 = forms.CharField(label='Senha', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar senha', widget=forms.PasswordInput)

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        User = get_user_model()
        if not username:
            raise forms.ValidationError('Informe o usuario.')
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Ja existe um usuario com este login.')
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'As senhas nao conferem.')
        return cleaned
class LicencaForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # customer/subscription vem do Stripe via webhook; nao precisa digitar manualmente
        for fn in ('stripe_customer_id', 'stripe_subscription_id'):
            if fn in self.fields:
                self.fields[fn].required = False
                self.fields[fn].widget.attrs.update({'readonly': 'readonly', 'placeholder': 'Preenchido automaticamente pelo Stripe (webhook)'})

        # price ids: copie do Stripe > Produtos > Precos (Price ID)
        for fn in ('stripe_price_id_semestral', 'stripe_price_id_anual', 'stripe_price_id'):
            if fn in self.fields:
                self.fields[fn].required = False
                self.fields[fn].widget.attrs.update({'placeholder': 'Cole aqui o Price ID (ex.: price_...)'})
    class Meta:
        model = Licenca
        fields = [
            'cliente',
            'cpf_cnpj',
            'endereco',
            'numero',
            'cep',
            'cidade',
            'uf',
            'email',
            'contato',
            'logo',
            'slogan',
            'status',
            'inicio_vigencia',
            'fim_vigencia',
            'stripe_customer_id',
            'stripe_subscription_id',
            'stripe_price_id',
            'stripe_price_id_semestral',
            'stripe_price_id_anual',
        ]
        widgets = {'inicio_vigencia': forms.DateInput(), 'fim_vigencia': forms.DateInput()}

