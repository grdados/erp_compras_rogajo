from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    BrandedPasswordResetCompleteView,
    BrandedPasswordResetConfirmView,
    BrandedPasswordResetDoneView,
    BrandedPasswordResetView,
    StatusLoginView,
)

app_name = 'accounts'

urlpatterns = [
    path('login/', StatusLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password_reset/', BrandedPasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', BrandedPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', BrandedPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', BrandedPasswordResetCompleteView.as_view(), name='password_reset_complete'),
]
