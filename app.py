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
CONSUMER_KEY = 'ck_b6d4bab7af943dd44fab3902735281511151df20'
CONSUMER_SECRET = 'cs_b70925c86fe66dcc3b24108104240ba829ee75b8'

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


def filter_orders_by_sku_prefix(orders, sku_prefix):
    filtered_orders = []
    for order in orders:
        for item in order.get('line_items', []):
            sku = item.get('sku', '')
            if sku.startswith(sku_prefix):
                filtered_orders.append(order)
                break  # Found a matching item, no need to check other items in this order
    return filtered_orders


def filter_orders_by_restaurant(orders, restaurant_name):
    filtered_orders = []
    sku_prefix = sku_prefix_name_mapping.get(restaurant_name, '')
    if not sku_prefix:
        return orders  # If no matching restaurant, return all orders

    for order in orders:
        for item in order.get('line_items', []):
            sku = item.get('sku', '')
            if sku.startswith(sku_prefix):
                filtered_orders.append(order)
                break  # Found a matching item, no need to check other items
    return filtered_orders


def get_orders_by_status(status, restaurant_name=None):
    all_orders = []
    page = 1
    while True:
        params = {
            'per_page': 100,  # Adjust as needed
            'order': 'desc',
            'orderby': 'date',
            'status': status,
            'page': page
        }

        # Add restaurant_name metadata to the params if it's provided
        # if restaurant_name:
        #     params['store_name'] = restaurant_name

        response = requests.get(WC_API_URL + "orders", params=params,
                                auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET),
                                verify=False)

        if response.ok:
            orders = response.json()

            # print('BANANA:')
            # print(orders)

            if not orders:
                break  # No more orders

            if restaurant_name:
                # Convert restaurant name to SKU prefix
                sku_prefix = next((k for k, v in sku_prefix_name_mapping.items()
                                  if v.lower() == restaurant_name.lower()), None)
                if sku_prefix:
                    orders = filter_orders_by_sku_prefix(orders, sku_prefix)

            all_orders.extend(orders)
            page += 1
        else:
            app.logger.error(f"Failed to fetch orders: {response.status_code} {response.text}")
            return []  # Return an empty list on error

    return simplify_orders(all_orders)


def simplify_orders(orders):
    simplified_orders = []
    for order in orders:
        shop_name = 'Unknown Shop'
        products = []
        subtotal = 0

        for item in order.get('line_items', []):
            sku = item.get('sku', '')
            name = item.get('name', 'Unknown Product')
            quantity = item.get('quantity', 1)
            price_per_item = float(item.get('total', 0)) / quantity
            subtotal += price_per_item * quantity

            products.append({
                'name': name,
                'quantity': quantity,
                'price': price_per_item
            })
            for prefix, name in sku_prefix_name_mapping.items():
                if sku.startswith(prefix):
                    shop_name = name
                    break

        charges = 1.0
        total = subtotal + charges
        payment_method = order.get('payment_method_title', 'Unknown Payment Method')

        # Convert to UTC and format as ISO 8601
        # TODO: corrigir mais tarde, apos mudar o horario do servidor do wordpress e vps
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


@app.route('/orders/processing', methods=['GET'])
@cache.cached(timeout=50, key_prefix='processing_orders')
def get_processing_orders():
    restaurant_name = request.args.get('restaurant', default=None, type=str)
    orders = get_orders_by_status('processing', restaurant_name)
    return jsonify(orders)


@app.route('/orders/completed', methods=['GET'])
@cache.cached(timeout=50, key_prefix='completed_orders')
def get_completed_orders():
    restaurant_name = request.args.get('restaurant', default=None, type=str)
    orders = get_orders_by_status('completed', restaurant_name)
    return jsonify(orders)


@app.route('/orders/<status>', defaults={'restaurant_name': None}, methods=['GET'])
@app.route('/orders/<status>/<restaurant_name>', methods=['GET'])
def get_orders_by_status_route(status, restaurant_name):
    return jsonify(get_orders_by_status(status, restaurant_name))


# Complete the order
@app.route('/complete-order/<int:order_id>', methods=['POST'])
def complete_order(order_id):
    data_payload = {'status': 'completed'}
    response = requests.put(f"{WC_API_URL}orders/{order_id}", auth=HTTPBasicAuth(CONSUMER_KEY,
                            CONSUMER_SECRET), json=data_payload, verify=False)
    if response.ok:
        cache.delete('processing_orders')
        cache.delete('completed_orders')
        socketio.emit('order_completed', {'order_id': order_id}, broadcast=True)
        return jsonify({'success': 'Order status updated to completed'})
    else:
        return jsonify({'error': 'Failed to complete order'}), response.status_code

