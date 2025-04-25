# PapuGO - Platforma do Zamawiania Jedzenia Online

[![Status Wdrożenia](https://img.shields.io/badge/deployment-AWS%20App%20Runner-orange)](https://aws.amazon.com/apprunner/)
[![Baza Danych](https://img.shields.io/badge/database-PostgreSQL%20(RDS)-blue)](https://aws.amazon.com/rds/postgresql/)
[![Magazyn Plików](https://img.shields.io/badge/storage-AWS%20S3-red)](https://aws.amazon.com/s3/)

PapuGO to aplikacja webowa typu platforma do zamawiania jedzenia, umożliwiająca użytkownikom przeglądanie restauracji, składanie zamówień, a administratorom zarządzanie treścią. Projekt został zbudowany z wykorzystaniem nowoczesnych technologii chmurowych AWS.

## Kluczowe Funkcjonalności

**Dla Użytkowników:**

* Przeglądanie i wyszukiwanie restauracji.
* Przeglądanie menu restauracji.
* Rejestracja i logowanie.
* Dodawanie dań do koszyka i zarządzanie nim.
* Składanie zamówień (z symulowaną płatnością).
* Przeglądanie historii i śledzenie statusu zamówień.

**Dla Administratorów:**

* Panel administracyjny.
* Zarządzanie restauracjami (CRUD + zdjęcia).
* Zarządzanie daniami (CRUD + zdjęcia).
* Zarządzanie użytkownikami (przeglądanie, edycja, usuwanie, uprawnienia).
* Przeglądanie wszystkich zamówień i zmiana ich statusów.

## Stos Technologiczny

* **Backend:** Python, Flask
* **Frontend:** HTML, CSS, Bootstrap 5, Jinja2
* **Baza Danych:** PostgreSQL
* **Interfejs Bazy Danych:** Psycopg2
* **Integracja z AWS:** Boto3
* **Bezpieczeństwo:** Werkzeug Security (hashowanie haseł)

## Architektura Chmurowa (AWS)

* **Platforma Uruchomieniowa:** AWS App Runner (zarządzane kontenery, automatyczne skalowanie, load balancing, SSL)
* **Baza Danych:** Amazon RDS for PostgreSQL (zarządzana, skalowalna, niezawodna baza danych)
* **Magazyn Plików:** Amazon S3 (skalowalny magazyn obiektów dla zdjęć restauracji i dań)
* **CI/CD:** GitHub Actions (automatyczny deployment na App Runner po pushu do gałęzi `main`)

## Deployment

Aplikacja jest skonfigurowana do automatycznego wdrożenia na **AWS App Runner** za pomocą **GitHub Actions**. Zmiany wypchnięte do gałęzi `main` automatycznie wyzwalają proces budowania i wdrażania nowej wersji. Konfiguracja usług AWS (App Runner, RDS, S3) znajduje się w konsoli AWS.