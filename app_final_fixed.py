# -*- coding: utf-8 -*-
"""Single-file XAUT/PAXG paired grid dashboard — DEMO ONLY.

Market data: Delta Exchange India public REST API.
Execution: local simulated fills only. No authenticated/private order API is called.

Agreed strategy encoded here:
- XAUTUSD BUY / LONG: 330 simulated one-lot grid levels.
- PAXGUSD SELL / SHORT: 330 simulated one-lot grid levels.
- Each side has 330 independent levels: 176 in Zone 1 ($5 spacing), 88 in
  Zone 2 ($10), 44 in Zone 3 ($20), and 22 in Zone 4 ($40).
- Each level has its own predefined grid entry and target price.
- Initial entry lines are hidden on charts; active target lines are shown.
- After a level TP, its target line is replaced by its waiting re-entry line.
- Every level is managed independently; the opposite instrument is untouched.
- Separate leg P&L plus combined P&L.

Important: this is a DEMO simulator. It polls the public ticker every 5 seconds;
brief intraperiod price touches can be missed. It does not send real orders.
"""

from __future__ import annotations

import time
import json
import os
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any

import pandas as pd
import streamlit.components.v1 as components
import requests
import streamlit as st

# ----------------------------- App configuration -----------------------------
st.set_page_config(page_title="XAUT + PAXG Grid Demo", page_icon="🟡", layout="wide")

BASE_URL = "https://api.india.delta.exchange"
SYMBOLS = {"XAUT": "XAUTUSD", "PAXG": "PAXGUSD"}
REFRESH_SECONDS = 5
CANDLE_RESOLUTION = "5m"
CANDLE_COUNT = 120
INITIAL_LOTS_PER_SIDE = 330
STRATEGY_VERSION = 4
LEVERAGE_DISPLAY = 100  # Display/planning assumption only; never applied to an order.

# Exact ranges supplied in the conversation.
# Each segment is (low, high, grid_step) for ascending XAUT prices.
XAUT_ZONES = [
    (3520.0, 4399.0, 5.0),
    (4399.0, 5279.0, 10.0),
    (5279.0, 6159.0, 20.0),
    (6159.0, 7040.0, 40.0),
]
# Each segment is (high, low, grid_step) for descending PAXG prices.
PAXG_ZONES = [
    (3520.0, 2640.0, 5.0),
    (2640.0, 1760.0, 10.0),
    (1760.0, 880.0, 20.0),
    (880.0, 0.0, 40.0),
]

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.0rem; padding-bottom: 1.5rem; max-width: 1600px;}
    div[data-testid="stMetric"] {background: rgba(120,120,120,.07); padding: 12px; border-radius: 10px;}
    .small-note {font-size: .82rem; opacity: .78;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🟡 XAUT BUY + PAXG SELL — 330 Lot Target Dashboard")
st.caption("DEMO ONLY · Delta Exchange India public live market data · Simulated orders · No real orders are sent")

# ----------------------------- Public API helpers ----------------------------
@st.cache_data(ttl=5, show_spinner=False)
def get_ticker(symbol: str) -> dict:
    url = f"{BASE_URL}/v2/tickers/{symbol}"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=12)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(str(payload.get("error", payload)))
    result = payload.get("result")
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected ticker response for {symbol}")
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def get_product(symbol: str) -> dict:
    url = f"{BASE_URL}/v2/products/{symbol}"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=12)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(str(payload.get("error", payload)))
    result = payload.get("result") or {}
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected product response for {symbol}")
    return result


def _read_secret(name: str, default: str = "") -> str:
    """Read private API credentials from Streamlit Secrets or environment only."""
    value = default
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = os.getenv(name, default)
    if value in (None, ""):
        value = os.getenv(name, default)
    return str(value or "").strip()


@st.cache_data(ttl=300, show_spinner=False)
def get_server_public_ip() -> str:
    """Return this Streamlit host's outbound public IP, not the user's phone IP."""
    try:
        response = requests.get("https://api.ipify.org", timeout=5)
        response.raise_for_status()
        candidate = response.text.strip()
        import ipaddress
        return str(ipaddress.ip_address(candidate))
    except Exception:
        return ""


