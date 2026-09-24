# ============================================================
# SANJAY RANA - DELTA REAL TRADING DASHBOARD
# PART 1/4
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
# SETTINGS
# ============================================================

BASE_URL = os.getenv(
    "DELTA_BASE_URL",
    "https://api.india.delta.exchange"
).rstrip("/")

SYMBOL = os.getenv("DELTA_SYMBOL", "BTCUSD")
PRODUCT_ID = int(os.getenv("DELTA_PRODUCT_ID", "27"))

TIMEFRAME = "60m"
CANDLE_SECONDS = 3600

ATR_PERIOD = 10
MULTIPLIER = 3.0

REFRESH_SECONDS = 5

# ============================================================
# REAL TRADING MASTER SWITCH
# ============================================================

REMOTE_TRADING = (
    os.getenv("REMOTE_TRADING", "false").lower() == "true"
)

# ============================================================
# DEFAULT REMOTE CONTROL SETTINGS
# ============================================================

DEFAULT_BUY_OFFSET = int(
    os.getenv("BUY_OFFSET", "-20")
)

DEFAULT_SELL_OFFSET = int(
    os.getenv("SELL_OFFSET", "20")
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

TARGET_1 = int(os.getenv("TARGET_1", "200"))
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
    "1 Minute | ATR 10 | Multiplier 3.0 | HL2 | "
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


def _ensure_order_tracking():
    if "order_tracking" not in st.session_state:
        st.session_state["order_tracking"] = {}
    return st.session_state["order_tracking"]


def _remember_sent_order(order_id, data):
    if order_id is None:
        return
    tracking = _ensure_order_tracking()
    tracking[str(order_id)] = dict(data)


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
    # REAL ACCOUNT BALANCE / MARGIN / LEVERAGE
    # ADDITIVE ONLY - EXISTING API METHODS ARE UNCHANGED
    # --------------------------------------------------------

    def wallet_balances(self):

        return self.request(
            "GET",
            "/v2/wallet/balances",
            private=True
        )


    def order_leverage(self):

        return self.request(
            "GET",
            f"/v2/products/{PRODUCT_ID}/orders/leverage",
            private=True
        )


    def margined_positions(self):

        return self.request(
            "GET",
            "/v2/positions/margined",
            params={
                "product_id": PRODUCT_ID
            },
            private=True
        )


    # --------------------------------------------------------
    # PLACE LIMIT ORDER
    # --------------------------------------------------------

    def place_market_order(self, side, size, client_order_id=None):

        body = {
            "product_id": PRODUCT_ID,
            "product_symbol": SYMBOL,
            "size": int(abs(size)),
            "side": side,
            "order_type": "market_order",
            "reduce_only": False,
        }

        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:32]

        return self.request(
            "POST",
            "/v2/orders",
            body=body,
            private=True
        )


    def place_limit_order(
        self,
        side,
        size,
        limit_price,
        take_profit_price=None,
        client_order_id=None
    ):

        body = {

            "product_id": PRODUCT_ID,

            "product_symbol": SYMBOL,

            "limit_price": str(
                limit_price
            ),

            "size": int(size),

            "side": side,

            "order_type": "limit_order",

            "time_in_force": "gtc",

            "post_only": False,

            "reduce_only": False
        }

        # Delta supports a bracket take-profit attached directly to a
        # new LIMIT order. This keeps TP visible with the pending entry
        # instead of waiting for a separate TP order after the fill.
        if False and take_profit_price is not None:
            body["bracket_take_profit_price"] = str(
                take_profit_price
            )
            body["bracket_take_profit_limit_price"] = str(
                take_profit_price
            )
            body["bracket_stop_trigger_method"] = (
                "last_traded_price"
            )

        if client_order_id:
            body["client_order_id"] = str(
                client_order_id
            )

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
# REAL DELTA ACCOUNT SNAPSHOT
# ADDITIVE ONLY - READS LIVE DATA FROM DELTA EXCHANGE
# ============================================================

