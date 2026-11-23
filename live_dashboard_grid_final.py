# ============================================================
# SANJAY RANA - XAUTUSD SUPERTREND REVERSE TRADING DASHBOARD
# Strategy: 1 Minute | ATR 10 | Multiplier 2 | HL2
# BUY signal -> 1 lot BUY
# SELL signal -> close BUY and open 1 lot SELL
# Opposite signal -> close old position and reverse immediately
# No target / no stop-loss. Hold until opposite confirmed signal.
# ============================================================

import os
import time
import json
import hmac
import hashlib
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# FIXED STRATEGY SETTINGS
# ============================================================

BASE_URL = os.getenv(
    "DELTA_BASE_URL",
    "https://api.india.delta.exchange"
).rstrip("/")

SYMBOL = "XAUTUSD"
TIMEFRAME = "1m"
CANDLE_SECONDS = 60

ATR_PERIOD = 10
MULTIPLIER = 2.0
ORDER_LOTS = 1

REFRESH_SECONDS = 5
REMOTE_TRADING = os.getenv("REMOTE_TRADING", "true").lower() == "true"

IST = timezone(timedelta(hours=5, minutes=30))


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="Sanjay Rana XAUTUSD Real Trading",
    page_icon="📈",
    layout="wide"
)

st.title("📈 SANJAY RANA — XAUTUSD SUPERTREND REVERSE TRADING")
st.caption(
    "XAUTUSD | 1 Minute | ATR 10 | Multiplier 2.0 | HL2 | "
    "Confirmed Candle Close | 1 Lot BUY / 1 Lot SELL"
)


# ============================================================
# HELPERS
# ============================================================

def get_secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return value
    except Exception:
        pass
    return os.getenv(name, default)


def number(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def show_price(value):
    value = number(value)
    return "-" if value is None else f"{value:,.2f}"


def indian_time(timestamp):
    try:
        ts = float(timestamp)
        if ts > 10_000_000_000:
            ts /= 1000
        return datetime.fromtimestamp(
            ts, tz=timezone.utc
        ).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    except Exception:
        return "-"


def get_result(response):
    if isinstance(response, dict) and response.get("success") is True:
        return response.get("result")
    return None


def safe_result_list(response):
    result = get_result(response)
    return result if isinstance(result, list) else []


# ============================================================
# DELTA API
# ============================================================

class DeltaAPI:
    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Sanjay-Rana-XAUTUSD-SuperTrend-Bot"
        })

    def request(self, method, path, params=None, body=None, private=False):
        params = params or {}
        body = body or {}

        payload = json.dumps(body, separators=(",", ":")) if body else ""
        query_string = ""
        if params:
            query_string = "?" + "&".join(
                f"{key}={value}" for key, value in params.items()
            )

        headers = {
            "Accept": "application/json",
            "User-Agent": "Sanjay-Rana-XAUTUSD-SuperTrend-Bot"
        }

        if private:
            if not self.api_key or not self.api_secret:
                return {
                    "success": False,
                    "error": "OWNER_API_KEY / OWNER_API_SECRET missing"
                }

            timestamp = str(int(time.time()))
            message = (
                method.upper()
                + timestamp
                + path
                + query_string
                + payload
            )
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            headers.update({
                "api-key": self.api_key,
                "timestamp": timestamp,
                "signature": signature,
                "Content-Type": "application/json"
            })

        try:
            response = self.session.request(
                method.upper(),
                BASE_URL + path,
                params=params,
                data=payload if payload else None,
                headers=headers,
                timeout=15
            )
            try:
                return response.json()
            except Exception:
                return {
                    "success": False,
                    "error": response.text
                }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc)
            }

    def product(self):
        return self.request("GET", f"/v2/products/{SYMBOL}")

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
                "end": end
            }
        )

    def ticker(self):
        return self.request("GET", f"/v2/tickers/{SYMBOL}")

    def position(self, product_id):
        return self.request(
            "GET",
            "/v2/positions",
            params={"product_id": product_id},
            private=True
        )

    def open_orders(self, product_id):
        return self.request(
            "GET",
            "/v2/orders",
            params={"product_id": product_id, "state": "open"},
            private=True
        )

    def closed_orders(self, product_id):
        return self.request(
            "GET",
            "/v2/orders",
            params={"product_id": product_id, "state": "closed"},
            private=True
        )

    def place_market_order(
        self, product_id, side, size, reduce_only=False, client_order_id=None
    ):
        body = {
            "product_id": int(product_id),
            "product_symbol": SYMBOL,
            "size": int(size),
            "side": side,
            "order_type": "market_order",
            "reduce_only": bool(reduce_only)
        }
        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:32]

        return self.request(
            "POST",
            "/v2/orders",
            body=body,
            private=True
        )


