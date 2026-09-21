# ============================================================
# SANJAY RANA - DELTA REAL TRADING WORKER
# ============================================================
# IMPORTANT:
# - This file is NOT a Streamlit app.
# - Run it as an always-on background process/service.
# - Closing the browser/Streamlit dashboard does not stop this worker.
# - NO TAKE PROFIT / NO BRACKET TP.
# - One entry LIMIT order per confirmed 5-minute SuperTrend signal.
# - On BUY<->SELL direction change:
#       1) cancel all old opposite bot-owned open orders
#       2) close any opposite live position with reduce-only MARKET
#       3) verify exchange state
#       4) place the new entry LIMIT order
#
# Strategy:
# BTCUSD | 5-minute | ATR 10 | Multiplier 3.0 | HL2
# BUY  = SuperTrend direction Bearish -> Bullish
# SELL = SuperTrend direction Bullish -> Bearish
# ============================================================

import os
import time
import json
import hmac
import hashlib
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd


# ============================================================
# SETTINGS
# ============================================================

BASE_URL = os.getenv(
    "DELTA_BASE_URL",
    "https://api.india.delta.exchange"
).rstrip("/")

SYMBOL = os.getenv("DELTA_SYMBOL", "BTCUSD")
PRODUCT_ID = int(os.getenv("DELTA_PRODUCT_ID", "27"))

TIMEFRAME = "5m"
CANDLE_SECONDS = 300

ATR_PERIOD = 10
MULTIPLIER = 3.0

WORKER_LOOP_SECONDS = float(os.getenv("WORKER_LOOP_SECONDS", "5"))

ORDER_QTY = float(os.getenv("ORDER_QTY", "0.001"))
CONTRACT_BTC = 0.001

BUY_OFFSET = float(os.getenv("BUY_OFFSET", "-10"))
SELL_OFFSET = float(os.getenv("SELL_OFFSET", "10"))

# Optional safety switch. Must explicitly be true on the worker host.
REMOTE_TRADING = os.getenv("REMOTE_TRADING", "false").lower() == "true"

CLIENT_PREFIX = "STW_"

IST = timezone(timedelta(hours=5, minutes=30))


# ============================================================
# CREDENTIALS
# ============================================================

API_KEY = os.getenv("OWNER_API_KEY", "")
API_SECRET = os.getenv("OWNER_API_SECRET", "")

if not API_KEY or not API_SECRET:
    raise RuntimeError(
        "OWNER_API_KEY and OWNER_API_SECRET environment variables are required."
    )

if ORDER_QTY <= 0:
    raise RuntimeError("ORDER_QTY must be greater than 0.")

contracts_float = ORDER_QTY / CONTRACT_BTC
if abs(contracts_float - round(contracts_float)) > 1e-9:
    raise RuntimeError("ORDER_QTY must be a multiple of 0.001 BTC.")

ORDER_SIZE = int(round(contracts_float))


# ============================================================
# LOGGING
# ============================================================

def log(message):
    now = datetime.now(timezone.utc).astimezone(IST)
    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S IST')}] {message}",
        flush=True
    )


# ============================================================
# DELTA API
# ============================================================