def get_owner_wallet_status() -> dict:
    """Read-only authenticated wallet check; this function never places orders."""
    now = time.time()
    cached = st.session_state.get("_owner_wallet_status_cache")
    # Avoid calling the private wallet endpoint on every 5-second chart refresh.
    if isinstance(cached, dict) and now - cached.get("checked_at", 0) < 15:
        return cached

    api_key = _read_secret("REAL_OWNER_API_KEY")
    api_secret = _read_secret("REAL_OWNER_API_SECRET")
    result = {
        "status": "DISCONNECTED" if not api_key or not api_secret else "UNKNOWN",
        "error": "Owner API credentials missing. Configure REAL_OWNER_API_KEY and REAL_OWNER_API_SECRET in Streamlit Secrets.",
        "whitelist": "UNKNOWN",
        "balances": [],
        "last_success": st.session_state.get("owner_last_successful_api_check", "-"),
        "checked_at": now,
    }
    if not api_key or not api_secret:
        st.session_state["_owner_wallet_status_cache"] = result
        return result

    path = "/v2/wallet/balances"
    timestamp = str(int(now))
    message = "GET" + timestamp + path
    signature = hmac.new(
        api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers = {
        "Accept": "application/json",
        "User-Agent": "Sanjay-Rana-Demo-Account-Monitor",
        "api-key": api_key,
        "timestamp": timestamp,
        "signature": signature,
    }
    try:
        response = requests.get(BASE_URL + path, headers=headers, timeout=15)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if response.ok and isinstance(payload, dict) and payload.get("success") is True:
            wallet_payload = payload.get("result")
            if isinstance(wallet_payload, dict):
                wallet_payload = wallet_payload.get("balances", [wallet_payload])
            balances = [row for row in (wallet_payload or []) if isinstance(row, dict)] if isinstance(wallet_payload, list) else []
            result.update({
                "status": "CONNECTED",
                "error": "None on the latest check",
                "whitelist": "ACCEPTED / VERIFIED",
                "balances": balances,
                "last_success": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            })
            st.session_state["owner_last_successful_api_check"] = result["last_success"]
        else:
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(err, dict):
                err_code = str(err.get("code", ""))
                err_message = str(err.get("message", ""))
                error_text = (err_code + ": " + err_message).strip(": ")
            else:
                error_text = str(err or f"HTTP {response.status_code}")
            if not error_text:
                error_text = f"HTTP {response.status_code}: Delta did not confirm private API access"
            lowered = error_text.lower()
            if any(term in lowered for term in ("ip whitelist", "ip_whitelist", "ip restriction", "whitelist", "ip not allowed", "not allowed from this ip")):
                result["whitelist"] = "IP RESTRICTION DETECTED"
            result.update({"status": "DISCONNECTED", "error": error_text})
    except Exception as exc:
        result.update({
            "status": "DISCONNECTED",
            "error": f"Connection check failed ({type(exc).__name__}); details hidden.",
        })

    # Never expose credentials if an exchange error happens to echo request data.
    for secret_value in (api_key, api_secret):
        if secret_value:
            result["error"] = str(result["error"]).replace(secret_value, "[REDACTED]")
    result["checked_at"] = time.time()
    st.session_state["_owner_wallet_status_cache"] = result
    return result


def render_owner_account_status() -> None:
    """Render the legacy owner-account balance, API, and outbound-IP monitor."""
    st.markdown("### 👑 OWNER ACCOUNT / DELTA API STATUS")
    wallet = get_owner_wallet_status()
    public_ip = get_server_public_ip()

    status_col, ip_col, whitelist_col, last_col = st.columns(4)
    with status_col:
        if wallet["status"] == "CONNECTED":
            st.success("🟢 API CONNECTED")
        elif wallet["status"] == "DISCONNECTED":
            st.error("🔴 API DISCONNECTED")
        else:
            st.warning("🟡 API STATUS UNKNOWN")
    with ip_col:
        st.metric("SERVER PUBLIC IP", public_ip or "IP UNAVAILABLE")
    with whitelist_col:
        if wallet["whitelist"] == "ACCEPTED / VERIFIED":
            st.success("🟢 IP WHITELIST VERIFIED")
        elif wallet["whitelist"] == "IP RESTRICTION DETECTED":
            st.error("🔴 IP RESTRICTION")
        else:
            st.warning("🟡 WHITELIST UNKNOWN")
    with last_col:
        st.metric("LAST SUCCESSFUL API CHECK", wallet["last_success"] or "—")

    balances = wallet.get("balances", [])
    shown = False
    for row in balances:
        currency = str(row.get("asset_symbol") or row.get("currency") or row.get("asset") or row.get("symbol") or "").strip()
        total = next((row.get(k) for k in ("balance", "total_balance", "total") if row.get(k) is not None), None)
        available = next((row.get(k) for k in ("available_balance", "available", "available_to_withdraw") if row.get(k) is not None), None)
        reserved = next((row.get(k) for k in ("reserved_balance", "reserved", "locked_balance") if row.get(k) is not None), None)
        if currency and any(value is not None for value in (total, available, reserved)):
            shown = True
            st.markdown(f"**Wallet Asset: {currency}**")
            cols = st.columns(3)
            for col, label, value in zip(cols, ("Total Balance", "Available Balance", "Reserved Balance"), (total, available, reserved)):
                with col:
                    if value is None:
                        st.metric(label, "Not returned by API")
                    else:
                        try:
                            display_value = f"{float(value):,.8f}"
                        except (TypeError, ValueError):
                            display_value = str(value)
                        st.metric(label, display_value)
    if wallet["status"] == "CONNECTED" and not shown:
        st.info("API connected, but the wallet response did not include recognized balance fields.")
    if wallet["status"] != "CONNECTED":
        st.caption("Wallet balance तभी दिखेगा जब authenticated private wallet request सफल होगा।")
    st.caption(f"Last API Error: {wallet['error']}")
    st.caption("यह IP Streamlit hosting server का outbound public IP है; यह आपके मोबाइल/डिवाइस का IP नहीं है।")


@st.cache_data(ttl=60, show_spinner=False)
def get_candles(symbol: str, resolution: str, count: int) -> pd.DataFrame:
    end = int(time.time())
    # 5m bars: request a little more than count; API limit is 2,000 candles.
    seconds_per_bar = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}.get(resolution, 300)
    start = end - seconds_per_bar * count
    params = {"resolution": resolution, "symbol": symbol, "start": str(start), "end": str(end)}
    r = requests.get(f"{BASE_URL}/v2/history/candles", params=params,
                     headers={"Accept": "application/json"}, timeout=15)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(str(payload.get("error", payload)))
    rows = payload.get("result") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ["open", "high", "low", "close", "volume", "time"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    return df.sort_values("time").tail(count).reset_index(drop=True)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def make_target_tranches(zones: List[Tuple[float, float, float]], direction: str) -> List[dict]:
    """Create one independent lot at each configured grid rung."""
    levels: List[dict] = []
    level_no = 0
    for zone_no, (zone_start, zone_end, step) in enumerate(zones, start=1):
        distance = abs(zone_end - zone_start)
        count = (176, 88, 44, 22)[zone_no - 1]
        for rung in range(count):
            level_no += 1
            target = zone_start + rung * step if direction == "long" else zone_start - rung * step
            entry = target - step if direction == "long" else target + step
            levels.append({
                "entry": entry, "target": target, "step": step,
                "zone": zone_no, "level_no": level_no, "lots": 1,
            })
    return levels


# 176 + 88 + 44 + 22 = 330 independent one-lot levels per instrument.
XAUT_LEVELS = make_target_tranches(XAUT_ZONES, "long")
PAXG_LEVELS = make_target_tranches(PAXG_ZONES, "short")


def init_leg_state(key: str, levels: List[dict]) -> dict:
    return {
        "levels": [
            {**x, "status": "NOT_STARTED", "entry_time": None,
             "entry_price": None, "target": None, "cycles": 0,
             "last_exit": None}
            for x in levels
        ],
        "last_price": None,
        "initialized": False,
        "realized_gross": 0.0,
        "realized_fees": 0.0,
        "closed_trades": 0,
        "events": [],
        "key": key,
        "strategy_version": STRATEGY_VERSION,
    }


if ("xaut_state" not in st.session_state
        or st.session_state.xaut_state.get("strategy_version") != STRATEGY_VERSION):
    st.session_state.xaut_state = init_leg_state("XAUT", XAUT_LEVELS)
if ("paxg_state" not in st.session_state
        or st.session_state.paxg_state.get("strategy_version") != STRATEGY_VERSION):
    st.session_state.paxg_state = init_leg_state("PAXG", PAXG_LEVELS)
if "demo_started_at" not in st.session_state:
    st.session_state.demo_started_at = datetime.now(timezone.utc).isoformat()
if "last_api_ok" not in st.session_state:
    st.session_state.last_api_ok = None


def start_initial_position(state: dict, current_price: float, direction: str) -> None:
    """Open all 330 demo lots at the first valid live-price snapshot."""
    if state.get("initialized"):
        return
    now_text = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    for item in state["levels"]:
        # Each one-lot grid level has its own predefined entry and target rung.
        entry = float(item["entry"])
        target = float(item["target"])
        item.update(status="OPEN", entry_time=now_text, entry_price=entry, target=target)
        side = "BUY" if direction == "long" else "SELL"
        add_event(state, f"Initial {side} · L{item['level_no']:03d} · Zone {item['zone']} · 1 lot · TP ${target:,.4f}", entry, "OPEN")
    state["initialized"] = True
    state["last_price"] = current_price


def add_event(state: dict, text: str, price: float, status: str) -> None:
    state["events"].insert(0, {
        "Time (IST)": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": state["key"], "Event": text,
        "Price": price, "Status": status,
    })
    state["events"] = state["events"][:3000]


def process_leg(state: dict, current_price: float, product: dict, direction: str) -> None:
    """Manage each live tranche independently; never alter the opposite leg."""
    if not state.get("initialized"):
        start_initial_position(state, current_price, direction)
        return

    previous = state["last_price"]
    if previous is None or abs(current_price - previous) < 1e-12:
        state["last_price"] = current_price
        return

    contract_value = safe_float(product.get("contract_value"), 0.0)
    maker_fee = safe_float(product.get("maker_commission_rate"), 0.0)
    if contract_value <= 0:
        state["last_price"] = current_price
        return

    now_text = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    for item in state["levels"]:
        entry = safe_float(item.get("entry_price"))
        target = safe_float(item.get("target"))
        lots = int(item.get("lots", 0))
        if lots <= 0:
            continue

        if direction == "long":
            target_hit = item["status"] == "OPEN" and current_price > previous and previous < target <= current_price
            reentry_hit = item["status"] == "WAITING_REENTRY" and current_price < previous and previous > entry >= current_price
            gross = (target - entry) * contract_value * lots
        else:
            target_hit = item["status"] == "OPEN" and current_price < previous and previous > target >= current_price
            reentry_hit = item["status"] == "WAITING_REENTRY" and current_price > previous and previous < entry <= current_price
            gross = (entry - target) * contract_value * lots

        if target_hit:
            fee = (abs(entry) + abs(target)) * contract_value * lots * maker_fee
            state["realized_gross"] += gross
            state["realized_fees"] += fee
            state["closed_trades"] += 1
            item.update(status="WAITING_REENTRY", last_exit=target, entry_time=None, target=None, cycles=item["cycles"] + 1)
            add_event(state, f"{'BUY' if direction == 'long' else 'SELL'} TP hit · L{item['level_no']:03d} · Zone {item['zone']} · {lots} lot · Re-entry waiting at ${entry:,.4f}", target, "PROFIT")
        elif reentry_hit:
            new_target = entry + item["step"] if direction == "long" else entry - item["step"]
            item.update(status="OPEN", entry_time=now_text, entry_price=entry, target=new_target)
            add_event(state, f"{'BUY' if direction == 'long' else 'SELL'} re-entry · L{item['level_no']:03d} · Zone {item['zone']} · {lots} lot · TP ${new_target:,.4f}", entry, "RE-ENTRY")

    state["last_price"] = current_price


def calc_open_pnl(state: dict, current_price: float, product: dict, direction: str) -> Tuple[float, float, int, int]:
    contract_value = safe_float(product.get("contract_value"), 0.0)
    maker_fee = safe_float(product.get("maker_commission_rate"), 0.0)
    gross = 0.0
    est_fees = 0.0
    open_count = 0
    open_lots = 0
    for item in state["levels"]:
        if item["status"] != "OPEN":
            continue
        open_count += 1
        lots = int(item.get("lots", 0))
        open_lots += lots
        entry = safe_float(item["entry_price"])
        units = contract_value * lots
        if direction == "long":
            gross += (current_price - entry) * units
        else:
            gross += (entry - current_price) * units
        est_fees += (abs(entry) + abs(current_price)) * units * maker_fee
    return gross, est_fees, open_count, open_lots


def render_original_style_chart(
    df: pd.DataFrame,
    symbol: str,
    title: str,
    current_price: float,
    levels: List[dict],
    direction: str,
    state: dict,
) -> None:
    """Render the original Lightweight Charts-style display, using DEMO state only."""
    if df.empty:
        st.warning(f"{symbol}: candle data is not available yet.")
        return

    candles = []
    for row in df.itertuples(index=False):
        try:
            candles.append({
                "time": int(row.time),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            })
        except (TypeError, ValueError, AttributeError):
            continue

    if not candles:
        st.warning(f"{symbol}: valid OHLC candles are not available.")
        return

    low_view = min(c["low"] for c in candles)
    high_view = max(c["high"] for c in candles)
    pad = max((high_view - low_view) * 0.08, abs(current_price) * 0.0005, 0.01)
    visible_low, visible_high = low_view - pad, high_view + pad

    grid_lines = []  # Initial entry lines are intentionally hidden; only active TP and waiting re-entry lines are shown.

    active_lines = []
    for item in state["levels"]:
        status = item.get("status")
        entry = safe_float(item.get("entry_price"))
        target = safe_float(item.get("target"))
        if status == "OPEN":
            if target > 0 and visible_low <= target <= visible_high:
                active_lines.append({"price": target, "color": "#ef4444", "width": 1,
                                     "style": 2, "title": f"TARGET Z{item['zone']} · L{item['level_no']:03d}"})
        elif status == "WAITING_REENTRY":
            reentry = safe_float(item.get("entry_price"))
            if reentry > 0 and visible_low <= reentry <= visible_high:
                active_lines.append({"price": reentry, "color": "#a78bfa", "width": 1,
                                     "style": 1, "title": f"RE-ENTRY WAIT Z{item['zone']} · L{item['level_no']:03d}"})

    events = []
    for event in state.get("events", [])[:250]:
        events.append({
            "time": str(event.get("Time (IST)", "")),
            "event": str(event.get("Event", "")),
            "status": str(event.get("Status", "")),
            "price": safe_float(event.get("Price"), 0.0),
        })

    payload = {
        "symbol": symbol,
        "title": title,
        "timeframe": CANDLE_RESOLUTION,
        "candles": candles,
        "currentPrice": float(current_price),
        "gridLines": grid_lines,
        "activeLines": active_lines,
        "events": events,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    chart_html = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
html,body{margin:0;padding:0;background:#131722;color:#d1d4dc;font-family:Arial,sans-serif;overflow:hidden}
#toolbar{height:38px;display:flex;align-items:center;gap:9px;padding:0 8px;background:#131722;white-space:nowrap}
#title{font-size:13px;font-weight:700;color:#d1d4dc}#tf{font-size:11px;color:#9aa4b2;background:#242936;padding:4px 7px;border-radius:4px}
#live{margin-left:auto;font-size:12px;font-weight:700;color:#d1d4dc}#fullscreen{background:#2a2e39;color:#d1d4dc;border:1px solid #434651;border-radius:4px;padding:5px 9px}
#chart{width:100%;height:490px}#status{font-size:11px;color:#8b93a7;padding:4px 8px}
</style></head><body>
<div id="toolbar"><span id="title"></span><span id="tf"></span><span id="live"></span><button id="fullscreen" type="button">⛶</button></div>
<div id="chart"></div><div id="status">Connecting chart...</div>
<script>
const D=__PAYLOAD__;
document.getElementById('title').textContent=D.title;
document.getElementById('tf').textContent=D.timeframe.toUpperCase();
document.getElementById('live').textContent='LIVE $'+Number(D.currentPrice).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4});
const el=document.getElementById('chart');
const chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:el.clientHeight,
 layout:{background:{type:'solid',color:'#131722'},textColor:'#d1d4dc',fontSize:11},
 grid:{vertLines:{color:'#1f2430'},horzLines:{color:'#1f2430'}},
 rightPriceScale:{visible:true,borderColor:'#434651',scaleMargins:{top:0.08,bottom:0.08}},
 timeScale:{borderColor:'#434651',timeVisible:true,secondsVisible:false},
 crosshair:{mode:LightweightCharts.CrosshairMode.Normal},handleScroll:true,handleScale:true});
