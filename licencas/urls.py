from django.urls import path

from .views import checkout_cancelado, checkout_sucesso, renovar_licenca, stripe_webhook

app_name = 'licencas'

urlpatterns = [
    path('renovar/', renovar_licenca, name='renovar'),
    path('checkout/sucesso/', checkout_sucesso, name='checkout_sucesso'),
    path('checkout/cancelado/', checkout_cancelado, name='checkout_cancelado'),
    path('webhook/stripe/', stripe_webhook, name='stripe_webhook'),
]
