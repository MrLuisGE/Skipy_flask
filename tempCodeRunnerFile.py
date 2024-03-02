from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from requests.auth import HTTPBasicAuth
from flask_caching import Cache
import firebase_admin
from firebase_admin import credentials, messaging
from flask_socketio import SocketIO, emit
from threading import Thread
import time

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes and origins
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure cache
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

# Initialize Firebase Admin
#cred = credentials.Certificate("C:\Users\W-Tech It Solutions\Downloads\my-first-project-5e08d-firebase-adminsdk-rpwps-b8ad93251d.json")
#firebase_admin.initialize_app(cred)

# WooCommerce API details
WC_API_URL = 'https://qjump.online/wp-json/wc/v3/'
CONSUMER_KEY = 'ck_6a3064219310542f1a242952033e34a189d095fa'
CONSUMER_SECRET = 'cs_3f509715d0138c9dd2ad1f6524c3fcda700a9d58'

# SKU prefix to shop name mapping
sku_prefix_name_mapping = {
    'snack-': 'Snack',
    'restaurantealvo-': 'Restaurante Alvo',
    'brasserie-': 'Brasserie',
    'pub-': 'Pub',
    'eventossociais-': 'Eventos Sociais',
    'pizzaria-': 'Pizzaria',
    'lionfoodmarket-': 'Lion Food Market',
    'eventoesportivo-': 'Evento Esportivo',
}


@app.route('/')
def home():
    return jsonify({'message': 'Flask WebSocket server running'})

@app.route('/orders/processing', methods=['GET'])
@cache.cached(timeout=50, key_prefix='processing_orders')
def get_processing_orders():
    return get_orders_by_status('processing')

@app.route('/orders/completed', methods=['GET'])
@cache.cached(timeout=50, key_prefix='completed_orders')
def get_completed_orders():
    return get_orders_by_status('completed')

def get_orders_by_status(status, restaurant_name=None):
    all_orders = []  # Initialize a list to hold all orders
    page = 1  # Start from the first page
    while True:
        params = {
            'per_page': 100,  # Adjust if necessary, but 100 is typically the max
            'order': 'desc',
            'orderby': 'date',
            'status': status,
            'page': page  # Specify the current page
        }
        response = requests.get(WC_API_URL + "orders", params=params, auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), verify=False)
        
        if response.ok:
            orders = response.json()
            if not orders:
                break  # Exit the loop if no orders are returned (end of pages)
            
            # Optionally filter orders by restaurant name if specified
            if restaurant_name:
                orders = filter_orders_by_restaurant(orders, restaurant_name)
            
            all_orders.extend(orders)  # Add the fetched orders to the all_orders list
            
            page += 1  # Increment the page number for the next iteration
        else:
            app.logger.error(f"Failed to fetch orders: {response.status_code} {response.text}")
            return jsonify({'error': 'Failed to fetch orders'}), response.status_code

    # Once all orders are fetched, simplify and return them
    simplified_orders = simplify_orders(all_orders)
    return jsonify(simplified_orders)

def periodic_cache_refresh():
    while True:
        cache_orders_for_all_restaurants()
        time.sleep(300)  # Refresh every 5 minutes

# Start the background thread
Thread(target=periodic_cache_refresh).start()

def filter_orders_by_restaurant(orders, restaurant_name):
    filtered_orders = []
    for order in orders:
        for item in order.get('line_items', []):
            sku = item.get('sku', '')
            for prefix, name in sku_prefix_name_mapping.items():
                if name == restaurant_name and sku.startswith(prefix):
                    filtered_orders.append(order)
                    break  # Break inner loop if a matching SKU is found
    return filtered_orders