const candles=chart.addCandlestickSeries({upColor:'#089981',downColor:'#f23645',borderUpColor:'#089981',borderDownColor:'#f23645',wickUpColor:'#089981',wickDownColor:'#f23645',priceLineVisible:false});
candles.setData(D.candles);
function addLine(L){if(!Number.isFinite(Number(L.price))||Number(L.price)<=0)return;
 candles.createPriceLine({price:Number(L.price),color:L.color,lineWidth:Number(L.width||1),lineStyle:Number(L.style||0),axisLabelVisible:true,title:L.title||''});}
D.gridLines.forEach(addLine);D.activeLines.forEach(addLine);
const livePriceLine=candles.createPriceLine({price:D.currentPrice,color:'#e5e7eb',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'LIVE'});
const markers=[];
for(const e of D.events){if(!e.time||!e.price)continue;
 const ms=Date.parse(e.time.replace(' ','T')+'+05:30');if(!Number.isFinite(ms))continue;
 const t=Math.floor(ms/1000);if(t<D.candles[0].time||t>D.candles[D.candles.length-1].time+3600)continue;
 const isProfit=e.status==='PROFIT';const isOpen=e.status==='OPEN';
 markers.push({time:t,position:isProfit?'aboveBar':(D.symbol==='XAUTUSD'?'belowBar':'aboveBar'),
 color:isProfit?'#f59e0b':(isOpen?'#22c55e':'#60a5fa'),shape:isProfit?'circle':(isOpen?'arrowUp':'circle'),
 text:(isProfit?'TP ':isOpen?(D.symbol==='XAUTUSD'?'BUY ':'SELL '):'')+Number(e.price).toLocaleString('en-US',{maximumFractionDigits:4})});}
