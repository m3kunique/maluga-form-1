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
    edit_telegram_message,
    answer_callback_query,
    get_telegram_config,
    get_admin_chat_ids,
)

# Persistent bottom reply keyboard
MAIN_MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "📋 Формы"}, {"text": "📊 Статистика"}],
        [{"text": "🔄 Повторить сбои"}, {"text": "ℹ️ Помощь"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

# In-memory dictionary tracking interactive user input states
# e.g. { "123456": { "action": "await_email_for_create", "slug": "dance-battle" } }
USER_STATES: dict[str, dict] = {}


def build_forms_view() -> tuple[str, dict]:
    """Build message text and inline keyboard for the list of forms."""
    forms = list(FormConfig.objects.all())
    if not forms:
        text = (
            "📋 <b>Список форм пуст</b>\n\n"
            "Сервис готов к работе. При отправке первого вебхука на "
            "<code>/webhook/&lt;slug&gt;/</code> форма зарегистрируется автоматически."
        )
        kb = {
            "inline_keyboard": [
                [{"text": "🔄 Обновить", "callback_data": "refresh_forms"}]
            ]
        }
        return text, kb

    lines = ["📋 <b>Зарегистрированные формы:</b>\n"]
    inline_buttons = []

    for f in forms:
        counts = WebhookSubmission.objects.filter(form_slug=f.slug).aggregate(
            pending=Count('id', filter=Q(status='PENDING')),
            success=Count('id', filter=Q(status='SUCCESS')),
            failed=Count('id', filter=Q(status='FAILED')),
        )
        status_icon = "🟢" if f.is_active else "🔴"
        status_text = "Активна" if f.is_active else "Отключена"
        sheet_text = (
            f"<a href='https://docs.google.com/spreadsheets/d/{f.spreadsheet_id}/edit'>Таблица</a>"
            if f.spreadsheet_id else "<i>не привязана</i>"
        )

        lines.append(
            f"{status_icon} <b>{f.slug}</b> ({f.title or 'Без названия'})\n"
            f"Статус: {status_text} | Таблица: {sheet_text}\n"
            f"Колонок: {len(f.columns_order)} | Заявок: ⏳ {counts['pending']} | ✅ {counts['success']} | ❌ {counts['failed']}\n"
        )

        row_buttons = [
            {"text": f"📜 Логи: {f.slug}", "callback_data": f"logs:{f.slug}"},
        ]
        if counts['failed'] > 0:
            row_buttons.append({"text": f"🔄 Повторить ({counts['failed']})", "callback_data": f"retry:{f.slug}"})

        toggle_text = "⏸ Отключить" if f.is_active else "▶️ Включить"
        row_buttons.append({"text": toggle_text, "callback_data": f"toggle:{f.slug}"})
        inline_buttons.append(row_buttons)

        # If sheet is not linked yet, add fast-setup buttons
        if not f.spreadsheet_id:
            inline_buttons.append([
                {"text": f"➕ Создать Таблицу для {f.slug}", "callback_data": f"create_prompt:{f.slug}"},
                {"text": f"🔗 Привязать ID к {f.slug}", "callback_data": f"bind_prompt:{f.slug}"},
            ])

    inline_buttons.append([{"text": "🔄 Обновить список", "callback_data": "refresh_forms"}])
    return "\n".join(lines), {"inline_keyboard": inline_buttons}


def build_stats_view() -> tuple[str, dict]:
    """Build message text and inline keyboard for system stats."""
    totals = WebhookSubmission.objects.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='PENDING')),
        success=Count('id', filter=Q(status='SUCCESS')),
        failed=Count('id', filter=Q(status='FAILED')),
        duplicate=Count('id', filter=Q(status='DUPLICATE')),
    )
    forms_count = FormConfig.objects.count()
    active_count = FormConfig.objects.filter(is_active=True).count()

    text = (
        "📊 <b>Статистика сервиса</b>\n\n"
        f"• Всего форм: <b>{forms_count}</b> (активных: {active_count})\n"
        f"• Всего поступило заявок: <b>{totals['total']}</b>\n"
        f"• ⏳ В очереди на отправку: <b>{totals['pending']}</b>\n"
        f"• ✅ Успешно записано в Google Таблицы: <b>{totals['success']}</b>\n"
        f"• ❌ Ошибок доставки: <b>{totals['failed']}</b>\n"
        f"• 🔁 Пропущено дубликатов: <b>{totals['duplicate']}</b>"
    )

    kb = {
        "inline_keyboard": [
            [
                {"text": "🔄 Обновить статистику", "callback_data": "refresh_stats"},
                {"text": "📋 К списку форм", "callback_data": "refresh_forms"},
            ]
        ]
    }
    return text, kb