# ============================================================
# OWNER + MEMBER API STATUS
# ============================================================

OWNER_KEY = get_secret("OWNER_API_KEY")
OWNER_SECRET = get_secret("OWNER_API_SECRET")
api = DeltaAPI(OWNER_KEY, OWNER_SECRET)

st.divider()
st.header("👑 OWNER API CONNECTION")

if not OWNER_KEY or not OWNER_SECRET:
    st.session_state["owner_api_connected"] = False
    st.error("OWNER_API_KEY / OWNER_API_SECRET GitHub Secrets में नहीं मिले।")
else:
    owner_test = api.request(
        "GET", "/v2/positions", params={}, private=True
    )
    if owner_test.get("success") is True:
        st.session_state["owner_api_connected"] = True
        st.success("🟢 OWNER API CONNECTED")
    else:
        st.session_state["owner_api_connected"] = False
        err = str(owner_test.get("error", owner_test))
        if "ip" in err.lower() or "whitelist" in err.lower():
            st.error(f"🌐 IP WHITELIST ERROR: {err}")
        else:
            st.error(f"🔴 OWNER API NOT CONNECTED: {err}")

st.subheader("👥 MEMBER API CONTROL (5 MEMBERS)")
members = []
for idx in range(1, 6):
    key = get_secret(f"MEMBER{idx}_API_KEY")
    secret = get_secret(f"MEMBER{idx}_API_SECRET")
    member = {
        "Member": f"Member {idx}",
        "API": "NOT CONNECTED",
        "Trading": "PAUSED"
    }
    if key and secret:
        member_api = DeltaAPI(key, secret)
        check = member_api.request(
            "GET", "/v2/positions", params={}, private=True
        )
        if check.get("success") is True:
            member["API"] = "CONNECTED"
            member["Trading"] = "ACTIVE"
        else:
            member["API"] = "CONNECTION ERROR"
    members.append(member)

st.dataframe(
    pd.DataFrame(members),
    use_container_width=True,
    hide_index=True
)


# ============================================================
# RESOLVE XAUTUSD PRODUCT ID FROM DELTA PUBLIC PRODUCT API
# ============================================================

product_response = api.product()
product = get_result(product_response)

if not isinstance(product, dict) or not product.get("id"):
    st.error(
        "Delta Exchange से XAUTUSD product details नहीं मिल सकीं। "
        f"API response: {product_response}"
    )
    st.stop()

PRODUCT_ID = int(product["id"])
CONTRACT_VALUE = number(product.get("contract_value"), 0.001)
TICK_SIZE = number(product.get("tick_size"), 0.01)
PRODUCT_STATE = str(product.get("state", "unknown")).upper()

st.success(
    f"XAUTUSD PRODUCT VERIFIED | Product ID: {PRODUCT_ID} | "
    f"1 Lot: {CONTRACT_VALUE} XAUT | Tick: {TICK_SIZE} | "
    f"State: {PRODUCT_STATE}"
)


# ============================================================
# CANDLE DATA
# ============================================================

candle_response = api.candles()
candle_result = get_result(candle_response)

if not isinstance(candle_result, list) or not candle_result:
    st.error(f"XAUTUSD candle data नहीं मिला: {candle_response}")
    st.stop()

