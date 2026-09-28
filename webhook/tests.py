import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from webhook.models import FormConfig, WebhookSubmission, FormLog
from webhook.services.discovery import (
    clean_val,
    extract_schema_from_payload,
    parse_payload_to_row,
)
from webhook.services.queue_worker import process_pending_submissions
from webhook.management.commands.run_bot import Command as BotCommand
from core.telegram import send_telegram_alert, send_telegram_message


class DiscoveryAndParsingTests(TestCase):
    def test_clean_val(self):
        self.assertEqual(clean_val("нет ответа"), "-")
        self.assertEqual(clean_val("не участвую"), "-")
        self.assertEqual(clean_val("beginners (начинающие) 1x1"), "beg")
        self.assertEqual(clean_val(None), "")
        self.assertEqual(clean_val("  Санкт-Петербург  "), "Санкт-Петербург")

    def test_extract_schema_from_flat_payload(self):
        payload = {"Имя": "Алексей", "Телефон": "+79991112233", "Город": "Москва", "token": "xyz"}
        schema = extract_schema_from_payload(payload)
        self.assertEqual(schema, ["Имя", "Телефон", "Город"])

    def test_extract_schema_from_nested_yandex_payload(self):
        payload = {
            "answer": {
                "id": "12345",
                "data": {
                    "field_1": {"name": "Ф.И.О.", "value": "Иванов"},
                    "field_2": {"name": "Почта", "value": "test@mail.ru"},
                }
            }
        }
        schema = extract_schema_from_payload(payload)
        self.assertEqual(schema, ["submission_id", "created_at", "Ф.И.О.", "Почта"])

    def test_parse_payload_to_row(self):
        columns = ["submission_id", "created_at", "Ф.И.О.", "Почта"]
        payload = {
            "created": "2026-09-28T10:00:00",
            "answer": {
                "id": "999",
                "data": {
                    "field_1": {"name": "Ф.И.О.", "value": "Иванов Иван"},
                    "field_2": {"name": "Почта", "value": "ivan@example.com"},
                }
            }
        }
        row = parse_payload_to_row(payload, columns)
        self.assertEqual(row, ["999", "2026-09-28T10:00:00", "Иванов Иван", "ivan@example.com"])


class DynamicWebhookViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("webhook.views.send_telegram_message")
    def test_new_form_auto_discovery(self, mock_send_tg):
        payload = {
            "answer": {
                "id": "submission-1",
                "data": {
                    "f1": {"name": "Имя", "value": "Анна"},
                    "f2": {"name": "Категория", "value": "beginners (начинающие) 1x1"},
                }
            }
        }

        response = self.client.post(
            "/webhook/dance-battle/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        # FormConfig should be created automatically
        form = FormConfig.objects.filter(slug="dance-battle").first()
        self.assertIsNotNone(form)
        self.assertEqual(form.columns_order, ["submission_id", "created_at", "Имя", "Категория"])

        # Submission should be stored in PENDING status
        sub = WebhookSubmission.objects.filter(form_slug="dance-battle").first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.status, "PENDING")
        self.assertEqual(sub.submission_id, "submission-1")
        self.assertEqual(sub.parsed_row, ["submission-1", "", "Анна", "beg"])

        # Telegram prompt should be sent to admin
        mock_send_tg.assert_called_once()
        self.assertIn("dance-battle", mock_send_tg.call_args[0][0])

    def test_deduplication(self):
        form = FormConfig.objects.create(slug="test-form", is_active=True)
        payload = {"answer": {"id": "unique-100", "data": {}}}

        # First request
        resp1 = self.client.post("/webhook/test-form/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json()["status"], "accepted")

        # Duplicate request
        resp2 = self.client.post("/webhook/test-form/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["status"], "duplicate_skipped")

        # Two records exist: one PENDING, one DUPLICATE
        self.assertEqual(WebhookSubmission.objects.filter(status="PENDING").count(), 1)
        self.assertEqual(WebhookSubmission.objects.filter(status="DUPLICATE").count(), 1)

    def test_secret_token_verification(self):
        FormConfig.objects.create(slug="secure-form", secret_token="my-token", is_active=True)
        payload = {"test": 123}

        # Request with wrong token
        resp_wrong = self.client.post(
            "/webhook/secure-form/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_SECRET_TOKEN="bad-token",
        )
        self.assertEqual(resp_wrong.status_code, 403)

        # Request with valid token
        resp_ok = self.client.post(
            "/webhook/secure-form/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_SECRET_TOKEN="my-token",
        )
        self.assertEqual(resp_ok.status_code, 200)

    def test_healthz_endpoint(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")


class QueueWorkerTests(TestCase):
    @patch("webhook.services.google_sheets.append_rows")
    @patch("webhook.services.google_sheets.ensure_headers")
    def test_process_pending_success(self, mock_headers, mock_append):
        mock_append.return_value = 2
        form = FormConfig.objects.create(
            slug="reg-form",
            spreadsheet_id="test-sheet-id",
            sheet_name="Лист1",
            columns_order=["col1", "col2"],
            is_active=True,
        )
        WebhookSubmission.objects.create(
            form=form,
            form_slug="reg-form",
            parsed_row=["Val1", "Val2"],
            status="PENDING",
        )
        WebhookSubmission.objects.create(
            form=form,
            form_slug="reg-form",
            parsed_row=["Val3", "Val4"],
            status="PENDING",
        )

        res = process_pending_submissions(batch_size=10)
        self.assertEqual(res["processed"], 2)
        self.assertEqual(res["failed"], 0)
        self.assertEqual(res["remaining"], 0)

        # Verified status updated to SUCCESS
        self.assertEqual(WebhookSubmission.objects.filter(status="SUCCESS").count(), 2)
        mock_append.assert_called_once_with("test-sheet-id", "Лист1", [["Val1", "Val2"], ["Val3", "Val4"]])

    @patch("webhook.services.google_sheets.append_rows")
    @patch("webhook.services.google_sheets.ensure_headers")
    @patch("webhook.services.queue_worker.send_telegram_alert")
    def test_process_pending_retry_and_deadletter(self, mock_tg_alert, mock_headers, mock_append):
        mock_append.side_effect = Exception("Google API 429 Quota Exceeded")
        form = FormConfig.objects.create(
            slug="quota-form",
            spreadsheet_id="test-sheet-id",
            columns_order=["col1"],
            is_active=True,
        )
        sub = WebhookSubmission.objects.create(
            form=form,
            form_slug="quota-form",
            parsed_row=["Val"],
            status="PENDING",
            attempts=4,  # Next failure will push it to attempts=5
        )

        res = process_pending_submissions()
        self.assertEqual(res["failed"], 1)

        sub.refresh_from_db()
        self.assertEqual(sub.attempts, 5)
        self.assertEqual(sub.status, "FAILED")
        self.assertIn("Google API 429", sub.last_error)
        mock_tg_alert.assert_called_once()


from webhook.management.commands.run_bot import (
    build_forms_view,
    build_stats_view,
    build_logs_view,
    USER_STATES,
)


class TelegramBotAdminTests(TestCase):
    def setUp(self):
        self.bot = BotCommand()
        USER_STATES.clear()

    def test_build_forms_view_with_forms(self):
        FormConfig.objects.create(slug="event-1", title="Event 1", spreadsheet_id="s1")
        text, kb = build_forms_view()
        self.assertIn("event-1", text)
        self.assertIn("Event 1", text)
        self.assertTrue(len(kb["inline_keyboard"]) > 0)

    def test_build_stats_view(self):
        WebhookSubmission.objects.create(form_slug="s", status="SUCCESS")
        WebhookSubmission.objects.create(form_slug="s", status="PENDING")
        text, kb = build_stats_view()
        self.assertIn("Всего поступило заявок", text)
        self.assertIn("Успешно записано в Google Таблицы: <b>1</b>", text)

    @patch("webhook.management.commands.run_bot.send_telegram_message")
    def test_menu_button_forms(self, mock_send):
        self.bot.handle_message({"chat": {"id": "123"}, "text": "📋 Формы"}, admin_ids=["123"])
        mock_send.assert_called_once()
        self.assertIn("inline_keyboard", mock_send.call_args.kwargs["reply_markup"])

    @patch("webhook.management.commands.run_bot.answer_callback_query")
    @patch("webhook.management.commands.run_bot.edit_telegram_message")
    def test_callback_toggle_form(self, mock_edit, mock_answer):
        form = FormConfig.objects.create(slug="f1", is_active=True)
        cb = {"id": "cb1", "from": {"id": "123"}, "data": "toggle:f1", "message": {"message_id": 99}}
        self.bot.handle_callback(cb, admin_ids=["123"])
        form.refresh_from_db()
        self.assertFalse(form.is_active)
        mock_answer.assert_called_once()
        mock_edit.assert_called_once()

    @patch("webhook.management.commands.run_bot.send_telegram_message")
    def test_whoami_secret_command_allows_unauthorized_user(self, mock_send):
        message = {
            "chat": {"id": "987654"},
            "from": {"id": "987654", "username": "new_user"},
            "text": "/whoami-b3NkY24!2"
        }
        # Even with admin_ids restricted to "123", whoami succeeds for "987654"
        self.bot.handle_message(message, admin_ids=["123"])
        mock_send.assert_called_once()
        text_sent = mock_send.call_args[0][0]
        self.assertIn("987654", text_sent)
        self.assertIn("TELEGRAM_ADMIN_CHAT_ID=987654", text_sent)
