# -*- coding: utf-8 -*-
import os
import pyodbc
import uuid # Do generowania unikalnych nazw plików
import logging # Do lepszego logowania
from functools import wraps
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    send_from_directory, abort
)

# --- Konfiguracja Początkowa ---
load_dotenv() # Ładuj zmienne z .env

app = Flask(__name__)

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO)

# Klucz sesji
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'J6QuuwwBz70xxOtmFLeNbmxiKCbuOka2VD')

# Konfiguracja Bazy Danych
connection_string = os.getenv('DATABASE_CONNECTION_STRING')
if not connection_string:
    app.logger.critical("KRYTYCZNY BŁĄD: Brak DATABASE_CONNECTION_STRING w zmiennych środowiskowych!")

# Konfiguracja Wgrywania Plików
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Opcjonalnie: Maksymalny rozmiar pliku (np. 16MB)
# app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Upewnij się, że folder uploads istnieje
if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
        app.logger.info(f"Utworzono folder {UPLOAD_FOLDER}")
    except OSError as e:
        app.logger.error(f"Nie można utworzyć folderu {UPLOAD_FOLDER}: {e}")


# --- Funkcje Pomocnicze ---

def get_db_connection():
    """Nawiązuje połączenie z bazą danych Azure SQL."""
    if not connection_string:
         app.logger.error("Próba połączenia z bazą bez connection stringa.")
         return None
    try:
        # Ustawienie kodowania na utf-8 dla połączenia
        conn = pyodbc.connect(connection_string, encoding='utf-8')
        # Opcjonalnie: ustawienie autocommit na False dla lepszej kontroli transakcji
        # conn.autocommit = False
        return conn
    except pyodbc.Error as ex:
        sqlstate = ex.args[0]
        app.logger.error(f"Błąd połączenia z bazą danych (SQLSTATE: {sqlstate}): {ex}")
    except Exception as e:
        app.logger.error(f"Nieoczekiwany błąd połączenia z bazą danych: {e}")
    return None

