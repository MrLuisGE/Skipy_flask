from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from requests.auth import HTTPBasicAuth
from flask_caching import Cache
import firebase_admin
from firebase_admin import credentials, messaging
from flask_socketio import SocketIO, emit

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes and origins
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure cache
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

# Initialize Firebase Admin
cred = credentials.Certificate("/root/Qjump-API/my-first-project-5e08d-firebase-adminsdk-rpwps-b8ad93251d.json")
firebase_admin.initialize_app(cred)

# WooCommerce API details
WC_API_URL = 'https://qjump.online/wp-json/wc/v3/'
CONSUMER_KEY = 'ck_3f8a3dd06b936ae126b5ba3dc97b8f01e9402461'
CONSUMER_SECRET = 'cs_e7e7df6806379fc54f49d2a19c0015a256fbc5fc'

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

def get_orders_by_status(status):
    params = {
        'per_page': 50,
        'order': 'desc',
        'orderby': 'date',
        'status': status
    }
    response = requests.get(WC_API_URL + "orders", params=params, auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), verify=False)
    
    if response.ok:
        orders = response.json()
        simplified_orders = simplify_orders(orders)
        return jsonify(simplified_orders)
    else:
        app.logger.error(f"Failed to fetch orders: {response.status_code} {response.text}")
        return jsonify({'error': 'Failed to fetch orders'}), response.status_code

def simplify_orders(orders):
    simplified_orders = []
    for order in orders:
        shop_name = 'Unknown Shop'
        products = []
        for item in order.get('line_items', []):
            sku = item.get('sku', '')
            products.append({
                'name': item.get('name'),
                'quantity': item.get('quantity'),
                'total': item.get('total')
            })
            for prefix, name in sku_prefix_name_mapping.items():
                if sku.startswith(prefix):
                    shop_name = name
                    break

        payment_method = order.get('payment_method_title', 'Unknown Payment Method')

        simplified_orders.append({
            'ORDER_ID': order.get('id'),
            'USER_NAME': f"{order['billing']['first_name']} {order['billing']['last_name']}",
            'DATE': order.get('date_created'),
            'STATUS': order.get('status'),
            'SHOP': shop_name,
            'TOTAL': order.get('total'),
            'PRODUCTS': products,
            'PAYMENT_METHOD': payment_method
        })
    return simplified_orders

@app.route('/complete-order/<int:order_id>', methods=['POST'])
def complete_order(order_id):
    # Define the data payload to update the order status
    data_payload = {
        'status': 'completed'
    }
    
    # Make a PUT request to the WooCommerce API to update the order status
    response = requests.put(f"{WC_API_URL}orders/{order_id}", auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), json=data_payload, verify=False)
    
    if response.ok:
        # Invalidate cache and notify clients if needed
        cache.clear()
        socketio.emit('order_updated', {'order_id': order_id, 'status': 'completed'}, broadcast=True)
        return jsonify({'success': 'Order status updated to completed'})
    else:
        app.logger.error(f"Failed to complete order {order_id}: {response.status_code} {response.text}")
        return jsonify({'error': 'Failed to complete order'}), response.status_code

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    print("Webhook received:", data)
    
    if 'type' in data and data['type'] == 'order.created':
        # Invalidate cache and notify clients
        cache.clear()
        notify_new_order(data)
        return jsonify({'success': 'Order created webhook received and processed'})
    
    return jsonify({'message': 'Webhook received but not processed'})

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

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0')
