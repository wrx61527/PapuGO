# Krok 1: Wybierz obraz bazowy Python (np. Python 3.10 na Debian 11 Bullseye)
FROM python:3.10-slim-bullseye

# Ustaw etykiety (opcjonalne)
LABEL maintainer="Patryk <pilecki.p@icloud.com>"
LABEL description="Aplikacja Flask z połączeniem do RDS PostgreSQL"

# Ustaw zmienne środowiskowe
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1 # Zalecane dla logów w kontenerach

# Krok 2: Skonfiguruj środowisko aplikacji
WORKDIR /app

# Skopiuj najpierw plik zależności, aby wykorzystać cache warstw Dockera
COPY requirements.txt .

# Zainstaluj zależności Python (w tym psycopg2-binary)
RUN pip install --no-cache-dir -r requirements.txt

# Skopiuj resztę kodu aplikacji do obrazu
COPY . .

# Krok 3: Zdefiniuj, jak uruchomić aplikację
# Użyj Gunicorn (lub innego serwera WSGI). Nasłuchuj na porcie podanym przez App Runner ($PORT).
# Dostosuj 'app:app' jeśli Twój plik/zmienna Flask nazywa się inaczej.
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "--workers", "2", "--threads", "4", "--timeout", "60", "app:app"]