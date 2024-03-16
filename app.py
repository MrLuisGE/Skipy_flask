import os
from datetime import datetime

import firebase_admin
import mysql.connector
import phpserialize
import pymysql.cursors
import requests
import stripe
from firebase_admin import credentials
from flask import Flask, jsonify, request, abort
from flask_caching import Cache
from flask_cors import CORS
from flask_socketio import SocketIO
from requests.auth import HTTPBasicAuth

app = Flask(__name__)

CORS(app, supports_credentials=True, resources={r"/*": {"origins": "*"}},
     allow_headers=["Authorization", "Content-Type", "X-API-Key"])

socketio = SocketIO(app, logger=True, engineio_logger=True, cors_allowed_origins="*")  # TODO estudar CORS

cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

cred = credentials.Certificate("/root/qjump-api/my-first-project-5e08d-firebase-adminsdk-rpwps-b8ad93251d.json")
firebase_admin.initialize_app(cred)

# TODO WORDPRESS KEY: iQlvL@~CCb,TW)RJ47Vk3RW:+L1)FFIb6)BWjma-Gg+Qe%qC>X
WC_API_URL = 'https://qjump.online/wp-json/wc/v3'
CONSUMER_KEY = 'ck_b6d4bab7af943dd44fab3902735281511151df20'
CONSUMER_SECRET = 'cs_b70925c86fe66dcc3b24108104240ba829ee75b8'
SEC_KEY = '27072001'
stripe.api_key = "sk_test_51OX5FkH6esboORTBVLyd5v5sA7lMgDMQstDExbujgMdHvQwAHDJvxH1zmlXs8CkxyhylDcGxoOZF3SRyD0hRA85M00Hb1PhBhX"
# Assuming your API key is stored in an environment variable or secure location
API_SEC_KEY = 'sk_test_51OX5FkH6esboORTBVLyd5v5sA7lMgDMQstDExbujgMdHvQwAHDJvxH1zmlXs8CkxyhylDcGxoOZF3SRyD0hRA85M00Hb1PhBhX'


@app.before_request
def require_api_key():
    open_endpoints = ['/', '/api']
    if request.method == 'OPTIONS' or request.path in open_endpoints:
        return  # Skip API key check for OPTIONS requests and open endpoints

    auth_header = request.headers.get('Authorization')
    token = auth_header.split(" ")[1] if auth_header and ' ' in auth_header else None

    if not token or token != SEC_KEY:
        abort(403, description='Access denied')


@app.route('/api')
def home():
    print(f"####### ENDPOINT CALLED: /api on {datetime.now()}")
    return jsonify({'message': f'server running on {datetime.now()}'})


@app.route('/api/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    print("####### Received webhook wordpress data:", data)

    # Emit the data to all connected clients
    socketio.emit('webhook_received', data)

    return jsonify({"status": "success"}), 200


def update_order_status(order_id, status):
    url = f"{WC_API_URL}/orders/{order_id}"
    data = {'status': status}
    response = requests.put(url, auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), json=data)

    if response.ok:
        print("####### Order status updated successfully.")
        return response.json()
    else:
        print(f"####### Failed to update order status: {response.text}")
        return None


def filter_orders_by_store(store_name, status):
    # Establish database connection
    connection = pymysql.connect(host='localhost',
                                 user='wordpress',
                                 password='admin123',
                                 database='wordpress',
                                 cursorclass=pymysql.cursors.DictCursor)

    filtered_orders = []
    try:
        with connection.cursor() as cursor:
            # Construct the query to fetch orders for a specific store and status
            query = "SELECT * FROM orders_table WHERE status = %s AND store_name = %s"  # TODO orders_table exist???
            params = [status, store_name]

            # Execute the query
            cursor.execute(query, params)
            orders = cursor.fetchall()

            # Simplify the order data structure (if necessary)
            for order in orders:
                simplified_order = simplify_order_structure(cursor, order)  # Define or adjust this function as needed
                filtered_orders.append(simplified_order)
    finally:
        connection.close()

    return filtered_orders


