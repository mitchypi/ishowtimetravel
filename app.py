from flask import Flask, render_template, session, redirect, url_for, request, jsonify
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from market_data import catalog as data_catalog, MarketDataCatalog
import calendar
import time

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

SHARE_STEP = Decimal('0.0001')
CASH_STEP = Decimal('0.01')


@app.template_filter('fmt_currency')
def fmt_currency(value):
    """Render a value as USD with thousands separators."""
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f"${dec:,.2f}"


@app.template_filter('fmt_signed_currency')
def fmt_signed_currency(value):
    """Render a value as signed USD with thousands separators."""
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value

    sign = '+' if dec >= 0 else '-'
    magnitude = abs(dec)
    return f"{sign}${magnitude:,.2f}"


@app.template_filter('fmt_shares')
def fmt_shares(value):
    """Render share quantities with commas and up to four decimals."""
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value

    dec = dec.quantize(SHARE_STEP, rounding=ROUND_DOWN)
    formatted = f"{dec:,.4f}".rstrip('0').rstrip('.')
    return formatted or '0'

class StockMarket:
    def __init__(self, catalog: MarketDataCatalog):
        self.cache = {}  # Cache stock data in memory
        self.ipo_dates = {}  # Cache IPO dates
        self.market_caps = {}  # Cache latest known market caps
        self.catalog = catalog

    def get_ipo_date(self, symbol):
        """Get the IPO date or earliest available date for a stock"""
        if symbol in self.ipo_dates:
            return self.ipo_dates[symbol]

        first_available = self.catalog.get_first_available_date(symbol)
        if first_available:
            self.ipo_dates[symbol] = first_available
            return first_available

        self.ipo_dates[symbol] = None
        return None

    def get_stock_info(self, symbol):
        """Get stock info including company name"""
        metadata = self.catalog.get_metadata(symbol)
        if metadata:
            return {'name': metadata.name, 'valid': True}
        return {'name': None, 'valid': False}

    def get_market_cap(self, symbol):
        """Fetch and cache the latest market cap for a stock."""
        if symbol in self.market_caps:
            return self.market_caps[symbol]

        market_cap = self.catalog.get_latest_market_cap(symbol)
        self.market_caps[symbol] = market_cap
        return market_cap

    def load_stock_data(self, symbol, start_date='2000-01-03', end_date='2025-10-31'):
        """Load historical stock data from local cache files"""
        if symbol in self.cache:
            return True

        frame = self.catalog.get_history(symbol)
        if frame is None or frame.empty:
            print(f"Local data not found for {symbol}")
            return False

        # Trim to requested window if provided
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d') if start_date else None
            end = datetime.strptime(end_date, '%Y-%m-%d') if end_date else None
        except ValueError:
            start = end = None

        if start or end:
            filtered = frame
            if start is not None:
                filtered = filtered[filtered.index >= start]
            if end is not None:
                filtered = filtered[filtered.index <= end]
            frame = filtered

        self.cache[symbol] = frame
        return True

    def get_price(self, symbol, date, time_of_day):
        """Get price for a specific date and time"""
        if symbol not in self.cache:
            return None

        hist = self.cache[symbol]
        date_obj = datetime.strptime(date, '%Y-%m-%d')

        # Check if date is before stock's IPO date (if we know it)
        ipo_date = self.get_ipo_date(symbol)
        if ipo_date:
            # Ensure both are timezone-naive for comparison
            if hasattr(ipo_date, 'tz_localize'):
                ipo_date = ipo_date.tz_localize(None) if ipo_date.tz else ipo_date
            elif hasattr(ipo_date, 'replace') and ipo_date.tzinfo:
                ipo_date = ipo_date.replace(tzinfo=None)

            if date_obj < ipo_date:
                print(f"DEBUG get_price: {symbol} requested on {date}, but IPO/first trade date is {ipo_date.strftime('%Y-%m-%d')}")
                return None

        # If no IPO date available, check if date is way before cache start
        # (more than 30 days before first cached date suggests stock didn't exist)
        if not ipo_date:
            first_cache_date = hist.index.min()
            if hasattr(first_cache_date, 'tz_localize'):
                first_cache_date = first_cache_date.tz_localize(None) if first_cache_date.tz else first_cache_date
            elif hasattr(first_cache_date, 'replace') and first_cache_date.tzinfo:
                first_cache_date = first_cache_date.replace(tzinfo=None)

            days_before_cache = (first_cache_date - date_obj).days
            if days_before_cache > 30:
                print(f"DEBUG get_price: {symbol} requested on {date}, which is {days_before_cache} days before cache start ({first_cache_date.strftime('%Y-%m-%d')})")
                return None

        # Find the closest trading day. Stocks look backward for the last session; crypto can move forward.
        max_attempts = 10
        attempts = 0
        search_forward = symbol in CRYPTOCURRENCIES
        while True:
            date_str = date_obj.strftime('%Y-%m-%d')
            day_data = hist[hist.index.strftime('%Y-%m-%d') == date_str]
            if not day_data.empty:
                break

            attempts += 1
            if attempts >= max_attempts:
                return None

            if search_forward:
                date_obj += timedelta(days=1)
            else:
                date_obj -= timedelta(days=1)

        return day_data['Open'].values[0] if time_of_day == 'open' else day_data['Close'].values[0]

    def get_price_change(self, symbol, date, time_of_day):
        """Get price change from previous close"""
        if symbol not in self.cache:
            return None, None

        current_price = self.get_price(symbol, date, time_of_day)
        if current_price is None or current_price == 0:
            return None, None

        hist = self.cache[symbol]
        date_obj = datetime.strptime(date, '%Y-%m-%d')

        # Find previous trading day
        prev_date = date_obj - timedelta(days=1)
        attempts = 0
        while attempts < 10:
            prev_date_str = prev_date.strftime('%Y-%m-%d')
            if prev_date_str in hist.index.strftime('%Y-%m-%d'):
                day_data = hist[hist.index.strftime('%Y-%m-%d') == prev_date_str]
                if not day_data.empty:
                    prev_price = day_data['Close'].values[0]
                    # Guard against zero or invalid previous prices
                    if prev_price is None or prev_price == 0:
                        return 0, 0
                    change = current_price - prev_price
                    percent_change = (change / prev_price) * 100
                    return change, percent_change
            prev_date -= timedelta(days=1)
            attempts += 1

        return 0, 0

    def get_history(self, symbol, start_date, end_date):
        """Get price history for graphing"""
        if symbol not in self.cache:
            return []

        hist = self.cache[symbol]

        # Ensure all datetime objects are timezone-naive for comparison
        if hist.index.tz is not None:
            hist_index = hist.index.tz_localize(None)
        else:
            hist_index = hist.index

        if hasattr(start_date, 'tz') and start_date.tz is not None:
            start_date = start_date.tz_localize(None)
        if hasattr(end_date, 'tz') and end_date.tz is not None:
            end_date = end_date.tz_localize(None)

        mask = (hist_index >= start_date) & (hist_index <= end_date)
        filtered = hist[mask]

        return [{
            'date': date.strftime('%Y-%m-%d'),
            'price': row['Close']
        } for date, row in filtered.iterrows()]

