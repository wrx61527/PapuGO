# -*- coding: utf-8 -*-
import os
import pyodbc 
import uuid
import logging
from functools import wraps
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    send_from_directory, abort
)

print(pyodbc.drivers())

# --- Konfiguracja Początkowa ---
load_dotenv()
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'domyslny_bardzo_tajny_klucz_do_zmiany_natychmiast')
connection_string = os.getenv('DATABASE_CONNECTION_STRING')

if not connection_string: app.logger.critical("KRYTYCZNY BŁĄD: Brak DATABASE_CONNECTION_STRING!")
if app.secret_key == 'domyslny_bardzo_tajny_klucz_do_zmiany_natychmiast': app.logger.warning("Używasz DOMYŚLNEGO sekretnego klucza Flaska!")

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    try: os.makedirs(UPLOAD_FOLDER); app.logger.info(f"Utworzono folder {UPLOAD_FOLDER}")
    except OSError as e: app.logger.error(f"Nie można utworzyć folderu {UPLOAD_FOLDER}: {e}")

# --- Funkcje Pomocnicze ---

def get_db_connection():
    """Nawiązuje połączenie z bazą danych Azure SQL używając pyodbc."""
    if not connection_string:
         app.logger.error("Próba połączenia z bazą bez connection stringa."); flash("Błąd krytyczny: Brak konfiguracji bazy danych!", "danger"); return None
    try:
        conn = pyodbc.connect(connection_string, autocommit=False, encoding='utf-8')
        app.logger.debug("Połączenie pyodbc nawiązane."); return conn
    except pyodbc.Error as ex:
        sqlstate = ex.args[0]; error_message = f"Błąd połączenia pyodbc (SQLSTATE: {sqlstate}): {ex}"
        app.logger.error(error_message)
        flash(f"Szczegółowy błąd DB (pyodbc): {ex}", "danger") # Flash diagnostyczny
    except Exception as e:
        error_message = f"Nieoczekiwany błąd połączenia pyodbc: {e}"; app.logger.error(error_message)
        flash(f"Nieoczekiwany błąd połączenia: {e}", "danger") # Flash diagnostyczny
    return None

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def format_address(street, number, code, city):
    address_parts = [part for part in [street, number] if part]; city_parts = [part for part in [code, city] if part]
    full_address = " ".join(address_parts); city_str = " ".join(city_parts)
    if city_str: full_address += (", " if full_address else "") + city_str
    return full_address if full_address else None

# Konwertuje wiersze pyodbc na słowniki
def rows_to_dicts(cursor, rows):
     columns = [column[0] for column in cursor.description]
     return [dict(zip(columns, row)) for row in rows]

def row_to_dict(cursor, row):
    if row:
        columns = [column[0] for column in cursor.description]; return dict(zip(columns, row))
    return None

# --- Dekorator Admina ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'): flash('Dostęp wymaga uprawnień administratora.', 'danger'); return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Trasy Frontend ---

