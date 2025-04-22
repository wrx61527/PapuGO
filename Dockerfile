# Użyj lekkiego obrazu z Pythonem
FROM python:3.9-slim

# Ustaw katalog roboczy
WORKDIR /app

# Skopiuj requirements i zainstaluj zależności
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Skopiuj cały kod aplikacji
COPY . .

# Otwórz port wymagany przez App Runner
EXPOSE 8080

# Zmienna środowiskowa portu (na wszelki wypadek)
ENV PORT=8080

# Komenda startowa
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