def get_orders_by_status(status, store_name=None, sort='ASC'):
    # Database connection
    connection = pymysql.connect(host='localhost', user='wordpress', password='admin123', database='wordpress',
                                 cursorclass=pymysql.cursors.DictCursor)
    all_orders = []
    try:
        with connection.cursor() as cursor:
            base_query = """
            SELECT p.ID as order_id, p.post_date as date_created, p.post_status as status,
                   max(case when pm.meta_key = '_billing_first_name' then pm.meta_value end) as billing_first_name,
                   max(case when pm.meta_key = '_billing_last_name' then pm.meta_value end) as billing_last_name,
                   max(case when pm.meta_key = '_billing_email' then pm.meta_value end) as billing_email,
                   max(case when pm.meta_key = '_billing_phone' then pm.meta_value end) as billing_phone,
                   max(case when pm.meta_key = '_order_total' then pm.meta_value end) as total,
                   max(case when pm.meta_key = 'store_name' then pm.meta_value end) as store_name,
                   max(case when pm.meta_key = '_payment_method_title' then pm.meta_value end) as payment_method_title
            FROM wp_posts p
            LEFT JOIN wp_postmeta pm ON p.ID = pm.post_id
            WHERE p.post_type = 'shop_order'
            """

            if status:
                base_query += " AND p.post_status = %s "
                params = [status]
            else:
                params = []

            if store_name:
                # base_query += " AND max(case when pm.meta_key = 'store_name' then pm.meta_value end) = %s"
                # base_query += " AND pm.meta_key = 'store_name' AND pm.meta_value = %s"
                base_query += (" AND pm.post_id IN "
                               "  (SELECT pm1.post_id FROM wp_postmeta pm1 "
                               "    WHERE pm1.meta_key = 'store_name' AND pm1.meta_value = %s ) ")
                params.append(store_name)

            base_query += " GROUP BY p.ID "
            base_query += f" ORDER BY p.ID {sort} "

            print("\n################# RUNNING QUERY: get_orders_by_status ################")
            print(f"####### CALLED: {datetime.now()}")
            print(f"####### STORE: {store_name}")
            print(f"####### STATUS: {status}")
            print(f"####### PARAMETERS:{params}")
            print(f"####### BASE QUERY:{base_query}")
            print("############################ END OF QUERY ############################\n")

            # Execute the query
            cursor.execute(base_query, params)
            orders = cursor.fetchall()

            # Simplify each order's structure
            for order in orders:
                simplified_order = simplify_order_structure(cursor, order)
                all_orders.append(simplified_order)

    finally:
        connection.close()

    return all_orders


def get_total_from_order(order):
    # Check if 'total' is None and substitute it with a default value (e.g., 0.0)
    total = order['total']
    if total is None:
        total_value = 0.0
    else:
        try:
            total_value = float(total)
        except ValueError:
            # Handle the case where 'total' is not convertible to float
            total_value = 0.0  # Or handle it in a way that makes sense for your application
    return total_value


def simplify_order_structure(cursor, order):
    # Simplify the order structure
    simplified_order = {
        'ORDER_ID': order['order_id'],
        'USER_NAME': f"{order['billing_first_name']} {order['billing_last_name']}",
        'EMAIL': order.get('billing_email', 'No email provided'),
        'PHONE': order.get('billing_phone', 'No phone provided'),
        'DATE': order['date_created'],
        'STATUS': order['status'],
        'SHOP': order['store_name'],
        'TOTAL': get_total_from_order(order),  # float(order['total']),
        'PAYMENT_METHOD': order.get('payment_method_title', 'No payment method provided'),
        'PRODUCTS': []  # Initialize an empty list for products
    }

    # Fetch and append product details
    # TODO nao fazer busca extra no futuro, tudo na query principal
    fetch_products_for_order(cursor, simplified_order['ORDER_ID'], simplified_order['PRODUCTS'])

    return simplified_order


