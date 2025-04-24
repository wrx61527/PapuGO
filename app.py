# -*- coding: utf-8 -*-
import os
import psycopg2
import psycopg2.extras # Dla DictCursor
import uuid
import logging
from functools import wraps
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    send_from_directory, abort, current_app
)

# --- Konfiguracja Początkowa ---
load_dotenv()
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
# Ustaw silny klucz sekretny jako zmienną środowiskową!
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'y9KzjYV6efkUdLnb3V8k')

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
        app.logger.info(f"Utworzono folder {UPLOAD_FOLDER}")
    except OSError as e:
        app.logger.error(f"Nie można utworzyć folderu {UPLOAD_FOLDER}: {e}")

# --- Funkcje Pomocnicze ---

def get_db_connection():
    """Nawiązuje połączenie z bazą danych PostgreSQL używając psycopg2."""
    try:
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        db_port = os.environ.get('DB_PORT', '5432')

        if not all([db_host, db_name, db_user, db_password]):
             current_app.logger.error("Brak wszystkich zmiennych środowiskowych do połączenia z bazą: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD")
             flash("Błąd krytyczny: Brak konfiguracji bazy danych!", "danger")
             return None

        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password,
            port=db_port,
            sslmode="require"
        )
        current_app.logger.debug("Połączenie psycopg2 nawiązane.")
        return conn
    except psycopg2.OperationalError as e:
        current_app.logger.error(f"Błąd połączenia psycopg2 (OperationalError) do {db_host}:{db_port}/{db_name} jako {db_user}: {e}")
        flash(f"Błąd połączenia z bazą danych: Nie można połączyć.", "danger")
    except Exception as e:
        current_app.logger.error(f"Nieoczekiwany błąd połączenia psycopg2: {e}")
        flash(f"Nieoczekiwany błąd połączenia: {e}", "danger")
    return None

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def format_address(street, number, code, city):
    address_parts = [part for part in [street, number] if part]; city_parts = [part for part in [code, city] if part]
    full_address = " ".join(address_parts); city_str = " ".join(city_parts)
    if city_str: full_address += (", " if full_address else "") + city_str
    return full_address if full_address else None

# Uproszczone funkcje konwersji, DictCursor zwraca obiekty podobne do słowników
def rows_to_dicts(cursor, rows):
    return [dict(row) for row in rows]

def row_to_dict(cursor, row):
    return dict(row) if row else None

# --- Dekorator Admina ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Dostęp wymaga uprawnień administratora.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Trasy Frontend ---

