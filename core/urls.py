from django.urls import path

from .views import dashboard, landing_page

app_name = 'core'

urlpatterns = [
    path('', landing_page, name='landing_page'),
    path('dashboard/', dashboard, name='dashboard'),
]
