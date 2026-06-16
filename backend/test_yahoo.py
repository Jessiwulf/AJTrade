import requests
import yfinance as yf

def fetch_yahoo_finance(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        print(f"--- {symbol} URL Request ---")
        print(f"Status Code: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")
        print(f"Body (first 500 characters):\n{response.text[:500]}\n")
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")

def check_yfinance(symbol):
    print(f"--- {symbol} yfinance ---")
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period='2d', interval='1d')
        if history.empty:
            print(f"yfinance history for {symbol} is EMPTY.")
        else:
            print(f"yfinance history for {symbol} is NOT empty.")
            print(history.head())
    except Exception as e:
        print(f"yfinance for {symbol} raised an exception: {e}")

if __name__ == '__main__':
    fetch_yahoo_finance('AAPL')
    fetch_yahoo_finance('MSFT')
    check_yfinance('AAPL')
