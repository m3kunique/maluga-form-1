from django.urls import path
from .views import dynamic_webhook, legacy_webhook, healthz

urlpatterns = [
    path('webhook/<slug:form_slug>/', dynamic_webhook, name='dynamic_webhook'),
    path('yandex-form/', legacy_webhook, name='legacy_webhook'),
    path('healthz/', healthz, name='healthz'),
]
