# ============================================================
# SANJAY RANA - DELTA REAL TRADING DASHBOARD
# PART 1/4
# ============================================================

import os
import time
import json
import sqlite3
import hmac
import hashlib
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# SETTINGS
# ============================================================

BASE_URL = os.getenv(
    "DELTA_BASE_URL",
    "https://api.india.delta.exchange"
).rstrip("/")

SYMBOL = os.getenv("DELTA_SYMBOL", "BTCUSD")
PRODUCT_ID = int(os.getenv("DELTA_PRODUCT_ID", "27"))

# ============================================================
# TRADING TIMEFRAME — ENTIRE TRADING ENGINE IS 1-MINUTE
# A signal/order is evaluated only after a 1-minute candle closes.
# ============================================================
TIMEFRAME = "5m"
CANDLE_SECONDS = 300

ATR_PERIOD = 10
MULTIPLIER = 3.0

REFRESH_SECONDS = 1

# ============================================================
# REAL TRADING MASTER SWITCH
# ============================================================

REMOTE_TRADING = False  # Trading is handled only by trading_worker.py

# ============================================================
# DEFAULT REMOTE CONTROL SETTINGS
# ============================================================

DEFAULT_BUY_OFFSET = int(
    os.getenv("BUY_OFFSET", "-10")
)

DEFAULT_SELL_OFFSET = int(
    os.getenv("SELL_OFFSET", "10")
)

# ============================================================
# MASTER BTC QUANTITY — ONLY THIS VALUE NEEDS TO BE CHANGED
# 0.001 = 1 contract = 1 TP basket
# 0.010 = 10 contracts = 3 baskets: 0.005 / 0.003 / 0.002 BTC
# ============================================================
ORDER_QTY = float(
    os.getenv("ORDER_QTY", "0.001")
)

CONTRACT_BTC = 0.001

if ORDER_QTY <= 0:
    raise ValueError("ORDER_QTY must be greater than 0")

_total_contracts = ORDER_QTY / CONTRACT_BTC
if abs(_total_contracts - round(_total_contracts)) > 1e-9:
    raise ValueError("ORDER_QTY must be a multiple of 0.001 BTC")

DEFAULT_ORDER_SIZE = int(round(_total_contracts))

# LIMIT pending रहने के बाद कितने seconds में MARKET करना है.
# 0 = automatic MARKET conversion बंद.
DEFAULT_LIMIT_TIMEOUT = int(
    os.getenv("LIMIT_TIMEOUT", "6000000")
)

TARGET_1 = int(os.getenv("TARGET_1", "300"))
TARGET_2 = int(os.getenv("TARGET_2", "600"))
TARGET_3 = int(os.getenv("TARGET_3", "900"))


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="Sanjay Rana Real Trading",
    page_icon="📈",
    layout="wide"
)

st.title("📈 SANJAY RANA — REAL TRADING DASHBOARD")

st.caption(
    "5 Minute | ATR 10 | Multiplier 3.0 | HL2 | "
    "Confirmed Candle Close"
)


# ============================================================
# INDIAN TIME
# ============================================================

IST = timezone(timedelta(hours=5, minutes=30))


def indian_time(timestamp):
    try:
        ts = int(float(timestamp))

        if ts > 10_000_000_000:
            ts = ts // 1000

        return datetime.fromtimestamp(
            ts,
            tz=timezone.utc
        ).astimezone(IST).strftime(
            "%Y-%m-%d %H:%M:%S IST"
        )

    except Exception:
        return "-"


# ============================================================
# DISPLAY HELPERS
# ============================================================

def number(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def show_price(value):
    value = number(value)

    if value is None:
        return "-"

    return f"{value:,.2f}"


# ============================================================
# ADDITIVE ORDER LIFECYCLE TRACKING
# Existing trading logic is intentionally left unchanged.
# ============================================================

def _order_epoch(value):
    if value is None:
        return None
    try:
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        return ts
    except Exception:
        return None


def _order_event_epoch(order, keys):
    for key in keys:
        if key in order and order.get(key) not in [None, ""]:
            ts = _order_epoch(order.get(key))
            if ts is not None:
                return ts
    return None


def _format_epoch(ts):
    if ts is None:
        return "-"
    return indian_time(ts)


def _duration_text(seconds):
    if seconds is None:
        return "-"
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "-"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m {secs}s"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# ============================================================
# PERMANENT ORDER LEDGER (ADDITIVE)
# Keeps dashboard order history across Streamlit restarts.
# No API credentials are stored here.
# ============================================================
ORDER_HISTORY_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "order_lifecycle_history.sqlite3"
)

