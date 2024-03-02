from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from requests.auth import HTTPBasicAuth
from flask_caching import Cache
import firebase_admin
from firebase_admin import credentials, messaging
from flask_socketio import SocketIO, emit
from datetime import datetime
import pytz
from collections import defaultdict

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

cred = credentials.Certificate("/root/qjump-api/my-first-project-5e08d-firebase-adminsdk-rpwps-b8ad93251d.json")
firebase_admin.initialize_app(cred)

WC_API_URL = 'https://qjump.online/wp-json/wc/v3/'
CONSUMER_KEY = 'your_consumer_key'
CONSUMER_SECRET = 'your_consumer_secret'


@app.route('/')
def home():
    return jsonify({'message': 'Flask WebSocket server running'})


def get_orders_by_status(status, store_name=None):
    all_orders = []
    page = 1
    while True:
        params = {
            'per_page': 100,
            'order': 'desc',
            'orderby': 'date',
            'status': status,
            'page': page
        }

        if store_name:
            params['store_name'] = store_name  # Directly using the store_name parameter

        response = requests.get(WC_API_URL + "orders", params=params,
                                auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                                verify=False)
        if response.ok:
            orders = response.json()
            if not orders:
                break
            all_orders.extend(orders)
            page += 1
        else:
            app.logger.error(f"Failed to fetch orders: {response.status_code} {response.text}")
            break  # Exit the loop on error

    return simplify_orders(all_orders)


def simplify_orders(orders):
    simplified_orders = []
    for order in orders:
        shop_name = order.get('store_name', 'Unknown Shop')  # Directly use 'store_name'
        products = []
        subtotal = 0

        for item in order.get('line_items', []):
            name = item.get('name', 'Unknown Product')
            quantity = item.get('quantity', 1)
            price_per_item = float(item.get('total', 0)) / quantity
            subtotal += price_per_item * quantity

            products.append({
                'name': name,
                'quantity': quantity,
                'price': price_per_item
            })

        charges = 1.0  # Assume some default charges; adjust if you have specific logic
        total = subtotal + charges
        payment_method = order.get('payment_method_title', 'Unknown Payment Method')

        date_created_utc = datetime.strptime(order.get('date_created'), '%Y-%m-%dT%H:%M:%S').replace(tzinfo=pytz.UTC)
        iso_date_created = date_created_utc.isoformat()

        simplified_orders.append({
            'ORDER_ID': order.get('id'),
            'USER_NAME': f"{order['billing']['first_name']} {order['billing']['last_name']}",
            'DATE': iso_date_created,
            'STATUS': order.get('status'),
            'SHOP': shop_name,
            'SUBTOTAL': subtotal,
            'CHARGES': charges,
            'TOTAL': total,
            'PRODUCTS': products,
            'PAYMENT_METHOD': payment_method
        })
    return simplified_orders


@app.route('/<store_name>/orders/processing', methods=['GET'])
@cache.cached(timeout=50, key_prefix=lambda: request.path)
def get_processing_orders(store_name=None):
    orders = get_orders_by_status('processing', store_name)
    return jsonify(orders)


@app.route('/<store_name>/orders/completed', methods=['GET'])
@cache.cached(timeout=50, key_prefix=lambda: request.path)
def get_completed_orders(store_name=None):
    orders = get_orders_by_status('completed', store_name)
    return jsonify(orders)


@app.route('/<store_name>/complete-order/<int:order_id>', methods=['POST'])
def complete_order(store_name, order_id):
    data_payload = {'status': 'completed'}
    response = requests.put(f"{WC_API_URL}orders/{order_id}", auth=HTTPBasicAuth(CONSUMER_KEY,
                            CONSUMER_SECRET), json=data_payload, verify=False)

    # Assuming you would want to delete cache specific to a store (if implemented)
    cache_key_processing = f'{store_name}_processing_orders'
    cache_key_completed = f'{store_name}_completed_orders'

    if response.ok:
        cache.delete(cache_key_processing)
        cache.delete(cache_key_completed)
        # Emitting a store-specific event might be useful if the frontend is also store-aware
        socketio.emit('order_completed', {'order_id': order_id, 'store_name': store_name}, broadcast=True)
        return jsonify({'success': f'Order {order_id} status updated to completed for {store_name}'})
    else:
        return jsonify({'error': 'Failed to complete order'}), response.status_code