def simplify_orders(orders):
    simplified_orders = []
    for order in orders:
        shop_name = 'Unknown Shop'
        products = []
        subtotal = 0  # Initialize subtotal

        for item in order.get('line_items', []):
            sku = item.get('sku', '')
            name = item.get('name', 'Unknown Product')
            quantity = item.get('quantity', 1)
            price_per_item = float(item.get('total', 0)) / quantity
            subtotal += price_per_item * quantity  # Update subtotal

            products.append({
                'name': name,
                'quantity': quantity,
                'price': price_per_item  # Unit price
            })
            for prefix, name in sku_prefix_name_mapping.items():
                if sku.startswith(prefix):
                    shop_name = name
                    break

        charges = 1.0  # Fixed charge
        total = subtotal + charges  # Calculate total including charges
        payment_method = order.get('payment_method_title', 'Unknown Payment Method')

        simplified_orders.append({
            'ORDER_ID': order.get('id'),
            'USER_NAME': f"{order['billing']['first_name']} {order['billing']['last_name']}",
            'DATE': order.get('date_created'),
            'STATUS': order.get('status'),
            'SHOP': shop_name,
            'SUBTOTAL': subtotal,
            'CHARGES': charges,
            'TOTAL': total,
            'PRODUCTS': products,
            'PAYMENT_METHOD': payment_method
        })
    return simplified_orders


@app.route('/orders/<status>/<restaurant_name>', methods=['GET'])
def get_orders_by_status_and_restaurant(status, restaurant_name):
    cache_key = f'{status}_orders_{restaurant_name}'
    cached_orders = cache.get(cache_key)
    if cached_orders is not None:
        return jsonify(cached_orders)
    
    orders = get_orders_by_status(status, restaurant_name)
    cache.set(cache_key, orders, timeout=300)  # Cache for 5 minutes
    return jsonify(orders)




@app.route('/complete-order/<int:order_id>', methods=['POST'])
def complete_order(order_id):
    data_payload = {'status': 'completed'}
    
    try:
        response = requests.put(f"{WC_API_URL}orders/{order_id}", auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), json=data_payload, verify=False)
        
        if response.ok:
            # Clear the cache for both processing and completed orders
            cache.delete('processing_orders')
            cache.delete('completed_orders')
            
            # Emit an update to all clients to refresh their orders data
            socketio.emit('event_name', {'data': 'message data'}, broadcast=True)
            
            return jsonify({'success': 'Order status updated to completed'})
        else:
            app.logger.error(f"Failed to complete order {order_id}: {response.status_code} {response.text}")
            return jsonify({'error': 'Failed to complete order'}), response.status_code
    except Exception as e:
        app.logger.error(f"Exception in completing order {order_id}: {e}")
        return jsonify({'error': 'Exception in completing order'}), 500

@app.route('/orders/<status>', methods=['GET'])
def get_orders_by_status_route(status):
    shop_name = request.args.get('shop', default=None, type=str)
    return get_orders_by_status_and_shop(status, shop_name)

def get_orders_by_status_and_shop(status, shop_name=None):
    params = {
        'per_page': 50,
        'order': 'desc',
        'orderby': 'date',
        'status': status
    }
    if shop_name:
        # Assuming there's a way to filter orders by shop name through your API
        params['shop'] = shop_name  # Adjust the parameter name as per your actual API

    response = requests.get(WC_API_URL + "orders", params=params, auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET))

    if response.ok:
        orders = response.json()
        return jsonify(orders)
    else:
        return jsonify({'error': 'Failed to fetch orders'}), response.status_code

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    print("Webhook received:", data)
    
    cache_orders_for_all_restaurants()  # Refresh cache on webhook
    return jsonify({'success': True})

@app.route('/send-notification', methods=['POST'])
def send_notification():
    # Sending notification logic
    return jsonify({'success': True, 'messageId': response})

socketio = SocketIO(app)

@socketio.on('connect', namespace='/ws')
def handle_connect():
    print('Client connected')
    emit('response', {'message': 'Connected'})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

def notify_new_order(order_data):
    socketio.emit('new_order', {'order': order_data}, broadcast=True)

def cache_orders_for_all_restaurants():
    for restaurant_name in sku_prefix_name_mapping.values():
        for status in ['processing', 'completed']:
            cache_orders_by_status_and_restaurant(status, restaurant_name)

def cache_orders_by_status_and_restaurant(status, restaurant_name):
    orders = get_orders_by_status(status, restaurant_name)
    cache_key = f'{status}_orders_{restaurant_name}'
    cache.set(cache_key, orders, timeout=300)  # Cache for 5 minutes

if __name__ == '__main__':
    with app.app_context():
        cache_orders_for_all_restaurants()
    # Other initialization code...
    Thread(target=periodic_cache_refresh).start()
    socketio.run(app, debug=True, host='0.0.0.0', port=7000, keyfile='/root/ssl/key.pem', certfile='/root/ssl/cert.pem')