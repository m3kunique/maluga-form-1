import json
import os
import socket
import traceback
import urllib.request
import urllib.error

# Force IPv4 resolution to prevent [Errno 101] Network is unreachable on IPv4-only networks
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_fallback_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == 0 or family == socket.AF_UNSPEC:
        try:
            res = _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
            if res:
                return res
        except Exception:
            pass
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _ipv4_fallback_getaddrinfo

try:
    from django.conf import settings
except ImportError:
    settings = None


def get_telegram_config() -> tuple[str, str, str]:
    """Return (token, default_chat_id, admin_chat_id)."""
    token = ""
    chat_id = ""
    admin_chat_id = ""

    if settings:
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
        admin_chat_id = getattr(settings, 'TELEGRAM_ADMIN_CHAT_ID', '')

    if not token:
        token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not chat_id:
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    if not admin_chat_id:
        admin_chat_id = os.getenv('TELEGRAM_ADMIN_CHAT_ID', chat_id)

    return token, chat_id, admin_chat_id


def get_admin_chat_ids() -> list[str]:
    """Return a list of authorized admin chat IDs (supports comma-separated list)."""
    _, chat_id, admin_chat_id = get_telegram_config()
    raw = admin_chat_id or chat_id
    if not raw:
        return []
    return [cid.strip() for cid in str(raw).split(',') if cid.strip()]


def send_telegram_message(
    text: str,
    chat_id: str | None = None,
    parse_mode: str = 'HTML',
    reply_markup: dict | None = None,
) -> bool:
    """Send a message to a specific Telegram chat, or broadcast to all admins if chat_id is None."""
    token, default_chat_id, _ = get_telegram_config()
    if not token:
        return False

    targets = [chat_id] if chat_id else get_admin_chat_ids()
    if not targets and default_chat_id:
        targets = [default_chat_id]

    if not targets:
        return False

    success = False
    for target in targets:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = {
            'chat_id': str(target),
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True,
        }
        if reply_markup is not None:
            body['reply_markup'] = reply_markup

        payload = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    success = True
        except Exception as e:
            print(f"Failed to send Telegram message to {target}: {e}")

    return success


def answer_callback_query(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge Telegram callback query to dismiss loading state on button."""
    token, _, _ = get_telegram_config()
    if not token:
        return False

    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = json.dumps({
        'callback_query_id': str(callback_query_id),
        'text': text,
    }).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception as e:
        print(f"Failed to answer callback query: {e}")
        return False


def edit_telegram_message(
    chat_id: str,
    message_id: int,
    text: str,
    parse_mode: str = 'HTML',
    reply_markup: dict | None = None,
) -> bool:
    """Edit existing Telegram message text and inline keyboard."""
    token, _, _ = get_telegram_config()
    if not token:
        return False

    url = f"https://api.telegram.org/bot{token}/editMessageText"
    body = {
        'chat_id': str(chat_id),
        'message_id': message_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }
    if reply_markup is not None:
        body['reply_markup'] = reply_markup

    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception as e:
        print(f"Failed to edit message: {e}")
        return False


def send_telegram_alert(source: str, error: Exception | str, details: dict | None = None) -> bool:
    """Send an alert to all configured Telegram admin chats."""
    error_text = str(error)
    tb = traceback.format_exc()
    if tb.strip() == 'NoneType: None':
        tb_str = ''
    else:
        tb_str = f"\n\nTraceback:\n{tb[-1500:]}"

    details_str = ''
    if details:
        details_items = [f"{k}: {v}" for k, v in details.items()]
        details_str = "\n\nDetails:\n" + "\n".join(details_items)

    message = (
        f"🚨 <b>Service Alert</b>\n"
        f"<b>Source:</b> {source}\n"
        f"<b>Error:</b> <code>{error_text}</code>"
        f"{details_str}"
        f"{tb_str}"
    )

    return send_telegram_message(message)


def get_bot_info() -> dict | None:
    """Fetch info about the current bot user via getMe."""
    token, _, _ = get_telegram_config()
    if not token or token == 'dummy':
        return None
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'FormSyncBot/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get('ok'):
                return data.get('result')
            print(f"Telegram getMe failed: {data}", flush=True)
    except Exception as e:
        print(f"Error fetching bot info: {e}", flush=True)
    return None


def delete_telegram_webhook(drop_pending_updates: bool = False) -> bool:
    """Delete any active Telegram webhook so that getUpdates polling works."""
    token, _, _ = get_telegram_config()
    if not token or token == 'dummy':
        return False
    drop_param = "?drop_pending_updates=true" if drop_pending_updates else ""
    url = f"https://api.telegram.org/bot{token}/deleteWebhook{drop_param}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'FormSyncBot/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            ok = res_data.get('ok', False)
            if ok:
                print("Successfully removed Telegram webhook for long-polling.", flush=True)
            return ok
    except Exception as e:
        print(f"deleteWebhook error: {e}", flush=True)
        return False


def get_telegram_updates(offset: int | None = None, timeout: int = 25) -> list[dict]:
    """Long-poll Telegram Bot API for updates."""
    token, _, _ = get_telegram_config()
    if not token or token == 'dummy':
        return []

    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout={timeout}"
    if offset is not None:
        url += f"&offset={offset}"

    req = urllib.request.Request(url, headers={'User-Agent': 'FormSyncBot/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get('ok'):
                return data.get('result', [])
            print(f"Telegram getUpdates response not ok: {data}", flush=True)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        print(f"Telegram HTTP {e.code} error: {err_body}", flush=True)
        if e.code == 409:
            print("Webhook is active on Telegram servers. Deleting webhook...", flush=True)
            delete_telegram_webhook()
    except Exception as e:
        if 'timed out' not in str(e).lower():
            print(f"Error fetching Telegram updates: {e}", flush=True)
    return []