def _order_history_db_init():
    try:
        conn = sqlite3.connect(ORDER_HISTORY_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_lifecycle (
                order_id TEXT PRIMARY KEY,
                product_id INTEGER,
                client_order_id TEXT,
                kind TEXT,
                basket TEXT,
                signal TEXT,
                side TEXT,
                qty REAL,
                tp REAL,
                limit_price REAL,
                sent_epoch REAL,
                sent_text TEXT,
                exchange_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _order_history_db_upsert(order_id, tracking=None, exchange_order=None):
    if order_id is None:
        return
    try:
        _order_history_db_init()
        conn = sqlite3.connect(ORDER_HISTORY_DB)
        old = conn.execute(
            "SELECT * FROM order_lifecycle WHERE order_id=?",
            (str(order_id),)
        ).fetchone()
        cols = [
            "order_id", "product_id", "client_order_id", "kind",
            "basket", "signal", "side", "qty", "tp", "limit_price",
            "sent_epoch", "sent_text", "exchange_json"
        ]
        row = dict(zip(cols, old)) if old else {}
        tracking = tracking or {}
        exchange_order = exchange_order or {}

        def pick(name, default=None):
            if name in tracking and tracking.get(name) not in [None, ""]:
                return tracking.get(name)
            if name in exchange_order and exchange_order.get(name) not in [None, ""]:
                return exchange_order.get(name)
            return row.get(name, default)

        payload = dict(row)
        payload.update({
            "order_id": str(order_id),
            "product_id": pick("product_id", PRODUCT_ID),
            "client_order_id": pick("client_order_id", ""),
            "kind": pick("kind", "ENTRY"),
            "basket": pick("basket", "-"),
            "signal": pick("signal", ""),
            "side": pick("side", exchange_order.get("side", "")),
            "qty": pick("qty", exchange_order.get("size", 0)),
            "tp": pick("tp", None),
            "limit_price": pick("limit_price", exchange_order.get("limit_price")),
            "sent_epoch": pick("sent_epoch", None),
            "sent_text": pick("sent_text", _format_epoch(pick("sent_epoch", None))),
            "exchange_json": json.dumps(exchange_order or {}, default=str),
        })
        conn.execute("""
            INSERT INTO order_lifecycle
            (order_id, product_id, client_order_id, kind, basket, signal, side, qty, tp, limit_price, sent_epoch, sent_text, exchange_json)
            VALUES (:order_id,:product_id,:client_order_id,:kind,:basket,:signal,:side,:qty,:tp,:limit_price,:sent_epoch,:sent_text,:exchange_json)
            ON CONFLICT(order_id) DO UPDATE SET
                product_id=excluded.product_id, client_order_id=excluded.client_order_id,
                kind=excluded.kind, basket=excluded.basket, signal=excluded.signal,
                side=excluded.side, qty=excluded.qty, tp=excluded.tp,
                limit_price=excluded.limit_price, sent_epoch=COALESCE(order_lifecycle.sent_epoch, excluded.sent_epoch),
                sent_text=COALESCE(order_lifecycle.sent_text, excluded.sent_text),
                exchange_json=excluded.exchange_json
        """, payload)
        conn.commit()
        conn.close()
    except Exception:
        pass


def _order_history_db_load():
    rows = []
    try:
        _order_history_db_init()
        conn = sqlite3.connect(ORDER_HISTORY_DB)
        data = conn.execute("SELECT * FROM order_lifecycle").fetchall()
        cols = [d[1] for d in conn.execute("PRAGMA table_info(order_lifecycle)").fetchall()]
        conn.close()
        for r in data:
            item = dict(zip(cols, r))
            try:
                item["exchange_order"] = json.loads(item.get("exchange_json") or "{}")
            except Exception:
                item["exchange_order"] = {}
            rows.append(item)
    except Exception:
        pass
    return rows


_order_history_db_init()


def _ensure_order_tracking():
    if "order_tracking" not in st.session_state:
        st.session_state["order_tracking"] = {}
    return st.session_state["order_tracking"]


def _remember_sent_order(order_id, data):
    if order_id is None:
        return
    tracking = _ensure_order_tracking()
    tracking[str(order_id)] = dict(data)
    _order_history_db_upsert(order_id, tracking=tracking[str(order_id)])


def _order_status_label(order):
    state = str(order.get("state", "")).lower()
    if state in {"filled", "closed"}:
        return "EXECUTED / CLOSED"
    if state in {"cancelled", "canceled"}:
        return "CANCELLED"
    if state in {"rejected", "failed"}:
        return "REJECTED"
    if state in {"open", "pending", "active", "partially_filled"}:
        return "PENDING"
    return str(order.get("state", "-")).upper()


def _order_sent_epoch(order):
    return _order_event_epoch(
        order,
        (
            "created_at_ts",
            "created_at",
            "created_time",
            "timestamp"
        )
    )


def _order_final_epoch(order):
    return _order_event_epoch(
        order,
        (
            "filled_at",
            "executed_at",
            "closed_at",
            "cancelled_at",
            "canceled_at",
            "updated_at"
        )
    )


# ============================================================
# DELTA API
# ============================================================

class DeltaAPI:

    def __init__(self, api_key=None, api_secret=None):
        global API_KEY, API_SECRET
        
        if api_key:
            API_KEY = api_key
        if api_secret:
            API_SECRET = api_secret

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": "Sanjay-Rana-Real-Trading-Bot",
            "Accept": "application/json"
        })
        



    # --------------------------------------------------------
    # HMAC SIGNATURE
    # --------------------------------------------------------

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
            API_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()


    # --------------------------------------------------------
    # REQUEST
    # --------------------------------------------------------

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

            parts = []

            for key, value in params.items():

                parts.append(
                    f"{key}={value}"
                )

            query_string = "?" + "&".join(parts)


        headers = {
            "Accept": "application/json",
            "User-Agent": "Sanjay-Rana-Real-Trading-Bot"
        }


        if private:

            if not API_KEY or not API_SECRET:

                return {
                    "success": False,
                    "error": "API key/secret missing"
                }


            timestamp = str(
                int(time.time())
            )


            signature = self.make_signature(
                method,
                timestamp,
                path,
                query_string,
                payload
            )


            headers.update({
                "api-key": API_KEY,
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
                data = response.json()

            except Exception:

                return {
                    "success": False,
                    "error": response.text
                }


            return data


        except Exception as e:

            return {
                "success": False,
                "error": str(e)
            }


    # --------------------------------------------------------
    # PUBLIC
    # --------------------------------------------------------

    def candles(self):

        end = int(time.time())

        start = (
            end
            - (500 * CANDLE_SECONDS)
        )

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

        return self.request(
            "GET",
            f"/v2/tickers/{SYMBOL}"
        )


    # --------------------------------------------------------
    # PRIVATE
    # --------------------------------------------------------

    def open_orders(self):

        return self.request(
            "GET",
            "/v2/orders",
            params={
                "product_id": PRODUCT_ID,
                "state": "open"
            },
            private=True
        )


    def closed_orders(self):

        return self.request(
            "GET",
            "/v2/orders",
            params={
                "product_id": PRODUCT_ID,
                "state": "closed"
            },
            private=True
        )


    def position(self):

        return self.request(
            "GET",
            "/v2/positions",
            params={
                "product_id": PRODUCT_ID
            },
            private=True
        )


    # --------------------------------------------------------
    # PLACE LIMIT ORDER
    # --------------------------------------------------------

    def place_limit_order(
        self,
        side,
        size,
        limit_price,
        client_order_id=None
    ):
        # NO TAKE-PROFIT / NO BRACKET:
        # The separate always-on trading worker manages entries and
        # closes the position only on a confirmed SuperTrend direction change.
        body = {
            "product_id": PRODUCT_ID,
            "product_symbol": SYMBOL,
            "limit_price": str(limit_price),
            "size": int(size),
            "side": side,
            "order_type": "limit_order",
            "time_in_force": "gtc",
            "post_only": False,
            "reduce_only": False
        }

        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:32]

        return self.request(
            "POST",
            "/v2/orders",
            body=body,
            private=True
        )


    # --------------------------------------------------------
    # MARKET REDUCE-ONLY ORDER — CLOSE OPPOSITE POSITION
    # --------------------------------------------------------

    def place_market_reduce_only(self, side, size, client_order_id=None):

        body = {
            "product_id": PRODUCT_ID,
            "product_symbol": SYMBOL,
            "size": int(abs(size)),
            "side": side,
            "order_type": "market_order",
            "reduce_only": True,
        }

        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:32]

        return self.request(
            "POST",
            "/v2/orders",
            body=body,
            private=True
        )


    # --------------------------------------------------------
    # CANCEL ORDER
    # --------------------------------------------------------

    def cancel_order(self, order_id):

        return self.request(
            "DELETE",
            f"/v2/orders/{order_id}",
            private=True
        )


# ============================================================
# API CREDENTIALS (OWNER & MEMBERS FROM GITHUB SECRETS)
# ============================================================
# यह कोड GitHub Secrets से कीज़ खुद ले लेता है
OWNER_KEY = st.secrets.get("OWNER_API_KEY", "")
OWNER_SECRET = st.secrets.get("OWNER_API_SECRET", "")

MEMBER1_KEY = st.secrets.get("MEMBER1_API_KEY", "")
MEMBER1_SECRET = st.secrets.get("MEMBER1_API_SECRET", "")



API_KEY = OWNER_KEY
API_SECRET = OWNER_SECRET

api = DeltaAPI()



# ============================================================
# BASIC STATUS (PERMANENTLY LIVE)
# ============================================================

st.info(
    "⚡ LIVE TRADING MODE: PERMANENTLY ACTIVE"
)

    # ============================================================
