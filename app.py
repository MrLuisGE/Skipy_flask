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
cred = credentials.Certificate("C:/Users/W-Tech It Solutions/Downloads/my-first-project-5e08d-firebase-adminsdk-rpwps-b8ad93251d.json")
firebase_admin.initialize_app(cred)

# WooCommerce API details
WC_API_URL = 'https://qjump.local/wp-json/wc/v3/'
CONSUMER_KEY = 'ck_acf32580377d9aba65183c62db60c892d3fce299'
CONSUMER_SECRET = 'cs_fa87b25680d3259b672f89a394b6e9f3be081bf3'

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

@app.route('/latest-orders', methods=['GET'])
@cache.cached(timeout=50)
def get_latest_orders():
    params = {
        'per_page': 50,
        'order': 'desc',
        'orderby': 'date'
    }
    response = requests.get(WC_API_URL + "orders", params=params, auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), verify=False)
    if response.ok:
        orders = response.json()
        simplified_orders = []  # Initialize the list to hold simplified orders data

        # Process each order and add to simplified_orders
        for order in orders:
            shop_name = 'Unknown Shop'  # Default shop name
            # Attempt to determine shop name based on SKU prefix
            for item in order.get('line_items', []):
                sku = item.get('sku', '')
                for prefix, name in sku_prefix_name_mapping.items():
                    if sku.startswith(prefix):
                        shop_name = name
                        break  # Stop looking if we find a matching prefix

            # Append a simplified representation of the order to the list
            simplified_orders.append({
                'ORDER_ID': order.get('id'),
                'USER_NAME': "{} {}".format(order['billing']['first_name'], order['billing']['last_name']),
                'DATE': order.get('date_created'),
                'STATUS': order.get('status'),
                'SHOP': shop_name,
                'TOTAL': order.get('total')
            })

        return jsonify(simplified_orders)
    else:
        return jsonify({'error': 'Failed to fetch orders'}), 500


@app.route('/complete-order/<int:order_id>', methods=['POST'])
def complete_order(order_id):
    # Complete order logic
    return jsonify({'success': 'Order completed'})

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
