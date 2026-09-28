import traceback
import json
import gspread
from google.oauth2.service_account import Credentials
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
CREDENTIALS_FILE = 'google_credentials.json'
SPREADSHEET_ID = '1X0koco9C3PieSI_L3cuaIxjxkSEhISVrN6DULoEBsfA'

VALUE_MAPPING = {
    "нет ответа": "-",
    "не участвую": "-",
    "beginners (начинающие) 1x1": "beg",
    "pro (профессионалы) 1x1": "pro",
    "intermediate (продолжающие) 1x1": "int",
    "даю согласие на обработку персональных данных.": "да",
    "с условиями участия ознакомился и полностью согласен.": "да",
}

def clean_val(val):
    if val is None:
        return ""
    val_str = str(val).strip()
    return VALUE_MAPPING.get(val_str.lower(), val_str)

@csrf_exempt
def yandex_webhook_to_sheets(request):
    if request.method != 'POST':
        return JsonResponse({"error": "Method not allowed"}, status=405)

    EXPECTED_TOKEN = '0kYQ3XoDfJvV0XrlXxzL2XI3Vt8urysb'
    received_token = request.headers.get('X-Secret-Token') or request.META.get('HTTP_X_SECRET_TOKEN')

    if received_token != EXPECTED_TOKEN:
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
        print("Received payload:", json.dumps(data, ensure_ascii=False))

        # 1. Если данные уже пришли плоским словарем
        if 'answer' not in data:
            # Задаем жесткий порядок колонок, чтобы данные не перемешивались
            columns_order = [
                "Ф.И.",
                "Никнейм",
                "Город",
                "Ваш номер телефона для связи",
                "Электронная почта",
                "Аккаунт в Телеграм",
                "Школа танцев\\ команда\\тренер (если есть)",
                "Animation",
                "Popping",
                "Waving",
                "Интенсив",
                "Заполняя данную форму вы даете согласие на обработку персональных данных.",
                "Заполняя данную форму вы подтверждаете, что ознакомились и соглашаетесь с условиями участия, и не будете их оспаривать."
            ]

            # Если поле есть в словаре - берем его, иначе берем по порядку из data.values()
            if any(k in data for k in columns_order):
                row_values = [clean_val(data.get(col, "")) for col in columns_order]
            else:
                row_values = [clean_val(v) for v in data.values()]

        # 2. Если вдруг пришел стандартный сырой payload от Яндекса
        else:
            answer_data = data.get('answer', {}).get('data', {})
            row_values = []
            for _, field_val in answer_data.items():
                val = field_val.get('value')
                if isinstance(val, list):
                    texts = [clean_val(item.get('text', str(item))) for item in val if isinstance(item, dict)]
                    row_values.append(", ".join(texts) if texts else "-")
                else:
                    row_values.append(clean_val(val))

            sub_id = str(data.get('answer', {}).get('id', ''))
            created_at = str(data.get('created', ''))
            row_values = [sub_id, created_at] + row_values

        print("Writing row to sheets:", row_values)

        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        sheet.append_row(row_values)

        return JsonResponse({"status": "success"}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        print(f"Error processing webhook: {repr(e)}")
        traceback.print_exc()
        return JsonResponse({"error": "Internal server error"}, status=500)
