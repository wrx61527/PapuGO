import os
import psycopg2
import psycopg2.extras 
import uuid
import logging
from functools import wraps
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
# Dodajemy obsługę błędów HTTP (np. 403 Forbidden)
from werkzeug.exceptions import Forbidden, NotFound

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    abort, current_app, send_from_directory
)
import boto3
from botocore.exceptions import ClientError

# --- Konfiguracja Początkowa ---
load_dotenv()
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- Konfiguracja AWS S3 ---
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
AWS_REGION = os.getenv('AWS_REGION')

if not S3_BUCKET_NAME or not AWS_REGION:
    app.logger.error("Krytyczny błąd: Brak konfiguracji S3_BUCKET_NAME lub AWS_REGION!")
    S3_LOCATION = None
    s3_client = None
else:
    S3_LOCATION = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/"
    app.logger.info(f"Konfiguracja S3: Bucket={S3_BUCKET_NAME}, Region={AWS_REGION}, Location={S3_LOCATION}")
    try:
        s3_client = boto3.client('s3', region_name=AWS_REGION)
        app.logger.info(f"Klient Boto3 S3 utworzony dla regionu {AWS_REGION} (używa poświadczeń z roli)")
    except Exception as e:
        app.logger.error(f"Błąd inicjalizacji klienta Boto3 S3: {e}")
        s3_client = None
    if not s3_client:
        app.logger.error("Nie udało się zainicjalizować klienta S3.")

# --- Funkcje Pomocnicze Bazy Danych ---
def get_db_connection():
    """Nawiązuje połączenie z bazą danych PostgreSQL używając psycopg2."""
    try:
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD') 
        db_port = os.environ.get('DB_PORT', '5432')
        required_db_vars = {'DB_HOST': db_host, 'DB_NAME': db_name, 'DB_USER': db_user, 'DB_PASSWORD': db_password}
        missing_vars = [k for k, v in required_db_vars.items() if not v]
        if missing_vars:
             current_app.logger.error(f"Brak zmiennych środowiskowych bazy: {', '.join(missing_vars)}")
             flash("Błąd krytyczny: Brak konfiguracji bazy danych!", "danger")
             return None
        conn = psycopg2.connect(host=db_host, database=db_name, user=db_user, password=db_password, port=db_port, sslmode="require")
        current_app.logger.debug("Połączenie psycopg2 nawiązane.")
        return conn
    except Exception as e: # Łapiemy ogólny wyjątek, logujemy szczegóły
        current_app.logger.error(f"Błąd połączenia psycopg2: {type(e).__name__}: {e}")
        flash(f"Błąd połączenia z bazą danych.", "danger")
    return None

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def format_address(street, number, code, city):
    address_parts = [part for part in [street, number] if part]; city_parts = [part for part in [code, city] if part]
    full_address = " ".join(address_parts); city_str = " ".join(city_parts)
    if city_str: full_address += (", " if full_address else "") + city_str
    return full_address if full_address else None

def rows_to_dicts(cursor, rows): return [dict(row) for row in rows]
def row_to_dict(cursor, row): return dict(row) if row else None

# --- Funkcje Pomocnicze S3 ---
def upload_file_to_s3(file, bucket_name, object_name=None):
    if not s3_client: app.logger.error("S3 client error."); return None
    if not file or not file.filename: app.logger.warning("Empty file upload attempt."); return None
    if object_name is None:
        original_filename = secure_filename(file.filename)
        extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        if 'restaurants/' in file.filename or 'dishes/' in file.filename: # Proste sprawdzenie
             object_name = f"{'restaurants' if 'restaurants/' in file.filename else 'dishes'}/{uuid.uuid4()}.{extension}"
        else: object_name = f"uploads/{uuid.uuid4()}.{extension}"
    try:
        s3_client.upload_fileobj(file, bucket_name, object_name, ExtraArgs={"ContentType": file.content_type})
        file_url = f"{S3_LOCATION}{object_name}"
        app.logger.info(f"File uploaded to S3: {file_url}")
        return file_url
    except ClientError as e: app.logger.error(f"S3 upload error for '{object_name}': {e}"); return None
    except Exception as e: app.logger.error(f"Unexpected S3 upload error: {e}"); return None