# OWNER API + MEMBER API CONTROL
# PLACE THIS DIRECTLY BELOW PART 1
# ============================================================

st.divider()

# ============================================================
# OWNER API
# ============================================================

st.header("👑 OWNER API")

# Credentials are loaded automatically from environment / GitHub Secrets
# ============================================================
# OWNER API CREDENTIALS (SECURE FETCH)
# ============================================================
try:
    OWNER_API_KEY = st.secrets.get("OWNER_API_KEY", os.getenv("OWNER_API_KEY", ""))
    OWNER_API_SECRET = st.secrets.get("OWNER_API_SECRET", os.getenv("OWNER_API_SECRET", ""))
except Exception:
    OWNER_API_KEY = os.getenv("OWNER_API_KEY", "")
    OWNER_API_SECRET = os.getenv("OWNER_API_SECRET", "")
    

# ============================================================
# OWNER STATUS (AUTOMATIC HEALTH CHECK)
# ============================================================

if not OWNER_API_KEY or not OWNER_API_SECRET:
    st.error("❌ OWNER_API_KEY / OWNER_API_SECRET GitHub Secrets में नहीं मिले।")
    st.session_state["owner_api_connected"] = False
else:
    try:
        owner_client = DeltaAPI(OWNER_API_KEY, OWNER_API_SECRET)
        owner_result = owner_client.position()
        
        if owner_result.get("success"):
            st.success("👑 Owner Status: CONNECTED & LIVE 🟢")
            st.session_state["owner_api_connected"] = True
            st.session_state["owner_api_key"] = OWNER_API_KEY
            st.session_state["owner_api_secret"] = OWNER_API_SECRET
        else:
            st.session_state["owner_api_connected"] = False
            err_text = str(owner_result.get("error", ""))
            
            if "ip" in err_text.lower() or "whitelist" in err_text.lower():
                st.error(f"🌐 IP WHITELIST ERROR: Streamlit Cloud का IP Delta Exchange पर जोड़ा नहीं है! | Details: {err_text}")
            else:
                st.error(f"🔴 Owner Status: NOT CONNECTED | Reason: {err_text}")
                
    except Exception as e:
        st.session_state["owner_api_connected"] = False
        st.error(f"❌ Owner API Connection Error: {e}")



# ============================================================
# CONNECTION DISCONNECT / RECONNECT TIME TRACKER
# ============================================================
# यह केवल connection status का monitoring block है।
# Existing trading/order logic को नहीं बदलता।

_now_connection_ist = datetime.now(timezone.utc).astimezone(IST)
_now_connection_text = _now_connection_ist.strftime("%Y-%m-%d %H:%M:%S IST")

if "connection_was_connected" not in st.session_state:
    st.session_state["connection_was_connected"] = None

if "disconnect_started_at" not in st.session_state:
    st.session_state["disconnect_started_at"] = ""

if "last_reconnected_at" not in st.session_state:
    st.session_state["last_reconnected_at"] = ""

if "last_connection_check_at" not in st.session_state:
    st.session_state["last_connection_check_at"] = ""

_current_connection_ok = bool(
    st.session_state.get("owner_api_connected", False)
)

if _current_connection_ok:
    # अगर पहले disconnect था और अब connection वापस आया है
    if (
        st.session_state["connection_was_connected"] is False
        and st.session_state["disconnect_started_at"]
    ):
        st.session_state["last_reconnected_at"] = _now_connection_text

    st.session_state["connection_was_connected"] = True
    st.session_state["last_connection_check_at"] = _now_connection_text

else:
    # पहली बार disconnect detect होने का exact समय
    if st.session_state["connection_was_connected"] is not False:
        st.session_state["disconnect_started_at"] = _now_connection_text

    st.session_state["connection_was_connected"] = False
    st.session_state["last_connection_check_at"] = _now_connection_text

st.divider()
st.subheader("📡 CONNECTION DISCONNECT MONITOR")

if _current_connection_ok:
    st.success("🟢 CONNECTION: CONNECTED")

    st.write(
        "Last Successful Connection: "
        f"**{st.session_state['last_connection_check_at']}**"
    )

    if st.session_state["last_reconnected_at"]:
        st.info(
            "🟢 RECONNECTED AT: "
            f"**{st.session_state['last_reconnected_at']}**"
        )

        if st.session_state["disconnect_started_at"]:
            st.write(
                "Previous Disconnect Started At: "
                f"**{st.session_state['disconnect_started_at']}**"
            )
    else:
        st.write(
            "Disconnect History: **No disconnect detected in this session**"
        )

else:
    st.error("🔴 CONNECTION: DISCONNECTED")

    st.write(
        "Disconnected At: "
        f"**{st.session_state['disconnect_started_at']}**"
    )

    st.write(
        "Last Connection Check: "
        f"**{st.session_state['last_connection_check_at']}**"
    )


# ============================================================
# FIXED 5 MEMBERS PRE-CONFIGURED (AUTOMATIC HEALTH CHECK)
# ============================================================

st.divider()
st.header("👥 MEMBER API CONTROL (5 MEMBERS)")

# 5 फिक्स मेंबर्स सीधे GitHub Secrets से लोड होंगे
st.session_state["members"] = [
    {
        "name": "Member 1",
        "api_key": st.secrets.get("MEMBER1_API_KEY", os.getenv("MEMBER1_API_KEY", "")),
        "api_secret": st.secrets.get("MEMBER1_API_SECRET", os.getenv("MEMBER1_API_SECRET", "")),
        "connected": False,
        "active": True
    },
    {
        "name": "Member 2",
        "api_key": st.secrets.get("MEMBER2_API_KEY", os.getenv("MEMBER2_API_KEY", "")),
        "api_secret": st.secrets.get("MEMBER2_API_SECRET", os.getenv("MEMBER2_API_SECRET", "")),
        "connected": False,
        "active": True
    },
    {
        "name": "Member 3",
        "api_key": st.secrets.get("MEMBER3_API_KEY", os.getenv("MEMBER3_API_KEY", "")),
        "api_secret": st.secrets.get("MEMBER3_API_SECRET", os.getenv("MEMBER3_API_SECRET", "")),
        "connected": False,
        "active": True
    },
    {
        "name": "Member 4",
        "api_key": st.secrets.get("MEMBER4_API_KEY", os.getenv("MEMBER4_API_KEY", "")),
        "api_secret": st.secrets.get("MEMBER4_API_SECRET", os.getenv("MEMBER4_API_SECRET", "")),
        "connected": False,
        "active": True
    },
    {
        "name": "Member 5",
        "api_key": st.secrets.get("MEMBER5_API_KEY", os.getenv("MEMBER5_API_KEY", "")),
        "api_secret": st.secrets.get("MEMBER5_API_SECRET", os.getenv("MEMBER5_API_SECRET", "")),
        "connected": False,
        "active": True
    }
]

st.info("👥 Total Pre-configured Members: 5 (Automatic Live Sync Enabled)")