# TODO englobar toda esta funcao/queries na query principal por questao de performance
def fetch_products_for_order(cursor, order_id, products_list):
    # Query to get all product IDs, quantities, and line totals (price) associated with the order
    product_query = """
    SELECT order_item_id, order_item_name
    FROM wp_woocommerce_order_items
    WHERE order_id = %s AND order_item_type = 'line_item'
    """
    cursor.execute(product_query, (order_id,))
    order_items = cursor.fetchall()

    # For each item, fetch associated product details including the product price
    for item in order_items:
        # Get product ID and quantity
        product_id_query = """
        SELECT meta_value
        FROM wp_woocommerce_order_itemmeta
        WHERE order_item_id = %s AND meta_key = '_product_id'
        """
        cursor.execute(product_id_query, (item['order_item_id'],))
        product_id_result = cursor.fetchone()
        product_id = product_id_result['meta_value'] if product_id_result else 'Unknown'

        quantity_query = """
        SELECT meta_value
        FROM wp_woocommerce_order_itemmeta
        WHERE order_item_id = %s AND meta_key = '_qty'
        """
        cursor.execute(quantity_query, (item['order_item_id'],))
        quantity_result = cursor.fetchone()
        quantity = quantity_result['meta_value'] if quantity_result else '0'

        # Get product price (line total)
        price_query = """
        SELECT meta_value
        FROM wp_woocommerce_order_itemmeta
        WHERE order_item_id = %s AND meta_key = '_line_total'
        """
        cursor.execute(price_query, (item['order_item_id'],))
        price_result = cursor.fetchone()
        price = price_result['meta_value'] if price_result else '0.00'

        # Construct the product object with id, name, quantity, and price, then append it to the list
        product = {
            'id': product_id,
            'name': item['order_item_name'],
            'quantity': quantity,
            'price': price
        }
        products_list.append(product)


@app.route('/api/orders', methods=['GET'])
@app.route('/api/orders/<store_name>', methods=['GET'])
def get_all_store_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name> on {datetime.now()}")
    sort_order = get_sort_asc_desc(request)
    all_orders = get_orders_by_status(None, store_name, sort_order)
    return jsonify(all_orders)


@app.route('/api/orders/open', methods=['GET'])
@app.route('/api/orders/<store_name>/open', methods=['GET'])
def get_store_open_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name>/open on {datetime.now()}")
    sort_order = get_sort_asc_desc(request)
    processing_orders = get_orders_by_status('wc-processing', store_name, sort_order)
    preparing_orders = get_orders_by_status('wc-preparing', store_name, sort_order)
    ready_orders = get_orders_by_status('wc-ready', store_name, sort_order)
    open_orders = processing_orders + preparing_orders + ready_orders
    return jsonify(open_orders)


@app.route('/api/orders/processing', methods=['GET'])
@app.route('/api/orders/<store_name>/processing', methods=['GET'])
def get_store_processing_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name>/processing on {datetime.now()}")
    sort_order = get_sort_asc_desc(request)
    processing_orders = get_orders_by_status('wc-processing', store_name, sort_order)
    return jsonify(processing_orders)


@app.route('/api/orders/preparing', methods=['GET'])
@app.route('/api/orders/<store_name>/preparing', methods=['GET'])
def get_store_preparing_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name>/preparing on {datetime.now()}")
    sort_order = get_sort_asc_desc(request)
    preparing_orders = get_orders_by_status('wc-preparing', store_name, sort_order)
    return jsonify(preparing_orders)


@app.route('/api/orders/ready', methods=['GET'])
@app.route('/api/orders/<store_name>/ready', methods=['GET'])
def get_store_ready_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name>/ready on {datetime.now()}")
    sort_order = get_sort_asc_desc(request)
    ready_orders = get_orders_by_status('wc-ready', store_name, sort_order)
    return jsonify(ready_orders)


@app.route('/api/orders/completed', methods=['GET'])
@app.route('/api/orders/<store_name>/completed', methods=['GET'])
def get_store_completed_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name>/completed on {datetime.now()}")
    sort_order = 'DESC'  # get_sort_asc_desc(request)
    completed_orders = get_orders_by_status('wc-completed', store_name, sort_order)
    return jsonify(completed_orders)


@app.route('/api/orders/refunded', methods=['GET'])
@app.route('/api/orders/<store_name>/refunded', methods=['GET'])
def get_store_refunded_orders(store_name=None):
    print(f"####### ENDPOINT CALLED: /api/orders/<store_name>/refunded on {datetime.now()}")
    sort_order = get_sort_asc_desc(request)
    refunded_orders = get_orders_by_status('wc-refunded', store_name, sort_order)
    return jsonify(refunded_orders)


