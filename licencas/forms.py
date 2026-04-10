from django import forms
from django.contrib.auth import get_user_model


class PrimeiroAcessoLicencaForm(forms.Form):
    cliente = forms.CharField(label='Cliente', max_length=150)
    cpf_cnpj = forms.CharField(label='CPF/CNPJ', max_length=18)
    email = forms.EmailField(label='Email')
    contato = forms.CharField(label='Contato', max_length=80)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        css = 'w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-800'

        self.fields['cliente'].widget.attrs.update({'class': css, 'placeholder': 'Nome da empresa'})
        self.fields['cpf_cnpj'].widget.attrs.update({'class': css, 'data-mask': 'cpf_cnpj', 'maxlength': 18, 'placeholder': 'CPF ou CNPJ', 'inputmode': 'numeric', 'autocomplete': 'off'})
        self.fields['email'].widget.attrs.update({'class': css, 'placeholder': 'email@empresa.com'})
        self.fields['contato'].widget.attrs.update({'class': css, 'data-mask': 'contato', 'maxlength': 15, 'placeholder': '(00) 00000-0000', 'inputmode': 'numeric', 'autocomplete': 'off'})


class PrimeiroAcessoPublicForm(PrimeiroAcessoLicencaForm):
    """Primeiro acesso a partir da tela de login (usuario ainda nao existe)."""

    username = forms.CharField(label='Usuario', max_length=150)
    password1 = forms.CharField(label='Senha', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar senha', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        css = 'w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-800'

        self.fields['username'].widget.attrs.update(
            {
                'class': css,
                'placeholder': 'Crie seu usuario (ex.: empresa)',
                'autocomplete': 'username',
            }
        )
        self.fields['password1'].widget.attrs.update(
            {
                'class': css,
                'placeholder': 'Crie uma senha',
                'autocomplete': 'new-password',
            }
        )
        self.fields['password2'].widget.attrs.update(
            {
                'class': css,
                'placeholder': 'Repita a senha',
                'autocomplete': 'new-password',
            }
        )

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError('Informe um usuario.')

        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Este usuario ja existe. Escolha outro.')

        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1') or ''
        p2 = cleaned.get('password2') or ''

        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'As senhas nao conferem.')
        if p1 and len(p1) < 6:
            self.add_error('password1', 'A senha deve ter pelo menos 6 caracteres.')

        return cleaned