market = StockMarket(data_catalog)

# Preload cryptocurrency data on startup
def preload_crypto_data():
    """Preload Bitcoin and Ethereum data to avoid delays"""
    # Only preload if not already cached
    if 'BTC-USD' not in market.cache:
        print("\n" + "="*60)
        print("PRELOADING CRYPTOCURRENCY DATA")
        print("="*60)
        start_time = time.time()

        market.load_stock_data('BTC-USD')
        market.load_stock_data('ETH-USD')

        total_elapsed = time.time() - start_time
        print("="*60)
        print(f"✓ All cryptocurrency data loaded in {total_elapsed:.2f}s")
        print("="*60 + "\n")

# Cryptocurrency invention dates
BITCOIN_INVENTION_DATE = datetime(2009, 1, 3)
ETHEREUM_INVENTION_DATE = datetime(2015, 7, 30)

CRYPTOCURRENCIES = {
    'BTC-USD': {
        'name': 'Bitcoin',
        'invention_date': BITCOIN_INVENTION_DATE
    },
    'ETH-USD': {
        'name': 'Ethereum',
        'invention_date': ETHEREUM_INVENTION_DATE
    }
}

def is_crypto_available(symbol, current_date_str):
    """Check if a cryptocurrency is available for purchase on the given date"""
    if symbol not in CRYPTOCURRENCIES:
        return True  # Not a crypto, so no date restriction

    current_date = datetime.strptime(current_date_str, '%Y-%m-%d')
    return current_date >= CRYPTOCURRENCIES[symbol]['invention_date']

