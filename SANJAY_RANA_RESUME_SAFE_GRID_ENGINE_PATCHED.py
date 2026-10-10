# ============================================================
# SANJAY RANA - DELTA DEMO / TESTNET DASHBOARD
# COMPLETE CONSOLIDATED FILE — CODE AUDIT V8
# ============================================================

import os
import time
import json
import hmac
import hashlib
import re
import uuid
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# SETTINGS
# ============================================================

# ============================================================
# ONE-LINE ENVIRONMENT SWITCH
# Keep DEMO while testing. Change ONLY this value to "REAL" when
# you intentionally want to connect the production account.
# Real mode requires REAL_OWNER_API_KEY / REAL_OWNER_API_SECRET in
# Streamlit Secrets (or environment variables); never paste secrets here.
# ============================================================
TRADING_MODE = "DEMO"  # Change to "REAL" only after Demo validation.
TRADING_MODE = str(TRADING_MODE).strip().upper()
if TRADING_MODE not in {"DEMO", "REAL"}:
    raise ValueError('TRADING_MODE must be exactly "DEMO" or "REAL"')

if TRADING_MODE == "DEMO":
    BASE_URL = "https://cdn-ind.testnet.deltaex.org"
    PUBLIC_WS_URL = "wss://socket-ind-pub.testnet.deltaex.org"
    PUBLIC_REST_V2_URL = BASE_URL + "/v2"
else:
    BASE_URL = "https://api.india.delta.exchange"
    PUBLIC_WS_URL = "wss://public-socket.india.delta.exchange"
    PUBLIC_REST_V2_URL = BASE_URL + "/v2"


def _read_secret(name, default=""):
    """Read a credential from Streamlit Secrets first, then environment."""
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = os.getenv(name, default)
    if value in (None, ""):
        value = os.getenv(name, default)
    return str(value or "")


def _load_owner_credentials():
    if TRADING_MODE == "DEMO":
        # Legacy OWNER_* names remain supported for the current Demo setup.
        key = _read_secret("DEMO_OWNER_API_KEY") or _read_secret("OWNER_API_KEY")
        secret = _read_secret("DEMO_OWNER_API_SECRET") or _read_secret("OWNER_API_SECRET")
    else:
        # Do not fall back to Demo credentials in Real mode.
        key = _read_secret("REAL_OWNER_API_KEY")
        secret = _read_secret("REAL_OWNER_API_SECRET")
    return key, secret


SYMBOL = os.getenv("DELTA_SYMBOL", "BTCUSD")
PRODUCT_ID = int(os.getenv("DELTA_PRODUCT_ID", "-1"))
PRODUCT_VERIFIED = False
PRODUCT_DETAILS = {}
PRODUCT_ERROR = f"{TRADING_MODE} BTCUSD product has not been verified yet."

# ------------------------------------------------------------
# SUPERTREND REMOTE CONTROL DEFAULTS
# ------------------------------------------------------------
# These are defaults only. The live dashboard controls below can
# change timeframe / ATR period / multiplier without editing this file.
TIMEFRAME = os.getenv("SUPERTREND_TIMEFRAME", "1h")
ATR_PERIOD = int(os.getenv("SUPERTREND_ATR_PERIOD", "10"))
MULTIPLIER = float(os.getenv("SUPERTREND_MULTIPLIER", "2.0"))

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
}
CANDLE_SECONDS = _TIMEFRAME_SECONDS.get(TIMEFRAME, 3600)

REFRESH_SECONDS = 5

# Mode flags are derived from the one switch above; do not edit separately.
DEMO_TESTNET_ONLY = (TRADING_MODE == "DEMO")
REAL_MARKET_ONLY = (TRADING_MODE == "REAL")

# ============================================================
# DEFAULT REMOTE CONTROL SETTINGS
# ============================================================

DEFAULT_BUY_OFFSET = int(
    os.getenv("BUY_OFFSET", "0")
)

DEFAULT_SELL_OFFSET = int(
    os.getenv("SELL_OFFSET", "0")
)

# ============================================================
# GRID QUANTITY CONTROL
# This setting is visible in the dashboard and can be changed
# without editing the Python file.
# 0.001 BTC = 1 contract per grid order
# 0.002 BTC = 2 contracts per grid order
# 0.003 BTC = 3 contracts per grid order
# ============================================================
CONTRACT_BTC = 0.001

# ============================================================
# OWNER ACCOUNT — SINGLE ACCOUNT SETTINGS
# ============================================================
OWNER_DEFAULT_QTY = 0.001  # requested default: 0.001 BTC per order
if OWNER_DEFAULT_QTY <= 0:
    OWNER_DEFAULT_QTY = 0.001

OWNER_GRID_QTY = st.number_input(
    "👑 OWNER ACCOUNT — GRID QUANTITY PER ORDER (BTC)",
    min_value=0.001,
    value=max(0.001, float(OWNER_DEFAULT_QTY)),
    step=0.001,
    format="%.3f",
    key="owner_grid_order_qty_setting_v2",
)
ACCOUNT_VIEW = "OWNER ACCOUNT"

def _qty_to_contracts(qty_btc):
    total = float(qty_btc) / CONTRACT_BTC
    if abs(total - round(total)) > 1e-9:
        raise ValueError("GRID QUANTITY must be a multiple of 0.001 BTC")
    return int(round(total))

OWNER_ORDER_SIZE = _qty_to_contracts(OWNER_GRID_QTY)

# ============================================================
# OWNER ACCOUNT DIRECTION REMOTE CONTROL
# ============================================================
_DIRECTION_OPTIONS = ["SUPER TREND", "OPPOSITE", "BUY", "SELL"]

OWNER_DIRECTION_MODE = st.selectbox(
    "🎛️ OWNER ACCOUNT — TRADING DIRECTION",
    _DIRECTION_OPTIONS,
    index=_DIRECTION_OPTIONS.index("SUPER TREND"),
    key="owner_direction_mode",
)

OWNER_EFFECTIVE_DIRECTION = ""

def _resolve_account_direction(mode, supertrend_direction):
    mode = str(mode or "SUPER TREND").upper().strip()
    st_dir = str(supertrend_direction or "").upper().strip()
    if mode == "BUY":
        return "BUY"
    if mode == "SELL":
        return "SELL"
    if mode == "OPPOSITE":
        return "SELL" if st_dir == "BUY" else "BUY" if st_dir == "SELL" else ""
    return st_dir

# Backward-compatible names used by the existing engine.
ORDER_QTY = float(OWNER_GRID_QTY)
DEFAULT_ORDER_SIZE = int(OWNER_ORDER_SIZE)

# LIMIT pending रहने के बाद कितने seconds में MARKET करना है.
# 0 = automatic MARKET conversion बंद.
DEFAULT_LIMIT_TIMEOUT = int(
    os.getenv("LIMIT_TIMEOUT", "6000000")
)

TARGET_1 = int(os.getenv("TARGET_1", "100"))
TARGET_2 = int(os.getenv("TARGET_2", "200"))
TARGET_3 = int(os.getenv("TARGET_3", "300"))
TARGET_4 = int(os.getenv("TARGET_4", "400"))
TARGET_5 = int(os.getenv("TARGET_5", "500"))
TARGET_6 = int(os.getenv("TARGET_6", "600"))
TARGET_7 = int(os.getenv("TARGET_7", "700"))
TARGET_8 = int(os.getenv("TARGET_8", "800"))
TARGET_9 = int(os.getenv("TARGET_9", "900"))
TARGET_10 = int(os.getenv("TARGET_10", "1000"))
TARGET_11 = int(os.getenv("TARGET_11", "1100"))
TARGET_12 = int(os.getenv("TARGET_12", "1200"))
TARGET_13 = int(os.getenv("TARGET_13", "1300"))
TARGET_14 = int(os.getenv("TARGET_14", "1400"))
TARGET_15 = int(os.getenv("TARGET_15", "1500"))
TARGET_16 = int(os.getenv("TARGET_16", "1600"))
TARGET_17 = int(os.getenv("TARGET_17", "1700"))
TARGET_18 = int(os.getenv("TARGET_18", "1800"))
TARGET_19 = int(os.getenv("TARGET_19", "1900"))
TARGET_20 = int(os.getenv("TARGET_20", "2000"))
TARGET_21 = int(os.getenv("TARGET_21", "2100"))
TARGET_22 = int(os.getenv("TARGET_22", "2200"))
TARGET_23 = int(os.getenv("TARGET_23", "2300"))
TARGET_24 = int(os.getenv("TARGET_24", "2400"))
TARGET_25 = int(os.getenv("TARGET_25", "2500"))

# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="Sanjay Rana Real Trading",
    page_icon="📈",
    layout="wide"
)


# ============================================================
# SUPERTREND REMOTE CONTROL — MAIN DASHBOARD (VISIBLE)
# ============================================================
st.markdown(
    """
    <div style="margin-top:10px;padding:14px 16px;border-radius:14px;
    background:linear-gradient(135deg,#0f172a,#172554);
    border:2px solid #38bdf8;box-shadow:0 0 16px rgba(56,189,248,.18);">
      <div style="font-size:22px;font-weight:950;color:#38bdf8;">🎛️ SUPERTREND REMOTE CONTROL</div>
      <div style="margin-top:4px;font-size:13px;font-weight:700;color:#cbd5e1;">
        Timeframe / ATR / Multiplier यहाँ से बदलें — बदलते ही SuperTrend, Signal, Grid Anchor और History उसी setting पर recalculate होंगे.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

_tf_options = ["1m", "5m", "15m", "30m", "1h", "2h", "4h"]
_default_tf_index = _tf_options.index(TIMEFRAME) if TIMEFRAME in _tf_options else _tf_options.index("1h")

_rc1, _rc2, _rc3 = st.columns(3)
with _rc1:
    _remote_timeframe = st.selectbox(
        "⏱️ TIME FRAME",
        _tf_options,
        index=_default_tf_index,
        key="remote_supertrend_timeframe",
        format_func=lambda x: x.upper(),
    )
with _rc2:
    _remote_atr_period = st.number_input(
        "📐 ATR PERIOD",
        min_value=1,
        max_value=100,
        value=max(1, min(100, int(ATR_PERIOD))),
        step=1,
        key="remote_supertrend_atr_period",
    )
with _rc3:
    _remote_multiplier = st.number_input(
        "⚙️ SUPERTREND MULTIPLIER",
        min_value=0.1,
        max_value=20.0,
        value=float(MULTIPLIER),
        step=0.1,
        format="%.1f",
        key="remote_supertrend_multiplier",
    )

# These are the live values consumed by the existing engine.
TIMEFRAME = str(_remote_timeframe)
ATR_PERIOD = int(_remote_atr_period)
MULTIPLIER = float(_remote_multiplier)
CANDLE_SECONDS = _TIMEFRAME_SECONDS[TIMEFRAME]

st.markdown(
    f"<div style='margin:6px 0 14px 0;padding:9px 12px;border-radius:9px;background:#111827;border:1px solid #334155;color:#e2e8f0;font-weight:900;'>🎯 ACTIVE SUPERTREND: <span style='color:#22c55e;'>{TIMEFRAME.upper()}</span> &nbsp;|&nbsp; ATR <span style='color:#38bdf8;'>{ATR_PERIOD}</span> &nbsp;|&nbsp; MULTIPLIER <span style='color:#facc15;'>{MULTIPLIER:.1f}</span> &nbsp;|&nbsp; HL2 &nbsp;|&nbsp; Confirmed Candle Close</div>",
    unsafe_allow_html=True,
)


st.markdown(
    f"""
    <style>
    .sr-banner {{
        position: relative; width: 100%; overflow: hidden;
        border-radius: 12px; margin: 0 0 16px 0;
    }}
    .sr-banner img {{display:block;width:100%;height:auto;}}
    .sr-banner-title {{
        position:absolute; top:2%; left:50%; transform:translateX(-50%);
        width:max-content; max-width:96%; text-align:center;
        font-size:clamp(15px, 2.5vw, 39px); font-weight:900;
        color:#ffffff; text-shadow:0 2px 7px #000, 0 0 12px #000;
        background:rgba(0,0,0,.72); border-radius:9px;
        padding:8px 15px; letter-spacing:.5px;
    }}
    @media(max-width:600px) {{
        .sr-banner-title {{font-size:clamp(10px,3.15vw,18px);padding:5px 7px;top:1%;}}
    }}
    </style>
    <div class="sr-banner">
      <div class="sr-banner-title">SANJAY RANA REAL DASHBOARD GRID BOT</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# Removed decorative SANJAY RANA / animated SuperTrend BUY-SELL panel (not real market data).

st.caption(
    "1 Hour | ATR 10 | Multiplier 2.0 | HL2 | "
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


def _grid_log_event(account_id, level_no, role, message, price=None):
    if "grid_error_log" not in st.session_state:
        st.session_state["grid_error_log"] = []
    st.session_state["grid_error_log"].append({
        "time": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "account": account_id,
        "level": f"L{level_no}" if level_no else "-",
        "role": role,
        "price": price,
        "message": str(message),
    })
    st.session_state["grid_error_log"] = st.session_state["grid_error_log"][-300:]


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

        # Keep the existing candle source/logic, but fetch enough completed
        # candles for the requested rolling 80-day SuperTrend history.
        end = int(time.time())
        chunk = 500 * CANDLE_SECONDS
        all_rows = []

        for _n in range(4):
            start = end - chunk
            response = self.request(
                "GET",
                "/v2/history/candles",
                params={
                    "symbol": SYMBOL,
                    "resolution": TIMEFRAME,
                    "start": start,
                    "end": end
                }
            )
            if not isinstance(response, dict) or not response.get("success"):
                if all_rows:
                    break
                return response
            result = response.get("result")
            if isinstance(result, list):
                all_rows.extend(result)
            end = start

        return {
            "success": True,
            "result": all_rows
        }


    def ticker(self):

        return self.request(
            "GET",
            f"/v2/tickers/{SYMBOL}"
        )


    # --------------------------------------------------------
    # PRIVATE
    # --------------------------------------------------------

    def open_orders(self):

        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}


        return self.request(
            "GET",
            "/v2/orders",
            params={
                "product_id": PRODUCT_ID,
                "state": "open"
            },
            private=True
        )


    def _fetch_product_history_pages(self, path):
        """Fetch up to 5 cursor pages so older fills/orders are not silently missed.

        Delta removed the old `total` metadata field; `meta.after` is the
        cursor used for the next page. This is read-only and never submits or
        cancels an order.
        """
        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}

        rows = []
        cursor = None
        seen_cursors = set()
        last_response = None
        for _page in range(5):
            params = {"product_ids": str(PRODUCT_ID), "page_size": 50}
            if cursor:
                params["after"] = cursor
            response = self.request("GET", path, params=params, private=True)
            last_response = response
            if not isinstance(response, dict) or response.get("success") is not True:
                if rows:
                    break
                return response if isinstance(response, dict) else {"success": False, "error": "Invalid history response"}

            result = response.get("result", [])
            if isinstance(result, list):
                rows.extend(item for item in result if isinstance(item, dict))
            elif isinstance(result, dict):
                nested = result.get("orders", result.get("fills", []))
                if isinstance(nested, list):
                    rows.extend(item for item in nested if isinstance(item, dict))

            meta = response.get("meta", {})
            next_cursor = meta.get("after") if isinstance(meta, dict) else None
            if not next_cursor or str(next_cursor) in seen_cursors:
                break
            seen_cursors.add(str(next_cursor))
            cursor = str(next_cursor)

        return {"success": True, "result": rows, "meta": {"pages_limit": 5, "rows_loaded": len(rows)}}


    def closed_orders(self):
        """Fetch recent closed/cancelled order history with cursor pagination."""
        return self._fetch_product_history_pages("/v2/orders/history")


    def fills(self):
        """Fetch real execution records with cursor pagination."""
        return self._fetch_product_history_pages("/v2/fills")


    def position(self):

        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}


        return self.request(
            "GET",
            "/v2/positions",
            params={
                "product_id": PRODUCT_ID
            },
            private=True
        )


    # --------------------------------------------------------
    # DEMO ACCOUNT BALANCE / MARGIN / LEVERAGE
    # Demo/Testnet account snapshot methods
    # --------------------------------------------------------

    def wallet_balances(self):

        return self.request(
            "GET",
            "/v2/wallet/balances",
            private=True
        )


    def order_leverage(self):

        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}


        return self.request(
            "GET",
            f"/v2/products/{PRODUCT_ID}/orders/leverage",
            private=True
        )


    def margined_positions(self):

        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}


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
        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}

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
        client_order_id=None,
        reduce_only=False
    ):
        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}

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

            # Delta REST v2 defines reduce_only as a JSON boolean. Sending the
            # string "true" can be rejected or ignored by strict validators,
            # which leaves the target absent on the exchange.
            "reduce_only": bool(reduce_only)
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
        if not PRODUCT_VERIFIED:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}

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
        # Fail closed: never send a cancellation request with an unknown
        # or unverified Testnet product ID.
        if not PRODUCT_VERIFIED or PRODUCT_ID <= 0:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}

        # Delta Exchange India REST v2 cancellation uses DELETE /v2/orders
        # with the order id/product id in the request body.  Keep this as
        # the canonical single-order cancellation path so manual orders
        # created outside this bot can also be cancelled reliably.
        return self.request(
            "DELETE",
            "/v2/orders",
            body={
                "id": int(order_id),
                "product_id": PRODUCT_ID,
            },
            private=True
        )


    def cancel_all_open_orders_for_product(self):
        # Fail closed: do not cancel against an unknown/invalid product ID.
        if not PRODUCT_VERIFIED or PRODUCT_ID <= 0:
            return {"success": False, "error": {"code": "product_unverified", "message": PRODUCT_ERROR}}

        # Delta Exchange India REST v2 supports account-local cancellation
        # for one product.  All three filters are enabled deliberately so
        # reversal cleanup includes limit, stop/trigger and reduce-only
        # active orders belonging to THIS API account/product.
        return self.request(
            "DELETE",
            "/v2/orders/all",
            body={
                "product_id": PRODUCT_ID,
                "cancel_limit_orders": True,
                "cancel_stop_orders": True,
                "cancel_reduce_only_orders": True,
            },
            private=True
        )


# ============================================================
# API CREDENTIALS (OWNER ONLY FROM GITHUB SECRETS)
# ============================================================
# यह कोड GitHub Secrets से कीज़ खुद ले लेता है
OWNER_API_KEY, OWNER_API_SECRET = _load_owner_credentials()

OWNER_KEY = OWNER_API_KEY
OWNER_SECRET = OWNER_API_SECRET




API_KEY = OWNER_KEY
API_SECRET = OWNER_SECRET


