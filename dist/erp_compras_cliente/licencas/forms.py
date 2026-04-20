import re

from django import forms
from django.contrib.auth import get_user_model


class CompletarCadastroLicencaForm(forms.Form):
    cliente = forms.CharField(label='Nome da empresa/pessoa', max_length=150)
    cpf_cnpj = forms.CharField(label='CPF/CNPJ', max_length=18)
    email = forms.EmailField(label='Email')
    contato = forms.CharField(label='Contato', max_length=80)
    endereco = forms.CharField(label='Endereco', max_length=200, required=False)
    numero = forms.CharField(label='Numero', max_length=15, required=False)
    cep = forms.CharField(label='CEP', max_length=9, required=False)
    cidade = forms.CharField(label='Cidade', max_length=80, required=False)
    uf = forms.CharField(label='UF', max_length=2, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        css = 'w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-800'
        for name, field in self.fields.items():
            attrs = {'class': css}
            if name == 'cpf_cnpj':
                attrs.update({'data-mask': 'cpf_cnpj', 'maxlength': 18, 'placeholder': 'CPF ou CNPJ', 'inputmode': 'numeric', 'autocomplete': 'off'})
            if name == 'contato':
                attrs.update({'data-mask': 'contato', 'maxlength': 15, 'placeholder': '(00) 00000-0000', 'inputmode': 'numeric', 'autocomplete': 'off'})
            if name == 'cep':
                attrs.update({'data-mask': 'cep', 'maxlength': 9, 'placeholder': '00000-000', 'inputmode': 'numeric', 'autocomplete': 'off'})
            field.widget.attrs.update(attrs)


class RegistroContaForm(forms.Form):
    username = forms.CharField(label='Usuario', max_length=150)
    email = forms.EmailField(label='Email')
    password1 = forms.CharField(label='Senha', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar senha', widget=forms.PasswordInput)
    aceitar_termos = forms.BooleanField(
        label='Ao continuar voce aceita nossos termos e condicoes.',
        required=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        css = 'w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-800'

        self.fields['username'].widget.attrs.update(
            {'class': css, 'placeholder': 'Crie seu usuario (ex.: empresa)', 'autocomplete': 'username'}
        )
        self.fields['email'].widget.attrs.update(
            {'class': css, 'placeholder': 'email@empresa.com', 'autocomplete': 'email'}
        )
        self.fields['password1'].widget.attrs.update(
            {'class': css, 'placeholder': 'Crie uma senha', 'autocomplete': 'new-password'}
        )
        self.fields['password2'].widget.attrs.update(
            {'class': css, 'placeholder': 'Repita a senha', 'autocomplete': 'new-password'}
        )

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError('Informe um usuario.')
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Este usuario ja existe. Escolha outro.')
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError('Informe um email.')
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Este email ja possui cadastro.')
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1') or ''
        p2 = cleaned.get('password2') or ''

        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'As senhas nao conferem.')

        if p1:
            if len(p1) < 8:
                self.add_error('password1', 'A senha deve ter 8 caracteres ou mais.')
            if not re.search(r'[A-Z]', p1) or not re.search(r'[a-z]', p1):
                self.add_error('password1', 'A senha deve ter letras maiusculas e minusculas.')
            if not re.search(r'\d', p1):
                self.add_error('password1', 'A senha deve ter pelo menos um numero.')
            if not re.search(r'[^A-Za-z0-9]', p1):
                self.add_error('password1', 'A senha deve ter pelo menos um caractere especial.')

        return cleaned


# Compatibilidade com codigo legado
PrimeiroAcessoLicencaForm = CompletarCadastroLicencaForm
PrimeiroAcessoPublicForm = RegistroContaForm