def build_logs_view(slug: str) -> tuple[str, dict]:
    """Build recent logs for a specific form."""
    logs = list(FormLog.objects.filter(form_slug=slug).order_by('-created_at')[:10])
    if not logs:
        text = f"📜 <b>Логи по форме {slug}:</b>\n\nСобытий пока нет."
    else:
        lines = [f"📜 <b>Последние события по форме {slug}:</b>\n"]
        for log in logs:
            icon = "❌" if log.level == 'ERROR' else ("⚠️" if log.level == 'WARNING' else "ℹ️")
            ts = log.created_at.strftime('%H:%M:%S')
            lines.append(f"{icon} [{ts}] <b>{log.event}</b>: {log.message[:120]}")
        text = "\n".join(lines)

    kb = {
        "inline_keyboard": [
            [
                {"text": "🔄 Обновить логи", "callback_data": f"logs:{slug}"},
                {"text": "🔙 К списку форм", "callback_data": "back_to_forms"},
            ]
        ]
    }
    return text, kb


def build_help_view() -> str:
    """Help message text."""
    return (
        "🤖 <b>Панель управления Google Forms Proxy</b>\n\n"
        "Используйте кнопки нижнего меню для быстрой навигации:\n"
        "• <b>📋 Формы</b> — просмотр всех форм, их таблиц и статусов\n"
        "• <b>📊 Статистика</b> — состояние очереди и счетчики заявок\n"
        "• <b>🔄 Повторить сбои</b> — перезапуск отправки всех ошибочных заявок\n\n"
        "Для связывания новой формы:\n"
        "Отправьте первый вебхук из Яндекс Форм, и бот сам предложит "
        "кнопки для создания или привязки таблицы."
    )

