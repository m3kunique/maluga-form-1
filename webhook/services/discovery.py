import json

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


def extract_schema_from_payload(payload: dict) -> list[str]:
    """Extract list of question/column names from incoming payload."""
    if not isinstance(payload, dict):
        return []

    # 1. Standard raw Yandex Forms format: payload has 'answer' with 'data'
    if 'answer' in payload and isinstance(payload['answer'], dict):
        data_block = payload['answer'].get('data', {})
        columns = ["submission_id", "created_at"]
        for key, field_val in data_block.items():
            # In Yandex Forms, field_val can have a 'name' or 'label' or 'slug'
            col_name = None
            if isinstance(field_val, dict):
                col_name = field_val.get('name') or field_val.get('label') or field_val.get('question')
            if not col_name:
                col_name = str(key)
            columns.append(col_name)
        return columns

    # 2. Flat dictionary format
    excluded_meta_keys = {'_token', 'x_secret_token', 'token'}
    columns = [str(k) for k in payload.keys() if str(k).lower() not in excluded_meta_keys]
    return columns


def parse_payload_to_row(payload: dict, columns_order: list[str]) -> list[str]:
    """Given a payload and columns_order, produce the exact list of string cell values."""
    if not columns_order:
        return []

    # If nested Yandex Forms
    if 'answer' in payload and isinstance(payload['answer'], dict):
        sub_id = str(payload.get('answer', {}).get('id', ''))
        created_at = str(payload.get('created', ''))
        data_block = payload.get('answer', {}).get('data', {})

        # Build mapping of field_key and field_name to cleaned string value
        value_by_key = {}
        value_by_name = {}
        for key, field_val in data_block.items():
            if isinstance(field_val, dict):
                val = field_val.get('value')
                if isinstance(val, list):
                    texts = [clean_val(item.get('text', str(item))) for item in val if isinstance(item, dict)]
                    cleaned = ", ".join(texts) if texts else "-"
                else:
                    cleaned = clean_val(val)

                name = field_val.get('name') or field_val.get('label')
                if name:
                    value_by_name[str(name)] = cleaned
            else:
                cleaned = clean_val(field_val)
            value_by_key[str(key)] = cleaned

        row = []
        for col in columns_order:
            if col == "submission_id":
                row.append(sub_id)
            elif col == "created_at":
                row.append(created_at)
            elif col in value_by_name:
                row.append(value_by_name[col])
            elif col in value_by_key:
                row.append(value_by_key[col])
            else:
                row.append("")
        return row

    # Flat dictionary payload
    row = [clean_val(payload.get(col, "")) for col in columns_order]
    return row