def allowed_file(filename):
    """Sprawdza, czy rozszerzenie pliku jest dozwolone."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def format_address(street, number, code, city):
    """Tworzy sformatowany ciąg adresu z dostępnych części."""
    address_parts = [part for part in [street, number] if part]
    city_parts = [part for part in [code, city] if part]
    full_address = " ".join(address_parts)
    city_str = " ".join(city_parts)
    if city_str:
        full_address += (", " if full_address else "") + city_str
    return full_address if full_address else None # Zwróć None jeśli nic nie ma

# --- Dekorator Wymagający Logowania jako Admin ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Dostęp wymaga uprawnień administratora.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Trasy Związane z Użytkownikiem (Frontend) ---

@app.route('/')
def index():
    """Wyświetla stronę główną z listą restauracji."""
    conn = get_db_connection()
    restaurants_display = []
    if not conn:
        flash("Nie udało się połączyć z bazą danych.", "danger")
        # Nadal renderuj stronę, ale bez danych
        return render_template('index.html', restaurants=restaurants_display)

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants ORDER BY Name")
        columns = [column[0] for column in cursor.description]
        restaurants = [dict(zip(columns, row)) for row in cursor.fetchall()]
        for r in restaurants:
             r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
             restaurants_display.append(r)
    except Exception as e:
        app.logger.error(f"Błąd pobierania restauracji: {e}")
        flash("Wystąpił błąd podczas pobierania listy restauracji.", "danger")
    finally:
        conn.close() # Zawsze zamykaj połączenie

    return render_template('index.html', restaurants=restaurants_display)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Obsługuje logowanie i rejestrację użytkownika."""
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
             flash('Nazwa użytkownika i hasło są wymagane.', 'warning')
             return redirect(url_for('login'))

        conn = get_db_connection()
        if not conn:
            flash('Błąd połączenia z bazą danych. Spróbuj ponownie później.', 'danger')
            return redirect(url_for('login'))

        cursor = conn.cursor()
        user_logged_in = False # Flaga pomocnicza
        try:
            if action == 'login':
                cursor.execute("SELECT UserID, Username, IsAdmin, Password FROM Users WHERE Username = ?", (username,))
                user_row = cursor.fetchone()
                if user_row and user_row.Password == password: 
                    session['user_id'] = user_row.UserID
                    session['username'] = user_row.Username
                    session['is_admin'] = user_row.IsAdmin
                    session.permanent = True # Opcjonalnie: sesja trwała
                    app.logger.info(f"Użytkownik '{username}' zalogowany pomyślnie.")
                    flash('Zalogowano pomyślnie!', 'success')
                    user_logged_in = True
                    # Przekieruj na odpowiednią stronę
                    redirect_url = url_for('admin_dashboard') if user_row.IsAdmin else url_for('index')
                    # Zamknij połączenie PRZED przekierowaniem
                    conn.close()
                    return redirect(redirect_url)
                else:
                    flash('Nieprawidłowa nazwa użytkownika lub hasło.', 'danger')

            elif action == 'register':
                try:
                
                    cursor.execute("INSERT INTO Users (Username, Password) VALUES (?, ?)", (username, password)) 
                    conn.commit()
                    app.logger.info(f"Zarejestrowano nowego użytkownika: '{username}'.")
                    flash('Rejestracja zakończona pomyślnie. Możesz się teraz zalogować.', 'success')
                except pyodbc.IntegrityError:
                    # Błąd unikalności Username
                    flash('Nazwa użytkownika jest już zajęta.', 'warning')
                    conn.rollback() # Wycofaj transakcję
                except Exception as e:
                    conn.rollback() # Wycofaj transakcję
                    app.logger.error(f"Błąd rejestracji użytkownika '{username}': {e}")
                    flash('Wystąpił nieoczekiwany błąd podczas rejestracji.', 'danger')
        except Exception as e:
            app.logger.error(f"Nieoczekiwany błąd podczas logowania/rejestracji dla '{username}': {e}")
            flash('Wystąpił nieoczekiwany błąd serwera.', 'danger')
        finally:
            # Zamknij połączenie tylko jeśli nie zostało zamknięte wcześniej (przy udanym logowaniu)
            if conn and not user_logged_in:
                conn.close()

        # Wróć do strony logowania po nieudanej próbie logowania lub po rejestracji
        return redirect(url_for('login'))

    # Metoda GET - wyświetl formularz
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Wylogowuje użytkownika."""
    username = session.get('username')
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('is_admin', None)
    session.pop('cart', None) # Wyczyść też koszyk
    if username:
         app.logger.info(f"Użytkownik '{username}' wylogowany.")
    flash('Wylogowano pomyślnie.', 'info')
    return redirect(url_for('index'))


@app.route('/restaurant/<int:restaurant_id>')
def restaurant_detail(restaurant_id):
    """Wyświetla szczegóły restauracji i jej menu."""
    conn = get_db_connection()
    restaurant_display = None
    dishes_display = []

    if not conn:
        flash("Nie udało się połączyć z bazą danych.", "danger")
        return redirect(url_for('index')) # Poprawka: Zawsze zwracaj redirect

    cursor = conn.cursor()
    try:
        # Pobierz dane restauracji
        cursor.execute("SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
        columns_rest = [column[0] for column in cursor.description]
        restaurant_row = cursor.fetchone()

        if restaurant_row:
             restaurant_display = dict(zip(columns_rest, restaurant_row))
             restaurant_display['FullAddress'] = format_address(
                 restaurant_display.get('Street'),
                 restaurant_display.get('StreetNumber'),
                 restaurant_display.get('PostalCode'),
                 restaurant_display.get('City')
             ) or "Brak adresu"

             # Pobierz dania dla tej restauracji
             cursor.execute("SELECT DishID, Name, Description, Price, ImageURL FROM Dishes WHERE RestaurantID = ? ORDER BY Name", (restaurant_id,))
             columns_dish = [column[0] for column in cursor.description]
             dishes_display = [dict(zip(columns_dish, row)) for row in cursor.fetchall()]

             # Renderuj szablon tylko jeśli restauracja została znaleziona
             # Połączenie zostanie zamknięte w bloku finally
             return render_template('restaurant_detail.html', restaurant=restaurant_display, dishes=dishes_display)

        else:
             # Restauracja o podanym ID nie została znaleziona
             flash('Nie znaleziono restauracji o podanym ID.', 'warning')
             # Połączenie zostanie zamknięte w bloku finally
             return redirect(url_for('index')) # Przekieruj

    except Exception as e:
         # Obsłuż błędy, które mogły wystąpić w bloku try
         app.logger.error(f"Błąd pobierania szczegółów restauracji {restaurant_id}: {e}")
         flash("Wystąpił błąd podczas pobierania danych restauracji.", "danger")
         # Połączenie zostanie zamknięte w bloku finally
         return redirect(url_for('index')) # Przekieruj w razie błędu

    finally:
         # Zawsze zamykaj połączenie, jeśli zostało otwarte
         if conn:
             conn.close()
             app.logger.debug(f"Zamknięto połączenie DB dla /restaurant/{restaurant_id}")


@app.route('/search')
def search():
    """Wyświetla wyniki wyszukiwania restauracji."""
    query = request.args.get('query', '').strip()
    restaurants_display = []

    # Wykonaj wyszukiwanie tylko jeśli zapytanie nie jest puste
    if query:
        conn = get_db_connection()
        if not conn:
             flash("Nie udało się połączyć z bazą danych.", "danger")
             # Renderuj szablon bez wyników
             return render_template('index.html', restaurants=restaurants_display, search_query=query)

        cursor = conn.cursor()
        try:
            search_term = f"%{query}%"
            sql = """
                SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL
                FROM Restaurants
                WHERE Name LIKE ? OR CuisineType LIKE ? OR City LIKE ?
                ORDER BY Name
            """
            cursor.execute(sql, (search_term, search_term, search_term))
            columns = [column[0] for column in cursor.description]
            restaurants = [dict(zip(columns, row)) for row in cursor.fetchall()]
            for r in restaurants:
                 r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
                 restaurants_display.append(r)
            if not restaurants_display:
                 flash(f"Nie znaleziono restauracji pasujących do '{query}'.", "info")
        except Exception as e:
             app.logger.error(f"Błąd wyszukiwania restauracji ('{query}'): {e}")
             flash("Wystąpił błąd podczas wyszukiwania.", "danger")
        finally:
             if conn:
                  conn.close()
    # Jeśli query było puste, nie wyświetlaj błędu, po prostu pokaż pustą listę/stronę główną

    # Zawsze renderuj szablon 'index.html', przekazując wyniki (mogą być puste)
    return render_template('index.html', restaurants=restaurants_display, search_query=query)


# --- Trasy Związane z Koszykiem i Zamówieniami ---

@app.route('/cart/add/<int:dish_id>', methods=['POST'])
def add_to_cart(dish_id):
    """Dodaje danie do koszyka w sesji."""
    if 'user_id' not in session:
         flash('Musisz być zalogowany, aby dodać coś do koszyka.', 'warning')
         return redirect(url_for('login'))

    # Sprawdź poprawność ilości
    try:
        quantity = int(request.form.get('quantity', 1))
        if quantity <= 0:
             flash('Ilość musi być większa od zera.', 'warning')
             return redirect(request.referrer or url_for('index'))
    except ValueError:
         flash('Nieprawidłowa ilość.', 'warning')
         return redirect(request.referrer or url_for('index'))

    # Pobierz dane dania z bazy
    conn = get_db_connection()
    dish_data = None
    redirect_url = request.referrer or url_for('index') # Domyślny URL przekierowania

    if not conn:
        flash("Nie udało się połączyć z bazą danych.", "danger")
        return redirect(redirect_url)

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DishID, Name, Price FROM Dishes WHERE DishID = ?", (dish_id,))
        columns = [column[0] for column in cursor.description]
        dish_row = cursor.fetchone()
        if dish_row:
            dish_data = dict(zip(columns, dish_row))
        else:
            flash('Nie znaleziono dania o podanym ID.', 'danger')
            # Przekieruj, jeśli danie nie istnieje
            conn.close()
            return redirect(redirect_url)

    except Exception as e:
        app.logger.error(f"Błąd pobierania dania {dish_id} do koszyka: {e}")
        flash("Wystąpił błąd podczas pobierania danych dania.", "danger")
        conn.close()
        return redirect(redirect_url)
    finally:
        # Upewnij się, że połączenie jest zamknięte, jeśli nie zostało wcześniej
        # (np. w przypadku błędu lub gdy danie nie istnieje)
        if conn.connected == 1: # Sprawdź czy połączenie jest nadal aktywne
             conn.close()

    # Dodaj do koszyka w sesji (jeśli dane dania zostały pobrane)
    if dish_data:
        if 'cart' not in session:
            session['cart'] = {}
        cart = session.get('cart', {})
        dish_id_str = str(dish_id)
        try:
            price = float(dish_data['Price'])
            current_quantity = cart.get(dish_id_str, {}).get('quantity', 0)
            cart[dish_id_str] = {
                'name': dish_data['Name'],
                'price': price,
                'quantity': current_quantity + quantity
            }
            session['cart'] = cart
            session.modified = True # Jawne oznaczenie modyfikacji sesji
            flash(f"Dodano '{dish_data['Name']}' (x{quantity}) do koszyka.", 'success')
        except (KeyError, ValueError) as e:
             app.logger.error(f"Błąd przetwarzania danych dania {dish_id} w koszyku: {e}")
             flash("Wystąpił błąd podczas dodawania produktu do koszyka.", "danger")

    # Przekieruj z powrotem
    return redirect(redirect_url)


@app.route('/cart')
def view_cart():
    """Wyświetla zawartość koszyka."""
    if 'user_id' not in session:
         flash('Musisz być zalogowany, aby zobaczyć koszyk.', 'warning')
         return redirect(url_for('login'))

    cart = session.get('cart', {})
    items_display = []
    total_price = 0.0
    cart_changed = False # Flaga wskazująca, czy koszyk był modyfikowany

    if cart:
        for item_id in list(cart.keys()): # Użyj list() do bezpiecznego usuwania
            item_data = cart[item_id]
            try:
                price = float(item_data['price'])
                quantity = int(item_data['quantity'])
                if quantity <= 0: # Usuń elementy z zerową lub ujemną ilością
                     raise ValueError("Ilość musi być dodatnia")
                item_total = price * quantity
                items_display.append({
                    'id': item_id,
                    'name': item_data.get('name', f'Produkt ID {item_id}'),
                    'price': price,
                    'quantity': quantity,
                    'total': item_total
                })
                total_price += item_total
            except (KeyError, ValueError, TypeError) as e:
                 app.logger.warning(f"Usuwanie nieprawidłowego elementu z koszyka ID {item_id}: {e}")
                 flash(f"Produkt ID {item_id} miał nieprawidłowe dane i został usunięty z koszyka.", "warning")
                 del cart[item_id]
                 cart_changed = True

    # Zapisz sesję tylko jeśli coś zostało usunięte
    if cart_changed:
        session['cart'] = cart
        session.modified = True

    return render_template('cart.html', cart_items=items_display, total_price=total_price)


@app.route('/cart/remove/<dish_id>', methods=['POST'])
def remove_from_cart(dish_id):
    """Usuwa danie z koszyka w sesji."""
    if 'user_id' not in session:
        flash('Musisz być zalogowany, aby modyfikować koszyk.', 'warning')
        return redirect(url_for('login'))

    cart = session.get('cart', {})
    dish_id_str = str(dish_id)

    if dish_id_str in cart:
        item_name = cart[dish_id_str].get('name', f'Produkt ID {dish_id_str}')
        del cart[dish_id_str]
        session['cart'] = cart
        session.modified = True # Oznacz sesję jako zmodyfikowaną
        flash(f"Usunięto '{item_name}' z koszyka.", 'info')
    else:
        flash('Tego produktu nie ma już w koszyku.', 'warning')

    return redirect(url_for('view_cart')) # Zawsze wracaj do koszyka


@app.route('/checkout', methods=['POST'])
def checkout():
    """Przetwarza zamówienie na podstawie koszyka."""
    if 'user_id' not in session:
         flash('Musisz być zalogowany, aby złożyć zamówienie.', 'warning')
         return redirect(url_for('login'))

    cart = session.get('cart', {})
    if not cart:
         flash('Twój koszyk jest pusty. Nie można złożyć zamówienia.', 'warning')
         return redirect(url_for('view_cart'))

    # Walidacja koszyka przed próbą zapisu do DB
    total_price = 0.0
    order_items_data = []
    is_cart_valid = True
    for item_id, item_data in cart.items():
        try:
            price = float(item_data['price'])
            quantity = int(item_data['quantity'])
            if quantity <= 0:
                raise ValueError("Ilość musi być dodatnia")
            total_price += price * quantity
            order_items_data.append({
                'dish_id': int(item_id),
                'quantity': quantity,
                'price_per_item': price
            })
        except (KeyError, ValueError, TypeError) as e:
             app.logger.error(f"Błąd danych w koszyku podczas checkout dla ID {item_id}: {e}.")
             flash(f"Wystąpił problem z danymi produktu ID {item_id}. Popraw koszyk przed złożeniem zamówienia.", "danger")
             is_cart_valid = False
             break # Przerwij walidację przy pierwszym błędzie

    if not is_cart_valid:
         return redirect(url_for('view_cart'))

    # Jeśli koszyk jest poprawny, kontynuuj z bazą danych
    conn = get_db_connection()
    if not conn:
         flash('Błąd połączenia z bazą danych. Nie można złożyć zamówienia.', 'danger')
         return redirect(url_for('view_cart'))

    user_id = session['user_id']
    cursor = conn.cursor()
    try:
        # Rozpocznij transakcję (jeśli sterownik/baza obsługuje i nie jest w autocommit)
        # conn.autocommit = False

        # 1. Utwórz zamówienie
        cursor.execute("INSERT INTO Orders (UserID, TotalPrice, Status) OUTPUT INSERTED.OrderID VALUES (?, ?, ?)",
                       (user_id, total_price, 'Złożone'))
        new_order_id = cursor.fetchone()[0]
        app.logger.info(f"Utworzono zamówienie #{new_order_id} dla UserID: {user_id}")

        # 2. Dodaj pozycje zamówienia
        insert_item_sql = "INSERT INTO OrderItems (OrderID, DishID, Quantity, PricePerItem) VALUES (?, ?, ?, ?)"
        # Przygotuj dane do executemany dla wydajności (opcjonalnie)
        items_to_insert = [(new_order_id, item['dish_id'], item['quantity'], item['price_per_item']) for item in order_items_data]
        cursor.executemany(insert_item_sql, items_to_insert)
        app.logger.info(f"Dodano {len(items_to_insert)} pozycji do zamówienia #{new_order_id}")

        conn.commit() # Zatwierdź transakcję

        # 3. Wyczyść koszyk PO POMYŚLNYM ZAPISIE
        session.pop('cart', None)
        session.modified = True # Jawne oznaczenie

        # 4. Przekieruj na stronę potwierdzenia
        flash('Zamówienie zostało złożone pomyślnie!', 'success')
        return redirect(url_for('order_confirmation', order_id=new_order_id))

    except Exception as e:
        conn.rollback() # Wycofaj zmiany w razie błędu
        app.logger.error(f"KRYTYCZNY BŁĄD podczas składania zamówienia (UserID: {user_id}): {e}")
        flash(f'Wystąpił krytyczny błąd podczas składania zamówienia. Skontaktuj się z obsługą.', 'danger')
        return redirect(url_for('view_cart'))
    finally:
        conn.close()


@app.route('/order_confirmation/<int:order_id>')
def order_confirmation(order_id):
     """Wyświetla stronę potwierdzenia zamówienia."""
     if 'user_id' not in session:
         flash('Musisz być zalogowany, aby zobaczyć potwierdzenie.', 'warning')
         return redirect(url_for('login'))

     # W przyszłości: Sprawdź, czy zalogowany użytkownik jest właścicielem tego zamówienia
     # conn = get_db_connection()... SELECT UserID FROM Orders WHERE OrderID = ? ... conn.close()

     return render_template('order_confirmation.html', order_id=order_id)


# --- Trasy Związane z Panelem Administratora ---

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Wyświetla główny panel administratora."""
    return render_template('admin/admin_dashboard.html')