def get_available_cryptos(current_date_str):
    """Get list of cryptocurrencies available on the given date"""
    current_date = datetime.strptime(current_date_str, '%Y-%m-%d')
    available = []

    for symbol, info in CRYPTOCURRENCIES.items():
        if current_date >= info['invention_date']:
            available.append({
                'symbol': symbol,
                'name': info['name']
            })

    return available

def _aggregate_monthly_history(history):
    """Aggregate portfolio history entries with adaptive fidelity based on time range."""
    if not history:
        return []

    # First aggregate to monthly
    monthly = {}
    for entry in history:
        date_str = entry.get('date')
        value = entry.get('value')
        if not date_str or value is None:
            continue

        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            continue

        month_key = date_obj.strftime('%Y-%m')
        existing = monthly.get(month_key)
        if not existing or date_obj > existing['date']:
            monthly[month_key] = {
                'date': date_obj,
                'value': float(value)
            }

    sorted_months = sorted(monthly.values(), key=lambda item: item['date'])

    if not sorted_months:
        return []

    # Calculate time range in months
    first_date = sorted_months[0]['date']
    last_date = sorted_months[-1]['date']
    months_diff = (last_date.year - first_date.year) * 12 + (last_date.month - first_date.month)

    # Decide aggregation level based on time range
    if months_diff <= 24:  # < 2 years: keep monthly
        aggregation_months = 1
    elif months_diff <= 120:  # 2-10 years: use quarterly
        aggregation_months = 3
    else:  # > 10 years: use semi-annual
        aggregation_months = 6

    # If aggregation is monthly, return as-is
    if aggregation_months == 1:
        return [{
            'date': item['date'].strftime('%Y-%m-%d'),
            'value': item['value']
        } for item in sorted_months]

    # Otherwise, aggregate further
    aggregated = {}
    for item in sorted_months:
        date_obj = item['date']
        # Create a key based on year and aggregation period
        period = (date_obj.month - 1) // aggregation_months
        period_key = f"{date_obj.year}-{period}"

        existing = aggregated.get(period_key)
        if not existing or date_obj > existing['date']:
            aggregated[period_key] = {
                'date': date_obj,
                'value': item['value']
            }

    sorted_aggregated = sorted(aggregated.values(), key=lambda item: item['date'])
    return [{
        'date': item['date'].strftime('%Y-%m-%d'),
        'value': item['value']
    } for item in sorted_aggregated]

def _decimal_from_string(value):
    """Safely convert user input to Decimal or return None"""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None

def _quantize_shares(amount):
    return amount.quantize(SHARE_STEP, rounding=ROUND_DOWN)

def _quantize_cash(amount):
    return amount.quantize(CASH_STEP, rounding=ROUND_DOWN)

def _add_months(date_obj, months):
    """Return date advanced by a month delta, clamping to month end."""
    month_index = date_obj.month - 1 + months
    year = date_obj.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date_obj.day, calendar.monthrange(year, month)[1])
    return date_obj.replace(year=year, month=month, day=day)

    return filled


def _jump_forward(days=0, months=0, years=0):
    """Advance the simulation forward by fixed intervals."""
    if 'current_date' not in session:
        return redirect(url_for('index'))

    current = datetime.strptime(session['current_date'], '%Y-%m-%d')
    total_months = months + (years * 12)
    progressed = False

    if total_months > 0:
        for _ in range(total_months):
            current = _add_months(current, 1)
            session['current_date'] = current.strftime('%Y-%m-%d')
            session['time_of_day'] = 'open'
            _update_portfolio_value(session)
        progressed = True

    if days > 0:
        for _ in range(days):
            current += timedelta(days=1)
            session['current_date'] = current.strftime('%Y-%m-%d')
            session['time_of_day'] = 'open'
            _update_portfolio_value(session)
        progressed = True

    if not progressed:
        _update_portfolio_value(session)

    session.pop('jump_error', None)
    return redirect(url_for('index'))


def _parse_shares_from_form(form, price_decimal):
    """Determine share quantity from form inputs"""
    shares_value = _decimal_from_string(form.get('shares'))
    cash_value = _decimal_from_string(form.get('cash'))

    if shares_value is not None:
        shares = shares_value
    elif cash_value is not None and price_decimal > 0:
        shares = cash_value / price_decimal
    else:
        return None

    shares = _quantize_shares(shares)
    if shares <= 0:
        return None

    return shares