rows = []
for candle in candle_result:
    try:
        rows.append({
            "time": int(candle["time"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume", 0))
        })
    except Exception:
        continue

df = pd.DataFrame(rows)
if df.empty:
    st.error("Valid candle data उपलब्ध नहीं है।")
    st.stop()

df = (
    df.drop_duplicates("time")
      .sort_values("time")
      .reset_index(drop=True)
)

# Current forming candle is excluded. Signals are confirmed at candle close.
current_candle_start = (int(time.time()) // CANDLE_SECONDS) * CANDLE_SECONDS
df = df[df["time"] < current_candle_start].copy().reset_index(drop=True)

if len(df) < ATR_PERIOD + 5:
    st.error("SuperTrend calculation के लिए पर्याप्त completed candles नहीं हैं।")
    st.stop()


# ============================================================
# STANDARD SUPERTREND 10,2 — HL2 — WILDER ATR
# Direction: -1 = bullish / BUY; +1 = bearish / SELL
# ============================================================

prev_close = df["close"].shift(1)
tr1 = df["high"] - df["low"]
tr2 = (df["high"] - prev_close).abs()
tr3 = (df["low"] - prev_close).abs()
df["TR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

df["ATR"] = float("nan")
df.loc[ATR_PERIOD - 1, "ATR"] = df["TR"].iloc[:ATR_PERIOD].mean()

for i in range(ATR_PERIOD, len(df)):
    df.loc[i, "ATR"] = (
        df.loc[i - 1, "ATR"] * (ATR_PERIOD - 1)
        + df.loc[i, "TR"]
    ) / ATR_PERIOD

df["HL2"] = (df["high"] + df["low"]) / 2.0
for col in ["BASIC_UPPER", "BASIC_LOWER", "FINAL_UPPER", "FINAL_LOWER", "SUPERTREND"]:
    df[col] = float("nan")

df["ST_DIRECTION"] = 0
df["SIGNAL"] = ""

prev_final_upper = None
prev_final_lower = None
prev_supertrend = None
prev_direction = 1

for i in range(len(df)):
    atr = df.loc[i, "ATR"]
    if pd.isna(atr):
        continue

    hl2 = float(df.loc[i, "HL2"])
    close = float(df.loc[i, "close"])

    basic_upper = hl2 + MULTIPLIER * float(atr)
    basic_lower = hl2 - MULTIPLIER * float(atr)

    if prev_final_upper is None:
        final_upper = basic_upper
        final_lower = basic_lower
    else:
        previous_close = float(df.loc[i - 1, "close"])
        final_upper = (
            basic_upper
            if basic_upper < prev_final_upper or previous_close > prev_final_upper
            else prev_final_upper
        )
        final_lower = (
            basic_lower
            if basic_lower > prev_final_lower or previous_close < prev_final_lower
            else prev_final_lower
        )

    if prev_supertrend is None:
        direction = 1
        supertrend = final_upper
    elif prev_supertrend == prev_final_upper:
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

    df.loc[i, "BASIC_UPPER"] = basic_upper
    df.loc[i, "BASIC_LOWER"] = basic_lower
    df.loc[i, "FINAL_UPPER"] = final_upper
    df.loc[i, "FINAL_LOWER"] = final_lower
    df.loc[i, "ST_DIRECTION"] = direction
    df.loc[i, "SUPERTREND"] = supertrend

    if i > 0 and prev_direction != direction:
        df.loc[i, "SIGNAL"] = "BUY" if direction == -1 else "SELL"

    prev_final_upper = final_upper
    prev_final_lower = final_lower
    prev_supertrend = supertrend
    prev_direction = direction

df["M_TREND"] = df["ST_DIRECTION"]
signal_rows = df[df["SIGNAL"].isin(["BUY", "SELL"])].copy()

last_candle = df.iloc[-1]
current_close = float(last_candle["close"])
current_supertrend = float(last_candle["SUPERTREND"])
current_trend = int(last_candle["ST_DIRECTION"])

if current_trend == -1:
    current_direction = "BUY / BULLISH 🟢"
    active_side = "BUY"
else:
    current_direction = "SELL / BEARISH 🔴"
    active_side = "SELL"

if len(signal_rows):
    latest_signal = signal_rows.iloc[-1]
    signal_direction = str(latest_signal["SIGNAL"])
    signal_entry_price = float(latest_signal["close"])
    signal_time_epoch = int(latest_signal["time"])
    signal_time = indian_time(signal_time_epoch)
    signal_st = float(latest_signal["SUPERTREND"])
else:
    signal_direction = ""
    signal_entry_price = current_close
    signal_time_epoch = None
    signal_time = "-"
    signal_st = current_supertrend

if len(signal_rows) >= 2:
    previous_signal = signal_rows.iloc[-2]
    previous_signal_direction = str(previous_signal["SIGNAL"])
    previous_signal_price = float(previous_signal["close"])
    previous_signal_time = indian_time(previous_signal["time"])
else:
    previous_signal_direction = ""
    previous_signal_price = None
    previous_signal_time = "-"


# ============================================================
# SUPERTREND SIGNAL DASHBOARD
# ============================================================

st.divider()
st.header("📈 SUPERTREND — XAUTUSD 1 MINUTE")

m1, m2, m3, m4 = st.columns(4)
m1.metric("CURRENT DIRECTION", current_direction)
m2.metric("LAST COMPLETED CLOSE", show_price(current_close))
m3.metric("SUPERTREND LINE", show_price(current_supertrend))
m4.metric("SIGNAL ENTRY PRICE", show_price(signal_entry_price))

if signal_direction == "BUY":
    st.success(
        f"🟢 LAST CONFIRMED SIGNAL: BUY | "
        f"Entry: {show_price(signal_entry_price)} | {signal_time}"
    )
elif signal_direction == "SELL":
    st.error(
        f"🔴 LAST CONFIRMED SIGNAL: SELL | "
        f"Entry: {show_price(signal_entry_price)} | {signal_time}"
    )
else:
    st.info("अभी कोई confirmed BUY/SELL signal उपलब्ध नहीं है।")

st.write(f"**Previous Signal:** {previous_signal_direction or '-'}")
st.write(f"**Previous Signal Price:** {show_price(previous_signal_price)}")
st.write(f"**Previous Signal Time:** {previous_signal_time}")

st.subheader("📜 Recent Confirmed Signals")
if not signal_rows.empty:
    history = signal_rows.tail(15).copy()
    history["Time (IST)"] = history["time"].apply(indian_time)
    history["Entry Price"] = history["close"].map(show_price)
    history["SuperTrend"] = history["SUPERTREND"].map(show_price)
    st.dataframe(
        history[["Time (IST)", "SIGNAL", "Entry Price", "SuperTrend"]],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("Signal history अभी उपलब्ध नहीं है।")


# ============================================================
# POSITION / ORDER HELPERS
# ============================================================

def get_xaut_position():
    response = api.position(PRODUCT_ID)
    if response.get("success") is not True:
        return None, response

    result = get_result(response)
    if isinstance(result, list):
        matching = [
            p for p in result
            if int(p.get("product_id", PRODUCT_ID)) == PRODUCT_ID
        ]
        return (matching[0] if matching else {}), response
    if isinstance(result, dict):
        return result, response
    return {}, response


def place_market(side, size, reduce_only, signal_epoch, purpose):
    client_id = f"SRX_{purpose}_{side}_{int(signal_epoch)}"
    return api.place_market_order(
        PRODUCT_ID,
        side,
        size,
        reduce_only=reduce_only,
        client_order_id=client_id
    )


def close_position_if_needed(position, new_side, signal_epoch):
    size = number(position.get("size"), 0)
    if size == 0:
        return True, "कोई पुरानी position खुली नहीं है।"

    old_side = "BUY" if size > 0 else "SELL"
    if old_side == new_side:
        return True, "Position पहले से सही दिशा में है; अतिरिक्त order नहीं भेजा।"

    close_side = "sell" if size > 0 else "buy"
    close_result = place_market(
        close_side,
        abs(int(size)),
        True,
        signal_epoch,
        "CLOSE"
    )

    if close_result.get("success") is not True:
        return False, f"पुरानी {old_side} position close नहीं हुई: {close_result}"

    # Confirm flat before opening the reverse position.
    for _ in range(5):
        time.sleep(1)
        refreshed, check_response = get_xaut_position()
        if refreshed is None:
            continue
        refreshed_size = number(refreshed.get("size"), 0)
        if refreshed_size == 0:
            return True, f"पुरानी {old_side} position सफलतापूर्वक बंद।"

    return False, (
        "Close order accepted, लेकिन Delta position flat होने की पुष्टि "
        "नहीं मिली। नई reverse entry सुरक्षा के लिए रोक दी गई।"
    )


# ============================================================
# REAL TRADING ENGINE — ONLY ON A NEW CONFIRMED SIGNAL
# ============================================================

st.divider()
st.header("🤖 XAUTUSD REAL TRADING ENGINE")

st.info(
    "नियम: BUY signal पर 1 lot BUY; SELL signal पर 1 lot SELL. "
    "Opposite signal पर पुरानी position market से पूरी close होगी, "
    "फिर नई direction में 1 lot market entry होगी. कोई Target या Stop Loss नहीं।"
)

if "strategy_last_processed_signal" not in st.session_state:
    # On first launch, use the latest signal as a baseline only.
    # This prevents an old historical signal from unexpectedly opening a trade.
    st.session_state["strategy_last_processed_signal"] = (
        int(signal_time_epoch) if signal_time_epoch is not None else 0
    )
    st.session_state["strategy_started_at"] = time.time()
    st.session_state["strategy_last_action"] = (
        "Dashboard started. Waiting for the next new confirmed signal."
    )

new_signal = (
    signal_time_epoch is not None
    and int(signal_time_epoch) > int(
        st.session_state.get("strategy_last_processed_signal", 0)
    )
)

if not REMOTE_TRADING:
    st.warning("REMOTE_TRADING=false है — वास्तविक orders बंद हैं।")
elif not st.session_state.get("owner_api_connected", False):
    st.error("Owner API connected नहीं है — real trading रोक दी गई।")
elif PRODUCT_STATE != "LIVE":
    st.error(f"XAUTUSD product LIVE नहीं है (State: {PRODUCT_STATE}) — trading रोक दी गई।")
elif new_signal:
    st.warning(
        f"नया confirmed {signal_direction} signal मिला: "
        f"{signal_time} | Signal close: {show_price(signal_entry_price)}"
    )

    current_position, position_response = get_xaut_position()
    if current_position is None:
        st.error(
            "Position verify नहीं हो सकी; कोई नया order नहीं भेजा गया। "
            f"Details: {position_response}"
        )
    else:
        close_ok, close_message = close_position_if_needed(
            current_position,
            signal_direction,
            signal_time_epoch
        )
        st.write(close_message)

        if close_ok:
            # Re-check the position after close logic.
            flat_position, flat_response = get_xaut_position()
            if flat_position is None:
                st.error(
                    "नई entry से पहले position verification fail हुई; "
                    "नई entry रोक दी गई।"
                )
            else:
                flat_size = number(flat_position.get("size"), 0)
                if flat_size != 0:
                    st.error(
                        "Position अभी भी खुली है; duplicate/hedged entry रोक दी गई।"
                    )
                else:
                    entry_side = "buy" if signal_direction == "BUY" else "sell"
                    entry_result = place_market(
                        entry_side,
                        ORDER_LOTS,
                        False,
                        signal_time_epoch,
                        "ENTRY"
                    )
                    if entry_result.get("success") is True:
                        st.success(
                            f"✅ REAL {signal_direction} MARKET ENTRY SENT — "
                            f"1 LOT | Signal close {show_price(signal_entry_price)}"
                        )
                        st.session_state["strategy_last_action"] = (
                            f"{signal_direction} 1 lot market order accepted "
                            f"for signal {signal_time}"
                        )
                    else:
                        st.error(f"नई {signal_direction} entry fail हुई: {entry_result}")

    # Mark this signal as processed whether accepted or blocked, so refresh
    # cannot repeatedly submit the same signal.
    st.session_state["strategy_last_processed_signal"] = int(signal_time_epoch)

elif signal_time_epoch is not None:
    st.caption(
        "इस signal को पहले baseline/processed माना गया है। "
        "अगले नए confirmed SuperTrend signal पर ही order भेजा जाएगा।"
    )

st.write(
    "**Last Engine Action:** "
    + str(st.session_state.get("strategy_last_action", "-"))
)


# ============================================================
# REAL POSITION
# ============================================================

st.divider()
st.header("📍 REAL XAUTUSD POSITION")

position, position_response = get_xaut_position()
if position is None:
    st.error(f"Position data नहीं मिली: {position_response}")
    position = {}

position_size = number(position.get("size"), 0)
position_entry = number(position.get("entry_price"))
position_pnl = number(position.get("unrealized_pnl"), 0)

if position_size > 0:
    position_side = "LONG / BUY 🟢"
elif position_size < 0:
    position_side = "SHORT / SELL 🔴"
else:
    position_side = "FLAT ⚪"

p1, p2, p3, p4 = st.columns(4)
p1.metric("POSITION", position_side)
p2.metric("SIZE (LOTS)", str(abs(int(position_size))))
p3.metric("ENTRY PRICE", show_price(position_entry))
p4.metric("UNREALIZED P&L", f"{position_pnl:,.2f}")


# ============================================================
# LIVE PRICE + OPEN ORDERS + ORDER HISTORY
# ============================================================

st.divider()
st.header("💰 LIVE XAUTUSD MARKET PRICE")

ticker_response = api.ticker()
ticker = get_result(ticker_response)
live_price = None
if isinstance(ticker, dict):
    for key in ("close", "last_price", "mark_price", "spot_price"):
        live_price = number(ticker.get(key))
        if live_price is not None:
            break

st.metric("XAUTUSD", show_price(live_price))

st.subheader("📋 OPEN ORDERS")
open_response = api.open_orders(PRODUCT_ID)
open_orders = safe_result_list(open_response)
if open_response.get("success") is True and open_orders:
    order_rows = []
    for order in open_orders:
        order_rows.append({
            "Order ID": order.get("id", "-"),
            "Side": str(order.get("side", "-")).upper(),
            "Type": order.get("order_type", "-"),
            "Price": show_price(order.get("limit_price")),
            "Size (Lots)": order.get("size", "-"),
            "Reduce Only": order.get("reduce_only", False),
            "State": order.get("state", "-")
        })
    st.dataframe(pd.DataFrame(order_rows), use_container_width=True, hide_index=True)
elif open_response.get("success") is True:
    st.info("कोई open order नहीं है।")
else:
    st.error(f"Open orders check नहीं हो सका: {open_response}")

st.subheader("🧾 RECENT ORDER HISTORY")
closed_response = api.closed_orders(PRODUCT_ID)
closed_orders = safe_result_list(closed_response)
if closed_response.get("success") is True and closed_orders:
    history_rows = []
    for order in closed_orders[-30:]:
        history_rows.append({
            "Order ID": order.get("id", "-"),
            "Side": str(order.get("side", "-")).upper(),
            "Type": order.get("order_type", "-"),
            "Size (Lots)": order.get("size", "-"),
            "Average Fill": show_price(
                order.get("average_fill_price")
                or order.get("avg_fill_price")
                or order.get("fill_price")
            ),
            "Reduce Only": order.get("reduce_only", False),
            "State": order.get("state", "-"),
            "Client Order ID": order.get("client_order_id", "-")
        })
    st.dataframe(
        pd.DataFrame(history_rows[::-1]),
        use_container_width=True,
        hide_index=True
    )
elif closed_response.get("success") is True:
    st.info("Closed order history उपलब्ध नहीं है।")
else:
    st.error(f"Order history check नहीं हो सकी: {closed_response}")


# ============================================================
# TRADING SUMMARY
# ============================================================

st.divider()
st.header("📊 TRADING SUMMARY")

summary_rows = [
    {"Item": "Trading Pair", "Value": SYMBOL},
    {"Item": "Product ID", "Value": PRODUCT_ID},
    {"Item": "Timeframe", "Value": "1 Minute"},
    {"Item": "SuperTrend", "Value": "ATR 10 | Multiplier 2.0 | HL2"},
    {"Item": "Latest Signal", "Value": signal_direction or "-"},
    {"Item": "Signal Candle Close", "Value": show_price(signal_entry_price)},
    {"Item": "Signal Time", "Value": signal_time},
    {"Item": "Order Size", "Value": "1 Lot per entry"},
    {"Item": "Exit Rule", "Value": "Only on opposite confirmed signal"},
    {"Item": "Target / Stop Loss", "Value": "None"},
    {"Item": "Remote Trading", "Value": "ON" if REMOTE_TRADING else "OFF"},
    {"Item": "Current Position", "Value": position_side},
    {"Item": "Current Position Size", "Value": abs(int(position_size))}
]
st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)


# ============================================================
# TRADINGVIEW CHART — XAUTUSD
# ============================================================

st.divider()
st.header("📉 XAUTUSD CHART — 1 MINUTE")

components.html(
    """
    <div class="tradingview-widget-container" style="height:700px;width:100%">
      <div class="tradingview-widget-container__widget" style="height:700px;width:100%"></div>
      <script type="text/javascript"
        src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
        async>
      {
        "autosize": true,
        "symbol": "DELTA:XAUTUSD",
        "interval": "1",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "studies": [
          {
            "id": "SuperTrend@tv-basicstudies",
            "inputs": {
              "length": 10,
              "factor": 2
            }
          }
        ],
        "enable_publishing": false,
        "allow_symbol_change": false,
        "hide_top_toolbar": false,
        "hide_legend": false,
        "save_image": false,
        "hide_volume": false,
        "support_host": "https://www.tradingview.com"
      }
      </script>
    </div>
    """,
    height=710,
    scrolling=False
)


# ============================================================
# STATUS + REFRESH
# ============================================================

now_ist = datetime.now(timezone.utc).astimezone(IST)
st.caption(
    f"Dashboard Time: {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')} | "
    f"Auto Refresh: {REFRESH_SECONDS} seconds"
)

st.warning(
    "REAL TRADING: यह dashboard वास्तविक XAUTUSD market orders भेजता है। "
    "Position reversal से पहले पुरानी position close और flat verification "
    "की जाती है। पहली बार dashboard चालू होने पर पुराने historical signal "
    "पर trade नहीं खुलेगी; अगला नया confirmed signal आने पर trading शुरू होगी।"
)

time.sleep(REFRESH_SECONDS)
st.rerun()