@app.route('/')
def index():
    conn = get_db_connection(); restaurants_display = []
    if not conn: flash("Nie udało się połączyć z bazą danych.", "danger"); return render_template('index.html', restaurants=restaurants_display)
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants ORDER BY Name")
        restaurants = rows_to_dicts(cursor, cursor.fetchall()) # Konwersja
        for r in restaurants: r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"; restaurants_display.append(r)
    except Exception as e: app.logger.error(f"Błąd pobierania R: {e}"); flash("Błąd pobierania restauracji.", "danger")
    finally:
        if cursor: cursor.close() # Zamknij kursor
        if conn: conn.close()
    return render_template('index.html', restaurants=restaurants_display)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action'); username = request.form.get('username'); password = request.form.get('password')
        if not username or not password: flash('Nazwa i hasło wymagane.', 'warning'); return redirect(url_for('login'))
        conn = get_db_connection()
        if not conn: flash('Błąd połączenia.', 'danger'); return redirect(url_for('login'))
        user_logged_in = False; cursor = None
        try:
            cursor = conn.cursor()
            if action == 'login':
                cursor.execute("SELECT UserID, Username, IsAdmin, Password FROM Users WHERE Username = ?", (username,))
                user_row = row_to_dict(cursor, cursor.fetchone())
                # UŻYJ HASHOWANIA!
                if user_row and user_row['Password'] == password:
                    session['user_id'] = user_row['UserID']; session['username'] = user_row['Username']; session['is_admin'] = user_row['IsAdmin']
                    session.permanent = True; app.logger.info(f"User '{username}' logged in."); flash('Zalogowano!', 'success'); user_logged_in = True
                    redirect_url = url_for('admin_dashboard') if user_row['IsAdmin'] else url_for('index'); conn.close(); return redirect(redirect_url) # Zamknij TYLKO tu
                else: flash('Nieprawidłowe dane.', 'danger')
            elif action == 'register':
                try:
                    # ZAHASHUJ HASŁO!
                    cursor.execute("INSERT INTO Users (Username, Password) VALUES (?, ?)", (username, password))
                    conn.commit(); app.logger.info(f"Zarejestrowano: '{username}'."); flash('Rejestracja OK.', 'success')
                except pyodbc.IntegrityError: flash('Nazwa zajęta.', 'warning'); conn.rollback()
                except Exception as e: conn.rollback(); app.logger.error(f"Błąd rejestracji: {e}"); flash('Błąd rejestracji.', 'danger')
        except Exception as e: app.logger.error(f"Błąd login/reg: {e}"); flash('Błąd serwera.', 'danger')
        finally:
            if cursor: cursor.close()
            if conn and not user_logged_in: conn.close() # Zamknij jeśli nie zamknięto wcześniej
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username'); session.clear()
    if username: app.logger.info(f"User '{username}' logged out.")
    flash('Wylogowano.', 'info'); return redirect(url_for('index'))

@app.route('/restaurant/<int:restaurant_id>')
def restaurant_detail(restaurant_id):
    conn = get_db_connection(); restaurant_display = None; dishes_display = []; cursor = None
    if not conn: flash("Błąd połączenia.", "danger"); return redirect(url_for('index'))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
        restaurant_display = row_to_dict(cursor, cursor.fetchone())
        if restaurant_display:
             restaurant_display['FullAddress'] = format_address(restaurant_display.get('Street'), restaurant_display.get('StreetNumber'), restaurant_display.get('PostalCode'), restaurant_display.get('City')) or "Brak adresu"
             cursor.execute("SELECT DishID, Name, Description, Price, ImageURL FROM Dishes WHERE RestaurantID = ? ORDER BY Name", (restaurant_id,))
             dishes_display = rows_to_dicts(cursor, cursor.fetchall())
             return render_template('restaurant_detail.html', restaurant=restaurant_display, dishes=dishes_display) # Return w try jest OK
        else: flash('Nie znaleziono restauracji.', 'warning'); return redirect(url_for('index'))
    except Exception as e: app.logger.error(f"Błąd pobierania R {restaurant_id}: {e}"); flash("Błąd pobierania danych.", "danger"); return redirect(url_for('index'))
    finally:
         if cursor: cursor.close()
         if conn: conn.close() # Finally wykona się nawet po return w try/except

@app.route('/search')
def search():
    query = request.args.get('query', '').strip(); restaurants_display = []
    if query:
        conn = get_db_connection(); cursor = None
        if not conn: flash("Błąd połączenia.", "danger"); return render_template('index.html', restaurants=restaurants_display, search_query=query)
        try:
            cursor = conn.cursor(); search_term = f"%{query}%"
            sql = "SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants WHERE Name LIKE ? OR CuisineType LIKE ? OR City LIKE ? ORDER BY Name"
            cursor.execute(sql, (search_term, search_term, search_term))
            restaurants = rows_to_dicts(cursor, cursor.fetchall())
            for r in restaurants: r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "Brak adresu"; restaurants_display.append(r)
            if not restaurants_display: flash(f"Brak wyników dla '{query}'.", "info")
        except Exception as e: app.logger.error(f"Błąd search ('{query}'): {e}"); flash("Błąd wyszukiwania.", "danger")
        finally:
             if cursor: cursor.close()
             if conn: conn.close()
    return render_template('index.html', restaurants=restaurants_display, search_query=query)