def _update_portfolio_value(session):
    """Update portfolio history with current value"""
    cash = session.get('cash', 0)
    holdings = session.get('holdings', {})
    date = session.get('current_date')
    time_of_day = session.get('time_of_day')

    total_value = cash
    for symbol, holding in holdings.items():
        if holding['shares'] > 0:
            if symbol not in market.cache:
                market.load_stock_data(symbol)
            price = market.get_price(symbol, date, time_of_day)
            if price is not None:  # Allow zero prices
                total_value += holding['shares'] * price

    portfolio_history = session.get('portfolio_history', [])
    portfolio_history.append({
        'date': date,
        'time': time_of_day,
        'value': total_value
    })
    session['portfolio_history'] = portfolio_history

@app.route('/')
def index():
    # Lazy load crypto data on first request
    preload_crypto_data()

    # Initialize session
    if 'current_date' not in session:
        session['current_date'] = '2000-01-03'
        session['cash'] = 10000.0
        session['time_of_day'] = 'open'
        session['holdings'] = {}  # {symbol: {'shares': 0, 'avg_cost': 0}}
        session['transactions'] = []  # Transaction history
        session['portfolio_history'] = [{'date': '2000-01-03', 'value': 10000.0}]  # Portfolio value over time
        session['pinned_stocks'] = []  # List of pinned stock symbols

    date = session['current_date']
    time = session['time_of_day']
    cash = session['cash']
    holdings = session.get('holdings', {})

    # Calculate portfolio
    portfolio = []
    total_position_value = 0

    for symbol, holding in holdings.items():
        shares = holding['shares']
        if shares <= 0:
            continue

        # Load stock data if not cached
        if symbol not in market.cache:
            market.load_stock_data(symbol)

        price = market.get_price(symbol, date, time)
        if price is None:
            continue

        change, percent_change = market.get_price_change(symbol, date, time)
        avg_cost = holding['avg_cost']
        position_value = shares * price  # Allow zero prices
        total_position_value += position_value

        total_gain_loss = 0
        gain_loss_percent = 0
        if avg_cost > 0:
            total_cost = avg_cost * shares
            total_gain_loss = position_value - total_cost
            gain_loss_percent = (total_gain_loss / total_cost) * 100

        portfolio.append({
            'symbol': symbol,
            'shares': shares,
            'avg_cost': avg_cost,
            'price': price,
            'change': change,
            'percent_change': percent_change,
            'position_value': position_value,
            'total_gain_loss': total_gain_loss,
            'gain_loss_percent': gain_loss_percent
        })

    portfolio_value = cash + total_position_value

    # Get transaction history (last 20)
    transactions = session.get('transactions', [])[-20:]
    transactions.reverse()  # Show most recent first

    # Get portfolio history
    portfolio_history = session.get('portfolio_history', [])
    monthly_history = _aggregate_monthly_history(portfolio_history)

    current_date_obj = datetime.strptime(date, '%Y-%m-%d')
    day_name = current_date_obj.strftime('%A')
    is_weekend = current_date_obj.weekday() >= 5
    current_month_key = current_date_obj.strftime('%Y-%m')
    if monthly_history:
        last_entry_date = datetime.strptime(monthly_history[-1]['date'], '%Y-%m-%d')
        last_month_key = last_entry_date.strftime('%Y-%m')
        if last_month_key == current_month_key:
            monthly_history[-1]['date'] = date
            monthly_history[-1]['value'] = portfolio_value
        else:
            last_value = monthly_history[-1]['value']
            gap_cursor = last_entry_date
            next_month = _add_months(gap_cursor, 1)
            while next_month.strftime('%Y-%m') != current_month_key:
                monthly_history.append({
                    'date': next_month.strftime('%Y-%m-%d'),
                    'value': last_value
                })
                gap_cursor = next_month
                next_month = _add_months(gap_cursor, 1)
            monthly_history.append({'date': date, 'value': portfolio_value})
    else:
        monthly_history = [{'date': date, 'value': portfolio_value}]

    monthly_history = sorted(monthly_history, key=lambda item: item['date'])

    # Get buy stock info if searching - update price for current date
    buy_stock = session.get('buy_stock', None)
    if buy_stock:
        symbol = buy_stock['symbol']
        # Update price for current date
        if symbol not in market.cache:
            market.load_stock_data(symbol)
        price = market.get_price(symbol, date, time)
        print(f"DEBUG: Checking price for {symbol} on {date} at {time}: price={price}")
        if price:
            change, percent_change = market.get_price_change(symbol, date, time)
            buy_stock['price'] = price
            buy_stock['change'] = change
            buy_stock['percent_change'] = percent_change
            buy_stock['price_available'] = True
            session['buy_stock'] = buy_stock
            print(f"DEBUG: Price available for {symbol}, set price_available=True")
        else:
            # No price data available for this date
            print(f"DEBUG: NO PRICE DATA for {symbol} on {date}, setting price_available=False")
            buy_stock['price_available'] = False
            buy_stock['price'] = None
            buy_stock['change'] = None
            buy_stock['percent_change'] = None
            session['buy_stock'] = buy_stock

    buy_error = session.get('buy_error', None)
    jump_error = session.get('jump_error', None)

    # Clear errors after displaying
    if buy_error:
        session.pop('buy_error', None)
    if jump_error:
        session.pop('jump_error', None)

    # Get pinned stocks with current prices
    pinned_stocks = []
    for symbol in session.get('pinned_stocks', []):
        if symbol not in market.cache:
            market.load_stock_data(symbol)
        price = market.get_price(symbol, date, time)
        stock_info = market.get_stock_info(symbol)
        print(f"DEBUG PINNED: {symbol} on {date} at {time}: price={price}")

        if price:
            change, percent_change = market.get_price_change(symbol, date, time)
            pinned_stocks.append({
                'symbol': symbol,
                'name': stock_info.get('name', symbol),
                'price': price,
                'change': change,
                'percent_change': percent_change,
                'price_available': True
            })
            print(f"DEBUG PINNED: {symbol} price_available=True")
        else:
            # No price data available for this date
            print(f"DEBUG PINNED: NO PRICE for {symbol}, price_available=False")
            pinned_stocks.append({
                'symbol': symbol,
                'name': stock_info.get('name', symbol),
                'price': None,
                'change': None,
                'percent_change': None,
                'price_available': False
            })

    # Get available cryptocurrencies
    available_cryptos = get_available_cryptos(date)

    # Get crypto prices if available (only if already cached to avoid slowdown)
    crypto_data = []
    for crypto in available_cryptos:
        symbol = crypto['symbol']

        # Only show crypto if it's already cached (preloaded)
        if symbol in market.cache:
            price = market.get_price(symbol, date, time)
            if price:
                change, percent_change = market.get_price_change(symbol, date, time)
                crypto_data.append({
                    'symbol': symbol,
                    'name': crypto['name'],
                    'price': price,
                    'change': change,
                    'percent_change': percent_change
                })

    crypto_symbols = list(CRYPTOCURRENCIES.keys())
    return render_template('portfolio.html',
                         date=date,
                         time_of_day=time,
                         cash=cash,
                         portfolio=portfolio,
                         portfolio_value=portfolio_value,
                         transactions=transactions,
                         portfolio_history=portfolio_history,
                         monthly_history=monthly_history,
                         buy_stock=buy_stock,
                         buy_error=buy_error,
                         jump_error=jump_error,
                         crypto_data=crypto_data,
                         holdings=holdings,
                         pinned_stocks=pinned_stocks,
                         day_name=day_name,
                         is_weekend=is_weekend,
                         crypto_symbols=crypto_symbols)