@app.route('/')
def index():
    conn = get_db_connection()
    restaurants_display = []
    if not conn:
        # flash jest już w get_db_connection
        return render_template('index.html', restaurants=restaurants_display)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" ORDER BY "Name"')
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        for r in restaurants:
            r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
            # r jest już słownikiem
            restaurants_display.append(r)
    except Exception as e:
        app.logger.error(f"Błąd pobierania restauracji: {e}")
        flash("Wystąpił błąd podczas pobierania danych restauracji.", "danger")
    finally:
        if cursor: cursor.close()
        if conn:
             try:
                 if not conn.closed: conn.close()
             except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w index: {close_err}")
    return render_template('index.html', restaurants=restaurants_display)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Nazwa użytkownika i hasło są wymagane.', 'warning')
            return redirect(url_for('login'))

        conn = get_db_connection()
        if not conn:
            return redirect(url_for('login'))

        user_logged_in = False
        cursor = None
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if action == 'login':
                # PAMIĘTAJ O HASHOWANIU HASEŁ W PRAWDZIWEJ APLIKACJI!
                cursor.execute('SELECT "UserID", "Username", "IsAdmin", "Password" FROM "Users" WHERE "Username" = %s', (username,))
                user_row = row_to_dict(cursor, cursor.fetchone())

                # TO JEST NIEBEZPIECZNE PORÓWNANIE HASEŁ - TYLKO DLA PRZYKŁADU!
                if user_row and user_row['Password'] == password:
                    session['user_id'] = user_row['UserID']
                    session['username'] = user_row['Username']
                    session['is_admin'] = user_row['IsAdmin']
                    session.permanent = True
                    app.logger.info(f"User '{username}' logged in.")
                    flash('Zalogowano pomyślnie!', 'success')
                    user_logged_in = True
                    redirect_url = url_for('admin_dashboard') if user_row['IsAdmin'] else url_for('index')
                    # Zamknij połączenie przed przekierowaniem
                    if cursor: cursor.close()
                    if conn and not conn.closed: conn.close()
                    return redirect(redirect_url)
                else:
                    flash('Nieprawidłowa nazwa użytkownika lub hasło.', 'danger')

            elif action == 'register':
                try:
                    # PAMIĘTAJ O HASHOWANIU HASEŁ W PRAWDZIWEJ APLIKACJI!
                    cursor.execute('INSERT INTO "Users" ("Username", "Password") VALUES (%s, %s)', (username, password))
                    conn.commit()
                    app.logger.info(f"Zarejestrowano nowego użytkownika: '{username}'.")
                    flash('Rejestracja zakończona pomyślnie. Możesz się teraz zalogować.', 'success')
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    flash('Nazwa użytkownika jest już zajęta.', 'warning')
                except Exception as e:
                    conn.rollback()
                    app.logger.error(f"Błąd podczas rejestracji użytkownika {username}: {e}")
                    flash('Wystąpił błąd podczas rejestracji.', 'danger')

        except Exception as e:
            app.logger.error(f"Błąd podczas logowania/rejestracji dla użytkownika {username}: {e}")
            flash('Wystąpił błąd serwera.', 'danger')
        finally:
            # Zamknij zasoby tylko jeśli nie zostały zamknięte wcześniej
            if cursor and not cursor.closed: cursor.close()
            if conn and not conn.closed and not user_logged_in:
                 try:
                     conn.close()
                 except Exception as close_err:
                     app.logger.error(f"Błąd zamykania połączenia w finally login: {close_err}")

        # Przekierowanie po nieudanej próbie logowania lub po (udanej/nieudanej) rejestracji
        return redirect(url_for('login'))

    # Metoda GET
    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username')
    session.clear()
    if username:
        app.logger.info(f"User '{username}' logged out.")
    flash('Wylogowano pomyślnie.', 'info')
    return redirect(url_for('index'))

@app.route('/restaurant/<int:restaurant_id>')
def restaurant_detail(restaurant_id):
    conn = get_db_connection()
    restaurant_display = None
    dishes_display = []
    if not conn:
        return redirect(url_for('index'))

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
        restaurant_display = row_to_dict(cursor, cursor.fetchone())

        if restaurant_display:
            restaurant_display['FullAddress'] = format_address(restaurant_display.get('Street'), restaurant_display.get('StreetNumber'), restaurant_display.get('PostalCode'), restaurant_display.get('City')) or "Brak adresu"
            cursor.execute('SELECT "DishID", "Name", "Description", "Price", "ImageURL" FROM "Dishes" WHERE "RestaurantID" = %s ORDER BY "Name"', (restaurant_id,))
            dishes_display = rows_to_dicts(cursor, cursor.fetchall())
            # Przekazujemy słowniki do szablonu
            return render_template('restaurant_detail.html', restaurant=restaurant_display, dishes=dishes_display)
        else:
            flash('Nie znaleziono restauracji o podanym ID.', 'warning')
            return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Błąd pobierania szczegółów restauracji {restaurant_id}: {e}")
        flash("Wystąpił błąd podczas pobierania danych restauracji.", "danger")
        return redirect(url_for('index'))
    finally:
        if cursor: cursor.close()
        if conn:
             try:
                 if not conn.closed: conn.close()
             except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w restaurant_detail: {close_err}")

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    restaurants_display = []
    if not query:
        # Można dodać flasha, że pole wyszukiwania jest puste
        return render_template('index.html', restaurants=restaurants_display, search_query=query)

    conn = get_db_connection()
    if not conn:
        return render_template('index.html', restaurants=restaurants_display, search_query=query)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        search_term = f"%{query}%"
        # Użycie ILIKE dla wyszukiwania bez względu na wielkość liter w PostgreSQL
        sql = 'SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" WHERE "Name" ILIKE %s OR "CuisineType" ILIKE %s OR "City" ILIKE %s ORDER BY "Name"'
        cursor.execute(sql, (search_term, search_term, search_term))
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        for r in restaurants:
            r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
            restaurants_display.append(r)
        if not restaurants_display:
            flash(f"Nie znaleziono restauracji pasujących do zapytania '{query}'.", "info")
    except Exception as e:
        app.logger.error(f"Błąd podczas wyszukiwania dla '{query}': {e}")
        flash("Wystąpił błąd podczas wyszukiwania.", "danger")
    finally:
        if cursor: cursor.close()
        if conn:
            try:
                 if not conn.closed: conn.close()
            except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w search: {close_err}")

    return render_template('index.html', restaurants=restaurants_display, search_query=query)

