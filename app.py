from flask import Flask, render_template, session, redirect, url_for, request, jsonify
from datetime import datetime, timedelta
import yfinance as yf
import pickle
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

# Directory for storing stock data
CACHE_DIR = 'stock_cache'
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

class StockMarket:
    def __init__(self):
        self.cache = {}  # Cache stock data in memory

    def _get_cache_file(self, symbol):
        """Get the cache file path for a symbol"""
        return os.path.join(CACHE_DIR, f"{symbol}.pkl")

    def get_stock_info(self, symbol):
        """Get stock info including company name"""
        try:
            stock = yf.Ticker(symbol)
            info = stock.info
            return {
                'name': info.get('longName', info.get('shortName', symbol)),
                'valid': True
            }
        except:
            return {'name': None, 'valid': False}

    def load_stock_data(self, symbol, start_date='2000-01-01', end_date='2025-10-31'):
        """Load historical stock data from cache or Yahoo Finance"""
        if symbol in self.cache:
            return True

        # Try to load from disk cache first
        cache_file = self._get_cache_file(symbol)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    self.cache[symbol] = pickle.load(f)
                return True
            except:
                pass

        # Download from Yahoo Finance if not cached
        try:
            stock = yf.Ticker(symbol)
            hist = stock.history(start=start_date, end=end_date)

            if hist.empty:
                return False

            self.cache[symbol] = hist

            # Save to disk cache
            try:
                with open(cache_file, 'wb') as f:
                    pickle.dump(hist, f)
            except:
                pass

            return True
        except:
            return False

    def get_price(self, symbol, date, time_of_day):
        """Get price for a specific date and time"""
        if symbol not in self.cache:
            return None

        hist = self.cache[symbol]
        date_obj = datetime.strptime(date, '%Y-%m-%d')

        # Find the closest trading day
        while date_obj.strftime('%Y-%m-%d') not in hist.index.strftime('%Y-%m-%d'):
            date_obj += timedelta(days=1)
            if date_obj > datetime.now():
                return None

        date_str = date_obj.strftime('%Y-%m-%d')
        day_data = hist[hist.index.strftime('%Y-%m-%d') == date_str]

        if day_data.empty:
            return None

        return day_data['Open'].values[0] if time_of_day == 'open' else day_data['Close'].values[0]

    def get_price_change(self, symbol, date, time_of_day):
        """Get price change from previous close"""
        if symbol not in self.cache:
            return None, None

        current_price = self.get_price(symbol, date, time_of_day)
        if current_price is None:
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
        mask = (hist.index >= start_date) & (hist.index <= end_date)
        filtered = hist[mask]

        return [{
            'date': date.strftime('%Y-%m-%d'),
            'price': row['Close']
        } for date, row in filtered.iterrows()]

market = StockMarket()

def _update_portfolio_value(session):
    """Update portfolio history with current value"""
    cash = session.get('cash', 0)
    holdings = session.get('holdings', {})
    date = session.get('current_date')
    time_of_day = session.get('time_of_day')

    total_value = cash
    for symbol, holding in holdings.items():
        if holding['shares'] > 0:
            price = market.get_price(symbol, date, time_of_day)
            if price:
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
    # Initialize session
    if 'current_date' not in session:
        session['current_date'] = '2000-01-01'
        session['cash'] = 10000.0
        session['time_of_day'] = 'open'
        session['holdings'] = {}  # {symbol: {'shares': 0, 'avg_cost': 0}}
        session['transactions'] = []  # Transaction history
        session['portfolio_history'] = [{'date': '2000-01-01', 'value': 10000.0}]  # Portfolio value over time

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
        position_value = shares * price
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

    # Get buy stock info if searching
    buy_stock = session.get('buy_stock', None)
    buy_error = session.get('buy_error', None)

    # Clear errors after displaying
    if buy_error:
        session.pop('buy_error', None)

    return render_template('portfolio.html',
                         date=date,
                         time_of_day=time,
                         cash=cash,
                         portfolio=portfolio,
                         portfolio_value=portfolio_value,
                         transactions=transactions,
                         portfolio_history=portfolio_history,
                         buy_stock=buy_stock,
                         buy_error=buy_error)

@app.route('/buy')
def buy_page():
    """Show buy page with search"""
    return render_template('buy.html',
                         date=session.get('current_date', '2000-01-01'),
                         time_of_day=session.get('time_of_day', 'open'),
                         cash=session.get('cash', 10000.0))

@app.route('/buy/search', methods=['POST'])
def search_stock():
    """Search for a stock to buy"""
    symbol = request.form.get('symbol', '').upper()

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

@app.route('/api/history')
def get_history():
    symbol = request.args.get('symbol')
    if not symbol:
        return jsonify([])

    current_date = session.get('current_date', '2000-01-01')
    start_date = datetime.strptime(current_date, '%Y-%m-%d') - timedelta(days=30)
    end_date = datetime.strptime(current_date, '%Y-%m-%d')

    history = market.get_history(symbol, start_date, end_date)
    return jsonify(history)