def delete_file_from_s3(bucket_name, object_url_or_key):
    if not s3_client: app.logger.error("S3 client error."); return False
    if not object_url_or_key: app.logger.warning("S3 delete attempt with no key/URL."); return False
    object_key = object_url_or_key
    if object_url_or_key.startswith(S3_LOCATION): object_key = object_url_or_key[len(S3_LOCATION):]
    if not object_key: app.logger.warning(f"Could not extract S3 key from: {object_url_or_key}"); return False
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=object_key)
        app.logger.info(f"File {object_key} deleted from S3 bucket {bucket_name}")
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey': app.logger.warning(f"File {object_key} not found in S3 during delete."); return True
        app.logger.error(f"S3 delete error for {object_key}: {e}"); return False
    except Exception as e: app.logger.error(f"Unexpected S3 delete error for {object_key}: {e}"); return False

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
    if not conn: return render_template('index.html', restaurants=restaurants_display)
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" ORDER BY "Name"')
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        for r in restaurants: r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
        restaurants_display = restaurants
    except Exception as e: app.logger.error(f"Błąd pobierania restauracji: {e}"); flash("Wystąpił błąd.", "danger")
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()
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
        if not conn: return redirect(url_for('login'))
        cursor = None; user_logged_in = False
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if action == 'login':
                cursor.execute('SELECT "UserID", "Username", "IsAdmin", "Password" FROM "Users" WHERE "Username" = %s', (username,))
                user_row = row_to_dict(cursor, cursor.fetchone())

                if user_row and user_row['Password'] == password: 
                    session['user_id'] = user_row['UserID']
                    session['username'] = user_row['Username']
                    session['is_admin'] = user_row['IsAdmin']
                    session.permanent = True # Remember me
                    app.logger.info(f"User '{username}' logged in.")
                    flash('Zalogowano pomyślnie!', 'success')
                    user_logged_in = True
                    redirect_url = url_for('admin_dashboard') if user_row['IsAdmin'] else url_for('index')
                    # Zamknij zasoby przed przekierowaniem
                    if cursor: cursor.close()
                    if conn and not conn.closed: conn.close()
                    return redirect(redirect_url)
                else:
                    flash('Nieprawidłowa nazwa użytkownika lub hasło.', 'danger')

            elif action == 'register':
                try:
                    cursor.execute('INSERT INTO "Users" ("Username", "Password") VALUES (%s, %s)', (username, password))
                    conn.commit()
                    app.logger.info(f"Zarejestrowano: '{username}'.")
                    flash('Rejestracja pomyślna. Możesz się zalogować.', 'success')
                except psycopg2.errors.UniqueViolation:
                    conn.rollback(); flash('Nazwa użytkownika jest już zajęta.', 'warning')
                except Exception as e:
                    conn.rollback(); app.logger.error(f"Błąd rejestracji {username}: {e}"); flash('Błąd rejestracji.', 'danger')

        except Exception as e: app.logger.error(f"Błąd logowania/rejestracji {username}: {e}"); flash('Błąd serwera.', 'danger')
        finally:
            if cursor and not cursor.closed: cursor.close()
            if conn and not conn.closed and not user_logged_in: conn.close() # Zamknij tylko jeśli nie było przekierowania

        return redirect(url_for('login')) # Po rejestracji lub błędzie logowania

    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username')
    session.clear()
    if username: app.logger.info(f"User '{username}' logged out.")
    flash('Wylogowano pomyślnie.', 'info')
    return redirect(url_for('index'))

@app.route('/restaurant/<int:restaurant_id>')
def restaurant_detail(restaurant_id):
    conn = get_db_connection()
    restaurant_display = None; dishes_display = []
    if not conn: return redirect(url_for('index'))
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
        else: flash('Nie znaleziono restauracji.', 'warning'); return redirect(url_for('index'))
    except Exception as e: app.logger.error(f"Błąd szczegółów restauracji {restaurant_id}: {e}"); flash("Wystąpił błąd.", "danger"); return redirect(url_for('index'))
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    restaurants_display = []
    if not query: return render_template('index.html', restaurants=restaurants_display, search_query=query)
    conn = get_db_connection()
    if not conn: return render_template('index.html', restaurants=restaurants_display, search_query=query)
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        search_term = f"%{query}%"
        sql = 'SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" WHERE "Name" ILIKE %s OR "CuisineType" ILIKE %s OR "City" ILIKE %s ORDER BY "Name"'
        cursor.execute(sql, (search_term, search_term, search_term))
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        for r in restaurants: r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"
        restaurants_display = restaurants
        if not restaurants_display: flash(f"Nie znaleziono restauracji dla '{query}'.", "info")
    except Exception as e: app.logger.error(f"Błąd wyszukiwania '{query}': {e}"); flash("Błąd wyszukiwania.", "danger")
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()
    return render_template('index.html', restaurants=restaurants_display, search_query=query)

# --- Koszyk ---
@app.route('/cart/add/<int:dish_id>', methods=['POST'])
def add_to_cart(dish_id):
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    try: quantity = int(request.form.get('quantity', 1)); assert quantity > 0
    except: flash('Nieprawidłowa ilość.', 'warning'); return redirect(request.referrer or url_for('index'))

    conn = get_db_connection(); redirect_url = request.referrer or url_for('index'); dish_data_dict = None
    if not conn: return redirect(redirect_url)
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "DishID", "Name", "Price" FROM "Dishes" WHERE "DishID" = %s', (dish_id,))
        dish_data_dict = row_to_dict(cursor, cursor.fetchone())
        if not dish_data_dict: flash('Nie znaleziono dania.', 'danger')
    except Exception as e: app.logger.error(f"Błąd pobierania dania {dish_id}: {e}"); flash("Błąd pobierania dania.", "danger")
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed and not dish_data_dict: conn.close()

    if dish_data_dict:
        if 'cart' not in session: session['cart'] = {}
        cart = session.get('cart', {}); dish_id_str = str(dish_id)
        try:
            price = float(dish_data_dict['Price']); current_quantity = cart.get(dish_id_str, {}).get('quantity', 0)
            cart[dish_id_str] = {'name': dish_data_dict['Name'], 'price': price, 'quantity': current_quantity + quantity}
            session['cart'] = cart; session.modified = True
            flash(f"Dodano '{dish_data_dict['Name']}' (x{quantity}).", 'success')
        except (KeyError, ValueError) as e: app.logger.error(f"Błąd koszyka {dish_id}: {e}"); flash("Błąd dodawania do koszyka.", "danger")
        finally:
            if conn and not conn.closed: conn.close()

    return redirect(redirect_url)

@app.route('/cart')
def view_cart():
    if 'user_id' not in session: flash('Zaloguj się, by zobaczyć koszyk.', 'warning'); return redirect(url_for('login'))
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
                app.logger.warning(f"Usuwanie błędnego elem. z koszyka ID {item_id_str}: {e}"); flash(f"Produkt ID {item_id_str} usunięty (błędne dane).", "warning"); del cart[item_id_str]; cart_changed = True
    if cart_changed: session['cart'] = cart; session.modified = True
    # Przekazujemy total_price do szablonu
    return render_template('cart.html', cart_items=items_display, total_price=total_price)

