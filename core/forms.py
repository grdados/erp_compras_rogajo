import unicodedata

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import timedelta
import calendar

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

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
from compras.models import CotacaoProduto, Planejamento, PlanejamentoItem, PedidoCompra, PedidoCompraItem
from financeiro.models import ContaPagar, Faturamento, FaturamentoItem
from licencas.models import Licenca, PerfilUsuarioLicenca
from licencas.pricing import valor_anual, valor_mensal_plano, valor_semestral


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Permitir cadastrar Produtor sem Cliente (pedido do usuÃ¡rio).
        if 'cliente' in self.fields:
            self.fields['cliente'].required = False


class PropriedadeForm(StyledModelForm):
    class Meta:
        model = Propriedade
        fields = ['propriedade', 'produtor', 'ha', 'matricula', 'sicar', 'localizacao']


class ProdutoForm(StyledModelForm):
    class Meta:
        model = Produto
        fields = ['nome', 'nome_abreviado', 'npk', 'variedade', 'custo', 'categoria', 'status']


class PlanejamentoForm(StyledModelForm):
    class Meta:
        model = Planejamento
        fields = ['data', 'safra', 'custo', 'cliente', 'preco_produto', 'vencimento', 'valor_total']
        widgets = {'data': forms.DateInput(), 'vencimento': forms.DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valor_total'].required = False
        self.fields['valor_total'].initial = 0
        self.initial.setdefault('valor_total', 0)
        # Lock this field: it is calculated from items.
        self.fields['valor_total'].disabled = True
        self.fields['valor_total'].widget.attrs.update({'readonly': 'readonly'})


class PlanejamentoItemForm(StyledModelForm):
    class Meta:
        model = PlanejamentoItem
        fields = ['area_ha', 'produto_cadastro', 'quantidade', 'unidade', 'preco', 'desconto', 'total_item', 'custo_ha']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Calculated fields
        for fname in ['total_item', 'custo_ha']:
            if fname in self.fields:
                self.fields[fname].required = False
                self.fields[fname].disabled = True
                self.fields[fname].widget.attrs.update({'readonly': 'readonly'})

        # UX labels
        if 'produto_cadastro' in self.fields:
            self.fields['produto_cadastro'].label = 'Produto'
        if 'unidade' in self.fields:
            # Compact label (e.g., KG, LT)
            self.fields['unidade'].label_from_instance = lambda obj: (obj.unidade_abreviado or obj.nome)

        # Decimal masks (PT-BR) + display defaults
        if 'area_ha' in self.fields:
            self.fields['area_ha'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '1', 'placeholder': '0,0'})
        if 'quantidade' in self.fields:
            # Allow up to 2 decimals; UI will show integer when not focused (JS).
            self.fields['quantidade'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0'})
        if 'preco' in self.fields:
            # Show and input with 2 decimals (UI is cleaner in the grid).
            self.fields['preco'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})
        if 'desconto' in self.fields:
            self.fields['desconto'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})
        if 'total_item' in self.fields:
            self.fields['total_item'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})
        if 'custo_ha' in self.fields:
            # Sacas/HA shown with 1 decimal (JS).
            self.fields['custo_ha'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '1', 'placeholder': '0,0'})


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Naming: in UI this is the "Preco" for the quoted product.
        if 'valor_total' in self.fields:
            self.fields['valor_total'].label = 'Preco'
            self.fields['valor_total'].widget.attrs.update({'data-decimal-br': '1', 'data-decimals': '2', 'placeholder': '0,00'})


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
    @staticmethod
    def _add_months(base_date, months):
        if not base_date:
            return base_date
        month = base_date.month - 1 + int(months)
        year = base_date.year + month // 12
        month = month % 12 + 1
        day = min(base_date.day, calendar.monthrange(year, month)[1])
        return base_date.replace(year=year, month=month, day=day)

    @staticmethod
    def _format_decimal_br(value):
        try:
            dec = Decimal(value or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except Exception:
            dec = Decimal('0.00')
        s = f'{dec:,.2f}'
        return s.replace(',', 'X').replace('.', ',').replace('X', '.')

    @staticmethod
    def _parse_decimal_robusto(raw_value):
        if raw_value is None:
            return Decimal('0.00')
        raw = str(raw_value).strip().replace(' ', '')
        if not raw:
            return Decimal('0.00')

        if ',' in raw and '.' in raw:
            last_comma = raw.rfind(',')
            last_dot = raw.rfind('.')
            if last_comma > last_dot:
                normalized = raw.replace('.', '').replace(',', '.')
            else:
                normalized = raw.replace(',', '')
        elif ',' in raw:
            normalized = raw.replace('.', '').replace(',', '.')
        elif '.' in raw:
            if raw.count('.') == 1:
                normalized = raw
            else:
                head, tail = raw.rsplit('.', 1)
                normalized = head.replace('.', '') + '.' + tail
        else:
            normalized = raw

        try:
            return Decimal(normalized).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return Decimal('0.00')

    @staticmethod
    def _valor_por_plano(plano):
        if plano == Licenca.Plano.MENSAL:
            return Decimal(valor_mensal_plano()).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if plano == Licenca.Plano.ANUAL:
            return Decimal(valor_anual()).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if plano == Licenca.Plano.SEMESTRAL:
            return Decimal(valor_semestral()).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return Decimal('0.00')

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        if 'valor_total' in self.fields:
            self.fields['valor_total'].widget.attrs.update(
                {
                    'data-decimal-br': '0',
                    'data-license-currency': '1',
                    'data-decimals': '2',
                    'placeholder': '0,00',
                    'readonly': 'readonly',
                }
            )
            if not self.is_bound:
                plano_inicial = getattr(self.instance, 'plano', None) or self.initial.get('plano')
                self.initial['valor_total'] = self._format_decimal_br(self._valor_por_plano(plano_inicial))

        role_user = getattr(getattr(self, 'request', None), 'user', None)
        effective_role = getattr(role_user, 'effective_role', '')
        if effective_role != 'ADMIN' and 'status' in self.fields:
            self.fields.pop('status')

        # Datas automaticas
        hoje = timezone.localdate()
        data_emissao_base = getattr(self.instance, 'data_emissao', None) or hoje
        plano_atual = getattr(self.instance, 'plano', None) or self.initial.get('plano')
        data_pagamento_atual = getattr(self.instance, 'data_pagamento', None)

        self.initial['data_emissao'] = data_emissao_base
        self.initial['data_vencimento_pagamento'] = data_emissao_base + timedelta(days=7)
        self.initial['data_pagamento'] = data_pagamento_atual
        self.initial['inicio_vigencia'] = data_pagamento_atual

        if data_pagamento_atual and plano_atual == Licenca.Plano.MENSAL:
            self.initial['fim_vigencia'] = self._add_months(data_pagamento_atual, 1)
        elif data_pagamento_atual and plano_atual == Licenca.Plano.SEMESTRAL:
            self.initial['fim_vigencia'] = self._add_months(data_pagamento_atual, 6)
        elif data_pagamento_atual and plano_atual == Licenca.Plano.ANUAL:
            self.initial['fim_vigencia'] = self._add_months(data_pagamento_atual, 12)

        for fname in ('data_emissao', 'data_vencimento_pagamento', 'inicio_vigencia', 'fim_vigencia'):
            if fname in self.fields:
                self.fields[fname].disabled = True
                self.fields[fname].widget.attrs.update({'readonly': 'readonly'})

        if effective_role != 'ADMIN' and 'data_pagamento' in self.fields:
            self.fields['data_pagamento'].disabled = True
            self.fields['data_pagamento'].widget.attrs.update({'readonly': 'readonly'})

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
            'plano',
            'forma_pagamento',
            'valor_total',
            'data_emissao',
            'data_vencimento_pagamento',
            'data_pagamento',
            'status',
            'inicio_vigencia',
            'fim_vigencia',
        ]
        widgets = {
            'data_emissao': forms.DateInput(),
            'data_vencimento_pagamento': forms.DateInput(),
            'data_pagamento': forms.DateInput(),
            'inicio_vigencia': forms.DateInput(),
            'fim_vigencia': forms.DateInput(),
        }

    def clean_valor_total(self):
        value = self.cleaned_data.get('valor_total')
        if isinstance(value, Decimal):
            return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        raw = self.data.get(self.add_prefix('valor_total'))
        return self._parse_decimal_robusto(raw)

    def clean(self):
        cleaned = super().clean()

        role = getattr(getattr(self.request, 'user', None), 'effective_role', '')

        # Data emissao fixa + vencimento de pagamento automatico (emissao + 7)
        data_emissao = getattr(self.instance, 'data_emissao', None) or timezone.localdate()
        cleaned['data_emissao'] = data_emissao
        cleaned['data_vencimento_pagamento'] = data_emissao + timedelta(days=7)

        plano_novo = cleaned.get('plano') or getattr(self.instance, 'plano', '')
        plano_antigo = getattr(self.instance, 'plano', '')
        cleaned['valor_total'] = self._valor_por_plano(plano_novo)

        # Nao permite mudar plano durante vigencia ativa.
        if self.instance.pk and getattr(self.instance, 'status', '') == Licenca.Status.ATIVA and plano_novo and plano_novo != plano_antigo:
            self.add_error('plano', 'Nao e permitido alterar o plano durante a vigencia ativa. Renove para um novo ciclo.')

        # Data de pagamento: somente admin informa.
        if role == 'ADMIN':
            data_pagamento = cleaned.get('data_pagamento') or getattr(self.instance, 'data_pagamento', None)
        else:
            data_pagamento = getattr(self.instance, 'data_pagamento', None)
            cleaned['data_pagamento'] = data_pagamento

        if data_pagamento and data_pagamento < data_emissao:
            self.add_error('data_pagamento', 'Data do pagamento nao pode ser menor que a data de emissao.')

        status_novo = cleaned.get('status') if 'status' in cleaned else getattr(self.instance, 'status', '')
        if status_novo == Licenca.Status.ATIVA and not data_pagamento:
            self.add_error('data_pagamento', 'Informe a data do pagamento para ativar a assinatura.')

        # Ao informar data de pagamento (admin), ativa automaticamente.
        if role == 'ADMIN' and data_pagamento:
            cleaned['status'] = Licenca.Status.ATIVA

        # Inicio vigencia sempre = data de pagamento.
        cleaned['inicio_vigencia'] = data_pagamento

        # Fim vigencia automatico pela data de pagamento e plano.
        if data_pagamento and plano_novo == Licenca.Plano.MENSAL:
            cleaned['fim_vigencia'] = self._add_months(data_pagamento, 1)
        elif data_pagamento and plano_novo == Licenca.Plano.SEMESTRAL:
            cleaned['fim_vigencia'] = self._add_months(data_pagamento, 6)
        elif data_pagamento and plano_novo == Licenca.Plano.ANUAL:
            cleaned['fim_vigencia'] = self._add_months(data_pagamento, 12)
        else:
            cleaned['fim_vigencia'] = None

        return cleaned