# Change the order status to "preparing"
@app.route('/prepare-order/<int:order_id>', methods=['POST'])
def prepare_order(order_id):
    print(f"####### ENDPOINT CALLED: /api/prepare-order/<int:order_id> on {datetime.now()}")
    data_payload = {'status': 'preparing'}  # preparing is not a WordPress status, it is a custom status
    response = requests.put(f"{WC_API_URL}/orders/{order_id}",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                            json=data_payload, verify=False)

    if response.ok:
        # Your logic for when order status change is successful
        cache.delete('processing_orders')  # Update cache accordingly
        socketio.emit('order_preparing', {'order_id': order_id}, broadcast=True)
        return jsonify({'success': 'Order status updated to preparing'})
    else:
        # Your logic for when order status change fails
        return jsonify({'error': 'Failed to change order status to preparing'}), response.status_code


@app.route('/mark-ready/<int:order_id>', methods=['POST'])
def mark_order_as_ready(order_id):
    print(f"####### ENDPOINT CALLED: /api/mark-ready/{order_id} on {datetime.now()}")

    data_payload = {'status': 'ready'}  # preparing is not a WordPress status, it is a custom status
    response = requests.put(f"{WC_API_URL}/orders/{order_id}",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                            json=data_payload,
                            verify=False)  # TODO Proper SSL verification is recommended in production

    if response.ok:
        socketio.emit('order_ready', {'order_id': order_id}, broadcast=True)
        return jsonify({'success': 'Order status updated to ready'}), 200
    else:
        app.logger.error(f"Failed to mark order {order_id} as ready: {response.text}")
        return jsonify(
            {'error': 'Failed to update order status to ready', 'details': response.text}), response.status_code


# Complete the order
@app.route('/complete-order/<int:order_id>', methods=['POST', 'OPTIONS'])
def complete_order(order_id):
    if request.method == 'OPTIONS':
        # Respond to the preflight request with an appropriate CORS header
        return jsonify({'message': 'Success'}), 200

    if request.method == 'POST':
        print(f"####### ENDPOINT CALLED: /api/complete-order/{order_id} on {datetime.now()}")
        data_payload = {'status': 'completed'}
        response = requests.put(f"{WC_API_URL}/orders/{order_id}",
                                auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                                json=data_payload, verify=False)
        if response.ok:
            # Check if specific cache keys exist before deleting using cache.get
            if cache.get('processing_orders') is not None:
                cache.delete('processing_orders')
            if cache.get('completed_orders') is not None:
                cache.delete('completed_orders')

            # Use socketio.emit to broadcast messages
            socketio.emit('order_completed', {'order_id': order_id}, broadcast=True)
            return jsonify({'success': 'Order status updated to completed'})
        else:
            return jsonify({'error': 'Failed to complete order', 'details': response.text}), response.status_code


# Database configuration - update these values based on your database
db_config = {
    'user': 'wordpress',
    'password': 'admin123',
    'host': 'localhost',
    'database': 'wordpress',
}


def authenticate(inbound_request):
    """
    Authenticate the incoming request by comparing the provided bearer token
    with your API secret key.
    """
    auth_header = inbound_request.headers.get('Authorization', '')
    bearer_token = auth_header.split(' ')[1] if ' ' in auth_header else ''

    # Check API key in the X-API-Key header
    api_key = inbound_request.headers.get('X-API-Key', '')

    # Validate both the Bearer token and the API key
    return bearer_token == SEC_KEY and api_key == API_SEC_KEY


def get_order_stripe_charge_id(order_id):
    """
    Retrieve the Stripe charge ID for a given WooCommerce order ID.
    """
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)

    try:
        # WooCommerce stores order meta in the wp_postmeta table. Adjust meta_key as needed.
        cursor.execute("""
            SELECT meta_value AS stripe_charge_id
            FROM wp_postmeta 
            WHERE post_id = %s AND meta_key = '_transaction_id'
        """, (order_id,))
        result = cursor.fetchone()
        return result['stripe_charge_id'] if result else None
    finally:
        cursor.close()
        connection.close()


def update_order_status(order_id, status):
    data_payload = {'status': 'refunded' if status == 'refunded' else status}
    response = requests.put(f"{WC_API_URL}/orders/{order_id}",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                            json=data_payload,
                            verify=False)
    if response.ok:
        return True
    else:
        print(f"Failed to update order status in WooCommerce: {response.status_code}, {response.text}")
        return False