# --- Trasy Koszyka i Zamówień --- (Logika sesji bez zmian, zmiany w dostępie do DB w checkout)

@app.route('/cart/add/<int:dish_id>', methods=['POST'])
def add_to_cart(dish_id):
    if 'user_id' not in session:
        flash('Musisz być zalogowany, aby dodać produkty do koszyka.', 'warning')
        return redirect(url_for('login'))

    try:
        quantity = int(request.form.get('quantity', 1))
        if quantity <= 0: raise ValueError("Ilość musi być dodatnia")
    except (ValueError, TypeError):
        flash('Nieprawidłowa ilość produktu.', 'warning')
        return redirect(request.referrer or url_for('index'))

    conn = get_db_connection()
    dish_data_dict = None
    redirect_url = request.referrer or url_for('index')
    if not conn:
        return redirect(redirect_url)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "DishID", "Name", "Price" FROM "Dishes" WHERE "DishID" = %s', (dish_id,))
        dish_data_dict = row_to_dict(cursor, cursor.fetchone())
        if not dish_data_dict:
            flash('Nie znaleziono wybranego dania.', 'danger')
            # Nie ma sensu kontynuować, zwracamy od razu
            if cursor: cursor.close()
            if conn and not conn.closed: conn.close()
            return redirect(redirect_url)
    except Exception as e:
        app.logger.error(f"Błąd pobierania dania {dish_id} do koszyka: {e}")
        flash("Wystąpił błąd podczas pobierania informacji o daniu.", "danger")
        # Nie ma sensu kontynuować, zwracamy od razu
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()
        return redirect(redirect_url)
    finally:
        # Zamknij tylko jeśli nie zamknięto wcześniej
        if cursor and not cursor.closed: cursor.close()
        if conn and not conn.closed and not dish_data_dict: # Zamknij tylko jeśli nie znaleziono dania
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w add_to_cart(finally): {close_err}")

    # Jeśli znaleziono danie, kontynuuj logikę koszyka
    if dish_data_dict:
        if 'cart' not in session:
            session['cart'] = {}
        cart = session.get('cart', {})
        dish_id_str = str(dish_id)
        try:
            # Używamy Decimal dla cen dla lepszej precyzji, ale float też zadziała dla prostoty
            price = float(dish_data_dict['Price'])
            current_quantity = cart.get(dish_id_str, {}).get('quantity', 0)
            cart[dish_id_str] = {'name': dish_data_dict['Name'], 'price': price, 'quantity': current_quantity + quantity}
            session['cart'] = cart
            session.modified = True
            flash(f"Dodano '{dish_data_dict['Name']}' (x{quantity}) do koszyka.", 'success')
        except (KeyError, ValueError) as e:
            app.logger.error(f"Błąd przetwarzania koszyka dla dania {dish_id}: {e}")
            flash("Wystąpił błąd podczas dodawania produktu do koszyka.", "danger")
        finally:
            # Zamknij połączenie po operacji na koszyku
            if conn and not conn.closed:
                try: conn.close()
                except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w add_to_cart po operacji: {close_err}")


    return redirect(redirect_url)


@app.route('/cart')
def view_cart():
    if 'user_id' not in session:
        flash('Musisz być zalogowany, aby zobaczyć koszyk.', 'warning')
        return redirect(url_for('login'))

    cart = session.get('cart', {})
    items_display = []
    total_price = 0.0
    cart_changed = False

    # Walidacja zawartości koszyka przy wyświetlaniu
    if cart:
        for item_id_str in list(cart.keys()): # Iterujemy po kopii kluczy
            item_data = cart[item_id_str]
            try:
                item_id = int(item_id_str) # Konwersja ID na int dla spójności
                price = float(item_data['price'])
                quantity = int(item_data['quantity'])
                if quantity <= 0: raise ValueError("Ilość musi być dodatnia")
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
                app.logger.warning(f"Usuwanie nieprawidłowego elementu z koszyka ID {item_id_str}: {e}")
                flash(f"Produkt ID {item_id_str} został usunięty z koszyka (nieprawidłowe dane).", "warning")
                del cart[item_id_str]
                cart_changed = True

    if cart_changed:
        session['cart'] = cart
        session.modified = True

    return render_template('cart.html', cart_items=items_display, total_price=total_price)