class Command(BaseCommand):
    help = "Run Telegram admin bot daemon with interactive buttons and long-polling"

    def handle(self, *args, **options):
        while True:
            token, default_chat, admin_chat = get_telegram_config()
            if token and token != 'dummy':
                break
            self.stdout.write(self.style.WARNING("TELEGRAM_BOT_TOKEN is not configured or is set to dummy. Waiting 15s..."))
            time.sleep(15)

        admin_ids = get_admin_chat_ids()
        self.stdout.write(self.style.SUCCESS(
            f"Starting Telegram admin bot with buttons for admin ID(s): {', '.join(admin_ids) or 'any'}"
        ))

        offset = None
        while True:
            try:
                updates = get_telegram_updates(offset=offset, timeout=25)
                for update in updates:
                    offset = update['update_id'] + 1

                    # 1. Handle Inline Button Clicks (callback_query)
                    if 'callback_query' in update:
                        self.handle_callback(update['callback_query'], admin_ids)
                        continue

                    # 2. Handle Text Messages
                    message = update.get('message')
                    if message:
                        self.handle_message(message, admin_ids)

            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Bot polling error: {e}"))
                time.sleep(3)

    def handle_callback(self, cb: dict, admin_ids: list[str]):
        cb_id = str(cb.get('id', ''))
        chat_id = str(cb.get('from', {}).get('id', ''))
        message_id = cb.get('message', {}).get('message_id')
        data = cb.get('data', '')

        if admin_ids and chat_id not in admin_ids:
            answer_callback_query(cb_id, text="Доступ запрещен.")
            return

        if data == "refresh_forms" or data == "back_to_forms":
            text, kb = build_forms_view()
            if message_id:
                edit_telegram_message(chat_id, message_id, text, reply_markup=kb)
            else:
                send_telegram_message(text, chat_id=chat_id, reply_markup=kb)
            answer_callback_query(cb_id, text="Обновлено")
            return

        if data == "refresh_stats":
            text, kb = build_stats_view()
            if message_id:
                edit_telegram_message(chat_id, message_id, text, reply_markup=kb)
            else:
                send_telegram_message(text, chat_id=chat_id, reply_markup=kb)
            answer_callback_query(cb_id, text="Статистика обновлена")
            return

        if data.startswith("logs:"):
            slug = data.split(":", 1)[1]
            text, kb = build_logs_view(slug)
            if message_id:
                edit_telegram_message(chat_id, message_id, text, reply_markup=kb)
            else:
                send_telegram_message(text, chat_id=chat_id, reply_markup=kb)
            answer_callback_query(cb_id)
            return

        if data.startswith("toggle:"):
            slug = data.split(":", 1)[1]
            form = FormConfig.objects.filter(slug=slug).first()
            if form:
                form.is_active = not form.is_active
                form.save()
                status_str = "включена" if form.is_active else "отключена"
                answer_callback_query(cb_id, text=f"Форма {slug} {status_str}")
            text, kb = build_forms_view()
            if message_id:
                edit_telegram_message(chat_id, message_id, text, reply_markup=kb)
            return

        if data.startswith("retry:"):
            slug = data.split(":", 1)[1]
            qs = WebhookSubmission.objects.filter(status='FAILED')
            if slug != 'all':
                qs = qs.filter(form_slug=slug)
            count = qs.update(status='PENDING', attempts=0)
            res = process_pending_submissions()
            answer_callback_query(cb_id, text=f"Повтор: {count}, отправлено: {res['processed']}")
            text, kb = build_forms_view()
            if message_id:
                edit_telegram_message(chat_id, message_id, text, reply_markup=kb)
            return

        if data.startswith("create_prompt:"):
            slug = data.split(":", 1)[1]
            USER_STATES[chat_id] = {"action": "await_email_for_create", "slug": slug}
            answer_callback_query(cb_id)
            prompt = (
                f"📧 <b>Создание Google Таблицы для {slug}</b>\n\n"
                f"Отправьте сообщением ваш Google email (например: <code>user@gmail.com</code>).\n"
                f"Бот автоматически создаст таблицу на Диске, запишет названия колонок и выдаст вам доступ редактора:"
            )
            cancel_kb = {
                "inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_action"}]]
            }
            send_telegram_message(prompt, chat_id=chat_id, reply_markup=cancel_kb)
            return

        if data.startswith("bind_prompt:"):
            slug = data.split(":", 1)[1]
            USER_STATES[chat_id] = {"action": "await_sheet_id", "slug": slug}
            answer_callback_query(cb_id)
            prompt = (
                f"🔗 <b>Привязка таблицы к {slug}</b>\n\n"
                f"Отправьте идентификатор таблицы (Spreadsheet ID из адресной строки):\n"
                f"<code>https://docs.google.com/spreadsheets/d/&lt;ID&gt;/edit</code>\n\n"
                f"<i>Не забудьте выдать права редактора сервисному аккаунту!</i>"
            )
            cancel_kb = {
                "inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_action"}]]
            }
            send_telegram_message(prompt, chat_id=chat_id, reply_markup=cancel_kb)
            return

        if data == "cancel_action":
            if chat_id in USER_STATES:
                del USER_STATES[chat_id]
            answer_callback_query(cb_id, text="Действие отменено")
            text, kb = build_forms_view()
            if message_id:
                edit_telegram_message(chat_id, message_id, text, reply_markup=kb)
            return

        answer_callback_query(cb_id)

    def handle_message(self, message: dict, admin_ids: list[str]):
        chat_id = str(message.get('chat', {}).get('id', ''))
        text = (message.get('text') or '').strip()

        # Secret whoami command to identify chat ID before authorization
        cmd_part = text.split()[0].split('@')[0] if text else ''
        if cmd_part == '/whoami-b3NkY24!2':
            user = message.get('from', {})
            username = user.get('username')
            first_name = user.get('first_name', '')
            user_info = f"@{username}" if username else (first_name or "Не указано")

            reply = (
                f"👤 <b>Ваш Telegram Chat ID:</b> <code>{chat_id}</code>\n"
                f"<b>Пользователь:</b> {user_info}\n\n"
                f"Добавьте этот ID в <code>TELEGRAM_ADMIN_CHAT_ID</code>:\n"
                f"<code>TELEGRAM_ADMIN_CHAT_ID={chat_id}</code>"
            )
            send_telegram_message(reply, chat_id=chat_id)
            return

        if admin_ids and chat_id not in admin_ids:
            send_telegram_message("Доступ запрещен.", chat_id=chat_id)
            return

        # Check if user is in an interactive input flow
        if chat_id in USER_STATES:
            state = USER_STATES[chat_id]
            action = state.get("action")
            slug = state.get("slug")

            if text.lower() in ('отмена', 'cancel', '/cancel'):
                del USER_STATES[chat_id]
                send_telegram_message("Действие отменено.", chat_id=chat_id, reply_markup=MAIN_MENU_KEYBOARD)
                return

            if action == "await_email_for_create":
                email = text.strip()
                form, _ = FormConfig.objects.get_or_create(slug=slug)
                title = form.title or f"Форма - {slug}"
                send_telegram_message(f"⏳ Создаю Google Таблицу «{title}» для {email}...", chat_id=chat_id)

                try:
                    sheet_id, url = google_sheets.create_spreadsheet(title=title, share_email=email)
                    form.spreadsheet_id = sheet_id
                    form.is_active = True
                    form.save()

                    if form.columns_order:
                        google_sheets.ensure_headers(sheet_id, form.sheet_name, form.columns_order)

                    res = process_pending_submissions()
                    del USER_STATES[chat_id]

                    result_msg = (
                        f"🎉 <b>Таблица успешно создана!</b>\n\n"
                        f"• Форма: <code>{slug}</code>\n"
                        f"• Ссылка: <a href='{url}'>{title}</a>\n"
                        f"• Доступ выдан: <code>{email}</code>\n"
                        f"• Отложенных записей выгружено: {res['processed']}"
                    )
                    send_telegram_message(result_msg, chat_id=chat_id, reply_markup=MAIN_MENU_KEYBOARD)
                    return
                except Exception as e:
                    send_telegram_message(
                        f"❌ Ошибка создания таблицы: {e}\nПопробуйте снова или отправьте «Отмена».",
                        chat_id=chat_id,
                    )
                    return

            if action == "await_sheet_id":
                sheet_id = text.strip()
                # Clean URL if user pasted full link
                if "/spreadsheets/d/" in sheet_id:
                    sheet_id = sheet_id.split("/spreadsheets/d/")[1].split("/")[0]

                form, _ = FormConfig.objects.get_or_create(slug=slug)
                form.spreadsheet_id = sheet_id
                form.is_active = True
                form.save()

                if form.columns_order:
                    try:
                        google_sheets.ensure_headers(sheet_id, form.sheet_name, form.columns_order)
                    except Exception as e:
                        print(f"Warning writing headers: {e}")

                res = process_pending_submissions()
                del USER_STATES[chat_id]

                result_msg = (
                    f"✅ <b>Таблица успешно привязана!</b>\n\n"
                    f"• Форма: <code>{slug}</code>\n"
                    f"• Spreadsheet ID: <code>{sheet_id}</code>\n"
                    f"• Отложенных записей выгружено: {res['processed']}"
                )
                send_telegram_message(result_msg, chat_id=chat_id, reply_markup=MAIN_MENU_KEYBOARD)
                return

        # Main menu actions (both button text and commands)
        if text in ('📋 Формы', '/forms'):
            text_out, kb = build_forms_view()
            send_telegram_message(text_out, chat_id=chat_id, reply_markup=kb)
            return

        if text in ('📊 Статистика', '/stats'):
            text_out, kb = build_stats_view()
            send_telegram_message(text_out, chat_id=chat_id, reply_markup=kb)
            return

        if text in ('🔄 Повторить сбои', '🔄 Повторить ошибки', '/retry'):
            count = WebhookSubmission.objects.filter(status='FAILED').update(status='PENDING', attempts=0)
            res = process_pending_submissions()
            msg = (
                f"🔄 <b>Повторная отправка</b>\n\n"
                f"Перезапущено ошибочных заявок: {count}\n"
                f"Успешно выгружено: {res['processed']}"
            )
            send_telegram_message(msg, chat_id=chat_id, reply_markup=MAIN_MENU_KEYBOARD)
            return

        if text in ('ℹ️ Помощь', '/help', '/start'):
            send_telegram_message(build_help_view(), chat_id=chat_id, reply_markup=MAIN_MENU_KEYBOARD)
            return

        # Fallback for unrecognized text
        send_telegram_message(
            "Выберите нужный раздел в меню ниже:",
            chat_id=chat_id,
            reply_markup=MAIN_MENU_KEYBOARD,
        )