# --- Trasy Koszyka i Zamówień ---

@app.route('/cart/add/<int:dish_id>', methods=['POST'])
def add_to_cart(dish_id):
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    try: quantity = int(request.form.get('quantity', 1)); assert quantity > 0
    except: flash('Nieprawidłowa ilość.', 'warning'); return redirect(request.referrer or url_for('index'))
    conn = get_db_connection(); dish_data_dict = None; redirect_url = request.referrer or url_for('index'); cursor = None
    if not conn: flash("Błąd połączenia.", "danger"); return redirect(redirect_url)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DishID, Name, Price FROM Dishes WHERE DishID = ?", (dish_id,))
        dish_data_dict = row_to_dict(cursor, cursor.fetchone())
        if not dish_data_dict: flash('Nie znaleziono dania.', 'danger'); return redirect(redirect_url) # Zamknięcie w finally
    except Exception as e: app.logger.error(f"Błąd pobierania D {dish_id}: {e}"); flash("Błąd pobierania dania.", "danger"); return redirect(redirect_url) # Zamknięcie w finally
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
    if dish_data_dict:
        if 'cart' not in session: session['cart'] = {}
        cart = session.get('cart', {}); dish_id_str = str(dish_id)
        try:
            price = float(dish_data_dict['Price']); current_quantity = cart.get(dish_id_str, {}).get('quantity', 0)
            cart[dish_id_str] = {'name': dish_data_dict['Name'], 'price': price, 'quantity': current_quantity + quantity}
            session['cart'] = cart; session.modified = True; flash(f"Dodano '{dish_data_dict['Name']}' (x{quantity}).", 'success')
        except (KeyError, ValueError) as e: app.logger.error(f"Błąd koszyka D {dish_id}: {e}"); flash("Błąd dodawania.", "danger")
    return redirect(redirect_url)

@app.route('/cart')
def view_cart(): # Bez zmian logiki sesji
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {}); items_display = []; total_price = 0.0; cart_changed = False
    if cart:
        for item_id in list(cart.keys()):
            item_data = cart[item_id]
            try: price = float(item_data['price']); quantity = int(item_data['quantity']); assert quantity > 0; item_total = price * quantity; items_display.append({'id': item_id, 'name': item_data.get('name', f'ID {item_id}'), 'price': price, 'quantity': quantity, 'total': item_total}); total_price += item_total
            except: app.logger.warning(f"Usuwanie złego el. koszyka ID {item_id}"); flash(f"Produkt ID {item_id} usunięty (błędne dane).", "warning"); del cart[item_id]; cart_changed = True
    if cart_changed: session['cart'] = cart; session.modified = True
    return render_template('cart.html', cart_items=items_display, total_price=total_price)

@app.route('/cart/remove/<dish_id>', methods=['POST'])
def remove_from_cart(dish_id): # Bez zmian logiki sesji
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {}); dish_id_str = str(dish_id)
    if dish_id_str in cart: item_name = cart[dish_id_str].get('name', f'ID {dish_id_str}'); del cart[dish_id_str]; session['cart'] = cart; session.modified = True; flash(f"Usunięto '{item_name}'.", 'info')
    else: flash('Tego produktu już nie ma w koszyku.', 'warning')
    return redirect(url_for('view_cart'))

