import time
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.conf import settings
from webhook.models import FormConfig, WebhookSubmission, FormLog
from webhook.services import google_sheets
from webhook.services.queue_worker import process_pending_submissions
from core.telegram import (
    get_telegram_updates,
    send_telegram_message,
    get_telegram_config,
    get_admin_chat_ids,
)


class Command(BaseCommand):
    help = "Run Telegram admin bot daemon (long-polling)"

    def handle(self, *args, **options):
        token, default_chat, admin_chat = get_telegram_config()
        if not token:
            self.stdout.write(self.style.ERROR("TELEGRAM_BOT_TOKEN is not configured."))
            return

        admin_ids = get_admin_chat_ids()
        self.stdout.write(self.style.SUCCESS(
            f"Starting Telegram admin bot for admin ID(s): {', '.join(admin_ids) or 'any'}"
        ))

        offset = None
        while True:
            try:
                updates = get_telegram_updates(offset=offset, timeout=25)
                for update in updates:
                    offset = update['update_id'] + 1
                    message = update.get('message')
                    if not message:
                        continue

                    chat_id = str(message.get('chat', {}).get('id', ''))
                    text = (message.get('text') or '').strip()

                    # Restrict access to admin chat IDs if configured
                    if admin_ids and chat_id not in admin_ids:
                        send_telegram_message("Access denied.", chat_id=chat_id)
                        continue

                    if text.startswith('/'):
                        response = self.process_command(text, chat_id)
                        if response:
                            send_telegram_message(response, chat_id=chat_id)

            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Bot polling error: {e}"))
                time.sleep(3)

    def process_command(self, text: str, chat_id: str) -> str:
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ('/start', '/help'):
            return (
                "🤖 <b>Form Webhook Admin</b>\n\n"
                "Available commands:\n"
                "• <code>/forms</code> — List registered forms\n"
                "• <code>/stats</code> — Queue metrics & totals\n"
                "• <code>/logs &lt;slug&gt;</code> — Last 10 events for a form\n"
                "• <code>/bind &lt;slug&gt; &lt;spreadsheet_id&gt; [sheet_name]</code> — Link sheet\n"
                "• <code>/create_sheet &lt;slug&gt; &lt;email&gt;</code> — Auto-create Google Sheet\n"
                "• <code>/retry &lt;slug|all&gt;</code> — Re-queue failed submissions\n"
                "• <code>/activate &lt;slug&gt;</code> — Enable form\n"
                "• <code>/deactivate &lt;slug&gt;</code> — Disable form"
            )

        if cmd == '/forms':
            forms = FormConfig.objects.all()
            if not forms.exists():
                return "No forms registered yet. Send a webhook to <code>/webhook/&lt;slug&gt;/</code> to auto-register."

            lines = ["📋 <b>Registered Forms:</b>"]
            for f in forms:
                counts = WebhookSubmission.objects.filter(form_slug=f.slug).aggregate(
                    pending=Count('id', filter=Q(status='PENDING')),
                    success=Count('id', filter=Q(status='SUCCESS')),
                    failed=Count('id', filter=Q(status='FAILED')),
                )
                sheet_info = f"<code>{f.spreadsheet_id}</code>" if f.spreadsheet_id else "<i>(not linked)</i>"
                status_icon = "🟢" if f.is_active else "🔴"
                lines.append(
                    f"\n{status_icon} <b>{f.slug}</b> ({f.title or 'No title'})\n"
                    f"Sheet: {sheet_info} | Columns: {len(f.columns_order)}\n"
                    f"Stats: Pending: {counts['pending']} | OK: {counts['success']} | Failed: {counts['failed']}"
                )
            return "\n".join(lines)

        if cmd == '/stats':
            totals = WebhookSubmission.objects.aggregate(
                total=Count('id'),
                pending=Count('id', filter=Q(status='PENDING')),
                success=Count('id', filter=Q(status='SUCCESS')),
                failed=Count('id', filter=Q(status='FAILED')),
                duplicate=Count('id', filter=Q(status='DUPLICATE')),
            )
            forms_count = FormConfig.objects.count()
            return (
                "📊 <b>System Statistics</b>\n\n"
                f"• Active Forms: {forms_count}\n"
                f"• Total Submissions: {totals['total']}\n"
                f"• ⏳ Pending in Queue: {totals['pending']}\n"
                f"• ✅ Successfully Written: {totals['success']}\n"
                f"• ❌ Failed Deliveries: {totals['failed']}\n"
                f"• 🔁 Duplicate Submissions: {totals['duplicate']}"
            )

        if cmd == '/logs':
            if not args:
                return "Usage: <code>/logs &lt;slug&gt;</code>"
            slug = args[0]
            logs = FormLog.objects.filter(form_slug=slug).order_by('-created_at')[:10]
            if not logs.exists():
                return f"No logs found for form <code>{slug}</code>."

            lines = [f"📜 <b>Recent logs for {slug}:</b>"]
            for log in logs:
                icon = "❌" if log.level == 'ERROR' else ("⚠️" if log.level == 'WARNING' else "ℹ️")
                ts = log.created_at.strftime('%H:%M:%S')
                lines.append(f"{icon} [{ts}] <b>{log.event}</b>: {log.message[:120]}")
            return "\n".join(lines)

        if cmd == '/bind':
            if len(args) < 2:
                return "Usage: <code>/bind &lt;slug&gt; &lt;spreadsheet_id&gt; [sheet_name]</code>"
            slug = args[0]
            sheet_id = args[1]
            sheet_name = args[2] if len(args) > 2 else 'Лист1'

            form, _ = FormConfig.objects.get_or_create(slug=slug)
            form.spreadsheet_id = sheet_id
            form.sheet_name = sheet_name
            form.is_active = True
            form.save()

            # Ensure headers
            if form.columns_order:
                try:
                    google_sheets.ensure_headers(sheet_id, sheet_name, form.columns_order)
                except Exception as e:
                    return f"Form updated, but failed to write headers: {e}"

            # Process pending items
            res = process_pending_submissions()
            return (
                f"✅ Form <code>{slug}</code> linked to sheet <code>{sheet_id}</code>.\n"
                f"Processed {res['processed']} pending items (remaining: {res['remaining']})."
            )

        if cmd == '/create_sheet':
            if len(args) < 2:
                return "Usage: <code>/create_sheet &lt;slug&gt; &lt;admin_email&gt;</code>"
            slug = args[0]
            email = args[1]

            form, _ = FormConfig.objects.get_or_create(slug=slug)
            title = form.title or f"Form Submissions - {slug}"

            try:
                sheet_id, url = google_sheets.create_spreadsheet(title=title, share_email=email)
                form.spreadsheet_id = sheet_id
                form.is_active = True
                form.save()

                if form.columns_order:
                    google_sheets.ensure_headers(sheet_id, form.sheet_name, form.columns_order)

                res = process_pending_submissions()
                return (
                    f"🎉 Created Google Sheet for <code>{slug}</code>!\n"
                    f"URL: {url}\n"
                    f"Shared with: <code>{email}</code>\n"
                    f"Pending processed: {res['processed']}."
                )
            except Exception as e:
                return f"❌ Failed to create spreadsheet: {e}"

        if cmd == '/retry':
            target = args[0] if args else 'all'
            qs = WebhookSubmission.objects.filter(status='FAILED')
            if target != 'all':
                qs = qs.filter(form_slug=target)

            count = qs.update(status='PENDING', attempts=0)
            res = process_pending_submissions()
            return f"🔄 Re-queued {count} submissions. Processed: {res['processed']}."

        if cmd == '/activate':
            if not args:
                return "Usage: <code>/activate &lt;slug&gt;</code>"
            FormConfig.objects.filter(slug=args[0]).update(is_active=True)
            return f"Form <code>{args[0]}</code> activated."

        if cmd == '/deactivate':
            if not args:
                return "Usage: <code>/deactivate &lt;slug&gt;</code>"
            FormConfig.objects.filter(slug=args[0]).update(is_active=False)
            return f"Form <code>{args[0]}</code> deactivated."

        return "Unknown command. Send <code>/help</code> for a list of commands."