def _resolve_exchange_product(symbol=SYMBOL):
    """Resolve the symbol from the selected exchange catalog; never reuse IDs across modes."""
    try:
        response = requests.get(
            f"{BASE_URL}/v2/products",
            params={"symbol": symbol},
            headers={"Accept": "application/json"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("success"):
            return -1, False, {}, f"Exchange product lookup failed: {payload}"

        result = payload.get("result", [])
        if isinstance(result, dict):
            products = [result]
        elif isinstance(result, list):
            products = result
        else:
            products = []

        product = next(
            (item for item in products
             if isinstance(item, dict)
             and str(item.get("symbol", "")).upper() == str(symbol).upper()),
            None,
        )
        if not product:
            return -1, False, {}, f"Symbol {symbol} was not found in the exchange product catalog."

        product_id = product.get("id")
        if product_id is None:
            return -1, False, product, "Exchange product response did not contain an ID."

        state = str(product.get("state", "")).lower()
        trading_status = str(product.get("trading_status", "")).lower()
        # Delta product payloads commonly use state=live. If trading_status
        # is present, reject explicit non-operational statuses as well.
        if state != "live":
            return int(product_id), False, product, f"Exchange contract state is '{state or 'unknown'}', not 'live'."
        if trading_status and trading_status not in {"operational", "live", "trading"}:
            return int(product_id), False, product, f"Exchange trading status is '{trading_status}', not operational."

        return int(product_id), True, product, ""
    except Exception as exc:
        return -1, False, {}, f"Exchange product lookup error: {exc}"


PRODUCT_ID, PRODUCT_VERIFIED, PRODUCT_DETAILS, PRODUCT_ERROR = _resolve_exchange_product(SYMBOL)

api = DeltaAPI()



# ============================================================
if True:
    # BASIC STATUS (DEMO / TESTNET ONLY)
    # ============================================================

    st.info(
        f"🧪 {TRADING_MODE} MODE — orders are routed to the selected {TRADING_MODE} environment."
    )
    if PRODUCT_VERIFIED:
        st.success(
            f"🧪 {TRADING_MODE} product verified: {SYMBOL} • Product ID {PRODUCT_ID} • State: {PRODUCT_DETAILS.get('state', 'live')}"
        )
    else:
        st.error(
            f"🛑 {TRADING_MODE} product NOT verified: {PRODUCT_ERROR} New orders are blocked until the contract is verified."
        )

        # ============================================================
    # OWNER API CONTROL
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
    OWNER_API_KEY, OWNER_API_SECRET = _load_owner_credentials()
        

    # ============================================================
    # OWNER STATUS (AUTOMATIC HEALTH CHECK)
    # ============================================================

    if not OWNER_API_KEY or not OWNER_API_SECRET:
        st.error(f"❌ {'DEMO_OWNER_API_KEY / DEMO_OWNER_API_SECRET (or legacy OWNER_API_KEY / OWNER_API_SECRET)' if TRADING_MODE == 'DEMO' else 'REAL_OWNER_API_KEY / REAL_OWNER_API_SECRET'} Secrets/environment में नहीं मिले।")
        st.session_state["owner_api_connected"] = False
    else:
        try:
            owner_client = DeltaAPI(OWNER_API_KEY, OWNER_API_SECRET)
            owner_result = owner_client.wallet_balances()
            
            if owner_result.get("success"):
                st.success(f"🧪 Owner {TRADING_MODE} API: CONNECTED 🟢")
                if not PRODUCT_VERIFIED:
                    st.warning(f"⚠️ API credentials responded, but BTCUSD exchange product is not verified: {PRODUCT_ERROR}")
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
            st.header("🧪 DELTA DEMO ACCOUNT — TESTNET")
            st.caption(f"यह section Delta Exchange {TRADING_MODE} के private API से account data पढ़ता है।")

            _a1, _a2, _a3, _a4, _a5 = st.columns(5)
            with _a1:
                st.metric("DEMO BALANCE", str(_balance) if _balance is not None else "-")
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

            st.subheader("📌 DEMO OPEN ORDERS — DELTA TESTNET")
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
# MEMBER API / MEMBER SUMMARY REMOVED
# OWNER API ONLY
# ============================================================

# ============================================================
# END — OWNER API BLOCK
# ============================================================
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
# ONLY COMPLETED CANDLES FOR THE SELECTED TIMEFRAME
# ============================================================
# Delta candle timestamps are used directly. This avoids hard-coding
# the old 1-hour :30 boundary and works for every remote timeframe.
_now_epoch = int(time.time())
if not df.empty:
    _last_candle_start = int(pd.to_numeric(df["time"].iloc[-1], errors="coerce"))
    if _last_candle_start > 10_000_000_000:
        _last_candle_start //= 1000
    if _last_candle_start + CANDLE_SECONDS > _now_epoch:
        df = df.iloc[:-1].copy()

df = df.reset_index(drop=True)

df = df.reset_index(drop=True)

if len(df) < ATR_PERIOD + 5:
    st.error("SuperTrend ke liye enough candles nahi hain.")
    st.stop()


# ============================================================
# ============================================================
# STANDARD TRADINGVIEW SUPERTREND REMOTE-CONTROL ENGINE
# ============================================================
# Reference chart: regular Delta BTCUSD candles for the selected timeframe,
# SuperTrend uses the selected ATR period/multiplier, Source = HL2.
#
# This is NOT Heikin-Ashi.
# ATR = TradingView-style Wilder/RMA.
# Only completed 1-hour Delta candles reach this engine.
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

# Rolling history for the selected timeframe.
# The API fetch depth is resolution-dependent; keep up to 45 days from
# the candles actually fetched, so the history always matches the setting.
try:
    _history_cutoff = int(time.time()) - (45 * 24 * 60 * 60)
    _signal_epoch = pd.to_numeric(signal_rows["time"], errors="coerce")
    _signal_epoch = _signal_epoch.where(_signal_epoch < 10_000_000_000, _signal_epoch / 1000.0)
    signal_rows = signal_rows[_signal_epoch >= _history_cutoff].copy()
except Exception:
    pass


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
# LIVE MARKET PRICE — ADDITIVE ONLY
# ------------------------------------------------------------
# Strategy/SuperTrend calculations continue to use completed 1H candles.
# This separate value is the live Delta ticker price used only for the
# live-price display/line on the existing chart. The normal 5-second
# Streamlit refresh keeps this price updating while the dashboard is open.
# ============================================================

live_price = current_close
try:
    _live_ticker_response = api.ticker()
    _live_ticker_result = (
        _live_ticker_response.get("result")
        if isinstance(_live_ticker_response, dict)
        else None
    )

    if isinstance(_live_ticker_result, dict):
        for _live_key in (
            "close",
            "last_price",
            "mark_price",
            "spot_price",
            "price"
        ):
            _live_value = _live_ticker_result.get(_live_key)
            if _live_value is not None and str(_live_value).strip() != "":
                live_price = float(_live_value)
                break
except Exception:
    live_price = current_close

if not isinstance(live_price, (int, float)) or live_price <= 0:
    live_price = current_close


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
# COLOURED SUPERTREND HISTORY — ROLLING 80 DAYS
# ============================================================

if not signal_rows.empty:
    _history_html = [
        '<div style="overflow-x:auto;width:100%;">',
        '<table style="width:100%;border-collapse:collapse;font-size:0.88rem;">',
        '<thead><tr style="background:#111827;color:#cbd5e1;">'
        '<th style="padding:8px;text-align:left;">#</th>'
        '<th style="padding:8px;text-align:left;">DATE / TIME</th>'
        '<th style="padding:8px;text-align:left;">DIRECTION</th>'
        '<th style="padding:8px;text-align:right;">SIGNAL PRICE</th>'
        '<th style="padding:8px;text-align:right;">POINT CHANGE</th>'
        '<th style="padding:8px;text-align:right;">POINTS DIFFERENCE</th>'
        '<th style="padding:8px;text-align:right;">SUPERTREND</th>'
        '</tr></thead><tbody>'
    ]
    _history_rows = signal_rows.sort_values("time").reset_index(drop=True)
    # Keep exactly the signal history produced for the selected timeframe.
    # No stale 1H history is mixed into another timeframe.
    _history_rows["_epoch"] = pd.to_numeric(_history_rows["time"], errors="coerce")
    _history_rows.loc[_history_rows["_epoch"] > 10_000_000_000, "_epoch"] = _history_rows.loc[_history_rows["_epoch"] > 10_000_000_000, "_epoch"] / 1000.0
    _history_rows = _history_rows.dropna(subset=["_epoch"]).reset_index(drop=True)
    for _idx, _row in _history_rows.iterrows():
        _sig = str(_row["SIGNAL"]).upper()
        _price = float(_row["close"])
        _st = float(_row["SUPERTREND"])
        _dt = indian_time(_row["time"])
        if _idx == 0:
            _signed = None
            _absdiff = None
        else:
            _prev_row = _history_rows.iloc[_idx - 1]
            _prev_price = float(_prev_row["close"])
            _prev_signal = str(_prev_row["SIGNAL"]).upper()

            # Direction-aware +/-:
            # Previous BUY  -> higher next signal price = positive
            # Previous SELL -> lower next signal price = positive
            # This makes history +/- represent the result of the
            # previous SuperTrend trade, not just raw price movement.
            if _prev_signal == "BUY":
                _signed = _price - _prev_price
            elif _prev_signal == "SELL":
                _signed = _prev_price - _price
            else:
                _signed = _price - _prev_price

            _absdiff = abs(_signed)
        _bg = "#0d2418" if _sig == "BUY" else "#2a1111"
        _badge_bg = "#14532d" if _sig == "BUY" else "#7f1d1d"
        _badge_color = "#86efac" if _sig == "BUY" else "#fca5a5"
        _signal_text_color = "#86efac" if _sig == "BUY" else "#fca5a5"
        if _signed is None:
            _change_html = '<span style="color:#fca5a5;font-weight:700;">—</span>'
            _abs_html = '<span style="color:#fca5a5;font-weight:700;">—</span>'
        else:
            _change_color = "#86efac" if _signed >= 0 else "#fca5a5"
            _change_html = f'<span style="color:{_change_color};font-weight:700;">{_signed:+,.2f}</span>'
            _abs_html = f'<span style="color:{_change_color};font-weight:700;">{_absdiff:,.2f}</span>'
        _history_html.append(
            f'<tr style="background:{_bg};border-bottom:1px solid #252a31;">'
            f'<td style="padding:8px;">{_idx+1}</td>'
            f'<td style="padding:8px;white-space:nowrap;color:{_signal_text_color};font-weight:800;">{_dt}</td>'
            f'<td style="padding:8px;"><span style="display:inline-block;padding:4px 9px;border-radius:999px;background:{_badge_bg};color:{_badge_color};font-weight:700;">{"🟢 BUY" if _sig == "BUY" else "🔴 SELL"}</span></td>'
            f'<td style="padding:8px;text-align:right;color:{_signal_text_color};font-weight:900;">{show_price(_price)}</td>'
            f'<td style="padding:8px;text-align:right;">{_change_html}</td>'
            f'<td style="padding:8px;text-align:right;">{_abs_html}</td>'
            f'<td style="padding:8px;text-align:right;">{show_price(_st)}</td>'
            f'</tr>'
        )
    _history_html.append('</tbody></table></div>')
    _green_points = 0.0
    _red_points = 0.0
    for _i in range(1, len(_history_rows)):
        _prev_row = _history_rows.iloc[_i - 1]
        _curr_row = _history_rows.iloc[_i]
        _prev_price = float(_prev_row["close"])
        _curr_price = float(_curr_row["close"])
        _prev_signal = str(_prev_row["SIGNAL"]).upper()

        if _prev_signal == "BUY":
            _delta = _curr_price - _prev_price
        elif _prev_signal == "SELL":
            _delta = _prev_price - _curr_price
        else:
            _delta = _curr_price - _prev_price

        if _delta > 0:
            _green_points += _delta
        elif _delta < 0:
            _red_points += _delta
    _history_html.append(
        '<div style="margin-top:10px;padding:12px 14px;border-radius:10px;background:#111827;border:1px solid #252a31;display:flex;gap:22px;flex-wrap:wrap;align-items:center;">'
        f'<span style="font-weight:800;color:#86efac;">🟢 GREEN POINTS: +{_green_points:,.2f}</span>'
        f'<span style="font-weight:800;color:#fca5a5;">🔴 RED POINTS: {_red_points:,.2f}</span>'
        '</div>'
    )
    # ------------------------------------------------------------
    # SUPERTREND TRADE SUMMARY — BASED ON THE DISPLAYED POINT CHANGE
    # ------------------------------------------------------------
    # The history table already shows POINT CHANGE for every signal.
    # Count exactly those displayed point changes:
    #   positive point change  = WIN
    #   negative point change  = LOSS
    # The first history row has no previous signal, so it is not counted.
    # The latest/current signal is also excluded because its result is not
    # available until the next SuperTrend signal.
    _wins = 0
    _losses = 0

    for _trade_i in range(1, len(_history_rows) - 1):
        _prev_row = _history_rows.iloc[_trade_i - 1]
        _result_row = _history_rows.iloc[_trade_i]
        _entry_price = float(_prev_row["close"])
        _result_price = float(_result_row["close"])
        _entry_signal = str(_prev_row["SIGNAL"]).upper()

        if _entry_signal == "BUY":
            _point_change = _result_price - _entry_price
        elif _entry_signal == "SELL":
            _point_change = _entry_price - _result_price
        else:
            _point_change = _result_price - _entry_price

        if _point_change > 0:
            _wins += 1
        elif _point_change < 0:
            _losses += 1

    _total_trades = _wins + _losses
    _accuracy = (
        (_wins / _total_trades) * 100.0
        if _total_trades > 0
        else 0.0
    )

    _history_from = indian_time(_history_rows.iloc[0]["time"])
    _history_to = indian_time(_history_rows.iloc[-1]["time"])

    _trade_summary_html = f"""
    <div style="
        margin:10px 0 14px 0;
        padding:18px 18px;
        border-radius:12px;
        background:#111827;
        border:1px solid #252a31;
        color:#e5e7eb;
    ">
        <div style="font-size:0.92rem;font-weight:800;color:#cbd5e1;margin-bottom:10px;">
            📅 HISTORY PERIOD
        </div>
        <div style="font-size:0.95rem;font-weight:700;margin-bottom:14px;">
            {_history_from} &nbsp; → &nbsp; {_history_to}
        </div>
        <div style="
            display:flex;
            gap:12px;
            flex-wrap:wrap;
            align-items:stretch;
        ">
            <div style="flex:1;min-width:145px;padding:14px 14px;border-radius:12px;background:#243244;border:1px solid #475569;">
                <div style="font-size:0.82rem;color:#cbd5e1;font-weight:800;">TOTAL TRADE</div>
                <div style="font-size:1.70rem;font-weight:950;color:#ffffff;">{_total_trades}</div>
            </div>
            <div style="flex:1;min-width:120px;padding:14px 14px;border-radius:12px;background:#123d27;border:1px solid #22c55e;">
                <div style="font-size:0.82rem;color:#86efac;font-weight:900;">WIN</div>
                <div style="font-size:1.70rem;font-weight:950;color:#4ade80;">{_wins}</div>
            </div>
            <div style="flex:1;min-width:120px;padding:14px 14px;border-radius:12px;background:#4a1717;border:1px solid #ef4444;">
                <div style="font-size:0.82rem;color:#fca5a5;font-weight:900;">LOSS</div>
                <div style="font-size:1.70rem;font-weight:950;color:#fb7185;">{_losses}</div>
            </div>
            <div style="flex:1;min-width:145px;padding:14px 14px;border-radius:12px;background:#30304f;border:1px solid #818cf8;">
                <div style="font-size:0.82rem;color:#c7d2fe;font-weight:900;">ACCURACY</div>
                <div style="font-size:1.70rem;font-weight:950;color:#a5b4fc;">{_accuracy:.2f}%</div>
            </div>
        </div>
    </div>
    """

    st.divider()
    st.header(f"📜 SUPERTREND HISTORY — {TIMEFRAME.upper()} | ATR {ATR_PERIOD} | MULTIPLIER {MULTIPLIER:.1f}")
    st.markdown(_trade_summary_html, unsafe_allow_html=True)
    st.markdown("".join(_history_html), unsafe_allow_html=True)


# ============================================================
# DISPLAY
# ============================================================

st.divider()

st.header(f"📈 SUPERTREND — {TIMEFRAME.upper()}")

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
        f"🟢🚦🔉 BUY | "
        f"ENTRY: {show_price(signal_entry_price)} | "
        f"SUPERTREND: {show_price(signal_supertrend)}"
    )

elif signal_direction == "SELL":

    st.error(
        f"🔴🚦🔉 SELL | "
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
# ADDITIVE LIVE TSL TRACKING + 1H CANDLE COUNTDOWN
# ------------------------------------------------------------
# Existing Entry / Live Price / Points display is intentionally untouched.
# TSL uses the SAME confirmed SuperTrend line already used by the strategy.
# The countdown is display-only and follows the real browser clock.
# No order, signal, grid, or existing strategy logic is changed here.
# ============================================================

_tsl_tracking_price = float(current_supertrend)
_tsl_tracking_direction = (
    "BUY / BULLISH" if current_trend == -1
    else "SELL / BEARISH"
)
_tsl_tracking_icon = "🟢" if current_trend == -1 else "🔴"
_tsl_tracking_color = "#22c55e" if current_trend == -1 else "#ff3b30"

components.html(
    f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
    margin:0;
    padding:0;
    background:transparent;
    font-family:Arial,sans-serif;
}}
.track-wrap {{
    display:grid;
    grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
    gap:12px;
    margin:16px 0 8px 0;
}}
.track-card {{
    min-height:142px;
    box-sizing:border-box;
    border-radius:18px;
    padding:18px 16px;
    background:linear-gradient(135deg,#0b1220,#111827);
    border:2px solid #334155;
    box-shadow:0 0 18px rgba(0,0,0,.20);
    text-align:center;
}}
.tsl-card {{
    border-color:{_tsl_tracking_color};
}}
.label {{
    font-size:clamp(16px,3.5vw,21px);
    font-weight:950;
    letter-spacing:.7px;
    color:#cbd5e1;
    margin-bottom:7px;
}}
.tsl-price {{
    font-size:clamp(34px,8vw,54px);
    line-height:1.05;
    font-weight:1000;
    color:{_tsl_tracking_color};
    text-shadow:0 0 12px rgba(255,255,255,.08);
}}
.sub {{
    margin-top:7px;
    font-size:clamp(12px,2.8vw,15px);
    font-weight:800;
    color:#94a3b8;
}}
.count {{
    font-size:clamp(38px,9vw,58px);
    line-height:1.0;
    font-weight:1000;
    color:#facc15;
    letter-spacing:1px;
    margin-top:7px;
}}
.time {{
    font-size:clamp(12px,2.8vw,15px);
    color:#93c5fd;
    font-weight:800;
    margin-top:8px;
}}
@media (max-width: 620px) {{
    .track-wrap {{
        grid-template-columns:1fr;
    }}
    .track-card {{
        min-height:128px;
    }}
}}
</style>
</head>
<body>
<div class="track-wrap">

  <div class="track-card tsl-card">
    <div class="label">{_tsl_tracking_icon} TSL TRACKING</div>
    <div class="tsl-price">{_tsl_tracking_price:,.2f}</div>
    <div class="sub">SuperTrend trailing price • {_tsl_tracking_direction}</div>
  </div>

  <div class="track-card">
    <div class="label">⏱️ CURRENT {TIMEFRAME.upper()} CANDLE</div>
    <div class="count" id="countdown">59:59</div>
    <div class="time" id="candleTime">Calculating...</div>
  </div>

</div>

<script>
function pad2(n) {{
    return String(n).padStart(2, "0");
}}

function updateCandleCountdown() {{
    // 1H candles MUST start at :30 IST (02:30, 03:30, 04:30 ...),
    // not at :00.  Calculate the boundary explicitly in IST so the
    // display remains correct even if the device/browser timezone differs.
    const nowMs = Date.now();
    const IST_OFFSET_MS = (5 * 60 + 30) * 60000;
    const HOUR_MS = 60 * 60000;
    const HALF_HOUR_MS = 30 * 60000;

    const istNowMs = nowMs + IST_OFFSET_MS;
    const candleStartIstMs =
        Math.floor((istNowMs - HALF_HOUR_MS) / HOUR_MS) * HOUR_MS
        + HALF_HOUR_MS;
    const candleEndIstMs = candleStartIstMs + HOUR_MS;

    const remaining = Math.max(0, candleEndIstMs - istNowMs);
    const totalSeconds = Math.max(0, Math.ceil(remaining / 1000));

    const mm = Math.floor(totalSeconds / 60);
    const ss = totalSeconds % 60;

    document.getElementById("countdown").textContent =
        pad2(mm) + ":" + pad2(ss);

    // Convert the IST wall-clock timestamps back to UTC Date objects.
    const start = new Date(candleStartIstMs - IST_OFFSET_MS);
    const end = new Date(candleEndIstMs - IST_OFFSET_MS);

    const fmt = {{
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZone: "Asia/Kolkata"
    }};

    document.getElementById("candleTime").textContent =
        "IST " + start.toLocaleTimeString("en-IN", fmt) +
        " → " + end.toLocaleTimeString("en-IN", fmt);
}}

updateCandleCountdown();
setInterval(updateCandleCountdown, 250);
</script>
</body>
</html>
""",
    height=305,
    scrolling=False
)


# ============================================================
# LIVE PRICE vs CURRENT ENTRY
# Difference is based ONLY on the latest signal entry price.
# Current SuperTrend is NOT used for this calculation.
# ============================================================

_live_entry_difference = live_price - signal_entry_price
_live_entry_points = _live_entry_difference

if _live_entry_difference < 0:
    _live_entry_position = "BELOW ENTRY"
    _live_entry_color = "#dc2626"
elif _live_entry_difference > 0:
    _live_entry_position = "ABOVE ENTRY"
    _live_entry_color = "#16a34a"
else:
    _live_entry_position = "AT ENTRY"
    _live_entry_color = "#eab308"

_signal_icon = "🟢" if signal_direction == "BUY" else "🔴" if signal_direction == "SELL" else "⚪"

st.markdown(
    f"""
    <div style="
        margin-top:18px;
        padding:18px 16px;
        border:2px solid {_live_entry_color};
        border-radius:16px;
        background:#0b1220;
        text-align:center;
    ">
        <div style="font-size:clamp(20px,5vw,28px);font-weight:1000;color:#ffffff;margin-bottom:12px;">
            {_signal_icon} CURRENT {signal_direction} SIGNAL
        </div>
        <div style="font-size:clamp(16px,4vw,21px);font-weight:900;color:#ffffff;line-height:1.7;">
            ENTRY: <span style="color:#facc15;">{signal_entry_price:,.2f}</span><br>
            LIVE PRICE: <span style="color:#38bdf8;">{live_price:,.2f}</span>
        </div>
        <div style="font-size:clamp(38px,10vw,62px);font-weight:1000;color:{_live_entry_color};line-height:1;margin:16px 0 8px;">
            {_live_entry_points:+,.2f}
        </div>
        <div style="font-size:clamp(18px,4.5vw,25px);font-weight:1000;color:{_live_entry_color};">
            POINTS {_live_entry_position}
        </div>
    </div>
    """,
    unsafe_allow_html=True
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

# ============================================================
# ADDITIVE MTF SUPERTREND + TSL + SIGNAL PRICE DISPLAY
# ------------------------------------------------------------
# DISPLAY ONLY. Existing trading/account/grid/reversal/chart logic is untouched.
# Fixed display settings: ATR 10 / Multiplier 2.0 / HL2.
# Signals use completed candles only.
# ============================================================
_MTF_ST_SETTINGS = [("1m",60),("5m",300),("15m",900),("30m",1800),("1h",3600)]
_MTF_ST_ATR = 10
_MTF_ST_MULT = 2.0

def _mtf_st_dataframe(rows, candle_seconds):
    if not isinstance(rows, list): return pd.DataFrame()
    parsed=[]
    for c in rows:
        try:
            parsed.append({"time":int(c["time"]),"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"])})
        except Exception: continue
    if not parsed: return pd.DataFrame()
    x=pd.DataFrame(parsed).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    now=int(time.time())
    if not x.empty:
        last_t=int(x["time"].iloc[-1]); last_t=last_t//1000 if last_t>10_000_000_000 else last_t
        if last_t+candle_seconds>now: x=x.iloc[:-1].copy()
    x=x.reset_index(drop=True)
    if len(x)<_MTF_ST_ATR+5: return pd.DataFrame()
    pc=x["close"].shift(1)
    x["TR"]=pd.concat([(x["high"]-x["low"]),(x["high"]-pc).abs(),(x["low"]-pc).abs()],axis=1).max(axis=1)
    x["ATR"]=float("nan")
    x.loc[_MTF_ST_ATR-1,"ATR"]=x["TR"].iloc[:_MTF_ST_ATR].mean()
    for i in range(_MTF_ST_ATR,len(x)):
        x.loc[i,"ATR"]=(x.loc[i-1,"ATR"]*(_MTF_ST_ATR-1)+x.loc[i,"TR"])/_MTF_ST_ATR
    x["HL2"]=(x["high"]+x["low"])/2.0
    x["FINAL_UPPER"]=float("nan"); x["FINAL_LOWER"]=float("nan"); x["SUPERTREND"]=float("nan"); x["ST_DIRECTION"]=0; x["SIGNAL"]=""
    pfu=pfl=pst=None; pdir=1
    for i in range(len(x)):
        atr=x.loc[i,"ATR"]
        if pd.isna(atr): continue
        h=float(x.loc[i,"HL2"]); close=float(x.loc[i,"close"])
        bu=h+_MTF_ST_MULT*float(atr); bl=h-_MTF_ST_MULT*float(atr)
        if i==_MTF_ST_ATR-1 or pfu is None: fu,fl=bu,bl
        else:
            prevc=float(x.loc[i-1,"close"])
            fu=bu if (bu<pfu or prevc>pfu) else pfu
            fl=bl if (bl>pfl or prevc<pfl) else pfl
        if i==_MTF_ST_ATR-1 or pst is None: direction,stv=1,fu
        elif pst==pfu: direction,stv=(1,fu) if close<=fu else (-1,fl)
        else: direction,stv=(-1,fl) if close>=fl else (1,fu)
        x.loc[i,"FINAL_UPPER"]=fu; x.loc[i,"FINAL_LOWER"]=fl; x.loc[i,"ST_DIRECTION"]=direction; x.loc[i,"SUPERTREND"]=stv
        if i>0 and pdir!=direction: x.loc[i,"SIGNAL"]="BUY" if direction==-1 else "SELL"
        pfu,pfl,pst,pdir=fu,fl,stv,direction
    return x

def _get_mtf_st_snapshot():
    snaps=[]
    for tf,seconds in _MTF_ST_SETTINGS:
        try:
            end=int(time.time()); start=end-max(500*seconds,7*24*60*60)
            response=api.request("GET","/v2/history/candles",params={"symbol":SYMBOL,"resolution":tf,"start":start,"end":end})
            rows=response.get("result",[]) if isinstance(response,dict) and response.get("success") else []
            x=_mtf_st_dataframe(rows,seconds)
            if x.empty: raise ValueError("candle data unavailable")
            last=x.iloc[-1]; sr=x[x["SIGNAL"].isin(["BUY","SELL"])]
            if not sr.empty:
                sig=sr.iloc[-1]; signal=str(sig["SIGNAL"]); signal_price=float(sig["close"]); stime=int(sig["time"]); stime=stime//1000 if stime>10_000_000_000 else stime
                signal_time=datetime.fromtimestamp(stime,tz=timezone.utc).astimezone().strftime("%d-%m-%Y %H:%M:%S")
            else: signal,signal_price,signal_time="—",None,"—"
            direction="BUY" if int(last["ST_DIRECTION"])==-1 else "SELL"
            snaps.append({"tf":tf.upper(),"direction":direction,"signal":signal,"signal_price":signal_price,"signal_time":signal_time,"tsl":float(last["SUPERTREND"])})
        except Exception:
            snaps.append({"tf":tf.upper(),"direction":"—","signal":"—","signal_price":None,"signal_time":"—","tsl":None})
    return snaps

_MTF_ST_SNAPSHOTS=_get_mtf_st_snapshot()
st.markdown('''
<div style="margin:12px 0 10px;padding:14px 16px;border-radius:14px;background:linear-gradient(135deg,#0b1220,#111827);border:1px solid #334155;box-shadow:0 8px 24px rgba(0,0,0,.18);">
<div style="font-size:clamp(20px,4vw,30px);font-weight:1000;color:#f8fafc;">📊 MTF SUPERTREND + TSL + SIGNAL PRICE</div>
<div style="margin-top:4px;font-size:14px;font-weight:800;color:#94a3b8;">ATR 10 &nbsp;|&nbsp; Multiplier 2.0 &nbsp;|&nbsp; HL2 &nbsp;|&nbsp; Confirmed Candle Close</div>
</div>''',unsafe_allow_html=True)
_MTF_CSS='''<style>
.mtf-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:0 0 16px}.mtf-card{background:#0b1220;border:2px solid #334155;border-radius:14px;padding:13px 10px;text-align:center;min-height:220px;box-sizing:border-box}.mtf-tf{font-size:clamp(18px,3.8vw,25px);font-weight:1000;color:#f8fafc;margin-bottom:8px}.mtf-dir{font-size:clamp(20px,4vw,28px);font-weight:1000;margin:5px 0 12px}.mtf-label{font-size:12px;font-weight:900;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-top:8px}.mtf-value{font-size:clamp(16px,3.2vw,21px);font-weight:1000;color:#e2e8f0;word-break:break-word}.mtf-signal{font-size:clamp(14px,2.8vw,18px);font-weight:1000}.mtf-time{font-size:13px;font-weight:1000;color:#cbd5e1}@media(max-width:900px){.mtf-grid{grid-template-columns:repeat(2,minmax(0,1fr));}.mtf-card{min-height:205px}}@media(max-width:520px){.mtf-grid{grid-template-columns:1fr}.mtf-card{min-height:auto}}
</style>'''
_cards=[]
for item in _MTF_ST_SNAPSHOTS:
    d=item["direction"]; color="#22c55e" if d=="BUY" else "#ef4444" if d=="SELL" else "#94a3b8"; sig=item["signal"]; sigcolor="#22c55e" if sig=="BUY" else "#ef4444" if sig=="SELL" else "#94a3b8"
    sp=f'{item["signal_price"]:,.2f}' if item["signal_price"] is not None else "—"; tsl=f'{item["tsl"]:,.2f}' if item["tsl"] is not None else "—"; icon="🟢" if d=="BUY" else "🔴" if d=="SELL" else "⚪"
    _cards.append('<div class="mtf-card" style="border-color:'+color+';">'
                  '<div class="mtf-tf">'+item["tf"]+' SUPER TREND</div>'
                  '<div class="mtf-dir" style="color:'+color+';">'+icon+' '+d+'</div>'
                  '<div class="mtf-label">Latest Signal</div><div class="mtf-signal" style="color:'+sigcolor+';">'+sig+'</div>'
                  '<div class="mtf-label">Signal Price</div><div class="mtf-value">'+sp+'</div>'
                  '<div class="mtf-label">TSL / SuperTrend</div><div class="mtf-value" style="color:'+color+';">'+tsl+'</div>'
                  '<div class="mtf-label">Signal Time</div><div class="mtf-time">'+item["signal_time"]+'</div></div>')
st.markdown(_MTF_CSS+'<div class="mtf-grid">'+''.join(_cards)+'</div>',unsafe_allow_html=True)

# ============================================================
# OWNER-ONLY ENGINE RUNTIME
# ============================================================

class _SilentColumn:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False

class _SilentStreamlit:
    def __init__(self, state):
        self.session_state = state
    def __getattr__(self, _name):
        def _noop(*_args, **_kwargs):
            if _name == "columns":
                n = _args[0] if _args else _kwargs.get("spec", 1)
                if isinstance(n, int):
                    return tuple(_SilentColumn() for _ in range(n))
                return tuple(_SilentColumn() for _ in n)
            return None
        return _noop


class _AccountStreamlit:
    """Visible Streamlit facade with account-isolated session state."""
    def __init__(self, real_streamlit, state):
        self._real = real_streamlit
        self.session_state = state

    def __getattr__(self, name):
        return getattr(self._real, name)

if "_owner_account_state" not in st.session_state:
    st.session_state["_owner_account_state"] = {}

def _account_state(account_id):
    state = st.session_state["_owner_account_state"]
    if not isinstance(state, dict):
        state = {}
        st.session_state["_owner_account_state"] = state
    return state

def _account_client(account_id):
    return DeltaAPI(OWNER_API_KEY, OWNER_API_SECRET)

def _account_credentials(account_id):
    return OWNER_API_KEY, OWNER_API_SECRET
def _run_account_engine(account_id, account_api, account_qty_btc, render_output):
    _ACCOUNT_STATE = _account_state(account_id)
    _real_st = globals()["st"]
    # Owner account are rendered, while engine/session state remains isolated.
    st = _AccountStreamlit(_real_st, _ACCOUNT_STATE)
    api = account_api
    ORDER_QTY = float(account_qty_btc)
    DEFAULT_ORDER_SIZE = _qty_to_contracts(ORDER_QTY)
    # PART 3/4 — SUPERTREND GRID ENGINE
    # ============================================================
    # User-defined grid rules:
    # 1) SuperTrend is direction selector only.
    # 2) New cycle starts from the confirmed SuperTrend signal-time ATR line.
    # 3) ALL 25 fixed levels are created immediately at signal time.
    # 4) One grid order uses the GRID QUANTITY PER ORDER setting.
    # 5) Grid spacing = 100 points.
    # 6) BUY: L1=ATR, then L2..L25 = ATR + 100, +200 ... +2400.
    # 7) SELL: L1=ATR, then L2..L25 = ATR - 100, -200 ... -2400.
    # 8) Every filled level creates its own opposite-side LIMIT target at
    #    exactly level +/- 100. Target fill creates re-entry at that SAME level.
    # 9) SELL direction is the exact mirror image.
    # 10) On SuperTrend reversal, cancel ALL bot LIMIT orders and close the
    #     old position before starting a fresh cycle.
    # 11) No attached TP orders are used. The grid itself is the target engine.
    # ============================================================

    st.divider()
    st.header(f"🧪 {account_id} — FULL {TRADING_MODE} RECORD")
    st.caption(f"Owner {TRADING_MODE} account • Quantity: {ORDER_QTY:.3f} BTC/order • Same existing SuperTrend/Grid logic")
    st.info(f"🧪 {account_id} API • {TRADING_MODE} ACCOUNT • GRID ENGINE")
    _account_mode = OWNER_DIRECTION_MODE
    _account_effective_direction = _resolve_account_direction(_account_mode, signal_direction)
    _dir_icon = "🟢" if _account_effective_direction == "BUY" else "🔴" if _account_effective_direction == "SELL" else "⚪"
    st.success(
        f"{_dir_icon} {account_id} CURRENT TRADING DIRECTION: "
        f"{_account_effective_direction or 'WAITING FOR SIGNAL'} • CONTROL: {_account_mode}"
    )
    GRID_STEP = int(os.getenv("GRID_STEP", "100"))
    # Every grid entry/target/re-entry follows the dashboard quantity setting.
    GRID_CHUNK_CONTRACTS = DEFAULT_ORDER_SIZE
    GRID_INITIAL_CONTRACTS = DEFAULT_ORDER_SIZE
    GRID_LEVEL_COUNT = 25

    # FINAL FIXED-GRID RULE:
    # On a NEW confirmed SuperTrend signal, all 25 fixed levels are created
    # from the signal-time ATR/SuperTrend line. ALL 25 entries are submitted
    # as LIMIT orders at those exact fixed prices. If a limit is already
    # marketable at the current exchange price, Delta can fill it immediately.
    # The order price/level itself never changes.
    #
    # Every filled level owns its own fixed target: BUY = level + GRID_STEP,
    # SELL = level - GRID_STEP. Target is NEVER calculated from live CMP or
    # the actual fill price. After target hit, re-entry returns to the SAME
    # fixed level and the SAME fixed target cycle repeats until signal change.
    st.session_state["initial_entry_offset"] = 0

    if GRID_STEP <= 0:
        raise ValueError("GRID_STEP must be greater than 0")
    if GRID_LEVEL_COUNT != 25:
        raise ValueError("GRID_LEVEL_COUNT must remain 25")

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

    def _grid_has_bot_orders_for_direction(open_orders, direction):
        """RESUME DETECTION ONLY.

        Agar current direction (BUY/SELL) ke bot GRID orders Delta par
        abhi bhi open hain, to yeh True return karega. Isका use SIRF
        yeh decide karne ke liye hota hai ki dashboard restart ko safe
        RESUME maana jaye (cancel/close NAHIN) ya fresh/reversal cycle.
        """
        direction = str(direction or "").upper().strip()
        if direction == "BUY":
            prefix = "GRID_B_"
        elif direction == "SELL":
            prefix = "GRID_S_"
        else:
            return False
        for order in open_orders or []:
            if not _grid_is_bot(order):
                continue
            cid = str(order.get("client_order_id", "")).upper()
            if cid.startswith(prefix):
                return True
        return False


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
        value = order.get("id", order.get("order_id"))
        return str(value) if value is not None else None


    def _grid_order_fill_quantities(order):
        """Return (total contracts, filled contracts, unfilled contracts) when available."""
        def _number(keys):
            for key in keys:
                try:
                    value = order.get(key)
                    if value not in (None, ""):
                        return abs(float(value))
                except (TypeError, ValueError):
                    continue
            return None

        total = _number(("size", "order_size", "quantity", "requested_size"))
        filled = _number(("filled_size", "filled_quantity", "filled", "executed_size", "executed_quantity"))
        unfilled = _number(("unfilled_size", "remaining_size", "remaining_quantity", "leaves_qty"))
        if filled is None and total is not None and unfilled is not None:
            filled = max(0.0, total - unfilled)
        if unfilled is None and total is not None and filled is not None:
            unfilled = max(0.0, total - filled)
        return total, filled, unfilled


    def _grid_order_filled_contracts(order):
        """Filled quantity from exchange fields; never infer a fill from price alone."""
        _total, filled, _unfilled = _grid_order_fill_quantities(order)
        return float(filled or 0.0)


    def _grid_order_was_filled(order):
        """Recognize Delta fills even when its closed-order schema omits filled_size.

        Delta's closed-order response can expose average_fill_price and/or
        paid_commission without a filled_size field.  The previous implementation
        treated those valid fills as unfilled, so the target/re-entry chain never
        started.  Explicit cancellation/rejection always wins over these hints.
        """
        state = str(order.get("state", order.get("status", ""))).lower().strip()
        cancellation_reason = str(order.get("cancellation_reason", "") or "").strip().lower()
        total, filled, unfilled = _grid_order_fill_quantities(order)

        # Real executed quantity wins even if the remaining order was later
        # cancelled/rejected. A partially-filled-then-cancelled order still
        # requires a target for the executed contracts.
        if filled is not None and filled > 1e-9:
            return True
        if state in {"cancelled", "canceled", "rejected", "failed", "expired"}:
            return False

        # A fully-filled state is authoritative unless the exchange explicitly
        # provides a cancellation/rejection reason.
        if state in {"filled", "fully_filled", "fully-filled", "completed", "complete"}:
            return not any(word in cancellation_reason for word in ("cancel", "reject", "expire"))

        # Closed orders may omit filled_size but report zero remaining quantity.
        if state in {"closed", "close"}:
            if total is not None and unfilled is not None and unfilled <= 1e-9 and total > 0:
                return not any(word in cancellation_reason for word in ("cancel", "reject", "expire"))
            # Some Delta responses omit fill quantities entirely on filled orders,
            # but retain the exchange's average execution price.
            avg_fill = order.get("average_fill_price", order.get("average_filled_price"))
            try:
                if avg_fill not in (None, "") and float(avg_fill) > 0:
                    return not any(word in cancellation_reason for word in ("cancel", "reject", "expire"))
            except (TypeError, ValueError):
                pass
            # Commission > 0 is another execution hint when price/size fields are
            # missing. Zero commission alone is not treated as proof of a fill.
            try:
                commission = order.get("paid_commission", order.get("commission_paid"))
                if commission not in (None, "") and abs(float(commission)) > 1e-12:
                    return not any(word in cancellation_reason for word in ("cancel", "reject", "expire"))
            except (TypeError, ValueError):
                pass
            return False

        # Partial fills can still be active on the exchange.
        return (filled or 0) > 1e-9


    def _grid_close_position(position_size, direction, tag):
        """Close an old position with a reduce-only market order."""
        size = int(abs(float(position_size or 0)))
        if size <= 0:
            return True, None

        close_side = "sell" if float(position_size) > 0 else "buy"
        # Every new order instance gets a new client ID.  The exchange order_id
        # remains the primary identity; this client ID is only a mapping tag.
        close_cid = (
            f"GRID_CLOSE_{str(direction)[:1]}_{str(tag)[:3]}_"
            f"{uuid.uuid4().hex[:10]}"
        )[:32]
        result = api.place_market_reduce_only(
            side=close_side,
            size=size,
            client_order_id=close_cid
        )
        ok = isinstance(result, dict) and result.get("success") is True
        return ok, result


    _grid_fills_cache = {"loaded": False, "ok": False, "rows": [], "error": None}

    def _grid_fetch_fills():
        """Fetch recent executions once per dashboard engine pass."""
        if _grid_fills_cache["loaded"]:
            return _grid_fills_cache["ok"], list(_grid_fills_cache["rows"])
        _grid_fills_cache["loaded"] = True
        try:
            response = api.fills()
            ok = isinstance(response, dict) and response.get("success") is True
            rows = _grid_result(response)
            if not isinstance(rows, list):
                rows = []
            _grid_fills_cache["ok"] = ok
            _grid_fills_cache["rows"] = rows
            _grid_fills_cache["error"] = None if ok else response.get("error", response.get("message", "Delta fills request failed")) if isinstance(response, dict) else "Invalid Delta fills response"
            return ok, list(rows)
        except Exception as exc:
            _grid_fills_cache["ok"] = False
            _grid_fills_cache["rows"] = []
            _grid_fills_cache["error"] = str(exc)
            return False, []


    def _grid_attach_real_fills(order_rows):
        """Attach execution quantities to orders using Delta's immutable order_id."""
        if not isinstance(order_rows, list) or not order_rows:
            return order_rows if isinstance(order_rows, list) else []
        fills_ok, fill_rows = _grid_fetch_fills()
        if not fills_ok or not fill_rows:
            return order_rows

        totals = {}
        latest_fill = {}
        seen_fill_ids = set()
        for fill in fill_rows:
            if not isinstance(fill, dict):
                continue
            nested_order = fill.get("order") if isinstance(fill.get("order"), dict) else {}
            oid = fill.get("order_id", nested_order.get("id"))
            if oid is None:
                continue
            try:
                qty = abs(float(fill.get("size", fill.get("filled_size", fill.get("quantity", 0))) or 0))
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                continue

            # Cursor pagination should not overlap, but deduplicate defensively
            # so repeated fill rows can never inflate the executed quantity.
            fill_id = fill.get("id", fill.get("fill_id"))
            if fill_id not in (None, ""):
                identity = ("id", str(fill_id))
            else:
                identity = (
                    "fallback", str(oid), str(fill.get("created_at", fill.get("timestamp", ""))),
                    str(fill.get("price", "")), str(qty), str(fill.get("side", ""))
                )
            if identity in seen_fill_ids:
                continue
            seen_fill_ids.add(identity)

            key = str(oid)
            totals[key] = totals.get(key, 0.0) + qty
            latest_fill[key] = fill

        enriched = []
        for original in order_rows:
            if not isinstance(original, dict):
                continue
            order = dict(original)
            oid = _grid_order_id(order)
            if oid and str(oid) in totals:
                total, existing_filled, unfilled = _grid_order_fill_quantities(order)
                # Use real fill records as the source of truth. Never reduce an
                # explicitly reported larger quantity due to pagination overlap.
                filled_qty = max(float(existing_filled or 0.0), totals[str(oid)])
                order["filled_size"] = filled_qty
                if total is not None:
                    order["unfilled_size"] = max(0.0, total - filled_qty)
                order["_grid_fill_source"] = "delta_v2_fills"
                fill = latest_fill.get(str(oid), {})
                order["_grid_last_fill_price"] = fill.get("price")
                order["_grid_last_fill_created_at"] = fill.get("created_at", fill.get("updated_at"))
                # Cancelled orders can still have a genuine partial execution.
                if filled_qty > 0 and str(order.get("state", "")).lower() in {"cancelled", "canceled"}:
                    order["_grid_cancelled_after_partial_fill"] = True
            enriched.append(order)
        return enriched


    def _grid_fetch_orders():
        response = api.open_orders()
        ok = isinstance(response, dict) and response.get("success") is True
        rows = _grid_result(response)
        if not isinstance(rows, list):
            rows = []
        return ok, _grid_attach_real_fills(rows) if ok else rows


    def _grid_fetch_closed_orders():
        response = api.closed_orders()
        ok = isinstance(response, dict) and response.get("success") is True
        rows = _grid_result(response)
        if not isinstance(rows, list):
            rows = []
        return ok, _grid_attach_real_fills(rows) if ok else rows


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


    def _grid_cancel_all_active_orders_verified(open_orders):
        """
        REVERSAL/FRESH-CYCLE HARD CLEANUP.

        Cancel EVERY active order returned by this account's Delta open-orders
        endpoint for the current product: bot orders, manual orders, ENTRY,
        TARGET, REENTRY, LIMIT, bracket/triggered orders, etc.  The exchange
        order_id is used directly; client_order_id is never required.

        A cancel response is not trusted by itself.  After each cancellation
        attempt the order is verified against Delta again.  This prevents a
        stale/ambiguous cancel response from blocking a new cycle when the
        exchange has actually already removed the order.
        """
        active_states = {
            "open", "pending", "active",
            "partially_filled", "partially-filled", "triggered"
        }
        failed = False
        cancelled = 0
        details = []

        def _is_active(order):
            state = str(order.get("state", "")).lower().strip()
            return (
                state not in {"cancelled", "canceled", "filled", "closed", "rejected", "failed"}
                and (not state or state in active_states)
            )

        # First use Delta's product-scoped account-wide cancellation endpoint.
        # This is the strongest reversal cleanup because it does not depend on
        # client_order_id, order role, or whether the order was created manually
        # or by this bot.  The `api` object is the currently processed account.
        try:
            bulk_result = api.cancel_all_open_orders_for_product()
            bulk_ok = isinstance(bulk_result, dict) and bulk_result.get("success") is True
        except Exception as exc:
            bulk_ok = False
            details.append({"bulk_cancel_error": str(exc)})

        verify_ok, verify_orders = _grid_fetch_orders()
        if verify_ok:
            pending = [o for o in verify_orders if _is_active(o)]
            if not pending:
                return True, cancelled, details
        else:
            pending = list(open_orders or [])
            failed = True

        # If Delta's bulk cancellation leaves anything active, fall back to
        # canonical single-order DELETE /v2/orders cancellation and verify each
        # exchange order id.
        if not bulk_ok:
            failed = True

        # Two passes handle an order that was added between the initial snapshot
        # and the first verification refresh.
        for _pass in range(2):
            attempted_ids = set()
            for order in list(pending):
                if not _is_active(order):
                    continue
                order_id = _grid_order_id(order)
                if not order_id or str(order_id) in attempted_ids:
                    continue
                attempted_ids.add(str(order_id))

                cid = str(order.get("client_order_id", "") or "")
                side = _grid_order_side(order)
                price = _grid_price(order)
                result = api.cancel_order(order_id)

                # Always verify against the exchange.  A failed/ambiguous POST
                # is considered successful if the order has already disappeared.
                verify_ok, verify_orders = _grid_fetch_orders()
                still_open = False
                if verify_ok:
                    for remaining in verify_orders:
                        if str(_grid_order_id(remaining)) == str(order_id) and _is_active(remaining):
                            still_open = True
                            break

                api_success = isinstance(result, dict) and result.get("success") is True
                if verify_ok and not still_open:
                    cancelled += 1
                    details.append({
                        "order_id": str(order_id),
                        "client_order_id": cid,
                        "side": side,
                        "price": price,
                        "cancel_verified": True,
                    })
                elif api_success and not verify_ok:
                    # Response explicitly confirmed cancellation, but the
                    # verification request itself failed.  Do not call this a
                    # hard success; the final refresh below will decide.
                    details.append({
                        "order_id": str(order_id),
                        "client_order_id": cid,
                        "side": side,
                        "price": price,
                        "cancel_verified": False,
                        "verify_failed": True,
                    })
                    failed = True
                else:
                    failed = True
                    details.append({
                        "order_id": str(order_id),
                        "client_order_id": cid,
                        "side": side,
                        "price": price,
                        "cancel_failed": True,
                    })

            verify_ok, verify_orders = _grid_fetch_orders()
            if not verify_ok:
                failed = True
                break
            pending = [o for o in verify_orders if _is_active(o)]
            if not pending:
                return True, cancelled, details

        # Final authoritative check.  If Delta says there are no active orders,
        # cleanup is successful even if an earlier cancel response was ambiguous.
        final_ok, final_orders = _grid_fetch_orders()
        if final_ok:
            remaining = [o for o in final_orders if _is_active(o)]
            if not remaining:
                return True, cancelled, details

        return False, cancelled, details


    def _grid_cancel_wrong_direction_orders(open_orders, current_direction, current_cycle_tag=""):
        """
        HARD EXCHANGE CLEANUP.

        Only a fully identified CURRENT-CYCLE GRID order is allowed to remain.
        Client ID is NOT required for cancellation: an order with a missing,
        malformed, unknown, stale, or wrong client ID is treated as an invalid
        transaction and cancelled by Delta's real exchange order_id.

        Orders are validated by direction + current cycle + role/level. Any active
        order that fails that validation is cancelled using Delta's exchange
        order_id. After cleanup the normal engine re-arms the correct fixed grid.
        """
        direction = str(current_direction or "").upper().strip()
        if direction not in {"BUY", "SELL"}:
            return True, 0, []

        active_states = {
            "open", "pending", "active",
            "partially_filled", "partially-filled", "triggered"
        }
        expected_prefix = "GRID_B_" if direction == "BUY" else "GRID_S_"
        cycle_tag = str(current_cycle_tag or "").strip()

        failed = False
        cancelled = 0
        cancelled_details = []

        for order in list(open_orders or []):
            state = str(order.get("state", "")).lower().strip()
            if state in {"cancelled", "canceled", "filled", "closed", "rejected", "failed"}:
                continue
            if state and state not in active_states:
                continue

            order_id = _grid_order_id(order)
            if not order_id:
                continue

            cid_raw = str(order.get("client_order_id", "")).strip()
            cid = cid_raw.upper()
            side = _grid_order_side(order)
            price = _grid_price(order)

            valid_current = False
            role = ""
            level_no = 0

            # A valid order MUST carry a current-cycle GRID identity. This also
            # deliberately rejects an order with no client ID.
            if cid.startswith(expected_prefix) and cycle_tag and f"_{cycle_tag}_" in cid:
                match = re.search(r"_(?:ENTRY|TARGET|REENTRY)?(?:_)?L(\d+)_(?:ENT|TGT|RE)_", cid)
                if match:
                    try:
                        level_no = int(match.group(1))
                    except Exception:
                        level_no = 0
                if "_ENT_" in cid:
                    role = "ENTRY"
                elif "_TGT_" in cid:
                    role = "TARGET"
                elif "_RE_" in cid:
                    role = "REENTRY"

                if role in {"ENTRY", "TARGET", "REENTRY"} and 1 <= level_no <= GRID_LEVEL_COUNT:
                    valid_current = True

            if valid_current:
                continue

            if not cid_raw:
                reason = "invalid active transaction: no Client ID; not a current-cycle GRID order"
            elif not cid.startswith("GRID_"):
                reason = "invalid active transaction: non-GRID/foreign Client ID"
            elif not cid.startswith(expected_prefix):
                reason = f"wrong GRID direction; current SuperTrend direction is {direction}"
            elif cycle_tag and f"_{cycle_tag}_" not in cid:
                reason = "stale GRID cycle; order does not belong to the current confirmed signal"
            else:
                reason = "malformed/unknown GRID transaction; no valid current level/role mapping"

            result = api.cancel_order(order_id)
            if isinstance(result, dict) and result.get("success") is True:
                cancelled += 1
                cancelled_details.append({
                    "order_id": order_id,
                    "client_order_id": cid_raw,
                    "side": side,
                    "price": price,
                    "reason": reason,
                })
            else:
                failed = True
                cancelled_details.append({
                    "order_id": order_id,
                    "client_order_id": cid_raw,
                    "side": side,
                    "price": price,
                    "reason": reason,
                    "cancel_failed": True,
                })

        return not failed, cancelled, cancelled_details


    def _grid_label_parts(label):
        """
        Internal mapping only:
          ENTRY_123456789_L8
          TARGET_123456789_L8
          REENTRY_123456789_L8

        Returns cycle-tag, level-number and role.  The actual Delta order_id
        is still the primary order identity.
        """
        match = re.search(
            r"^(ENTRY|TARGET|REENTRY)_(\d+)_L(\d+)$",
            str(label)
        )
        if not match:
            return "", 0, ""
        return match.group(2), int(match.group(3)), match.group(1)


    def _grid_client_prefix(direction, label):
        """Stable mapping prefix; the final token is unique per order instance."""
        cycle_tag, level_no, role = _grid_label_parts(label)
        if not cycle_tag or not level_no or not role:
            return f"GRID_{str(direction)[:1]}_{str(label)}_"
        role_code = {
            "ENTRY": "ENT",
            "TARGET": "TGT",
            "REENTRY": "RE",
        }.get(role, role[:3])
        return (
            f"GRID_{str(direction)[:1]}_{cycle_tag}_"
            f"L{level_no:02d}_{role_code}_"
        )


    def _grid_client_id(direction, label):
        # Delta client IDs are intentionally compact.  Cycle/level/role are
        # encoded for internal mapping, and the UUID suffix makes EVERY order
        # instance unique (including repeated TARGET/REENTRY instances).
        prefix = _grid_client_prefix(direction, label)
        return f"{prefix}{uuid.uuid4().hex[:6]}"[:32]


    def _grid_extract_order_id(response):
        """Extract Delta's real exchange order_id from a placement response."""
        if not isinstance(response, dict):
            return None

        for container in (
            response,
            response.get("result") if isinstance(response.get("result"), dict) else {},
        ):
            for key in ("id", "order_id"):
                value = container.get(key)
                if value is not None and str(value).strip():
                    return str(value)

        result = response.get("result")
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                for key in ("id", "order_id"):
                    value = first.get(key)
                    if value is not None and str(value).strip():
                        return str(value)
        return None


    def _grid_record_order_identity(client_order_id, order_id, label, direction):
        """Store client_id -> Delta order_id only as an internal mapping."""
        if "grid_order_identity_map" not in st.session_state:
            st.session_state["grid_order_identity_map"] = {}

        if order_id:
            st.session_state["grid_order_identity_map"][str(client_order_id)] = {
                "order_id": str(order_id),
                "client_order_id": str(client_order_id),
                "label": str(label),
                "direction": str(direction),
            }


    def _grid_order_matches_label(order, label):
        """Match an order to its cycle/level/role mapping without using price."""
        cid = str(order.get("client_order_id", "")).strip()
        if not cid or not _grid_is_bot(order):
            return False

        cycle_tag, level_no, role = _grid_label_parts(label)
        if not cycle_tag or not level_no or not role:
            return False

        role_code = {
            "ENTRY": "ENT",
            "TARGET": "TGT",
            "REENTRY": "RE",
        }.get(role, role[:3])

        return (
            f"_{cycle_tag}_L{level_no:02d}_{role_code}_"
            in cid
        )


    def _grid_verify_client_instance(client_order_id):
        """
        If an API response is uncertain, verify the exact client ID against
        Delta open and closed orders before any retry is allowed.
        """
        open_ok, open_orders = _grid_fetch_orders()
        if open_ok:
            for order in open_orders:
                if str(order.get("client_order_id", "")) == str(client_order_id):
                    return True, order

        closed_ok, closed_orders = _grid_fetch_closed_orders()
        if closed_ok:
            for order in closed_orders:
                if str(order.get("client_order_id", "")) == str(client_order_id):
                    return True, order

        if not open_ok or not closed_ok:
            return None, None
        return False, None


    def _grid_place_limit(
        side, price, label, direction, size=GRID_CHUNK_CONTRACTS, reduce_only=False
    ):
        """
        Submit one LIMIT order with a unique client_order_id.

        Duplicate protection:
          1. Check Delta open orders for the same cycle/level/role intent.
          2. Submit exactly one new client ID.
          3. If the API response is uncertain, query Delta by that exact client
             ID before permitting any later retry.
        """
        open_ok, open_orders = _grid_fetch_orders()
        if not open_ok:
            return {
                "success": False,
                "error": "Delta open-order verification failed before LIMIT submit."
            }

        for existing in open_orders:
            if (
                _grid_is_bot(existing)
                and _grid_is_limit(existing)
                and _grid_order_matches_label(existing, label)
            ):
                existing_id = _grid_order_id(existing)
                existing_cid = str(existing.get("client_order_id", ""))
                existing_price = _grid_price(existing)
                existing_size = _grid_contracts(existing)
                existing_side = _grid_order_side(existing)
                existing_reduce_only_raw = existing.get("reduce_only", False)
                existing_reduce_only = (
                    existing_reduce_only_raw is True
                    or str(existing_reduce_only_raw).strip().lower() in {"true", "1", "yes"}
                )
                price_matches = (
                    existing_price is not None
                    and abs(float(existing_price) - float(price)) <= 0.01
                )
                exact_match = (
                    bool(existing_id)
                    and existing_side == str(side).lower()
                    and price_matches
                    and existing_size == int(size)
                    and existing_reduce_only == bool(reduce_only)
                )
                if not exact_match:
                    return {
                        "success": False,
                        "error": (
                            f"Existing {label} order does not match requested side/price/size/reduce_only; "
                            f"not treating it as armed and not submitting a duplicate. "
                            f"Existing id={existing_id}, side={existing_side}, price={existing_price}, "
                            f"size={existing_size}, reduce_only={existing_reduce_only}; requested "
                            f"side={str(side).lower()}, price={price}, size={int(size)}, "
                            f"reduce_only={bool(reduce_only)}."
                        ),
                    }
                _grid_record_order_identity(
                    existing_cid, existing_id, label, direction
                )
                return {
                    "success": True,
                    "result": existing,
                    "id": existing_id,
                    "order_id": existing_id,
                    "client_order_id": existing_cid,
                    "verified_existing": True,
                }

        cid = _grid_client_id(direction, label)

        result = api.place_limit_order(
            side=side,
            size=int(size),
            limit_price=float(price),
            take_profit_price=None,
            client_order_id=cid,
            reduce_only=reduce_only
        )

        order_id = _grid_extract_order_id(result)
        # ARMED is ONLY a real Delta acceptance with a valid exchange Order ID.
        if isinstance(result, dict) and result.get("success") is True and order_id:
            _grid_record_order_identity(cid, order_id, label, direction)
            result.setdefault("client_order_id", cid)
            result.setdefault("order_id", order_id)
            return result

        # The POST may have reached Delta even if the HTTP/API response was
        # timeout/uncertain.  NEVER blindly submit another order.
        verified, existing = _grid_verify_client_instance(cid)
        if verified is True:
            existing_id = _grid_order_id(existing)
            if not existing_id:
                return {
                    "success": False,
                    "error": (
                        "Delta returned a matching client_order_id but no exchange order ID; "
                        "the order is not marked armed. Verify the order in Delta before retrying."
                    ),
                    "client_order_id": cid,
                }
            _grid_record_order_identity(cid, existing_id, label, direction)
            return {
                "success": True,
                "result": existing,
                "id": existing_id,
                "order_id": existing_id,
                "client_order_id": cid,
                "verified_after_uncertain_response": True,
            }

        if verified is None:
            return {
                "success": False,
                "error": (
                    "LIMIT response was uncertain and Delta verification "
                    "also failed; no retry was submitted."
                ),
                "client_order_id": cid,
            }

        return result


    def _grid_place_market(side, label, direction, size=GRID_CHUNK_CONTRACTS):
        cid = _grid_client_id(direction, label)
        return api.place_market_order(
            side=side,
            size=int(size),
            client_order_id=cid
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


    def _grid_has_client_label(open_orders, label):
        """
        Return True ONLY while the exact GRID order is still active on Delta.

        Important lifecycle rule:
          FILLED/CLOSED entry must no longer be treated as a pending entry.
          Once Delta reports it filled, the green pending line disappears and
          the target lifecycle is allowed to start.
        """
        active_states = {"open", "pending", "active", "partially_filled", "partially-filled"}
        for order in open_orders:
            if not _grid_is_bot(order):
                continue
            state = str(order.get("state", "")).lower().strip()
            if state and state not in active_states:
                continue
            if _grid_order_matches_label(order, label):
                order_id = _grid_order_id(order)
                cid = str(order.get("client_order_id", ""))
                if order_id and cid:
                    _grid_record_order_identity(
                        cid, order_id, label, ""
                    )
                return True
        return False


    def _grid_client_role_level(order):
        """Return (role, level_no) from a GRID client_order_id.

        This works for LIMIT and MARKET orders because the lifecycle identity is
        carried by client_order_id, while the Delta exchange order_id remains the
        actual order identity.
        """
        cid = str(order.get("client_order_id", "")).strip()
        if not cid.startswith("GRID_"):
            return "", 0
        match = re.search(r"_L(\d+)_([A-Z]+)_", cid.upper())
        if not match:
            return "", 0
        level_no = int(match.group(1))
        role_code = match.group(2)
        role = {"ENT": "ENTRY", "TGT": "TARGET", "RE": "REENTRY"}.get(role_code, "")
        return role, level_no


    def _grid_order_event_epoch(order):
        """Best available exchange timestamp for ordering lifecycle events."""
        for key in (
            "updated_at", "filled_at", "executed_at", "closed_at",
            "created_at_ts", "created_at", "created_time", "timestamp"
        ):
            value = order.get(key)
            if value in (None, ""):
                continue
            try:
                ts = float(value)
                if ts > 10_000_000_000:
                    ts /= 1000.0
                return ts
            except Exception:
                continue
        try:
            return float(order.get("id", 0) or 0)
        except Exception:
            return 0.0


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

    # ------------------------------------------------------------
    # CLEAN-ONCE STARTUP GATE
    # Every fresh dashboard session clears old product orders and closes an
    # old product position before the strategy can place a new grid. This is
    # deliberately done once per session, not on every 5-second rerun.
    # WARNING: in REAL mode this will cancel BTCUSD orders and close BTCUSD
    # position on dashboard startup, as explicitly requested by the owner.
    # ------------------------------------------------------------
    _startup_cleanup_ok = bool(st.session_state.get("startup_cleanup_done", False))
    if not _startup_cleanup_ok:
        if grid_open_ok and _grid_has_bot_orders_for_direction(grid_open_orders, _account_effective_direction):
            st.success(
                f"🔄 RESUME DETECTED AT STARTUP: existing {_account_effective_direction} GRID orders already "
                "found on Delta. Skipping full startup cleanup — no cancel, no position close."
            )
            st.session_state["startup_cleanup_done"] = True
            _startup_cleanup_ok = True
        elif not grid_open_ok or not grid_pos_ok:
            st.error("🛑 STARTUP CLEANUP BLOCKED: Delta open orders/position could not be verified. No new grid actions are allowed.")
            _startup_cleanup_ok = False
        else:
            _startup_cancel_ok, _startup_cancelled, _startup_cancel_details = _grid_cancel_all_active_orders_verified(grid_open_orders)
            _startup_cleanup_ok = bool(_startup_cancel_ok)
            if not _startup_cancel_ok:
                st.error("🛑 STARTUP CLEANUP BLOCKED: one or more old orders could not be confirmed cancelled. No new grid actions are allowed.")
            if _startup_cleanup_ok and abs(grid_position_size) >= 0.000001:
                _startup_close_ok, _startup_close_result = _grid_close_position(
                    grid_position_size, "", "STARTUP"
                )
                if not _startup_close_ok:
                    st.error(f"🛑 STARTUP CLEANUP BLOCKED: old position close request failed: {_startup_close_result}")
                    _startup_cleanup_ok = False
                else:
                    _startup_cleanup_ok = False
                    for _startup_attempt in range(6):
                        time.sleep(0.5)
                        _startup_pos_ok, _startup_position, _startup_position_size = _grid_fetch_position()
                        if _startup_pos_ok and abs(_startup_position_size) < 0.000001:
                            _startup_cleanup_ok = True
                            break
                    if not _startup_cleanup_ok:
                        st.error("🛑 STARTUP CLEANUP BLOCKED: Delta has not confirmed the old position is closed. No new grid actions are allowed.")
            if _startup_cleanup_ok:
                _startup_orders_ok, _startup_orders_after = _grid_fetch_orders()
                _startup_pos_ok, _startup_position_after, _startup_size_after = _grid_fetch_position()
                _startup_cleanup_ok = bool(
                    _startup_orders_ok and _startup_pos_ok
                    and not _startup_orders_after
                    and abs(_startup_size_after) < 0.000001
                )
                if _startup_cleanup_ok:
                    st.session_state["startup_cleanup_done"] = True
                    st.success("🧹 Startup verified: no old BTCUSD orders and no open BTCUSD position. Grid engine may now start.")
                else:
                    st.error("🛑 STARTUP CLEANUP BLOCKED: final Delta verification still finds orders/position or the API check failed.")

    if not grid_open_ok:
        st.error("🛑 Grid engine stopped: Delta open-order check failed.")
    elif not grid_pos_ok:
        st.error("🛑 Grid engine stopped: Delta position check failed.")


    # ------------------------------------------------------------
    # ACCOUNT / MARGIN / LEVERAGE
    # ------------------------------------------------------------
    st.divider()

    st.markdown('<h2 style="color:#86efac;margin-bottom:0.5rem">💰 GRID ACCOUNT / MARGIN</h2>', unsafe_allow_html=True)

    try:
        # DEMO/TESTNET ACCOUNT CONNECTION CHECK — credentials alone never count as connected.
        wallet_response = api.wallet_balances()
        leverage_response = api.order_leverage()
        wallet_data = _grid_result(wallet_response)
        leverage_data = _grid_result(leverage_response)
        _api_ok = (isinstance(wallet_response, dict) and wallet_response.get("success") is True)
        _lev_ok = (isinstance(leverage_response, dict) and leverage_response.get("success") is True)
        _api_connected = bool(_api_ok and _lev_ok)
        try:
            _public_ip = requests.get("https://api.ipify.org?format=json", timeout=3).json().get("ip", "-")
        except Exception:
            _public_ip = "Unavailable"
        _api_status_color = "#22c55e" if _api_connected else "#ef4444"
        _api_status_text = "CONNECTED & LIVE" if _api_connected else "DISCONNECTED / API ERROR"
        st.markdown(
            f'<div style="padding:14px;border:2px solid {_api_status_color};border-radius:14px;margin:8px 0 14px 0;">'
            f'<div style="font-size:1.15rem;font-weight:900;color:{_api_status_color};">● {account_id} API — {_api_status_text}</div>'
            f'<div style="margin-top:6px;color:#cbd5e1;font-weight:700;">Server Public IP: <span style="color:#f8fafc;">{_public_ip}</span></div>'
            f'<div style="margin-top:3px;color:#94a3b8;font-size:.86rem;">API keys/secrets are never displayed.</div>'
            f'</div>', unsafe_allow_html=True
        )

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

        # --- Balance display: exact 0.001 precision + automatic daily change ---
        try:
            _balance_now = float(balance) if balance is not None else None
        except Exception:
            _balance_now = None
        _today_ist = datetime.now(IST).date().isoformat()
        if "_grid_balance_day" not in st.session_state or st.session_state.get("_grid_balance_day") != _today_ist:
            st.session_state["_grid_balance_day"] = _today_ist
            st.session_state["_grid_balance_open"] = _balance_now
            st.session_state["_grid_balance_last"] = _balance_now
        elif "_grid_balance_open" not in st.session_state:
            st.session_state["_grid_balance_open"] = _balance_now
        _daily_balance_change = (_balance_now - float(st.session_state.get("_grid_balance_open"))
            if _balance_now is not None and st.session_state.get("_grid_balance_open") is not None else None)
        if _balance_now is not None:
            st.session_state["_grid_balance_last"] = _balance_now
        _bal_text = f"{_balance_now:,.3f}" if _balance_now is not None else "-"
        if _daily_balance_change is None:
            _daily_text, _daily_color = "—", "#9ca3af"
        elif _daily_balance_change > 0:
            _daily_text, _daily_color = f"+{_daily_balance_change:,.3f}", "#86efac"
        elif _daily_balance_change < 0:
            _daily_text, _daily_color = f"{_daily_balance_change:,.3f}", "#fca5a5"
        else:
            _daily_text, _daily_color = "+0.000", "#d1d5db"

        # ------------------------------------------------------------
        # DAILY BALANCE HISTORY + TOTAL PNL (balance-based)
        # Keeps daily opening/change/closing values and cumulative PNL.
        # ------------------------------------------------------------
        try:
            _ledger_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"daily_balance_history_{account_id.replace(' ', '_').lower()}.json")
            if os.path.exists(_ledger_path):
                with open(_ledger_path, "r", encoding="utf-8") as _f:
                    _daily_ledger = json.load(_f)
                if not isinstance(_daily_ledger, list):
                    _daily_ledger = []
            else:
                _daily_ledger = []
        except Exception:
            _daily_ledger = []

        if _balance_now is not None:
            _day_key = _today_ist
            _found = next((x for x in _daily_ledger if x.get("date") == _day_key), None)
            if _found is None:
                _found = {"date": _day_key, "opening": round(_balance_now, 3), "closing": round(_balance_now, 3), "change": 0.0}
                _daily_ledger.append(_found)
            else:
                _found["closing"] = round(_balance_now, 3)
                _found["change"] = round(_balance_now - float(_found.get("opening", _balance_now)), 3)
            _daily_ledger = sorted(_daily_ledger, key=lambda x: str(x.get("date", "")))[-80:]
            try:
                with open(_ledger_path, "w", encoding="utf-8") as _f:
                    json.dump(_daily_ledger, _f, indent=2)
            except Exception:
                pass

        _total_pnl = sum(float(x.get("change", 0) or 0) for x in _daily_ledger)
        _total_pnl_color = "#86efac" if _total_pnl > 0 else ("#fca5a5" if _total_pnl < 0 else "#d1d5db")

        a1, a2, a3, a4, a5, a6 = st.columns(6)
        with a1:
            st.markdown(
                f'<div style="padding:8px 0"><div style="font-size:0.82rem;color:#9ca3af;font-weight:600">TOTAL BALANCE</div>'
                f'<div style="font-size:1.55rem;font-weight:750;color:#f8fafc">{_bal_text} <span style="font-size:0.95rem;color:#9ca3af">USDT</span></div>'
                f'<div style="font-size:0.9rem;font-weight:700;color:{_daily_color}">Today {_daily_text} USDT</div></div>',
                unsafe_allow_html=True,
            )
        with a2:
            st.metric("NET EQUITY", str(net_equity) if net_equity is not None else "-")
        with a3:
            st.metric("AVAILABLE MARGIN", str(available) if available is not None else "-")
        with a4:
            st.metric("USED MARGIN", str(blocked) if blocked is not None else "-")
        with a5:
            st.metric("LEVERAGE", f"{leverage}x" if leverage is not None else "-")
        with a6:
            st.markdown(
                f'<div style="padding:8px 0"><div style="font-size:0.82rem;color:#9ca3af;font-weight:600">TOTAL PNL / BALANCE CHANGE</div>'
                f'<div style="font-size:1.55rem;font-weight:800;color:{_total_pnl_color}">{_total_pnl:+,.3f} <span style="font-size:0.95rem;color:#9ca3af">USDT</span></div></div>',
                unsafe_allow_html=True,
            )

        # Detailed live position record — sourced directly from this account's private endpoint.
        _pos_side = "LONG / BUY" if grid_position_size > 0 else ("SHORT / SELL" if grid_position_size < 0 else "FLAT")
        try:
            _avg_entry = grid_position.get("entry_price", grid_position.get("average_entry_price", grid_position.get("avg_entry_price")))
        except Exception:
            _avg_entry = None
        try:
            _mark_price = grid_position.get("mark_price", grid_position.get("current_price"))
        except Exception:
            _mark_price = None
        try:
            _unrealized = grid_position.get("unrealized_pnl", grid_position.get("unrealised_pnl"))
        except Exception:
            _unrealized = None
        st.markdown(
            '<div style="margin:12px 0 8px 0;padding:12px;border:1px solid #334155;border-radius:12px;background:#0f172a;">'
            f'<div style="font-weight:900;color:#93c5fd;margin-bottom:7px;">📍 {account_id} — COMPLETE POSITION RECORD</div>'
            f'<div>Side: <b>{_pos_side}</b> &nbsp;|&nbsp; Contracts: <b>{abs(int(grid_position_size))}</b> &nbsp;|&nbsp; Size: <b>{abs(grid_position_size)*CONTRACT_BTC:.3f} BTC</b></div>'
            f'<div style="margin-top:5px;">Average Entry: <b>{show_price(_avg_entry) if _avg_entry is not None else "-"}</b> &nbsp;|&nbsp; Mark/Current: <b>{show_price(_mark_price) if _mark_price is not None else "-"}</b> &nbsp;|&nbsp; Unrealized P&L: <b>{_unrealized if _unrealized is not None else "-"}</b></div>'
            '</div>', unsafe_allow_html=True
        )

        st.markdown(
            f'<div style="padding:6px 0;font-weight:700;">'
            f'<span style="color:#fbbf24;">Order Margin:</span> '
            f'<span style="color:#f8fafc;">{order_margin if order_margin is not None else "-"}</span>'
            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
            f'<span style="color:#60a5fa;">Position Margin:</span> '
            f'<span style="color:#f8fafc;">{position_margin if position_margin is not None else "-"}</span>'
            f'</div>', unsafe_allow_html=True
        )

        st.subheader("📅 DAILY BALANCE / PNL HISTORY — LAST 80 DAYS")
        if _daily_ledger:
            _daily_html = ['<div style="overflow-x:auto;width:100%;"><table style="width:100%;border-collapse:collapse;font-size:0.88rem;">',
                           '<thead><tr style="background:#111827;color:#cbd5e1;">',
                           '<th style="padding:8px;text-align:left;">DATE</th><th style="padding:8px;text-align:right;">OPENING</th>',
                           '<th style="padding:8px;text-align:right;">DAILY +/-</th><th style="padding:8px;text-align:right;">CLOSING</th>',
                           '</tr></thead><tbody>']
            for _d in reversed(_daily_ledger):
                _chg = float(_d.get("change", 0) or 0)
                _cc = "#86efac" if _chg > 0 else ("#fca5a5" if _chg < 0 else "#d1d5db")
                _bg = "#0d2418" if _chg > 0 else ("#2a1111" if _chg < 0 else "#111827")
                _daily_html.append(
                    f'<tr style="background:{_bg};border-bottom:1px solid #252a31;">'
                    f'<td style="padding:8px;font-weight:800;">{_d.get("date","-")}</td>'
                    f'<td style="padding:8px;text-align:right;">{float(_d.get("opening",0)):,.3f}</td>'
                    f'<td style="padding:8px;text-align:right;color:{_cc};font-weight:900;">{_chg:+,.3f}</td>'
                    f'<td style="padding:8px;text-align:right;">{float(_d.get("closing",0)):,.3f}</td>'
                    '</tr>'
                )
            _daily_html.append('</tbody></table></div>')
            st.markdown("".join(_daily_html), unsafe_allow_html=True)
        else:
            st.info("Daily balance history अभी उपलब्ध नहीं है।")

    except Exception as exc:
        st.warning(f"Account/margin data unavailable: {exc}")


    # ------------------------------------------------------------
    # GRID LEVEL CALCULATION — 25 FIXED LEVELS / ATR ANCHOR
    # ------------------------------------------------------------
    # FINAL USER LOGIC:
    #   • On every confirmed SuperTrend BUY/SELL flip, the SuperTrend ATR
    #     line is the immutable Level #1 Anchor.
    #   • BUY:  L1 = ATR, then L2..L25 = ATR + 100, +200 ... +2400.
    #   • SELL: L1 = ATR, then L2..L25 = ATR - 100, -200 ... -2400.
    #   • The 25 prices never move during the cycle.
    #   • On bot startup / fresh cycle, ALL 25 fixed levels are armed.
    #     Crossed BUY levels (<= CMP) or crossed SELL levels (>= CMP) are sent
    #     as LIMIT orders at their exact fixed prices.
    #   • The actual fill price never changes the level's target.
    #     Every target is calculated only from that level's fixed price.
    #   • After target hit, re-entry returns to that SAME fixed level and its
    #     SAME fixed target repeats.
    # ------------------------------------------------------------

    # Resolve this account's direction independently from the global SuperTrend.
    # SUPER TREND = same direction; OPPOSITE = reverse direction; BUY/SELL = manual lock.
    _direction_mode = OWNER_DIRECTION_MODE
    grid_direction = _resolve_account_direction(_direction_mode, signal_direction)
    grid_signal_base = float(signal_entry_price)
    grid_atr_line = float(signal_supertrend)

    # Stable identity for the currently confirmed SuperTrend signal cycle.
    # It must be defined before the fresh-cycle/reversal logic below.
    try:
        _signal_epoch_for_level = float(current_entry["time"])
        if _signal_epoch_for_level > 10_000_000_000:
            _signal_epoch_for_level /= 1000.0
    except Exception:
        try:
            _signal_epoch_for_level = float(pd.Timestamp(signal_time).timestamp())
        except Exception:
            _signal_epoch_for_level = 0.0

    new_cycle_key = (f"{TIMEFRAME}|ATR{ATR_PERIOD}|M{MULTIPLIER:.4f}|{grid_direction}|{int(_signal_epoch_for_level)}|{grid_atr_line:.8f}" if grid_direction else "")

    def _build_fixed_25_level_map(atr_line, direction):
        """Immutable 25-level map. Level #1 is ALWAYS the ATR anchor.

        IMPORTANT:
        live_price is NOT used to calculate any level.  It is used later only
        to decide whether an already-fixed startup level should be sent as a
        market order (already crossed) or as a pending limit order.
        """
        if direction == "BUY":
            levels = [
                float(atr_line) + GRID_STEP * i
                for i in range(GRID_LEVEL_COUNT)
            ]
        elif direction == "SELL":
            levels = [
                float(atr_line) - GRID_STEP * i
                for i in range(GRID_LEVEL_COUNT)
            ]
        else:
            return []

        return levels


    # Owner account uses its existing fixed 25-level grid calculation.
    grid_levels = (
        _build_fixed_25_level_map(grid_atr_line, grid_direction)
        if grid_direction
        else []
    )

    # IMMUTABLE ORIGINAL LEVEL MAP
    # ------------------------------------------------------------
    # Every level keeps the exact price assigned when this SuperTrend
    # signal-cycle was created. Target calculations MUST use this map.
    # They must NEVER use actual exchange fill price, average fill price,
    # live/CMP price, or any later market price.
    #
    # Example: if L5/L6/L7/L8 are all filled by the exchange at 85,300
    # because price jumped through several limits, their targets still
    # come from their ORIGINAL L5/L6/L7/L8 prices respectively.
    fixed_level_price_map = {
        int(level_no): float(level_price)
        for level_no, level_price in enumerate(grid_levels, start=1)
    }

    def _original_level_price(level_no):
        """Return the immutable signal-time price for this level."""
        level_no = int(level_no)
        if level_no not in fixed_level_price_map:
            return None
        return float(fixed_level_price_map[level_no])

    def _target_from_original_level(level_no, direction):
        """Build target ONLY from the original fixed grid level."""
        original_price = _original_level_price(level_no)
        if original_price is None:
            return None

        if direction == "BUY":
            return float(original_price + GRID_STEP)
        if direction == "SELL":
            return float(original_price - GRID_STEP)
        return None

    # There is NO special/nearest "current level" anymore.
    # All 25 levels are real levels and all 25 participate in the first
    # startup cycle.
    current_level_no = 0
    current_level_price = float(grid_atr_line)

    pending_entry_levels = [
        (i, float(price))
        for i, price in enumerate(grid_levels, start=1)
    ]

    outer_levels = [
        (i, float(price))
        for i, price in enumerate(grid_levels, start=1)
    ]

    upper_levels = [price for _, price in outer_levels]
    lower_levels = [price for _, price in outer_levels]


    # ------------------------------------------------------------
    # DISPLAY GRID PLAN
    # ------------------------------------------------------------
    st.subheader("🧱 ACTIVE GRID PLAN")

    if grid_direction:
        g1, g2, g3, g4 = st.columns(4)
        with g1:
            st.metric("DIRECTION", grid_direction)
        with g2:
            st.metric("LEVELS", f"{GRID_LEVEL_COUNT}")
        with g3:
            st.metric("STEP", f"{GRID_STEP} POINTS")
        with g4:
            st.metric("ATR LINE / L1", show_price(grid_atr_line))

        st.write(
            f"STARTUP: **ALL {GRID_LEVEL_COUNT} FIXED LEVELS × "
            f"{GRID_CHUNK_BTC:.3f} BTC** एक साथ arm होंगे."
        )
        st.write(
            "Startup पर सभी fixed levels **LIMIT** ही रहते हैं. "
            "Marketable crossed LIMIT exchange पर उसी fixed price पर "
            "तुरंत fill हो सकता है; किसी level को MARKET में convert नहीं किया जाता."
        )
        st.write(
            "हर filled level का target उसी level से fixed "
            f"**{GRID_STEP} points** दूर रहेगा. Target hit होने पर उसी level पर "
            "fixed-price re-entry और फिर वही fixed target दोबारा बनेगा."
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
    grid_cycle_ready = (
        bool(grid_direction)
        and bool(new_cycle_key)
        and new_cycle_key == st.session_state.get("grid_cycle_key", "")
        and grid_open_ok
        and grid_pos_ok
    )
    direction_changed = (
        bool(previous_grid_direction)
        and bool(grid_direction)
        and previous_grid_direction != grid_direction
    )

    fresh_signal_cycle = (
        bool(new_cycle_key)
        and new_cycle_key != st.session_state.get("grid_cycle_key", "")
    )


    # ------------------------------------------------------------
    # SAFETY: CANCEL ANY ACTIVE ORDER FROM THE WRONG DIRECTION
    # ------------------------------------------------------------
    # This check runs on EVERY refresh.  It is deliberately broader than the
    # normal reversal cleanup: stale opposite-direction GRID orders are removed,
    # and any non-GRID/manual active order on the opposite side is removed too.
    # Current-cycle GRID TARGET orders are preserved because their opposite side
    # is intentional and is part of the valid target lifecycle.
    wrong_direction_cleanup_ok = True
    wrong_direction_cancelled = 0
    wrong_direction_cancelled_details = []

    if grid_direction and grid_open_ok:
        try:
            _cleanup_cycle_tag = str(int(float(_signal_epoch_for_level)))[-9:]
        except Exception:
            _cleanup_cycle_tag = ""
        (
            wrong_direction_cleanup_ok,
            wrong_direction_cancelled,
            wrong_direction_cancelled_details,
        ) = _grid_cancel_wrong_direction_orders(
            grid_open_orders,
            grid_direction,
            _cleanup_cycle_tag,
        )

        if wrong_direction_cancelled_details:
            failed_items = [
                item for item in wrong_direction_cancelled_details
                if item.get("cancel_failed")
            ]
            if failed_items:
                st.error(
                    f"🛑 WRONG-DIRECTION SAFETY: {len(failed_items)} order(s) "
                    "could not be cancelled. New grid actions remain blocked "
                    "until Delta confirms cleanup."
                )
            else:
                st.warning(
                    f"🛑 WRONG-DIRECTION SAFETY: "
                    f"{wrong_direction_cancelled} conflicting active order(s) "
                    f"cancelled immediately. Current direction: {grid_direction}."
                )

            # Never continue with a stale pre-cancel snapshot.
            grid_open_ok, grid_open_orders = _grid_fetch_orders()

        if not wrong_direction_cleanup_ok:
            st.error(
                "🛑 Wrong-direction order cleanup failed. "
                "Grid placement is blocked for this refresh."
            )


    # ------------------------------------------------------------
    # REVERSAL / FRESH CYCLE: CANCEL ALL ACTIVE ORDERS + CLOSE POSITION
    # ------------------------------------------------------------
    # IMPORTANT: this block intentionally does NOT depend on the earlier
    # wrong-direction cleanup result.  If that earlier cleanup failed or returned
    # an ambiguous response, this account-wide verified cleanup gets one direct
    # chance to reconcile the real Delta order state before the new grid is armed.
    _grid_resume_detected = (
        bool(grid_direction)
        and not direction_changed
        and grid_open_ok
        and _grid_has_bot_orders_for_direction(grid_open_orders, grid_direction)
    )

    if _grid_resume_detected:
        st.success(
            f"🔄 RESUME DETECTED: existing {grid_direction} GRID orders already found on Delta "
            "for this same direction. No cancel, no close — missing/left-over levels will be "
            "re-checked and re-armed by the normal per-level engine below."
        )
        st.session_state["grid_cycle_key"] = new_cycle_key
        st.session_state["grid_current_level_no"] = int(current_level_no)
        st.session_state["grid_last_direction"] = grid_direction
        grid_cycle_ready = True

    elif grid_direction and (direction_changed or fresh_signal_cycle) and _startup_cleanup_ok:
        if grid_open_ok and grid_pos_ok:
            cancel_ok, cancelled_count, cancelled_details = _grid_cancel_all_active_orders_verified(
                grid_open_orders
            )

            if not cancel_ok:
                st.error(
                    "🛑 New grid blocked: Delta still reports one or more active "
                    "old/manual/bot orders after account-wide cleanup. "
                    "No new grid was placed."
                )
            else:
                if previous_grid_direction:
                    close_ok, close_result = _grid_close_position(
                        grid_position_size,
                        grid_direction,
                        "REVERSAL"
                    )
                    if not close_ok:
                        st.error("🛑 New grid blocked: old position could not be closed.")
                    else:
                        time.sleep(0.5)
                        verify_pos_ok, verify_position, verify_position_size = _grid_fetch_position()
                        if not verify_pos_ok:
                            st.error("🛑 New grid blocked: old position closure could not be verified with Delta.")
                        elif abs(verify_position_size) >= 0.000001:
                            st.error(
                                f"🛑 New grid blocked: old position is still open ({verify_position_size} contracts)."
                            )
                        else:
                            st.warning(
                                f"🔄 Direction changed {previous_grid_direction} → {grid_direction}. "
                                f"{cancelled_count} active order(s) cancelled (bot + manual), old position closed."
                            )
                            grid_open_ok, grid_open_orders = _grid_fetch_orders()
                            grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()
                            st.session_state["grid_cycle_key"] = new_cycle_key
                            st.session_state["grid_current_level_no"] = int(current_level_no)
                            st.session_state["grid_last_direction"] = grid_direction
                            grid_cycle_ready = True
                else:
                    if previous_grid_direction:
                        close_ok, close_result = _grid_close_position(
                            grid_position_size,
                            grid_direction,
                            "REVERSAL"
                        )
                        if not close_ok:
                            st.error("🛑 New grid blocked: old position could not be closed.")
                        else:
                            time.sleep(0.5)
                            verify_pos_ok, verify_position, verify_position_size = _grid_fetch_position()
                            if not verify_pos_ok:
                                st.error("🛑 New grid blocked: old position closure could not be verified with Delta.")
                            elif abs(verify_position_size) >= 0.000001:
                                st.error(
                                    f"🛑 New grid blocked: old position is still open ({verify_position_size} contracts)."
                                )
                            else:
                                st.warning(
                                    f"🔄 Direction changed {previous_grid_direction} → {grid_direction}. "
                                    f"{cancelled_count} old GRID LIMIT order(s) cancelled and old position closed."
                                )
                                grid_open_ok, grid_open_orders = _grid_fetch_orders()
                                grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()
                                st.session_state["grid_cycle_key"] = new_cycle_key
                                st.session_state["grid_current_level_no"] = int(current_level_no)
                                st.session_state["grid_last_direction"] = grid_direction
                                grid_cycle_ready = True
                    else:
                        # FIRST START / DASHBOARD REOPEN: an empty Streamlit
                        # session has no saved direction, but exchange orders or
                        # a position may still exist. Cancel all product orders,
                        # close any old position, and verify both before arming.
                        verify_open_ok, verify_open_orders = _grid_fetch_orders()
                        verify_pos_ok, verify_position, verify_position_size = _grid_fetch_position()
                        if not verify_open_ok or not verify_pos_ok:
                            st.error("🛑 First-cycle cleanup failed: Delta orders/position could not be verified. No new grid was placed.")
                        else:
                            startup_close_ok = True
                            if abs(verify_position_size) >= 0.000001:
                                startup_close_ok, startup_close_result = _grid_close_position(
                                    verify_position_size, grid_direction, "STARTUP"
                                )
                                if startup_close_ok:
                                    for _verify_attempt in range(4):
                                        time.sleep(0.5)
                                        verify_pos_ok, verify_position, verify_position_size = _grid_fetch_position()
                                        if verify_pos_ok and abs(verify_position_size) < 0.000001:
                                            break
                                if not startup_close_ok:
                                    st.error(f"🛑 First-cycle cleanup failed: existing position close request was rejected: {startup_close_result}")
                            if startup_close_ok:
                                verify_open_ok, verify_open_orders = _grid_fetch_orders()
                                verify_pos_ok, verify_position, verify_position_size = _grid_fetch_position()
                                if (verify_open_ok and verify_pos_ok
                                        and not verify_open_orders
                                        and abs(verify_position_size) < 0.000001):
                                    st.warning("🧹 Startup cleanup verified: old orders cleared and old position closed. Starting the new signal cycle.")
                                    st.session_state["grid_cycle_key"] = new_cycle_key
                                    st.session_state["grid_current_level_no"] = int(current_level_no)
                                    st.session_state["grid_last_direction"] = grid_direction
                                    grid_cycle_ready = True
                                else:
                                    st.error("🛑 New grid blocked: first-cycle cleanup verification failed. Old orders/position must be absent before new entries.")
        else:
            st.error("🛑 Fresh grid blocked because Delta order/position verification failed.")


    # ------------------------------------------------------------
    # RE-FETCH AFTER ANY CLEANUP
    # ------------------------------------------------------------
    grid_open_ok, grid_open_orders = _grid_fetch_orders()
    grid_pos_ok, grid_position, grid_position_size = _grid_fetch_position()

    if (
        grid_direction
        and wrong_direction_cleanup_ok
        and grid_open_ok
        and grid_pos_ok
        and grid_cycle_ready
        and _startup_cleanup_ok
    ):

        # --------------------------------------------------------
        # DIRECTION / ORDER SIDES
        # --------------------------------------------------------
        if grid_direction == "BUY":
            entry_side = "buy"
            target_side = "sell"
            reentry_side = "buy"
        else:
            entry_side = "sell"
            target_side = "buy"
            reentry_side = "sell"

        # Unique tag for THIS confirmed SuperTrend signal cycle.
        # It prevents a new cycle from being confused with old closed orders
        # at the same price.
        try:
            _cycle_epoch_tag = str(int(float(_signal_epoch_for_level)))
        except Exception:
            _cycle_epoch_tag = str(int(time.time()))
        _cycle_tag = _cycle_epoch_tag[-9:]

        # --------------------------------------------------------
        # STARTUP / FRESH-CYCLE: ARM ALL 25 LEVELS TOGETHER
        # --------------------------------------------------------
        # ALL 25 levels are submitted as LIMIT orders at their exact fixed
        # signal-time prices.  We do NOT convert crossed levels to MARKET.
        # A marketable LIMIT (for example BUY limit below/at CMP or SELL limit
        # above/at CMP) is allowed to fill immediately at the exchange, while
        # the remaining limits stay pending at their immutable level prices.
        # live_price is therefore NOT used to calculate or change any level.
        # A per-refresh/cycle action guard below also prevents duplicate placement
        # while Delta is still updating its open/closed order feeds.
        startup_armed_key = st.session_state.get("grid_startup_armed_cycle", "")
        # Prevent the same level from being attempted twice during one Streamlit refresh.
        grid_attempted_this_refresh = set()

        if startup_armed_key != new_cycle_key:
            grid_open_ok, grid_open_orders = _grid_fetch_orders()
            grid_closed_ok, grid_closed_orders = _grid_fetch_closed_orders()

            startup_results = []
            all_startup_ok = True

            # ========================================================
            # FULL 25-LEVEL MARGIN GATE — ALL OR NOTHING
            # ========================================================
            # Required margin is calculated for every fixed level first.
            # If the complete 25-level grid cannot fit inside the account's
            # REAL available margin, NOTHING is submitted to Delta.
            # No partial grid is allowed.
            try:
                _margin_available = float(available)
            except Exception:
                _margin_available = None
            try:
                _margin_leverage = float(leverage)
            except Exception:
                _margin_leverage = None

            _level_required_margins = []
            _full_required_margin = None
            _margin_check_error = None
            if _margin_available is None or _margin_leverage is None or _margin_leverage <= 0:
                _margin_check_error = "REAL AVAILABLE MARGIN / LEVERAGE UNAVAILABLE"
            else:
                for _margin_level_no, _margin_level_price in pending_entry_levels:
                    # BTCUSD grid quantity is represented by CONTRACT_BTC.
                    # Margin = level notional / the account's live leverage.
                    _level_margin = (
                        float(_margin_level_price) * float(GRID_CHUNK_BTC)
                    ) / _margin_leverage
                    _level_required_margins.append((_margin_level_no, _level_margin))
                _full_required_margin = sum(v for _, v in _level_required_margins)

            _full_margin_available = (
                _margin_check_error is None
                and _full_required_margin is not None
                and _full_required_margin <= _margin_available
            )

            if not _full_margin_available:
                _shortfall = (
                    (_full_required_margin - _margin_available)
                    if _full_required_margin is not None and _margin_available is not None
                    else None
                )
                if _margin_check_error:
                    st.error(
                        f"🛑 {GRID_LEVEL_COUNT} LEVELS PLANNED | 0 ARMED | {GRID_LEVEL_COUNT} BLOCKED | "
                        f"Reason: {_margin_check_error}"
                    )
                else:
                    st.error(
                        f"🛑 {GRID_LEVEL_COUNT} LEVELS PLANNED | 0 ARMED | {GRID_LEVEL_COUNT} BLOCKED | "
                        f"Required Full Margin: {_full_required_margin:,.6f} | "
                        f"Available Margin: {_margin_available:,.6f} | "
                        f"Shortfall: {_shortfall:,.6f} | "
                        f"Reason: INSUFFICIENT FULL MARGIN"
                    )

                # BLOCKED means NOT SUBMITTED. Do not call Delta order APIs.
                startup_results = [f"L{i}=BLOCKED" for i in range(1, GRID_LEVEL_COUNT + 1)]
                all_startup_ok = False
                st.session_state["grid_startup_armed_cycle"] = ""
            
            if "grid_action_done" not in st.session_state:
                st.session_state["grid_action_done"] = set()
            _grid_action_done = st.session_state["grid_action_done"]

            if _full_margin_available:
                for level_no, price in pending_entry_levels:
                    price = float(price)
                    entry_label = f"ENTRY_{_cycle_tag}_L{level_no}"
                    entry_open = _grid_has_client_label(
                        grid_open_orders,
                        entry_label
                    )

                    # A startup entry is already considered armed if the same
                    # cycle/level has a filled historical order.
                    entry_filled = False
                    if grid_closed_ok:
                        for closed in grid_closed_orders:
                            if not _grid_is_bot(closed):
                                continue
                            if not _grid_order_was_filled(closed):
                                continue
                            if _grid_order_id(closed) and _grid_order_matches_label(closed, entry_label):
                                entry_filled = True
                                _grid_record_order_identity(
                                    str(closed.get("client_order_id", "")),
                                    _grid_order_id(closed),
                                    entry_label,
                                    grid_direction
                                )
                                break

                    if entry_open or entry_filled:
                        startup_results.append(
                            f"L{level_no}=ALREADY"
                        )
                        continue

                    # ALWAYS place the original fixed level as a LIMIT order.
                    # If the level is already crossed by CMP, Delta may fill this
                    # marketable LIMIT immediately. It is still the same fixed-level
                    # order, so its target remains tied to this exact level price.
                    _startup_action_key = f"STARTUP|{new_cycle_key}|L{level_no}"
                    if _startup_action_key in _grid_action_done:
                        startup_results.append(f"L{level_no}=ALREADY")
                        continue
                    if level_no in grid_attempted_this_refresh:
                        startup_results.append(f"L{level_no}=ALREADY")
                        continue
                    grid_attempted_this_refresh.add(level_no)

                    result = _grid_place_limit(
                        side=entry_side,
                        price=price,
                        label=entry_label,
                        direction=grid_direction,
                        size=GRID_CHUNK_CONTRACTS
                    )
                    mode = "LIMIT"

                    ok = (
                        isinstance(result, dict)
                        and result.get("success") is True
                    )

                    if ok:
                        _grid_action_done.add(_startup_action_key)
                        startup_results.append(
                            f"L{level_no}={mode}@{show_price(price)}"
                        )
                    else:
                        all_startup_ok = False
                        startup_results.append(
                            f"L{level_no}=FAILED"
                        )
                        _grid_log_event(account_id, level_no, "STARTUP_ENTRY", result, price)
                        st.error(
                            f"❌ Startup L{level_no} {mode} failed "
                            f"@ fixed level {show_price(price)}: {result}"
                        )

            if _full_margin_available:
                _armed_count = 0
                _failed_count = 0
                for _r in startup_results:
                    if "=FAILED" in str(_r):
                        _failed_count += 1
                    elif "=ALREADY" in str(_r) or "=" in str(_r):
                        # A successful _grid_place_limit() always carries a
                        # valid Delta order ID. Only those results reach here.
                        if "=FAILED" not in str(_r):
                            _armed_count += 1

                if _failed_count == 0 and _armed_count == GRID_LEVEL_COUNT:
                    st.session_state["grid_startup_armed_cycle"] = new_cycle_key
                    st.success(
                        f"✅ {GRID_LEVEL_COUNT} LEVELS PLANNED | {GRID_LEVEL_COUNT} ARMED | 0 FAILED | "
                        f"{grid_direction} | Fixed ATR anchor {show_price(grid_atr_line)}"
                    )
                else:
                    st.warning(
                        f"⚠️ {GRID_LEVEL_COUNT} LEVELS PLANNED | {_armed_count} ARMED | "
                        f"{_failed_count} FAILED | FAILED = SUBMITTED BUT REJECTED"
                    )

            # Refresh immediately after the 25 startup submissions.
            grid_open_ok, grid_open_orders = _grid_fetch_orders()

        # --------------------------------------------------------
        def _grid_place_fixed_target(side, price, label, direction, size):
            """Place a fixed-level reduce-only target only when exchange position backs it.

            Target price is always derived from the immutable grid level.  Before
            submitting, re-read Delta's position and open orders so a delayed fill
            update cannot create a reduce-only target against a flat position, and
            existing targets cannot collectively exceed the available position.
            """
            # An already-live target is accepted only if every material field
            # matches. A same-label but wrong-price/side/size target must never be
            # silently treated as correct.
            open_ok, live_orders = _grid_fetch_orders()
            if not open_ok:
                return {"success": False, "error": "Cannot verify Delta open orders before target placement; will retry next refresh."}
            matching_targets = [
                _o for _o in live_orders
                if _grid_is_bot(_o) and _grid_is_limit(_o) and _grid_order_matches_label(_o, label)
            ]
            if len(matching_targets) > 1:
                return {
                    "success": False,
                    "error": (
                        f"Multiple open orders share target label {label}; "
                        "refusing to claim success or add another target. "
                        "Reconcile duplicate exchange orders first."
                    ),
                }
            if matching_targets:
                _o = matching_targets[0]
                _oid = _grid_order_id(_o)
                _existing_price = _grid_price(_o)
                _existing_size = _grid_contracts(_o)
                _existing_side = _grid_order_side(_o)
                _existing_ro_raw = _o.get("reduce_only", False)
                _existing_ro = (
                    _existing_ro_raw is True
                    or str(_existing_ro_raw).strip().lower() in {"true", "1", "yes"}
                )
                _same = (
                    bool(_oid)
                    and _existing_side == str(side).lower()
                    and _existing_price is not None
                    and abs(float(_existing_price) - float(price)) <= 0.01
                    and _existing_size == int(abs(float(size)))
                    and _existing_ro is True
                )
                if _same:
                    return {
                        "success": True,
                        "result": _o,
                        "id": _oid,
                        "order_id": _oid,
                        "client_order_id": _o.get("client_order_id", ""),
                        "verified_existing": True,
                    }

                # A mismatched open target is cancelled only when the API proves
                # that none of it has filled. If fill state is unknown/partial,
                # stop safely rather than risking a duplicate close order.
                _total, _filled, _unfilled = _grid_order_fill_quantities(_o)
                if not _oid or _filled is None or _filled > 1e-9:
                    return {
                        "success": False,
                        "error": (
                            f"Existing target {label} mismatches the fixed target and "
                            "its fill state is unknown/partial; refusing to duplicate "
                            "or cancel it automatically. "
                            f"Existing id={_oid}, side={_existing_side}, "
                            f"price={_existing_price}, size={_existing_size}, "
                            f"reduce_only={_existing_ro}; requested "
                            f"side={str(side).lower()}, price={price}, "
                            f"size={int(abs(float(size)))}, reduce_only=True."
                        ),
                    }
                _cancel_result = api.cancel_order(_oid)
                _verify_ok, _verified_orders = _grid_fetch_orders()
                if not _verify_ok:
                    return {
                        "success": False,
                        "error": (
                            f"Mismatched target {_oid} cancellation cannot be verified; "
                            "no replacement target was sent."
                        ),
                    }
                _still_live = any(
                    _grid_is_bot(_x) and _grid_order_matches_label(_x, label)
                    for _x in _verified_orders
                )
                if _still_live:
                    return {
                        "success": False,
                        "error": (
                            f"Mismatched target {_oid} is still listed by Delta after "
                            f"cancellation attempt ({_cancel_result}); replacement "
                            "held to avoid duplicates."
                        ),
                    }
                live_orders = _verified_orders

            pos_ok, pos, raw_pos_size = _grid_fetch_position()
            if not pos_ok:
                return {
                    "success": False,
                    "error": (
                        "Delta position verification failed; target is held safely "
                        "and will retry next refresh."
                    ),
                }
            try:
                pos_size = float(raw_pos_size or 0)
            except (TypeError, ValueError):
                pos_size = 0.0
            if abs(pos_size) < 1e-9:
                return {
                    "success": False,
                    "error": (
                        "waiting_for_position: Delta reports flat; target will retry "
                        "after the entry fill appears in the live position."
                    ),
                }

            # Delta position payload normally carries side. If not, use signed size
            # when available; do not guess when the direction cannot be established.
            _pos_side_raw = str(
                pos.get("side", pos.get("direction", "")) or ""
            ).strip().lower()
            if _pos_side_raw in {"buy", "long"}:
                _pos_direction = "BUY"
            elif _pos_side_raw in {"sell", "short"}:
                _pos_direction = "SELL"
            elif pos_size > 0:
                _pos_direction = "BUY"
            elif pos_size < 0:
                _pos_direction = "SELL"
            else:
                _pos_direction = ""
            if _pos_direction and _pos_direction != str(direction).upper():
                return {
                    "success": False,
                    "error": (
                        f"position_direction_mismatch: expected {direction}, "
                        f"Delta position is {_pos_direction}; target will retry "
                        "after reconciliation."
                    ),
                }

            try:
                requested = int(abs(float(size)))
            except (TypeError, ValueError):
                requested = 0
            if requested <= 0:
                return {
                    "success": False,
                    "error": "No confirmed executed contracts are available for this target.",
                }

            # Do not arm reduce-only targets whose aggregate size exceeds the live
            # position. Count only currently open GRID targets for this cycle.
            try:
                _cycle_tag_for_target = str(label).split("_")[1]
            except Exception:
                _cycle_tag_for_target = ""
            _reserved_target_contracts = 0
            for _o in live_orders:
                if not _grid_is_bot(_o) or not _grid_is_limit(_o):
                    continue
                _role, _level = _grid_client_role_level(_o)
                _cid = str(_o.get("client_order_id", ""))
                if _role != "TARGET":
                    continue
                if _cycle_tag_for_target and f"_{_cycle_tag_for_target}_" not in _cid:
                    continue
                _reserved_target_contracts += _grid_contracts(_o)

            _position_contracts = int(abs(pos_size))
            _available_target_contracts = max(
                0, _position_contracts - _reserved_target_contracts
            )
            if _available_target_contracts <= 0:
                return {
                    "success": False,
                    "error": (
                        f"waiting_for_target_capacity: live position={_position_contracts} "
                        f"contracts, already reserved by open targets={_reserved_target_contracts}; "
                        "will retry after exchange state updates."
                    ),
                }
            if _available_target_contracts < requested:
                return {
                    "success": False,
                    "error": (
                        f"waiting_for_target_capacity: target needs {requested} contracts, "
                        f"but only {_available_target_contracts} unreserved position "
                        "contracts are currently verified; no partial target was placed."
                    ),
                }

            result = _grid_place_limit(
                side=side,
                price=price,
                label=label,
                direction=direction,
                size=requested,
                reduce_only=True,
            )
            if isinstance(result, dict) and result.get("success") is True:
                return result

            # If Delta reports a reduce-only/position race, don't mark the lifecycle
            # complete. The next Streamlit refresh re-checks actual position and retries.
            return result
        # CLOSED ORDERS / LIVE ORDERS FOR LEVEL STATE
        # --------------------------------------------------------
        grid_open_ok, grid_open_orders = _grid_fetch_orders()
        grid_closed_ok, grid_closed_orders = _grid_fetch_closed_orders()

        def _closed_order_epoch(order):
            # ISO-8601 timestamps must be parsed as timestamps, not skipped in
            # favour of the numeric order ID; role sequencing depends on this.
            for key in ("updated_at", "filled_at", "executed_at", "closed_at", "created_at", "id"):
                value = order.get(key)
                if value in (None, ""):
                    continue
                try:
                    numeric = float(value)
                    if numeric > 10_000_000_000:
                        numeric /= 1000.0
                    return numeric
                except (TypeError, ValueError):
                    try:
                        parsed = pd.Timestamp(value)
                        if pd.isna(parsed):
                            continue
                        if parsed.tzinfo is None:
                            parsed = parsed.tz_localize("UTC")
                        return float(parsed.timestamp())
                    except Exception:
                        continue
            return 0.0

        startup_cycle_armed = (
            st.session_state.get("grid_startup_armed_cycle", "")
            == new_cycle_key
        )

        # --------------------------------------------------------
        # ONE INDEPENDENT CHAIN PER FIXED LEVEL
        # --------------------------------------------------------
        # ENTRY @ fixed level
        #   -> TARGET @ fixed level +/- GRID_STEP
        #   -> RE-ENTRY @ SAME fixed level
        #   -> TARGET @ SAME fixed target
        #   -> repeat
        #
        # Crucial startup rule:
        # A pending LIMIT entry gets NO target until that exact level fills.
        # If a fixed-price LIMIT fills immediately because it is marketable,
        # its target is still based on the fixed level, never on the live fill price.
        for level_no, price in pending_entry_levels:
            price = float(price)

            entry_label = f"ENTRY_{_cycle_tag}_L{level_no}"
            target_label = f"TARGET_{_cycle_tag}_L{level_no}"
            reentry_label = f"REENTRY_{_cycle_tag}_L{level_no}"

            target_price = _target_from_original_level(
                level_no,
                grid_direction
            )

            entry_open = _grid_has_client_label(
                grid_open_orders,
                entry_label
            )
            target_open = _grid_has_client_label(
                grid_open_orders,
                target_label
            )
            reentry_open = _grid_has_client_label(
                grid_open_orders,
                reentry_label
            )

            latest_role = ""
            latest_epoch = -1.0
            latest_order_id = ""

            if grid_closed_ok:
                for closed in grid_closed_orders:
                    if not _grid_is_bot(closed):
                        continue
                    if not _grid_order_was_filled(closed):
                        continue

                    order_id = _grid_order_id(closed)
                    if not order_id:
                        # A closed order without Delta's actual exchange ID is
                        # not a confirmed order instance.
                        continue

                    cid = str(closed.get("client_order_id", ""))
                    epoch = _closed_order_epoch(closed)

                    matched_role = ""
                    matched_label = ""
                    if _grid_order_matches_label(closed, entry_label):
                        matched_role = "ENTRY"
                        matched_label = entry_label
                    elif _grid_order_matches_label(closed, target_label):
                        matched_role = "TARGET"
                        matched_label = target_label
                    elif _grid_order_matches_label(closed, reentry_label):
                        matched_role = "REENTRY"
                        matched_label = reentry_label

                    if matched_role and epoch >= latest_epoch:
                        _grid_record_order_identity(
                            cid, order_id, matched_label, grid_direction
                        )
                        latest_role = matched_role
                        latest_epoch = epoch
                        latest_order_id = order_id

            # A still-open entry with zero executed contracts is only pending;
            # it must not get a target. If Delta reports a partial fill while
            # the remainder is still open, treat the filled portion as an ENTRY
            # event and place its fixed-level target instead of silently waiting.
            _active_entry_order = next((
                o for o in grid_open_orders
                if _grid_is_bot(o) and _grid_order_matches_label(o, entry_label)
            ), None)
            _active_entry_filled = (
                _grid_order_filled_contracts(_active_entry_order)
                if _active_entry_order else 0.0
            )
            if entry_open and _active_entry_filled <= 0:
                continue
            if entry_open and _active_entry_filled > 0:
                _active_entry_id = _grid_order_id(_active_entry_order)
                _active_entry_epoch = _grid_order_event_epoch(_active_entry_order)
                if _active_entry_epoch >= latest_epoch:
                    latest_role = "ENTRY"
                    latest_epoch = _active_entry_epoch
                    latest_order_id = _active_entry_id or f"partial-L{level_no}"

            # One action per exact level-state. This is deliberately based on
            # the latest filled role/epoch, not just price or label. It prevents
            # duplicate target/re-entry placement when Delta's open/closed feeds
            # lag for a moment after an order is submitted.
            if "grid_action_done" not in st.session_state:
                st.session_state["grid_action_done"] = set()
            _grid_action_done = st.session_state["grid_action_done"]
            _state_action_key = (
                f"{new_cycle_key}|L{level_no}|{latest_role}|"
                f"{latest_order_id or latest_epoch}"
            )

            # If the exact level has never filled in this cycle, arm/repair
            # its fixed entry. This also recovers after a Streamlit restart.
            if latest_role == "":
                # If this cycle was successfully submitted, do not immediately
                # duplicate an order while Delta is still updating its
                # closed/open-order feed. Failed startup submissions are retried
                # only when the cycle was NOT fully armed.
                if startup_cycle_armed:
                    continue
                # Startup already attempted this level in this same refresh.
                # Do not immediately retry it and print a duplicate failure.
                if level_no in grid_attempted_this_refresh:
                    continue

                if (
                    not target_open
                    and not reentry_open
                    and _state_action_key not in _grid_action_done
                ):
                    # Recovery uses the SAME immutable fixed level as a LIMIT.
                    # A marketable LIMIT may fill immediately; it is never changed
                    # into a MARKET order and never recalculated from live CMP.
                    result = _grid_place_limit(
                        side=entry_side,
                        price=price,
                        label=entry_label,
                        direction=grid_direction,
                        size=GRID_CHUNK_CONTRACTS
                    )
                    mode = "LIMIT"

                    if not (
                        isinstance(result, dict)
                        and result.get("success") is True
                    ):
                        _grid_log_event(account_id, level_no, "ENTRY_RETRY", result, price)
                        st.error(
                            f"❌ L{level_no} entry retry failed "
                            f"({mode}) @ {show_price(price)}: {result}"
                        )
                    else:
                        _grid_action_done.add(_state_action_key)
                continue

            # Entry filled -> identify the level by its fixed client label and
        # build the target from the immutable fixed level, never from fill price.

            if latest_role == "ENTRY":
                if (
                    not target_open
                    and not reentry_open
                    and _state_action_key not in _grid_action_done
                ):
                    # Match the target size to the actual entry execution, not
                    # blindly to the configured chunk when an entry partially fills.
                    _entry_fill_qty = 0.0
                    for _entry_row in (grid_open_orders + grid_closed_orders):
                        if _grid_order_id(_entry_row) == str(latest_order_id) and _grid_order_matches_label(_entry_row, entry_label):
                            _entry_fill_qty = _grid_order_filled_contracts(_entry_row)
                            break
                    _target_qty = int(round(_entry_fill_qty))
                    if _target_qty <= 0:
                        # A closed-order status alone is not enough to invent a
                        # target quantity. Wait until Delta's fill endpoint
                        # confirms executed contracts.
                        continue
                    result = _grid_place_fixed_target(
                        side=target_side,
                        price=target_price,
                        label=target_label,
                        direction=grid_direction,
                        size=_target_qty
                    )
                    if not (
                        isinstance(result, dict)
                        and result.get("success") is True
                    ):
                        _target_error = str(result.get("error", result)) if isinstance(result, dict) else str(result)
                        _grid_log_event(account_id, level_no, "TARGET_ENTRY", _target_error, target_price)
                        if any(_w in _target_error.lower() for _w in ("waiting_for_position", "waiting_for_target_capacity", "position_direction_mismatch", "position verification failed", "open orders before target")):
                            st.warning(f"⏳ L{level_no} target is waiting for verified Delta position/state at fixed price {show_price(target_price)}. It will retry on refresh. Details: {_target_error}")
                        else:
                            st.error(f"❌ L{level_no} target failed @ fixed target {show_price(target_price)}: {result}")
                    else:
                        _grid_action_done.add(_state_action_key)

            # Target filled -> re-entry goes back to THE SAME fixed level.
            elif latest_role == "TARGET":
                if (
                    not reentry_open
                    and not target_open
                    and _state_action_key not in _grid_action_done
                ):
                    result = _grid_place_limit(
                        side=reentry_side,
                        price=price,
                        label=reentry_label,
                        direction=grid_direction,
                        size=GRID_CHUNK_CONTRACTS
                    )
                    if not (
                        isinstance(result, dict)
                        and result.get("success") is True
                    ):
                        st.error(
                            f"❌ L{level_no} re-entry failed "
                            f"@ fixed level {show_price(price)}: {result}"
                        )
                    else:
                        _grid_action_done.add(_state_action_key)

            # Re-entry filled -> same fixed target again.
            elif latest_role == "REENTRY":
                if (
                    not target_open
                    and not reentry_open
                    and _state_action_key not in _grid_action_done
                ):
                    _reentry_fill_qty = 0.0
                    for _reentry_row in (grid_open_orders + grid_closed_orders):
                        if _grid_order_id(_reentry_row) == str(latest_order_id) and _grid_order_matches_label(_reentry_row, reentry_label):
                            _reentry_fill_qty = _grid_order_filled_contracts(_reentry_row)
                            break
                    _target_qty = int(round(_reentry_fill_qty))
                    if _target_qty <= 0:
                        # Do not guess executed quantity; wait for Delta fills.
                        continue
                    result = _grid_place_fixed_target(
                        side=target_side,
                        price=target_price,
                        label=target_label,
                        direction=grid_direction,
                        size=_target_qty
                    )
                    if not (
                        isinstance(result, dict)
                        and result.get("success") is True
                    ):
                        _target_error = str(result.get("error", result)) if isinstance(result, dict) else str(result)
                        _grid_log_event(account_id, level_no, "TARGET_REENTRY", _target_error, target_price)
                        if any(_w in _target_error.lower() for _w in ("waiting_for_position", "waiting_for_target_capacity", "position_direction_mismatch", "position verification failed", "open orders before target")):
                            st.warning(f"⏳ L{level_no} target is waiting for verified Delta position/state at fixed price {show_price(target_price)}. It will retry on refresh. Details: {_target_error}")
                        else:
                            st.error(f"❌ L{level_no} target retry failed @ fixed target {show_price(target_price)}: {result}")
                    else:
                        _grid_action_done.add(_state_action_key)

            # Pull both feeds after lifecycle actions. This keeps the following
            # chart/report pass tied to Delta's latest exchange state.
            grid_open_ok, grid_open_orders = _grid_fetch_orders()
            grid_closed_ok, grid_closed_orders = _grid_fetch_closed_orders()


    # ------------------------------------------------------------
    # DELTA EXCHANGE ORDER REPORT — OPEN + FILLED/CLOSED
    # ------------------------------------------------------------
    # This report is intentionally sourced from Delta itself, not from
    # Streamlit session state.  Therefore a LIMIT, MARKET, FILLED, CANCELLED
    # or REJECTED order can be seen even after the open-order list changes.
    grid_open_ok, grid_open_orders = _grid_fetch_orders()
    grid_closed_ok, grid_closed_orders = _grid_fetch_closed_orders()

    # A target chain depends on reliable execution history. Show the exact
    # private fills endpoint error to the operator instead of hiding it.
    if _grid_fills_cache.get("loaded") and not _grid_fills_cache.get("ok"):
        st.error(
            "⚠️ DELTA FILLS API FAILED — automatic ENTRY → TARGET / TARGET → RE-ENTRY "
            "verification may be incomplete. Error: "
            f"{_grid_fills_cache.get('error') or 'Unknown fills API error'}"
        )

    st.subheader("📋 DELTA EXCHANGE ORDER REPORT")
    _exchange_report_rows = []
    _seen_exchange_orders = set()
    _exchange_orders = []
    if grid_open_ok:
        _exchange_orders.extend(grid_open_orders)
    if grid_closed_ok:
        _exchange_orders.extend(grid_closed_orders)

    for order in sorted(_exchange_orders, key=_grid_order_event_epoch, reverse=True)[:150]:
        _oid = str(order.get("id", "")).strip()
        _cid = str(order.get("client_order_id", "")).strip()
        _key = _oid or _cid
        if not _key or _key in _seen_exchange_orders:
            continue
        _seen_exchange_orders.add(_key)

        _role, _level_no = _grid_client_role_level(order)
        _state = str(order.get("state", "-")).lower()
        if _state in {"filled", "closed"}:
            _status = "FILLED / EXECUTED"
        elif _state in {"open", "pending", "active", "partially_filled", "partially-filled"}:
            _status = "PENDING"
        elif _state in {"cancelled", "canceled"}:
            _status = "CANCELLED"
        elif _state in {"rejected", "failed"}:
            _status = "REJECTED"
        else:
            _status = _state.upper()

        _otype = str(order.get("order_type", order.get("type", "-"))).upper()
        _p = _grid_price(order)
        _q = _grid_contracts(order)
        _exchange_report_rows.append({
            "Exchange Order ID": _oid or "-",
            "Client ID": _cid or "-",
            "Role": f"{_role} L{_level_no}" if _role and _level_no else "EXCHANGE / OTHER",
            "Type": _otype,
            "Side": str(order.get("side", "")).upper(),
            "Price": show_price(_p) if _p is not None else "MARKET",
            "Qty": f"{_q * CONTRACT_BTC:.3f} BTC",
            "Status": _status,
        })

    if _exchange_report_rows:
        st.dataframe(pd.DataFrame(_exchange_report_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Delta Exchange se abhi koi open/closed order report nahi mila.")

    # Only genuinely OPEN GRID orders are used for the live pending-order table.
    st.subheader("🟢 LIVE PENDING GRID ORDERS")
    _bot_pending_rows = []
    for order in _grid_active_bot_orders(grid_open_orders if grid_open_ok else []):
        _p = _grid_price(order)
        _q = _grid_contracts(order)
        _role, _level_no = _grid_client_role_level(order)
        _bot_pending_rows.append({
            "Order ID": order.get("id", "-"),
            "Client ID": order.get("client_order_id", "-"),
            "Role": f"{_role} L{_level_no}" if _role else "GRID",
            "Side": str(order.get("side", "")).upper(),
            "Price": show_price(_p) if _p is not None else "-",
            "Qty": f"{_q * CONTRACT_BTC:.3f} BTC",
            "Status": "PENDING"
        })

    if _bot_pending_rows:
        st.dataframe(pd.DataFrame(_bot_pending_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Abhi koi GRID pending order exchange par open nahi hai. Filled order ko green pending line ke roop mein nahi dikhaya jayega.")


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
    st.write(f"Signal price: **{show_price(grid_signal_base)}**")
    st.write(f"ATR line: **{show_price(grid_atr_line)}**")
    st.write(f"Fixed grid: **{GRID_LEVEL_COUNT} levels × {GRID_STEP} points** | Anchor L1: **{show_price(grid_atr_line)}**")
    st.write(f"All fixed levels armed: **{GRID_LEVEL_COUNT}** | L1 ATR anchor: **{show_price(grid_atr_line)}**")
    st.write(f"GRID QUANTITY PER ORDER: **{GRID_CHUNK_BTC:.3f} BTC** ({GRID_CHUNK_CONTRACTS} contracts)")
    st.caption("PENDING ENTRY / TARGET / RE-ENTRY — sab isi quantity par chalenge.")
    st.write(f"Startup: **{GRID_LEVEL_COUNT} levels × {GRID_CHUNK_BTC:.3f} BTC** | Per-level quantity: **{GRID_CHUNK_BTC:.3f} BTC**")
    st.write(f"Startup mode: **ALL {GRID_LEVEL_COUNT} fixed levels = LIMIT** | Marketable crossed levels may **fill immediately** | Uncrossed levels remain **LIMIT**")
    st.write("**No later order is chased by live price. Every real filled order is matched to its own fixed level ID. Target is always calculated from that level, never from the actual fill/CMP. Direction change cancels the old grid and starts a new ATR-anchored cycle.**")
    st.write("**Chart: 🟢 GREEN = current valid pending ENTRY/RE-ENTRY order | 🔴 RED = current valid TARGET order. Wrong/unknown exchange transactions are cancelled first and are not drawn as valid grid lines.**")

    st.divider()
    st.subheader("🧾 PERSISTENT GRID ERROR/WARNING LOG (SURVIVES REFRESH)")
    _persist_log_rows = st.session_state.get("grid_error_log", [])
    if _persist_log_rows:
        st.dataframe(pd.DataFrame(_persist_log_rows[::-1]), use_container_width=True, hide_index=True)
    else:
        st.info("Abhi tak koi error/warning log nahi hua.")



    # ============================================================
    # PART 4/4 — REAL DELTA CANDLE CHART + SUPERTREND + GRID
    # ============================================================
    # Display only:
    # - Uses the SAME completed 1-hour Delta candles already loaded in df.
    # - Does not change order placement / target / re-entry logic.
    # - STARTUP arms all 25 fixed levels as LIMIT orders; marketable levels may fill immediately.
    # - Every grid level uses the configured GRID QUANTITY PER ORDER.
    # - Upper grid = RED, lower grid = GREEN.
    # - SuperTrend line = GREEN on BUY direction, RED on SELL direction.
    # - Prices and order quantities are written directly on the chart.
    # ============================================================

    now_ist = datetime.now(timezone.utc).astimezone(IST)
    st.write(f"Dashboard Time: **{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}**")

    # Server-side strategy data remains COMPLETED-CANDLE ONLY.
    # The chart independently loads the real current/incomplete Delta candle
    # through Delta's public candle API + candlestick WebSocket below.
    # Therefore the live candle never enters SuperTrend/order calculations.
    _chart_df = df.tail(80).copy()

    _chart_times = [
        datetime.fromtimestamp(int(v), tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M")
        for v in _chart_df["time"].tolist()
    ]
    _chart_open = [float(v) for v in _chart_df["open"].tolist()]
    _chart_high = [float(v) for v in _chart_df["high"].tolist()]
    _chart_low = [float(v) for v in _chart_df["low"].tolist()]
    _chart_close = [float(v) for v in _chart_df["close"].tolist()]

    # SuperTrend is already calculated by the existing engine.
    _chart_st = [None if pd.isna(v) else float(v) for v in _chart_df["SUPERTREND"].tolist()]
    _chart_dir = [int(v) for v in _chart_df["ST_DIRECTION"].tolist()]

    # TSL uses the SAME confirmed SuperTrend line already used by the strategy.
    # It is display-only: no order/trading logic is changed here.
    _chart_tsl_price = float(current_supertrend)
    _chart_tsl_color = "#22c55e" if current_trend == -1 else "#ef5350"
    _chart_tsl_direction = "BUY" if current_trend == -1 else "SELL"


    # LIVE ORDER LINES — REAL DELTA ORDERS ONLY
    # ------------------------------------------------------------
    # IMPORTANT: The chart never invents a grid/target/re-entry line.
    # It displays only orders that are currently returned by Delta's
    # private OPEN-ORDERS endpoint for this OWNER account.
    # Bot orders and manually placed orders are both shown.
    _chart_grid_lines = []
    _chart_open_order_labels = []
    _chart_target_lines = []

    if grid_open_ok:
        for order in grid_open_orders:
            _p = _grid_price(order)
            if _p is None:
                continue
            _state = str(order.get("state", "")).lower()
            if _state in {"cancelled", "filled", "rejected"}:
                continue

            _cid = str(order.get("client_order_id", "")).strip()
            _role, _level_no = _grid_client_role_level(order)
            # FINAL CHART COLOR RULE:
            # ENTRY / RE-ENTRY = GREEN, TARGET = RED.
            # This is visual only; it never changes order side or trading logic.
            if _role == "TARGET":
                _line_role = "TARGET"
                _line_color = "#ef4444"
            elif _role == "ENTRY":
                _line_role = "PENDING ENTRY"
                _line_color = "#22c55e"
            elif _role == "REENTRY":
                _line_role = "PENDING REENTRY"
                _line_color = "#22c55e"
            else:
                # Manual / externally-created order: show it exactly as Delta
                # reports it; never infer a target or grid level.
                _line_role = "OPEN ORDER"
                _line_color = "#60a5fa"

            _q = _grid_contracts(order)
            _side = str(order.get("side", "")).upper()
            _order_id = str(order.get("id", order.get("order_id", ""))).strip()
            _level_text = f"L{_level_no}" if _level_no else ""
            _chart_grid_lines.append({
                "price": float(_p),
                "color": _line_color,
                "width": 3,
                "dash": "solid",
                "label": (
                    f"{_line_role} {_level_text} • {show_price(_p)} • "
                    f"{_q * CONTRACT_BTC:.3f} BTC • {_side} • ORDER {_order_id}"
                ),
                "role": _line_role,
                "order_id": _order_id,
                "client_id": _cid,
            })
            _chart_open_order_labels.append(
                f"{_line_role} {_level_text} {_side} "
                f"{_q * CONTRACT_BTC:.3f} BTC @ {show_price(_p)} • ORDER {_order_id}"
            )

    # ------------------------------------------------------------
    # IMPORTANT: DO NOT DRAW PLANNED/FUTURE GRID LINES
    # ------------------------------------------------------------
    # A line is drawn on the chart ONLY when the corresponding order is
    # actually open on Delta right now.  Fixed grid levels that are merely
    # calculated/armed, rejected for margin, or not yet submitted are NOT
    # displayed as order lines.  This prevents red/green lines from appearing
    # before a real exchange order exists.

    # Historical SuperTrend signals.
    _chart_signal_x = []
    _chart_signal_y = []
    _chart_signal_text = []
    _chart_signal_color = []
    # ONLY TRUE SuperTrend direction-change candles get a marker.
    # Entry/re-entry/target orders NEVER create markers or dots.
    for _, row in signal_rows.drop_duplicates(subset=["time"]).tail(1).iterrows():
        _sig = str(row.get("SIGNAL", "")).upper().strip()
        if _sig not in {"BUY", "SELL"}:
            continue
        try:
            _signal_ts = int(float(row["time"]))
            _signal_close = float(row["close"])
        except Exception:
            continue
        _chart_signal_x.append(
            datetime.fromtimestamp(_signal_ts, tz=timezone.utc)
            .astimezone(IST).strftime("%Y-%m-%d %H:%M")
        )
        _chart_signal_y.append(_signal_close)
        _chart_signal_text.append(_sig)
        _chart_signal_color.append("#22c55e" if _sig == "BUY" else "#ff3b30")

    # ============================================================
    # CHART Y-AXIS = ALL REAL ACTIVE ORDER PRICES
    # ============================================================
    # Candles remain the base layer.  Every REAL active ENTRY/RE-ENTRY
    # and TARGET order must remain visible on the SAME price chart.
    # Therefore the Y-axis is calculated from ALL candle prices + ALL
    # real active order prices.  If orders are far above/below candles,
    # the candles may become smaller — that is intentional.  We NEVER
    # hide, clip, offset, or move a real order line just to keep candles large.
    # The live ticker itself is not used to continuously resize the viewport.
    _chart_candle_values = _chart_high + _chart_low + _chart_close
    _chart_order_values = [
        float(L["price"]) for L in _chart_grid_lines
        if L.get("price") is not None
    ]
    _chart_values_for_view = _chart_candle_values + _chart_order_values
    if not _chart_values_for_view:
        _chart_values_for_view = [0.0, 1.0]

    _chart_view_data_min = min(_chart_values_for_view)
    _chart_view_data_max = max(_chart_values_for_view)
    _chart_view_span = max(_chart_view_data_max - _chart_view_data_min, 1.0)
    _chart_pad = max(_chart_view_span * 0.05, 15.0)

    # Recalculate the viewport whenever the completed candles or REAL
    # active order set changes.  A normal live-price refresh does not move it.
    _chart_view_key = (
        tuple(_chart_times),
        tuple(
            (str(x.get("order_id", "")), round(float(x["price"]), 2), str(x.get("role", "")))
            for x in _chart_grid_lines
        ),
        grid_direction or "-"
    )
    if st.session_state.get("chart_view_key") != _chart_view_key:
        st.session_state["chart_view_key"] = _chart_view_key
        st.session_state["chart_view_min"] = _chart_view_data_min - _chart_pad
        st.session_state["chart_view_max"] = _chart_view_data_max + _chart_pad

    _chart_min = float(
        st.session_state.get("chart_view_min", _chart_view_data_min - _chart_pad)
    )
    _chart_max = float(
        st.session_state.get("chart_view_max", _chart_view_data_max + _chart_pad)
    )

    import json as _json

    _chart_payload = {
        "times": _chart_times,
        "open": _chart_open,
        "high": _chart_high,
        "low": _chart_low,
        "close": _chart_close,
        "tsl_price": _chart_tsl_price,
        "tsl_color": _chart_tsl_color,
        "signals_x": _chart_signal_x,
        "signals_y": _chart_signal_y,
        "signals_text": _chart_signal_text,
        "signals_color": _chart_signal_color,
        "cycle_signal_x": _chart_signal_x[-1] if _chart_signal_x else (_chart_times[0] if _chart_times else ""),
        "last_candle_x": _chart_times[-1] if _chart_times else "",
        "grid_lines": _chart_grid_lines,
        "target_lines": _chart_target_lines,
        "open_orders": _chart_open_order_labels,
        "direction": grid_direction or "-",
        "symbol": SYMBOL,
        "timeframe": "1H",
        "min_price": _chart_min - _chart_pad,
        "max_price": _chart_max + _chart_pad,
        # Live ticker price is intentionally separate from the completed-candle
        # close used by the SuperTrend engine. It never changes fixed levels or targets.
        "current_price": float(live_price),
    }

    _chart_json = _json.dumps(_chart_payload, ensure_ascii=False)

    components.html(
        """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Delta Exchange Chart</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  body { background:#131722; color:#d1d4dc; font-family:Arial,sans-serif; margin:0; padding:10px; overflow:hidden; }
  #toolbar { margin-bottom:10px; white-space:nowrap; overflow-x:auto; }
  .tf-btn {
    background:#2a2e39; color:#d1d4dc; border:1px solid #434651;
    padding:6px 14px; margin-right:5px; cursor:pointer; border-radius:4px;
  }
  .tf-btn.active { background:#2962ff; color:#fff; }
  #chart { width:100%; height:600px; }
  #status { margin-top:8px; font-size:12px; color:#787b86; }
  #live-price-box {
    display:inline-flex; align-items:center; gap:8px; margin-left:14px;
    padding:6px 11px; border-radius:7px; border:1px solid #3a3f4b;
    background:#171a21; font-size:15px; font-weight:900;
    vertical-align:middle;
  }
  #live-price-label { color:#9aa4b2; font-size:11px; letter-spacing:.4px; }
  #live-price-value { color:#d1d4dc; font-variant-numeric:tabular-nums; }
</style>
</head>
<body>

<div id="toolbar">
  <button class="tf-btn" data-res="1m">1m</button>
  <button class="tf-btn" data-res="5m">5m</button>
  <button class="tf-btn" data-res="15m">15m</button>
  <button class="tf-btn" data-res="1h">1h</button>
  <button class="tf-btn" data-res="4h">4h</button>
  <button class="tf-btn" data-res="1d">1d</button>
  <label for="indicatorSelect" style="margin-left:15px;font-weight:800;">INDICATORS</label>
  <select id="indicatorSelect" style="margin-left:6px;background:#2a2e39;color:#d1d4dc;border:1px solid #434651;padding:6px 10px;border-radius:4px;">
    <option value="none" selected>None</option>
    <option value="supertrend">SuperTrend (10, 2)</option>
  </select>
  <span id="live-price-box"><span id="live-price-label">LIVE PRICE</span><span id="live-price-value">--</span></span>
</div>

<div id="chart"></div>
<div id="status">Connecting...</div>

<script>
const SERVER_DATA = __CHART_DATA__;
const SYMBOL = "__SYMBOL__";
const BASE_URL = "__PUBLIC_BASE_URL__";
const WS_URL = "__WS_URL__";

const ATR_PERIOD = 10;
const ATR_MULTIPLIER = 2;

let currentResolution = "__TIMEFRAME__";
let candles = [];
let ws = null;
let reconnectTimer = null;
let pingTimer = null;

const chartContainer = document.getElementById('chart');
const statusEl = document.getElementById('status');

const chart = LightweightCharts.createChart(chartContainer, {
  layout: { background: { color: '#131722' }, textColor: '#d1d4dc' },
  grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
  timeScale: { borderColor: '#434651', timeVisible: true, secondsVisible: false, rightOffset: 5 },
  rightPriceScale: { borderColor: '#434651' }
});

const candleSeries = chart.addCandlestickSeries({
  upColor: '#26a69a',
  downColor: '#ef5350',
  borderVisible: false,
  wickUpColor: '#26a69a',
  wickDownColor: '#ef5350',
  priceLineVisible: false,
  lastValueVisible: true
});

// SuperTrend is drawn as separate color segments.
// IMPORTANT: the flip candle belongs to ONLY the new color segment.
// This prevents the old/new series from overlapping at the direction change.
let supertrendSegments = [];
let selectedIndicator = "none";

let orderPriceLines = [];
let livePriceLine = null;
let previousLivePrice = null;

const livePriceValueEl = document.getElementById('live-price-value');

function formatLivePrice(price) {
  if (!Number.isFinite(Number(price))) return '--';
  return Number(price).toLocaleString('en-US', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1
  });
}

function liveMovementColor(price) {
  const p = Number(price);
  if (!Number.isFinite(p) || previousLivePrice === null) return '#d1d4dc';
  if (p > previousLivePrice) return '#22c55e';
  if (p < previousLivePrice) return '#ef5350';
  return '#d1d4dc';
}

function updateLivePrice(price, remember = true) {
  const p = Number(price);
  if (!Number.isFinite(p)) return;

  const color = liveMovementColor(p);
  if (livePriceValueEl) {
    livePriceValueEl.textContent = formatLivePrice(p);
    livePriceValueEl.style.color = color;
  }

  if (livePriceLine) {
    try {
      livePriceLine.applyOptions({ price: p, color });
    } catch (e) {}
  }

  if (remember) previousLivePrice = p;
}

window.addEventListener('resize', () => {
  chart.resize(chartContainer.clientWidth, chartContainer.clientHeight);
});

function calculateSupertrend(data, period, multiplier) {
  const result = [];
  let atr = 0;
  const finalUpperBand = [];
  const finalLowerBand = [];
  const trend = [];

  for (let i = 0; i < data.length; i++) {
    const curr = data[i];
    const prev = data[i - 1];

    let tr;
    if (i === 0) {
      tr = curr.high - curr.low;
    } else {
      tr = Math.max(
        curr.high - curr.low,
        Math.abs(curr.high - prev.close),
        Math.abs(curr.low - prev.close)
      );
    }

    if (i === 0) {
      atr = tr;
    } else if (i < period) {
      atr = (atr * i + tr) / (i + 1);
    } else {
      atr = (atr * (period - 1) + tr) / period;
    }

    const mid = (curr.high + curr.low) / 2;
    const basicUpper = mid + multiplier * atr;
    const basicLower = mid - multiplier * atr;

    let fUpper, fLower;
    if (i === 0) {
      fUpper = basicUpper;
      fLower = basicLower;
    } else {
      fUpper =
        (basicUpper < finalUpperBand[i - 1] ||
         data[i - 1].close > finalUpperBand[i - 1])
          ? basicUpper
          : finalUpperBand[i - 1];

      fLower =
        (basicLower > finalLowerBand[i - 1] ||
         data[i - 1].close < finalLowerBand[i - 1])
          ? basicLower
          : finalLowerBand[i - 1];
    }

    finalUpperBand[i] = fUpper;
    finalLowerBand[i] = fLower;

    let dir;
    if (i === 0) {
      dir = 1;
    } else if (trend[i - 1] === 1 && curr.close < fLower) {
      dir = -1;
    } else if (trend[i - 1] === -1 && curr.close > fUpper) {
      dir = 1;
    } else {
      dir = trend[i - 1];
    }

    trend[i] = dir;
    result.push({
      time: curr.time,
      value: dir === 1 ? fLower : fUpper,
      trend: dir
    });
  }

  return result;
}

function renderSupertrend(stData) {
  // Clean segmented SuperTrend drawing:
  // BUY = green, SELL = red. When the direction changes, the previous
  // segment is closed and a new segment starts at the flip candle.
  // The flip candle is NEVER drawn in both series, so there is no double line.

  for (const series of supertrendSegments) {
    try { chart.removeSeries(series); } catch (e) {}
  }
  supertrendSegments = [];

  if (!Array.isArray(stData) || stData.length === 0) return;

  let currentSeries = null;
  let currentColor = null;
  let currentData = [];

  for (let i = 0; i < stData.length; i++) {
    const item = stData[i];
    if (!item || !Number.isFinite(Number(item.value))) continue;

    const val = Number(item.value);
    // Existing engine convention: trend === -1 = BUY, trend === 1 = SELL.
    const col = Number(item.trend) === -1 ? '#22c55e' : '#ef5350';

    if (currentColor !== col) {
      if (currentSeries && currentData.length > 0) {
        currentSeries.setData(currentData);
      }

      currentColor = col;
      currentData = [];
      currentSeries = chart.addLineSeries({
        color: currentColor,
        lineWidth: 3,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
      });
      supertrendSegments.push(currentSeries);
    }

    currentData.push({
      time: item.time,
      value: val
    });
  }

  if (currentSeries && currentData.length > 0) {
    currentSeries.setData(currentData);
  }
}

function clearOrderLines() {
  for (const line of orderPriceLines) {
    try { candleSeries.removePriceLine(line); } catch (e) {}
  }
  orderPriceLines = [];

  if (livePriceLine) {
    try { candleSeries.removePriceLine(livePriceLine); } catch (e) {}
    livePriceLine = null;
  }
}

function drawServerOrderLines() {
  clearOrderLines();

  const lines = Array.isArray(SERVER_DATA.grid_lines)
    ? SERVER_DATA.grid_lines
    : [];

  for (const L of lines) {
    const price = Number(L.price);
    if (!Number.isFinite(price)) continue;

    const role = String(L.role || '').toUpperCase();
    const color = L.color || (
      role.includes('TARGET') ? '#ef4444' :
      role.includes('ENTRY') || role.includes('REENTRY') ? '#22c55e' :
      '#60a5fa'
    );
    const style = String(L.dash || '').toLowerCase() === 'dashed'
      ? LightweightCharts.LineStyle.Dashed
      : LightweightCharts.LineStyle.Solid;

    orderPriceLines.push(
      candleSeries.createPriceLine({
        price,
        color,
        lineWidth: Number(L.width || 2),
        lineStyle: style,
        axisLabelVisible: true,
        title: role.includes('TARGET') ? 'TARGET' : 'ENTRY'
      })
    );
  }

  const live = Number(SERVER_DATA.current_price);
  if (Number.isFinite(live)) {
    livePriceLine = candleSeries.createPriceLine({
      price: live,
      color: '#d1d4dc',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'LIVE'
    });
    previousLivePrice = null;
    updateLivePrice(live, true);
  }
}

function drawServerSignalMarkers() {
  const markers = [];
  const xs = Array.isArray(SERVER_DATA.signals_x) ? SERVER_DATA.signals_x : [];
  const ys = Array.isArray(SERVER_DATA.signals_y) ? SERVER_DATA.signals_y : [];
  const texts = Array.isArray(SERVER_DATA.signals_text) ? SERVER_DATA.signals_text : [];

  for (let i = 0; i < xs.length; i++) {
    const t = Math.floor(Date.parse(String(xs[i]).replace(' ', 'T') + ':00+05:30') / 1000);
    if (!Number.isFinite(t)) continue;

    const text = String(texts[i] || '').toUpperCase();
    const buy = text.includes('BUY');
    const entry = ys[i] != null ? Number(ys[i]) : null;

    markers.push({
      time: t,
      position: buy ? 'belowBar' : 'aboveBar',
      color: buy ? '#22c55e' : '#ef5350',
      shape: buy ? 'arrowUp' : 'arrowDown',
      text: Number.isFinite(entry)
        ? ((buy ? 'BUY ' : 'SELL ') +
           entry.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}))
        : text
    });
  }

  markers.sort((a, b) => a.time - b.time);
  if (markers.length) candleSeries.setMarkers(markers);
}

async function fetchCandles(resolution) {
  const end = Math.floor(Date.now() / 1000);
  const barsNeeded = 300;
  const resSeconds = {
    "1m":60,
    "5m":300,
    "15m":900,
    "1h":3600,
    "4h":14400,
    "1d":86400
  };
  const start = end - (resSeconds[resolution] * barsNeeded);

  const url =
    BASE_URL + "/history/candles?symbol=" +
    encodeURIComponent(SYMBOL) +
    "&resolution=" + encodeURIComponent(resolution) +
    "&start=" + start +
    "&end=" + end;

  const res = await fetch(url);
  const json = await res.json();

  if (!json.success) {
    throw new Error(JSON.stringify(json));
  }

  return (json.result || [])
    .sort((a, b) => Number(a.time) - Number(b.time))
    .map(c => ({
      time: Number(c.time),
      open: Number(c.open),
      high: Number(c.high),
      low: Number(c.low),
      close: Number(c.close),
      volume: Number(c.volume || 0)
    }));
}

async function loadChart(resolution) {
  statusEl.innerText = "Loading " + resolution + " candles...";
  currentResolution = resolution;

  try {
    candles = await fetchCandles(resolution);

    candleSeries.setData(candles.map(c => ({
      time: c.time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close
    })));

    // SuperTrend is confirmed-candle only; the current live candle is not
    // allowed to create or visually confirm an intra-candle signal.
    const initialConfirmedCandles = candles.length > 1
      ? candles.slice(0, -1)
      : candles;

    if (selectedIndicator === "supertrend") {
      renderSupertrend(
        calculateSupertrend(initialConfirmedCandles, ATR_PERIOD, ATR_MULTIPLIER)
      );
    } else {
      renderSupertrend([]);
    }

    drawServerOrderLines();
    drawServerSignalMarkers();

    chart.timeScale().fitContent();
    statusEl.innerText =
      "Live: " + SYMBOL + " (" + resolution + ")";

    subscribeWebSocket(resolution);
  } catch (err) {
    statusEl.innerText = "Error fetching candles: " + err.message;
    console.error(err);
  }
}

function subscribeWebSocket(resolution) {
  if (ws) {
    try { ws.close(); } catch (e) {}
    ws = null;
  }

  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    ws.send(JSON.stringify({
      type: "subscribe",
      payload: {
        channels: [
          { name: "candlestick_" + resolution, symbols: [SYMBOL] }
        ]
      }
    }));

    pingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 25000);
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);

      if (!msg.type || !msg.type.startsWith("candlestick_")) return;

      const resSeconds = {
        "1m":60,
        "5m":300,
        "15m":900,
        "1h":3600,
        "4h":14400,
        "1d":86400
      };

      let candleTime;

      if (msg.ts != null) {
        const rawTs = Number(msg.ts);
        candleTime = rawTs > 100000000000
          ? Math.floor(rawTs / 1000000)
          : Math.floor(rawTs);
      } else {
        candleTime = Math.floor(Date.now() / 1000);
      }

      const bucketTime =
        Math.floor(candleTime / resSeconds[currentResolution]) *
        resSeconds[currentResolution];

      const newCandle = {
        time: bucketTime,
        open: Number(msg.o),
        high: Number(msg.h),
        low: Number(msg.l),
        close: Number(msg.c),
        volume: Number(msg.v || 0)
      };

      if (
        !Number.isFinite(newCandle.open) ||
        !Number.isFinite(newCandle.high) ||
        !Number.isFinite(newCandle.low) ||
        !Number.isFinite(newCandle.close)
      ) return;

      const last = candles[candles.length - 1];

      // REAL LIVE CANDLE RULE:
      // - Open is fixed by the first real exchange value for this timestamp.
      // - Every later tick can only move High upward, Low downward, and Close
      //   to the latest real market value.
      // - The same timestamp is updated in-place, so duplicate candles are
      //   never created.
      if (last && last.time === newCandle.time) {
        newCandle.open = Number(last.open);
        newCandle.high = Math.max(Number(last.high), newCandle.high, newCandle.open);
        newCandle.low = Math.min(Number(last.low), newCandle.low, newCandle.open);
        candles[candles.length - 1] = newCandle;
      } else if (!last || newCandle.time > last.time) {
        newCandle.high = Math.max(newCandle.high, newCandle.open);
        newCandle.low = Math.min(newCandle.low, newCandle.open);
        candles.push(newCandle);
        if (candles.length > 350) candles.shift();
      } else {
        return;
      }

      candleSeries.update({
        time: newCandle.time,
        open: newCandle.open,
        high: newCandle.high,
        low: newCandle.low,
        close: newCandle.close
      });

      // The latest real candle close/tick is the live market price.
      // This is display-only and never changes SuperTrend/order logic.
      updateLivePrice(newCandle.close, true);

      // SuperTrend remains confirmed-candle based. The live candle is
      // deliberately excluded so no intra-candle flip becomes a signal.
      const confirmedCandles = candles.length > 1
        ? candles.slice(0, -1)
        : candles;

      if (selectedIndicator === "supertrend") {
        renderSupertrend(
          calculateSupertrend(confirmedCandles, ATR_PERIOD, ATR_MULTIPLIER)
        );
      } else {
        renderSupertrend([]);
      }
    } catch (e) {
      console.error("WebSocket candle error", e);
    }
  };

  ws.onerror = (err) => {
    statusEl.innerText = "WebSocket error";
    console.error(err);
  };

  ws.onclose = () => {
    if (pingTimer) {
      clearInterval(pingTimer);
      pingTimer = null;
    }

    statusEl.innerText = "Disconnected. Reconnecting...";

    reconnectTimer = setTimeout(() => {
      subscribeWebSocket(currentResolution);
    }, 3000);
  };
}

document.getElementById("indicatorSelect").addEventListener("change", function() {
  selectedIndicator = this.value;
  if (selectedIndicator === "supertrend") {
    const confirmed = candles.length > 1 ? candles.slice(0, -1) : candles;
    renderSupertrend(calculateSupertrend(confirmed, ATR_PERIOD, ATR_MULTIPLIER));
  } else {
    renderSupertrend([]);
  }
});

document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tf-btn')
      .forEach(b => b.classList.remove('active'));

    btn.classList.add('active');
    loadChart(btn.dataset.res);
  });
});

document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.classList.toggle('active', btn.dataset.res === currentResolution);
});

loadChart(currentResolution);
</script>

</body>
</html>
"""
        .replace("__CHART_DATA__", _chart_json)
        .replace("__SYMBOL__", str(SYMBOL).replace("\\", "\\\\").replace('"', '\\"'))
        .replace("__TIMEFRAME__", str(TIMEFRAME))
        .replace("__PUBLIC_BASE_URL__", PUBLIC_REST_V2_URL)
        .replace("__WS_URL__", PUBLIC_WS_URL),
        height=660,
        scrolling=False
    )


    # ============================================================
    # ============================================================



    # ============================================================
    # ADD-ON: LIVE MARGIN / 25-LEVEL / LIQUIDATION CALCULATOR
    # DISPLAY + CALCULATION ONLY — DOES NOT PLACE/CANCEL ORDERS.
    # Existing SuperTrend, grid, entry, target and re-entry logic is untouched.
    # ============================================================
    try:
        st.divider()
        st.header(f"🧮 {account_id} — LIVE MARGIN, 25 LEVELS & LIQUIDATION CALCULATOR")
        st.caption(
            "Read-only calculator. It never submits/cancels orders or treats custom balance "
            "as a real deposit. Live values are shown only when received from Delta Exchange."
        )

        _calc_now = datetime.now(timezone.utc)
        _calc_mark = None
        _calc_mark_updated = None
        _calc_product = {}
        _calc_positions = []
        _calc_liq_error = None

        # Real public mark price and contract specification.
        try:
            _calc_ticker_response = api.ticker()
            _calc_ticker_data = _grid_result(_calc_ticker_response)
            if isinstance(_calc_ticker_data, list):
                _calc_ticker_data = _calc_ticker_data[0] if _calc_ticker_data else {}
            if isinstance(_calc_ticker_data, dict):
                for _mk in ("mark_price", "mark", "last_price"):
                    try:
                        _mv = float(_calc_ticker_data.get(_mk))
                        if _mv > 0:
                            _calc_mark = _mv
                            break
                    except (TypeError, ValueError):
                        pass
                _calc_mark_updated = (
                    _calc_ticker_data.get("last_updated_at")
                    or _calc_ticker_data.get("updated_at")
                    or _calc_ticker_data.get("timestamp")
                )
        except Exception:
            _calc_ticker_data = {}

        try:
            _calc_product_response = requests.get(
                f"{BASE_URL}/v2/products/{SYMBOL}",
                headers={"Accept": "application/json"},
                timeout=5,
            )
            _calc_product_response.raise_for_status()
            _calc_product_json = _calc_product_response.json()
            _calc_product = _calc_product_json.get("result", {}) if isinstance(_calc_product_json, dict) else {}
        except Exception:
            _calc_product = {}

        try:
            _calc_pos_response = api.margined_positions()
            _calc_pos_data = _grid_result(_calc_pos_response)
            if isinstance(_calc_pos_data, list):
                _calc_positions = [
                    _p for _p in _calc_pos_data
                    if str(_p.get("product_symbol", _p.get("symbol", SYMBOL))).upper() == str(SYMBOL).upper()
                    and int(float(_p.get("size", 0) or 0)) != 0
                ]
            elif isinstance(_calc_pos_data, dict):
                _calc_positions = [_calc_pos_data] if int(float(_calc_pos_data.get("size", 0) or 0)) != 0 else []
        except Exception as _calc_pos_exc:
            _calc_liq_error = str(_calc_pos_exc)

        def _calc_num(_value):
            try:
                _v = float(_value)
                return _v if pd.notna(_v) else None
            except (TypeError, ValueError):
                return None

        _calc_available = _calc_num(locals().get("available"))
        _calc_balance = _calc_num(locals().get("balance"))
        _calc_blocked = _calc_num(locals().get("blocked"))
        _calc_order_margin = _calc_num(locals().get("order_margin"))
        _calc_position_margin = _calc_num(locals().get("position_margin"))
        _calc_leverage = _calc_num(locals().get("leverage"))
        _calc_contract_value = _calc_num(_calc_product.get("contract_value"))
        _calc_contract_unit = str(_calc_product.get("contract_unit_currency", "") or "")
        _calc_product_maint = _calc_num(_calc_product.get("maintenance_margin"))
        _calc_product_initial = _calc_num(_calc_product.get("initial_margin"))
        _calc_tick_size = _calc_num(_calc_product.get("tick_size"))

        _lev_label = f"{_calc_leverage:g}x" if _calc_leverage is not None and _calc_leverage > 0 else "LEVERAGE DATA UNAVAILABLE"
        _lev_update_label = _calc_now.strftime("%Y-%m-%d %H:%M:%S UTC") if _calc_leverage is not None and _calc_leverage > 0 else "No verified API value"
        _mk_cols = st.columns(4)
        with _mk_cols[0]:
            st.metric("LIVE MARK PRICE", f"{_calc_mark:,.2f}" if _calc_mark is not None else "MARK PRICE UNAVAILABLE")
            if _calc_mark is not None:
                st.caption(f"Fetched: {_calc_now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            else:
                st.warning("Live Mark Price unavailable. Calculator will not substitute CMP for ATR Anchor.")
        with _mk_cols[1]:
            st.metric("ACTUAL EXCHANGE LEVERAGE", _lev_label)
            st.caption(f"API checked: {_lev_update_label}")
        with _mk_cols[2]:
            st.metric("REAL AVAILABLE BALANCE", f"{_calc_available:,.4f} USDT" if _calc_available is not None else "BALANCE DATA UNAVAILABLE")
            st.caption("Available balance is not the same as equity.")
        with _mk_cols[3]:
            st.metric("USED / RESERVED MARGIN", f"{((_calc_blocked or 0) + (_calc_order_margin or 0) + (_calc_position_margin or 0)):,.4f} USDT" if any(v is not None for v in (_calc_blocked, _calc_order_margin, _calc_position_margin)) else "MARGIN DATA UNAVAILABLE")
            st.caption("Exchange fields may overlap; see individual fields below.")

        st.write(
            f"**Account:** {account_id}  |  **Product:** {SYMBOL} (ID {PRODUCT_ID})  |  "
            f"**Contract value:** {_calc_contract_value if _calc_contract_value is not None else 'UNAVAILABLE'} "
            f"{_calc_contract_unit or '(unit unavailable)'}  |  **Tick size:** {_calc_tick_size if _calc_tick_size is not None else 'UNAVAILABLE'}"
        )
        if _calc_product_initial is None or _calc_product_maint is None or _calc_contract_value is None:
            st.warning("Some public contract specifications were unavailable. Margin values below are estimates using live leverage and BTC quantity; exchange-specific adjustments/fees may differ.")
        else:
            st.caption(
                f"Product metadata received: initial_margin={_calc_product_initial}, "
                f"maintenance_margin={_calc_product_maint}, contract_value={_calc_contract_value}."
            )

        # L1 comes only from the currently confirmed signal-time ATR line in the existing engine.
        _calc_anchor = _calc_num(grid_atr_line) if str(grid_direction or "").upper() in {"BUY", "SELL"} else None
        _calc_dir = str(grid_direction or "").upper()
        _calc_step = 100.0
        _calc_qty_btc = _calc_num(GRID_CHUNK_BTC)
        _calc_contracts = _calc_num(GRID_CHUNK_CONTRACTS)
        _calc_levels = []
        _calc_margin_error = None

        st.subheader("📐 CONFIRMED SUPERTREND ATR ANCHOR + FIXED 25 LEVELS")
        if _calc_anchor is None or _calc_anchor <= 0:
            st.warning("ATR ANCHOR UNAVAILABLE: no active confirmed BUY/SELL grid signal. Live Mark Price is not used as L1.")
        else:
            st.write(
                f"**Confirmed direction:** {_calc_dir}  |  **L1 ATR Anchor:** {_calc_anchor:,.2f}  |  "
                f"**Grid step:** 100 points  |  **L25:** {_calc_anchor + (24 * _calc_step if _calc_dir == 'BUY' else -24 * _calc_step):,.2f}  |  "
                f"**L1→L25 distance:** 2,400 points"
            )
            for _n in range(1, 26):
                _lp = _calc_anchor + ((_n - 1) * _calc_step if _calc_dir == "BUY" else -(_n - 1) * _calc_step)
                # This calculator uses the real exchange-reported product contract value when available.
                # For the configured BTC quantity, quantity * price is the notional approximation.
                _notional = (_calc_qty_btc * _lp) if _calc_qty_btc is not None and _lp > 0 else None
                _margin = (_notional / _calc_leverage) if _notional is not None and _calc_leverage is not None and _calc_leverage > 0 else None
                _calc_levels.append({
                    "Level": f"L{_n}", "Price": _lp,
                    "Qty (BTC/order)": _calc_qty_btc,
                    "Contracts/order": _calc_contracts,
                    "Notional (USDT est.)": _notional,
                    "Initial Margin (USDT est.)": _margin,
                })

            _calc_cum = 0.0
            _calc_shortfall_started = None
            _calc_shortfall_total = None
            for _row in _calc_levels:
                _m = _row["Initial Margin (USDT est.)"]
                if _m is not None:
                    _calc_cum += _m
                _row["Cumulative Margin (USDT est.)"] = _calc_cum if _m is not None else None
                _row["Available Balance (USDT)"] = _calc_available
                if _calc_available is not None and _m is not None and _calc_cum > _calc_available and _calc_shortfall_started is None:
                    _calc_shortfall_started = _row["Price"]
                    _calc_shortfall_total = _calc_cum - _calc_available

            st.dataframe(pd.DataFrame(_calc_levels), use_container_width=True, hide_index=True)
            _calc_total_notional = sum(x["Notional (USDT est.)"] or 0 for x in _calc_levels)
            _calc_total_margin = (
                sum(x["Initial Margin (USDT est.)"] for x in _calc_levels)
                if all(x["Initial Margin (USDT est.)"] is not None for x in _calc_levels) else None
            )
            _tot_cols = st.columns(4)
            _tot_cols[0].metric("25 LEVELS — TOTAL QTY", f"{(_calc_qty_btc or 0) * 25:.3f} BTC" if _calc_qty_btc is not None else "UNAVAILABLE")
            _tot_cols[1].metric("25 LEVELS — TOTAL NOTIONAL", f"{_calc_total_notional:,.4f} USDT (est.)")
            _tot_cols[2].metric("25 LEVELS — TOTAL MARGIN", f"{_calc_total_margin:,.4f} USDT (est.)" if _calc_total_margin is not None else "LEVERAGE DATA UNAVAILABLE")
            _tot_cols[3].metric("L25 DISTANCE", "2,400 points")
            if _calc_shortfall_started is not None:
                st.error(
                    f"FIRST ESTIMATED BALANCE SHORTFALL at price {_calc_shortfall_started:,.2f}. "
                    f"At that level cumulative estimated margin exceeds real available balance by "
                    f"{_calc_shortfall_total:,.4f} USDT."
                )
            elif _calc_available is None:
                st.warning("Cannot determine the first shortfall level: real available balance is unavailable.")
            elif _calc_total_margin is not None:
                st.success(f"Estimated 25-level margin shortfall: 0.0000 USDT; estimated remaining available balance: {_calc_available - _calc_total_margin:,.4f} USDT.")
            st.caption("Margin amounts are explicitly estimates (not an exchange margin quote). Real order margin can vary with product margin tiers, open orders, fees and risk rules.")

            # Display-only additional levels supported by the current real available balance.
            _extra_rows = []
            _extra_cum = 0.0
            if _calc_available is not None and _calc_leverage is not None and _calc_leverage > 0 and _calc_qty_btc is not None:
                for _n in range(26, 1001):
                    _lp = _calc_anchor + ((_n - 1) * _calc_step if _calc_dir == "BUY" else -(_n - 1) * _calc_step)
                    if _lp <= 0:
                        break
                    _nm = (_calc_qty_btc * _lp) / _calc_leverage
                    if _extra_cum + _nm > max(0.0, _calc_available - (_calc_total_margin or 0.0)):
                        break
                    _extra_cum += _nm
                    _extra_rows.append({
                        "Level": f"L{_n}", "Price": _lp,
                        "Qty (BTC/order)": _calc_qty_btc,
                        "Notional (USDT est.)": _calc_qty_btc * _lp,
                        "Additional Margin (USDT est.)": _nm,
                        "Cumulative Extra Margin (USDT est.)": _extra_cum,
                    })
            st.subheader("➕ EXTRA LEVEL CAPACITY — DISPLAY ONLY")
            st.write(
                f"Extra levels supported after the first 25 (estimate): **{len(_extra_rows)}**  |  "
                f"Extra quantity: **{len(_extra_rows) * (_calc_qty_btc or 0):.3f} BTC**  |  "
                f"Extra notional: **{sum(x['Notional (USDT est.)'] for x in _extra_rows):,.4f} USDT (est.)**"
            )
            if _extra_rows:
                st.dataframe(pd.DataFrame(_extra_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No additional levels can be confirmed from the currently available balance/leverage data. This does not change the live grid order count.")

        # Current open position: use exchange-reported liquidation price where supplied.
        st.subheader("⚠️ OPEN POSITION — EXCHANGE LIQUIDATION / RISK")
        if _calc_positions:
            for _pi, _pos in enumerate(_calc_positions, 1):
                _psize = _calc_num(_pos.get("size"))
                _entry = _calc_num(_pos.get("entry_price", _pos.get("average_entry_price")))
                _pos_mark = _calc_num(_pos.get("mark_price")) or _calc_mark
                _liq = _calc_num(_pos.get("liquidation_price"))
                _pmargin = _calc_num(_pos.get("margin"))
                _upnl = _calc_num(_pos.get("unrealized_pnl", _pos.get("unrealised_pnl")))
                _p_leverage = _calc_num(_pos.get("leverage"))
                _maint = _calc_num(_pos.get("maintenance_margin"))
                _liq_dist = abs(_pos_mark - _liq) if _pos_mark is not None and _liq is not None else None
                _pos_cols = st.columns(4)
                _pos_cols[0].metric(f"Position {_pi} — Side / Qty", f"{'LONG' if (_psize or 0) > 0 else 'SHORT'} / {abs(_psize or 0):,.0f} contracts")
                _pos_cols[1].metric("Average Entry", f"{_entry:,.2f}" if _entry is not None else "UNAVAILABLE")
                _pos_cols[2].metric("Mark Price", f"{_pos_mark:,.2f}" if _pos_mark is not None else "UNAVAILABLE")
                _pos_cols[3].metric("Exchange Liquidation Price", f"{_liq:,.2f}" if _liq is not None else "UNAVAILABLE")
                st.write(
                    f"Position leverage: **{f'{_p_leverage:g}x' if _p_leverage is not None else 'LEVERAGE DATA UNAVAILABLE'}**  |  "
                    f"Position margin: **{f'{_pmargin:,.4f} USDT' if _pmargin is not None else 'UNAVAILABLE'}**  |  "
                    f"Maintenance margin: **{f'{_maint:,.4f} USDT' if _maint is not None else 'UNAVAILABLE'}**  |  "
                    f"Unrealized P&L: **{f'{_upnl:,.4f} USDT' if _upnl is not None else 'UNAVAILABLE'}**  |  "
                    f"Distance to liquidation: **{f'{_liq_dist:,.2f} points' if _liq_dist is not None else 'UNAVAILABLE'}**"
                )
                if _liq is None:
                    st.warning("Exchange liquidation price unavailable for this position; no synthetic liquidation price is presented.")
                elif _pos_mark is not None and _liq_dist is not None:
                    st.warning(f"Exchange-reported liquidation distance: {_liq_dist:,.2f} points. Liquidation risk is separate from grid order-margin shortfall.")
                if _entry is not None and _pos_mark is not None and _psize is not None:
                    st.caption("P&L at test price is only calculated when position contract value/unit and exchange position size can be interpreted reliably; this display does not predict liquidation.")
        else:
            st.info("No open position returned by the exchange for this product, or margined position data is unavailable.")
            if _calc_liq_error:
                st.caption(f"Position endpoint detail: {_calc_liq_error}")

        # Independent custom-balance / distance calculator; purely hypothetical.
        st.subheader("🧾 CUSTOM BALANCE & PRICE-DISTANCE CALCULATOR (HYPOTHETICAL)")
        st.caption("Extra balance entered here is hypothetical only. It is not deposited into Delta and does not change order execution.")
        _custom_cols = st.columns(2)
        with _custom_cols[0]:
            _custom_extra_balance = st.number_input(
                "Hypothetical additional balance (USDT)",
                min_value=0.0, value=0.0, step=100.0, format="%.4f",
                key=f"calc_extra_balance_{str(account_id).lower().replace(' ', '_')}",
            )
        with _custom_cols[1]:
            _default_test_price = (
                _calc_anchor + (3000 if _calc_dir == "BUY" else -3000)
                if _calc_anchor is not None else (_calc_mark or 0.0)
            )
            _custom_test_price = st.number_input(
                "Price to test (not CMP/anchor)",
                min_value=0.0, value=max(0.0, float(_default_test_price)),
                step=100.0, format="%.2f",
                key=f"calc_test_price_{str(account_id).lower().replace(' ', '_')}",
            )
        _custom_total_funds = (
            _calc_available + float(_custom_extra_balance)
            if _calc_available is not None else None
        )
        st.write(f"Real available balance: **{f'{_calc_available:,.4f} USDT' if _calc_available is not None else 'UNAVAILABLE'}**")
        st.write(f"Hypothetical extra balance: **{_custom_extra_balance:,.4f} USDT**")
        st.write(f"Total calculation-only funds: **{f'{_custom_total_funds:,.4f} USDT' if _custom_total_funds is not None else 'UNAVAILABLE (real balance missing)'}**")
        if _calc_anchor is None or _calc_qty_btc is None or _calc_leverage is None or _calc_leverage <= 0 or _custom_total_funds is None:
            st.warning("Custom calculation needs confirmed ATR Anchor, actual quantity, real available balance and actual exchange leverage. Missing values are not guessed.")
        else:
            _custom_steps = int(max(0, round(abs(_custom_test_price - _calc_anchor) / _calc_step)))
            _custom_direction_ok = (_custom_test_price >= _calc_anchor if _calc_dir == "BUY" else _custom_test_price <= _calc_anchor)
            if not _custom_direction_ok:
                st.warning("Test price is on the opposite side of the current grid direction. Distance is still shown, but level support is not interpreted as grid capacity.")
            _custom_nlevels = _custom_steps + 1
            _custom_rows = []
            _custom_required = 0.0
            _custom_first_short = None
            for _n in range(1, _custom_nlevels + 1):
                _p = _calc_anchor + ((_n - 1) * _calc_step if _calc_dir == "BUY" else -(_n - 1) * _calc_step)
                if _p <= 0:
                    break
                _m = (_calc_qty_btc * _p) / _calc_leverage
                _custom_required += _m
                if _custom_first_short is None and _custom_required > _custom_total_funds:
                    _custom_first_short = _p
                _custom_rows.append({"Level": f"L{_n}", "Price": _p, "Qty BTC": _calc_qty_btc, "Notional USDT (est.)": _calc_qty_btc * _p, "Cumulative Margin USDT (est.)": _custom_required})
            _custom_shortfall = max(0.0, _custom_required - _custom_total_funds)
            _custom_remaining = max(0.0, _custom_total_funds - _custom_required)
            _custom_summary = st.columns(4)
            _custom_summary[0].metric("Distance from ATR Anchor", f"{abs(_custom_test_price - _calc_anchor):,.2f} points")
            _custom_summary[1].metric("Tested level count", f"{_custom_nlevels:,}")
            _custom_summary[2].metric("Estimated required margin", f"{_custom_required:,.4f} USDT")
            _custom_summary[3].metric("Additional balance required", f"{_custom_shortfall:,.4f} USDT")
            if _custom_first_short is not None:
                st.error(f"Insufficient hypothetical funds. First shortfall at level price {_custom_first_short:,.2f}; shortage {_custom_shortfall:,.4f} USDT.")
            else:
                st.success(f"Estimated funds cover the tested range. Remaining hypothetical funds: {_custom_remaining:,.4f} USDT.")
            st.dataframe(pd.DataFrame(_custom_rows), use_container_width=True, hide_index=True)
            if _calc_mark is not None:
                _test_pnl_rows = []
                for _pos in _calc_positions:
                    _psize = _calc_num(_pos.get("size"))
                    _entry = _calc_num(_pos.get("entry_price", _pos.get("average_entry_price")))
                    if _psize is not None and _entry is not None and _calc_contract_value is not None:
                        # Display-only linear approximation; only report if product metadata exists.
                        _test_pnl = (_custom_test_price - _entry) * _psize * _calc_contract_value
                        _test_pnl_rows.append({
                            "Position": "LONG" if _psize > 0 else "SHORT",
                            "Test Price": _custom_test_price,
                            "Estimated P&L at test price (USDT)": _test_pnl,
                            "Note": "Approximation; exchange-reported liquidation is authoritative",
                        })
                if _test_pnl_rows:
                    st.dataframe(pd.DataFrame(_test_pnl_rows), use_container_width=True, hide_index=True)
            st.caption("The custom-balance margin uses notional ÷ exchange-reported order leverage as an estimate. It is not an official margin quote and excludes risk-tier changes, fees/funding and other exchange adjustments.")
        # ============================================================
        # ADD-ON: DEMO ORDER EXPOSURE & LIQUIDATION SAFETY SESSION
        # Read-only: uses exchange-returned orders/positions; never trades.
        # ============================================================
        st.divider()
        st.header(f"🧪 {account_id} — DEMO ORDER EXPOSURE & LIQUIDATION SAFETY")
        st.caption(
            f"This session reads the {TRADING_MODE} account's current open orders and positions. "
            "Extra balance is hypothetical only. It does not deposit funds or place/cancel orders."
        )

        _safe_open_orders = []
        _safe_closed_orders = []
        _safe_open_ok = False
        _safe_closed_ok = False
        _safe_orders_error = None
        try:
            _safe_open_response = api.open_orders()
            _safe_open_orders = _grid_result(_safe_open_response)
            _safe_open_ok = isinstance(_safe_open_response, dict) and _safe_open_response.get("success") is True
            if not isinstance(_safe_open_orders, list):
                _safe_open_orders = []
            _safe_closed_response = api.closed_orders()
            _safe_closed_orders = _grid_result(_safe_closed_response)
            _safe_closed_ok = isinstance(_safe_closed_response, dict) and _safe_closed_response.get("success") is True
            if not isinstance(_safe_closed_orders, list):
                _safe_closed_orders = []
        except Exception as _safe_orders_exc:
            _safe_orders_error = str(_safe_orders_exc)

        def _safe_first_num(_obj, _keys):
            for _key in _keys:
                if isinstance(_obj, dict) and _obj.get(_key) is not None:
                    try:
                        _value = float(_obj.get(_key))
                        if pd.notna(_value):
                            return _value
                    except (TypeError, ValueError):
                        pass
            return None

        def _safe_level_for_price(_price):
            if _price is None or _calc_anchor is None or _calc_step <= 0:
                return "N/A"
            _offset = (_price - _calc_anchor) if _calc_dir == "BUY" else (_calc_anchor - _price)
            if _offset < -(_calc_step / 2):
                return "Outside grid"
            return f"L{max(1, int(round(max(0.0, _offset) / _calc_step)) + 1)}"

        _safe_order_rows = []
        _safe_pending_margin_est = 0.0
        _safe_pending_margin_known = True
        _safe_pending_contracts = 0
        for _safe_order in _safe_open_orders:
            if not isinstance(_safe_order, dict):
                continue
            _safe_price = _safe_first_num(_safe_order, ("limit_price", "stop_price", "trigger_price", "price"))
            _safe_size = _safe_first_num(_safe_order, ("size",))
            _safe_unfilled = _safe_first_num(_safe_order, ("unfilled_size", "remaining_size"))
            _safe_qty = _safe_unfilled if _safe_unfilled is not None else _safe_size
            _safe_contract_value_for_order = _calc_contract_value
            _safe_notional = (
                abs(_safe_qty) * _safe_contract_value_for_order * _safe_price
                if _safe_qty is not None and _safe_price is not None and _safe_contract_value_for_order is not None
                else None
            )
            _safe_order_margin = (
                _safe_notional / _calc_leverage
                if _safe_notional is not None and _calc_leverage is not None and _calc_leverage > 0
                else None
            )
            if _safe_order_margin is None:
                _safe_pending_margin_known = False
            else:
                _safe_pending_margin_est += _safe_order_margin
            if _safe_qty is not None:
                _safe_pending_contracts += int(abs(_safe_qty))
            _safe_side = str(_safe_order.get("side", "UNKNOWN")).upper()
            _safe_order_rows.append({
                "Grid Level (nearest)": _safe_level_for_price(_safe_price),
                "Side": _safe_side,
                "Order Type": _safe_order.get("order_type", _safe_order.get("type", "-")),
                "Price": _safe_price,
                "Original Size (contracts)": _safe_size,
                "Remaining (contracts)": _safe_qty,
                "Estimated Margin (not official)": _safe_order_margin,
                "Exchange Status": _safe_order.get("state", _safe_order.get("status", "OPEN")),
                "Exchange Order ID": _safe_order.get("id", _safe_order.get("order_id", "-")),
                "Client Order ID": _safe_order.get("client_order_id", "-"),
                "Created At": _safe_order.get("created_at", _safe_order.get("updated_at", "-")),
            })

        _safe_filled_rows = []
        for _safe_order in _safe_closed_orders[-100:]:
            if not isinstance(_safe_order, dict):
                continue
            _safe_closed_size = _safe_first_num(_safe_order, ("size",))
            _safe_filled_size = _safe_first_num(_safe_order, ("filled_size", "filled_quantity"))
            _safe_unfilled_size = _safe_first_num(_safe_order, ("unfilled_size", "remaining_size"))
            _safe_state = str(_safe_order.get("state", _safe_order.get("status", "CLOSED"))).upper()
            _safe_filled_rows.append({
                "Side": str(_safe_order.get("side", "UNKNOWN")).upper(),
                "Order Type": _safe_order.get("order_type", _safe_order.get("type", "-")),
                "Price": _safe_first_num(_safe_order, ("average_fill_price", "limit_price", "price")),
                "Size (contracts)": _safe_closed_size,
                "Filled Size (API field)": _safe_filled_size,
                "Unfilled Size (API field)": _safe_unfilled_size,
                "Exchange Status": _safe_state,
                "Exchange Order ID": _safe_order.get("id", _safe_order.get("order_id", "-")),
                "Client Order ID": _safe_order.get("client_order_id", "-"),
                "Created At": _safe_order.get("created_at", "-"),
                "Updated At": _safe_order.get("updated_at", _safe_order.get("done_at", "-")),
            })

        _safe_summary_cols = st.columns(4)
        _safe_summary_cols[0].metric("LIVE PENDING ORDERS", f"{len(_safe_order_rows)}" if _safe_open_ok else "API UNAVAILABLE")
        _safe_summary_cols[1].metric("PENDING CONTRACTS", f"{_safe_pending_contracts:,}" if _safe_open_ok else "API UNAVAILABLE")
        _safe_summary_cols[2].metric("EST. PENDING MARGIN", f"{_safe_pending_margin_est:,.4f}" if _safe_open_ok and _safe_pending_margin_known else "UNAVAILABLE")
        _safe_summary_cols[3].metric("RECENT CLOSED ORDERS", f"{len(_safe_filled_rows)}" if _safe_closed_ok else "API UNAVAILABLE")
        if not _safe_open_ok or not _safe_closed_ok:
            st.warning("Exchange order history could not be fully read. Do not treat missing orders as zero exposure.")
            if _safe_orders_error:
                st.caption(f"API detail: {_safe_orders_error}")

        st.subheader("📌 REAL EXCHANGE PENDING ORDERS — CURRENT SNAPSHOT")
        if _safe_order_rows:
            st.dataframe(pd.DataFrame(_safe_order_rows), use_container_width=True, hide_index=True)
        elif _safe_open_ok:
            st.info("The exchange returned no currently open orders for this product/account.")

        st.subheader("🧾 RECENT CLOSED ORDER RECORD — FILLS / CANCELS / OTHER CLOSED STATES")
        st.caption("These are recent exchange-returned closed orders; a closed order is not automatically assumed to be fully filled. Check the exchange status and fill fields.")
        if _safe_filled_rows:
            st.dataframe(pd.DataFrame(_safe_filled_rows), use_container_width=True, hide_index=True)
        elif _safe_closed_ok:
            st.info("No closed-order records were returned in this response.")

        st.subheader("📉 LIVE POSITION SAFETY — OFFICIAL EXCHANGE LIQUIDATION")
        if _calc_positions:
            _safe_liq_found = False
            for _safe_pos_idx, _safe_pos in enumerate(_calc_positions, 1):
                _safe_psize = _safe_first_num(_safe_pos, ("size",))
                _safe_pentry = _safe_first_num(_safe_pos, ("entry_price", "average_entry_price"))
                _safe_pmark = _safe_first_num(_safe_pos, ("mark_price",)) or _calc_mark
                _safe_pliq = _safe_first_num(_safe_pos, ("liquidation_price",))
                _safe_pmargin = _safe_first_num(_safe_pos, ("margin", "position_margin"))
                _safe_pupnl = _safe_first_num(_safe_pos, ("unrealized_pnl", "unrealised_pnl"))
                if _safe_pliq is not None:
                    _safe_liq_found = True
                _safe_pos_cols = st.columns(5)
                _safe_pos_cols[0].metric(f"Position {_safe_pos_idx}", f"{'LONG' if (_safe_psize or 0) > 0 else 'SHORT'} / {abs(_safe_psize or 0):,.0f} contracts")
                _safe_pos_cols[1].metric("Entry", f"{_safe_pentry:,.2f}" if _safe_pentry is not None else "UNAVAILABLE")
                _safe_pos_cols[2].metric("Mark", f"{_safe_pmark:,.2f}" if _safe_pmark is not None else "UNAVAILABLE")
                _safe_pos_cols[3].metric("Exchange Liquidation", f"{_safe_pliq:,.2f}" if _safe_pliq is not None else "UNAVAILABLE")
                _safe_pos_cols[4].metric("Position Margin", f"{_safe_pmargin:,.4f}" if _safe_pmargin is not None else "UNAVAILABLE")
                st.write(f"Unrealized P&L from exchange: **{f'{_safe_pupnl:,.4f}' if _safe_pupnl is not None else 'UNAVAILABLE'}**")
                if _safe_pliq is not None and _safe_pmark is not None:
                    _safe_liq_dist = abs(_safe_pmark - _safe_pliq)
                    _safe_liq_pct = (_safe_liq_dist / _safe_pmark * 100.0) if _safe_pmark > 0 else None
                    st.warning(f"Exchange-reported liquidation distance: {_safe_liq_dist:,.2f} points" + (f" ({_safe_liq_pct:.2f}% of mark)" if _safe_liq_pct is not None else "") + ". This is the exchange's current value and can change with fills, fees, funding, margin and mark price.")
                else:
                    st.warning("No official liquidation price was returned for this position. A synthetic liquidation price will not be invented.")
        else:
            st.info("No live position was returned for this product, or the positions endpoint did not return a usable position.")

        st.subheader("➕ ADD FUNDS SCENARIO — ESTIMATED EXTRA GRID CAPACITY")
        st.caption("Enter a hypothetical top-up to estimate how many additional same-size grid orders the current available balance plus that top-up might support. This is not a liquidation-price prediction and does not move money.")
        _safe_extra_funds = st.number_input(
            "Hypothetical extra funds (account currency; estimate only)",
            min_value=0.0,
            value=0.0,
            step=100.0,
            format="%.4f",
            key=f"safe_extra_funds_{str(account_id).lower().replace(' ', '_')}",
        )
        _safe_test_price = st.number_input(
            "Stress-test mark price (hypothetical)",
            min_value=0.0,
            value=float(_calc_mark or _calc_anchor or 0.0),
            step=100.0,
            format="%.2f",
            key=f"safe_test_price_{str(account_id).lower().replace(' ', '_')}",
        )
        _safe_total_available = (_calc_available + _safe_extra_funds) if _calc_available is not None else None
        _safe_one_order_margin = None
        if _calc_qty_btc is not None and _calc_qty_btc > 0 and _safe_test_price > 0 and _calc_leverage is not None and _calc_leverage > 0:
            _safe_one_order_margin = (_calc_qty_btc * _safe_test_price) / _calc_leverage
        _safe_capacity = None
        _safe_leftover = None
        if _safe_total_available is not None and _safe_one_order_margin is not None and _safe_one_order_margin > 0:
            _safe_capacity = int(max(0, _safe_total_available // _safe_one_order_margin))
            _safe_leftover = _safe_total_available - (_safe_capacity * _safe_one_order_margin)
        _safe_scenario_cols = st.columns(4)
        _safe_scenario_cols[0].metric("Real Available Balance", f"{_calc_available:,.4f}" if _calc_available is not None else "UNAVAILABLE")
        _safe_scenario_cols[1].metric("Hypothetical Top-Up", f"{_safe_extra_funds:,.4f}")
        _safe_scenario_cols[2].metric("Combined Calculation Funds", f"{_safe_total_available:,.4f}" if _safe_total_available is not None else "UNAVAILABLE")
        _safe_scenario_cols[3].metric("Estimated Same-Size Orders", f"{_safe_capacity:,}" if _safe_capacity is not None else "UNAVAILABLE")
        if _safe_one_order_margin is not None:
            st.write(f"Estimated margin per new same-size order at the test price: **{_safe_one_order_margin:,.4f}** (not an official exchange quote).")
            st.write(f"Estimated remainder after whole orders: **{_safe_leftover:,.4f}**" if _safe_leftover is not None else "")
            if _safe_capacity is not None and _calc_anchor is not None and _calc_dir in {"BUY", "SELL"}:
                _safe_new_level_rows = []
                for _safe_i in range(1, min(_safe_capacity, 250) + 1):
                    _safe_lp = _calc_anchor + (_safe_i * _calc_step if _calc_dir == "BUY" else -_safe_i * _calc_step)
                    if _safe_lp <= 0:
                        break
                    _safe_new_level_rows.append({
                        "Additional Level": f"L{25 + _safe_i}",
                        "Grid Price": _safe_lp,
                        "Qty BTC (configured)": _calc_qty_btc,
                        "Estimated Margin": (_calc_qty_btc * _safe_lp / _calc_leverage) if _calc_qty_btc is not None and _calc_leverage else None,
                        "Note": "Hypothetical capacity only — not an active bot order",
                    })
                if _safe_new_level_rows:
                    st.dataframe(pd.DataFrame(_safe_new_level_rows), use_container_width=True, hide_index=True)
            st.caption("Important: this rough estimate does not calculate a new liquidation price after adding funds. Delta's official liquidation value is only shown when returned by the exchange. Available balance may already reflect reserved margin, and contract/risk-tier/fees/funding rules can change the real result. Confirm in Delta before changing exposure.")
        else:
            st.warning("Capacity estimate unavailable because real available balance, configured quantity, positive test price, or API-reported leverage is missing.")

    except Exception as _calc_display_exc:
        st.error(f"Calculator display error (trading logic not modified): {_calc_display_exc}")


# ============================================================
# RUN OWNER ACCOUNT ONLY ON EVERY REFRESH
_owner_key, _owner_secret = _account_credentials("OWNER ACCOUNT")

if _owner_key and _owner_secret:
    _run_account_engine(
        "OWNER ACCOUNT",
        _account_client("OWNER ACCOUNT"),
        OWNER_GRID_QTY,
        True,
    )
else:
    st.error(f"❌ {'DEMO_OWNER_API_KEY / DEMO_OWNER_API_SECRET (or legacy OWNER_API_KEY / OWNER_API_SECRET)' if TRADING_MODE == 'DEMO' else 'REAL_OWNER_API_KEY / REAL_OWNER_API_SECRET'} credentials नहीं मिले।")
# END — OWNER ACCOUNT ONLY / ORIGINAL GRID LOGIC PRESERVED
# ============================================================

time.sleep(max(3, int(REFRESH_SECONDS)))
st.rerun()