# पांचों मेंबर्स का ऑटोमैटिक हेल्थ चेक लूप (बिना किसी बटन के)
for index, member in enumerate(st.session_state["members"]):
    st.markdown("---")
    st.subheader(f"👤 {member['name']}")

    if not member["api_key"] or not member["api_secret"]:
        member["connected"] = False
        st.error(f"❌ {member['name']}: GitHub Secrets में API Key या Secret गायब है (`MEMBER{index+1}_API_KEY`).")
    else:
        try:
            member_client = DeltaAPI(member["api_key"], member["api_secret"])
            member_result = member_client.position()
            
            if member_result.get("success"):
                member["connected"] = True
                st.success(f"🟢 {member['name']} — API CONNECTED & LIVE")
            else:
                member["connected"] = False
                err_text = str(member_result.get("error", ""))
                
                if "ip" in err_text.lower() or "whitelist" in err_text.lower():
                    st.error(f"🌐 IP WHITELIST ERROR ({member['name']}): Streamlit IP Delta पर जोड़ी नहीं है! | {err_text}")
                else:
                    st.error(f"🔴 {member['name']} — NOT CONNECTED | Reason: {err_text}")
                    
        except Exception as e:
        #   member["connected"] = False
            st.error(f"❌ {member['name']} API Error: {e}")

    if member["connected"]:
        st.write(f"Status: **REAL TRADING ACTIVE (AUTO)** 🚀")
    else:
        st.write(f"Status: **TRADING PAUSED (Check Secrets / IP)** ⚠️")


# ============================================================
# ACTIVE MEMBER SUMMARY TABLE
# ============================================================

st.divider()
st.subheader("📊 MEMBER SUMMARY")

summary = []
for member in st.session_state["members"]:
    summary.append({
        "Member": member["name"],
        "API": "CONNECTED" if member["connected"] else "NOT CONNECTED",
        "Trading": "ACTIVE" if member["active"] else "OFF"
    })

st.dataframe(
    pd.DataFrame(summary),
    use_container_width=True,
    hide_index=True
)



# ============================================================
# END — OWNER + MEMBER API BLOCK
#
# PART 2 इसके नीचे आएगा।
# PART 3 इसके बाद।
# PART 4 सबसे बाद।
#
# FINAL st.rerun() पूरी file के बिल्कुल अंत में रहेगा।
# ============================================================
# ============================================================
# PART 2/4
# CANDLE DATA + SUPERTREND ENGINE
# ============================================================

def get_result(data):

    if not data:
        return None

    if not data.get("success"):
        return None

    return data.get("result")