@app.route('/buy')
def buy_page():
    """Show buy page with search"""
    current_date = session.get('current_date', '2000-01-03')
    current_date_obj = datetime.strptime(current_date, '%Y-%m-%d')
    day_name = current_date_obj.strftime('%A')
    is_weekend = current_date_obj.weekday() >= 5
    return render_template('buy.html',
                         date=current_date,
                         time_of_day=session.get('time_of_day', 'open'),
                         cash=session.get('cash', 10000.0),
                         day_name=day_name,
                         is_weekend=is_weekend,
                         crypto_symbols=list(CRYPTOCURRENCIES.keys()))
@app.route('/buy/search', methods=['POST'])
def search_stock():
    """Search for a stock to buy"""
    symbol = request.form.get('symbol', '').upper()

    # Check if cryptocurrency and if it's available yet
    if symbol in CRYPTOCURRENCIES:
        if not is_crypto_available(symbol, session['current_date']):
            crypto_name = CRYPTOCURRENCIES[symbol]['name']
            invention_date = CRYPTOCURRENCIES[symbol]['invention_date'].strftime('%B %d, %Y')
            session['buy_error'] = f"{crypto_name} hasn't been invented yet! {crypto_name} was created on {invention_date}."
            return redirect(url_for('index'))

    # Get stock info first (fast check)
    stock_info = market.get_stock_info(symbol)

    if not stock_info['valid']:
        session['buy_error'] = f"Invalid stock symbol: {symbol}"
        return redirect(url_for('index'))

    if market.load_stock_data(symbol):
        # Get current price
        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price:
            change, percent_change = market.get_price_change(symbol, session['current_date'], session['time_of_day'])

            # Store in session for display on main page
            session['buy_stock'] = {
                'symbol': symbol,
                'name': stock_info['name'],
                'price': price,
                'change': change,
                'percent_change': percent_change
            }
            return redirect(url_for('index'))

    session['buy_error'] = f"Could not find stock data for: {symbol}"
    return redirect(url_for('index'))



