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

# --- Funkcja usuwania pliku ---
def delete_image_file(filename):
    """Bezpiecznie usuwa plik obrazka z folderu uploads."""
    if not filename:
        return False
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            app.logger.info(f"Usunięto plik: {file_path}")
            return True
        except Exception as e:
            app.logger.error(f"Nie udało się usunąć pliku {file_path}: {e}")
            return False
    else:
        app.logger.warning(f"Plik do usunięcia nie istnieje: {file_path}")
        return False

# --- Trasy Frontend --- (bez zmian)

@app.route('/')
def index():
    conn = get_db_connection()
    restaurants_display = []
    if not conn:
        return render_template('index.html', restaurants=restaurants_display)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" ORDER BY "Name"')
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        for r in restaurants:
            r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
            restaurants_display.append(r)
    except Exception as e:
        app.logger.error(f"Błąd pobierania restauracji: {e}")
        flash("Wystąpił błąd podczas pobierania danych restauracji.", "danger")
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed:
             try: conn.close()
             except Exception as close_err: app.logger.error(f"Błąd zamykania połączenia w index: {close_err}")
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
            if cursor and not cursor.closed: cursor.close()
            if conn and not conn.closed and not user_logged_in:
                 try: conn.close()
                 except Exception as close_err: app.logger.error(f"Błąd zamykania połączenia w finally login: {close_err}")

        return redirect(url_for('login'))

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
        if conn and not conn.closed:
             try: conn.close()
             except Exception as close_err: app.logger.error(f"Błąd zamykania połączenia w restaurant_detail: {close_err}")

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    restaurants_display = []
    if not query:
        return render_template('index.html', restaurants=restaurants_display, search_query=query)

    conn = get_db_connection()
    if not conn:
        return render_template('index.html', restaurants=restaurants_display, search_query=query)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        search_term = f"%{query}%"
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
        if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania połączenia w search: {close_err}")

    return render_template('index.html', restaurants=restaurants_display, search_query=query)


# --- Trasy Koszyka i Zamówień --- (bez zmian)

@app.route('/cart/add/<int:dish_id>', methods=['POST'])
def add_to_cart(dish_id):
    if 'user_id' not in session:
        flash('Musisz być zalogowany, aby dodać produkty do koszyka.', 'warning')
        return redirect(url_for('login'))

    try: quantity = int(request.form.get('quantity', 1)); assert quantity > 0
    except: flash('Nieprawidłowa ilość produktu.', 'warning'); return redirect(request.referrer or url_for('index'))

    conn = get_db_connection(); redirect_url = request.referrer or url_for('index'); dish_data_dict = None
    if not conn: return redirect(redirect_url)
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "DishID", "Name", "Price" FROM "Dishes" WHERE "DishID" = %s', (dish_id,))
        dish_data_dict = row_to_dict(cursor, cursor.fetchone())
        if not dish_data_dict: flash('Nie znaleziono wybranego dania.', 'danger')
    except Exception as e: app.logger.error(f"Błąd pobierania dania {dish_id} do koszyka: {e}"); flash("Błąd pobierania dania.", "danger")
    finally:
        if cursor: cursor.close()
        # Zamknij połączenie tylko jeśli danie nie zostało znalezione lub wystąpił błąd
        if conn and not conn.closed and not dish_data_dict:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w add_to_cart (finally): {close_err}")

    if dish_data_dict:
        if 'cart' not in session: session['cart'] = {}
        cart = session.get('cart', {}); dish_id_str = str(dish_id)
        try:
            price = float(dish_data_dict['Price']); current_quantity = cart.get(dish_id_str, {}).get('quantity', 0)
            cart[dish_id_str] = {'name': dish_data_dict['Name'], 'price': price, 'quantity': current_quantity + quantity}
            session['cart'] = cart; session.modified = True
            flash(f"Dodano '{dish_data_dict['Name']}' (x{quantity}) do koszyka.", 'success')
        except (KeyError, ValueError) as e: app.logger.error(f"Błąd koszyka dla dania {dish_id}: {e}"); flash("Błąd dodawania do koszyka.", "danger")
        finally:
            # Zamknij połączenie po operacji na koszyku
            if conn and not conn.closed:
                try: conn.close()
                except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w add_to_cart po operacji: {close_err}")

    return redirect(redirect_url)