@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
    cart = session.get('cart', {});
    if not cart: flash('Koszyk jest pusty.', 'warning'); return redirect(url_for('view_cart'))
    total_price = 0.0; order_items_data = []; is_cart_valid = True
    for item_id, item_data in cart.items():
        try: price = float(item_data['price']); quantity = int(item_data['quantity']); assert quantity > 0; total_price += price * quantity; order_items_data.append({'dish_id': int(item_id), 'quantity': quantity, 'price_per_item': price})
        except: app.logger.error(f"Błąd checkout ID {item_id}."); flash(f"Problem z ID {item_id}.", "danger"); is_cart_valid = False; break
    if not is_cart_valid: return redirect(url_for('view_cart'))
    conn = get_db_connection();
    if not conn: flash('Błąd połączenia.', 'danger'); return redirect(url_for('view_cart'))
    cursor = None; new_order_id = None
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Orders (UserID, TotalPrice, Status) OUTPUT INSERTED.OrderID VALUES (?, ?, ?)", (session['user_id'], total_price, 'Złożone'))
        new_order_id = cursor.fetchone()[0]
        app.logger.info(f"Zamówienie #{new_order_id} dla UserID: {session['user_id']}")
        insert_item_sql = "INSERT INTO OrderItems (OrderID, DishID, Quantity, PricePerItem) VALUES (?, ?, ?, ?)"
        items_to_insert = [(new_order_id, item['dish_id'], item['quantity'], item['price_per_item']) for item in order_items_data]
        cursor.executemany(insert_item_sql, items_to_insert)
        app.logger.info(f"Dodano {len(items_to_insert)} pozycji do zam. #{new_order_id}")
        conn.commit(); session.pop('cart', None); session.modified = True
        flash('Zamówienie złożone!', 'success'); return redirect(url_for('order_confirmation', order_id=new_order_id))
    except Exception as e: conn.rollback(); app.logger.error(f"KRYTYCZNY BŁĄD składania zam.: {e}"); flash('Błąd składania zamówienia.', 'danger'); return redirect(url_for('view_cart'))
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route('/order_confirmation/<int:order_id>')
def order_confirmation(order_id):
     if 'user_id' not in session: flash('Musisz być zalogowany.', 'warning'); return redirect(url_for('login'))
     return render_template('order_confirmation.html', order_id=order_id)

# --- Trasy Panelu Administratora ---

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard(): return render_template('admin/admin_dashboard.html')

