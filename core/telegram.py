import json
import os
import traceback
import urllib.request
import urllib.error

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


def send_telegram_message(text: str, chat_id: str | None = None, parse_mode: str = 'HTML') -> bool:
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
        payload = json.dumps({
            'chat_id': str(target),
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True,
        }).encode('utf-8')

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


def get_telegram_updates(offset: int | None = None, timeout: int = 25) -> list[dict]:
    """Long-poll Telegram Bot API for updates."""
    token, _, _ = get_telegram_config()
    if not token:
        return []

    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout={timeout}"
    if offset is not None:
        url += f"&offset={offset}"

    req = urllib.request.Request(url, headers={'User-Agent': 'FormSyncBot/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                if data.get('ok'):
                    return data.get('result', [])
    except Exception as e:
        if 'timed out' not in str(e).lower():
            print(f"Error fetching Telegram updates: {e}")
    return []
