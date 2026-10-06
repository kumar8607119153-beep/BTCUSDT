
"""
SANJAY RANA - TWO ACCOUNT DELTA SUPERTrend GRID BOT
=====================================================

Core routing requested:
    SuperTrend BUY  -> Owner BUY 0.002 BTC, Second SELL 0.001 BTC
    SuperTrend SELL -> Owner SELL 0.002 BTC, Second BUY 0.001 BTC

Delta BTCUSD contract_value is 0.001 BTC, so:
    0.001 BTC = 1 contract
    0.002 BTC = 2 contracts

IMPORTANT:
- LIVE TRADING is OFF by default.
- Put API keys in Streamlit Secrets, NOT in this file.
- Test on Delta Demo/Testnet before enabling live trading.
- The bot uses completed candles for SuperTrend signals.
- A signal is acted on once per completed candle to avoid duplicate orders.
"""

import time
import hmac
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, List

import requests
import pandas as pd
import streamlit as st


# ============================================================
# CONFIG
# ============================================================

DEFAULT_BASE_URL = "https://api.india.delta.exchange"
DEFAULT_SYMBOL = "BTCUSD"
DEFAULT_RESOLUTION = "1h"

BASE_QTY_BTC = Decimal("0.001")
OWNER_MULTIPLIER = Decimal("2")
SECOND_MULTIPLIER = Decimal("1")

DEFAULT_ATR_PERIOD = 10
DEFAULT_ST_FACTOR = Decimal("3.0")
DEFAULT_GRID_STEP_PCT = Decimal("0.20")
DEFAULT_TARGET_PCT = Decimal("0.20")

# Per-account grid controls (editable from the Control Panel).
DEFAULT_OWNER_LEVELS = 15
DEFAULT_SECOND_LEVELS = 15
DEFAULT_OWNER_GRID_GAP = Decimal("300")
DEFAULT_SECOND_GRID_GAP = Decimal("300")
DEFAULT_OWNER_QTY_BTC = Decimal("0.002")
DEFAULT_SECOND_QTY_BTC = Decimal("0.001")

REQUEST_TIMEOUT = 12
LOOP_SECONDS = 15

# Delta BTCUSD currently uses contract_value = 0.001 BTC.
# The program also verifies this from /v2/products/BTCUSD.
EXPECTED_CONTRACT_VALUE_BTC = Decimal("0.001")


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="Sanjay Rana - Two Account Grid Bot",
    page_icon="₿",
    layout="wide",
)

st.title("₿ Sanjay Rana — Two Account SuperTrend Grid Bot")
st.caption(
    "One dashboard • One bot • Two Delta accounts • "
    "SuperTrend BUY/SELL routing"
)


# ============================================================
# SECRETS / SETTINGS
# ============================================================

def secret_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
        return str(value) if value is not None else default
    except Exception:
        return default