def make_dataframe(data):

    result = get_result(data)

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
                "volume": float(candle.get("volume", 0))
            })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = (
        df
        .drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# GET CANDLES
# ============================================================

candle_response = api.candles()

df = make_dataframe(candle_response)

if df.empty:
    st.error("Delta se candle data nahi mila.")
    st.stop()


# ============================================================
# ONLY COMPLETED 1-MINUTE CANDLES
# ============================================================

current_candle_start = (
    int(time.time()) // CANDLE_SECONDS
) * CANDLE_SECONDS

df = df[
    df["time"] < current_candle_start
].copy()

df = df.reset_index(drop=True)

if len(df) < ATR_PERIOD + 5:
    st.error("SuperTrend ke liye enough candles nahi hain.")
    st.stop()


# ============================================================
# ============================================================
# STANDARD TRADINGVIEW SUPERTREND 10,3 ENGINE
# ============================================================
# Reference chart: regular Delta BTCUSD 1-minute candles,
# SuperTrend 10 3, Source = HL2.
#
# This is NOT Heikin-Ashi.
# ATR = TradingView-style Wilder/RMA.
# Only completed 1-minute Delta candles reach this engine.
# ============================================================

prev_close = df["close"].shift(1)

tr1 = df["high"] - df["low"]
tr2 = (df["high"] - prev_close).abs()
tr3 = (df["low"] - prev_close).abs()
df["TR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

df["ATR"] = float("nan")

if len(df) >= ATR_PERIOD:
    df.loc[ATR_PERIOD - 1, "ATR"] = (
        df["TR"].iloc[:ATR_PERIOD].mean()
    )

    for i in range(ATR_PERIOD, len(df)):
        df.loc[i, "ATR"] = (
            df.loc[i - 1, "ATR"] * (ATR_PERIOD - 1)
            + df.loc[i, "TR"]
        ) / ATR_PERIOD

df["HL2"] = (df["high"] + df["low"]) / 2.0

df["BASIC_UPPER"] = float("nan")
df["BASIC_LOWER"] = float("nan")
df["FINAL_UPPER"] = float("nan")
df["FINAL_LOWER"] = float("nan")
df["SUPERTREND"] = float("nan")
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

    df.loc[i, "BASIC_UPPER"] = basic_upper
    df.loc[i, "BASIC_LOWER"] = basic_lower

    if i == ATR_PERIOD - 1 or prev_final_upper is None:
        final_upper = basic_upper
        final_lower = basic_lower
    else:
        previous_close = float(df.loc[i - 1, "close"])

        if (
            basic_upper < prev_final_upper
            or previous_close > prev_final_upper
        ):
            final_upper = basic_upper
        else:
            final_upper = prev_final_upper

        if (
            basic_lower > prev_final_lower
            or previous_close < prev_final_lower
        ):
            final_lower = basic_lower
        else:
            final_lower = prev_final_lower

    df.loc[i, "FINAL_UPPER"] = final_upper
    df.loc[i, "FINAL_LOWER"] = final_lower

    if i == ATR_PERIOD - 1 or prev_supertrend is None:
        direction = 1
        supertrend = final_upper
    else:
        if prev_supertrend == prev_final_upper:
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

    df.loc[i, "ST_DIRECTION"] = direction
    df.loc[i, "SUPERTREND"] = supertrend

    if i > 0 and prev_direction != direction:
        if direction == -1:
            df.loc[i, "SIGNAL"] = "BUY"
        elif direction == 1:
            df.loc[i, "SIGNAL"] = "SELL"

    prev_final_upper = final_upper
    prev_final_lower = final_lower
    prev_supertrend = supertrend
    prev_direction = direction

# Compatibility with the existing dashboard.
df["M_TREND"] = df["ST_DIRECTION"]


# ============================================================
# SIGNAL HISTORY
# ============================================================


# ============================================================

signal_rows = df[
    df["SIGNAL"].isin(
        ["BUY", "SELL"]
    )
].copy()


# ============================================================
# CURRENT CANDLE
# ============================================================

last_candle = df.iloc[-1]

current_trend = int(
    last_candle["M_TREND"]
)

current_close = float(
    last_candle["close"]
)

current_supertrend = float(
    last_candle["SUPERTREND"]
)

current_signal = str(
    last_candle["SIGNAL"]
)


# ============================================================
# CURRENT DIRECTION
# ============================================================

if current_trend == -1:

    current_direction = "BUY / BULLISH 🟢"

else:

    current_direction = "SELL / BEARISH 🔴"


# ============================================================
# CURRENT ENTRY
# ============================================================

if len(signal_rows) >= 1:

    current_entry = signal_rows.iloc[-1]

    signal_direction = str(
        current_entry["SIGNAL"]
    )

    signal_entry_price = float(
        current_entry["close"]
    )

    signal_supertrend = float(
        current_entry["SUPERTREND"]
    )

    signal_time = indian_time(
        current_entry["time"]
    )

else:

    signal_direction = ""

    signal_entry_price = current_close

    signal_supertrend = current_supertrend

    signal_time = indian_time(
        last_candle["time"]
    )


# ============================================================
# PREVIOUS ENTRY
# ============================================================

if len(signal_rows) >= 2:

    previous_entry = signal_rows.iloc[-2]

    previous_entry_signal = str(
        previous_entry["SIGNAL"]
    )

    previous_entry_price = float(
        previous_entry["close"]
    )

    previous_entry_st = float(
        previous_entry["SUPERTREND"]
    )

    previous_entry_time = indian_time(
        previous_entry["time"]
    )

else:

    previous_entry_signal = ""

    previous_entry_price = None

    previous_entry_st = None

    previous_entry_time = "-"


# ============================================================
# DISPLAY
# ============================================================

st.divider()

st.header("📈 SUPERTREND — 1 MINUTE")

c1, c2, c3 = st.columns(3)

with c1:

    st.metric(
        "CURRENT DIRECTION",
        current_direction
    )

with c2:

    st.metric(
        "CURRENT CLOSE",
        show_price(current_close)
    )

with c3:

    st.metric(
        "SUPERTREND",
        show_price(current_supertrend)
    )


# ============================================================
# CURRENT ENTRY
# ============================================================

st.subheader("🎯 CURRENT ENTRY")

if signal_direction == "BUY":

    st.success(
        f"🟢 BUY | "
        f"ENTRY: {show_price(signal_entry_price)} | "
        f"SUPERTREND: {show_price(signal_supertrend)}"
    )

elif signal_direction == "SELL":

    st.error(
        f"🔴 SELL | "
        f"ENTRY: {show_price(signal_entry_price)} | "
        f"SUPERTREND: {show_price(signal_supertrend)}"
    )

else:

    st.info(
        "No confirmed SuperTrend entry."
    )


st.write(
    f"Signal Candle: **{signal_time}**"
)


# ============================================================
# PREVIOUS ENTRY
# ============================================================

st.subheader("📜 PREVIOUS ENTRY")

if previous_entry_signal == "BUY":

    st.success(
        f"🟢 BUY | "
        f"ENTRY: {show_price(previous_entry_price)} | "
        f"SUPERTREND: {show_price(previous_entry_st)}"
    )

elif previous_entry_signal == "SELL":

    st.error(
        f"🔴 SELL | "
        f"ENTRY: {show_price(previous_entry_price)} | "
        f"SUPERTREND: {show_price(previous_entry_st)}"
    )

else:

    st.info(
        "Previous entry available nahi hai."
    )


st.write(
    f"Signal Candle: **{previous_entry_time}**"
                                          )
# ============================================================
# PART 3/4
# MONITOR-ONLY MODE
# ============================================================
# REAL ORDER PLACEMENT IS intentionally NOT done by Streamlit.
# trading_worker.py is the single source of truth for real trading.
# This prevents duplicate orders and keeps trading alive after the
# browser is closed.
# ============================================================

st.divider()
st.header("🖥️ DASHBOARD / TRADING WORKER")

remote_enabled = False
st.session_state["remote_enabled"] = False

st.success(
    "🟢 DASHBOARD IS MONITOR-ONLY — REAL TRADING WORKER RUNS SEPARATELY"
)

st.info(
    "Browser बंद होने पर भी trading जारी रखने के लिए "
    "trading_worker.py को an always-on server/service पर चलाएँ."
)

# Values retained for the existing lower dashboard summary/history.
order_size = DEFAULT_ORDER_SIZE
limit_entry_price = signal_entry_price
target1 = None
target2 = None
target3 = None
target_baskets = []

st.write(f"Order Qty: **{ORDER_QTY:.3f} BTC**")
st.write("Take Profit: **DISABLED**")
st.write("Entry management: **SuperTrend direction change**")
st.write("Order engine: **trading_worker.py**")

# Exchange state is still displayed by the dashboard, but the dashboard
# never cancels or places an order.
open_orders_response = api.open_orders()
open_orders_check_ok = bool(
    isinstance(open_orders_response, dict)
    and open_orders_response.get("success") is True
)
open_orders = get_result(open_orders_response)
if not isinstance(open_orders, list):
    open_orders = []

closed_orders_response = api.closed_orders()
closed_orders_check_ok = bool(
    isinstance(closed_orders_response, dict)
    and closed_orders_response.get("success") is True
)
closed_orders = get_result(closed_orders_response)
if not isinstance(closed_orders, list):
    closed_orders = []

pending_orders = [
    order for order in open_orders
    if str(order.get("state", "")).lower()
    not in {"cancelled", "filled", "rejected"}
]

st.subheader("📋 OPEN ORDERS — MONITOR ONLY")
if pending_orders:
    rows = []
    for order in pending_orders:
        rows.append({
            "Order ID": order.get("id", "-"),
            "Side": str(order.get("side", "")).upper(),
            "Type": order.get("order_type", order.get("type", "-")),
            "Entry": show_price(order.get("limit_price")),
            "Size": order.get("size", "-"),
            "Client ID": order.get("client_order_id", "-"),
            "TP": "DISABLED",
            "State": order.get("state", "-"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("No open orders.")

st.subheader("🔄 DIRECTION-CHANGE MANAGEMENT")
st.write(
    "BUY ↔ SELL direction change पर old opposite bot order cancel और "
    "opposite position reduce-only close केवल trading_worker.py करेगा."
)

# CURRENT SIGNAL INFORMATION
# ============================================================

st.divider()

st.subheader(
    "📡 SIGNAL INFORMATION"
)

s1, s2, s3 = st.columns(3)


with s1:

    st.write(
        f"Direction: **{current_direction}**"
    )


with s2:

    st.write(
        f"Signal Entry: "
        f"**{show_price(signal_entry_price)}**"
    )


with s3:

    st.write(
        f"Signal Time: **{signal_time}**"
)
    # ============================================================
# PART 4/4
# REAL POSITION + ORDER STATUS + TARGET STATUS + REFRESH
# ============================================================

st.divider()

st.header("📍 REAL POSITION")


# ============================================================
# GET REAL POSITION
# ============================================================

position_response = api.position()

position_data = get_result(position_response)

if isinstance(position_data, list):

    if position_data:
        position = position_data[0]
    else:
        position = {}

elif isinstance(position_data, dict):

    position = position_data

else:

    position = {}


position_size = number(
    position.get("size"),
    0
)

position_entry = number(
    position.get("entry_price")
)

position_pnl = number(
    position.get("unrealized_pnl"),
    0
)


# ============================================================
# POSITION SIDE
# ============================================================

if position_size > 0:

    position_side = "LONG 🟢"

elif position_size < 0:

    position_side = "SHORT 🔴"

else:

    position_side = "FLAT ⚪"


p1, p2, p3, p4 = st.columns(4)


with p1:

    st.metric(
        "POSITION",
        position_side
    )


with p2:

    st.metric(
        "SIZE",
        str(abs(position_size))
    )


with p3:

    st.metric(
        "ENTRY PRICE",
        show_price(position_entry)
    )


with p4:

    st.metric(
        "UNREALIZED P&L",
        f"₹{position_pnl:,.2f}"
    )


# ============================================================
# ORDER STATUS
# ============================================================

st.header("📋 REAL ORDER STATUS")


orders_response = api.open_orders()

orders_result = get_result(
    orders_response
)


if isinstance(orders_result, list):

    open_orders = orders_result

else:

    open_orders = []


if open_orders:

    order_rows = []


    for order in open_orders:

        order_rows.append({

            "Order ID":
                order.get(
                    "id",
                    "-"
                ),

            "Side":
                str(
                    order.get(
                        "side",
                        ""
                    )
                ).upper(),

            "Type":
                order.get(
                    "order_type",
                    order.get(
                        "type",
                        "-"
                    )
                ),

            "Price":
                show_price(
                    order.get(
                        "limit_price"
                    )
                ),

            "Size":
                order.get(
                    "size",
                    "-"
                ),

            "Take Profit":
                "DISABLED",

            "Client/Basket":
                order.get(
                    "client_order_id",
                    "-"
                ),

            "State":
                order.get(
                    "state",
                    "-"
                )
        })


    st.dataframe(
        pd.DataFrame(
            order_rows
        ),
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "No open orders."
    )


# ============================================================
# COMPLETE ORDER LIFECYCLE / HISTORY
# ADDITIVE DISPLAY ONLY — EXISTING ORDER LOGIC IS UNCHANGED.
# ============================================================

st.divider()
st.header("🧾 COMPLETE ORDER LIFECYCLE / HISTORY")

_lifecycle_open_response = api.open_orders()
_lifecycle_closed_response = api.closed_orders()

_lifecycle_open = get_result(_lifecycle_open_response)
_lifecycle_closed = get_result(_lifecycle_closed_response)

if not isinstance(_lifecycle_open, list):
    _lifecycle_open = []

if not isinstance(_lifecycle_closed, list):
    _lifecycle_closed = []

_lifecycle_tracking = _ensure_order_tracking()

# Build one combined list. Open orders represent current PENDING status;
# closed orders provide EXECUTED / CANCELLED / REJECTED history.
_lifecycle_orders = []

for _source_name, _source_orders in (
    ("OPEN", _lifecycle_open),
    ("CLOSED", _lifecycle_closed),
):
    for _order in _source_orders:
        try:
            _pid = int(_order.get("product_id", PRODUCT_ID))
        except Exception:
            _pid = PRODUCT_ID

        if _pid != PRODUCT_ID:
            continue

        _client_id = str(_order.get("client_order_id", ""))
        _oid = _order.get("id")

        # Only dashboard-created orders plus orders remembered by this
        # dashboard session are shown in this new lifecycle panel.
        _is_dashboard_order = (
            _client_id.startswith("STB_")
            or _client_id.startswith("ST_BUY_")
            or _client_id.startswith("ST_SELL_")
            or str(_oid) in _lifecycle_tracking
        )

        if _is_dashboard_order:
            _lifecycle_orders.append(_order)

# De-duplicate by exchange order ID.
_lifecycle_unique = {}
for _order in _lifecycle_orders:
    _oid = _order.get("id")
    if _oid is not None:
        _lifecycle_unique[str(_oid)] = _order

# Merge the permanent ledger so old orders remain visible even after
# Streamlit restarts and even when they are no longer returned by the
# exchange history endpoint. Current exchange responses always win for
# status/details, while the original sent timestamp is preserved.
for _saved in _order_history_db_load():
    _saved_id = str(_saved.get("order_id"))
    _saved_exchange = _saved.get("exchange_order") or {}
    if _saved_id in _lifecycle_unique:
        _order_history_db_upsert(
            _saved_id,
            tracking=_saved,
            exchange_order=_lifecycle_unique[_saved_id]
        )
    else:
        _lifecycle_unique[_saved_id] = _saved_exchange if _saved_exchange else {
            "id": _saved_id,
            "product_id": _saved.get("product_id", PRODUCT_ID),
            "client_order_id": _saved.get("client_order_id", ""),
            "side": _saved.get("side", ""),
            "size": _saved.get("qty", "-"),
            "limit_price": _saved.get("limit_price"),
            "state": "pending"
        }

# Persist every exchange order currently visible and combine it with
# the original local tracking record.
for _oid, _order in list(_lifecycle_unique.items()):
    _tracking_now = _lifecycle_tracking.get(str(_oid), {})
    _order_history_db_upsert(_oid, tracking=_tracking_now, exchange_order=_order)

_lifecycle_rows = []

for _oid, _order in _lifecycle_unique.items():
    _tracking = _lifecycle_tracking.get(str(_oid), {})

    _state = str(_order.get("state", "")).lower()
    _status = _order_status_label(_order)

    _sent_ts = _order_sent_epoch(_order)
    if _sent_ts is None:
        _sent_ts = _order_epoch(_tracking.get("sent_epoch"))

    _final_ts = _order_final_epoch(_order)

    # For a still-open order, duration runs until the current refresh.
    if _status == "PENDING":
        _duration = (
            time.time() - _sent_ts
            if _sent_ts is not None
            else None
        )
    else:
        _duration = (
            _final_ts - _sent_ts
            if _sent_ts is not None and _final_ts is not None
            else None
        )

    _reduce_only = bool(_order.get("reduce_only", False))
    _order_type = str(
        _order.get(
            "order_type",
            _order.get("type", "-")
        )
    )

    if (
        _tracking.get("kind") == "EXIT"
        or _reduce_only
        or "close" in str(_order.get("client_order_id", "")).lower()
    ):
        _kind = "EXIT"
    else:
        _kind = _tracking.get("kind", "ENTRY")

    _executed_price = (
        _order.get("average_fill_price")
        or _order.get("avg_fill_price")
        or _order.get("fill_price")
        or _order.get("average_price")
    )

    _lifecycle_rows.append({
        "Order ID": _oid,
        "Kind": _kind,
        "Basket": _tracking.get(
            "basket",
            _order.get("client_order_id", "-")
        ),
        "Side": str(_order.get("side", "")).upper(),
        "Qty": _order.get("size", _tracking.get("qty", "-")),
        "Order Price": show_price(
            _order.get(
                "limit_price",
                _tracking.get("limit_price")
            )
        ),
        "Executed Price": show_price(_executed_price),
        "Sent At": _format_epoch(_sent_ts),
        "Executed/Cancelled At": _format_epoch(_final_ts),
        "Pending/Active For": _duration_text(_duration),
        "Status": _status,
        "Exchange State": _state.upper() or "-",
        "Client/Basket ID": _order.get(
            "client_order_id",
            "-"
        ),
        "Order Type": _order_type,
    })

# Also keep locally remembered orders visible if the exchange has not yet
# returned them in the current open/closed response.
_seen_ids = {str(x.get("Order ID")) for x in _lifecycle_rows}
for _saved in _order_history_db_load():
    _oid = str(_saved.get("order_id"))
    if _oid in _seen_ids:
        continue
    _tracking = _saved
    _ex = _saved.get("exchange_order") or {}
    _sent_ts = _order_epoch(_saved.get("sent_epoch"))
    _final_ts = _order_final_epoch(_ex)
    _state = str(_ex.get("state", ""))
    _status = _order_status_label(_ex) if _ex else "PENDING / LAST KNOWN"
    if _status == "PENDING" or _status == "PENDING / LAST KNOWN":
        _dur = time.time() - _sent_ts if _sent_ts is not None else None
    else:
        _dur = (_final_ts - _sent_ts) if _sent_ts is not None and _final_ts is not None else None
    _lifecycle_rows.append({
        "Order ID": _oid,
        "Kind": _tracking.get("kind", "ENTRY"),
        "Basket": _tracking.get("basket", "-"),
        "Side": str(_tracking.get("side") or _tracking.get("signal") or _ex.get("side", "")).upper(),
        "Qty": _tracking.get("qty", _ex.get("size", "-")),
        "Order Price": show_price(_tracking.get("limit_price", _ex.get("limit_price"))),
        "Executed Price": show_price(_ex.get("average_fill_price") or _ex.get("avg_fill_price") or _ex.get("fill_price") or _ex.get("average_price")),
        "Sent At": _saved.get("sent_text") or _format_epoch(_sent_ts),
        "Executed/Cancelled At": _format_epoch(_final_ts),
        "Pending/Active For": _duration_text(_dur),
        "Status": _status,
        "Exchange State": _state.upper() or "-",
        "Client/Basket ID": _saved.get("client_order_id") or _ex.get("client_order_id", "-"),
        "Order Type": _ex.get("order_type", _ex.get("type", "-")),
    })

if _lifecycle_rows:
    _lifecycle_rows.sort(
        key=lambda row: str(row.get("Order ID", "")),
        reverse=True
    )
    st.dataframe(
        pd.DataFrame(_lifecycle_rows),
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("अभी इस Dashboard के लिए कोई order lifecycle history उपलब्ध नहीं है।")

st.caption(
    "🟡 PENDING = exchange पर order खड़ा है और अभी execute नहीं हुआ। "
    "🟢 EXECUTED/CLOSED = exchange ने order पूरा/close किया। "
    "🔴 CANCELLED = order execute हुए बिना cancel हुआ। "
    "EXIT = reduce-only/position-close order."
)


# ============================================================
# TARGET STATUS — TP DISABLED
# ============================================================

st.header("🎯 TRADE MANAGEMENT")

ts1, ts2, ts3 = st.columns(3)

with ts1:
    st.metric("DIRECTION", "LONG" if signal_direction == "BUY" else ("SHORT" if signal_direction == "SELL" else "NONE"))

with ts2:
    st.metric("ENTRY", show_price(signal_entry_price))

with ts3:
    st.metric("TAKE PROFIT", "DISABLED")

st.info(
    "No TP1 / TP2 / TP3 / bracket TP. "
    "Position management is by confirmed SuperTrend direction change."
)

# LIVE MARKET PRICE
# ============================================================

st.header("💰 LIVE MARKET PRICE")


ticker_response = api.ticker()

ticker_result = get_result(
    ticker_response
)


if isinstance(ticker_result, dict):

    live_price = None


    for key in [
        "close",
        "last_price",
        "mark_price",
        "spot_price"
    ]:

        value = number(
            ticker_result.get(key)
        )


        if value is not None:

            live_price = value

            break


else:

    live_price = None


st.metric(
    "BTCUSD",
    show_price(live_price)
)


# ============================================================
# SIGNAL / ORDER SUMMARY
# ============================================================

st.header("📊 TRADING SUMMARY")


summary_rows = [

    {
        "Item": "SuperTrend Direction",
        "Value": current_direction
    },

    {
        "Item": "Signal Entry",
        "Value": show_price(
            signal_entry_price
        )
    },

    {
        "Item": "Limit Entry",
        "Value": show_price(
            limit_entry_price
        )
    },


    {
        "Item": "Signal Time",
        "Value": signal_time
    },

    {
        "Item": "Remote Trading",
        "Value": (
            "ON 🔴"
            if remote_enabled
            else "OFF 🟢"
        )
    }
]


st.dataframe(
    pd.DataFrame(
        summary_rows
    ),
    use_container_width=True,
    hide_index=True
)


# ============================================================
# SAFETY INFORMATION
# ============================================================

st.divider()

st.subheader(
    "⚠️ REAL TRADING SAFETY"
)

if remote_enabled:

    st.error(
        "REAL TRADING ACTIVE — "
        "Dashboard se exchange orders bheje ja sakte hain."
    )

else:

    st.success(
        "REAL TRADING OFF — "
        "Dashboard order place nahi karega."
    )


st.write(
    "SuperTrend direction change पर opposite order/position management "
    "केवल trading_worker.py में enabled है."
)


# ============================================================
# INDIAN TIME
# ============================================================

now_ist = datetime.now(
    timezone.utc
).astimezone(IST)


st.write(
    "Dashboard Time: "
    f"**{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}**"
)


# ============================================================
# AUTO REFRESH
# ============================================================

# ============================================================
# CHART OVERLAY — REAL DELTA ORDER STATUS + 1-MINUTE SIGNAL
# The TradingView chart remains unchanged. This overlay is placed
# INSIDE the chart container and is refreshed from the real Delta
# exchange state on every Streamlit refresh.
# ============================================================

_chart_signal_direction = str(signal_direction or "").upper()
_chart_signal_price = number(signal_entry_price)
_chart_signal_time = str(signal_time or "-")

_chart_signal_epoch = None
if isinstance(current_entry, pd.Series):
    try:
        _chart_signal_epoch = int(float(current_entry["time"]))
    except Exception:
        _chart_signal_epoch = None

_chart_signal_prefix = (
    f"STB_{_chart_signal_direction}_{_chart_signal_epoch}"
    if _chart_signal_direction in ["BUY", "SELL"] and _chart_signal_epoch is not None
    else ""
)

_chart_entry_rows = []
if _chart_signal_prefix:
    for _row in _lifecycle_rows:
        _client = str(_row.get("Client/Basket ID", ""))
        _kind = str(_row.get("Kind", "ENTRY")).upper()
        if _kind == "ENTRY" and _client.startswith(_chart_signal_prefix):
            _chart_entry_rows.append(_row)

_chart_status = "NO ORDER"
_chart_sent_at = "-"
_chart_event_at = "-"
_chart_executed_price = "-"

if _chart_entry_rows:
    _statuses = [str(r.get("Status", "")).upper() for r in _chart_entry_rows]

    if any("PENDING" in x for x in _statuses):
        if any("EXECUTED" in x for x in _statuses):
            _chart_status = "PARTIAL / PENDING"
        else:
            _chart_status = "PENDING"
    elif all("EXECUTED" in x for x in _statuses):
        _chart_status = "EXECUTED"
    elif any("REJECTED" in x for x in _statuses):
        _chart_status = "REJECTED"
    elif all("CANCELLED" in x for x in _statuses):
        _chart_status = "CANCELLED"
    else:
        _chart_status = _statuses[0] if _statuses else "NO ORDER"

    _sent_values = [str(r.get("Sent At", "-")) for r in _chart_entry_rows if r.get("Sent At") not in [None, "-"]]
    _event_values = [str(r.get("Executed/Cancelled At", "-")) for r in _chart_entry_rows if r.get("Executed/Cancelled At") not in [None, "-"]]
    _price_values = [str(r.get("Executed Price", "-")) for r in _chart_entry_rows if r.get("Executed Price") not in [None, "-"]]

    if _sent_values:
        _chart_sent_at = _sent_values[0]
    if _event_values and _chart_status == "EXECUTED":
        _chart_event_at = _event_values[-1]
    elif _event_values and _chart_status in ["CANCELLED", "REJECTED"]:
        _chart_event_at = _event_values[-1]
    if _price_values:
        _chart_executed_price = _price_values[0]

# Current confirmed 1-minute SuperTrend state.
# Existing engine: ST_DIRECTION -1 = bullish/up, +1 = bearish/down.
_chart_st_direction = "-"
_chart_st_value = "-"
if isinstance(last_candle, pd.Series):
    try:
        _st_dir_value = int(last_candle["ST_DIRECTION"])
        if _st_dir_value == -1:
            _chart_st_direction = "UP / BULLISH"
        elif _st_dir_value == 1:
            _chart_st_direction = "DOWN / BEARISH"
    except Exception:
        pass

    try:
        _chart_st_value = show_price(last_candle["SUPERTREND"])
    except Exception:
        _chart_st_value = "-"

_chart_payload = {
    "signal": _chart_signal_direction if _chart_signal_direction in ["BUY", "SELL"] else "-",
    "signal_price": show_price(_chart_signal_price),
    "signal_time": _chart_signal_time,
    "st_direction": _chart_st_direction,
    "st_value": _chart_st_value,
    "order_status": _chart_status,
    "sent_at": _chart_sent_at,
    "event_at": _chart_event_at,
    "executed_price": _chart_executed_price,
}

_chart_json = json.dumps(_chart_payload, ensure_ascii=False)

components.html(
    f"""
    <div
        class="tradingview-widget-container"
        style="height:700px;width:100%;position:relative;overflow:hidden;">

        <div
            class="tradingview-widget-container__widget"
            style="height:700px;width:100%;">
        </div>

        <!-- REAL DELTA STATUS + 1-MIN SIGNAL OVERLAY INSIDE CHART -->
        <div id="delta-trade-overlay" style="
            position:absolute;
            top:12px;
            left:12px;
            z-index:20;
            min-width:245px;
            max-width:310px;
            padding:10px 12px;
            border-radius:10px;
            background:rgba(10,10,10,0.90);
            border:1px solid rgba(255,255,255,0.18);
            color:#fff;
            font-family:Arial,sans-serif;
            font-size:12px;
            line-height:1.45;
            box-shadow:0 4px 18px rgba(0,0,0,0.35);
            pointer-events:none;
        ">
            <div style="font-size:13px;font-weight:700;margin-bottom:5px;">
                1-MIN SUPERTREND 10,3 • REAL DELTA
            </div>
            <div id="tv-signal-line" style="font-size:16px;font-weight:800;">Loading...</div>
            <div id="tv-signal-time"></div>
            <div id="tv-st-direction" style="margin-top:4px;font-weight:800;"></div>
            <div id="tv-st-value"></div>
            <div id="tv-order-status" style="margin-top:5px;font-weight:800;"></div>
            <div id="tv-order-time"></div>
        </div>

        <script>
        (function() {{
            const data = {_chart_json};
            const signalLine = document.getElementById('tv-signal-line');
            const signalTime = document.getElementById('tv-signal-time');
            const stDirection = document.getElementById('tv-st-direction');
            const stValue = document.getElementById('tv-st-value');
            const orderStatus = document.getElementById('tv-order-status');
            const orderTime = document.getElementById('tv-order-time');

            if (data.signal === 'BUY') {{
                signalLine.textContent = '🟢 BUY  @  ' + data.signal_price;
            }} else if (data.signal === 'SELL') {{
                signalLine.textContent = '🔴 SELL @  ' + data.signal_price;
            }} else {{
                signalLine.textContent = 'WAITING FOR 1-MIN SIGNAL';
            }}

            signalTime.textContent = 'Signal candle: ' + data.signal_time;

            if (data.st_direction === 'UP / BULLISH') {{
                stDirection.textContent = '🟢 ST 10,3 DIRECTION: UP / BULLISH';
            }} else if (data.st_direction === 'DOWN / BEARISH') {{
                stDirection.textContent = '🔴 ST 10,3 DIRECTION: DOWN / BEARISH';
            }} else {{
                stDirection.textContent = '⚪ ST 10,3 DIRECTION: -';
            }}

            stValue.textContent = 'SuperTrend 10,3: ' + (data.st_value || '-');

            const status = data.order_status || 'NO ORDER';
            if (status === 'PENDING' || status === 'PARTIAL / PENDING') {{
                orderStatus.textContent = '🟡 DELTA: ' + status;
            }} else if (status === 'EXECUTED') {{
                orderStatus.textContent = '🟢 DELTA: EXECUTED';
            }} else if (status === 'CANCELLED') {{
                orderStatus.textContent = '🔴 DELTA: CANCELLED';
            }} else if (status === 'REJECTED') {{
                orderStatus.textContent = '🔴 DELTA: REJECTED';
            }} else {{
                orderStatus.textContent = '⚪ DELTA: ' + status;
            }}

            if (status === 'PENDING' || status === 'PARTIAL / PENDING') {{
                orderTime.textContent = 'Pending since: ' + data.sent_at;
            }} else if (status === 'EXECUTED') {{
                orderTime.textContent = 'Executed: ' + data.event_at + ' | Fill: ' + data.executed_price;
            }} else if (status === 'CANCELLED' || status === 'REJECTED') {{
                orderTime.textContent = 'Final: ' + data.event_at;
            }} else {{
                orderTime.textContent = '';
            }}
        }})();
        </script>

        <script
            type="text/javascript"
            src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
            async>

        {{
            "autosize": false,
            "height": 700,
            "symbol": "BINANCE:BTCUSDT",
            "interval": "5",
            "timezone": "Asia/Kolkata",
            "theme": "dark",
            "style": "1",
            "locale": "en",
            "studies": [
                {{
                    "id": "SuperTrend@tv-basicstudies",
                    "inputs": {{
                        "length": 10,
                        "factor": 3
                    }}
                }}
            ],
            "studies_overrides": {{
                "SuperTrend@tv-basicstudies.0": "#4CAF50",
                "SuperTrend@tv-basicstudies.1": "#F44336"
            }},
            "enable_publishing": false,
            "allow_symbol_change": true,
            "hide_top_toolbar": false,
            "hide_legend": false,
            "save_image": false,
            "hide_volume": false,
            "support_host": "https://www.tradingview.com"
        }}

        </script>
    </div>
    """,
    height=700,
    scrolling=False
)

# ============================================================
# END OF PART 1
# PART 2 = CANDLE + SUPERTREND ENGINE
# ============================================================
time.sleep(REFRESH_SECONDS)
st.rerun()