if st.session_state.get("owner_api_connected", False):
    try:
        _real_account_client = DeltaAPI(OWNER_API_KEY, OWNER_API_SECRET)
        _real_wallet_response = _real_account_client.wallet_balances()
        _real_leverage_response = _real_account_client.order_leverage()
        _real_position_response = _real_account_client.position()
        _real_open_orders_response = _real_account_client.open_orders()

        def _real_result(response):
            if isinstance(response, dict):
                return response.get("result", response)
            return response

        _wallet_result = _real_result(_real_wallet_response)
        _leverage_result = _real_result(_real_leverage_response)
        _position_result = _real_result(_real_position_response)
        _open_orders_result = _real_result(_real_open_orders_response)

        if isinstance(_wallet_result, list):
            _wallet_rows = _wallet_result
        else:
            _wallet_rows = []

        _primary_wallet = None
        for _wallet in _wallet_rows:
            if str(_wallet.get("asset_symbol", "")).upper() in {"USDT", "USD"}:
                _primary_wallet = _wallet
                break
        if _primary_wallet is None and _wallet_rows:
            _primary_wallet = _wallet_rows[0]

        _net_equity = None
        if isinstance(_wallet_result, dict):
            _net_equity = _wallet_result.get("meta", {}).get("net_equity")
        elif isinstance(_real_wallet_response, dict):
            _net_equity = _real_wallet_response.get("meta", {}).get("net_equity")

        _balance = (_primary_wallet or {}).get("balance")
        _available = (_primary_wallet or {}).get("available_balance")
        _blocked = (_primary_wallet or {}).get("blocked_margin")
        _order_margin = (_primary_wallet or {}).get("order_margin")
        _position_margin = (_primary_wallet or {}).get("position_margin")
        _leverage = (_leverage_result or {}).get("leverage") if isinstance(_leverage_result, dict) else None

        st.divider()
        st.header("💰 DELTA REAL ACCOUNT — LIVE")
        st.caption("यह section सीधे Delta Exchange के private API से real account data पढ़ता है।")

        _a1, _a2, _a3, _a4, _a5 = st.columns(5)
        with _a1:
            st.metric("REAL BALANCE", str(_balance) if _balance is not None else "-")
        with _a2:
            st.metric("TOTAL / NET EQUITY", str(_net_equity) if _net_equity is not None else "-")
        with _a3:
            st.metric("AVAILABLE MARGIN", str(_available) if _available is not None else "-")
        with _a4:
            st.metric("USED / BLOCKED MARGIN", str(_blocked) if _blocked is not None else "-")
        with _a5:
            st.metric("LEVERAGE", f"{_leverage}x" if _leverage is not None else "-")

        st.write(
            f"**Order Margin:** {str(_order_margin) if _order_margin is not None else '-'}  |  "
            f"**Position Margin:** {str(_position_margin) if _position_margin is not None else '-'}"
        )

        st.subheader("📌 REAL OPEN ORDERS — DELTA")
        if isinstance(_open_orders_result, list):
            _real_orders_for_display = _open_orders_result
        elif isinstance(_open_orders_result, dict):
            _real_orders_for_display = _open_orders_result.get("result", [])
        else:
            _real_orders_for_display = []

        if _real_orders_for_display:
            # ONLY Open Orders display renderer is changed.
            # Real Delta API data and all trading logic remain untouched.
            st.table(
                pd.DataFrame(_real_orders_for_display)
            )
        else:
            st.info("Delta पर अभी कोई real open order नहीं है।")

        st.subheader("📍 REAL OPEN POSITION — DELTA")
        if isinstance(_position_result, list):
            _real_positions_for_display = _position_result
        elif isinstance(_position_result, dict):
            _real_positions_for_display = [_position_result] if _position_result else []
        else:
            _real_positions_for_display = []

        _real_positions_for_display = [
            _p for _p in _real_positions_for_display
            if isinstance(_p, dict) and float(_p.get("size", 0) or 0) != 0
        ]

        if _real_positions_for_display:
            st.dataframe(
                pd.DataFrame(_real_positions_for_display),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Delta पर अभी कोई real open position नहीं है।")

    except Exception as _real_account_error:
        st.error(f"❌ Real Delta Account Sync Error: {_real_account_error}")


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
# PART 3/4 — SUPERTREND GRID ENGINE
# ============================================================
# User-defined grid rules:
# 1) SuperTrend is direction selector only.
# 2) New cycle starts at the confirmed SuperTrend signal price.
# 3) Initial position = 0.010 BTC = 10 contracts.
# 4) One grid order = 0.001 BTC = 1 contract.
# 5) Grid spacing = 200 points.
# 6) BUY direction: 10 SELL levels above signal, 200 points apart.
# 7) BUY direction: BUY levels below signal continue only until the
#    SuperTrend/ATR line; number of levels is dynamic.
# 8) When an upper SELL fills, its 0.001 BTC is recycled as a BUY LIMIT
#    one grid step below that SELL level. This keeps every completed
#    upper rung armed without chasing price upward.
# 9) SELL direction is the exact mirror image.
# 10) On SuperTrend reversal, cancel ALL bot LIMIT orders and close the
#     old position before starting a fresh cycle.
# 11) No attached TP orders are used. The grid itself is the target engine.
# ============================================================

GRID_STEP = int(os.getenv("GRID_STEP", "200"))
GRID_CHUNK_CONTRACTS = int(os.getenv("GRID_CHUNK_CONTRACTS", "1"))
GRID_INITIAL_CONTRACTS = int(os.getenv("GRID_INITIAL_CONTRACTS", "10"))
GRID_UPPER_LEVELS = int(os.getenv("GRID_UPPER_LEVELS", "10"))

if GRID_STEP <= 0:
    raise ValueError("GRID_STEP must be greater than 0")
if GRID_CHUNK_CONTRACTS != 1:
    raise ValueError("GRID_CHUNK_CONTRACTS must remain 1 (0.001 BTC)")
if GRID_INITIAL_CONTRACTS != GRID_UPPER_LEVELS:
    raise ValueError("Initial 0.010 BTC must equal 10 x 0.001 BTC")

GRID_CHUNK_BTC = GRID_CHUNK_CONTRACTS * CONTRACT_BTC
GRID_INITIAL_BTC = GRID_INITIAL_CONTRACTS * CONTRACT_BTC


def _grid_result(response):
    if isinstance(response, dict):
        return response.get("result", response)
    return response


def _grid_order_side(order):
    return str(order.get("side", "")).lower()


def _grid_is_limit(order):
    return "limit" in str(
        order.get("order_type", order.get("type", ""))
    ).lower()


def _grid_is_bot(order):
    cid = str(order.get("client_order_id", ""))
    return cid.startswith("GRID_")


def _grid_price(order):
    value = order.get("limit_price")
    try:
        return float(value)
    except Exception:
        return None


def _grid_contracts(order):
    try:
        return int(abs(float(order.get("size", 0))))
    except Exception:
        return 0


def _grid_order_id(order):
    value = order.get("id")
    return str(value) if value is not None else None


def _grid_close_position(position_size, direction, tag):
    """Close an old position with a reduce-only market order."""
    size = int(abs(float(position_size or 0)))
    if size <= 0:
        return True, None

    close_side = "sell" if float(position_size) > 0 else "buy"
    result = api.place_market_reduce_only(
        side=close_side,
        size=size,
        client_order_id=f"GRID_CLOSE_{direction}_{int(time.time())}"
    )
    ok = isinstance(result, dict) and result.get("success") is True
    return ok, result


def _grid_fetch_orders():
    response = api.open_orders()
    ok = isinstance(response, dict) and response.get("success") is True
    rows = _grid_result(response)
    if not isinstance(rows, list):
        rows = []
    return ok, rows


def _grid_fetch_closed_orders():
    response = api.closed_orders()
    ok = isinstance(response, dict) and response.get("success") is True
    rows = _grid_result(response)
    if not isinstance(rows, list):
        rows = []
    return ok, rows


def _grid_fetch_position():
    response = api.position()
    ok = isinstance(response, dict) and response.get("success") is True
    data = _grid_result(response)
    if isinstance(data, list):
        pos = data[0] if data else {}
    elif isinstance(data, dict):
        pos = data
    else:
        pos = {}
    try:
        size = float(pos.get("size", 0) or 0)
    except Exception:
        size = 0.0
    return ok, pos, size


def _grid_cancel_all_bot_limits(open_orders):
    failed = False
    cancelled = 0
    for order in list(open_orders):
        if not _grid_is_bot(order) or not _grid_is_limit(order):
            continue
        order_id = _grid_order_id(order)
        if not order_id:
            continue
        result = api.cancel_order(order_id)
        if isinstance(result, dict) and result.get("success") is True:
            cancelled += 1
        else:
            failed = True
    return not failed, cancelled


def _grid_place_limit(side, price, label, direction, size=GRID_CHUNK_CONTRACTS):
    # Client ID is deterministic per direction/price/role. It prevents
    # Streamlit reruns from creating a second identical order.
    cents_price = int(round(float(price) * 100))
    cid = f"GRID_{direction}_{label}_{cents_price}"
    return api.place_limit_order(
        side=side,
        size=int(size),
        limit_price=float(price),
        take_profit_price=None,
        client_order_id=cid[:32]
    )


def _grid_has_price_side(open_orders, side, price, tolerance=0.01):
    for order in open_orders:
        if not _grid_is_bot(order):
            continue
        if _grid_order_side(order) != side:
            continue
        op = _grid_price(order)
        if op is not None and abs(op - float(price)) <= tolerance:
            return True
    return False


def _grid_active_bot_orders(open_orders):
    return [
        o for o in open_orders
        if _grid_is_bot(o)
        and _grid_is_limit(o)
        and str(o.get("state", "")).lower() not in {"cancelled", "filled", "rejected"}
    ]


def _grid_signal_key(direction, price, signal_time):
    try:
        epoch = int(float(signal_time))
    except Exception:
        epoch = int(time.time())
    return f"{direction}_{epoch}_{int(round(float(price)))}"


# ------------------------------------------------------------
# GRID ACCOUNT / ORDER SNAPSHOT
# ------------------------------------------------------------
grid_open_ok, grid_open_orders = _grid_fetch_orders()
grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()

if not grid_open_ok:
    st.error("🛑 Grid engine stopped: Delta open-order check failed.")
elif not grid_pos_ok:
    st.error("🛑 Grid engine stopped: Delta position check failed.")


# ------------------------------------------------------------
# ACCOUNT / MARGIN / LEVERAGE
# ------------------------------------------------------------
st.divider()
st.header("💰 GRID ACCOUNT / MARGIN")

try:
    wallet_response = api.wallet_balances()
    leverage_response = api.order_leverage()
    wallet_data = _grid_result(wallet_response)
    leverage_data = _grid_result(leverage_response)

    wallets = wallet_data if isinstance(wallet_data, list) else []
    wallet = None
    for w in wallets:
        if str(w.get("asset_symbol", "")).upper() in {"USDT", "USD"}:
            wallet = w
            break
    if wallet is None and wallets:
        wallet = wallets[0]
    wallet = wallet or {}

    net_equity = None
    if isinstance(wallet_data, dict):
        net_equity = wallet_data.get("meta", {}).get("net_equity")
    elif isinstance(wallet_response, dict):
        net_equity = wallet_response.get("meta", {}).get("net_equity")

    balance = wallet.get("balance")
    available = wallet.get("available_balance")
    blocked = wallet.get("blocked_margin")
    order_margin = wallet.get("order_margin")
    position_margin = wallet.get("position_margin")
    leverage = leverage_data.get("leverage") if isinstance(leverage_data, dict) else None

    a1, a2, a3, a4, a5 = st.columns(5)
    with a1:
        st.metric("TOTAL BALANCE", str(balance) if balance is not None else "-")
    with a2:
        st.metric("NET EQUITY", str(net_equity) if net_equity is not None else "-")
    with a3:
        st.metric("AVAILABLE MARGIN", str(available) if available is not None else "-")
    with a4:
        st.metric("USED MARGIN", str(blocked) if blocked is not None else "-")
    with a5:
        st.metric("LEVERAGE", f"{leverage}x" if leverage is not None else "-")

    st.write(
        f"**Order Margin:** {order_margin if order_margin is not None else '-'}  |  "
        f"**Position Margin:** {position_margin if position_margin is not None else '-'}"
    )
except Exception as exc:
    st.warning(f"Account/margin data unavailable: {exc}")


# ------------------------------------------------------------
# GRID LEVEL CALCULATION
# ------------------------------------------------------------

grid_direction = signal_direction if signal_direction in {"BUY", "SELL"} else ""
grid_base = float(signal_entry_price)
grid_atr_line = float(signal_supertrend)

# For BUY direction, lower grid is bounded by the SuperTrend line.
# For SELL direction, upper grid is bounded by the SuperTrend line.

def _build_lower_buy_levels(base, atr_line, direction):
    levels = []
    if direction == "BUY":
        if atr_line < base:
            n = int((base - atr_line) // GRID_STEP)
            for i in range(1, n + 1):
                p = base - i * GRID_STEP
                if p >= atr_line:
                    levels.append(p)
    else:
        if atr_line > base:
            n = int((atr_line - base) // GRID_STEP)
            for i in range(1, n + 1):
                p = base + i * GRID_STEP
                if p <= atr_line:
                    levels.append(p)
    return levels


if grid_direction == "BUY":
    upper_levels = [grid_base + GRID_STEP * i for i in range(1, GRID_UPPER_LEVELS + 1)]
    lower_levels = _build_lower_buy_levels(grid_base, grid_atr_line, "BUY")
else:
    upper_levels = [grid_base - GRID_STEP * i for i in range(1, GRID_UPPER_LEVELS + 1)]
    lower_levels = _build_lower_buy_levels(grid_base, grid_atr_line, "SELL")


# ------------------------------------------------------------
# DISPLAY GRID PLAN
# ------------------------------------------------------------
st.subheader("🧱 ACTIVE GRID PLAN")

if grid_direction:
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        st.metric("DIRECTION", grid_direction)
    with g2:
        st.metric("SIGNAL / BASE", show_price(grid_base))
    with g3:
        st.metric("ATR LINE", show_price(grid_atr_line))
    with g4:
        st.metric("GRID STEP", f"{GRID_STEP} points")

    st.write(
        f"Initial position: **{GRID_INITIAL_BTC:.3f} BTC** "
        f"({GRID_INITIAL_CONTRACTS} × {GRID_CHUNK_BTC:.3f} BTC)"
    )
    st.write(
        f"Upper target levels: **{GRID_UPPER_LEVELS} × {GRID_CHUNK_BTC:.3f} BTC** "
        f"over **{GRID_UPPER_LEVELS * GRID_STEP} points**"
    )
    st.write(
        f"ATR-side levels: **{len(lower_levels)}** — number depends only on ATR line distance."
    )
else:
    st.info("Confirmed SuperTrend BUY/SELL signal ka wait hai.")


# ------------------------------------------------------------
# DIRECTION CHANGE / FRESH-CYCLE CONTROL
# ------------------------------------------------------------
if "grid_last_direction" not in st.session_state:
    st.session_state["grid_last_direction"] = ""
if "grid_cycle_key" not in st.session_state:
    st.session_state["grid_cycle_key"] = ""

previous_grid_direction = st.session_state.get("grid_last_direction", "")
direction_changed = (
    bool(previous_grid_direction)
    and bool(grid_direction)
    and previous_grid_direction != grid_direction
)

# Any change of the confirmed signal price/time starts a fresh cycle.
_signal_epoch_for_grid = signal_time
try:
    _signal_epoch_for_grid = float(current_entry["time"])
except Exception:
    pass

new_cycle_key = _grid_signal_key(
    grid_direction,
    grid_base,
    _signal_epoch_for_grid
) if grid_direction else ""

fresh_signal_cycle = (
    bool(new_cycle_key)
    and new_cycle_key != st.session_state.get("grid_cycle_key", "")
)


# ------------------------------------------------------------
# REVERSAL: CANCEL ALL GRID LIMITS + CLOSE OLD POSITION
# ------------------------------------------------------------
if grid_direction and (direction_changed or fresh_signal_cycle):
    if grid_open_ok and grid_pos_ok:
        cancel_ok, cancelled_count = _grid_cancel_all_bot_limits(grid_open_orders)
        if not cancel_ok:
            st.error("🛑 Direction change blocked: old grid LIMIT cancellation failed.")
        else:
            # If any position exists, it belongs to the old cycle unless this
            # is the very first cycle with no prior direction.
            if previous_grid_direction:
                close_ok, close_result = _grid_close_position(
                    grid_position_size,
                    grid_direction,
                    "REVERSAL"
                )
                if not close_ok:
                    st.error("🛑 Direction change blocked: old position could not be closed.")
                else:
                    st.warning(
                        f"🔄 Direction changed {previous_grid_direction} → {grid_direction}. "
                        f"{cancelled_count} old grid LIMIT order(s) cancelled and old position closed."
                    )
                    # Force a fresh exchange snapshot after cleanup.
                    time.sleep(0.5)
                    grid_open_ok, grid_open_orders = _grid_fetch_orders()
                    grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()
                    if grid_open_ok and grid_pos_ok and abs(grid_position_size) < 0.000001:
                        st.session_state["grid_cycle_key"] = new_cycle_key
                        st.session_state["grid_last_direction"] = grid_direction
                        st.session_state["grid_initial_cycle"] = ""
                    else:
                        st.error("🛑 Old cycle is not fully cleared; new grid is blocked.")
            else:
                st.session_state["grid_cycle_key"] = new_cycle_key
                st.session_state["grid_last_direction"] = grid_direction
    else:
        st.error("🛑 Fresh grid blocked because Delta order/position verification failed.")
else:
    if grid_direction and not st.session_state.get("grid_cycle_key"):
        st.session_state["grid_cycle_key"] = new_cycle_key
        st.session_state["grid_last_direction"] = grid_direction


# ------------------------------------------------------------
# RE-FETCH AFTER ANY CLEANUP
# ------------------------------------------------------------
grid_open_ok, grid_open_orders = _grid_fetch_orders()
grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()

if grid_direction and grid_open_ok and grid_pos_ok:

    same_direction_position = (
        (grid_direction == "BUY" and grid_position_size > 0)
        or
        (grid_direction == "SELL" and grid_position_size < 0)
    )

    # --------------------------------------------------------
    # INITIAL 0.010 BTC POSITION
    # --------------------------------------------------------
    # Only the first cycle entry is market. Existing same-direction
    # position means the initial inventory already exists.
    if not same_direction_position and st.session_state.get("grid_initial_cycle") != new_cycle_key:
        entry_side = "buy" if grid_direction == "BUY" else "sell"
        initial_client_id = f"GRID_INITIAL_{grid_direction}_{new_cycle_key.split('_')[1] if '_' in new_cycle_key else int(time.time())}"
        initial_result = api.place_market_order(
            side=entry_side,
            size=GRID_INITIAL_CONTRACTS,
            client_order_id=initial_client_id
        )
        if isinstance(initial_result, dict) and initial_result.get("success") is True:
            st.session_state["grid_initial_cycle"] = new_cycle_key
            st.success(
                f"✅ Initial {grid_direction} position opened: "
                f"{GRID_INITIAL_BTC:.3f} BTC ({GRID_INITIAL_CONTRACTS} contracts)."
            )
            time.sleep(0.5)
            grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()
        else:
            st.error(f"❌ Initial {grid_direction} position failed: {initial_result}")

    # If direction is already established, do not chase price with a new
    # market entry. The grid works only with the pre-defined LIMIT levels.
    if grid_direction == "BUY":
        upper_side = "sell"
        lower_side = "buy"
    else:
        upper_side = "buy"
        lower_side = "sell"

    # --------------------------------------------------------
    # UPPER TARGET / RE-ENTRY PAIRS
    # --------------------------------------------------------
    # One rung is a two-step cycle:
    #   BUY direction : BUY lower -> SELL upper -> BUY lower -> ...
    #   SELL direction: SELL upper -> BUY lower -> SELL upper -> ...
    # The target is re-armed only after its paired re-entry has filled.
    # Therefore a strong move upward/downward never causes a chase entry.

    grid_open_ok, grid_open_orders = _grid_fetch_orders()
    grid_closed_ok, grid_closed_orders = _grid_fetch_closed_orders()

    def _closed_order_epoch(order):
        for key in ("updated_at", "created_at", "closed_at", "id"):
            value = order.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except Exception:
                continue
        return 0.0

    if grid_direction == "BUY":
        target_side = "sell"
        reentry_side = "buy"
    else:
        target_side = "buy"
        reentry_side = "sell"

    for i, target_price in enumerate(upper_levels, start=1):
        reentry_price = (
            target_price - GRID_STEP
            if grid_direction == "BUY"
            else target_price + GRID_STEP
        )

        target_open = _grid_has_price_side(
            grid_open_orders, target_side, target_price
        )
        reentry_open = _grid_has_price_side(
            grid_open_orders, reentry_side, reentry_price
        )

        latest_filled_role = ""
        if grid_closed_ok:
            relevant = []
            for closed in grid_closed_orders:
                if not _grid_is_bot(closed):
                    continue
                if str(closed.get("state", "")).lower() != "filled":
                    continue
                side = _grid_order_side(closed)
                price = _grid_price(closed)
                if price is None:
                    continue
                if side == target_side and abs(price - target_price) <= 0.01:
                    relevant.append(("TARGET", _closed_order_epoch(closed)))
                elif side == reentry_side and abs(price - reentry_price) <= 0.01:
                    relevant.append(("REENTRY", _closed_order_epoch(closed)))
            if relevant:
                latest_filled_role = max(relevant, key=lambda x: x[1])[0]

        # If either leg is already open, leave it alone.
        if target_open or reentry_open:
            continue

        # No previous fill: arm the target.
        # Target was filled last: arm the return/re-entry order.
        # Re-entry was filled last: arm the target again.
        if latest_filled_role == "TARGET":
            result = _grid_place_limit(
                side=reentry_side,
                price=reentry_price,
                label=f"REENTRY{i}",
                direction=grid_direction,
                size=GRID_CHUNK_CONTRACTS
            )
            if not (isinstance(result, dict) and result.get("success") is True):
                st.error(f"❌ REENTRY{i} failed at {show_price(reentry_price)}: {result}")
        else:
            # If this is a fresh target, it is always placed at its fixed
            # 200-point rung. It does not move with the market.
            result = _grid_place_limit(
                side=target_side,
                price=target_price,
                label=f"TARGET{i}",
                direction=grid_direction,
                size=GRID_CHUNK_CONTRACTS
            )
            if not (isinstance(result, dict) and result.get("success") is True):
                st.error(f"❌ TARGET{i} failed at {show_price(target_price)}: {result}")

    # --------------------------------------------------------
    # ATR-SIDE LIMITS
    # --------------------------------------------------------
    # These are independent lower/upper accumulation orders. They stop at
    # the SuperTrend ATR line and are never extended beyond it.
    grid_open_ok, grid_open_orders = _grid_fetch_orders()
    for i, price in enumerate(lower_levels, start=1):
        if not _grid_has_price_side(grid_open_orders, lower_side, price):
            result = _grid_place_limit(
                side=lower_side,
                price=price,
                label=f"ATR{i}",
                direction=grid_direction,
                size=GRID_CHUNK_CONTRACTS
            )
            if not (isinstance(result, dict) and result.get("success") is True):
                st.error(f"❌ ATR{i} failed at {show_price(price)}: {result}")


# ------------------------------------------------------------
# LIVE GRID ORDER DISPLAY
# ------------------------------------------------------------
grid_open_ok, grid_open_orders = _grid_fetch_orders()

st.subheader("📋 LIVE GRID LIMIT ORDERS")

bot_rows = []
for order in _grid_active_bot_orders(grid_open_orders if grid_open_ok else []):
    p = _grid_price(order)
    q = _grid_contracts(order)
    bot_rows.append({
        "Order ID": order.get("id", "-"),
        "Client ID": order.get("client_order_id", "-"),
        "Side": str(order.get("side", "")).upper(),
        "Price": show_price(p) if p is not None else "-",
        "Qty": f"{q * CONTRACT_BTC:.3f} BTC",
        "Contracts": q,
        "Status": order.get("state", "-")
    })

if bot_rows:
    st.dataframe(pd.DataFrame(bot_rows), use_container_width=True, hide_index=True)
else:
    st.info("Grid ke open LIMIT orders abhi exchange par nahi mile.")


# ------------------------------------------------------------
# LIVE POSITION
# ------------------------------------------------------------
if grid_pos_ok:
    st.subheader("📍 LIVE GRID POSITION")
    direction_text = "LONG / BUY" if grid_position_size > 0 else ("SHORT / SELL" if grid_position_size < 0 else "FLAT")
    st.write(
        f"Position: **{direction_text}** | "
        f"Contracts: **{abs(int(grid_position_size))}** | "
        f"BTC: **{abs(grid_position_size) * CONTRACT_BTC:.3f} BTC**"
    )


# ------------------------------------------------------------
# CURRENT SIGNAL / GRID SUMMARY
# ------------------------------------------------------------
st.divider()
st.subheader("📡 GRID SUMMARY")
st.write(f"SuperTrend direction: **{grid_direction or '-'}**")
st.write(f"Signal price: **{show_price(grid_base)}**")
st.write(f"ATR line: **{show_price(grid_atr_line)}**")
st.write(f"Upper grid: **{GRID_UPPER_LEVELS} levels × {GRID_STEP} points**")
st.write(f"ATR-side grid: **{len(lower_levels)} levels**")
st.write(f"Initial inventory: **{GRID_INITIAL_BTC:.3f} BTC**")
st.write("**No new order is chased above the final upper level. Direction change cancels the old grid.**")


# ============================================================
# PART 4/4 — TIME / CHART / REFRESH
# ============================================================

now_ist = datetime.now(timezone.utc).astimezone(IST)
st.write(f"Dashboard Time: **{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}**")

components.html(
    """
    <div class="tradingview-widget-container" style="height:700vh;width:100%;">
      <div class="tradingview-widget-container__widget" style="height:700%;width:100%;"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
      {
        "autosize": false,
        "height": 700,
        "symbol": "BINANCE:BTCUSDT",
        "interval": "60",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "studies": [{"id":"SuperTrend@tv-basicstudies","inputs":{"length":10,"factor":3}}],
        "enable_publishing": false,
        "allow_symbol_change": true,
        "hide_top_toolbar": false,
        "hide_legend": false,
        "save_image": false,
        "hide_volume": false,
        "support_host": "https://www.tradingview.com"
      }
      </script>
    </div>
    """,
    height=700,
    scrolling=False
)

time.sleep(REFRESH_SECONDS)
st.rerun()