@app.route('/cart')
def view_cart():
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {}); items_display = []; total_price = 0.0; cart_changed = False
    if cart:
        for item_id_str in list(cart.keys()):
            item_data = cart[item_id_str]
            try:
                item_id = int(item_id_str); price = float(item_data['price']); quantity = int(item_data['quantity'])
                if quantity <= 0: raise ValueError("Ilość <= 0")
                item_total = price * quantity
                items_display.append({'id': item_id, 'name': item_data.get('name', f'ID {item_id}'), 'price': price, 'quantity': quantity, 'total': item_total})
                total_price += item_total
            except (KeyError, ValueError, TypeError) as e:
                app.logger.warning(f"Usuwanie błędnego elementu z koszyka ID {item_id_str}: {e}"); flash(f"Produkt ID {item_id_str} usunięty (nieprawidłowe dane).", "warning"); del cart[item_id_str]; cart_changed = True
    if cart_changed: session['cart'] = cart; session.modified = True
    return render_template('cart.html', cart_items=items_display, total_price=total_price)

@app.route('/cart/remove/<dish_id>', methods=['POST'])
def remove_from_cart(dish_id):
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {}); dish_id_str = str(dish_id)
    if dish_id_str in cart: item_name = cart[dish_id_str].get('name', f'Produkt ID {dish_id_str}'); del cart[dish_id_str]; session['cart'] = cart; session.modified = True; flash(f"Usunięto '{item_name}' z koszyka.", 'info')
    else: flash('Tego produktu nie ma już w koszyku.', 'warning')
    return redirect(url_for('view_cart'))

@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {});
    if not cart: flash('Koszyk jest pusty.', 'warning'); return redirect(url_for('view_cart'))

    total_price = 0.0; order_items_data = []; is_cart_valid = True
    for item_id_str, item_data in cart.items():
        try:
            dish_id = int(item_id_str); price = float(item_data['price']); quantity = int(item_data['quantity'])
            if quantity <= 0 or price < 0: raise ValueError("Nieprawidłowe dane")
            total_price += price * quantity
            order_items_data.append({'dish_id': dish_id, 'quantity': quantity, 'price_per_item': price})
        except (KeyError, ValueError, TypeError) as e: app.logger.error(f"Błąd walidacji koszyka w checkout dla ID {item_id_str}: {e}"); flash(f"Problem z produktem ID {item_id_str}. Popraw koszyk.", "danger"); is_cart_valid = False; break
    if not is_cart_valid: return redirect(url_for('view_cart'))

    conn = get_db_connection();
    if not conn: flash('Błąd bazy danych przy zamówieniu.', 'danger'); return redirect(url_for('view_cart'))
    cursor = None; new_order_id = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('INSERT INTO "Orders" ("UserID", "TotalPrice", "Status") VALUES (%s, %s, %s) RETURNING "OrderID"', (session['user_id'], total_price, 'Złożone'))
        result = cursor.fetchone();
        if result: new_order_id = result['OrderID']; app.logger.info(f"Zamówienie #{new_order_id} dla UserID: {session['user_id']}")
        else: raise Exception("Nie pobrano ID nowego zamówienia.")

        insert_item_sql = 'INSERT INTO "OrderItems" ("OrderID", "DishID", "Quantity", "PricePerItem") VALUES (%s, %s, %s, %s)'
        items_to_insert = [(new_order_id, item['dish_id'], item['quantity'], item['price_per_item']) for item in order_items_data]
        cursor.executemany(insert_item_sql, items_to_insert)
        app.logger.info(f"Dodano {len(items_to_insert)} pozycji do zamówienia #{new_order_id}")

        conn.commit(); session.pop('cart', None); session.modified = True
        flash('Zamówienie złożone pomyślnie!', 'success')
        return redirect(url_for('order_confirmation', order_id=new_order_id))
    except Exception as e:
        conn.rollback(); app.logger.error(f"BŁĄD checkout dla UserID {session.get('user_id')}: {e}"); flash('Błąd podczas składania zamówienia.', 'danger')
        return redirect(url_for('view_cart'))
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania połączenia w checkout: {close_err}")