class DeltaAPI:

    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Sanjay-Rana-Real-Trading-Worker",
            "Accept": "application/json",
        })

    def make_signature(
        self,
        method,
        timestamp,
        path,
        query_string="",
        body=""
    ):
        message = (
            method.upper()
            + timestamp
            + path
            + query_string
            + body
        )
        return hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def request(
        self,
        method,
        path,
        params=None,
        body=None,
        private=False
    ):
        params = params or {}
        body = body or {}

        payload = ""
        if body:
            payload = json.dumps(
                body,
                separators=(",", ":")
            )

        query_string = ""
        if params:
            query_string = "?" + "&".join(
                f"{key}={value}"
                for key, value in params.items()
            )

        headers = {
            "Accept": "application/json",
            "User-Agent": "Sanjay-Rana-Real-Trading-Worker",
        }

        if private:
            timestamp = str(int(time.time()))
            signature = self.make_signature(
                method,
                timestamp,
                path,
                query_string,
                payload
            )
            headers.update({
                "api-key": self.api_key,
                "timestamp": timestamp,
                "signature": signature,
                "Content-Type": "application/json",
            })

        try:
            response = self.session.request(
                method.upper(),
                BASE_URL + path,
                params=params,
                data=payload if payload else None,
                headers=headers,
                timeout=15,
            )

            try:
                data = response.json()
            except Exception:
                return {
                    "success": False,
                    "error": response.text
                }

            return data

        except Exception as exc:
            return {
                "success": False,
                "error": str(exc)
            }

    def candles(self):
        end = int(time.time())
        start = end - (500 * CANDLE_SECONDS)

        return self.request(
            "GET",
            "/v2/history/candles",
            params={
                "symbol": SYMBOL,
                "resolution": TIMEFRAME,
                "start": start,
                "end": end,
            }
        )

    def open_orders(self):
        return self.request(
            "GET",
            "/v2/orders",
            params={
                "product_id": PRODUCT_ID,
                "state": "open",
            },
            private=True,
        )

    def closed_orders(self):
        return self.request(
            "GET",
            "/v2/orders",
            params={
                "product_id": PRODUCT_ID,
                "state": "closed",
            },
            private=True,
        )

    def position(self):
        return self.request(
            "GET",
            "/v2/positions",
            params={
                "product_id": PRODUCT_ID,
            },
            private=True,
        )

    def place_limit_order(
        self,
        side,
        size,
        limit_price,
        client_order_id,
    ):
        # NO TP PARAMETERS ARE SENT.
        body = {
            "product_id": PRODUCT_ID,
            "product_symbol": SYMBOL,
            "limit_price": str(limit_price),
            "size": int(size),
            "side": side,
            "order_type": "limit_order",
            "time_in_force": "gtc",
            "post_only": False,
            "reduce_only": False,
            "client_order_id": str(client_order_id)[:32],
        }

        return self.request(
            "POST",
            "/v2/orders",
            body=body,
            private=True,
        )

    def place_market_reduce_only(
        self,
        side,
        size,
        client_order_id,
    ):
        body = {
            "product_id": PRODUCT_ID,
            "product_symbol": SYMBOL,
            "size": int(abs(size)),
            "side": side,
            "order_type": "market_order",
            "reduce_only": True,
            "client_order_id": str(client_order_id)[:32],
        }

        return self.request(
            "POST",
            "/v2/orders",
            body=body,
            private=True,
        )

    def cancel_order(self, order_id):
        return self.request(
            "DELETE",
            f"/v2/orders/{order_id}",
            private=True,
        )


api = DeltaAPI(API_KEY, API_SECRET)


# ============================================================
# RESPONSE HELPERS
# ============================================================

def get_result(data):
    if not isinstance(data, dict):
        return None
    if not data.get("success"):
        return None
    return data.get("result")


