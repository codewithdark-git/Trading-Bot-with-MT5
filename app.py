import time
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

# Set up logging
logging.basicConfig(filename='trading_bot.log', level=logging.INFO,
                    format='%(asctime)s:%(levelname)s:%(message)s')

# Connect to MetaTrader 5
if not mt5.initialize():
    logging.error("initialize() failed")
    mt5.shutdown()

# Login to your account
account_number = 156090030
password = "codewithdark_E0"
server = "Exness-MT5Trial"

authorized = mt5.login(account_number, password, server)
if not authorized:
    logging.error("Failed to connect at account #{}, error code: {}".format(account_number, mt5.last_error()))
    mt5.shutdown()
    exit()
else:
    print("Connected to account #{}".format(account_number))

# Check if auto trading is enabled
terminal_info = mt5.terminal_info()
if not terminal_info.trade_allowed:
    logging.error("Auto trading is disabled in MetaTrader 5 terminal")
else:
    logging.info("Auto trading is enabled in MetaTrader 5 terminal")

# Define the symbols to trade
symbols = ["EURUSDm", "BTCUSDm", "XAUUSDm", "BTCJPYm"]
lot_size = 0.1
max_open_trades_per_symbol = 2
profile_in_usd = 10


def get_data(symbol, timeframe, num_bars):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_bars)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df


def moving_average_crossover(df):
    df['fast_ma'] = df['close'].rolling(window=9).mean()
    df['slow_ma'] = df['close'].rolling(window=21).mean()
    if df['fast_ma'].iloc[-1] > df['slow_ma'].iloc[-1] and df['fast_ma'].iloc[-2] <= df['slow_ma'].iloc[-2]:
        return "buy"
    elif df['fast_ma'].iloc[-1] < df['slow_ma'].iloc[-1] and df['fast_ma'].iloc[-2] >= df['slow_ma'].iloc[-2]:
        return "sell"
    return None


def rsi_strategy(df):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    if df['rsi'].iloc[-1] < 30:
        return "buy"
    elif df['rsi'].iloc[-1] > 70:
        return "sell"
    return None


def macd_strategy(df):
    df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    if df['macd'].iloc[-1] > df['signal'].iloc[-1] and df['macd'].iloc[-2] <= df['signal'].iloc[-2]:
        return "buy"
    elif df['macd'].iloc[-1] < df['signal'].iloc[-1] and df['macd'].iloc[-2] >= df['signal'].iloc[-2]:
        return "sell"
    return None


def bollinger_bands_strategy(df):
    df['middle_band'] = df['close'].rolling(window=20).mean()
    df['upper_band'] = df['middle_band'] + 2 * df['close'].rolling(window=20).std()
    df['lower_band'] = df['middle_band'] - 2 * df['close'].rolling(window=20).std()
    if df['close'].iloc[-1] < df['lower_band'].iloc[-1]:
        return "buy"
    elif df['close'].iloc[-1] > df['upper_band'].iloc[-1]:
        return "sell"
    return None


def breakout_strategy(df):
    df['highest_high'] = df['high'].rolling(window=20).max()
    df['lowest_low'] = df['low'].rolling(window=20).min()
    if df['close'].iloc[-1] > df['highest_high'].iloc[-1]:
        return "buy"
    elif df['close'].iloc[-1] < df['lowest_low'].iloc[-1]:
        return "sell"
    return None


def combined_strategy(df):
    rsi_signal = rsi_strategy(df)
    bb_signal = bollinger_bands_strategy(df)
    if rsi_signal == bb_signal and rsi_signal is not None:
        return rsi_signal
    return None


def create_order(symbol, lot_size, action):
    price = mt5.symbol_info_tick(symbol).ask if action == "buy" else mt5.symbol_info_tick(symbol).bid
    order_type = mt5.ORDER_TYPE_BUY if action == "buy" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "deviation": 10,
        "magic": 234000,
        "comment": "Python script order",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(f"Order send failed for {symbol}, retcode={result.retcode}, comment={result.comment}")
    else:
        logging.info(f"Order sent successfully for {symbol}")


def close_profitable_positions(symbol):
    positions = mt5.positions_get(symbol=symbol)
    for pos in positions:
        profit = pos.profit
        if profit >= profile_in_usd:  # Close only profitable positions
            order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).bid if order_type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(
                symbol).ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": order_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 10,
                "magic": 234000,
                "comment": "Close position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC
            }
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logging.error(f"Close position failed for {symbol}, retcode={result.retcode}")
            else:
                logging.info(f"Position closed successfully for {symbol}")