@app.route('/buy/execute', methods=['POST'])
def execute_buy():
    try:
        symbol = request.form.get('symbol', '').upper()
        if not symbol:
            return redirect(url_for('buy_page'))

        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price is None:
            return redirect(url_for('buy_page'))

        # Check if buying by shares or cash
        if 'shares' in request.form and request.form.get('shares'):
            amount = int(request.form.get('shares', 0))
            if amount <= 0:
                return redirect(url_for('buy_page'))
            cost = price * amount
        else:
            cash_amount = float(request.form.get('cash', 0))
            if cash_amount <= 0:
                return redirect(url_for('buy_page'))
            amount = int(cash_amount / price)
            cost = price * amount

        if amount <= 0 or session['cash'] < cost:
            return redirect(url_for('buy_page'))

        # Get or create holding
        holdings = session.get('holdings', {})
        if symbol not in holdings:
            holdings[symbol] = {'shares': 0, 'avg_cost': 0}

        holding = holdings[symbol]
        current_shares = holding['shares']
        current_avg_cost = holding['avg_cost']

        # Update average cost basis
        if current_shares > 0:
            total_cost = (current_shares * current_avg_cost) + cost
            new_shares = current_shares + amount
            holding['avg_cost'] = total_cost / new_shares
        else:
            holding['avg_cost'] = price

        holding['shares'] = current_shares + amount
        holdings[symbol] = holding
        session['holdings'] = holdings
        session['cash'] = float(session['cash'] - cost)

        # Record transaction
        transactions = session.get('transactions', [])
        transactions.append({
            'date': session['current_date'],
            'time': session['time_of_day'],
            'type': 'BUY',
            'symbol': symbol,
            'shares': amount,
            'price': price,
            'total': cost
        })
        session['transactions'] = transactions

        # Update portfolio history
        _update_portfolio_value(session)
    except:
        pass

    return redirect(url_for('index'))

@app.route('/sell/<symbol>', methods=['POST'])
def sell(symbol):
    try:
        symbol = symbol.upper()
        holdings = session.get('holdings', {})

        if symbol not in holdings:
            return redirect(url_for('index'))

        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price is None:
            return redirect(url_for('index'))

        holding = holdings[symbol]
        current_shares = holding['shares']

        # Check if selling by shares or cash
        if 'shares' in request.form and request.form.get('shares'):
            amount = int(request.form.get('shares', 0))
        else:
            cash_amount = float(request.form.get('cash', 0))
            if cash_amount <= 0:
                return redirect(url_for('index'))
            amount = int(cash_amount / price)

        if amount <= 0 or current_shares < amount:
            return redirect(url_for('index'))

        session['cash'] = float(session['cash'] + price * amount)
        holding['shares'] = current_shares - amount

        # Reset avg cost if all shares sold
        if holding['shares'] == 0:
            holding['avg_cost'] = 0
            del holdings[symbol]  # Remove empty position
        else:
            holdings[symbol] = holding

        session['holdings'] = holdings

        # Record transaction
        transactions = session.get('transactions', [])
        transactions.append({
            'date': session['current_date'],
            'time': session['time_of_day'],
            'type': 'SELL',
            'symbol': symbol,
            'shares': amount,
            'price': price,
            'total': price * amount
        })
        session['transactions'] = transactions

        # Update portfolio history
        _update_portfolio_value(session)
    except:
        pass

    return redirect(url_for('index'))

@app.route('/sell/<symbol>/all', methods=['POST'])
def sell_all(symbol):
    """Sell all shares of a stock"""
    try:
        symbol = symbol.upper()
        holdings = session.get('holdings', {})

        if symbol not in holdings:
            return redirect(url_for('index'))

        price = market.get_price(symbol, session['current_date'], session['time_of_day'])
        if price is None:
            return redirect(url_for('index'))

        holding = holdings[symbol]
        amount = holding['shares']

        if amount <= 0:
            return redirect(url_for('index'))

        session['cash'] = float(session['cash'] + price * amount)
        del holdings[symbol]
        session['holdings'] = holdings

        # Record transaction
        transactions = session.get('transactions', [])
        transactions.append({
            'date': session['current_date'],
            'time': session['time_of_day'],
            'type': 'SELL ALL',
            'symbol': symbol,
            'shares': amount,
            'price': price,
            'total': price * amount
        })
        session['transactions'] = transactions

        # Update portfolio history
        _update_portfolio_value(session)
    except:
        pass

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
    return redirect(url_for('index'))

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
            session['current_date'] = new_date
            session['time_of_day'] = 'open'
    except:
        pass

    return redirect(url_for('index'))

@app.route('/reset')
def reset():
    """Clear session and start over"""
    session.clear()
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True, port=5001)