@app.route('/refund-order/<int:order_id>', methods=['POST'])
def refund_order(order_id):
    print(f"####### ENDPOINT CALLED: /api/refund-order/<int:order_id> on {datetime.now()}")
    if not authenticate(request):
        return jsonify({'error': 'Authentication failed'}), 403

    stripe_charge_id = get_order_stripe_charge_id(order_id)
    if not stripe_charge_id:
        return jsonify({'error': 'Order not found or Stripe charge ID missing'}), 404

    try:
        refund = stripe.Refund.create(charge=stripe_charge_id)
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e)}), 500

    # Update the order status in WordPress to 'refunded'
    if update_order_status(order_id, 'refunded'):
        return jsonify({'success': 'Order refunded successfully', 'refund_id': refund.id})
    else:
        return jsonify({'error': 'Failed to update order status in WordPress'}), 500


@app.route('/api/user-shop-association', methods=['GET'])
def user_shop_association():
    print(f"####### ENDPOINT CALLED: /api/user-shop-association on {datetime.now()}")
    user_id = request.args.get('id')
    if not user_id:
        return jsonify({'error': 'User ID is required'}), 400

    # Assuming you have a proper MySQL connection
    connection = pymysql.connect(host='localhost',
                                 user='wordpress',
                                 password='admin123',
                                 database='wordpress',
                                 cursorclass=pymysql.cursors.DictCursor)

    shop_name = "Not Assigned"
    try:
        with connection.cursor() as cursor:
            # Fetch the shop association from the user's metadata
            sql = "SELECT meta_value FROM wp_usermeta WHERE user_id = %s AND meta_key = 'shop_association'"
            cursor.execute(sql, (user_id,))
            result = cursor.fetchone()
            if result:
                shop_name = result['meta_value']
    finally:
        connection.close()

    return jsonify({'shop_name': shop_name})


@app.route('/api/user-data', methods=['GET'])
def get_user_data():
    print(f"####### ENDPOINT CALLED: /api/user-data on {datetime.now()}")
    user_id = request.args.get('username')  # User ID is optional; if not provided, fetch data for all users.
    users_data = fetch_wordpress_users(user_id)
    return jsonify(users_data)


def fetch_wordpress_users(username=None):
    connection = pymysql.connect(host='localhost',
                                 user='wordpress',
                                 password='admin123',
                                 database='wordpress',
                                 cursorclass=pymysql.cursors.DictCursor)

    users = []
    try:
        with connection.cursor() as cursor:
            if username:
                # Fetch user details for a specific username.
                sql = """
                    SELECT u.ID, u.user_login, u.user_email, m.meta_value as capabilities 
                    FROM wp_users u 
                    LEFT JOIN wp_usermeta m ON u.ID = m.user_id AND m.meta_key = 'wp_capabilities'
                    WHERE u.user_login = %s
                """
                cursor.execute(sql, (username,))
            else:
                # Fetch details for all users if no username is specified.
                sql = """
                    SELECT u.ID, u.user_login, u.user_email, m.meta_value as capabilities 
                    FROM wp_users u 
                    LEFT JOIN wp_usermeta m ON u.ID = m.user_id AND m.meta_key = 'wp_capabilities'
                """
                cursor.execute(sql)

            users_basic_info = cursor.fetchall()

            for user in users_basic_info:
                user_id = user['ID']
                roles = []

                if user['capabilities']:
                    try:
                        capabilities = phpserialize.loads(user['capabilities'].encode(), decode_strings=True)
                        roles = [role for role, granted in capabilities.items() if granted]
                    except Exception as e:
                        print(f"Error deserializing capabilities for user {user_id}: {e}")
                        roles = ['No Role Assigned']

                cursor.execute(
                    "SELECT meta_value FROM wp_usermeta WHERE user_id = %s AND meta_key = 'shop_association'",
                    (user_id,))
                result = cursor.fetchone()
                shop_name = result['meta_value'] if result else 'No Shop Assigned'

                users.append({
                    'id': user_id,
                    'username': user['user_login'],
                    'email': user['user_email'],
                    'role': roles[0] if roles else 'No Role Assigned',
                    'shop': shop_name
                })

    finally:
        connection.close()

    return users


