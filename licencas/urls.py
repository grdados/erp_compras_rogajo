from django.urls import path

from .views import (
    checkout_cancelado,
    checkout_sucesso,
    confirmar_email,
    politica_privacidade,
    primeiro_acesso,
    reenviar_confirmacao_email,
    registrar,
    renovar_licenca,
    stripe_webhook,
)

app_name = 'licencas'

urlpatterns = [
    path('registrar/', registrar, name='registrar'),
    path('confirmar-email/<str:token>/', confirmar_email, name='confirmar_email'),
    path('reenviar-confirmacao-email/', reenviar_confirmacao_email, name='reenviar_confirmacao_email'),
    path('politica-privacidade/', politica_privacidade, name='politica_privacidade'),
    path('primeiro-acesso/', primeiro_acesso, name='primeiro_acesso'),
    path('renovar/', renovar_licenca, name='renovar'),
    path('checkout/sucesso/', checkout_sucesso, name='checkout_sucesso'),
    path('checkout/cancelado/', checkout_cancelado, name='checkout_cancelado'),
    path('webhook/stripe/', stripe_webhook, name='stripe_webhook'),
]