@app.route('/api/tickers')
def api_tickers():
    """Return the curated universe of tradeable symbols for autocomplete."""
    symbols = []
    for metadata in market.catalog.list_symbols(include_crypto=True):
        symbols.append({
            'symbol': metadata.symbol,
            'name': metadata.name,
            'type': metadata.asset_type,
            'segment': metadata.segment,
        })
    return jsonify(symbols)

@app.route('/api/history')
def get_history():
    symbol = request.args.get('symbol')
    if not symbol:
        return jsonify([])

    current_date = session.get('current_date', '2000-01-03')
    # Get all data from start date (2000-01-03) to current date
    start_date = datetime(2000, 1, 1)
    end_date = datetime.strptime(current_date, '%Y-%m-%d')

    # Get full history and aggregate to monthly intervals
    history = market.get_history(symbol, start_date, end_date)

    # Aggregate to monthly data points (one per month)
    monthly_data = {}
    for point in history:
        date_obj = datetime.strptime(point['date'], '%Y-%m-%d')
        month_key = date_obj.strftime('%Y-%m')
        # Keep the latest data point for each month
        if month_key not in monthly_data or date_obj > datetime.strptime(monthly_data[month_key]['date'], '%Y-%m-%d'):
            monthly_data[month_key] = point

    # Sort by date
    monthly_history = sorted(monthly_data.values(), key=lambda x: x['date'])

    return jsonify(monthly_history)

@app.route('/buy/execute', methods=['POST'])
def execute_buy():
    try:
        symbol = request.form.get('symbol', '').upper()
        if not symbol:
            session['buy_error'] = "No symbol provided"
            return redirect(url_for('index'))

        if symbol not in market.cache:
            market.load_stock_data(symbol)

        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price is None:
            session['buy_error'] = f"No price data available for {symbol} on {session['current_date']}"
            return redirect(url_for('index'))

        current_date_obj = datetime.strptime(session['current_date'], '%Y-%m-%d')
        if current_date_obj.weekday() >= 5 and symbol not in CRYPTOCURRENCIES:
            session['buy_error'] = "Stock market is closed on weekends. Use Skip Weekend to trade stocks."
            return redirect(url_for('index'))

        price_decimal = Decimal(str(price))
        shares = _parse_shares_from_form(request.form, price_decimal)
        if shares is None:
            session['buy_error'] = "Please enter a valid share or cash amount"
            return redirect(url_for('index'))

        cost = _quantize_cash(shares * price_decimal)
        cash_balance = Decimal(str(session.get('cash', 0)))

        if cost <= 0:
            session['buy_error'] = "Cannot buy zero shares"
            return redirect(url_for('index'))

        if cash_balance < cost:
            session['buy_error'] = f"Insufficient funds. Need ${float(cost):.2f}, have ${session['cash']:.2f}"
            return redirect(url_for('index'))

        holdings = session.get('holdings', {})
        if symbol not in holdings:
            holdings[symbol] = {'shares': 0, 'avg_cost': 0}

        holding = holdings[symbol]
        current_shares = Decimal(str(holding.get('shares', 0)))
        current_avg_cost = Decimal(str(holding.get('avg_cost', 0)))
        new_shares = _quantize_shares(current_shares + shares)

        market_cap_value = market.get_market_cap(symbol)
        if market_cap_value is not None and market_cap_value > 0:
            cap_decimal = Decimal(str(market_cap_value))
            position_value = new_shares * price_decimal
            if position_value >= cap_decimal:
                session['buy_error'] = (
                    f"Order would control ${float(position_value):,.2f}, exceeding {symbol}'s market cap of ${float(cap_decimal):,.2f}. "
                    "Try a smaller trade."
                )
                return redirect(url_for('index'))

        if current_shares > 0 and new_shares > 0:
            total_cost = (current_shares * current_avg_cost) + cost
            holding['avg_cost'] = float(_quantize_cash(total_cost / new_shares))
        else:
            holding['avg_cost'] = float(price_decimal)

        holding['shares'] = float(new_shares)
        holdings[symbol] = holding
        session['holdings'] = holdings
        session['cash'] = float(_quantize_cash(cash_balance - cost))

        # Record transaction
        transactions = session.get('transactions', [])
        transactions.append({
            'date': session['current_date'],
            'time': session['time_of_day'],
            'type': 'BUY',
            'symbol': symbol,
            'shares': float(shares),
            'price': price,
            'total': float(cost)
        })
        session['transactions'] = transactions

        # Update portfolio history
        _update_portfolio_value(session)
    except Exception as e:
        session['buy_error'] = f"Error processing purchase: {str(e)}"

    return redirect(url_for('index'))