@app.route('/api/products', methods=['GET'])
def get_all_product_details():
    print(f"####### ENDPOINT CALLED: /api/products on {datetime.now()}")
    response = requests.get(f"{WC_API_URL}/products",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), verify=False)

    if response.ok:
        return jsonify(response.json())
    else:
        return jsonify({'error': 'Failed to fetch product details'}), response.status_code


@app.route('/api/product/<int:product_id>', methods=['GET'])
def get_product_details(product_id):
    print(f"####### ENDPOINT CALLED: /api/product/<int:product_id> on {datetime.now()}")
    response = requests.get(f"{WC_API_URL}/products/{product_id}",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                            verify=False)
    if response.ok:
        return jsonify(response.json())
    else:
        return jsonify({'error': 'Failed to fetch product details'}), response.status_code


@app.route('/api/product/<int:product_id>/details', methods=['GET'])
def get_product_image_price(product_id):
    print(f"####### ENDPOINT CALLED: /api/product/<int:product_id>/details on {datetime.now()}")
    response = requests.get(f"{WC_API_URL}/products/{product_id}",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), verify=False)
    if response.ok:
        product_data = response.json()
        product_details = {
            'image': product_data['images'][0]['src'] if product_data['images'] else None,
            'price': product_data['price']
        }
        return jsonify(product_details)
    else:
        return jsonify({'error': 'Failed to fetch product details'}), response.status_code


@app.route('/api/product/<int:product_id>/update-price', methods=['POST'])
def update_product_price(product_id):
    print(f"####### ENDPOINT CALLED: /api/product/<int:product_id>/update-price on {datetime.now()}")
    new_price = request.json.get('price')
    if not new_price:
        return jsonify({'error': 'New price is required'}), 400

    data_payload = {'regular_price': str(new_price)}
    response = requests.put(f"{WC_API_URL}/products/{product_id}",
                            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                            json=data_payload, verify=False)

    if response.ok:
        return jsonify({'success': 'Product price updated'})
    else:
        return jsonify({'error': 'Failed to update product price'}), response.status_code


@app.route('/api/product/<int:product_id>/update-image', methods=['POST'])
def update_product_image(product_id):
    print(f"####### ENDPOINT CALLED: /api/product/<int:product_id>/update-image on {datetime.now()}")
    # Check if the post request has the file part
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400
    file = request.files['file']

    # If user does not select file, browser also submits an empty part without filename
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Save the file temporarily on the server
    filename = file.filename
    filepath = os.path.join('/tmp', filename)
    file.save(filepath)

    # Upload the image to WordPress/WooCommerce
    with open(filepath, 'rb') as img:
        media_response = requests.post(f"{WC_API_URL}/media",
                                       auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                                       files={'file': img},
                                       headers={'Content-Disposition': f'attachment; filename={filename}'})

    if not media_response.ok:
        return jsonify({'error': 'Failed to upload image to WordPress'}), media_response.status_code

    # Get the ID of the uploaded image
    media_response_data = media_response.json()
    image_id = media_response_data['id']

    # Now update the product with the new image ID
    update_response = requests.put(f"{WC_API_URL}/products/{product_id}",
                                   auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                                   json={'images': [{'id': image_id}]},
                                   verify=False)

    if not update_response.ok:
        return jsonify({'error': 'Failed to update product image'}), update_response.status_code

    return jsonify({'success': 'Product image updated'})


def get_sort_asc_desc(my_request):
    # Get the sort parameter from the query string, default to 'asc' if not provided
    sort_param = my_request.args.get('sort', 'ASC').upper()

    # Validate the sort_order value
    if sort_param not in ['ASC', 'DESC']:
        return jsonify({'error': f'Invalid sort parameter [{sort_param}]'}), 400

    return sort_param


# ===================== SocketIO event handlers =====================

@socketio.on('connect', namespace='/ws')
def connect():
    print("A client connected")


@socketio.on('disconnect', namespace='/ws')
def disconnect():
    print('Client disconnected')


if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=7000, keyfile='/root/ssl/key.pem', certfile='/root/ssl/cert.pem')