def get_config() -> Dict[str, str]:
    return {
        "base_url": secret_value("DELTA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "owner_key": secret_value("OWNER_DELTA_API_KEY"),
        "owner_secret": secret_value("OWNER_DELTA_API_SECRET"),
        "second_key": secret_value("SECOND_DELTA_API_KEY"),
        "second_secret": secret_value("SECOND_DELTA_API_SECRET"),
        "discord_webhook": secret_value("DISCORD_WEBHOOK_URL"),
    }


CFG = get_config()


# ============================================================
# SESSION STATE
# ============================================================

if "bot_running" not in st.session_state:
    st.session_state.bot_running = False

if "last_signal_candle" not in st.session_state:
    st.session_state.last_signal_candle = None

if "last_signal" not in st.session_state:
    st.session_state.last_signal = "WAIT"

if "event_log" not in st.session_state:
    st.session_state.event_log = []

if "grid_records" not in st.session_state:
    st.session_state.grid_records = []

if "last_candle_ts" not in st.session_state:
    st.session_state.last_candle_ts = None

if "quantity_equal_stop" not in st.session_state:
    st.session_state.quantity_equal_stop = False

if "quantity_equal_message" not in st.session_state:
    st.session_state.quantity_equal_message = ""

# Rule: equal quantities => stop NEW orders; quantity changes and becomes unequal => resume.


# ============================================================
# TWO-ACCOUNT CONTROL PANEL
# Each account has independent Grid Levels, Grid Gap and Quantity.
# ============================================================

st.subheader("⚙️ Two-Account Grid Control Panel")
st.caption("Account 1 और Account 2 की Grid settings अलग-अलग रखी जा सकती हैं।")

cp1, cp2 = st.columns(2)

with cp1:
    st.markdown("### Account 1 — OWNER")
    owner_levels = st.number_input("Grid Levels — Account 1", min_value=1, max_value=200, value=DEFAULT_OWNER_LEVELS, step=1, key="owner_grid_levels")
    owner_gap = st.number_input("Grid Gap (points) — Account 1", min_value=1.0, max_value=100000.0, value=float(DEFAULT_OWNER_GRID_GAP), step=50.0, key="owner_grid_gap")
    owner_qty = st.number_input("Grid Quantity (BTC) — Account 1", min_value=0.001, max_value=100.0, value=float(DEFAULT_OWNER_QTY_BTC), step=0.001, format="%.3f", key="owner_grid_qty")

with cp2:
    st.markdown("### Account 2 — SECOND")
    second_levels = st.number_input("Grid Levels — Account 2", min_value=1, max_value=200, value=DEFAULT_SECOND_LEVELS, step=1, key="second_grid_levels")
    second_gap = st.number_input("Grid Gap (points) — Account 2", min_value=1.0, max_value=100000.0, value=float(DEFAULT_SECOND_GRID_GAP), step=50.0, key="second_grid_gap")
    second_qty = st.number_input("Grid Quantity (BTC) — Account 2", min_value=0.001, max_value=100.0, value=float(DEFAULT_SECOND_QTY_BTC), step=0.001, format="%.3f", key="second_grid_qty")

ACCOUNT_GRID_SETTINGS = {
    "OWNER": {"levels": int(owner_levels), "gap": Decimal(str(owner_gap)), "qty_btc": Decimal(str(owner_qty))},
    "SECOND": {"levels": int(second_levels), "gap": Decimal(str(second_gap)), "qty_btc": Decimal(str(second_qty))},
}

settings_df = pd.DataFrame([
    {"Account": "OWNER", "Grid Levels": int(owner_levels), "Grid Gap": f"{Decimal(str(owner_gap)):,.2f}", "Quantity (BTC)": f"{Decimal(str(owner_qty)):.3f}"},
    {"Account": "SECOND", "Grid Levels": int(second_levels), "Grid Gap": f"{Decimal(str(second_gap)):,.2f}", "Quantity (BTC)": f"{Decimal(str(second_qty)):.3f}"},
])
st.dataframe(settings_df, use_container_width=True, hide_index=True)


# ============================================================
# HELPERS
# ============================================================

def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log_event(message: str, level: str = "INFO") -> None:
    row = {
        "time": now_text(),
        "level": level,
        "message": message,
    }
    st.session_state.event_log.insert(0, row)
    st.session_state.event_log = st.session_state.event_log[:300]


def send_discord(message: str) -> None:
    url = CFG.get("discord_webhook", "")
    if not url:
        return
    try:
        requests.post(
            url,
            json={"content": message[:1900]},
            timeout=8,
        )
    except Exception:
        pass


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def round_price(price: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        return price
    units = (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return units * tick


def btc_to_contracts(btc_qty: Decimal, contract_value: Decimal) -> int:
    if contract_value <= 0:
        raise ValueError("Invalid Delta contract_value")
    contracts = btc_qty / contract_value
    if contracts != contracts.to_integral_value():
        raise ValueError(
            f"{btc_qty} BTC cannot be represented exactly by "
            f"contract value {contract_value} BTC."
        )
    return int(contracts)


# ============================================================
# DELTA REST CLIENT
# ============================================================

class DeltaClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    def _signature(
        self,
        method: str,
        path: str,
        timestamp: str,
        query_string: str = "",
        body: str = "",
    ) -> str:
        payload = (
            method.upper()
            + timestamp
            + path
            + query_string
            + body
        )
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> Dict[str, Any]:

        params = params or {}
        body = body or {}

        query_string = ""
        if params:
            # requests encodes the actual query. Keep this deterministic.
            from urllib.parse import urlencode
            query_string = urlencode(sorted(params.items()), doseq=True)

        body_text = json.dumps(
            body,
            separators=(",", ":"),
            ensure_ascii=False,
        ) if body else ""

        headers = {
            "Accept": "application/json",
            "User-Agent": "Sanjay-Rana-Two-Account-Bot/1.0",
        }

        if body:
            headers["Content-Type"] = "application/json"

        if auth:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("API credentials are missing.")

            # Delta requires a fresh timestamp for each signed request.
            timestamp = str(int(time.time()))
            headers["api-key"] = self.api_key
            headers["timestamp"] = timestamp
            headers["signature"] = self._signature(
                method,
                path,
                timestamp,
                query_string,
                body_text,
            )

        url = self.base_url + path

        response = requests.request(
            method=method.upper(),
            url=url,
            params=params,
            data=body_text if body else None,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "success": False,
                "error": {
                    "code": "invalid_json",
                    "message": response.text[:500],
                },
            }

        if response.status_code >= 400 or data.get("success") is False:
            raise RuntimeError(
                f"Delta API {response.status_code}: {data}"
            )

        return data

    def public_get(self, path: str, params: Optional[Dict[str, Any]] = None):
        return self._request("GET", path, params=params, auth=False)

    def private_get(self, path: str, params: Optional[Dict[str, Any]] = None):
        return self._request("GET", path, params=params, auth=True)

    def private_post(
        self,
        path: str,
        body: Dict[str, Any],
    ):
        return self._request("POST", path, body=body, auth=True)


# ============================================================
# MARKET DATA
# ============================================================

public_client = DeltaClient(CFG["base_url"])


@st.cache_data(ttl=10, show_spinner=False)
def get_product(symbol: str) -> Dict[str, Any]:
    result = public_client.public_get(f"/v2/products/{symbol}")
    return result.get("result", {})


@st.cache_data(ttl=10, show_spinner=False)
def get_candles(symbol: str, resolution: str, limit: int = 250) -> pd.DataFrame:
    # Delta candle endpoint uses unix seconds.
    end_ts = int(time.time())
    seconds_map = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "6h": 21600,
        "12h": 43200,
        "1d": 86400,
    }
    step = seconds_map.get(resolution, 3600)
    start_ts = end_ts - (limit + 20) * step

    data = public_client.public_get(
        "/v2/history/candles",
        params={
            "resolution": resolution,
            "symbol": symbol,
            "start": start_ts,
            "end": end_ts,
        },
    )

    rows = data.get("result", [])
    if not rows:
        raise RuntimeError("No candle data returned by Delta.")

    normalized = []
    for r in rows:
        # Current Delta response normally uses timestamp/open/high/low/close,
        # but this parser also accepts compact keys when present.
        ts = r.get("time", r.get("timestamp", r.get("t")))
        o = r.get("open", r.get("o"))
        h = r.get("high", r.get("h"))
        l = r.get("low", r.get("l"))
        c = r.get("close", r.get("c"))
        v = r.get("volume", r.get("v", 0))

        if ts is None or any(x is None for x in (o, h, l, c)):
            continue

        ts = float(ts)
        if ts > 10_000_000_000:
            ts /= 1_000_000
        elif ts > 10_000_000_000_000:
            ts /= 1_000_000_000

        normalized.append({
            "timestamp": int(ts),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
        })

    df = pd.DataFrame(normalized)
    if df.empty:
        raise RuntimeError("Delta candle response could not be parsed.")

    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    return df.tail(limit).reset_index(drop=True)


# ============================================================
# SUPERTREND
# ============================================================

def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_supertrend(
    df: pd.DataFrame,
    period: int,
    factor: Decimal,
) -> pd.DataFrame:

    out = df.copy()
    atr = calculate_atr(out, period)
    hl2 = (out["high"] + out["low"]) / 2.0

    upper = hl2 + float(factor) * atr
    lower = hl2 - float(factor) * atr

    final_upper = upper.copy()
    final_lower = lower.copy()

    direction = pd.Series(index=out.index, dtype="int64")
    st_line = pd.Series(index=out.index, dtype="float64")

    direction.iloc[0] = 1
    st_line.iloc[0] = lower.iloc[0]

    for i in range(1, len(out)):
        prev_close = out["close"].iloc[i - 1]

        if (
            upper.iloc[i] < final_upper.iloc[i - 1]
            or prev_close > final_upper.iloc[i - 1]
        ):
            final_upper.iloc[i] = upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        if (
            lower.iloc[i] > final_lower.iloc[i - 1]
            or prev_close < final_lower.iloc[i - 1]
        ):
            final_lower.iloc[i] = lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        prev_st = st_line.iloc[i - 1]

        if prev_st == final_upper.iloc[i - 1]:
            if out["close"].iloc[i] <= final_upper.iloc[i]:
                st_line.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
            else:
                st_line.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
        else:
            if out["close"].iloc[i] >= final_lower.iloc[i]:
                st_line.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
            else:
                st_line.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1

    out["atr"] = atr
    out["st_direction"] = direction
    out["supertrend"] = st_line
    out["signal"] = out["st_direction"].map(
        {1: "BUY", -1: "SELL"}
    )

    return out


# ============================================================
# ACCOUNT HELPERS
# ============================================================

def make_account_clients() -> Dict[str, DeltaClient]:
    return {
        "OWNER": DeltaClient(
            CFG["base_url"],
            CFG["owner_key"],
            CFG["owner_secret"],
        ),
        "SECOND": DeltaClient(
            CFG["base_url"],
            CFG["second_key"],
            CFG["second_secret"],
        ),
    }


def account_has_keys(name: str) -> bool:
    if name == "OWNER":
        return bool(CFG["owner_key"] and CFG["owner_secret"])
    return bool(CFG["second_key"] and CFG["second_secret"])


def get_balance(client: DeltaClient):
    return client.private_get("/v2/wallet/balances")


def get_open_orders(client: DeltaClient, product_id: int):
    return client.private_get(
        "/v2/orders",
        params={"product_id": product_id},
    )


def get_position(client: DeltaClient, product_id: int):
    return client.private_get(
        "/v2/positions",
        params={"product_id": product_id},
    )



# ============================================================
# QUANTITY EQUALITY STOP
# ============================================================

def _extract_position_quantity(position_response: Dict[str, Any]) -> Decimal:
    """
    Read the Delta position quantity defensively.
    Returns absolute BTC quantity where possible.

    Delta responses can contain either a single position object or
    a list under result. We inspect common quantity/size fields.
    """
    result = position_response.get("result", position_response)

    candidates = []
    if isinstance(result, list):
        candidates = result
    elif isinstance(result, dict):
        # Some responses wrap positions inside a list.
        for key in ("positions", "data"):
            if isinstance(result.get(key), list):
                candidates = result[key]
                break
        if not candidates:
            candidates = [result]

    total_contracts = Decimal("0")

    for item in candidates:
        if not isinstance(item, dict):
            continue

        for key in ("size", "quantity", "position_size", "contracts"):
            value = item.get(key)
            if value is not None:
                try:
                    total_contracts += abs(dec(value))
                    break
                except Exception:
                    pass

    # BTCUSD contract value is verified elsewhere as 0.001 BTC.
    return total_contracts * EXPECTED_CONTRACT_VALUE_BTC


def check_quantity_equality(
    clients: Dict[str, DeltaClient],
    product: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Stop condition:
    when absolute current quantity on Owner and Second accounts
    becomes equal, no new orders are routed.
    """
    if not account_has_keys("OWNER") or not account_has_keys("SECOND"):
        return {
            "checked": False,
            "equal": False,
            "owner_qty": None,
            "second_qty": None,
        }

    product_id = int(product["id"])

    try:
        owner_position = get_position(clients["OWNER"], product_id)
        second_position = get_position(clients["SECOND"], product_id)

        owner_qty = _extract_position_quantity(owner_position)
        second_qty = _extract_position_quantity(second_position)

        equal = owner_qty == second_qty

        return {
            "checked": True,
            "equal": equal,
            "owner_qty": owner_qty,
            "second_qty": second_qty,
        }

    except Exception as e:
        log_event(f"Quantity equality check failed: {e}", "ERROR")
        return {
            "checked": False,
            "equal": False,
            "owner_qty": None,
            "second_qty": None,
            "error": str(e),
        }


# ============================================================
# ORDER ROUTER
# ============================================================

def routing_for_signal(signal: str):
    """
    FINAL ACCOUNT ROUTING RULE

    OWNER ACCOUNT:
        Always trades in the SAME direction as SuperTrend.

    SECOND / MEMBER ACCOUNT:
        Always trades in the OPPOSITE direction to SuperTrend.

    SuperTrend BUY:
        OWNER  -> BUY  0.002 BTC (2 contracts)
        SECOND -> SELL 0.001 BTC (1 contract)

    SuperTrend SELL:
        OWNER  -> SELL 0.002 BTC (2 contracts)
        SECOND -> BUY  0.001 BTC (1 contract)
    """
    signal = str(signal).upper().strip()

    if signal == "BUY":
        return {
            "OWNER": ("buy", ACCOUNT_GRID_SETTINGS["OWNER"]["qty_btc"]),
            "SECOND": ("sell", ACCOUNT_GRID_SETTINGS["SECOND"]["qty_btc"]),
        }

    if signal == "SELL":
        return {
            "OWNER": ("sell", ACCOUNT_GRID_SETTINGS["OWNER"]["qty_btc"]),
            "SECOND": ("buy", ACCOUNT_GRID_SETTINGS["SECOND"]["qty_btc"]),
        }

    return {}


def submit_grid_order(
    client: DeltaClient,
    account_name: str,
    product: Dict[str, Any],
    side: str,
    btc_qty: Decimal,
    price: Decimal,
    dry_run: bool,
    grid_id: str,
) -> Dict[str, Any]:

    product_id = int(product["id"])
    contract_value = dec(product.get("contract_value", "0.001"))
    tick = dec(product.get("tick_size", "0.5"))

    if contract_value != EXPECTED_CONTRACT_VALUE_BTC:
        raise RuntimeError(
            f"{account_name}: Delta reports contract_value="
            f"{contract_value}, expected {EXPECTED_CONTRACT_VALUE_BTC}. "
            "Order size conversion was stopped for safety."
        )

    contracts = btc_to_contracts(btc_qty, contract_value)
    order_price = round_price(price, tick)

    client_order_id = (
        f"SR{int(time.time())}{account_name[:1]}{side[:1]}"
    )[:32]

    payload = {
        "product_id": product_id,
        "product_symbol": product["symbol"],
        "limit_price": str(order_price),
        "size": contracts,
        "side": side,
        "order_type": "limit_order",
        "time_in_force": "gtc",
        "post_only": False,
        "client_order_id": client_order_id,
    }

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "result": {
                "id": f"DRY-{client_order_id}",
                **payload,
            },
        }

    return client.private_post("/v2/orders", payload)


def route_signal(
    signal: str,
    entry_price: Decimal,
    product: Dict[str, Any],
    clients: Dict[str, DeltaClient],
    dry_run: bool,
    grid_id: str,
) -> List[Dict[str, Any]]:

    routing = routing_for_signal(signal)
    if not routing:
        return []

    results = []

    for account_name, (side, btc_qty) in routing.items():

        if not dry_run and not account_has_keys(account_name):
            raise RuntimeError(
                f"{account_name} API credentials are missing."
            )

        result = submit_grid_order(
            client=clients[account_name],
            account_name=account_name,
            product=product,
            side=side,
            btc_qty=btc_qty,
            price=entry_price,
            dry_run=dry_run,
            grid_id=grid_id,
        )

        order = result.get("result", result)
        order_id = order.get("id", "N/A")

        target = (
            entry_price * (Decimal("1") + DEFAULT_TARGET_PCT / Decimal("100"))
            if side == "buy"
            else entry_price * (Decimal("1") - DEFAULT_TARGET_PCT / Decimal("100"))
        )

        row = {
            "Grid": grid_id,
            "Account": account_name,
            "Signal": signal,
            "Side": side.upper(),
            "BTC Qty": str(btc_qty),
            "Contracts": order.get("size", ""),
            "Entry": float(entry_price),
            "Target": float(target),
            "Order ID": order_id,
            "Status": "DRY RUN" if dry_run else "SUBMITTED",
            "Time": now_text(),
        }

        st.session_state.grid_records.insert(0, row)
        st.session_state.grid_records = st.session_state.grid_records[:500]
        results.append(row)

    return results


# ============================================================
# SIGNAL ENGINE
# ============================================================

def get_confirmed_signal(
    candles: pd.DataFrame,
    period: int,
    factor: Decimal,
):
    st_df = calculate_supertrend(candles, period, factor)

    # The newest candle can still be forming.
    # We deliberately use the PREVIOUS completed candle.
    if len(st_df) < 3:
        raise RuntimeError("Not enough candles for confirmed signal.")

    confirmed = st_df.iloc[-2]
    previous = st_df.iloc[-3]

    signal = str(confirmed["signal"])
    previous_signal = str(previous["signal"])

    changed = signal != previous_signal

    return st_df, confirmed, previous, signal, changed


# ============================================================
# UI CONTROLS
# ============================================================

with st.sidebar:
    st.header("Bot Settings")

    symbol = st.text_input("Trading Pair", DEFAULT_SYMBOL).strip().upper()

    resolution = st.selectbox(
        "Candle",
        ["5m", "15m", "30m", "1h", "2h", "4h"],
        index=3,
    )

    atr_period = st.number_input(
        "SuperTrend ATR Period",
        min_value=1,
        max_value=100,
        value=DEFAULT_ATR_PERIOD,
        step=1,
    )

    st_factor = st.number_input(
        "SuperTrend Factor",
        min_value=0.1,
        max_value=20.0,
        value=float(DEFAULT_ST_FACTOR),
        step=0.1,
    )

    grid_step_pct = st.number_input(
        "Grid Step %",
        min_value=0.01,
        max_value=10.0,
        value=float(DEFAULT_GRID_STEP_PCT),
        step=0.01,
    )

    target_pct = st.number_input(
        "Target %",
        min_value=0.01,
        max_value=10.0,
        value=float(DEFAULT_TARGET_PCT),
        step=0.01,
    )

    st.divider()

    live_trading = st.checkbox(
        "⚠️ LIVE TRADING",
        value=False,
        help="OFF = simulation only. ON = real Delta orders.",
    )

    auto_refresh = st.checkbox(
        "Auto Refresh",
        value=False,
    )

    st.divider()

    st.subheader("Accounts")
    st.write(
        f"Owner API: {'Connected' if account_has_keys('OWNER') else 'Missing'}"
    )
    st.write(
        f"Member/Second API: {'Connected' if account_has_keys('SECOND') else 'Missing'}"
    )

    st.caption(
        "API keys are read from Streamlit Secrets and are never shown."
    )

    if live_trading:
        st.warning(
            "LIVE TRADING is ON. Orders can be sent to the real accounts."
        )

    if st.button(
        "Run Signal Once",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.bot_running = True
        st.rerun()

    if st.button(
        "Stop Bot",
        use_container_width=True,
    ):
        st.session_state.bot_running = False


# ============================================================
# PRODUCT VALIDATION
# ============================================================

try:
    product = get_product(symbol)
except Exception as e:
    st.error(f"Product error: {e}")
    st.stop()

if not product:
    st.error("Product not found.")
    st.stop()

contract_value = dec(product.get("contract_value", "0"))
tick_size = dec(product.get("tick_size", "0"))

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric("Product", product.get("symbol", symbol))

with c2:
    st.metric("Contract Value", f"{contract_value} BTC")

with c3:
    st.metric("Tick Size", str(tick_size))

with c4:
    st.metric("Mode", "LIVE" if live_trading else "SIMULATION")


# ============================================================
# EXPLICIT ROUTING TABLE
# ============================================================

st.subheader("Signal Router")
st.info("OWNER = SAME DIRECTION • MEMBER/SECOND = OPPOSITE DIRECTION • Equal quantities = NEW ORDERS STOP")

routing_preview = pd.DataFrame([
    {
        "SuperTrend": "BUY",
        "Owner": "BUY 0.002 BTC (2 contracts)",
        "Member/Second": "SELL 0.001 BTC (1 contract)",
    },
    {
        "SuperTrend": "SELL",
        "Owner": "SELL 0.002 BTC (2 contracts)",
        "Member/Second": "BUY 0.001 BTC (1 contract)",
    },
])

st.table(routing_preview)


# ============================================================
# LOAD CANDLES + SIGNAL
# ============================================================

try:
    candles = get_candles(symbol, resolution, 250)

    st_df, confirmed, previous, signal, changed = get_confirmed_signal(
        candles,
        atr_period,
        Decimal(str(st_factor)),
    )

    current_price = Decimal(str(candles.iloc[-1]["close"]))
    confirmed_close = Decimal(str(confirmed["close"]))
    st_price = Decimal(str(confirmed["supertrend"]))

except Exception as e:
    st.error(f"Market/indicator error: {e}")
    st.stop()


# ============================================================
# CURRENT SIGNAL CARDS
# ============================================================

s1, s2, s3, s4 = st.columns(4)

with s1:
    st.metric("Live/Latest Price", f"{current_price:,.2f}")

with s2:
    st.metric(
        "Confirmed Candle Close",
        f"{confirmed_close:,.2f}",
    )

with s3:
    st.metric(
        "SuperTrend",
        f"{st_price:,.2f}",
    )

with s4:
    st.metric(
        "Signal",
        signal,
        delta="REVERSAL" if changed else "Confirmed",
    )


# ============================================================
# CHART
# ============================================================

chart_df = st_df.tail(120).copy()
chart_df["time"] = pd.to_datetime(
    chart_df["timestamp"],
    unit="s",
    utc=True,
)

st.subheader("BTCUSD / SuperTrend")

chart_display = chart_df.set_index("time")[
    ["close", "supertrend"]
].rename(
    columns={
        "close": "BTC Price",
        "supertrend": "SuperTrend",
    }
)

st.line_chart(chart_display, height=380)


# ============================================================
# EXECUTION
# ============================================================

clients_for_check = make_account_clients()
quantity_status = check_quantity_equality(
    clients_for_check,
    product,
)

if quantity_status.get("equal"):
    if not st.session_state.quantity_equal_stop:
        st.session_state.quantity_equal_stop = True
        st.session_state.quantity_equal_message = (
            "Owner और Second account की quantity बराबर हो गई है। "
            "Bot ने नई order routing रोक दी है."
        )
        log_event(
            "QUANTITY EQUALITY STOP: "
            f"Owner={quantity_status['owner_qty']} BTC, "
            f"Second={quantity_status['second_qty']} BTC",
            "STOP",
        )
        send_discord(
            "🛑 QUANTITY EQUALITY STOP\n"
            f"Owner: {quantity_status['owner_qty']} BTC\n"
            f"Second: {quantity_status['second_qty']} BTC\n"
            "New orders are stopped."
        )

    st.warning(
        st.session_state.quantity_equal_message
        + f" Owner={quantity_status['owner_qty']} BTC, "
        f"Second={quantity_status['second_qty']} BTC."
    )

elif quantity_status.get("checked"):
    # IMPORTANT:
    # Once the two accounts' quantities change and are no longer equal,
    # the equality stop is automatically cleared and NEW routing resumes.
    if st.session_state.quantity_equal_stop:
        log_event(
            "QUANTITY CHANGED: Owner and Member quantities are no longer equal; "
            "bot routing RESUMED.",
            "INFO",
        )
        send_discord(
            "▶️ QUANTITY STOP CLEARED\n"
            f"Owner: {quantity_status['owner_qty']} BTC\n"
            f"Member/Second: {quantity_status['second_qty']} BTC\n"
            "New signal orders are enabled again."
        )

    st.session_state.quantity_equal_stop = False
    st.session_state.quantity_equal_message = ""

if changed and not st.session_state.quantity_equal_stop:
    st.info(
        f"Confirmed SuperTrend reversal: {previous['signal']} → {signal} "
        f"at candle close {confirmed_close:,.2f}"
    )

    signal_candle_key = str(int(confirmed["timestamp"]))

    if st.session_state.last_signal_candle != signal_candle_key:
        clients = make_account_clients()

        grid_id = (
            "G" +
            datetime.fromtimestamp(
                int(confirmed["timestamp"]),
                tz=timezone.utc,
            ).strftime("%Y%m%d%H%M")
        )

        try:
            results = route_signal(
                signal=signal,
                entry_price=confirmed_close,
                product=product,
                clients=clients,
                dry_run=not live_trading,
                grid_id=grid_id,
            )

            st.session_state.last_signal_candle = signal_candle_key
            st.session_state.last_signal = signal

            for r in results:
                message = (
                    f"**SuperTrend {signal} | {r['Account']}**\n"
                    f"Side: {r['Side']}\n"
                    f"Qty: {r['BTC Qty']} BTC / {r['Contracts']} contracts\n"
                    f"Entry: {r['Entry']}\n"
                    f"Target: {r['Target']}\n"
                    f"Order ID: {r['Order ID']}\n"
                    f"Mode: {r['Status']}"
                )
                send_discord(message)
                log_event(
                    f"{r['Account']} {r['Side']} "
                    f"{r['BTC Qty']} BTC @ {r['Entry']} "
                    f"Order={r['Order ID']}"
                )

            st.success(
                f"Signal routed successfully: {signal}. "
                f"{'Simulation only.' if not live_trading else 'Orders submitted.'}"
            )

        except Exception as e:
            log_event(f"Signal routing failed: {e}", "ERROR")
            st.error(f"Order routing failed: {e}")

    else:
        st.warning(
            "This confirmed candle was already processed. "
            "Duplicate order was blocked."
        )
else:
    st.caption(
        "No new SuperTrend reversal on the latest completed candle. "
        "No new pair of orders was created."
    )


# ============================================================
# GRID / ORDER HISTORY
# ============================================================

st.subheader("Two-Account Grid / Order History")

if st.session_state.grid_records:
    grid_df = pd.DataFrame(st.session_state.grid_records)
    st.dataframe(
        grid_df,
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No orders/signals processed yet.")


# ============================================================
# ACCOUNT STATUS
# ============================================================

st.subheader("Account Status")

clients = make_account_clients()

account_rows = []

for account_name in ["OWNER", "SECOND"]:
    if not account_has_keys(account_name):
        account_rows.append({
            "Account": account_name,
            "API": "NOT CONFIGURED",
            "Balance": "-",
            "Position": "-",
            "Status": "OFFLINE",
        })
        continue

    try:
        # We intentionally don't display secrets.
        balance_data = get_balance(clients[account_name])
        position_data = get_position(
            clients[account_name],
            int(product["id"]),
        )

        account_rows.append({
            "Account": account_name,
            "API": "CONFIGURED",
            "Balance": "Connected",
            "Position": str(position_data.get("result", "-")),
            "Status": "ONLINE",
        })
    except Exception as e:
        account_rows.append({
            "Account": account_name,
            "API": "CONFIGURED",
            "Balance": "-",
            "Position": "-",
            "Status": f"ERROR: {str(e)[:100]}",
        })

st.dataframe(
    pd.DataFrame(account_rows),
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# EVENT LOG
# ============================================================

st.subheader("Bot Event Log")

if st.session_state.event_log:
    st.dataframe(
        pd.DataFrame(st.session_state.event_log),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("No events yet.")


# ============================================================
# AUTO REFRESH
# ============================================================

if auto_refresh:
    time.sleep(LOOP_SECONDS)
    st.rerun()