@app.route('/sell/<symbol>', methods=['POST'])
def sell(symbol):
    try:
        symbol = symbol.upper()
        holdings = session.get('holdings', {})

        if symbol not in holdings:
            session['buy_error'] = f"You don't own any {symbol}"
            return redirect(url_for('index'))

        if symbol not in market.cache:
            market.load_stock_data(symbol)

        current_date_obj = datetime.strptime(session['current_date'], '%Y-%m-%d')
        if current_date_obj.weekday() >= 5 and symbol not in CRYPTOCURRENCIES:
            session['buy_error'] = "Stock market is closed on weekends. Use Skip Weekend to trade stocks."
            return redirect(url_for('index'))

        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price is None:
            session['buy_error'] = f"No price data available for {symbol} on {session['current_date']}"
            return redirect(url_for('index'))

        holding = holdings[symbol]
        current_shares = holding['shares']

        # Check if selling by shares or cash - allow fractional shares
        price_decimal = Decimal(str(price))
        shares = _parse_shares_from_form(request.form, price_decimal)
        if shares is None:
            session['buy_error'] = "Please enter a valid share or cash amount"
            return redirect(url_for('index'))

        if shares <= 0:
            session['buy_error'] = "Share amount must be greater than zero"
            return redirect(url_for('index'))

        available_shares = Decimal(str(current_shares))
        if available_shares < shares:
            session['buy_error'] = f"Insufficient shares. You have {available_shares:.4f}, trying to sell {float(shares):.4f}"
            return redirect(url_for('index'))

        proceeds = _quantize_cash(shares * price_decimal)
        cash_balance = Decimal(str(session.get('cash', 0)))
        session['cash'] = float(_quantize_cash(cash_balance + proceeds))

        # Calculate profit/loss
        avg_cost = Decimal(str(holding['avg_cost']))
        cost_basis = _quantize_cash(shares * avg_cost)
        profit_loss = float(proceeds - cost_basis)

        remaining_shares = _quantize_shares(available_shares - shares)

        if remaining_shares <= 0:
            holding['avg_cost'] = 0
            holding['shares'] = 0
            del holdings[symbol]
        else:
            holding['shares'] = float(remaining_shares)
            holdings[symbol] = holding

        session['holdings'] = holdings

        # Record transaction
        transactions = session.get('transactions', [])
        transactions.append({
            'date': session['current_date'],
            'time': session['time_of_day'],
            'type': 'SELL',
            'symbol': symbol,
            'shares': float(shares),
            'price': price,
            'total': float(proceeds),
            'profit_loss': profit_loss
        })
        session['transactions'] = transactions

        # Update portfolio history
        _update_portfolio_value(session)
    except Exception as e:
        session['buy_error'] = f"Error processing sale: {str(e)}"

    return redirect(url_for('index'))

