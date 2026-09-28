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


def extract_field_label(field_val, fallback_key: str) -> str:
    """Extract a clean human-readable string column name from field_val."""
    if isinstance(field_val, dict):
        # 1. Direct string label/name
        for prop in ('name', 'label', 'question'):
            v = field_val.get(prop)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                label = v.get('ru') or v.get('en') or v.get('name') or v.get('label') or v.get('slug')
                if isinstance(label, str) and label.strip():
                    return label.strip()

        # 2. Check question metadata if separate
        q = field_val.get('question')
        if isinstance(q, dict):
            q_label = q.get('name') or q.get('label') or q.get('slug')
            if isinstance(q_label, str) and q_label.strip():
                return q_label.strip()

        if field_val.get('slug') and isinstance(field_val.get('slug'), str):
            return field_val.get('slug').strip()

    return str(fallback_key)


def extract_schema_from_payload(payload: dict) -> list[str]:
    """Extract list of question/column names from incoming payload."""
    if not isinstance(payload, dict):
        return []

    # 1. Standard raw Yandex Forms format: payload has 'answer' with 'data'
    if 'answer' in payload and isinstance(payload['answer'], dict):
        data_block = payload['answer'].get('data', {})
        columns = ["submission_id", "created_at"]
        for key, field_val in data_block.items():
            col_name = extract_field_label(field_val, str(key))
            columns.append(str(col_name))
        return columns

    # 2. Flat dictionary format
    excluded_meta_keys = {'_token', 'x_secret_token', 'token'}
    columns = [str(k) for k in payload.keys() if str(k).lower() not in excluded_meta_keys]
    return columns


def parse_payload_to_row(payload: dict, columns_order: list) -> list[str]:
    """Given a payload and columns_order, produce the exact list of string cell values."""
    if not columns_order:
        return []

    cleaned_columns = []
    for col in columns_order:
        if isinstance(col, dict):
            cleaned_columns.append(extract_field_label(col, "field"))
        else:
            cleaned_columns.append(str(col))

    # If nested Yandex Forms
    if 'answer' in payload and isinstance(payload['answer'], dict):
        sub_id = str(payload.get('answer', {}).get('id', ''))
        created_at = str(payload.get('created', ''))
        data_block = payload.get('answer', {}).get('data', {})

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

                name = extract_field_label(field_val, str(key))
                value_by_name[name] = cleaned
            else:
                cleaned = clean_val(field_val)
            value_by_key[str(key)] = cleaned

        row = []
        for col in cleaned_columns:
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
    row = [clean_val(payload.get(col, "")) for col in cleaned_columns]
    return row