@app.route('/admin/restaurants', methods=['GET', 'POST'])
@admin_required
def manage_restaurants():
    conn = get_db_connection()
    if not conn: flash('Błąd połączenia.', 'danger'); return redirect(url_for('admin_dashboard'))
    cursor = None
    try:
        cursor = conn.cursor()
        if request.method == 'POST':
            action = request.form.get('action'); form_submitted = False
            if action == 'add':
                form_submitted = True; name = request.form.get('name', '').strip()
                cuisine = request.form.get('cuisine', '').strip() or None; street = request.form.get('street', '').strip() or None
                street_number = request.form.get('street_number', '').strip() or None; postal_code = request.form.get('postal_code', '').strip() or None
                city = request.form.get('city', '').strip() or None; image_file = request.files.get('image'); image_filename = None; save_path = None
                if not name: flash('Nazwa wymagana.', 'warning')
                else:
                    if image_file and image_file.filename != '':
                        if allowed_file(image_file.filename):
                             original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower()
                             unique_filename = f"restaurant_{uuid.uuid4()}.{extension}"; save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                             try: os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True); image_file.save(save_path); image_filename = unique_filename; app.logger.info(f"Zapisano R: {save_path}")
                             except Exception as e: app.logger.error(f"Błąd zapisu pliku R: {e}"); flash('Błąd zapisu pliku.', 'danger')
                        else: flash('Zły typ pliku.', 'warning')
                    try:
                        sql = "INSERT INTO Restaurants (Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL) VALUES (?, ?, ?, ?, ?, ?, ?)"
                        cursor.execute(sql, (name, cuisine, street, street_number, postal_code, city, image_filename)); conn.commit(); flash(f'Dodano "{name}".', 'success')
                    except Exception as e:
                        conn.rollback(); app.logger.error(f"Błąd INSERT R: {e}"); flash('Błąd zapisu do DB.', 'danger')
                        # POPRAWIONA SKŁADNIA usuwania pliku
                        if image_filename and save_path and os.path.exists(save_path):
                            try:
                                os.remove(save_path)
                                app.logger.info(f"Usunięto plik {save_path} po błędzie DB.")
                            except Exception as rm_err:
                                app.logger.error(f"Nie udało się usunąć pliku {save_path} po błędzie DB: {rm_err}")

            elif action == 'delete':
                 form_submitted = True; restaurant_id_str = request.form.get('restaurant_id')
                 if not restaurant_id_str: flash('Brak ID.', 'warning')
                 else:
                     try:
                         restaurant_id = int(restaurant_id_str)
                         cursor.execute("SELECT ImageURL FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
                         result_row = cursor.fetchone() # Pobierz jako Row
                         image_to_delete_path = None
                         image_filename_to_flash = None # Zmienna do bezpiecznego flashowania
                         if result_row and result_row[0]: # Dostęp po indeksie
                              image_filename_to_flash = result_row[0]
                              image_to_delete_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename_to_flash)

                         cursor.execute("DELETE FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,)); conn.commit(); app.logger.info(f"Usunięto R ID: {restaurant_id}"); flash(f'Usunięto R ID: {restaurant_id}.', 'success')

                         # POPRAWIONA SKŁADNIA I OBSŁUGA BŁĘDU FLASH
                         if image_to_delete_path and os.path.exists(image_to_delete_path):
                            try:
                                os.remove(image_to_delete_path)
                                app.logger.info(f"Usunięto plik obrazka R: {image_to_delete_path}")
                            except Exception as e:
                                app.logger.warning(f"Nie udało się usunąć pliku R {image_to_delete_path}: {e}")
                                # Bezpieczne użycie nazwy pliku we flashu
                                flash(f'Nie udało się usunąć pliku obrazka: {image_filename_to_flash or "[nieznany]"}', 'warning')
                     except ValueError: flash('Złe ID.', 'warning')
                     except Exception as e: conn.rollback(); app.logger.error(f"Błąd DELETE R: {e}"); flash('Błąd usuwania.', 'danger')

            if form_submitted: conn.close(); return redirect(url_for('manage_restaurants'))

        # Metoda GET
        cursor.execute("SELECT RestaurantID, Name, CuisineType, Street, StreetNumber, PostalCode, City, ImageURL FROM Restaurants ORDER BY Name")
        restaurants = rows_to_dicts(cursor, cursor.fetchall()); restaurants_display = []
        for r in restaurants: r['FullAddress'] = format_address(r.get('Street'), r.get('StreetNumber'), r.get('PostalCode'), r.get('City')) or "-"; restaurants_display.append(r)
        return render_template('admin/manage_restaurants.html', restaurants=restaurants_display)
    except Exception as e: app.logger.error(f"Błąd manage_restaurants: {e}"); flash("Błąd.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn: conn.close()

@app.route('/admin/dishes', methods=['GET', 'POST'])
@app.route('/admin/dishes/<int:restaurant_id>', methods=['GET', 'POST'])
@admin_required
def manage_dishes(restaurant_id=None):
    conn = get_db_connection()
    if not conn: flash('Błąd połączenia.', 'danger'); return redirect(url_for('admin_dashboard'))
    restaurants_list = []; dishes_display = []; selected_restaurant_name = None; cursor = None; redirect_to_restaurant_id = restaurant_id
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RestaurantID, Name FROM Restaurants ORDER BY Name")
        # POPRAWKA: Konwersja listy restauracji dla selecta
        restaurants_rows = cursor.fetchall()
        restaurants_list = [(row.RestaurantID, row.Name) for row in restaurants_rows] # Konwersja na listę tupli

        if request.method == 'POST':
            action = request.form.get('action'); rest_id_form_str = request.form.get('restaurant_id'); form_submitted = False
            try: current_restaurant_id = int(rest_id_form_str) if rest_id_form_str else restaurant_id
            except ValueError: flash('Złe ID restauracji.', 'danger'); conn.close(); return redirect(url_for('manage_dishes'))
            if action == 'add':
                form_submitted = True
                if not current_restaurant_id: flash('Wybierz restaurację.', 'warning')
                else:
                    name = request.form.get('name', '').strip(); description = request.form.get('description', '').strip() or None
                    price_str = request.form.get('price'); image_file = request.files.get('image'); image_filename = None; price_decimal = None; save_path = None
                    if not name or not price_str: flash('Nazwa i cena wymagane.', 'warning')
                    else:
                        try: price_decimal = float(price_str); assert price_decimal >= 0
                        except: flash('Zła cena.', 'warning'); price_decimal = None
                    if name and price_decimal is not None:
                        if image_file and image_file.filename != '':
                            if allowed_file(image_file.filename):
                                original_filename = secure_filename(image_file.filename); extension = original_filename.rsplit('.', 1)[1].lower()
                                unique_filename = f"dish_{uuid.uuid4()}.{extension}"; save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                                try: os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True); image_file.save(save_path); image_filename = unique_filename
                                except Exception as e: flash('Błąd zapisu pliku.', 'danger'); app.logger.error(f"Błąd save file D: {e}")
                            else: flash('Zły typ pliku.', 'warning')
                        try:
                            sql = "INSERT INTO Dishes (RestaurantID, Name, Description, Price, ImageURL) VALUES (?, ?, ?, ?, ?)"
                            cursor.execute(sql, (current_restaurant_id, name, description, price_decimal, image_filename)); conn.commit(); flash(f'Dodano "{name}".', 'success')
                        except Exception as e:
                            conn.rollback(); app.logger.error(f"Błąd INSERT D: {e}"); flash('Błąd zapisu do DB.', 'danger')
                            # POPRAWIONA SKŁADNIA usuwania pliku
                            if image_filename and save_path and os.path.exists(save_path):
                                try:
                                    os.remove(save_path)
                                except Exception as rm_err:
                                    app.logger.warning(f"Nie udało się usunąć D {save_path}: {rm_err}")
            elif action == 'delete':
                 form_submitted = True; dish_id_str = request.form.get('dish_id')
                 if not dish_id_str or not current_restaurant_id: flash('Brak ID.', 'warning')
                 else:
                      try:
                          dish_id = int(dish_id_str)
                          cursor.execute("SELECT ImageURL FROM Dishes WHERE DishID = ?", (dish_id,))
                          result_row = cursor.fetchone(); image_to_delete_path = None; image_filename_to_flash = None # Zmienna do flasha
                          if result_row and result_row[0]:
                               image_filename_to_flash = result_row[0]
                               image_to_delete_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename_to_flash)
                          cursor.execute("DELETE FROM Dishes WHERE DishID = ?", (dish_id,)); conn.commit(); app.logger.info(f"Usunięto D ID: {dish_id}"); flash(f'Usunięto D ID: {dish_id}.', 'success')
                          # POPRAWIONA SKŁADNIA i obsługa błędu flash
                          if image_to_delete_path and os.path.exists(image_to_delete_path):
                              try:
                                  os.remove(image_to_delete_path)
                                  app.logger.info(f"Usunięto plik D: {image_to_delete_path}")
                              except Exception as e:
                                  app.logger.warning(f"Błąd usuwania pliku D {image_to_delete_path}: {e}")
                                  flash(f'Nie udało się usunąć pliku obrazka: {image_filename_to_flash or "[nieznany]"}', 'warning') # Bezpieczny flash
                      except ValueError: flash('Złe ID.', 'warning')
                      except Exception as e: conn.rollback(); app.logger.error(f"Błąd DELETE D: {e}"); flash('Błąd usuwania.', 'danger')
            if form_submitted:
                 conn.close()
                 redirect_to_restaurant_id = current_restaurant_id or restaurant_id
                 return redirect(url_for('manage_dishes', restaurant_id=redirect_to_restaurant_id) if redirect_to_restaurant_id else url_for('manage_dishes'))
        # Metoda GET
        if restaurant_id:
            cursor.execute("SELECT Name FROM Restaurants WHERE RestaurantID = ?", (restaurant_id,))
            rest_name_row = cursor.fetchone()
            if rest_name_row:
                selected_restaurant_name = rest_name_row[0]
                cursor.execute("SELECT DishID, Name, Description, Price, ImageURL FROM Dishes WHERE RestaurantID = ? ORDER BY Name", (restaurant_id,))
                dishes_display = rows_to_dicts(cursor, cursor.fetchall()) # Konwersja
            else: flash(f"Restauracja ID {restaurant_id} nie istnieje.", "warning"); conn.close(); return redirect(url_for('manage_dishes'))
        # POPRAWKA: Przekazuj poprawną listę restauracji (lista tupli)
        return render_template('admin/manage_dishes.html',
                               dishes=dishes_display, restaurants=restaurants_list,
                               selected_restaurant_id=restaurant_id, selected_restaurant_name=selected_restaurant_name)
    except Exception as e: app.logger.error(f"Błąd manage_dishes: {e}"); flash("Błąd.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn: conn.close()

@app.route('/admin/users')
@admin_required
def manage_users():
    conn = get_db_connection(); users_display = []; cursor = None
    if not conn: flash("Błąd połączenia.", "danger"); return render_template('admin/manage_users.html', users=users_display)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT UserID, Username, IsAdmin FROM Users ORDER BY Username")
        users_display = rows_to_dicts(cursor, cursor.fetchall()) # Konwersja
    except Exception as e: app.logger.error(f"Błąd pobierania U: {e}"); flash("Błąd pobierania U.", "danger")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
    return render_template('admin/manage_users.html', users=users_display)

@app.route('/admin/orders', methods=['GET', 'POST'])
@admin_required
def view_orders():
    conn = get_db_connection(); cursor = None
    if not conn: flash('Błąd połączenia.', 'danger'); return redirect(url_for('admin_dashboard'))
    try:
        cursor = conn.cursor()
        if request.method == 'POST':
            action = request.form.get('action'); order_id_str = request.form.get('order_id'); new_status = request.form.get('status')
            allowed_statuses = ['Złożone', 'W realizacji', 'Dostarczone', 'Anulowane']; form_submitted = False
            if action == 'update_status':
                form_submitted = True
                if order_id_str and new_status and new_status in allowed_statuses:
                    try:
                        order_id = int(order_id_str)
                        cursor.execute("UPDATE Orders SET Status = ? WHERE OrderID = ?", (new_status, order_id))
                        conn.commit(); app.logger.info(f"Zmiana statusu O #{order_id} na '{new_status}'."); flash('Status zaktualizowany.', 'success')
                    except ValueError: flash('Złe ID.', 'warning')
                    except Exception as e: conn.rollback(); app.logger.error(f"Błąd UPDATE O: {e}"); flash('Błąd aktualizacji.', 'danger')
                else: flash('Złe dane.', 'warning')
            if form_submitted: conn.close(); return redirect(url_for('view_orders'))
        # Metoda GET
        sql = "SELECT o.OrderID, u.Username, o.OrderDate, o.TotalPrice, o.Status FROM Orders o LEFT JOIN Users u ON o.UserID = u.UserID ORDER BY o.OrderDate DESC"
        cursor.execute(sql); orders = rows_to_dicts(cursor, cursor.fetchall()); orders_display = [] # Konwersja
        for o in orders: o['Username'] = o['Username'] or "[Usunięty]"; orders_display.append(o)
        return render_template('admin/view_orders.html', orders=orders_display)
    except Exception as e: app.logger.error(f"Błąd view_orders: {e}"); flash("Błąd.", "danger"); return redirect(url_for('admin_dashboard'))
    finally:
         if cursor: cursor.close()
         if conn: conn.close()

# --- Trasa do Serwowania Wgranych Plików ---
@app.route('/uploads/<path:filename>')
def uploaded_file(filename): # Bez zmian
    try: return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    except FileNotFoundError:
        app.logger.warning(f"Nie znaleziono pliku: {filename}")
        if filename.startswith('dish_'): placeholder = 'placeholder.png'
        elif filename.startswith('restaurant_'): placeholder = 'placeholder_restaurant.png'
        else: placeholder = 'placeholder.png'
        placeholder_path = os.path.join('static', placeholder)
        if os.path.exists(placeholder_path): return send_from_directory('static', placeholder)
        else: abort(404)

# --- Uruchomienie Aplikacji ---
if __name__ == '__main__':
    app.logger.info("Uruchamianie serwera deweloperskiego Flask...")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host='0.0.0.0', port=port) # debug=False jest zalecane