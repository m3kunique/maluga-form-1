from django.contrib import admin
from .models import FormConfig, WebhookSubmission, FormLog


@admin.register(FormConfig)
class FormConfigAdmin(admin.ModelAdmin):
    list_display = ('slug', 'title', 'spreadsheet_id', 'sheet_name', 'is_active', 'created_at')
    search_fields = ('slug', 'title', 'spreadsheet_id')
    list_filter = ('is_active', 'auto_init')


@admin.register(WebhookSubmission)
class WebhookSubmissionAdmin(admin.ModelAdmin):
    list_display = ('id', 'form_slug', 'submission_id', 'status', 'attempts', 'created_at')
    search_fields = ('form_slug', 'submission_id', 'last_error')
    list_filter = ('status', 'form_slug', 'created_at')


@admin.register(FormLog)
class FormLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'form_slug', 'level', 'event', 'created_at')
    search_fields = ('form_slug', 'event', 'message')
    list_filter = ('level', 'event', 'form_slug', 'created_at')