@app.route('/sell/<symbol>/all', methods=['POST'])
def sell_all(symbol):
    """Sell all shares of a stock"""
    try:
        symbol = symbol.upper()
        holdings = session.get('holdings', {})

        if symbol not in holdings:
            session['buy_error'] = f"You don't own any {symbol}"
            return redirect(url_for('index'))

        if symbol not in market.cache:
            market.load_stock_data(symbol)

        current_date_obj = datetime.strptime(session['current_date'], '%Y-%m-%d')
        if current_date_obj.weekday() >= 5 and symbol not in CRYPTOCURRENCIES:
            session['buy_error'] = "Stock market is closed on weekends. Use Skip Weekend to trade stocks."
            return redirect(url_for('index'))

        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price is None:
            session['buy_error'] = f"No price data available for {symbol} on {session['current_date']}"
            return redirect(url_for('index'))

        holding = holdings[symbol]
        amount = Decimal(str(holding['shares']))

        if amount <= 0:
            session['buy_error'] = f"You don't have any shares of {symbol} to sell"
            return redirect(url_for('index'))

        price_decimal = Decimal(str(price))
        proceeds = _quantize_cash(amount * price_decimal)

        # Calculate profit/loss
        avg_cost = Decimal(str(holding['avg_cost']))
        cost_basis = _quantize_cash(amount * avg_cost)
        profit_loss = float(proceeds - cost_basis)

        cash_balance = Decimal(str(session.get('cash', 0)))
        session['cash'] = float(_quantize_cash(cash_balance + proceeds))
        del holdings[symbol]
        session['holdings'] = holdings

        # Record transaction
        transactions = session.get('transactions', [])
        transactions.append({
            'date': session['current_date'],
            'time': session['time_of_day'],
            'type': 'SELL ALL',
            'symbol': symbol,
            'shares': float(amount),
            'price': price,
            'total': float(proceeds),
            'profit_loss': profit_loss
        })
        session['transactions'] = transactions

        # Update portfolio history
        _update_portfolio_value(session)
    except Exception as e:
        session['buy_error'] = f"Error processing sale: {str(e)}"

    return redirect(url_for('index'))

@app.route('/next')
def next_time():
    if session['time_of_day'] == 'open':
        session['time_of_day'] = 'close'
    else:
        # Move to next day
        current = datetime.strptime(session['current_date'], '%Y-%m-%d')
        next_day = current + timedelta(days=1)
        session['current_date'] = next_day.strftime('%Y-%m-%d')
        session['time_of_day'] = 'open'
    _update_portfolio_value(session)
    return redirect(url_for('index'))

@app.route('/jump/week')
def jump_week():
    return _jump_forward(days=7)


@app.route('/jump/month')
def jump_month():
    return _jump_forward(months=1)


@app.route('/jump/year')
def jump_year():
    return _jump_forward(years=1)


@app.route('/skip/weekend')
def skip_weekend():
    current_date_str = session.get('current_date')
    if not current_date_str:
        return redirect(url_for('index'))

    current = datetime.strptime(current_date_str, '%Y-%m-%d')
    if current.weekday() < 5:
        return redirect(url_for('index'))

    days_to_skip = 7 - current.weekday()
    return _jump_forward(days=days_to_skip)


@app.route('/jump', methods=['POST'])
def jump():
    try:
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        day = int(request.form.get('day'))

        # Validate and create date
        date_obj = datetime(year, month, day)
        new_date = date_obj.strftime('%Y-%m-%d')

        current_date_obj = datetime.strptime(session['current_date'], '%Y-%m-%d')

        # Only allow jumping forward
        if date_obj >= current_date_obj:
            # Fill in portfolio history for all intermediate months
            cursor = current_date_obj
            while cursor < date_obj:
                # Move forward by one month
                cursor = _add_months(cursor, 1)
                if cursor > date_obj:
                    break
                session['current_date'] = cursor.strftime('%Y-%m-%d')
                session['time_of_day'] = 'open'
                _update_portfolio_value(session)

            # Set final date
            session['current_date'] = new_date
            session['time_of_day'] = 'open'
            _update_portfolio_value(session)
        else:
            # Set error message for trying to go back in time
            session['jump_error'] = "You can't travel backwards in time! You can only jump forward."
    except:
        session['jump_error'] = "Invalid date. Please try again."

    return redirect(url_for('index'))

@app.route('/pin/<symbol>', methods=['POST'])
def pin_stock(symbol):
    # Pin a stock to watch list
    symbol = symbol.upper()
    pinned_stocks = session.get('pinned_stocks', [])

    if symbol not in pinned_stocks:
        pinned_stocks.append(symbol)
        session['pinned_stocks'] = pinned_stocks

    return redirect(url_for('index'))

@app.route('/unpin/<symbol>', methods=['POST'])
def unpin_stock(symbol):
    # Unpin a stock from watch list
    symbol = symbol.upper()
    pinned_stocks = session.get('pinned_stocks', [])

    if symbol in pinned_stocks:
        pinned_stocks.remove(symbol)
        session['pinned_stocks'] = pinned_stocks

    return redirect(url_for('index'))

@app.route('/reset')
def reset():
    # Clear session and start over
    session.clear()
    return redirect(url_for('index'))


if __name__ == '__main__':
    # Preload crypto data before starting the app
    preload_crypto_data()
    app.run(debug=True, port=5001)