markers.sort((a,b)=>a.time-b.time);if(markers.length)candles.setMarkers(markers);
const key='sanjay_demo_chart_'+D.symbol;let restored=false;
try{const r=JSON.parse(localStorage.getItem(key)||'null');if(r&&Number.isFinite(r.from)&&Number.isFinite(r.to)&&r.to>r.from){chart.timeScale().setVisibleLogicalRange(r);restored=true;}}catch(e){}
if(!restored)chart.timeScale().fitContent();
chart.timeScale().subscribeVisibleLogicalRangeChange(r=>{if(r&&Number.isFinite(r.from)&&Number.isFinite(r.to)){try{localStorage.setItem(key,JSON.stringify(r));}catch(e){}}});
document.getElementById('fullscreen').onclick=()=>{const target=document.documentElement;if(!document.fullscreenElement){if(target.requestFullscreen)target.requestFullscreen();}else if(document.exitFullscreen)document.exitFullscreen();};
new ResizeObserver(()=>chart.applyOptions({width:el.clientWidth,height:el.clientHeight})).observe(el);
document.getElementById('status').textContent='Delta Exchange public OHLC + live WebSocket • '+D.symbol+' • '+D.timeframe+' • Demo active target/re-entry markers (initial entry lines hidden)';
let ws=null,pingTimer=null,reconnectTimer=null;
function connectMarketSocket(){
 if(ws){try{ws.close();}catch(e){}}
 if(pingTimer)clearInterval(pingTimer);
 if(reconnectTimer)clearTimeout(reconnectTimer);
 ws=new WebSocket('wss://public-socket.india.delta.exchange');
 ws.onopen=()=>{
  ws.send(JSON.stringify({type:'subscribe',payload:{channels:[{name:'candlestick_'+D.timeframe,symbols:[D.symbol]}]}}));
  pingTimer=setInterval(()=>{if(ws&&ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'ping'}));},25000);
  document.getElementById('status').textContent='Connected • Delta public live candles • '+D.symbol+' • '+D.timeframe;
 };
 ws.onmessage=(event)=>{
  try{
   const msg=JSON.parse(event.data);if(!msg.type||!msg.type.startsWith('candlestick_'))return;
   let ts=msg.ts!=null?Number(msg.ts):Math.floor(Date.now()/1000);
   if(ts>100000000000)ts=Math.floor(ts/1000000);
   const sec=300,bucket=Math.floor(ts/sec)*sec;
   const c={time:bucket,open:Number(msg.o),high:Number(msg.h),low:Number(msg.l),close:Number(msg.c)};
   if(![c.open,c.high,c.low,c.close].every(Number.isFinite))return;
   const last=D.candles[D.candles.length-1];
   if(last&&last.time===bucket){c.open=Number(last.open);c.high=Math.max(Number(last.high),c.high,c.open);c.low=Math.min(Number(last.low),c.low,c.open);D.candles[D.candles.length-1]=c;}
   else if(!last||bucket>last.time){D.candles.push(c);if(D.candles.length>350)D.candles.shift();}
   else return;
   candles.update(c);livePriceLine.applyOptions({price:c.close});
   document.getElementById('live').textContent='LIVE $'+c.close.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4});
  }catch(e){console.error('Delta candle socket message',e);}
 };
 ws.onerror=()=>{document.getElementById('status').textContent='WebSocket error • reconnecting';};
 ws.onclose=()=>{if(pingTimer)clearInterval(pingTimer);reconnectTimer=setTimeout(connectMarketSocket,3000);};
}
connectMarketSocket();
</script></body></html>""".replace('__PAYLOAD__', payload_json)
    components.html(chart_html, height=550, scrolling=False)


def money(x: float) -> str:
    return f"${x:,.4f}" if abs(x) < 1 else f"${x:,.2f}"


def render_dashboard():
    st.subheader("Live Market & Grid Monitor")
    st.caption(f"Auto-refresh: {REFRESH_SECONDS}s · Chart timeframe: {CANDLE_RESOLUTION} · All timestamps shown in IST")

    # Read-only Owner API monitor from the previous dashboard style.
    # The Demo grid engine remains simulated and is not connected to order execution.
    render_owner_account_status()

    try:
        x_ticker = get_ticker(SYMBOLS["XAUT"])
        p_ticker = get_ticker(SYMBOLS["PAXG"])
        x_product = get_product(SYMBOLS["XAUT"])
        p_product = get_product(SYMBOLS["PAXG"])
        x_price = safe_float(x_ticker.get("close") or x_ticker.get("mark_price"))
        p_price = safe_float(p_ticker.get("close") or p_ticker.get("mark_price"))
        if x_price <= 0 or p_price <= 0:
            raise RuntimeError("Delta ticker did not return a valid live price.")
        st.session_state.last_api_ok = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        st.error(f"Delta public market data could not be loaded: {exc}")
        st.info("No dummy prices are shown. Check the symbols/API connection and refresh the app.")
        return

    process_leg(st.session_state.xaut_state, x_price, x_product, "long")
    process_leg(st.session_state.paxg_state, p_price, p_product, "short")

    x_open, x_open_fees, x_open_count, x_open_lots = calc_open_pnl(st.session_state.xaut_state, x_price, x_product, "long")
    p_open, p_open_fees, p_open_count, p_open_lots = calc_open_pnl(st.session_state.paxg_state, p_price, p_product, "short")
    xs = st.session_state.xaut_state
    ps = st.session_state.paxg_state
    x_real_net = xs["realized_gross"] - xs["realized_fees"]
    p_real_net = ps["realized_gross"] - ps["realized_fees"]
    combined_realized = x_real_net + p_real_net
    combined_open_net = (x_open - x_open_fees) + (p_open - p_open_fees)
    combined_net = combined_realized + combined_open_net

    st.markdown("### Combined Profit & Loss")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Combined Net P&L", money(combined_net), help="Realized + open P&L, less estimated maker fees. Funding is not included.")
    c2.metric("Combined Realized", money(combined_realized))
    c3.metric("Combined Open P&L", money(combined_open_net))
    c4.metric("Total Open Lots", f"{x_open_lots + p_open_lots} / 660")

    st.markdown("### Live Prices")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("XAUTUSD Live Price", money(x_price))
    q2.metric("PAXGUSD Live Price", money(p_price))
    q3.metric("XAUT Product / Lot", str(x_product.get("id", "—")) + " / " + str(x_product.get("contract_value", "—")))
    q4.metric("PAXG Product / Lot", str(p_product.get("id", "—")) + " / " + str(p_product.get("contract_value", "—")))

    x_cv = safe_float(x_product.get("contract_value"), 0.0)
    p_cv = safe_float(p_product.get("contract_value"), 0.0)
    x_notional = x_price * x_cv * INITIAL_LOTS_PER_SIDE
    p_notional = p_price * p_cv * INITIAL_LOTS_PER_SIDE
    x_margin = x_notional / LEVERAGE_DISPLAY if x_cv > 0 else 0.0
    p_margin = p_notional / LEVERAGE_DISPLAY if p_cv > 0 else 0.0
    st.markdown("### Initial Position — Live Notional Estimate")
    m1, m2, m3 = st.columns(3)
    m1.metric("XAUT BUY · 330 lots", money(x_notional))
    m2.metric("PAXG SELL · 330 lots", money(p_notional))
    m3.metric("Combined Theoretical Margin", money(x_margin + p_margin))
    st.caption("Notional live price × product contract value × 330 lots. Margin is only a 100x planning estimate, not Delta's confirmed margin requirement.")

    st.markdown("### Separate Leg Performance")
    a, b = st.columns(2)
    with a:
        st.markdown("#### 🟢 XAUT — BUY / LONG")
        st.metric("XAUT Net P&L", money(x_real_net + x_open - x_open_fees))
        st.write(f"Realized: **{money(x_real_net)}** · Open: **{money(x_open - x_open_fees)}**")
        st.write(f"मौजूदा खुली मात्रा: **{x_open_lots} / 330 lots** · Active target levels: **{x_open_count} / 330**")
        st.write(f"TP cycles: **{xs['closed_trades']}** · Maker fee assumption: **{safe_float(x_product.get('maker_commission_rate')) * 100:.4f}%**")
    with b:
        st.markdown("#### 🔴 PAXG — SELL / SHORT")
        st.metric("PAXG Net P&L", money(p_real_net + p_open - p_open_fees))
        st.write(f"Realized: **{money(p_real_net)}** · Open: **{money(p_open - p_open_fees)}**")
        st.write(f"मौजूदा खुली मात्रा: **{p_open_lots} / 330 lots** · Active target levels: **{p_open_count} / 330**")
        st.write(f"TP cycles: **{ps['closed_trades']}** · Maker fee assumption: **{safe_float(p_product.get('maker_commission_rate')) * 100:.4f}%**")

    st.markdown("### दोनों Live Charts · Active Target और Re-entry Lines")
    chart_col1, chart_col2 = st.columns(2, gap="small")
    try:
        x_df = get_candles(SYMBOLS["XAUT"], CANDLE_RESOLUTION, CANDLE_COUNT)
        p_df = get_candles(SYMBOLS["PAXG"], CANDLE_RESOLUTION, CANDLE_COUNT)
        with chart_col1:
            render_original_style_chart(x_df, "XAUTUSD", "XAUTUSD · BUY / LONG", x_price, XAUT_LEVELS, "long", xs)
        with chart_col2:
            render_original_style_chart(p_df, "PAXGUSD", "PAXGUSD · SELL / SHORT", p_price, PAXG_LEVELS, "short", ps)
    except Exception as exc:
        st.warning(f"Live prices are available, but candle charts could not load: {exc}")

    st.markdown("### मौजूदा पोजीशन और आगे के टारगेट")
    tab_x, tab_p = st.tabs(["XAUTUSD · BUY", "PAXGUSD · SELL"])

    def live_target_rows(state: dict, current_price: float) -> pd.DataFrame:
        rows = []
        for item in state["levels"]:
            lots = int(item["lots"])
            entry = safe_float(item.get("entry_price"))
            target = safe_float(item.get("target"))
            if item["status"] == "OPEN":
                target_text = money(target)
                distance_text = money(abs(target - current_price))
                status_text = "OPEN · TARGET ACTIVE"
                open_qty = lots
            elif item["status"] == "WAITING_REENTRY":
                target_text = "TP पूरा · Re-entry"
                distance_text = money(abs(entry - current_price))
                status_text = f"WAITING RE-ENTRY @ {money(entry)}"
                open_qty = 0
            else:
                target_text, distance_text, status_text, open_qty = "—", "—", "NOT STARTED", 0
            rows.append({
                "Target": f"L{item['level_no']:03d} · Z{item['zone']} · {money(item['step'])}",
                "खुली मात्रा": f"{open_qty} / {lots} lots",
                "Target Price": target_text,
                "मौजूदा भाव से दूरी": distance_text,
                "स्थिति": status_text,
                "TP Count": item["cycles"],
            })
        return pd.DataFrame(rows)

    with tab_x:
        st.metric("XAUTUSD अभी खुली BUY मात्रा", f"{x_open_lots} lots")
        st.dataframe(live_target_rows(xs, x_price), use_container_width=True, hide_index=True)
    with tab_p:
        st.metric("PAXGUSD अभी खुली SELL मात्रा", f"{p_open_lots} lots")
        st.dataframe(live_target_rows(ps, p_price), use_container_width=True, hide_index=True)

    st.markdown("### Simulated Trade History — दोनों साथ")
    events = xs["events"] + ps["events"]
    if events:
        hist = pd.DataFrame(events).sort_values("Time (IST)", ascending=False)
        st.dataframe(hist, use_container_width=True, height=300, hide_index=True)
    else:
        st.info("पहली वैध live price पर दोनों तरफ की 330-330 lots की शुरुआती simulated positions बनेंगी।")

    with st.expander("महत्वपूर्ण Demo नियम और सीमाएँ", expanded=False):
        st.markdown(
            """
            - **केवल DEMO:** कोई API key/secret नहीं माँगा जाता और कोई real order नहीं भेजा जाता।
            - पहले valid live-price snapshot पर XAUT BUY और PAXG SELL के 330-330 simulated one-lot grid levels सक्रिय होते हैं; प्रत्येक level का अपना entry और target price है।
            - हर तरफ कुल 330 अलग-अलग one-lot grid levels हैं: Zone 1 में 176 स्तर ($5 spacing), Zone 2 में 88 ($10), Zone 3 में 44 ($20), और Zone 4 में 22 ($40)।
            - हर level का अपना अलग predefined grid entry और target है; chart पर शुरुआती entry lines नहीं दिखतीं, केवल active targets और TP के बाद waiting re-entry lines दिखती हैं।
            - जिस तरफ का target hit होगा, केवल उसी one-lot level की quantity बंद होगी; दूसरी तरफ और बाकी levels जस के तस रहेंगे।
            - TP के बाद वही level अपने original entry price पर वापसी का इंतजार करता है और लौटने पर एक lot से दोबारा खुलता है।
            - Demo polling हर 5 सेकंड में ticker snapshot लेती है। बीच में छूकर लौटे हुए price levels miss हो सकते हैं; इसलिए यह tick-perfect backtest नहीं है।
            - P&L contract_value और maker commission metadata से अनुमानित है। Funding, slippage, liquidation, margin scaling और partial fills शामिल नहीं हैं।
            - 100x सिर्फ आपकी planning assumption के रूप में दिखाया गया है; वास्तविक leverage/margin की पुष्टि इस Demo से नहीं होती।
            - Grid simulator state Streamlit session memory में है; app restart/session reset पर Demo history reset हो सकती है।
            """
        )

    st.caption(f"Last successful Delta data fetch: {st.session_state.last_api_ok or '—'} · Demo session started: {st.session_state.demo_started_at}")


@st.fragment(run_every=REFRESH_SECONDS)
def live_fragment():
    render_dashboard()

live_fragment()

st.divider()
left, right = st.columns([3, 1])
with left:
    st.warning("यह build DEMO ONLY है: 330-330 lots और target fills simulated हैं। कोई वास्तविक order Delta Exchange को नहीं भेजा जाता।")
with right:
    if st.button("Reset Demo History", type="secondary", use_container_width=True):
        st.session_state.xaut_state = init_leg_state("XAUT", XAUT_LEVELS)
        st.session_state.paxg_state = init_leg_state("PAXG", PAXG_LEVELS)
        st.session_state.demo_started_at = datetime.now(timezone.utc).isoformat()
        st.rerun()