# Get orders by status with an optional shop filter
# @app.route('/orders/<status>', methods=['GET'])
# def get_orders_by_status_route(status):
#    shop_name = request.args.get('shop', default=None, type=str)
#    return jsonify(get_orders_by_status_and_shop(status, shop_name))


@app.route('/orders/completed/<restaurant_name>/total-sales', methods=['GET'])
def get_restaurant_total_sales(restaurant_name):
    # Assuming you have a function to get completed orders by restaurant name
    completed_orders = get_orders_by_status('completed', restaurant_name)

    # Initialize total sales to 0
    total_sales = 0

    # Iterate through each completed order and accumulate total sales
    for order in completed_orders:
        # Check if the restaurant name matches (considering SKU mapping might not be needed here)
        if order['SHOP'].lower() == restaurant_name.lower():
            total_sales += order['SUBTOTAL']  # or 'TOTAL' based on your preference

    # Return the total sales for the restaurant
    return jsonify({'restaurant': restaurant_name, 'total_sales': total_sales})


@app.route('/orders/completed/<restaurant_name>/top-customers', methods=['GET'])
def get_top_customers_for_restaurant(restaurant_name):
    # Fetch all completed orders for the specified restaurant
    completed_orders = get_orders_by_status('completed', restaurant_name)

    # Initialize a dictionary to accumulate total spend per customer for the restaurant
    spend_per_customer = defaultdict(float)

    # Iterate through each completed order for the restaurant
    for order in completed_orders:
        # Assuming the 'SHOP' field matches the restaurant name and 'USER_NAME' uniquely identifies the customer
        if order['SHOP'].lower() == restaurant_name.lower():
            customer_name = f"{order['USER_NAME']}"  # Format or retrieve customer name as needed
            spend_per_customer[customer_name] += order['TOTAL']  # Accumulate total spend

    # Sort customers by total spend in descending order and select the top 5
    top_customers = sorted(spend_per_customer.items(), key=lambda x: x[1], reverse=True)[:5]

    # Format the result for JSON response
    result = [{'name': name, 'total_spent': total_spent} for name, total_spent in top_customers]

    return jsonify(result)


@app.route('/orders/completed/<restaurant_name>/top-products', methods=['GET'])
def get_top_products_for_restaurant(restaurant_name):
    # Fetch all completed orders for the specified restaurant
    completed_orders = get_orders_by_status('completed', restaurant_name)

    # Initialize a dictionary to track product sales (name and total quantity sold)
    product_sales = defaultdict(lambda: {'quantity': 0, 'price': 0.0})

    # Iterate through each completed order for the restaurant
    for order in completed_orders:
        if order['SHOP'].lower() == restaurant_name.lower():
            for item in order['PRODUCTS']:
                product_name = item['name']
                quantity = item['quantity']
                price = item['price']
                # Accumulate total quantity sold for each product and update price
                product_sales[product_name]['quantity'] += quantity
                product_sales[product_name]['price'] = price  # Assumes price remains constant

    # Convert to a list, sort by quantity sold in descending order, and select the top products
    top_products = sorted(product_sales.items(), key=lambda x: x[1]['quantity'], reverse=True)[:5]

    # Format the result for JSON response
    result = [{
        'name': product,
        'price': details['price'],
        'quantity_sold': details['quantity']
    } for product, details in top_products]

    return jsonify(result)

# Webhook endpoint


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


def cache_orders_for_all_restaurants():
    for restaurant_name in sku_prefix_name_mapping.values():
        for status in ['processing', 'completed']:
            cache_orders_by_status_and_restaurant(status, restaurant_name.lower())


def cache_orders_by_status_and_restaurant(status, restaurant_name):
    orders = get_orders_by_status(status, restaurant_name)
    cache_key = f'{status}_orders_{restaurant_name}'
    cache.set(cache_key, orders, timeout=300)
# Main execution


if __name__ == '__main__':
    # with app.app_context():
    #     cache_orders_for_all_restaurants()
    # Thread(target=periodic_cache_refresh).start()
    socketio.run(app, debug=True, host='0.0.0.0', port=7000, keyfile='/root/ssl/key.pem', certfile='/root/ssl/cert.pem')
