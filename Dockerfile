FROM python:3.11-slim

# Ustawienie katalogu roboczego
WORKDIR /app

# Skopiowanie plików aplikacji
COPY . /app

# Instalacja zależności
RUN pip install --no-cache-dir -r requirements.txt

# Ustawienie portu
ENV PORT=8080

# Polecenie startowe
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
