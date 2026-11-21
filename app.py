# -*- coding: utf-8 -*-
"""Single-file XAUT/PAXG paired grid dashboard — DEMO ONLY.

Market data: Delta Exchange India public REST API.
Execution: local simulated fills only. No authenticated/private order API is called.

Agreed strategy encoded here:
- XAUTUSD BUY / LONG; PAXGUSD SELL / SHORT.
- Four fixed price zones and grid spacing as supplied by the user.
- One exchange lot per grid entry.
- Take-profit distance equals that zone's grid spacing (the working interpretation
  used in the prior example: XAUT 3520 -> 3525; PAXG 3520 -> 3515).
- After TP, wait for price to return to the original entry price, then re-enter
  one lot at that same price and repeat.
- Separate leg P&L plus combined P&L.

Important: this is a DEMO simulator. It polls the public ticker every 5 seconds;
brief intraperiod price touches can be missed. It is not suitable for live execution.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ----------------------------- App configuration -----------------------------
st.set_page_config(page_title="XAUT + PAXG Grid Demo", page_icon="🟡", layout="wide")

BASE_URL = "https://api.india.delta.exchange"
SYMBOLS = {"XAUT": "XAUTUSD", "PAXG": "PAXGUSD"}
REFRESH_SECONDS = 5
CANDLE_RESOLUTION = "5m"
CANDLE_COUNT = 120
LOT_COUNT_PER_GRID = 1
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

st.title("🟡 XAUT BUY + PAXG SELL — Paired Grid Dashboard")
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


def make_levels_xaut() -> List[dict]:
    # One order for each calculated grid interval: 176 + 88 + 44 + 22 = 330.
    levels: List[dict] = []
    for zone_no, (low, high, step) in enumerate(XAUT_ZONES, start=1):
        count = int(round((high - low) / step))
        for idx in range(count):
            levels.append({"entry": round(low + idx * step, 8), "step": step, "zone": zone_no})
    return levels


def make_levels_paxg() -> List[dict]:
    # One order for each calculated grid interval: 176 + 88 + 44 + 22 = 330.
    levels: List[dict] = []
    for zone_no, (high, low, step) in enumerate(PAXG_ZONES, start=1):
        count = int(round((high - low) / step))
        for idx in range(count):
            levels.append({"entry": round(high - idx * step, 8), "step": step, "zone": zone_no})
    return levels


# Build the complete level lists once. These constants are used by both the
# simulator state and the dashboard; keeping them here makes app.py standalone.
XAUT_LEVELS = make_levels_xaut()
PAXG_LEVELS = make_levels_paxg()


def init_leg_state(key: str, levels: List[dict]) -> dict:
    return {
        "levels": [
            {**x, "status": "WAITING_ENTRY", "entry_time": None,
             "entry_price": None, "target": None, "cycles": 0,
             "last_exit": None}
            for x in levels
        ],
        "last_price": None,
        "realized_gross": 0.0,
        "realized_fees": 0.0,
        "closed_trades": 0,
        "events": [],
        "key": key,
    }


if "xaut_state" not in st.session_state:
    st.session_state.xaut_state = init_leg_state("XAUT", XAUT_LEVELS)
if "paxg_state" not in st.session_state:
    st.session_state.paxg_state = init_leg_state("PAXG", PAXG_LEVELS)
if "demo_started_at" not in st.session_state:
    st.session_state.demo_started_at = datetime.now(timezone.utc).isoformat()
if "last_api_ok" not in st.session_state:
    st.session_state.last_api_ok = None


def add_event(state: dict, text: str, price: float, status: str) -> None:
    state["events"].insert(0, {
        "Time (IST)": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": state["key"], "Event": text,
        "Price": price, "Status": status,
    })
    state["events"] = state["events"][:3000]


def process_leg(state: dict, current_price: float, product: dict, direction: str) -> None:
    """Process price crossings since the previous ticker snapshot.

    XAUT direction='long': initial entries trigger on upward crossing; TP on further rise;
    re-entry triggers on return down to the original entry.
    PAXG direction='short': initial entries trigger on downward crossing; TP on further fall;
    re-entry triggers on return up to the original entry.
    """
    previous = state["last_price"]
    if previous is None:
        state["last_price"] = current_price
        return  # Never create an artificial first-load fill.
    if abs(current_price - previous) < 1e-12:
        return

    contract_value = safe_float(product.get("contract_value"), 0.0)
    maker_fee = safe_float(product.get("maker_commission_rate"), 0.0)
    if contract_value <= 0:
        state["last_price"] = current_price
        return

    now_text = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    lot_units = contract_value * LOT_COUNT_PER_GRID

    for item in state["levels"]:
        entry = item["entry"]
        step = item["step"]
        if direction == "long":
            target = entry + step
            moving_up = current_price > previous
            moving_down = current_price < previous
            if item["status"] == "WAITING_ENTRY" and moving_up and previous < entry <= current_price:
                item.update(status="OPEN", entry_time=now_text, entry_price=entry, target=target)
                add_event(state, f"BUY fill · Zone {item['zone']} · 1 lot", entry, "OPEN")
            if item["status"] == "OPEN" and moving_up and previous < safe_float(item["target"]) <= current_price:
                fill = safe_float(item["entry_price"])
                gross = (safe_float(item["target"]) - fill) * lot_units
                fee = (abs(fill) + abs(safe_float(item["target"]))) * lot_units * maker_fee
                state["realized_gross"] += gross
                state["realized_fees"] += fee
                state["closed_trades"] += 1
                item.update(status="WAITING_REENTRY", last_exit=safe_float(item["target"]),
                            entry_time=None, entry_price=None, target=None, cycles=item["cycles"] + 1)
                add_event(state, f"BUY target hit · Profit booked · Re-entry waiting at ${entry:,.2f}",
                          safe_float(item["last_exit"]), "PROFIT")
            if item["status"] == "WAITING_REENTRY" and moving_down and previous > entry >= current_price:
                item.update(status="OPEN", entry_time=now_text, entry_price=entry, target=target)
                add_event(state, f"BUY re-entry fill · Zone {item['zone']} · 1 lot", entry, "RE-ENTRY")
        else:
            target = entry - step
            moving_down = current_price < previous
            moving_up = current_price > previous
            if item["status"] == "WAITING_ENTRY" and moving_down and previous > entry >= current_price:
                item.update(status="OPEN", entry_time=now_text, entry_price=entry, target=target)
                add_event(state, f"SELL fill · Zone {item['zone']} · 1 lot", entry, "OPEN")
            if item["status"] == "OPEN" and moving_down and previous > safe_float(item["target"]) >= current_price:
                fill = safe_float(item["entry_price"])
                gross = (fill - safe_float(item["target"])) * lot_units
                fee = (abs(fill) + abs(safe_float(item["target"]))) * lot_units * maker_fee
                state["realized_gross"] += gross
                state["realized_fees"] += fee
                state["closed_trades"] += 1
                item.update(status="WAITING_REENTRY", last_exit=safe_float(item["target"]),
                            entry_time=None, entry_price=None, target=None, cycles=item["cycles"] + 1)
                add_event(state, f"SELL target hit · Profit booked · Re-entry waiting at ${entry:,.2f}",
                          safe_float(item["last_exit"]), "PROFIT")
            if item["status"] == "WAITING_REENTRY" and moving_up and previous < entry <= current_price:
                item.update(status="OPEN", entry_time=now_text, entry_price=entry, target=target)
                add_event(state, f"SELL re-entry fill · Zone {item['zone']} · 1 lot", entry, "RE-ENTRY")

    state["last_price"] = current_price


def calc_open_pnl(state: dict, current_price: float, product: dict, direction: str) -> Tuple[float, float, int]:
    contract_value = safe_float(product.get("contract_value"), 0.0)
    maker_fee = safe_float(product.get("maker_commission_rate"), 0.0)
    gross = 0.0
    est_fees = 0.0
    open_count = 0
    for item in state["levels"]:
        if item["status"] != "OPEN":
            continue
        open_count += 1
        entry = safe_float(item["entry_price"])
        units = contract_value * LOT_COUNT_PER_GRID
        if direction == "long":
            gross += (current_price - entry) * units
        else:
            gross += (entry - current_price) * units
        est_fees += (abs(entry) + abs(current_price)) * units * maker_fee
    return gross, est_fees, open_count


def build_chart(df: pd.DataFrame, title: str, current_price: float,
                levels: List[dict], direction: str, state: dict) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(go.Candlestick(
            x=df["datetime"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name=title,
            increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
        ))
    fig.add_hline(y=current_price, line_dash="solid", line_width=1.5,
                  annotation_text=f"Live ${current_price:,.2f}", annotation_position="top right")
    if not df.empty:
        low_view = float(df["low"].min())
        high_view = float(df["high"].max())
    else:
        low_view, high_view = current_price * 0.98, current_price * 1.02
    # Draw grid lines visible in the chart window only.
    visible = [x for x in levels if low_view <= x["entry"] <= high_view]
    for item in visible[:100]:
        fig.add_hline(y=item["entry"], line_dash="dot", line_width=0.7,
                      line_color="rgba(100,116,139,.55)")
    # Show active entry and target levels.
    for item in state["levels"]:
        if item["status"] == "OPEN":
            ep = safe_float(item["entry_price"])
            tp = safe_float(item["target"])
            if low_view <= ep <= high_view:
                fig.add_hline(y=ep, line_dash="dash", line_width=1.3, line_color="#2563eb",
                              annotation_text=f"Entry {ep:,.2f}", annotation_position="bottom right")
            if low_view <= tp <= high_view:
                fig.add_hline(y=tp, line_dash="dash", line_width=1.3, line_color="#16a34a",
                              annotation_text=f"Target {tp:,.2f}", annotation_position="top right")
    fig.update_layout(
        title=title, height=470, margin=dict(l=10, r=15, t=45, b=10),
        xaxis_rangeslider_visible=False, template="plotly_dark",
        legend=dict(orientation="h", y=1.02, x=0),
        yaxis=dict(side="right", title="Price (USD)", tickformat=",.2f", fixedrange=False),
        xaxis=dict(title="Time (IST)"),
    )
    return fig


def money(x: float) -> str:
    return f"${x:,.4f}" if abs(x) < 1 else f"${x:,.2f}"


def render_dashboard():
    st.subheader("Live Market & Grid Monitor")
    st.caption(f"Auto-refresh: {REFRESH_SECONDS}s · Chart timeframe: {CANDLE_RESOLUTION} · All timestamps shown in IST")

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

    x_open, x_open_fees, x_open_count = calc_open_pnl(st.session_state.xaut_state, x_price, x_product, "long")
    p_open, p_open_fees, p_open_count = calc_open_pnl(st.session_state.paxg_state, p_price, p_product, "short")
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
    c4.metric("Open Grid Positions", f"{x_open_count + p_open_count}")

    st.markdown("### Live Prices")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("XAUTUSD Live Price", money(x_price))
    q2.metric("PAXGUSD Live Price", money(p_price))
    q3.metric("XAUT Product / Lot", str(x_product.get("id", "—")) + " / " + str(x_product.get("contract_value", "—")))
    q4.metric("PAXG Product / Lot", str(p_product.get("id", "—")) + " / " + str(p_product.get("contract_value", "—")))

    x_cv = safe_float(x_product.get("contract_value"), 0.0)
    p_cv = safe_float(p_product.get("contract_value"), 0.0)
    x_notional = sum(i["entry"] * x_cv * LOT_COUNT_PER_GRID for i in XAUT_LEVELS)
    p_notional = sum(i["entry"] * p_cv * LOT_COUNT_PER_GRID for i in PAXG_LEVELS)
    x_margin = x_notional / LEVERAGE_DISPLAY if x_cv > 0 else 0.0
    p_margin = p_notional / LEVERAGE_DISPLAY if p_cv > 0 else 0.0
    st.markdown("### Grid Capital Estimate — 100x planning assumption")
    m1, m2, m3 = st.columns(3)
    m1.metric("XAUT 330 Lots · Notional", money(x_notional))
    m2.metric("PAXG 330 Lots · Notional", money(p_notional))
    m3.metric("Combined Theoretical Margin", money(x_margin + p_margin))
    st.caption("यह सभी 660 one-lot grid orders के price levels पर आधारित सैद्धांतिक अनुमान है: कुल notional ÷ 100. इसमें exchange margin scaling, fees, funding, slippage और safety reserve शामिल नहीं हैं; वास्तविक required balance Delta की live margin calculation से अलग हो सकता है।")

    st.markdown("### Separate Leg Performance")
    a, b = st.columns(2)
    with a:
        st.markdown("#### 🟢 XAUT — BUY / LONG")
        st.metric("XAUT Net P&L", money(x_real_net + x_open - x_open_fees))
        st.write(f"Realized: **{money(x_real_net)}** · Open: **{money(x_open - x_open_fees)}**")
        st.write(f"Closed profitable cycles: **{xs['closed_trades']}** · Open positions: **{x_open_count}**")
        st.write(f"Grid levels: **{len(xs['levels'])}** · Maker fee assumption: **{safe_float(x_product.get('maker_commission_rate')) * 100:.4f}%**")
    with b:
        st.markdown("#### 🔴 PAXG — SELL / SHORT")
        st.metric("PAXG Net P&L", money(p_real_net + p_open - p_open_fees))
        st.write(f"Realized: **{money(p_real_net)}** · Open: **{money(p_open - p_open_fees)}**")
        st.write(f"Closed profitable cycles: **{ps['closed_trades']}** · Open positions: **{p_open_count}**")
        st.write(f"Grid levels: **{len(ps['levels'])}** · Maker fee assumption: **{safe_float(p_product.get('maker_commission_rate')) * 100:.4f}%**")

    st.markdown("### दोनों Live Candlestick Charts")
    chart_col1, chart_col2 = st.columns(2, gap="small")
    try:
        x_df = get_candles(SYMBOLS["XAUT"], CANDLE_RESOLUTION, CANDLE_COUNT)
        p_df = get_candles(SYMBOLS["PAXG"], CANDLE_RESOLUTION, CANDLE_COUNT)
        with chart_col1:
            st.plotly_chart(build_chart(x_df, "XAUTUSD · BUY Grid", x_price, XAUT_LEVELS, "long", xs), use_container_width=True)
        with chart_col2:
            st.plotly_chart(build_chart(p_df, "PAXGUSD · SELL Grid", p_price, PAXG_LEVELS, "short", ps), use_container_width=True)
    except Exception as exc:
        st.warning(f"Live prices are available, but candle charts could not load: {exc}")

    st.markdown("### चारों भाग — Grid Settings")
    z1, z2 = st.columns(2)
    with z1:
        st.markdown("**XAUT BUY range**")
        st.dataframe(pd.DataFrame([
            {"भाग": i, "Price Range": f"${lo:,.0f} → ${hi:,.0f}", "Grid अंतर": f"${step:,.0f}"}
            for i, (lo, hi, step) in enumerate(XAUT_ZONES, 1)
        ]), use_container_width=True, hide_index=True)
    with z2:
        st.markdown("**PAXG SELL range**")
        st.dataframe(pd.DataFrame([
            {"भाग": i, "Price Range": f"${hi:,.0f} → ${lo:,.0f}", "Grid अंतर": f"${step:,.0f}"}
            for i, (hi, lo, step) in enumerate(PAXG_ZONES, 1)
        ]), use_container_width=True, hide_index=True)

    st.markdown("### Grid Level Status")
    tab_x, tab_p = st.tabs(["XAUT BUY Levels", "PAXG SELL Levels"])
    with tab_x:
        x_rows = [{"Zone": i["zone"], "Entry Level": i["entry"], "Grid / Target Distance": i["step"],
                   "Target": i["entry"] + i["step"], "Status": i["status"], "Cycles": i["cycles"]}
                  for i in xs["levels"]]
        st.dataframe(pd.DataFrame(x_rows), use_container_width=True, height=330, hide_index=True)
    with tab_p:
        p_rows = [{"Zone": i["zone"], "Entry Level": i["entry"], "Grid / Target Distance": i["step"],
                   "Target": i["entry"] - i["step"], "Status": i["status"], "Cycles": i["cycles"]}
                  for i in ps["levels"]]
        st.dataframe(pd.DataFrame(p_rows), use_container_width=True, height=330, hide_index=True)

    st.markdown("### Simulated Trade History — दोनों साथ")
    events = xs["events"] + ps["events"]
    if events:
        hist = pd.DataFrame(events).sort_values("Time (IST)", ascending=False)
        st.dataframe(hist, use_container_width=True, height=300, hide_index=True)
    else:
        st.info("अभी कोई Grid trigger नहीं हुआ है। पहले price snapshot को baseline बनाया गया है; अगली price movement पर ही Demo entry trigger होगी।")

    with st.expander("महत्वपूर्ण Demo नियम और सीमाएँ", expanded=False):
        st.markdown(
            """
            - **केवल DEMO:** कोई API key/secret नहीं माँगा जाता और कोई real order नहीं भेजा जाता।
            - एक Grid level पर 1 exchange lot की simulated entry होती है। Lot का underlying size Delta के product metadata से लिया जाता है; hard-coded नहीं है।
            - XAUT में price level के ऊपर जाने पर BUY trigger, target एक grid step ऊपर; target hit होने पर profit book और उसी entry price पर re-entry का इंतजार।
            - PAXG में price level के नीचे जाने पर SELL trigger, target एक grid step नीचे; target hit होने पर profit book और उसी entry price पर re-entry का इंतजार।
            - इस build में Target distance = उसी zone का Grid distance रखा गया है।
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
    st.warning("REAL MODE अभी जानबूझकर बंद है। Demo परिणामों की जाँच और contract/margin verification के बाद ही authenticated order execution जोड़ा जाएगा।")
with right:
    if st.button("Reset Demo History", type="secondary", use_container_width=True):
        st.session_state.xaut_state = init_leg_state("XAUT", XAUT_LEVELS)
        st.session_state.paxg_state = init_leg_state("PAXG", PAXG_LEVELS)
        st.session_state.demo_started_at = datetime.now(timezone.utc).isoformat()
        st.rerun()