def get_open_positions_count(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return 0
    return len(positions)


# New strategy implementation
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def rng_size(df, qty, n):
    avrng = ema(abs(df['close'] - df['close'].shift(1)), n)
    wper = (n * 2) - 1
    AC = ema(avrng, wper) * qty
    return AC


def rng_filt(df, rng_, n):
    r = rng_
    rfilt = [df['close'].iloc[0], df['close'].iloc[0]]
    hi_band = np.zeros(len(df))
    lo_band = np.zeros(len(df))
    filt = np.zeros(len(df))

    for i in range(1, len(df)):
        if df['close'].iloc[i] - r.iloc[i] > rfilt[1]:
            rfilt[0] = df['close'].iloc[i] - r.iloc[i]
        if df['close'].iloc[i] + r.iloc[i] < rfilt[1]:
            rfilt[0] = df['close'].iloc[i] + r.iloc[i]
        rfilt[1] = rfilt[0]
        hi_band[i] = rfilt[0] + r.iloc[i]
        lo_band[i] = rfilt[0] - r.iloc[i]
        filt[i] = rfilt[0]

    return hi_band, lo_band, filt


def new_strategy(df):
    rng_per = 20
    rng_qty = 3.5
    df['rng_size'] = rng_size(df, rng_qty, rng_per)
    df['hi_band'], df['lo_band'], df['filt'] = rng_filt(df, df['rng_size'], rng_per)
    df['fdir'] = np.where(df['filt'] > df['filt'].shift(1), 1, np.where(df['filt'] < df['filt'].shift(1), -1, 0))
    df['upward'] = np.where(df['fdir'] == 1, 1, 0)
    df['downward'] = np.where(df['fdir'] == -1, 1, 0)

    df['longCond'] = np.where(
        (df['close'] > df['filt']) & (df['close'] > df['close'].shift(1)) & (df['upward'] > 0) |
        (df['close'] > df['filt']) & (df['close'] < df['close'].shift(1)) & (df['upward'] > 0),
        True, False)

    df['shortCond'] = np.where(
        (df['close'] < df['filt']) & (df['close'] < df['close'].shift(1)) & (df['downward'] > 0) |
        (df['close'] < df['filt']) & (df['close'] > df['close'].shift(1)) & (df['downward'] > 0),
        True, False)

    df['CondIni'] = 0
    df['CondIni'] = np.where(df['longCond'], 1, np.where(df['shortCond'], -1, df['CondIni'].shift(1)))
    df['longCondition'] = df['longCond'] & (df['CondIni'].shift(1) == -1)
    df['shortCondition'] = df['shortCond'] & (df['CondIni'].shift(1) == 1)

    if df['longCondition'].iloc[-1]:
        return "buy"
    elif df['shortCondition'].iloc[-1]:
        return "sell"
    return None


# Example of running the strategy
if __name__ == "__main__":
    while True:
        for symbol in symbols:
            logging.info(f"Running strategies for {symbol}")

            # Fetch data
            df = get_data(symbol, mt5.TIMEFRAME_M1, 100)

            # Check the number of open positions for the symbol
            open_positions_count = get_open_positions_count(symbol)
            logging.info(f"Number of open positions for {symbol}: {open_positions_count}")

            if open_positions_count < max_open_trades_per_symbol:
                # Apply strategies
                signal = combined_strategy(df)

                if signal:
                    create_order(symbol, lot_size, signal)
                else:
                    signal = new_strategy(df)
                    if signal:
                        create_order(symbol, lot_size, signal)
                    # else:
                    #     # for strategy in [moving_average_crossover, rsi_strategy, macd_strategy,
                    #     #                  bollinger_bands_strategy, breakout_strategy]:
                    #     signal = moving_average_crossover(df)
                    #     if signal:
                    #         create_order(symbol, lot_size, signal)
                    break  # Stop after the first valid signal to avoid multiple orders in one loop

            # Check and close profitable positions
            close_profitable_positions(symbol)

        # Wait for the next iteration
        time.sleep(60)  # Adjust as needed

    # Shutdown MT5 connection
    mt5.shutdown()