@app.route('/order_confirmation/<int:order_id>')
def order_confirmation(order_id):
     if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
     return render_template('order_confirmation.html', order_id=order_id)

# --- Trasy Panelu Administratora ---

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin/admin_dashboard.html')

# --- Zarządzanie Restauracjami (Dodawanie, Lista, Usuwanie) ---
@app.route('/admin/restaurants', methods=['GET', 'POST'])
@admin_required
def manage_restaurants():
    conn = get_db_connection()
    if not conn: flash('Nie można połączyć z bazą danych.', 'danger'); return redirect(url_for('admin_dashboard'))

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'POST':
            action = request.form.get('action')
            form_submitted = False # Flaga do kontroli przekierowania

            # --- DODAWANIE RESTAURACJI ---
            if action == 'add':
                form_submitted = True
                name = request.form.get('name', '').strip()
                if not name:
                    flash('Nazwa restauracji jest wymagana.', 'warning')
                else:
                    cuisine = request.form.get('cuisine', '').strip() or None
                    street = request.form.get('street', '').strip() or None
                    street_number = request.form.get('street_number', '').strip() or None
                    postal_code = request.form.get('postal_code', '').strip() or None
                    city = request.form.get('city', '').strip() or None
                    image_file = request.files.get('image')
                    image_filename = None; save_path = None

                    if image_file and image_file.filename != '':
                        if allowed_file(image_file.filename):
                            original_filename = secure_filename(image_file.filename)
                            extension = original_filename.rsplit('.', 1)[1].lower()
                            unique_filename = f"restaurant_{uuid.uuid4()}.{extension}"
                            save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                            try:
                                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                                image_file.save(save_path)
                                image_filename = unique_filename; app.logger.info(f"Zapisano obrazek restauracji: {save_path}")
                            except Exception as e: app.logger.error(f"Błąd zapisu pliku: {e}"); flash('Błąd zapisu pliku obrazka.', 'danger')
                        else: flash('Niedozwolony typ pliku obrazka.', 'warning')

                    try:
                        sql = 'INSERT INTO "Restaurants" ("Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL") VALUES (%s, %s, %s, %s, %s, %s, %s)'
                        cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_filename))
                        conn.commit()
                        flash(f'Restauracja "{name}" dodana pomyślnie.', 'success')
                    except Exception as e:
                        conn.rollback(); app.logger.error(f"Błąd dodawania restauracji '{name}': {e}"); flash('Błąd zapisu do bazy danych.', 'danger')
                        if save_path and os.path.exists(save_path): delete_image_file(unique_filename) # Usuń plik przy błędzie DB

            # --- USUWANIE RESTAURACJI ---
            elif action == 'delete':
                 form_submitted = True
                 restaurant_id_str = request.form.get('restaurant_id')
                 if not restaurant_id_str: flash('Nie podano ID restauracji.', 'warning')
                 else:
                     try:
                         restaurant_id = int(restaurant_id_str)
                         # Pobierz nazwę pliku obrazka przed usunięciem rekordu
                         cursor.execute('SELECT "ImageURL" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
                         result_row = cursor.fetchone()
                         image_to_delete = result_row['ImageURL'] if result_row and result_row['ImageURL'] else None

                         # Usuń rekord (CASCADE powinien usunąć powiązane dania i ich obrazki - ZALEŻY OD SCHEMATU DB)
                         # Jeśli nie ma CASCADE, trzeba najpierw usunąć dania i ich obrazki
                         # Zakładając, że CASCADE jest ustawiony:
                         cursor.execute('DELETE FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
                         deleted_count = cursor.rowcount
                         conn.commit()

                         if deleted_count > 0:
                             app.logger.info(f"Usunięto restaurację ID: {restaurant_id}")
                             flash(f'Restauracja ID: {restaurant_id} została usunięta.', 'success')
                             # Usuń główny obrazek restauracji
                             if image_to_delete: delete_image_file(image_to_delete)
                         else:
                             flash(f'Nie znaleziono restauracji o ID {restaurant_id} do usunięcia.', 'warning')

                     except ValueError: flash('Nieprawidłowe ID restauracji.', 'warning')
                     except Exception as e: conn.rollback(); app.logger.error(f"Błąd usuwania restauracji ID {restaurant_id_str}: {e}"); flash('Błąd podczas usuwania restauracji.', 'danger')

            # Przekieruj po AKCJI POST, aby uniknąć F5
            if form_submitted:
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
                return redirect(url_for('manage_restaurants'))

        # --- Metoda GET: Wyświetlanie listy ---
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
         if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w manage_restaurants: {close_err}")

# --- EDYCJA RESTAURACJI ---
@app.route('/admin/restaurants/<int:restaurant_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_restaurant(restaurant_id):
    conn = get_db_connection()
    if not conn: flash('Nie można połączyć z bazą danych.', 'danger'); return redirect(url_for('manage_restaurants'))
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Pobierz dane restauracji do edycji
        cursor.execute('SELECT * FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
        restaurant = row_to_dict(cursor, cursor.fetchone())
        if not restaurant:
            flash('Nie znaleziono restauracji o podanym ID.', 'warning')
            return redirect(url_for('manage_restaurants'))

        original_image_url = restaurant.get('ImageURL') # Zapamiętaj stary obrazek

        # --- Obsługa POST (zapis zmian) ---
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            if not name:
                flash('Nazwa restauracji jest wymagana.', 'warning')
                # Renderuj ponownie formularz z błędami, przekazując istniejące dane
                return render_template('admin/editRestaurant.html', restaurant=restaurant)

            cuisine = request.form.get('cuisine', '').strip() or None
            street = request.form.get('street', '').strip() or None
            street_number = request.form.get('street_number', '').strip() or None
            postal_code = request.form.get('postal_code', '').strip() or None
            city = request.form.get('city', '').strip() or None
            image_file = request.files.get('image')
            image_filename_to_save = original_image_url # Domyślnie zostaw stary
            new_image_saved_path = None
            delete_old_image = False

            # Obsługa nowego pliku obrazka
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    original_filename = secure_filename(image_file.filename)
                    extension = original_filename.rsplit('.', 1)[1].lower()
                    unique_filename = f"restaurant_{uuid.uuid4()}.{extension}"
                    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    try:
                        image_file.save(save_path)
                        image_filename_to_save = unique_filename # Użyj nowego pliku
                        new_image_saved_path = save_path
                        delete_old_image = True # Oznacz stary do usunięcia PO udanym zapisie DB
                        app.logger.info(f"Zapisano nowy obrazek restauracji: {save_path}")
                    except Exception as e:
                        app.logger.error(f"Błąd zapisu nowego pliku obrazka: {e}")
                        flash('Wystąpił błąd podczas zapisywania nowego pliku obrazka.', 'danger')
                        # Nie przerywaj, ale nie zmieniaj obrazka w DB
                        image_filename_to_save = original_image_url
                else:
                    flash('Niedozwolony typ pliku obrazka. Zmiany obrazka nie zostały zapisane.', 'warning')
                    image_filename_to_save = original_image_url

            # Aktualizacja w bazie danych
            try:
                sql = """
                    UPDATE "Restaurants" SET
                    "Name" = %s, "CuisineType" = %s, "Street" = %s, "StreetNumber" = %s,
                    "PostalCode" = %s, "City" = %s, "ImageURL" = %s
                    WHERE "RestaurantID" = %s
                """
                cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_filename_to_save, restaurant_id))
                conn.commit()
                flash(f'Restauracja "{name}" została zaktualizowana.', 'success')

                # Usuń stary obrazek TYLKO jeśli nowy został zapisany i DB update się powiódł
                if delete_old_image and original_image_url:
                    delete_image_file(original_image_url)

                # Zamknij zasoby PRZED przekierowaniem
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
                return redirect(url_for('manage_restaurants'))

            except Exception as e:
                conn.rollback()
                app.logger.error(f"Błąd aktualizacji restauracji ID {restaurant_id}: {e}")
                flash('Wystąpił błąd podczas zapisu zmian do bazy danych.', 'danger')
                # Jeśli zapis do DB się nie powiódł, a nowy obrazek został zapisany na dysku, usuń go
                if new_image_saved_path and os.path.exists(new_image_saved_path):
                    delete_image_file(unique_filename)
                # Renderuj ponownie formularz z danymi, które użytkownik próbował zapisać
                # (Można zaktualizować słownik 'restaurant' danymi z formularza dla lepszego UX)
                failed_data = request.form.to_dict()
                failed_data['RestaurantID'] = restaurant_id # Dodaj ID z powrotem
                failed_data['ImageURL'] = original_image_url # Użyj starego obrazka w widoku
                return render_template('admin/editRestaurant.html', restaurant=failed_data)

        # --- Metoda GET: Wyświetlanie formularza ---
        return render_template('admin/editRestaurant.html', restaurant=restaurant)

    except Exception as e:
        app.logger.error(f"Błąd w edycji restauracji ID {restaurant_id}: {e}")
        flash("Wystąpił nieoczekiwany błąd.", "danger")
        return redirect(url_for('manage_restaurants'))
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w edit_restaurant: {close_err}")

# --- Zarządzanie Daniami (Lista, Dodawanie, Usuwanie) ---
@app.route('/admin/dishes', methods=['GET', 'POST'])
@app.route('/admin/dishes/<int:restaurant_id>', methods=['GET', 'POST'])
@admin_required
def manage_dishes(restaurant_id=None):
    conn = get_db_connection()
    if not conn: flash('Nie można połączyć z bazą danych.', 'danger'); return redirect(url_for('admin_dashboard'))

    restaurants_list = []; dishes_display = []; selected_restaurant_name = None; cursor = None
    redirect_to_restaurant_id = restaurant_id # Do przekierowania po POST

    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Pobierz listę restauracji dla selecta
        cursor.execute('SELECT "RestaurantID", "Name" FROM "Restaurants" ORDER BY "Name"')
        restaurants_rows = cursor.fetchall()
        restaurants_list = [(row['RestaurantID'], row['Name']) for row in restaurants_rows]

        if request.method == 'POST':
            action = request.form.get('action'); form_submitted = False
            rest_id_form_str = request.form.get('restaurant_id')
            try: current_restaurant_id = int(rest_id_form_str) if rest_id_form_str else restaurant_id
            except (ValueError, TypeError):
                 flash('Nieprawidłowe ID restauracji.', 'danger')
                 if cursor: cursor.close();
                 if conn and not conn.closed: conn.close()
                 return redirect(url_for('manage_dishes'))

            # --- DODAWANIE DANIA ---
            if action == 'add':
                form_submitted = True
                if not current_restaurant_id: flash('Wybierz restaurację.', 'warning')
                else:
                    name = request.form.get('name', '').strip()
                    description = request.form.get('description', '').strip() or None
                    price_str = request.form.get('price')
                    image_file = request.files.get('image')
                    image_filename = None; price_decimal = None; save_path = None

                    if not name or not price_str: flash('Nazwa i cena są wymagane.', 'warning')
                    else:
                        try: price_decimal = float(price_str); assert price_decimal >= 0
                        except: flash('Nieprawidłowa cena.', 'warning'); price_decimal = None

                    if name and price_decimal is not None:
                        if image_file and image_file.filename != '':
                             if allowed_file(image_file.filename):
                                 original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower()
                                 unique_filename = f"dish_{uuid.uuid4()}.{extension}"; save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                                 try: os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True); image_file.save(save_path); image_filename = unique_filename
                                 except Exception as e: flash('Błąd zapisu obrazka.', 'danger'); app.logger.error(f"Błąd zapisu obrazka dania: {e}")
                             else: flash('Niedozwolony typ pliku.', 'warning')

                        try:
                            sql = 'INSERT INTO "Dishes" ("RestaurantID", "Name", "Description", "Price", "ImageURL") VALUES (%s, %s, %s, %s, %s)'
                            cursor.execute(sql, (current_restaurant_id, name, description, price_decimal, image_filename))
                            conn.commit()
                            flash(f'Danie "{name}" dodane.', 'success')
                        except Exception as e:
                            conn.rollback(); app.logger.error(f"Błąd dodawania dania '{name}': {e}"); flash('Błąd zapisu dania.', 'danger')
                            if save_path and os.path.exists(save_path): delete_image_file(unique_filename)

            # --- USUWANIE DANIA ---
            elif action == 'delete':
                 form_submitted = True
                 dish_id_str = request.form.get('dish_id')
                 if not dish_id_str or not current_restaurant_id: flash('Brak ID dania lub restauracji.', 'warning')
                 else:
                      try:
                          dish_id = int(dish_id_str)
                          # Pobierz nazwę pliku obrazka
                          cursor.execute('SELECT "ImageURL" FROM "Dishes" WHERE "DishID" = %s', (dish_id,))
                          result_row = cursor.fetchone()
                          image_to_delete = result_row['ImageURL'] if result_row and result_row['ImageURL'] else None

                          # Usuń danie
                          cursor.execute('DELETE FROM "Dishes" WHERE "DishID" = %s AND "RestaurantID" = %s', (dish_id, current_restaurant_id))
                          deleted_count = cursor.rowcount; conn.commit()

                          if deleted_count > 0:
                              app.logger.info(f"Usunięto danie ID: {dish_id} z restauracji ID: {current_restaurant_id}"); flash(f'Danie ID: {dish_id} usunięte.', 'success')
                              if image_to_delete: delete_image_file(image_to_delete)
                          else: flash(f'Nie znaleziono dania ID {dish_id} w tej restauracji.', 'warning')
                      except ValueError: flash('Nieprawidłowe ID dania.', 'warning')
                      except Exception as e: conn.rollback(); app.logger.error(f"Błąd usuwania dania ID {dish_id_str}: {e}"); flash('Błąd usuwania dania.', 'danger')

            # --- PRZEKIEROWANIE PO POST ---
            if form_submitted:
                 redirect_to_restaurant_id = current_restaurant_id or restaurant_id # Użyj ID z form lub URL
                 if cursor: cursor.close()
                 if conn and not conn.closed: conn.close()
                 redirect_url = url_for('manage_dishes', restaurant_id=redirect_to_restaurant_id) if redirect_to_restaurant_id else url_for('manage_dishes')
                 return redirect(redirect_url)

        # --- Metoda GET: Wyświetlanie dań ---
        if restaurant_id:
            cursor.execute('SELECT "Name" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
            rest_name_row = cursor.fetchone()
            if rest_name_row:
                selected_restaurant_name = rest_name_row['Name']
                cursor.execute('SELECT "DishID", "Name", "Description", "Price", "ImageURL" FROM "Dishes" WHERE "RestaurantID" = %s ORDER BY "Name"', (restaurant_id,))
                dishes_display = rows_to_dicts(cursor, cursor.fetchall())
            else:
                flash(f"Restauracja ID {restaurant_id} nie znaleziona.", "warning")
                return redirect(url_for('manage_dishes'))

        return render_template('admin/manage_dishes.html',
                               dishes=dishes_display,
                               restaurants=restaurants_list,
                               selected_restaurant_id=restaurant_id,
                               selected_restaurant_name=selected_restaurant_name)

    except Exception as e:
        app.logger.error(f"Błąd w manage_dishes: {e}"); flash("Nieoczekiwany błąd.", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w manage_dishes: {close_err}")


# --- EDYCJA DANIA ---
@app.route('/admin/dishes/<int:dish_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_dish(dish_id):
    conn = get_db_connection()
    if not conn: flash('Nie można połączyć z bazą danych.', 'danger'); return redirect(url_for('manage_dishes')) # lub admin_dashboard

    cursor = None
    restaurants_list = []

    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Pobierz listę wszystkich restauracji dla dropdowna
        cursor.execute('SELECT "RestaurantID", "Name" FROM "Restaurants" ORDER BY "Name"')
        restaurants_rows = cursor.fetchall()
        restaurants_list = [(row['RestaurantID'], row['Name']) for row in restaurants_rows]

        # Pobierz dane dania do edycji
        cursor.execute('SELECT * FROM "Dishes" WHERE "DishID" = %s', (dish_id,))
        dish = row_to_dict(cursor, cursor.fetchone())
        if not dish:
            flash('Nie znaleziono dania o podanym ID.', 'warning')
            return redirect(url_for('manage_dishes')) # Lub do dashboardu

        original_image_url = dish.get('ImageURL')
        original_restaurant_id = dish.get('RestaurantID') # Zapamiętaj ID restauracji

        # --- Obsługa POST (zapis zmian) ---
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            price_str = request.form.get('price')
            restaurant_id_str = request.form.get('restaurant_id') # Nowe ID restauracji
            price_decimal = None

            if not name or not price_str or not restaurant_id_str:
                 flash('Nazwa, cena i restauracja są wymagane.', 'warning')
                 return render_template('admin/editMenuItem.html', dish=dish, restaurants=restaurants_list)

            try: price_decimal = float(price_str); assert price_decimal >= 0
            except: flash('Nieprawidłowa wartość ceny.', 'warning'); price_decimal = None
            try: new_restaurant_id = int(restaurant_id_str)
            except: flash('Nieprawidłowe ID restauracji.', 'warning'); new_restaurant_id = None

            if name and price_decimal is not None and new_restaurant_id is not None:
                 description = request.form.get('description', '').strip() or None
                 image_file = request.files.get('image')
                 image_filename_to_save = original_image_url
                 new_image_saved_path = None
                 delete_old_image = False

                 # Obsługa nowego pliku obrazka
                 if image_file and image_file.filename != '':
                     if allowed_file(image_file.filename):
                         original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower()
                         unique_filename = f"dish_{uuid.uuid4()}.{extension}"; save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                         try:
                             image_file.save(save_path); image_filename_to_save = unique_filename
                             new_image_saved_path = save_path; delete_old_image = True
                             app.logger.info(f"Zapisano nowy obrazek dania: {save_path}")
                         except Exception as e: app.logger.error(f"Błąd zapisu pliku: {e}"); flash('Błąd zapisu pliku.', 'danger'); image_filename_to_save = original_image_url
                     else: flash('Niedozwolony typ pliku.', 'warning'); image_filename_to_save = original_image_url

                 # Aktualizacja w bazie danych
                 try:
                     sql = """
                         UPDATE "Dishes" SET
                         "Name" = %s, "Description" = %s, "Price" = %s,
                         "RestaurantID" = %s, "ImageURL" = %s
                         WHERE "DishID" = %s
                     """
                     cursor.execute(sql, (name, description, price_decimal, new_restaurant_id, image_filename_to_save, dish_id))
                     conn.commit()
                     flash(f'Danie "{name}" zostało zaktualizowane.', 'success')

                     if delete_old_image and original_image_url:
                         delete_image_file(original_image_url)

                     # Zamknij zasoby PRZED przekierowaniem
                     if cursor: cursor.close()
                     if conn and not conn.closed: conn.close()
                     # Przekieruj do widoku dań dla NOWEJ restauracji (lub starej, jeśli nie zmieniono)
                     return redirect(url_for('manage_dishes', restaurant_id=new_restaurant_id))

                 except Exception as e:
                     conn.rollback(); app.logger.error(f"Błąd aktualizacji dania ID {dish_id}: {e}"); flash('Błąd zapisu zmian.', 'danger')
                     if new_image_saved_path and os.path.exists(new_image_saved_path): delete_image_file(unique_filename)
                     # Renderuj ponownie formularz z błędami
                     failed_data = request.form.to_dict()
                     failed_data['DishID'] = dish_id
                     failed_data['ImageURL'] = original_image_url
                     return render_template('admin/editMenuItem.html', dish=failed_data, restaurants=restaurants_list)
            else:
                 # Błąd walidacji ceny lub ID restauracji, renderuj ponownie z błędami
                 return render_template('admin/editMenuItem.html', dish=dish, restaurants=restaurants_list)


        # --- Metoda GET: Wyświetlanie formularza ---
        return render_template('admin/editMenuItem.html', dish=dish, restaurants=restaurants_list)

    except Exception as e:
        app.logger.error(f"Błąd w edycji dania ID {dish_id}: {e}"); flash("Nieoczekiwany błąd.", "danger")
        # Przekieruj do listy dań dla oryginalnej restauracji lub dashboardu
        fallback_restaurant_id = dish.get('RestaurantID') if isinstance(dish, dict) else None
        redirect_url = url_for('manage_dishes', restaurant_id=fallback_restaurant_id) if fallback_restaurant_id else url_for('admin_dashboard')
        return redirect(redirect_url)
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w edit_dish: {close_err}")

# --- Zarządzanie Użytkownikami i Zamówieniami (bez zmian) ---

@app.route('/admin/users')
@admin_required
def manage_users():
    conn = get_db_connection()
    users_display = []
    if not conn: flash("Nie można połączyć z bazą danych.", "danger"); return render_template('admin/manage_users.html', users=users_display)
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "UserID", "Username", "IsAdmin" FROM "Users" ORDER BY "Username"')
        users_display = rows_to_dicts(cursor, cursor.fetchall())
    except Exception as e: app.logger.error(f"Błąd pobierania użytkowników: {e}"); flash("Błąd pobierania listy użytkowników.", "danger")
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w manage_users: {close_err}")
    return render_template('admin/manage_users.html', users=users_display)

@app.route('/admin/orders', methods=['GET', 'POST'])
@admin_required
def view_orders():
    conn = get_db_connection()
    if not conn: flash('Nie można połączyć z bazą danych.', 'danger'); return redirect(url_for('admin_dashboard'))
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'POST':
            action = request.form.get('action'); order_id_str = request.form.get('order_id'); new_status = request.form.get('status')
            allowed_statuses = ['Złożone', 'W realizacji', 'Dostarczone', 'Anulowane']; form_submitted = False
            if action == 'update_status':
                form_submitted = True
                if order_id_str and new_status and new_status in allowed_statuses:
                    try:
                        order_id = int(order_id_str)
                        cursor.execute('UPDATE "Orders" SET "Status" = %s WHERE "OrderID" = %s', (new_status, order_id))
                        conn.commit(); app.logger.info(f"Zmieniono status zamówienia #{order_id} na '{new_status}'.")
                        flash('Status zamówienia zaktualizowany.', 'success')
                    except ValueError: flash('Nieprawidłowe ID zamówienia.', 'warning')
                    except Exception as e: conn.rollback(); app.logger.error(f"Błąd aktualizacji statusu zam. #{order_id_str}: {e}"); flash('Błąd aktualizacji statusu.', 'danger')
                else: flash('Nieprawidłowe dane do aktualizacji statusu.', 'warning')

            if form_submitted:
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
                return redirect(url_for('view_orders'))

        # Metoda GET
        sql = """
            SELECT o."OrderID", u."Username", o."OrderDate", o."TotalPrice", o."Status"
            FROM "Orders" o LEFT JOIN "Users" u ON o."UserID" = u."UserID" ORDER BY o."OrderDate" DESC
        """
        cursor.execute(sql); orders = rows_to_dicts(cursor, cursor.fetchall()); orders_display = []
        for o in orders: o['Username'] = o['Username'] or "[Usunięty]"; orders_display.append(o)
        return render_template('admin/view_orders.html', orders=orders_display)
    except Exception as e: app.logger.error(f"Błąd w widoku zamówień admina: {e}"); flash("Błąd pobierania zamówień.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn and not conn.closed:
            try: conn.close()
            except Exception as close_err: app.logger.error(f"Błąd zamykania poł. w view_orders: {close_err}")

# --- Trasa do Serwowania Wgranych Plików --- (bez zmian)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serwuje pliki z folderu UPLOAD_FOLDER."""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    except FileNotFoundError:
        app.logger.warning(f"Nie znaleziono pliku w uploads: {filename}")
        placeholder = 'placeholder.png' # Domyślny
        if filename.startswith('restaurant_'): placeholder = 'placeholder_restaurant.png'
        placeholder_path = os.path.join('static', placeholder)
        if os.path.exists(placeholder_path): return send_from_directory('static', placeholder)
        else: app.logger.error(f"Nie znaleziono {filename} ani {placeholder}"); abort(404)

# --- Uruchomienie Aplikacji ---
if __name__ == '__main__':
    app.logger.info("Uruchamianie serwera deweloperskiego Flask...")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 't'], host='0.0.0.0', port=port)