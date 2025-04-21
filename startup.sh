#!/bin/bash

# Nakazuje skryptowi zakończyć działanie natychmiast, jeśli jakakolwiek komenda się nie powiedzie
set -e

echo "Starting custom startup script..."

# --- Instalacja Sterownika Microsoft ODBC Driver for SQL Server ---
echo "Attempting to install Microsoft ODBC Driver for SQL Server..."

# Sprawdź, czy sterownik już istnieje (prosta weryfikacja, aby potencjalnie przyspieszyć start)
# UWAGA: To nie jest w 100% niezawodne, bo /etc może nie być trwałe między restartami
# ale może pomóc w niektórych scenariuszach restartu bez zmiany obrazu.
DRIVER_INSTALLED=$(odbcinst -q -d | grep -c "ODBC Driver 18 for SQL Server" || true) # Użyj wersji 18 lub 17

if [ "$DRIVER_INSTALLED" -eq "0" ]; then
  echo "ODBC Driver not found or verification failed, proceeding with installation."

  # 1. Aktualizacja listy pakietów i instalacja zależności
  echo "Updating package lists and installing dependencies..."
  apt-get update -y
  # Dodano lsb-release do zależności, aby móc odczytać wersję OS
  apt-get install -y --no-install-recommends wget gnupg apt-transport-https ca-certificates lsb-release unixodbc-dev

  # 2. Dodanie klucza GPG Microsoftu
  echo "Adding Microsoft GPG key..."
  wget -qO- https://packages.microsoft.com/keys/microsoft.asc | apt-key add -

  # 3. Dynamiczne dodawanie repozytorium Microsoftu na podstawie wykrytej dystrybucji
  echo "Detecting OS distribution and adding Microsoft repository..."
  # Załaduj zmienne z /etc/os-release (jeśli istnieje)
  if [ -f /etc/os-release ]; then
      . /etc/os-release
  else
      echo "WARNING: Cannot find /etc/os-release to determine OS version. Falling back to Debian 11 (bullseye)."
      ID="debian"
      VERSION_ID="11"
  fi

  REPO_URL=""
  # Mapowanie popularnych wersji App Service Linux na URL repozytorium MS
  # Sprawdź aktualność na stronie Microsoft!
  if [[ "$ID" == "debian" && "$VERSION_ID" == "11" ]]; then
      REPO_URL="https://packages.microsoft.com/config/debian/11/prod.list"
  elif [[ "$ID" == "debian" && "$VERSION_ID" == "12" ]]; then
      REPO_URL="https://packages.microsoft.com/config/debian/12/prod.list"
  elif [[ "$ID" == "ubuntu" && "$VERSION_ID" == "20.04" ]]; then
      REPO_URL="https://packages.microsoft.com/config/ubuntu/20.04/prod.list"
  elif [[ "$ID" == "ubuntu" && "$VERSION_ID" == "22.04" ]]; then
      REPO_URL="https://packages.microsoft.com/config/ubuntu/22.04/prod.list"
  else
      echo "ERROR: Unsupported OS distribution ($ID $VERSION_ID) for automatic repository configuration."
      echo "Please update the startup.sh script with the correct repository URL from Microsoft documentation."
      exit 1 # Zakończ skrypt, jeśli nie można ustawić repozytorium
  fi

  echo "Using repository URL: $REPO_URL"
  wget -qO- $REPO_URL > /etc/apt/sources.list.d/mssql-release.list

  # 4. Aktualizacja listy pakietów po dodaniu repozytorium
  echo "Updating package lists again..."
  apt-get update -y

  # 5. Akceptacja licencji EULA (użyj wersji 18 lub 17)
  echo "Accepting EULA..."
  echo "msodbcsql18 msodbcsql/accepted-eula select true" | debconf-set-selections
  # Lub dla wersji 17:
  # echo "msodbcsql17 msodbcsql/accepted-eula select true" | debconf-set-selections

  # 6. Instalacja sterownika i narzędzi unixODBC (użyj tej samej wersji co w EULA)
  echo "Installing ODBC driver (msodbcsql18) and unixodbc-dev..."
  apt-get install -y --no-install-recommends msodbcsql18
  # Lub dla wersji 17:
  # apt-get install -y --no-install-recommends msodbcsql17

  # 7. Czyszczenie cache apt (opcjonalne)
  echo "Cleaning up apt cache..."
  apt-get clean && rm -rf /var/lib/apt/lists/*

  echo "ODBC Driver installation process finished."

else
  echo "ODBC Driver already seems to be installed (based on odbcinst check), skipping installation."
fi

# --- Uruchomienie Aplikacji Flask ---
echo "Starting Flask application using Gunicorn..."

# WAŻNE: Dostosuj tę linię do sposobu, w jaki uruchamiasz swoją aplikację!
# Zakładamy, że główny plik to 'app.py', a instancja aplikacji Flask nazywa się 'app'.
# Parametry --bind i --timeout są typowe dla App Service.
# Jeśli używasz innego serwera WSGI (np. waitress) lub masz inną strukturę, zmień tę komendę.
gunicorn --bind=0.0.0.0 --timeout 600 app:app

echo "Startup script finished."