@app.route('/cart/remove/<dish_id>', methods=['POST'])
def remove_from_cart(dish_id):
    if 'user_id' not in session:
        flash('Musisz być zalogowany.', 'warning')
        return redirect(url_for('login'))

    cart = session.get('cart', {})
    # Klucze w sesji są stringami
    dish_id_str = str(dish_id)

    if dish_id_str in cart:
        item_name = cart[dish_id_str].get('name', f'Produkt ID {dish_id_str}')
        del cart[dish_id_str]
        session['cart'] = cart
        session.modified = True
        flash(f"Usunięto '{item_name}' z koszyka.", 'info')
    else:
        flash('Tego produktu nie ma już w Twoim koszyku.', 'warning')

    return redirect(url_for('view_cart'))

@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session:
        flash('Musisz być zalogowany, aby złożyć zamówienie.', 'warning')
        return redirect(url_for('login'))

    cart = session.get('cart', {})
    if not cart:
        flash('Twój koszyk jest pusty. Dodaj produkty, aby złożyć zamówienie.', 'warning')
        return redirect(url_for('view_cart'))

    total_price = 0.0
    order_items_data = []
    is_cart_valid = True

    # Walidacja koszyka przed złożeniem zamówienia
    for item_id_str, item_data in cart.items():
        try:
            dish_id = int(item_id_str)
            price = float(item_data['price'])
            quantity = int(item_data['quantity'])
            if quantity <= 0 or price < 0: raise ValueError("Nieprawidłowe dane produktu")
            total_price += price * quantity
            order_items_data.append({'dish_id': dish_id, 'quantity': quantity, 'price_per_item': price})
        except (KeyError, ValueError, TypeError) as e:
            app.logger.error(f"Błąd walidacji koszyka podczas checkout dla ID {item_id_str}: {e}")
            flash(f"Wystąpił problem z produktem ID {item_id_str} w koszyku. Popraw koszyk i spróbuj ponownie.", "danger")
            is_cart_valid = False
            break # Przerwij walidację przy pierwszym błędzie

    if not is_cart_valid:
        return redirect(url_for('view_cart'))

    conn = get_db_connection()
    if not conn:
        flash('Nie można połączyć się z bazą danych, aby złożyć zamówienie.', 'danger')
        return redirect(url_for('view_cart'))

    cursor = None
    new_order_id = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Wstawienie zamówienia i pobranie jego ID (użycie RETURNING)
        cursor.execute(
            'INSERT INTO "Orders" ("UserID", "TotalPrice", "Status") VALUES (%s, %s, %s) RETURNING "OrderID"',
            (session['user_id'], total_price, 'Złożone')
        )
        result = cursor.fetchone()
        if result:
            new_order_id = result['OrderID'] # DictCursor zwraca dostęp przez klucz
            app.logger.info(f"Utworzono zamówienie #{new_order_id} dla UserID: {session['user_id']}")
        else:
            raise Exception("Nie udało się pobrać ID nowego zamówienia.")

        # Wstawienie pozycji zamówienia
        insert_item_sql = 'INSERT INTO "OrderItems" ("OrderID", "DishID", "Quantity", "PricePerItem") VALUES (%s, %s, %s, %s)'
        items_to_insert = [(new_order_id, item['dish_id'], item['quantity'], item['price_per_item']) for item in order_items_data]

        # executemany jest wydajniejsze dla wielu wierszy
        cursor.executemany(insert_item_sql, items_to_insert)
        app.logger.info(f"Dodano {len(items_to_insert)} pozycji do zamówienia #{new_order_id}")

        conn.commit()
        session.pop('cart', None) # Wyczyść koszyk po udanym zamówieniu
        session.modified = True
        flash('Twoje zamówienie zostało złożone pomyślnie!', 'success')
        return redirect(url_for('order_confirmation', order_id=new_order_id))

    except Exception as e:
        conn.rollback() # Wycofaj zmiany w razie błędu
        app.logger.error(f"KRYTYCZNY BŁĄD podczas składania zamówienia dla UserID {session.get('user_id')}: {e}")
        flash('Wystąpił nieoczekiwany błąd podczas składania zamówienia. Spróbuj ponownie.', 'danger')
        return redirect(url_for('view_cart'))
    finally:
        if cursor: cursor.close()
        if conn:
            try:
                if not conn.closed: conn.close()
            except Exception as close_err:
                app.logger.error(f"Błąd zamykania połączenia w checkout: {close_err}")