@app.route('/admin/restaurants', methods=['GET', 'POST'])
@admin_required
def manage_restaurants():
    """Zarządza restauracjami (dodawanie, usuwanie, wyświetlanie)."""
    conn = get_db_connection()
    if not conn:
        flash('Błąd połączenia z bazą danych.', 'danger')
        return redirect(url_for('admin_dashboard'))

    cursor = conn.cursor()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            form_submitted = False # Flaga do kontroli przekierowania

            if action == 'add':
                form_submitted = True
                name = request.form.get('name', '').strip()
                cuisine = request.form.get('cuisine', '').strip() or None
                street = request.form.get('street', '').strip() or None
                street_number = request.form.get('street_number', '').strip() or None
                postal_code = request.form.get('postal_code', '').strip() or None
                city = request.form.get('city', '').strip() or None
                image_file = request.files.get('image')
                image_filename = None

                if not name:
                    flash('Nazwa restauracji jest wymagana.', 'warning')
                else:
                    # --- Obsługa pliku ---
                    if image_file and image_file.filename != '':
                        if allowed_file(image_file.filename):
                            original_filename = secure_filename(image_file.filename)
                            extension = original_filename.rsplit('.', 1)[1].lower()
                            unique_filename = f"restaurant_{uuid.uuid4()}.{extension}"
                            save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                            try:
                                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                                image_file.save(save_path)
                                image_filename = unique_filename
                                app.logger.info(f"Zapisano obrazek restauracji: {save_path}")
                            except Exception as e:
                                app.logger.error(f"Błąd zapisu pliku obrazka restauracji {original_filename}: {e}")
                                flash(f'Nie udało się zapisać pliku obrazka: {e}', 'danger')
                        else:
                            flash('Niedozwolony typ pliku obrazka.', 'warning')
                    # --- Koniec obsługi pliku ---

                    # Zapis do DB
                    try:
                        sql = "INSERT INTO Restaurants (Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL) VALUES (?, ?, ?, ?, ?, ?, ?)"
                        cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_filename))
                        conn.commit()
                        flash(f'Dodano restaurację "{name}".', 'success')
                    except Exception as e:
                        conn.rollback()
                        app.logger.error(f"Błąd INSERT restauracji '{name}': {e}")
                        flash('Błąd podczas dodawania restauracji do bazy.', 'danger')
                        # Usuń plik jeśli zapis DB się nie powiódł
                        if image_filename and os.path.exists(save_path):
                            try:
                                os.remove(save_path)
                                app.logger.info(f"Usunięto plik {save_path} po błędzie zapisu do DB.")
                            except Exception as rm_err:
                                app.logger.error(f"Nie udało się usunąć pliku {save_path} po błędzie DB: {rm_err}")

            elif action == 'delete':
                 form_submitted = True
                 restaurant_id_str = request.form.get('restaurant_id')
                 if not restaurant_id_str:
                      flash('Brak ID restauracji do usunięcia.', 'warning')
                 else:
                     try:
                         restaurant_id = int(restaurant_id_str)
                         # Najpierw pobierz nazwę pliku do usunięcia
                         cursor.execute("SELECT ImageURL FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
                         result = cursor.fetchone()
                         image_to_delete_path = None
                         if result and result[0]:
                              image_to_delete_path = os.path.join(app.config['UPLOAD_FOLDER'], result[0])

                         # Usuń wpis z bazy (ON DELETE CASCADE zajmie się daniami)
                         cursor.execute("DELETE FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
                         conn.commit()
                         app.logger.info(f"Usunięto restaurację ID: {restaurant_id}")
                         flash(f'Usunięto restaurację (ID: {restaurant_id}).', 'success')

                         # Jeśli usunięcie z DB się powiodło, usuń plik
                         if image_to_delete_path and os.path.exists(image_to_delete_path):
                             try:
                                 os.remove(image_to_delete_path)
                                 app.logger.info(f"Usunięto plik obrazka restauracji: {image_to_delete_path}")
                             except Exception as e:
                                 app.logger.warning(f"Nie udało się usunąć pliku obrazka {image_to_delete_path}: {e}")
                                 flash(f'Nie udało się usunąć pliku obrazka: {result[0]}', 'warning')

                     except ValueError:
                         flash('Nieprawidłowe ID restauracji.', 'warning')
                     except Exception as e:
                         conn.rollback()
                         app.logger.error(f"Błąd DELETE restauracji (ID: {restaurant_id_str}): {e}")
                         flash('Błąd podczas usuwania restauracji.', 'danger')

            # Po akcji POST zawsze przekieruj
            if form_submitted:
                 conn.close() # Zamknij połączenie przed przekierowaniem
                 return redirect(url_for('manage_restaurants'))

        # Metoda GET - wyświetlanie listy
        cursor.execute("SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants ORDER BY Name")
        columns = [column[0] for column in cursor.description]
        restaurants = [dict(zip(columns, row)) for row in cursor.fetchall()]
        restaurants_display = []
        for r in restaurants:
            r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "-"
            restaurants_display.append(r)

        return render_template('admin/manage_restaurants.html', restaurants=restaurants_display)

    except Exception as e:
         app.logger.error(f"Nieoczekiwany błąd w manage_restaurants: {e}")
         flash("Wystąpił nieoczekiwany błąd.", "danger")
         return redirect(url_for('admin_dashboard'))
    finally:
         if conn and conn.connected == 1:
             conn.close()


@app.route('/admin/dishes', methods=['GET', 'POST'])
@app.route('/admin/dishes/<int:restaurant_id>', methods=['GET', 'POST'])
@admin_required
def manage_dishes(restaurant_id=None):
    """Zarządza daniami dla wybranej restauracji."""
    conn = get_db_connection()
    if not conn:
        flash('Błąd połączenia z bazą danych.', 'danger')
        return redirect(url_for('admin_dashboard'))

    cursor = conn.cursor()
    restaurants_list = []
    dishes_display = []
    selected_restaurant_name = None
    redirect_to_restaurant_id = restaurant_id # Domyślnie przekieruj tam skąd przyszliśmy (lub None)

    try:
        # Zawsze pobieraj listę restauracji
        cursor.execute("SELECT RestaurantID, Name FROM Restaurants ORDER BY Name")
        restaurants_list = cursor.fetchall()

        if request.method == 'POST':
            action = request.form.get('action')
            rest_id_form_str = request.form.get('restaurant_id') # ID restauracji z formularza
            form_submitted = False # Flaga

            try:
                # Ustal ID restauracji, której dotyczy akcja
                current_restaurant_id = int(rest_id_form_str) if rest_id_form_str else None
                if not current_restaurant_id:
                     # Jeśli nie ma ID w formularzu, spróbuj użyć ID z URL (ważne przy usuwaniu z widoku konkretnej restauracji)
                     current_restaurant_id = restaurant_id
            except ValueError:
                 flash('Nieprawidłowe ID restauracji w formularzu.', 'danger')
                 conn.close()
                 return redirect(url_for('manage_dishes'))

            if action == 'add':
                form_submitted = True
                if not current_restaurant_id:
                     flash('Musisz wybrać restaurację, aby dodać danie.', 'warning')
                else:
                    name = request.form.get('name', '').strip()
                    description = request.form.get('description', '').strip() or None
                    price_str = request.form.get('price')
                    image_file = request.files.get('image')
                    image_filename = None
                    price_decimal = None

                    # Walidacja
                    if not name: flash('Nazwa dania jest wymagana.', 'warning')
                    elif not price_str: flash('Cena dania jest wymagana.', 'warning')
                    else:
                        try:
                            price_decimal = float(price_str)
                            if price_decimal < 0:
                                 flash('Cena nie może być ujemna.', 'warning')
                                 price_decimal = None
                        except ValueError:
                             flash('Cena musi być liczbą.', 'warning')

                    # Jeśli walidacja OK, przetwarzaj plik i zapisz
                    if name and price_decimal is not None:
                        # --- Obsługa pliku ---
                        if image_file and image_file.filename != '':
                            if allowed_file(image_file.filename):
                                original_filename = secure_filename(image_file.filename)
                                extension = original_filename.rsplit('.', 1)[1].lower()
                                unique_filename = f"dish_{uuid.uuid4()}.{extension}" # Prefix 'dish_'
                                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                                try:
                                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                                    image_file.save(save_path)
                                    image_filename = unique_filename
                                    app.logger.info(f"Zapisano obrazek dania: {save_path}")
                                except Exception as e:
                                    app.logger.error(f"Błąd zapisu pliku obrazka dania {original_filename}: {e}")
                                    flash(f'Nie udało się zapisać pliku obrazka: {e}', 'danger')
                            else:
                                flash('Niedozwolony typ pliku obrazka.', 'warning')
                        # --- Koniec obsługi pliku ---

                        # Zapis do DB
                        try:
                            sql = "INSERT INTO Dishes (RestaurantID, Name, Description, Price, ImageURL) VALUES (?, ?, ?, ?, ?)"
                            cursor.execute(sql, (current_restaurant_id, name, description, price_decimal, image_filename))
                            conn.commit()
                            flash(f'Dodano danie "{name}".', 'success')
                        except Exception as e:
                            conn.rollback()
                            app.logger.error(f"Błąd INSERT dania '{name}' dla RestaurantID {current_restaurant_id}: {e}")
                            flash('Błąd podczas dodawania dania do bazy.', 'danger')
                            # Usuń plik jeśli zapis DB się nie powiódł
                            if image_filename and os.path.exists(save_path):
                                try: os.remove(save_path)
                                except Exception: pass # Ignoruj błąd usuwania

            elif action == 'delete':
                 form_submitted = True
                 dish_id_str = request.form.get('dish_id')
                 if not dish_id_str:
                      flash('Brak ID dania do usunięcia.', 'warning')
                 elif not current_restaurant_id: # Potrzebne do przekierowania
                      flash('Brak ID restauracji dla kontekstu usunięcia.', 'warning')
                 else:
                      try:
                          dish_id = int(dish_id_str)
                          # Pobierz nazwę pliku
                          cursor.execute("SELECT ImageURL FROM Dishes WHERE DishID = ?", (dish_id,))
                          result = cursor.fetchone()
                          image_to_delete_path = None
                          if result and result[0]:
                               image_to_delete_path = os.path.join(app.config['UPLOAD_FOLDER'], result[0])

                          # Usuń z DB
                          cursor.execute("DELETE FROM Dishes WHERE DishID = ?", (dish_id,))
                          conn.commit()
                          app.logger.info(f"Usunięto danie ID: {dish_id}")
                          flash(f'Usunięto danie (ID: {dish_id}).', 'success')

                          # Usuń plik
                          if image_to_delete_path and os.path.exists(image_to_delete_path):
                              try:
                                  os.remove(image_to_delete_path)
                                  app.logger.info(f"Usunięto plik obrazka dania: {image_to_delete_path}")
                              except Exception as e:
                                  app.logger.warning(f"Nie udało się usunąć pliku obrazka {image_to_delete_path}: {e}")
                                  flash(f'Nie udało się usunąć pliku obrazka: {result[0]}', 'warning')

                      except ValueError:
                           flash('Nieprawidłowe ID dania.', 'warning')
                      except Exception as e:
                          conn.rollback()
                          app.logger.error(f"Błąd DELETE dania (ID: {dish_id_str}): {e}")
                          flash('Błąd podczas usuwania dania.', 'danger')

            # Po akcji POST, ustal gdzie przekierować
            if form_submitted:
                 conn.close() # Zamknij połączenie
                 # Przekieruj do widoku dań dla restauracji, której dotyczyła akcja
                 redirect_to_restaurant_id = current_restaurant_id or restaurant_id
                 if redirect_to_restaurant_id:
                      return redirect(url_for('manage_dishes', restaurant_id=redirect_to_restaurant_id))
                 else:
                      return redirect(url_for('manage_dishes')) # Przekieruj na stronę ogólną

        # Metoda GET - wyświetlanie dań dla wybranej restauracji
        if restaurant_id:
            cursor.execute("SELECT Name FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
            rest_name_row = cursor.fetchone()
            if rest_name_row:
                selected_restaurant_name = rest_name_row[0]
                cursor.execute("SELECT DishID, Name, Description, Price, ImageURL FROM Dishes WHERE RestaurantID = ? ORDER BY Name", (restaurant_id,))
                columns = [column[0] for column in cursor.description]
                dishes_display = [dict(zip(columns, row)) for row in cursor.fetchall()]
            else:
                flash(f"Restauracja o ID {restaurant_id} nie istnieje.", "warning")
                conn.close()
                return redirect(url_for('manage_dishes'))

        # Zawsze renderuj szablon
        return render_template('admin/manage_dishes.html',
                               dishes=dishes_display,
                               restaurants=restaurants_list,
                               selected_restaurant_id=restaurant_id,
                               selected_restaurant_name=selected_restaurant_name)

    except Exception as e:
         app.logger.error(f"Nieoczekiwany błąd w manage_dishes (RestaurantID: {restaurant_id}): {e}")
         flash("Wystąpił nieoczekiwany błąd.", "danger")
         return redirect(url_for('admin_dashboard'))
    finally:
         if conn and conn.connected == 1:
             conn.close()


@app.route('/admin/users')
@admin_required
def manage_users():
    """Wyświetla listę użytkowników."""
    conn = get_db_connection()
    users_display = []
    if not conn:
        flash("Nie udało się połączyć z bazą danych.", "danger")
        return render_template('admin/manage_users.html', users=users_display) # Pokaż pustą stronę

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT UserID, Username, IsAdmin FROM Users ORDER BY Username")
        columns = [column[0] for column in cursor.description]
        users_display = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as e:
        app.logger.error(f"Błąd pobierania użytkowników: {e}")
        flash("Wystąpił błąd podczas pobierania listy użytkowników.", "danger")
    finally:
        conn.close()

    return render_template('admin/manage_users.html', users=users_display)


@app.route('/admin/orders', methods=['GET', 'POST'])
@admin_required
def view_orders():
    """Wyświetla zamówienia i pozwala zmieniać ich status."""
    conn = get_db_connection()
    if not conn:
        flash('Błąd połączenia z bazą danych.', 'danger')
        return redirect(url_for('admin_dashboard'))

    cursor = conn.cursor()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            order_id_str = request.form.get('order_id')
            new_status = request.form.get('status')
            allowed_statuses = ['Złożone', 'W realizacji', 'Dostarczone', 'Anulowane']
            form_submitted = False

            if action == 'update_status':
                form_submitted = True
                if order_id_str and new_status:
                    if new_status in allowed_statuses:
                        try:
                            order_id = int(order_id_str)
                            cursor.execute("UPDATE Orders SET Status = ? WHERE OrderID = ?", (new_status, order_id))
                            conn.commit()
                            app.logger.info(f"Zmieniono status zamówienia #{order_id} na '{new_status}' przez admina.")
                            flash(f'Zaktualizowano status zamówienia #{order_id} na "{new_status}".', 'success')
                        except ValueError:
                             flash('Nieprawidłowe ID zamówienia.', 'warning')
                        except Exception as e:
                            conn.rollback()
                            app.logger.error(f"Błąd UPDATE statusu zamówienia (ID: {order_id_str}): {e}")
                            flash('Błąd podczas aktualizacji statusu zamówienia.', 'danger')
                    else:
                        flash('Wybrano nieprawidłowy status zamówienia.', 'warning')
                else:
                     flash('Niekompletne dane do aktualizacji statusu.', 'warning')

            # Po akcji POST zawsze przekieruj
            if form_submitted:
                 conn.close()
                 return redirect(url_for('view_orders'))

        # Metoda GET - wyświetlanie listy zamówień
        sql = """
            SELECT o.OrderID, u.Username, o.OrderDate, o.TotalPrice, o.Status
            FROM Orders o
            LEFT JOIN Users u ON o.UserID = u.UserID
            ORDER BY o.OrderDate DESC
        """
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        orders_display = []
        for row in cursor.fetchall():
            order_dict = dict(zip(columns, row))
            order_dict['Username'] = order_dict['Username'] or "[Użytkownik usunięty]"
            orders_display.append(order_dict)

        return render_template('admin/view_orders.html', orders=orders_display)

    except Exception as e:
         app.logger.error(f"Nieoczekiwany błąd w view_orders: {e}")
         flash("Wystąpił nieoczekiwany błąd.", "danger")
         return redirect(url_for('admin_dashboard'))
    finally:
         if conn and conn.connected == 1:
             conn.close()


# --- Trasa do Serwowania Wgranych Plików ---

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serwuje pliki z folderu UPLOAD_FOLDER."""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    except FileNotFoundError:
        app.logger.warning(f"Nie znaleziono pliku w uploads: {filename}")
        # Spróbuj zwrócić placeholder dla dania lub restauracji
        if filename.startswith('dish_'):
            placeholder = 'placeholder.png'
        elif filename.startswith('restaurant_'):
            placeholder = 'placeholder_restaurant.png'
        else: # Domyślny placeholder
            placeholder = 'placeholder.png'

        placeholder_path = os.path.join('static', placeholder)
        if os.path.exists(placeholder_path):
             return send_from_directory('static', placeholder)
        else:
             app.logger.error(f"Nie znaleziono ani pliku {filename}, ani placeholdera {placeholder}")
             abort(404) # Zwróć błąd 404 Not Found


# --- Uruchomienie Aplikacji ---

if __name__ == '__main__':
    # Uruchom serwer deweloperski Flaska
    # debug=False jest bezpieczniejsze niż True
    # host='0.0.0.0' pozwala na dostęp z innych urządzeń w sieci lokalnej
    # W produkcji użyj serwera WSGI np. gunicorn lub waitress!
    app.logger.info("Uruchamianie serwera deweloperskiego Flask...")
    app.run(debug=False, host='0.0.0.0', port=5000)