@app.route('/<store_name>/top-customers', methods=['GET'])
def get_top_customers_for_store(store_name):
    # Fetch all completed orders for the specified store
    completed_orders = get_orders_by_status('completed', store_name)

    # Initialize a dictionary to accumulate total spend per customer for the store
    spend_per_customer = defaultdict(float)

    # Iterate through each completed order for the store
    for order in completed_orders:
        # Verify the store name matches
        if order['SHOP'].lower() == store_name.lower():
            customer_name = f"{order['USER_NAME']}"
            spend_per_customer[customer_name] += order['TOTAL']  # Accumulate total spend

    # Sort customers by total spend in descending order and select the top 5
    top_customers = sorted(spend_per_customer.items(), key=lambda x: x[1], reverse=True)[:5]

    # Format the result for JSON response
    result = [{'name': name, 'total_spent': total_spent} for name, total_spent in top_customers]

    return jsonify(result)


@app.route('/<store_name>/top-products', methods=['GET'])
def get_top_products_for_store(store_name):
    # Fetch all completed orders for the specified store
    completed_orders = get_orders_by_status('completed', store_name)

    # Initialize a dictionary to track product sales (name and total quantity sold)
    product_sales = defaultdict(lambda: {'quantity': 0, 'price': 0.0})

    # Iterate through each completed order for the store
    for order in completed_orders:
        if order['SHOP'].lower() == store_name.lower():
            for item in order['PRODUCTS']:
                product_name = item['name']
                quantity = item['quantity']
                price = item['price']
                # Accumulate total quantity sold for each product
                product_sales[product_name]['quantity'] += quantity
                product_sales[product_name]['price'] = price

    # Convert to a list, sort by quantity sold in descending order, and select the top products
    top_products = sorted(product_sales.items(), key=lambda x: x[1]['quantity'], reverse=True)[:5]

    # Format the result for JSON response
    result = [{
        'name': product,
        'price': details['price'],
        'quantity_sold': details['quantity']
    } for product, details in top_products]

    return jsonify(result)


@app.route('/<store_name>/total-sales', methods=['GET'])
def get_store_total_sales(store_name):
    # Fetch all completed orders for the specified store
    completed_orders = get_orders_by_status('completed', store_name)

    # Initialize total sales to 0
    total_sales = 0

    # Iterate through each completed order and accumulate total sales
    for order in completed_orders:
        if order['SHOP'].lower() == store_name.lower():
            total_sales += order['SUBTOTAL']

    # Return the total sales for the store
    return jsonify({'store': store_name, 'total_sales': total_sales})


@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    print("Webhook received:", data)

    # Assuming 'data' contains enough information to identify the restaurant
    restaurant_name = extract_restaurant_name(data)  # Implement this function

    # Fetch updated processing orders for this restaurant
    updated_orders = get_processing_orders_for_restaurant(restaurant_name)  # Implement this function

    # Emit a WebSocket message to update the Flutter app
    socketio.emit('update_processing_orders', {'restaurant': restaurant_name, 'orders': updated_orders}, broadcast=True)

    return jsonify({'status': 'success'}), 200


def extract_restaurant_name(webhook_data):
    # Logic to extract the restaurant name from the webhook data
    pass


def get_processing_orders_for_restaurant(restaurant_name):
    # Logic to get updated processing orders for the given restaurant
    pass

# Send notification (example implementation)


@app.route('/send-notification', methods=['POST'])
def send_notification():
    # Sample implementation, adjust according to your notification logic
    message = messaging.Message(
        notification=messaging.Notification(
            title="New Notification",
            body="You have a new notification."
        ),
        topic="all"
    )
    response = messaging.send(message)
    return jsonify({'success': True, 'messageId': response})

# SocketIO event handlers


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
    # with app.app_context():
    #     cache_orders_for_all_restaurants()
    # Thread(target=periodic_cache_refresh).start()
    socketio.run(app, debug=True, host='0.0.0.0', port=7000, keyfile='/root/ssl/key.pem', certfile='/root/ssl/cert.pem')