@app.route('/cart/remove/<dish_id>', methods=['POST'])
def remove_from_cart(dish_id):
    if 'user_id' not in session: flash('Zaloguj się.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {}); dish_id_str = str(dish_id)
    if dish_id_str in cart: item_name = cart[dish_id_str].get('name', f'Produkt ID {dish_id_str}'); del cart[dish_id_str]; session['cart'] = cart; session.modified = True; flash(f"Usunięto '{item_name}'.", 'info')
    else: flash('Tego produktu nie ma w koszyku.', 'warning')
    return redirect(url_for('view_cart'))

# --- Przepływ Płatności i Zamówienia ---

@app.route('/payment', methods=['GET'])
def payment_page():
    """Wyświetla stronę z udawaną płatnością."""
    if 'user_id' not in session:
        flash('Zaloguj się, aby przejść do płatności.', 'warning')
        return redirect(url_for('login'))
    cart = session.get('cart', {})
    if not cart:
        flash('Twój koszyk jest pusty.', 'warning')
        return redirect(url_for('index'))

    total_price = 0.0
    # Oblicz sumę ponownie dla pewności
    for item_id_str, item_data in cart.items():
        try: price = float(item_data['price']); quantity = int(item_data['quantity']); total_price += price * quantity
        except: pass # Ignoruj błędy tutaj, walidacja była w view_cart

    return render_template('payment.html', total_price=total_price)

@app.route('/place_order', methods=['POST'])
def place_order():
    """Logika faktycznego składania zamówienia (dawne /checkout)."""
    if 'user_id' not in session:
        flash('Musisz być zalogowany, aby złożyć zamówienie.', 'warning')
        return redirect(url_for('login'))

    cart = session.get('cart', {})
    if not cart:
        flash('Twój koszyk jest pusty.', 'warning')
        return redirect(url_for('view_cart'))

    total_price = 0.0
    order_items_data = []
    is_cart_valid = True

    for item_id_str, item_data in cart.items():
        try:
            dish_id = int(item_id_str); price = float(item_data['price']); quantity = int(item_data['quantity'])
            if quantity <= 0 or price < 0: raise ValueError("Nieprawidłowe dane")
            total_price += price * quantity
            order_items_data.append({'dish_id': dish_id, 'quantity': quantity, 'price_per_item': price})
        except (KeyError, ValueError, TypeError) as e:
            app.logger.error(f"Błąd walidacji koszyka w place_order dla ID {item_id_str}: {e}"); flash(f"Problem z produktem ID {item_id_str}. Popraw koszyk.", "danger"); is_cart_valid = False; break
    if not is_cart_valid: return redirect(url_for('view_cart'))

    conn = get_db_connection()
    if not conn: flash('Błąd bazy danych.', 'danger'); return redirect(url_for('payment_page')) # Wróć do płatności
    cursor = None; new_order_id = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('INSERT INTO "Orders" ("UserID", "TotalPrice", "Status") VALUES (%s, %s, %s) RETURNING "OrderID"', (session['user_id'], total_price, 'Złożone'))
        result = cursor.fetchone()
        if result: new_order_id = result['OrderID']; app.logger.info(f"Zamówienie #{new_order_id} dla UserID: {session['user_id']}")
        else: raise Exception("Nie pobrano ID nowego zamówienia.")

        insert_item_sql = 'INSERT INTO "OrderItems" ("OrderID", "DishID", "Quantity", "PricePerItem") VALUES (%s, %s, %s, %s)'
        items_to_insert = [(new_order_id, item['dish_id'], item['quantity'], item['price_per_item']) for item in order_items_data]
        cursor.executemany(insert_item_sql, items_to_insert)
        app.logger.info(f"Dodano {len(items_to_insert)} pozycji do zam. #{new_order_id}")

        conn.commit(); session.pop('cart', None); session.modified = True # Wyczyść koszyk
        flash('Zamówienie złożone pomyślnie!', 'success')
        # Zamknij połączenie przed przekierowaniem
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()
        return redirect(url_for('order_confirmation', order_id=new_order_id))

    except Exception as e:
        conn.rollback(); app.logger.error(f"BŁĄD place_order dla UserID {session.get('user_id')}: {e}"); flash('Błąd podczas składania zamówienia.', 'danger')
        return redirect(url_for('payment_page')) # Wróć do płatności w razie błędu
    finally:
        if cursor and not cursor.closed: cursor.close()
        if conn and not conn.closed: conn.close()


@app.route('/order_confirmation/<int:order_id>')
def order_confirmation(order_id):
     if 'user_id' not in session: flash('Zaloguj się.', 'warning'); return redirect(url_for('login'))
     return render_template('order_confirmation.html', order_id=order_id)

# --- Śledzenie Zamówień Użytkownika ---

@app.route('/orders')
def my_orders():
    """Wyświetla listę zamówień zalogowanego użytkownika."""
    if 'user_id' not in session:
        flash('Zaloguj się, aby zobaczyć swoje zamówienia.', 'warning')
        return redirect(url_for('login'))

    user_id = session['user_id']
    orders_list = []
    conn = get_db_connection()
    if not conn: return render_template('my_orders.html', orders=orders_list) # Pokaż pustą listę przy błędzie DB

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            'SELECT "OrderID", "OrderDate", "TotalPrice", "Status" FROM "Orders" WHERE "UserID" = %s ORDER BY "OrderDate" DESC',
            (user_id,)
        )
        orders_list = rows_to_dicts(cursor, cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Błąd pobierania zamówień dla UserID {user_id}: {e}")
        flash("Wystąpił błąd podczas pobierania historii zamówień.", "danger")
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()

    return render_template('my_orders.html', orders=orders_list)


@app.route('/orders/<int:order_id>')
def track_order_detail(order_id):
    """Wyświetla szczegóły konkretnego zamówienia."""
    if 'user_id' not in session:
        flash('Zaloguj się, aby zobaczyć szczegóły zamówienia.', 'warning')
        return redirect(url_for('login'))

    user_id = session['user_id']
    order_details = None
    order_items = []
    conn = get_db_connection()
    if not conn: return redirect(url_for('my_orders')) # Wróć do listy przy błędzie DB

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Pobierz dane zamówienia, sprawdzając czy należy do użytkownika
        cursor.execute(
            'SELECT "OrderID", "UserID", "OrderDate", "TotalPrice", "Status" FROM "Orders" WHERE "OrderID" = %s',
            (order_id,)
        )
        order_details = row_to_dict(cursor, cursor.fetchone())

        # Sprawdzenie, czy zamówienie istnieje i czy należy do zalogowanego użytkownika (lub czy to admin)
        if not order_details:
            flash('Nie znaleziono zamówienia o podanym ID.', 'warning')
            return redirect(url_for('my_orders'))
        if order_details['UserID'] != user_id and not session.get('is_admin'):
            flash('Nie masz uprawnień, aby zobaczyć to zamówienie.', 'danger')
            return redirect(url_for('my_orders'))

        # Pobierz pozycje zamówienia, dołączając dane dań
        sql_items = """
            SELECT oi."Quantity", oi."PricePerItem", d."Name", d."ImageURL"
            FROM "OrderItems" oi
            JOIN "Dishes" d ON oi."DishID" = d."DishID"
            WHERE oi."OrderID" = %s
        """
        cursor.execute(sql_items, (order_id,))
        order_items = rows_to_dicts(cursor, cursor.fetchall())

    except Forbidden as e: # Obsługa abort(403) 
         app.logger.warning(f"Odmowa dostępu (403) dla UserID {user_id} do zamówienia ID {order_id}")
         return redirect(url_for('my_orders'))
    except Exception as e:
        app.logger.error(f"Błąd pobierania szczegółów zamówienia ID {order_id}: {e}")
        flash("Wystąpił błąd podczas pobierania szczegółów zamówienia.", "danger")
        return redirect(url_for('my_orders'))
    finally:
        if cursor: cursor.close()
        if conn and not conn.closed: conn.close()

    return render_template('track_order.html', order=order_details, items=order_items)


# --- Panel Administratora ---

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin/admin_dashboard.html')

# --- Zarządzanie Restauracjami ---
@app.route('/admin/restaurants', methods=['GET', 'POST'])
@admin_required
def manage_restaurants():
    conn = get_db_connection()
    if not conn: flash('Błąd połączenia z DB.', 'danger'); return redirect(url_for('admin_dashboard'))
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'POST':
            action = request.form.get('action'); form_submitted = False
            if action == 'add':
                form_submitted = True; name = request.form.get('name', '').strip()
                if not name: flash('Nazwa jest wymagana.', 'warning')
                else:
                    cuisine = request.form.get('cuisine', '').strip() or None; street = request.form.get('street', '').strip() or None
                    street_number = request.form.get('street_number', '').strip() or None; postal_code = request.form.get('postal_code', '').strip() or None
                    city = request.form.get('city', '').strip() or None; image_file = request.files.get('image')
                    image_s3_url = None
                    if image_file and image_file.filename != '':
                        if allowed_file(image_file.filename):
                            original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
                            unique_object_key = f"restaurants/restaurant_{uuid.uuid4()}.{extension}"
                            image_s3_url = upload_file_to_s3(image_file, S3_BUCKET_NAME, unique_object_key)
                            if not image_s3_url: flash('Błąd wgrywania pliku do S3.', 'danger')
                        else: flash('Niedozwolony typ pliku.', 'warning')
                    try:
                        sql = 'INSERT INTO "Restaurants" ("Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL") VALUES (%s, %s, %s, %s, %s, %s, %s)'
                        cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_s3_url))
                        conn.commit(); flash(f'Restauracja "{name}" dodana.', 'success')
                    except Exception as e: conn.rollback(); app.logger.error(f"Błąd dodawania restauracji '{name}': {e}"); flash('Błąd zapisu.', 'danger');
            elif action == 'delete':
                 form_submitted = True; restaurant_id_str = request.form.get('restaurant_id')
                 if not restaurant_id_str: flash('Nie podano ID.', 'warning')
                 else:
                     try:
                         restaurant_id = int(restaurant_id_str)
                         cursor.execute('SELECT "ImageURL" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,)); rest_img_row = cursor.fetchone()
                         image_s3_url_to_delete = rest_img_row['ImageURL'] if rest_img_row and rest_img_row['ImageURL'] else None
                         cursor.execute('SELECT "ImageURL" FROM "Dishes" WHERE "RestaurantID" = %s', (restaurant_id,)); dishes_images_rows = cursor.fetchall()
                         cursor.execute('DELETE FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,)); deleted_count = cursor.rowcount
                         conn.commit()
                         if deleted_count > 0:
                             app.logger.info(f"Usunięto restaurację ID: {restaurant_id}"); flash(f'Restauracja ID: {restaurant_id} usunięta.', 'success')
                             for dish_image_row in dishes_images_rows:
                                 if dish_image_row['ImageURL']: delete_file_from_s3(S3_BUCKET_NAME, dish_image_row['ImageURL'])
                             if image_s3_url_to_delete: delete_file_from_s3(S3_BUCKET_NAME, image_s3_url_to_delete)
                         else: flash(f'Nie znaleziono restauracji ID {restaurant_id}.', 'warning')
                     except ValueError: flash('Nieprawidłowe ID.', 'warning')
                     except Exception as e: conn.rollback(); app.logger.error(f"Błąd usuwania restauracji ID {restaurant_id_str}: {e}"); flash('Błąd usuwania.', 'danger')
            if form_submitted:
                if cursor: cursor.close();
                if conn and not conn.closed: conn.close()
                return redirect(url_for('manage_restaurants'))
        cursor.execute('SELECT "RestaurantID", "Name", "CuisineType", "Street", "StreetNumber", "PostalCode", "City", "ImageURL" FROM "Restaurants" ORDER BY "Name"')
        restaurants = rows_to_dicts(cursor, cursor.fetchall())
        restaurants_display = [{'FullAddress': format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "-", **r} for r in restaurants]
        return render_template('admin/manage_restaurants.html', restaurants=restaurants_display)
    except Exception as e: app.logger.error(f"Błąd w manage_restaurants: {e}"); flash("Wystąpił błąd.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close();
         if conn and not conn.closed: conn.close()

@app.route('/admin/restaurants/<int:restaurant_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_restaurant(restaurant_id):
    conn = get_db_connection();
    if not conn: flash('Błąd połączenia z DB.', 'danger'); return redirect(url_for('manage_restaurants'))
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor); cursor.execute('SELECT * FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,))
        restaurant = row_to_dict(cursor, cursor.fetchone())
        if not restaurant: flash('Nie znaleziono restauracji.', 'warning'); return redirect(url_for('manage_restaurants'))
        original_image_url = restaurant.get('ImageURL')
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            if not name: flash('Nazwa jest wymagana.', 'warning'); return render_template('admin/editRestaurant.html', restaurant=restaurant)
            cuisine = request.form.get('cuisine', '').strip() or None; street = request.form.get('street', '').strip() or None
            street_number = request.form.get('street_number', '').strip() or None; postal_code = request.form.get('postal_code', '').strip() or None
            city = request.form.get('city', '').strip() or None; image_file = request.files.get('image')
            image_url_to_save = original_image_url; new_image_uploaded_url = None; delete_old_image = False
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
                    unique_object_key = f"restaurants/restaurant_{uuid.uuid4()}.{extension}"; new_image_uploaded_url = upload_file_to_s3(image_file, S3_BUCKET_NAME, unique_object_key)
                    if new_image_uploaded_url: image_url_to_save = new_image_uploaded_url; delete_old_image = True
                    else: flash('Błąd wgrywania nowego obrazka.', 'danger'); image_url_to_save = original_image_url
                else: flash('Niedozwolony typ pliku.', 'warning'); image_url_to_save = original_image_url
            try:
                sql = """UPDATE "Restaurants" SET "Name"=%s, "CuisineType"=%s, "Street"=%s, "StreetNumber"=%s, "PostalCode"=%s, "City"=%s, "ImageURL"=%s WHERE "RestaurantID"=%s"""
                cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_url_to_save, restaurant_id)); conn.commit()
                flash(f'Restauracja "{name}" zaktualizowana.', 'success')
                if delete_old_image and original_image_url: delete_file_from_s3(S3_BUCKET_NAME, original_image_url)
                if cursor: cursor.close();
                if conn and not conn.closed: conn.close()
                return redirect(url_for('manage_restaurants'))
            except Exception as e:
                conn.rollback(); app.logger.error(f"Błąd aktualizacji restauracji ID {restaurant_id}: {e}"); flash('Błąd zapisu.', 'danger');
                if new_image_uploaded_url: delete_file_from_s3(S3_BUCKET_NAME, new_image_uploaded_url)
                failed_data = request.form.to_dict(); failed_data['RestaurantID'] = restaurant_id; failed_data['ImageURL'] = original_image_url
                return render_template('admin/editRestaurant.html', restaurant=failed_data)
        return render_template('admin/editRestaurant.html', restaurant=restaurant)
    except Exception as e: app.logger.error(f"Błąd edycji restauracji ID {restaurant_id}: {e}"); flash("Wystąpił błąd.", "danger"); return redirect(url_for('manage_restaurants'))
    finally:
        if cursor: cursor.close();
        if conn and not conn.closed: conn.close()

# --- Zarządzanie Daniami ---
@app.route('/admin/dishes', methods=['GET', 'POST'])
@app.route('/admin/dishes/<int:restaurant_id>', methods=['GET', 'POST'])
@admin_required
def manage_dishes(restaurant_id=None):
    conn = get_db_connection();
    if not conn: flash('Błąd połączenia z DB.', 'danger'); return redirect(url_for('admin_dashboard'))
    restaurants_list = []; dishes_display = []; selected_restaurant_name = None; cursor = None; redirect_to_restaurant_id = restaurant_id
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor); cursor.execute('SELECT "RestaurantID", "Name" FROM "Restaurants" ORDER BY "Name"'); restaurants_rows = cursor.fetchall()
        restaurants_list = [(row['RestaurantID'], row['Name']) for row in restaurants_rows]
        if request.method == 'POST':
            action = request.form.get('action'); form_submitted = False; rest_id_form_str = request.form.get('restaurant_id')
            try: current_restaurant_id = int(rest_id_form_str) if rest_id_form_str else restaurant_id
            except: flash('Nieprawidłowe ID restauracji.', 'danger'); return redirect(url_for('manage_dishes'))
            if action == 'add':
                form_submitted = True
                if not current_restaurant_id: flash('Wybierz restaurację.', 'warning')
                else:
                    name = request.form.get('name', '').strip(); description = request.form.get('description', '').strip() or None
                    price_str = request.form.get('price'); image_file = request.files.get('image'); image_s3_url = None; price_decimal = None
                    if not name or not price_str: flash('Nazwa i cena są wymagane.', 'warning')
                    else:
                        try: price_decimal = float(price_str); assert price_decimal >= 0
                        except: flash('Nieprawidłowa cena.', 'warning'); price_decimal = None
                    if name and price_decimal is not None:
                        if image_file and image_file.filename != '':
                             if allowed_file(image_file.filename):
                                 original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
                                 unique_object_key = f"dishes/dish_{uuid.uuid4()}.{extension}"; image_s3_url = upload_file_to_s3(image_file, S3_BUCKET_NAME, unique_object_key)
                                 if not image_s3_url: flash('Błąd wgrywania obrazka dania.', 'danger')
                             else: flash('Niedozwolony typ pliku.', 'warning')
                        try:
                            sql = 'INSERT INTO "Dishes" ("RestaurantID", "Name", "Description", "Price", "ImageURL") VALUES (%s, %s, %s, %s, %s)'
                            cursor.execute(sql, (current_restaurant_id, name, description, price_decimal, image_s3_url)); conn.commit()
                            flash(f'Danie "{name}" dodane.', 'success')
                        except Exception as e: conn.rollback(); app.logger.error(f"Błąd dodawania dania '{name}': {e}"); flash('Błąd zapisu dania.', 'danger');
            elif action == 'delete':
                 form_submitted = True; dish_id_str = request.form.get('dish_id')
                 if not dish_id_str or not current_restaurant_id: flash('Brak ID dania/restauracji.', 'warning')
                 else:
                      try:
                          dish_id = int(dish_id_str); cursor.execute('SELECT "ImageURL" FROM "Dishes" WHERE "DishID" = %s', (dish_id,)); dish_img_row = cursor.fetchone()
                          image_s3_url_to_delete = dish_img_row['ImageURL'] if dish_img_row and dish_img_row['ImageURL'] else None
                          cursor.execute('DELETE FROM "Dishes" WHERE "DishID" = %s AND "RestaurantID" = %s', (dish_id, current_restaurant_id)); deleted_count = cursor.rowcount; conn.commit()
                          if deleted_count > 0:
                              app.logger.info(f"Usunięto danie ID: {dish_id}"); flash(f'Danie ID: {dish_id} usunięte.', 'success');
                              # Usuń plik z S3 tylko jeśli usunięcie z bazy się powiodło
                              if image_s3_url_to_delete: delete_file_from_s3(S3_BUCKET_NAME, image_s3_url_to_delete)
                          else: flash(f'Nie znaleziono dania ID {dish_id}.', 'warning')
                      except ValueError: flash('Nieprawidłowe ID dania.', 'warning')
                      except Exception as e: conn.rollback(); app.logger.error(f"Błąd usuwania dania ID {dish_id_str}: {e}"); flash('Błąd usuwania.', 'danger')
            if form_submitted:
                 redirect_to_restaurant_id = current_restaurant_id or restaurant_id;
                 if cursor: cursor.close();
                 if conn and not conn.closed: conn.close()
                 redirect_url = url_for('manage_dishes', restaurant_id=redirect_to_restaurant_id) if redirect_to_restaurant_id else url_for('manage_dishes')
                 return redirect(redirect_url)
        if restaurant_id:
            cursor.execute('SELECT "Name" FROM "Restaurants" WHERE "RestaurantID" = %s', (restaurant_id,)); rest_name_row = cursor.fetchone()
            if rest_name_row:
                selected_restaurant_name = rest_name_row['Name']; cursor.execute('SELECT "DishID", "Name", "Description", "Price", "ImageURL" FROM "Dishes" WHERE "RestaurantID" = %s ORDER BY "Name"', (restaurant_id,)); dishes_display = rows_to_dicts(cursor, cursor.fetchall())
            else: flash(f"Restauracja ID {restaurant_id} nie znaleziona.", "warning"); return redirect(url_for('manage_dishes'))
        return render_template('admin/manage_dishes.html', dishes=dishes_display, restaurants=restaurants_list, selected_restaurant_id=restaurant_id, selected_restaurant_name=selected_restaurant_name)
    except Exception as e: app.logger.error(f"Błąd w manage_dishes: {e}"); flash("Wystąpił błąd.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close();
         if conn and not conn.closed: conn.close()

@app.route('/admin/dishes/<int:dish_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_dish(dish_id):
    conn = get_db_connection();
    if not conn: flash('Błąd połączenia z DB.', 'danger'); return redirect(url_for('manage_dishes'))
    cursor = None; restaurants_list = []
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor); cursor.execute('SELECT "RestaurantID", "Name" FROM "Restaurants" ORDER BY "Name"'); restaurants_rows = cursor.fetchall()
        restaurants_list = [(row['RestaurantID'], row['Name']) for row in restaurants_rows]
        cursor.execute('SELECT * FROM "Dishes" WHERE "DishID" = %s', (dish_id,)); dish = row_to_dict(cursor, cursor.fetchone())
        if not dish: flash('Nie znaleziono dania.', 'warning'); return redirect(url_for('manage_dishes'))
        original_image_url = dish.get('ImageURL')
        if request.method == 'POST':
            name = request.form.get('name', '').strip(); price_str = request.form.get('price'); restaurant_id_str = request.form.get('restaurant_id')
            price_decimal = None; new_restaurant_id = None
            if not name or not price_str or not restaurant_id_str: flash('Nazwa, cena i restauracja są wymagane.', 'warning'); return render_template('admin/editMenuItem.html', dish=dish, restaurants=restaurants_list)
            try: price_decimal = float(price_str); assert price_decimal >= 0
            except: flash('Nieprawidłowa cena.', 'warning'); price_decimal = None
            try: new_restaurant_id = int(restaurant_id_str)
            except: flash('Nieprawidłowe ID restauracji.', 'warning'); new_restaurant_id = None
            if name and price_decimal is not None and new_restaurant_id is not None:
                 description = request.form.get('description', '').strip() or None; image_file = request.files.get('image')
                 image_url_to_save = original_image_url; new_image_uploaded_url = None; delete_old_image = False
                 if image_file and image_file.filename != '':
                     if allowed_file(image_file.filename):
                         original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
                         unique_object_key = f"dishes/dish_{uuid.uuid4()}.{extension}"; new_image_uploaded_url = upload_file_to_s3(image_file, S3_BUCKET_NAME, unique_object_key)
                         if new_image_uploaded_url: image_url_to_save = new_image_uploaded_url; delete_old_image = True
                         else: flash('Błąd wgrywania nowego obrazka.', 'danger'); image_url_to_save = original_image_url
                     else: flash('Niedozwolony typ pliku.', 'warning'); image_url_to_save = original_image_url
                 try:
                     sql = """UPDATE "Dishes" SET "Name"=%s, "Description"=%s, "Price"=%s, "RestaurantID"=%s, "ImageURL"=%s WHERE "DishID"=%s"""
                     cursor.execute(sql, (name, description, price_decimal, new_restaurant_id, image_url_to_save, dish_id)); conn.commit()
                     flash(f'Danie "{name}" zaktualizowane.', 'success')
                     if delete_old_image and original_image_url: delete_file_from_s3(S3_BUCKET_NAME, original_image_url)
                     if cursor: cursor.close();
                     if conn and not conn.closed: conn.close()
                     return redirect(url_for('manage_dishes', restaurant_id=new_restaurant_id))
                 except Exception as e:
                     conn.rollback(); app.logger.error(f"Błąd aktualizacji dania ID {dish_id}: {e}"); flash('Błąd zapisu.', 'danger');
                     if new_image_uploaded_url: delete_file_from_s3(S3_BUCKET_NAME, new_image_uploaded_url)
                     failed_data = request.form.to_dict(); failed_data['DishID'] = dish_id; failed_data['ImageURL'] = original_image_url
                     return render_template('admin/editMenuItem.html', dish=failed_data, restaurants=restaurants_list)
            else: return render_template('admin/editMenuItem.html', dish=dish, restaurants=restaurants_list)
        return render_template('admin/editMenuItem.html', dish=dish, restaurants=restaurants_list)
    except Exception as e:
        app.logger.error(f"Błąd edycji dania ID {dish_id}: {e}"); flash("Wystąpił błąd.", "danger"); fallback_restaurant_id = dish.get('RestaurantID') if isinstance(dish, dict) else None
        redirect_url = url_for('manage_dishes', restaurant_id=fallback_restaurant_id) if fallback_restaurant_id else url_for('admin_dashboard')
        return redirect(redirect_url)
    finally:
        if cursor: cursor.close();
        if conn and not conn.closed: conn.close()


# --- Zarządzanie Użytkownikami (Rozszerzone) ---

@app.route('/admin/users', methods=['GET', 'POST'])
@admin_required
def manage_users():
    conn = get_db_connection()
    if not conn: flash("Błąd połączenia z DB.", "danger"); return render_template('admin/manage_users.html', users=[])

    cursor = None
    # Obsługa usuwania użytkownika
    if request.method == 'POST' and request.form.get('action') == 'delete':
        user_id_to_delete_str = request.form.get('user_id')
        current_user_id = session.get('user_id')
        form_submitted = True # Flaga, żeby wiedzieć, że była akcja POST

        if not user_id_to_delete_str:
            flash('Nie podano ID użytkownika do usunięcia.', 'warning')
        else:
            try:
                user_id_to_delete = int(user_id_to_delete_str)
                if user_id_to_delete == current_user_id:
                    flash('Nie możesz usunąć własnego konta administratora.', 'danger')
                else:
                    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                    # Sprawdź, czy to nie jedyny admin
                    cursor.execute('SELECT COUNT(*) FROM "Users" WHERE "IsAdmin" = TRUE')
                    admin_count = cursor.fetchone()[0]
                    cursor.execute('SELECT "IsAdmin" FROM "Users" WHERE "UserID" = %s', (user_id_to_delete,))
                    user_to_delete_data = cursor.fetchone()

                    if user_to_delete_data and user_to_delete_data['IsAdmin'] and admin_count <= 1:
                        flash('Nie można usunąć ostatniego administratora.', 'danger')
                    else:
                        cursor.execute('DELETE FROM "Users" WHERE "UserID" = %s', (user_id_to_delete,))
                        deleted_count = cursor.rowcount
                        conn.commit()
                        if deleted_count > 0:
                            flash(f'Użytkownik o ID {user_id_to_delete} został usunięty.', 'success')
                            app.logger.info(f"Admin UserID {current_user_id} deleted UserID {user_id_to_delete}")
                        else:
                            flash(f'Nie znaleziono użytkownika o ID {user_id_to_delete}.', 'warning')

            except ValueError:
                flash('Nieprawidłowe ID użytkownika.', 'warning')
            except Exception as e:
                conn.rollback()
                app.logger.error(f"Błąd usuwania użytkownika ID {user_id_to_delete_str} przez UserID {current_user_id}: {e}")
                flash('Wystąpił błąd podczas usuwania użytkownika.', 'danger')
            finally:
                 # Zamknij kursor jeśli był otwarty
                 if cursor and not cursor.closed: cursor.close()

        # Przekieruj po akcji POST, aby uniknąć F5
        if form_submitted:
            if conn and not conn.closed: conn.close()
            return redirect(url_for('manage_users'))

    # Metoda GET - wyświetlanie listy użytkowników
    users_display = []
    try:
        if cursor is None or cursor.closed:
             cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT "UserID", "Username", "IsAdmin" FROM "Users" ORDER BY "Username"')
        users_display = rows_to_dicts(cursor, cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Błąd pobierania listy użytkowników: {e}")
        flash("Błąd pobierania listy użytkowników.", "danger")
    finally:
        if cursor and not cursor.closed: cursor.close()
        if conn and not conn.closed: conn.close()

    return render_template('admin/manage_users.html', users=users_display)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    """Edycja danych użytkownika (login, uprawnienia)."""
    conn = get_db_connection()
    if not conn: flash("Błąd połączenia z DB.", "danger"); return redirect(url_for('manage_users'))
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Pobierz dane użytkownika do edycji
        cursor.execute('SELECT "UserID", "Username", "IsAdmin" FROM "Users" WHERE "UserID" = %s', (user_id,))
        user = row_to_dict(cursor, cursor.fetchone())
        if not user: flash('Nie znaleziono użytkownika.', 'warning'); return redirect(url_for('manage_users'))

        # Pobierz liczbę adminów do walidacji
        cursor.execute('SELECT COUNT(*) FROM "Users" WHERE "IsAdmin" = TRUE')
        admin_count = cursor.fetchone()[0]

        # Obsługa POST (zapis zmian)
        if request.method == 'POST':
            new_username = request.form.get('username', '').strip()
            is_admin_form = request.form.get('is_admin') == 'on'
            current_user_id = session.get('user_id')

            if not new_username:
                flash('Nazwa użytkownika nie może być pusta.', 'warning')
                return render_template('admin/edit_user.html', user=user) # Pokazuj stare dane w formularzu

            # Sprawdzenie, czy nie odbieramy uprawnień ostatniemu adminowi
            if user['IsAdmin'] and not is_admin_form and admin_count <= 1:
                 flash('Nie można odebrać uprawnień ostatniemu administratorowi.', 'danger')
                 return render_template('admin/edit_user.html', user=user)
            # Sprawdzenie, czy nie próbujemy edytować własnego konta admina w sposób, który by nas zablokował
            if user_id == current_user_id and user['IsAdmin'] and not is_admin_form:
                 flash('Nie możesz odebrać uprawnień administratora samemu sobie.', 'danger')
                 return render_template('admin/edit_user.html', user=user)

            try:
                # Sprawdzenie unikalności nowej nazwy użytkownika (jeśli się zmieniła)
                if new_username != user['Username']:
                    cursor.execute('SELECT "UserID" FROM "Users" WHERE "Username" = %s', (new_username,))
                    if cursor.fetchone():
                        flash(f'Nazwa użytkownika "{new_username}" jest już zajęta.', 'warning')
                        # Przekaż dane z formularza z powrotem, aby użytkownik nie musiał wpisywać od nowa
                        user['Username'] = new_username # Zaktualizuj tylko w słowniku do wyświetlenia
                        user['IsAdmin'] = is_admin_form
                        return render_template('admin/edit_user.html', user=user)

                # Aktualizacja danych użytkownika
                sql = 'UPDATE "Users" SET "Username" = %s, "IsAdmin" = %s WHERE "UserID" = %s'
                cursor.execute(sql, (new_username, is_admin_form, user_id))
                conn.commit()
                flash(f'Dane użytkownika "{new_username}" zostały zaktualizowane.', 'success')
                app.logger.info(f"Admin UserID {current_user_id} updated UserID {user_id}. New data: username={new_username}, is_admin={is_admin_form}")

                # Zamknij zasoby przed przekierowaniem
                if cursor: cursor.close()
                if conn and not conn.closed: conn.close()
                return redirect(url_for('manage_users'))

            except Exception as e:
                conn.rollback()
                app.logger.error(f"Błąd aktualizacji użytkownika ID {user_id}: {e}")
                flash('Wystąpił błąd podczas zapisu zmian.', 'danger')
                # Renderuj ponownie formularz z danymi, które użytkownik próbował zapisać
                failed_data = request.form.to_dict(); failed_data['UserID'] = user_id
                # Ustaw 'IsAdmin' na podstawie checkboxa
                failed_data['IsAdmin'] = request.form.get('is_admin') == 'on'
                return render_template('admin/edit_user.html', user=failed_data)

        # Metoda GET - wyświetlenie formularza
        return render_template('admin/edit_user.html', user=user)

    except Exception as e:
        app.logger.error(f"Błąd edycji użytkownika ID {user_id}: {e}"); flash("Wystąpił błąd.", "danger")
        return redirect(url_for('manage_users'))
    finally:
        if cursor and not cursor.closed: cursor.close()
        if conn and not conn.closed: conn.close()


# --- Zarządzanie Zamówieniami ---
@app.route('/admin/orders', methods=['GET', 'POST'])
@admin_required
def view_orders():
    conn = get_db_connection();
    if not conn: flash('Błąd połączenia z DB.', 'danger'); return redirect(url_for('admin_dashboard'))
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
                        cursor.execute('UPDATE "Orders" SET "Status" = %s WHERE "OrderID" = %s', (new_status, order_id)); conn.commit();
                        app.logger.info(f"Zmieniono status zam. #{order_id} na '{new_status}'."); flash('Status zamówienia zaktualizowany.', 'success')
                    except ValueError: flash('Nieprawidłowe ID zamówienia.', 'warning')
                    except Exception as e: conn.rollback(); app.logger.error(f"Błąd aktualizacji statusu zam. #{order_id_str}: {e}"); flash('Błąd aktualizacji statusu.', 'danger')
                else: flash('Nieprawidłowe dane do aktualizacji.', 'warning')
            if form_submitted:
                if cursor: cursor.close();
                if conn and not conn.closed: conn.close()
                return redirect(url_for('view_orders'))
        sql = """SELECT o."OrderID", u."Username", o."OrderDate", o."TotalPrice", o."Status" FROM "Orders" o LEFT JOIN "Users" u ON o."UserID" = u."UserID" ORDER BY o."OrderDate" DESC"""
        cursor.execute(sql); orders = rows_to_dicts(cursor, cursor.fetchall()); orders_display = []
        for o in orders: o['Username'] = o['Username'] or "[Usunięty]"; orders_display.append(o)
        return render_template('admin/view_orders.html', orders=orders_display)
    except Exception as e: app.logger.error(f"Błąd w widoku zamówień admina: {e}"); flash("Błąd pobierania zamówień.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close();
         if conn and not conn.closed: conn.close()

# --- Uruchomienie Aplikacji ---
if __name__ == '__main__':
    # Uruchomienie lokalne (nie używane przez App Runner)
    app.logger.info("Uruchamianie lokalnego serwera Flask...")
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 't'], host='0.0.0.0', port=port)