@app.route('/order_confirmation/<int:order_id>')
def order_confirmation(order_id):
     if 'user_id' not in session:
         flash('Musisz być zalogowany, aby zobaczyć potwierdzenie zamówienia.', 'warning')
         return redirect(url_for('login'))
     # Można dodać logikę sprawdzającą, czy zamówienie należy do zalogowanego użytkownika
     return render_template('order_confirmation.html', order_id=order_id)

# --- Trasy Panelu Administratora --- (Wymagają analogicznych modyfikacji: DictCursor, %s, cudzysłowy, commit/rollback)

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    # Prosta strona powitalna dla admina
    return render_template('admin/admin_dashboard.html')

@app.route('/admin/restaurants', methods=['GET', 'POST'])
@admin_required
def manage_restaurants():
    conn = get_db_connection()
    if not conn:
        flash('Nie można połączyć się z bazą danych.', 'danger')
        return redirect(url_for('admin_dashboard'))

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'POST':
            action = request.form.get('action')
            form_submitted = False

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
                save_path = None

                if not name:
                    flash('Nazwa restauracji jest wymagana.', 'warning')
                else:
                    # Logika przetwarzania obrazka (bez zmian)
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
                                app.logger.error(f"Błąd zapisu pliku obrazka restauracji: {e}")
                                flash('Wystąpił błąd podczas zapisywania pliku obrazka.', 'danger')
                        else:
                            flash('Niedozwolony typ pliku obrazka.', 'warning')

                    # Zapis do bazy danych
                    try:
                        sql = 'INSERT INTO "Restaurants" ("Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL") VALUES (%s, %s, %s, %s, %s, %s, %s)'
                        cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_filename))
                        conn.commit()
                        flash(f'Restauracja "{name}" została dodana pomyślnie.', 'success')
                    except Exception as e:
                        conn.rollback()
                        app.logger.error(f"Błąd podczas dodawania restauracji '{name}' do bazy danych: {e}")
                        flash('Wystąpił błąd podczas zapisu do bazy danych.', 'danger')
                        # Usuń zapisany plik, jeśli zapis do DB się nie powiódł
                        if image_filename and save_path and os.path.exists(save_path):
                            try:
                                os.remove(save_path)
                                app.logger.info(f"Usunięto plik {save_path} po błędzie zapisu restauracji do DB.")
                            except Exception as rm_err:
                                app.logger.error(f"Nie udało się usunąć pliku {save_path} po błędzie DB: {rm_err}")

            elif action == 'delete':
                 form_submitted = True
                 restaurant_id_str = request.form.get('restaurant_id')
                 if not restaurant_id_str:
                     flash('Nie podano ID restauracji do usunięcia.', 'warning')
                 else:
                     try:
                         restaurant_id = int(restaurant_id_str)
                         # Najpierw pobierz nazwę pliku obrazka do usunięcia
                         cursor.execute('SELECT "ImageURL" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
                         result_row = cursor.fetchone()
                         image_to_delete_path = None
                         image_filename_to_flash = None
                         if result_row and result_row['ImageURL']: # Dostęp przez klucz dzięki DictCursor
                              image_filename_to_flash = result_row['ImageURL']
                              image_to_delete_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename_to_flash)

                         # Usuń rekord z bazy danych
                         cursor.execute('DELETE FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
                         conn.commit()
                         app.logger.info(f"Usunięto restaurację o ID: {restaurant_id}")
                         flash(f'Restauracja o ID: {restaurant_id} została usunięta.', 'success')

                         # Usuń plik obrazka, jeśli istniał
                         if image_to_delete_path and os.path.exists(image_to_delete_path):
                            try:
                                os.remove(image_to_delete_path)
                                app.logger.info(f"Usunięto plik obrazka restauracji: {image_to_delete_path}")
                            except Exception as e:
                                app.logger.warning(f"Nie udało się usunąć pliku obrazka restauracji {image_to_delete_path}: {e}")
                                flash(f'Nie udało się usunąć pliku obrazka: {image_filename_to_flash}', 'warning')

                     except ValueError:
                         flash('Nieprawidłowe ID restauracji.', 'warning')
                     except Exception as e:
                         conn.rollback()
                         app.logger.error(f"Błąd podczas usuwania restauracji o ID {restaurant_id_str}: {e}")
                         flash('Wystąpił błąd podczas usuwania restauracji.', 'danger')

            # Przekieruj po udanej akcji POST, aby uniknąć ponownego wysłania formularza
            if form_submitted:
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
                return redirect(url_for('manage_restaurants'))

        # Metoda GET - wyświetlanie listy restauracji
        cursor.execute('SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" ORDER BY "Name"')
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        restaurants_display = []
        for r in restaurants:
            r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "-"
            restaurants_display.append(r)
        return render_template('admin/manage_restaurants.html', restaurants=restaurants_display)

    except Exception as e:
        app.logger.error(f"Błąd w manage_restaurants: {e}")
        flash("Wystąpił nieoczekiwany błąd.", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn:
            try:
                 if not conn.closed: conn.close()
            except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w manage_restaurants: {close_err}")


@app.route('/admin/dishes', methods=['GET', 'POST'])
@app.route('/admin/dishes/<int:restaurant_id>', methods=['GET', 'POST'])
@admin_required
def manage_dishes(restaurant_id=None):
    conn = get_db_connection()
    if not conn:
        flash('Nie można połączyć się z bazą danych.', 'danger')
        return redirect(url_for('admin_dashboard'))

    restaurants_list = []
    dishes_display = []
    selected_restaurant_name = None
    cursor = None
    redirect_to_restaurant_id = restaurant_id

    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Pobierz listę restauracji dla selecta
        cursor.execute('SELECT "RestaurantID", "Name" FROM "Restaurants" ORDER BY "Name"')
        restaurants_rows = cursor.fetchall()
        # Konwersja na listę tupli (ID, Nazwa)
        restaurants_list = [(row['RestaurantID'], row['Name']) for row in restaurants_rows]

        if request.method == 'POST':
            action = request.form.get('action')
            rest_id_form_str = request.form.get('restaurant_id')
            form_submitted = False

            try:
                # Ustal ID restauracji, której dotyczy akcja
                current_restaurant_id = int(rest_id_form_str) if rest_id_form_str else restaurant_id
            except (ValueError, TypeError):
                flash('Nieprawidłowe ID restauracji.', 'danger')
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
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
                    save_path = None

                    if not name or not price_str:
                        flash('Nazwa dania i cena są wymagane.', 'warning')
                    else:
                        # Walidacja ceny
                        try:
                            # Używamy float dla uproszczenia, ale Decimal jest lepszy dla walut
                            price_decimal = float(price_str)
                            if price_decimal < 0: raise ValueError("Cena nie może być ujemna")
                        except (ValueError, TypeError):
                            flash('Nieprawidłowa wartość ceny.', 'warning')
                            price_decimal = None # Resetuj, aby zapobiec zapisowi

                    if name and price_decimal is not None: # Kontynuuj tylko jeśli nazwa i cena są OK
                        # Logika przetwarzania obrazka (bez zmian)
                        if image_file and image_file.filename != '':
                            if allowed_file(image_file.filename):
                                original_filename = secure_filename(image_file.filename)
                                extension = original_filename.rsplit('.', 1)[1].lower()
                                unique_filename = f"dish_{uuid.uuid4()}.{extension}"
                                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                                try:
                                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                                    image_file.save(save_path)
                                    image_filename = unique_filename
                                except Exception as e:
                                    flash('Błąd zapisu pliku obrazka dania.', 'danger')
                                    app.logger.error(f"Błąd zapisu pliku obrazka dania: {e}")
                            else:
                                flash('Niedozwolony typ pliku obrazka.', 'warning')

                        # Zapis dania do bazy danych
                        try:
                            sql = 'INSERT INTO "Dishes" ("RestaurantID", "Name", "Description", "Price", "ImageURL") VALUES (%s, %s, %s, %s, %s)'
                            cursor.execute(sql, (current_restaurant_id, name, description, price_decimal, image_filename))
                            conn.commit()
                            flash(f'Danie "{name}" zostało dodane pomyślnie.', 'success')
                        except Exception as e:
                            conn.rollback()
                            app.logger.error(f"Błąd podczas dodawania dania '{name}' do DB: {e}")
                            flash('Wystąpił błąd podczas zapisu dania do bazy danych.', 'danger')
                            # Usuń zapisany plik, jeśli zapis do DB się nie powiódł
                            if image_filename and save_path and os.path.exists(save_path):
                                try:
                                    os.remove(save_path)
                                except Exception as rm_err:
                                    app.logger.warning(f"Nie udało się usunąć pliku obrazka dania {save_path}: {rm_err}")

            elif action == 'delete':
                 form_submitted = True
                 dish_id_str = request.form.get('dish_id')
                 if not dish_id_str or not current_restaurant_id: # Potrzebujemy też ID restauracji kontekstowej
                     flash('Nie podano ID dania lub restauracji do usunięcia.', 'warning')
                 else:
                      try:
                          dish_id = int(dish_id_str)
                          # Pobierz nazwę pliku obrazka
                          cursor.execute('SELECT "ImageURL" FROM "Dishes" WHERE "DishID" = %s', (dish_id,))
                          result_row = cursor.fetchone()
                          image_to_delete_path = None
                          image_filename_to_flash = None
                          if result_row and result_row['ImageURL']:
                               image_filename_to_flash = result_row['ImageURL']
                               image_to_delete_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename_to_flash)

                          # Usuń danie z bazy
                          # Dodajemy warunek na RestaurantID dla bezpieczeństwa
                          cursor.execute('DELETE FROM "Dishes" WHERE "DishID" = %s AND "RestaurantID" = %s', (dish_id, current_restaurant_id))
                          deleted_count = cursor.rowcount # Sprawdź, czy cokolwiek zostało usunięte
                          conn.commit()

                          if deleted_count > 0:
                              app.logger.info(f"Usunięto danie o ID: {dish_id} z restauracji ID: {current_restaurant_id}")
                              flash(f'Danie o ID: {dish_id} zostało usunięte.', 'success')
                              # Usuń plik obrazka
                              if image_to_delete_path and os.path.exists(image_to_delete_path):
                                  try:
                                      os.remove(image_to_delete_path)
                                      app.logger.info(f"Usunięto plik obrazka dania: {image_to_delete_path}")
                                  except Exception as e:
                                      app.logger.warning(f"Błąd usuwania pliku obrazka dania {image_to_delete_path}: {e}")
                                      flash(f'Nie udało się usunąć pliku obrazka: {image_filename_to_flash}', 'warning')
                          else:
                              flash(f'Nie znaleziono dania o ID {dish_id} w tej restauracji.', 'warning')

                      except ValueError:
                          flash('Nieprawidłowe ID dania.', 'warning')
                      except Exception as e:
                          conn.rollback()
                          app.logger.error(f"Błąd podczas usuwania dania o ID {dish_id_str}: {e}")
                          flash('Wystąpił błąd podczas usuwania dania.', 'danger')

            # Przekierowanie po akcji POST
            if form_submitted:
                 redirect_to_restaurant_id = current_restaurant_id or restaurant_id # Użyj ID z formularza lub z URL
                 if cursor: cursor.close()
                 if conn and not conn.closed: conn.close()
                 # Przekieruj z powrotem do widoku dań dla tej samej restauracji
                 redirect_url = url_for('manage_dishes', restaurant_id=redirect_to_restaurant_id) if redirect_to_restaurant_id else url_for('manage_dishes')
                 return redirect(redirect_url)

        # Metoda GET - wyświetlanie dań dla wybranej restauracji
        if restaurant_id:
            cursor.execute('SELECT "Name" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
            rest_name_row = cursor.fetchone()
            if rest_name_row:
                selected_restaurant_name = rest_name_row['Name']
                cursor.execute('SELECT "DishID", "Name", "Description", "Price", "ImageURL" FROM "Dishes" WHERE "RestaurantID" = %s ORDER BY "Name"', (restaurant_id,))
                dishes_display = rows_to_dicts(cursor, cursor.fetchall())
            else:
                flash(f"Restauracja o ID {restaurant_id} nie została znaleziona.", "warning")
                # Nie zamykamy połączenia, bo finally to zrobi
                return redirect(url_for('manage_dishes'))

        return render_template('admin/manage_dishes.html',
                               dishes=dishes_display,
                               restaurants=restaurants_list, # Lista tupli dla selecta
                               selected_restaurant_id=restaurant_id,
                               selected_restaurant_name=selected_restaurant_name)

    except Exception as e:
        app.logger.error(f"Błąd w manage_dishes: {e}")
        flash("Wystąpił nieoczekiwany błąd.", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn:
            try:
                 if not conn.closed: conn.close()
            except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w manage_dishes: {close_err}")


@app.route('/admin/users')
@admin_required
def manage_users():
    conn = get_db_connection()
    users_display = []
    if not conn:
        flash("Nie można połączyć się z bazą danych.", "danger")
        # Możemy zwrócić pusty szablon lub przekierować
        return render_template('admin/manage_users.html', users=users_display)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "UserID", "Username", "IsAdmin" FROM "Users" ORDER BY "Username"')
        users_display = rows_to_dicts(cursor, cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Błąd podczas pobierania listy użytkowników: {e}")
        flash("Wystąpił błąd podczas pobierania listy użytkowników.", "danger")
    finally:
        if cursor: cursor.close()
        if conn:
            try:
                 if not conn.closed: conn.close()
            except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w manage_users: {close_err}")

    return render_template('admin/manage_users.html', users=users_display)

@app.route('/admin/orders', methods=['GET', 'POST'])
@admin_required
def view_orders():
    conn = get_db_connection()
    if not conn:
        flash('Nie można połączyć się z bazą danych.', 'danger')
        return redirect(url_for('admin_dashboard'))

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        if request.method == 'POST':
            action = request.form.get('action')
            order_id_str = request.form.get('order_id')
            new_status = request.form.get('status')
            allowed_statuses = ['Złożone', 'W realizacji', 'Dostarczone', 'Anulowane']
            form_submitted = False

            if action == 'update_status':
                form_submitted = True
                if order_id_str and new_status and new_status in allowed_statuses:
                    try:
                        order_id = int(order_id_str)
                        cursor.execute('UPDATE "Orders" SET "Status" = %s WHERE "OrderID" = %s', (new_status, order_id))
                        conn.commit()
                        app.logger.info(f"Zmieniono status zamówienia #{order_id} na '{new_status}'.")
                        flash('Status zamówienia został zaktualizowany.', 'success')
                    except ValueError:
                        flash('Nieprawidłowe ID zamówienia.', 'warning')
                    except Exception as e:
                        conn.rollback()
                        app.logger.error(f"Błąd podczas aktualizacji statusu zamówienia #{order_id_str}: {e}")
                        flash('Wystąpił błąd podczas aktualizacji statusu zamówienia.', 'danger')
                else:
                    flash('Nieprawidłowe dane do aktualizacji statusu zamówienia.', 'warning')

            # Przekieruj po akcji POST
            if form_submitted:
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
                return redirect(url_for('view_orders'))

        # Metoda GET - wyświetlanie zamówień
        sql = """
            SELECT o."OrderID", u."Username", o."OrderDate", o."TotalPrice", o."Status"
            FROM "Orders" o
            LEFT JOIN "Users" u ON o."UserID" = u."UserID"
            ORDER BY o."OrderDate" DESC
        """
        cursor.execute(sql)
        orders = rows_to_dicts(cursor, cursor.fetchall())
        orders_display = []
        for o in orders:
            # Zastąp None dla użytkownika, jeśli został usunięty
            o['Username'] = o['Username'] or "[Użytkownik usunięty]"
            orders_display.append(o)
        return render_template('admin/view_orders.html', orders=orders_display)

    except Exception as e:
        app.logger.error(f"Błąd w widoku zamówień administratora: {e}")
        flash("Wystąpił nieoczekiwany błąd podczas pobierania zamówień.", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn:
            try:
                 if not conn.closed: conn.close()
            except Exception as close_err:
                 app.logger.error(f"Błąd zamykania połączenia w view_orders: {close_err}")


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
# Ten blok nie jest zwykle wykonywany w produkcji, gdy używa się Gunicorn/WSGI
if __name__ == '__main__':
    app.logger.info("Uruchamianie serwera deweloperskiego Flask...")
    # W trybie deweloperskim można użyć zmiennych środowiskowych lub .env
    # Upewnij się, że zmienne DB_* są dostępne dla testów lokalnych
    port = int(os.environ.get("PORT", 5000))
    # debug=True jest przydatne lokalnie, ale NIGDY w produkcji
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 't'], host='0.0.0.0', port=port)