import os

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path('', include('core.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('licencas/', include('licencas.urls')),
]

# This project uses a custom painel outside Django Admin.
# Enable Django Admin only when explicitly needed (ENV: ENABLE_DJANGO_ADMIN=True).
if os.getenv('ENABLE_DJANGO_ADMIN', 'False').lower() == 'true':
    from django.contrib import admin

    urlpatterns += [path('admin/', admin.site.urls)]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
