from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from requests.auth import HTTPBasicAuth
from flask_caching import Cache

app = Flask(__name__)
CORS(app)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

WC_API_URL = 'https://qjump.local/wp-json/wc/v3/orders'
WC_CONSUMER_KEY = 'ck_acf32580377d9aba65183c62db60c892d3fce299'
WC_CONSUMER_SECRET = 'cs_fa87b25680d3259b672f89a394b6e9f3be081bf3'

sku_prefix_name_mapping = {
    'snack-': 'Snack',
    'restaurantealvo-': 'Restaurante Alvo',
    'brasserie-': 'Brasserie',
    'pub-': 'Pub',
    'eventossociais-': 'Eventos Sociais',
    'pizzaria-': 'Pizzaria',
    'lionfoodmarket-': 'Lion Food Market',
    'eventoesportivo-': 'Evento Esportivo',
    # Add more SKU prefixes and names as needed
}

def make_cache_key(*args, **kwargs):
    """Dynamic cache key function."""
    path = request.path
    args_str = '&'.join([f"{key}={value}" for key, value in request.args.items()])
    return f"{path}?{args_str}"

@app.route('/latest-orders', methods=['GET'])
@cache.cached(timeout=50, key_prefix=make_cache_key)
def get_latest_orders():
    params = {
        'per_page': 50,
        'order': 'desc',
        'orderby': 'date'
    }
    response = requests.get(WC_API_URL, params=params, auth=HTTPBasicAuth(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
    if response.ok:
        orders = response.json()
        simplified_orders = []
        for order in orders:
            # Default shop name if no items or SKUs match
            shop_name = 'Unknown Shop'
            for item in order.get('line_items', []):
                sku = item.get('sku', '')
                # Find the shop name based on the SKU prefix
                for prefix, name in sku_prefix_name_mapping.items():
                    if sku.startswith(prefix):
                        shop_name = name
                        break  # Found the shop name, no need to continue checking
                if shop_name != 'Unknown Shop':
                    break  # Found the shop name for an item, no need to check other items
            
            simplified_orders.append({
                'ORDER_ID': order.get('id', 'N/A'),
                'USER_NAME': f"{order['billing'].get('first_name', '')} {order['billing'].get('last_name', '')}".strip(),
                'DATE': order.get('date_created_gmt', 'No date provided'),
                'STATUS': order.get('status', 'No status provided'),
                'SHOP': shop_name,
                'TOTAL': order.get('total', '0')
            })
        return jsonify(simplified_orders), 200
    else:
        return jsonify({'error': 'Failed to fetch orders'}), 500



@app.route('/complete-order/<int:order_id>', methods=['POST'])
def complete_order(order_id):
    data = {'status': 'completed'}
    update_response = requests.post(f'{WC_API_URL}/{order_id}', json=data, 
                                    auth=HTTPBasicAuth(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
    if update_response.ok:
        # Invalidate the cache explicitly
        cache.clear()  # This clears the entire cache, consider more targeted invalidation if needed
        return jsonify({'success': 'Order completed'}), 200
    else:
        return jsonify({'error': 'Failed to complete order', 'details': update_response.text}), update_response.status_code



if __name__ == '__main__':
    app.run(debug=True)
