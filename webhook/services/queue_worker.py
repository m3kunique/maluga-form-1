import time
import traceback
from django.db import transaction
from django.utils import timezone
from webhook.models import FormConfig, WebhookSubmission, FormLog
from webhook.services.discovery import parse_payload_to_row
from webhook.services import google_sheets
from core.telegram import send_telegram_alert


def process_pending_submissions(batch_size: int = 20) -> dict:
    """Process a batch of pending submissions and write them to Google Sheets."""
    pending = list(
        WebhookSubmission.objects.filter(status='PENDING')
        .select_related('form')
        .order_by('created_at')[:batch_size]
    )

    if not pending:
        return {'processed': 0, 'failed': 0, 'remaining': 0}

    # Group submissions by form_slug
    by_slug: dict[str, list[WebhookSubmission]] = {}
    for item in pending:
        by_slug.setdefault(item.form_slug, []).append(item)

    processed_count = 0
    failed_count = 0

    for slug, items in by_slug.items():
        # Retrieve or refresh form config
        form = FormConfig.objects.filter(slug=slug).first()

        # If form is not configured with spreadsheet_id or is inactive, keep in PENDING
        if not form or not form.spreadsheet_id or not form.is_active:
            continue

        try:
            # Check and sync header row in Google Sheet
            if form.columns_order:
                google_sheets.ensure_headers(form.spreadsheet_id, form.sheet_name, form.columns_order)

            rows_to_write = []
            valid_items = []

            for item in items:
                row = item.parsed_row
                if not row and form.columns_order:
                    row = parse_payload_to_row(item.raw_payload, form.columns_order)
                    item.parsed_row = row

                if row:
                    rows_to_write.append(row)
                    valid_items.append(item)

            if rows_to_write:
                google_sheets.append_rows(form.spreadsheet_id, form.sheet_name, rows_to_write)

                with transaction.atomic():
                    for item in valid_items:
                        item.status = 'SUCCESS'
                        item.last_error = ''
                        item.updated_at = timezone.now()
                        item.save(update_fields=['status', 'parsed_row', 'last_error', 'updated_at'])

                FormLog.objects.create(
                    form_slug=slug,
                    level='INFO',
                    event='ROWS_WRITTEN',
                    message=f"Appended {len(rows_to_write)} row(s) to Google Sheets.",
                )
                processed_count += len(rows_to_write)

        except Exception as e:
            failed_count += len(items)
            err_msg = str(e)
            FormLog.objects.create(
                form_slug=slug,
                level='ERROR',
                event='DELIVERY_FAILED',
                message=f"Batch write failed: {err_msg}",
            )

            with transaction.atomic():
                for item in items:
                    item.attempts += 1
                    item.last_error = err_msg
                    if item.attempts >= 5:
                        item.status = 'FAILED'
                    item.save(update_fields=['attempts', 'last_error', 'status', 'updated_at'])

            # Send Telegram alert if threshold exceeded
            failed_permanently = [it for it in items if it.attempts >= 5]
            if failed_permanently:
                send_telegram_alert(
                    source=f"Queue Worker ({slug})",
                    error=f"Delivery failed after 5 attempts: {err_msg}",
                    details={
                        "form_slug": slug,
                        "failed_count": len(failed_permanently),
                        "spreadsheet_id": form.spreadsheet_id if form else "None",
                    },
                )

    remaining = WebhookSubmission.objects.filter(status='PENDING').count()
    return {
        'processed': processed_count,
        'failed': failed_count,
        'remaining': remaining,
    }
