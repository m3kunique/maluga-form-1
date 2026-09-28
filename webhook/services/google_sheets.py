import gspread
from google.oauth2.service_account import Credentials
from django.conf import settings

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]


def get_gspread_client() -> gspread.Client:
    credentials_file = getattr(settings, 'CREDENTIALS_FILE', 'google_credentials.json')
    creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return gspread.authorize(creds)


def get_worksheet(spreadsheet_id: str, sheet_name: str = 'Лист1') -> gspread.Worksheet:
    client = get_gspread_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # Fall back to first sheet if specified sheet not found
        return spreadsheet.sheet1


def ensure_headers(spreadsheet_id: str, sheet_name: str, headers: list[str]) -> bool:
    """Check if header row exists in the spreadsheet. If empty, write headers."""
    if not spreadsheet_id or not headers:
        return False

    worksheet = get_worksheet(spreadsheet_id, sheet_name)
    existing_row = worksheet.row_values(1)
    if not existing_row:
        worksheet.append_row(headers, value_input_option='USER_ENTERED')
        return True
    return False


def append_rows(spreadsheet_id: str, sheet_name: str, rows: list[list[str]]) -> int:
    """Batch append rows to Google Sheets."""
    if not spreadsheet_id or not rows:
        return 0

    worksheet = get_worksheet(spreadsheet_id, sheet_name)
    worksheet.append_rows(rows, value_input_option='USER_ENTERED')
    return len(rows)


def create_spreadsheet(title: str, share_email: str | None = None) -> tuple[str, str]:
    """Create a new Google Spreadsheet and optionally grant Editor access to an email.

    Returns (spreadsheet_id, spreadsheet_url).
    """
    client = get_gspread_client()
    spreadsheet = client.create(title)
    if share_email:
        spreadsheet.share(share_email, perm_type='user', role='writer', notify=False)
    return spreadsheet.id, spreadsheet.url
