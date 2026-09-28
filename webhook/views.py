import json
import traceback
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from webhook.models import FormConfig, WebhookSubmission, FormLog
from webhook.services.discovery import (
    clean_val,
    extract_schema_from_payload,
    parse_payload_to_row,
    VALUE_MAPPING,
)
from core.telegram import send_telegram_alert, send_telegram_message


def extract_submission_id(data: dict) -> str:
    """Extract unique submission/answer identifier from incoming payload."""
    if not isinstance(data, dict):
        return ""
    if 'answer' in data and isinstance(data['answer'], dict):
        return str(data['answer'].get('id', ''))
    return str(data.get('submission_id') or data.get('id') or '')


@csrf_exempt
def dynamic_webhook(request, form_slug: str):
    if request.method != 'POST':
        return JsonResponse({"error": "Method not allowed"}, status=405)

    form = FormConfig.objects.filter(slug=form_slug).first()

    # Verify secret token if required
    expected_token = None
    if form and form.secret_token:
        expected_token = form.secret_token
    else:
        global_token = getattr(settings, 'EXPECTED_TOKEN', '')
        if global_token:
            expected_token = global_token

    if expected_token:
        received_token = request.headers.get('X-Secret-Token') or request.META.get('HTTP_X_SECRET_TOKEN')
        if received_token != expected_token:
            FormLog.objects.create(
                form_slug=form_slug,
                level='WARNING',
                event='AUTH_FAILED',
                message="Invalid or missing X-Secret-Token",
            )
            return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError as e:
        send_telegram_alert(
            source=f"Webhook ({form_slug})",
            error=f"JSONDecodeError: {e}",
            details={"path": request.path},
        )
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        sub_id = extract_submission_id(data)

        # Deduplication check
        if sub_id:
            existing = WebhookSubmission.objects.filter(
                form_slug=form_slug,
                submission_id=sub_id,
                status__in=['SUCCESS', 'PENDING'],
            ).first()
            if existing:
                WebhookSubmission.objects.create(
                    form=form,
                    form_slug=form_slug,
                    submission_id=sub_id,
                    raw_payload=data,
                    status='DUPLICATE',
                )
                return JsonResponse({"status": "duplicate_skipped", "id": existing.id}, status=200)

        # Auto-discovery for new or unconfigured forms
        is_new_form = False
        if not form:
            form = FormConfig.objects.create(
                slug=form_slug,
                title=f"Form {form_slug}",
                auto_init=True,
                is_active=True,
            )
            is_new_form = True

        if form.auto_init and not form.columns_order:
            detected_columns = extract_schema_from_payload(data)
            if detected_columns:
                form.columns_order = detected_columns
                form.save(update_fields=['columns_order'])

                cols_preview = ", ".join(detected_columns[:6])
                if len(detected_columns) > 6:
                    cols_preview += f" ... (+{len(detected_columns) - 6} more)"

                FormLog.objects.create(
                    form_slug=form_slug,
                    level='INFO',
                    event='SCHEMA_DISCOVERED',
                    message=f"Discovered {len(detected_columns)} columns: {cols_preview}",
                )

                prompt_msg = (
                    f"🔔 <b>New Form Detected:</b> <code>{form_slug}</code>\n"
                    f"Discovered {len(detected_columns)} fields:\n"
                    f"<code>{cols_preview}</code>\n\n"
                    f"Выберите действие с помощью кнопок:"
                )
                buttons = {
                    "inline_keyboard": [
                        [
                            {"text": "➕ Создать Google Таблицу", "callback_data": f"create_prompt:{form_slug}"},
                            {"text": "🔗 Привязать существующую", "callback_data": f"bind_prompt:{form_slug}"},
                        ]
                    ]
                }
                send_telegram_message(prompt_msg, reply_markup=buttons)

        # Parse row values if columns_order is available
        parsed_row = None
        if form.columns_order:
            parsed_row = parse_payload_to_row(data, form.columns_order)

        submission = WebhookSubmission.objects.create(
            form=form,
            form_slug=form_slug,
            submission_id=sub_id,
            raw_payload=data,
            parsed_row=parsed_row,
            status='PENDING',
        )

        FormLog.objects.create(
            form_slug=form_slug,
            level='INFO',
            event='SUBMISSION_RECEIVED',
            message=f"Submission #{submission.id} received and queued.",
        )

        return JsonResponse({
            "status": "accepted",
            "submission_id": submission.id,
            "form_slug": form_slug,
        }, status=200)

    except Exception as e:
        traceback.print_exc()
        send_telegram_alert(
            source=f"Webhook ({form_slug})",
            error=e,
            details={"path": request.path},
        )
        return JsonResponse({"error": "Internal server error"}, status=500)


@csrf_exempt
def legacy_webhook(request):
    """Backwards-compatible endpoint for existing /yandex-form/ integrations."""
    return dynamic_webhook(request, form_slug='default')


def healthz(request):
    """Kubernetes liveness and readiness probe endpoint."""
    try:
        FormConfig.objects.count()
        return JsonResponse({"status": "healthy", "database": "connected"}, status=200)
    except Exception as e:
        return JsonResponse({"status": "unhealthy", "error": str(e)}, status=503)