def get_number(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def result_position_size(response):
    result = get_result(response)

    if isinstance(result, list):
        if not result:
            return 0.0
        result = result[0]

    if isinstance(result, dict):
        return get_number(result.get("size"), 0.0)

    return 0.0


# ============================================================
# CANDLE + SUPERTREND
# ============================================================

def make_dataframe(response):
    result = get_result(response)

    if not isinstance(result, list):
        return pd.DataFrame()

    rows = []

    for candle in result:
        try:
            rows.append({
                "time": int(candle["time"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", 0)),
            })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    return (
        pd.DataFrame(rows)
        .drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )


def calculate_supertrend(df):
    df = df.copy()

    previous_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - previous_close).abs()
    tr3 = (df["low"] - previous_close).abs()

    df["TR"] = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["ATR"] = float("nan")

    if len(df) < ATR_PERIOD:
        return df

    df.loc[ATR_PERIOD - 1, "ATR"] = (
        df["TR"].iloc[:ATR_PERIOD].mean()
    )

    for i in range(ATR_PERIOD, len(df)):
        df.loc[i, "ATR"] = (
            df.loc[i - 1, "ATR"] * (ATR_PERIOD - 1)
            + df.loc[i, "TR"]
        ) / ATR_PERIOD

    df["HL2"] = (df["high"] + df["low"]) / 2.0
    df["FINAL_UPPER"] = float("nan")
    df["FINAL_LOWER"] = float("nan")
    df["SUPERTREND"] = float("nan")
    df["ST_DIRECTION"] = 0
    df["SIGNAL"] = ""

    previous_final_upper = None
    previous_final_lower = None
    previous_supertrend = None
    previous_direction = 1

    for i in range(len(df)):
        atr = df.loc[i, "ATR"]

        if pd.isna(atr):
            continue

        hl2 = float(df.loc[i, "HL2"])
        close = float(df.loc[i, "close"])

        basic_upper = hl2 + MULTIPLIER * float(atr)
        basic_lower = hl2 - MULTIPLIER * float(atr)

        if (
            i == ATR_PERIOD - 1
            or previous_final_upper is None
        ):
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            previous_close_value = float(
                df.loc[i - 1, "close"]
            )

            if (
                basic_upper < previous_final_upper
                or previous_close_value > previous_final_upper
            ):
                final_upper = basic_upper
            else:
                final_upper = previous_final_upper

            if (
                basic_lower > previous_final_lower
                or previous_close_value < previous_final_lower
            ):
                final_lower = basic_lower
            else:
                final_lower = previous_final_lower

        if (
            i == ATR_PERIOD - 1
            or previous_supertrend is None
        ):
            direction = 1
            supertrend = final_upper
        else:
            if previous_supertrend == previous_final_upper:
                if close <= final_upper:
                    direction = 1
                    supertrend = final_upper
                else:
                    direction = -1
                    supertrend = final_lower
            else:
                if close >= final_lower:
                    direction = -1
                    supertrend = final_lower
                else:
                    direction = 1
                    supertrend = final_upper

        df.loc[i, "FINAL_UPPER"] = final_upper
        df.loc[i, "FINAL_LOWER"] = final_lower
        df.loc[i, "ST_DIRECTION"] = direction
        df.loc[i, "SUPERTREND"] = supertrend

        if i > 0 and previous_direction != direction:
            if direction == -1:
                df.loc[i, "SIGNAL"] = "BUY"
            elif direction == 1:
                df.loc[i, "SIGNAL"] = "SELL"

        previous_final_upper = final_upper
        previous_final_lower = final_lower
        previous_supertrend = supertrend
        previous_direction = direction

    return df


def get_latest_signal():
    response = api.candles()
    df = make_dataframe(response)

    if df.empty:
        raise RuntimeError("No candle data returned.")

    # Never use the currently forming 5-minute candle.
    current_candle_start = (
        int(time.time()) // CANDLE_SECONDS
    ) * CANDLE_SECONDS

    df = df[
        df["time"] < current_candle_start
    ].copy().reset_index(drop=True)

    if len(df) < ATR_PERIOD + 5:
        raise RuntimeError("Not enough confirmed candles.")

    df = calculate_supertrend(df)

    signal_rows = df[
        df["SIGNAL"].isin(["BUY", "SELL"])
    ].copy()

    if signal_rows.empty:
        return None

    row = signal_rows.iloc[-1]

    return {
        "direction": str(row["SIGNAL"]),
        "candle_time": int(row["time"]),
        "signal_price": float(row["close"]),
        "supertrend": float(row["SUPERTREND"]),
    }


# ============================================================
# BOT ORDER IDENTIFICATION
# ============================================================

def client_id_for(direction, candle_time):
    return f"{CLIENT_PREFIX}{direction}_{candle_time}"


def is_bot_order(order):
    client_id = str(
        order.get("client_order_id", "")
    )
    return client_id.startswith(CLIENT_PREFIX)


def is_open_order(order):
    state = str(
        order.get("state", "")
    ).lower()

    return state not in {
        "cancelled",
        "canceled",
        "filled",
        "closed",
        "rejected",
    }


def order_side(order):
    return str(
        order.get("side", "")
    ).lower()


# ============================================================
# EXCHANGE CLEANUP ON DIRECTION CHANGE
# ============================================================

def cleanup_opposite_state(new_direction):
    new_side = (
        "buy"
        if new_direction == "BUY"
        else "sell"
    )

    old_side = (
        "sell"
        if new_side == "buy"
        else "buy"
    )

    # --------------------------------------------------------
    # 1) Fresh open-order snapshot.
    # --------------------------------------------------------
    response = api.open_orders()

    if not (
        isinstance(response, dict)
        and response.get("success") is True
    ):
        raise RuntimeError(
            "Open-order check failed; refusing new order."
        )

    open_orders = get_result(response)
    if not isinstance(open_orders, list):
        open_orders = []

    # Cancel ONLY bot-owned opposite orders.
    for order in open_orders:
        if not is_bot_order(order):
            continue

        if order_side(order) != old_side:
            continue

        order_id = order.get("id")
        if order_id is None:
            continue

        result = api.cancel_order(order_id)

        if not (
            isinstance(result, dict)
            and result.get("success") is True
        ):
            raise RuntimeError(
                f"Failed to cancel old opposite order {order_id}: "
                f"{result}"
            )

        log(
            f"🗑️ CANCELLED OLD {old_side.upper()} "
            f"BOT ORDER {order_id}"
        )

    # --------------------------------------------------------
    # 2) Fresh position snapshot.
    # --------------------------------------------------------
    position_response = api.position()

    if not (
        isinstance(position_response, dict)
        and position_response.get("success") is True
    ):
        raise RuntimeError(
            "Position check failed; refusing new order."
        )

    position_size = result_position_size(
        position_response
    )

    opposite_position = (
        new_direction == "BUY"
        and position_size < 0
    ) or (
        new_direction == "SELL"
        and position_size > 0
    )

    if opposite_position:
        close_side = (
            "buy"
            if position_size < 0
            else "sell"
        )

        close_size = int(abs(position_size))

        if close_size <= 0:
            raise RuntimeError(
                "Opposite position detected but close size is zero."
            )

        close_id = (
            f"{CLIENT_PREFIX}CLOSE_"
            f"{new_direction}_{int(time.time())}"
        )

        result = api.place_market_reduce_only(
            side=close_side,
            size=close_size,
            client_order_id=close_id,
        )

        if not (
            isinstance(result, dict)
            and result.get("success") is True
        ):
            raise RuntimeError(
                f"Opposite position close failed: {result}"
            )

        log(
            f"🔄 CLOSED OPPOSITE POSITION "
            f"SIZE={close_size}"
        )

    # --------------------------------------------------------
    # 3) Verify no old opposite bot order remains.
    # --------------------------------------------------------
    verify_orders = api.open_orders()

    if not (
        isinstance(verify_orders, dict)
        and verify_orders.get("success") is True
    ):
        raise RuntimeError(
            "Final open-order verification failed."
        )

    verify_open = get_result(verify_orders)
    if not isinstance(verify_open, list):
        verify_open = []

    remaining_old_orders = [
        order
        for order in verify_open
        if (
            is_bot_order(order)
            and order_side(order) == old_side
            and is_open_order(order)
        )
    ]

    if remaining_old_orders:
        raise RuntimeError(
            "Old opposite bot order still exists; "
            "new entry BLOCKED."
        )

    # --------------------------------------------------------
    # 4) Verify opposite position is gone.
    # --------------------------------------------------------
    verify_position = api.position()

    if not (
        isinstance(verify_position, dict)
        and verify_position.get("success") is True
    ):
        raise RuntimeError(
            "Final position verification failed."
        )

    final_size = result_position_size(
        verify_position
    )

    opposite_remaining = (
        new_direction == "BUY"
        and final_size < 0
    ) or (
        new_direction == "SELL"
        and final_size > 0
    )

    if opposite_remaining:
        raise RuntimeError(
            "Opposite position still exists; "
            "new entry BLOCKED."
        )


# ============================================================
# DUPLICATE CHECK
# ============================================================

def signal_already_processed(direction, candle_time):
    stable_id = client_id_for(
        direction,
        candle_time
    )

    open_response = api.open_orders()

    if not (
        isinstance(open_response, dict)
        and open_response.get("success") is True
    ):
        raise RuntimeError(
            "Open-order duplicate check failed."
        )

    open_orders = get_result(open_response)
    if not isinstance(open_orders, list):
        open_orders = []

    for order in open_orders:
        if str(order.get("client_order_id", "")) == stable_id:
            return True

    closed_response = api.closed_orders()

    if not (
        isinstance(closed_response, dict)
        and closed_response.get("success") is True
    ):
        raise RuntimeError(
            "Closed-order duplicate check failed."
        )

    closed_orders = get_result(closed_response)
    if not isinstance(closed_orders, list):
        closed_orders = []

    for order in closed_orders:
        if str(order.get("client_order_id", "")) == stable_id:
            return True

    return False


# ============================================================
# SAME-DIRECTION POSITION GUARD
# ============================================================

def same_direction_position(direction):
    response = api.position()

    if not (
        isinstance(response, dict)
        and response.get("success") is True
    ):
        raise RuntimeError(
            "Position guard failed."
        )

    size = result_position_size(response)

    if direction == "BUY":
        return size > 0

    return size < 0


# ============================================================
# PLACE NEW ENTRY
# ============================================================

def place_new_entry(signal):
    direction = signal["direction"]
    candle_time = signal["candle_time"]
    signal_price = signal["signal_price"]

    side = (
        "buy"
        if direction == "BUY"
        else "sell"
    )

    offset = (
        BUY_OFFSET
        if direction == "BUY"
        else SELL_OFFSET
    )

    limit_price = signal_price + offset

    client_id = client_id_for(
        direction,
        candle_time
    )

    result = api.place_limit_order(
        side=side,
        size=ORDER_SIZE,
        limit_price=limit_price,
        client_order_id=client_id,
    )

    if not (
        isinstance(result, dict)
        and result.get("success") is True
    ):
        raise RuntimeError(
            f"NEW {direction} LIMIT ORDER FAILED: {result}"
        )

    data = result.get("result", {})

    log(
        f"🚀 NEW {direction} LIMIT ORDER SENT | "
        f"ENTRY={limit_price:.2f} | "
        f"SIZE={ORDER_SIZE} | "
        f"BTC={ORDER_SIZE * CONTRACT_BTC:.3f} | "
        f"ORDER_ID={data.get('id', '-')}"
    )


# ============================================================
# ONE WORKER CYCLE
# ============================================================

last_seen_signal = None


def worker_cycle():
    global last_seen_signal

    signal = get_latest_signal()

    if signal is None:
        log("⏳ No confirmed BUY/SELL signal found yet.")
        return

    signal_key = (
        signal["direction"],
        signal["candle_time"]
    )

    log(
        f"📡 SIGNAL={signal['direction']} | "
        f"CANDLE={datetime.fromtimestamp(signal['candle_time'], timezone.utc).astimezone(IST).strftime('%Y-%m-%d %H:%M:%S IST')} | "
        f"CLOSE={signal['signal_price']:.2f}"
    )

    # Same confirmed signal has already been seen by this worker process.
    if signal_key == last_seen_signal:
        return

    # Exchange-side duplicate protection survives worker restarts.
    if signal_already_processed(
        signal["direction"],
        signal["candle_time"]
    ):
        last_seen_signal = signal_key
        log(
            "🛡️ DUPLICATE BLOCKED — "
            "this confirmed signal already exists on Delta."
        )
        return

    # Only a NEW confirmed signal should trigger a new entry.
    last_seen_signal = signal_key

    # On every new opposite signal, clear the old opposite state first.
    cleanup_opposite_state(
        signal["direction"]
    )

    # If the same-direction position somehow exists after cleanup,
    # do not add another position.
    if same_direction_position(
        signal["direction"]
    ):
        log(
            "🛡️ SAME-DIRECTION POSITION EXISTS — "
            "new entry blocked."
        )
        return

    if not REMOTE_TRADING:
        log(
            "⚠️ REMOTE_TRADING=false — "
            "signal detected but NO REAL ORDER SENT."
        )
        return

    place_new_entry(signal)


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    log("============================================================")
    log("🟢 SANJAY RANA REAL TRADING WORKER STARTED")
    log(f"SYMBOL={SYMBOL} PRODUCT_ID={PRODUCT_ID}")
    log("TIMEFRAME=5m | ATR=10 | MULTIPLIER=3.0 | SOURCE=HL2")
    log(f"ORDER_QTY={ORDER_QTY:.3f} BTC | CONTRACTS={ORDER_SIZE}")
    log("TP=DISABLED")
    log(f"REMOTE_TRADING={REMOTE_TRADING}")
    log("============================================================")

    while True:
        cycle_started = time.time()

        try:
            worker_cycle()
        except Exception as exc:
            # IMPORTANT: an API/network/error cycle does not crash the
            # worker permanently. It logs the error and retries.
            log(f"🔴 WORKER ERROR: {exc}")

        elapsed = time.time() - cycle_started
        sleep_for = max(
            1.0,
            WORKER_LOOP_SECONDS - elapsed
        )

        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
