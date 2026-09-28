FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта (включая google_credentials.json)
COPY . .

# Открываем порт, на котором будет работать Gunicorn
EXPOSE 8000

# Запускаем Gunicorn (замените myproject на имя папки, где лежит ваш wsgi.py)
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
