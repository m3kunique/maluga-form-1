from django.urls import path
from .views import yandex_webhook_to_sheets

urlpatterns = [
    path('yandex-form/', yandex_webhook_to_sheets),
]
