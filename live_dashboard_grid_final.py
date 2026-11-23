# -*- coding: utf-8 -*-
"""
DELTA SMART SIGNAL AI - SIGNAL ONLY SCANNER
Single-file Streamlit app for Delta Exchange India.

IMPORTANT SAFETY DESIGN:
- Public market-data GET requests only.
- No API key/secret fields.
- No order, position, wallet, leverage, cancel, or trade endpoints.
- No real trades and no paper trades.
- Entry/SL/targets are analytical reference levels only.

Install:
    pip install streamlit pandas numpy requests plotly
Run:
    streamlit run app.py
"""

from __future__ import annotations

import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ----------------------------- CONFIG ---------------------------------
APP_TITLE = "DELTA SMART SIGNAL AI"
BASE_URL = "https://api.india.delta.exchange"
SYMBOLS = ["XAUTUSD", "PAXGUSD", "BTCUSD", "ETHUSD"]
TIMEFRAMES = ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]
TF_LABELS = {
    "1d": "1D - Larger direction",
    "4h": "4H - Main trend",
    "1h": "1H - Main signal",
    "30m": "30M - Trend strength",
    "15m": "15M - Confirmation",
    "5m": "5M - Short-term support",
    "1m": "1M - Final closed-candle timing",
}
DB_PATH = Path(__file__).with_name("delta_signal_history.sqlite3")
REQUEST_TIMEOUT = 15
HEADERS = {"Accept": "application/json", "User-Agent": "DeltaSignalOnlyScanner/1.0"}

st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")

# ----------------------------- SAFETY ---------------------------------
st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px;}
.signal-card {border:1px solid rgba(120,120,120,.35); border-radius:12px; padding:16px; margin:6px 0;}
.small-muted {color: #888; font-size: .88rem;}
div[data-testid="stMetric"] {background: rgba(128,128,128,.07); padding: 10px; border-radius: 10px;}
</style>
""", unsafe_allow_html=True)

st.title("📊 " + APP_TITLE)
st.caption("Delta Exchange India | केवल Market Analysis और Signals | कोई Trading नहीं")

st.error(
    "SIGNAL-ONLY MODE: यह ऐप केवल Public Market Data पढ़ता है। "
    "यह कोई BUY/SELL Order नहीं भेजता, कोई Paper Trade नहीं चलाता और आपके Trading Account को access नहीं करता।"
)

# ----------------------------- API HELPERS ----------------------------
def api_get(path: str, params: Optional[dict] = None) -> dict:
    """Public GET only. No authentication and no write endpoints."""
    response = requests.get(
        BASE_URL + path, params=params or {}, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or payload))
    return payload


@st.cache_data(ttl=300, show_spinner=False)
def get_live_products() -> dict:
    payload = api_get(
        "/v2/products",
        {"contract_types": "perpetual_futures", "states": "live", "page_size": 1000},
    )
    rows = payload.get("result", []) if isinstance(payload, dict) else []
    return {str(p.get("symbol", "")).upper(): p for p in rows if p.get("symbol")}


@st.cache_data(ttl=10, show_spinner=False)
def get_ticker(symbol: str) -> dict:
    payload = api_get(f"/v2/tickers/{symbol}")
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    if isinstance(result, list):
        return result[0] if result else {}
    return result if isinstance(result, dict) else {}


@st.cache_data(ttl=25, show_spinner=False)
def get_candles(symbol: str, resolution: str, count: int) -> pd.DataFrame:
    # Delta history endpoint accepts Unix seconds. End time is current UTC.
    end_ts = int(datetime.now(timezone.utc).timestamp())
    seconds = {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }[resolution]
    start_ts = end_ts - seconds * count
    payload = api_get(
        "/v2/history/candles",
        {
            "resolution": resolution,
            "symbol": symbol,
            "start": str(start_ts),
            "end": str(end_ts),
        },
    )
    rows = payload.get("result", []) if isinstance(payload, dict) else []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Delta may return time or timestamp; normalize common response forms.
    if "time" not in df.columns and "timestamp" in df.columns:
        df["time"] = df["timestamp"]
    required = ["open", "high", "low", "close"]
    if "time" not in df.columns or not all(c in df.columns for c in required):
        raise ValueError(f"Unexpected candle response fields: {list(df.columns)}")
    for col in required + (["volume"] if "volume" in df.columns else []):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    # API candle timestamps are Unix seconds; tolerate microseconds if returned.
    if df["time"].dropna().median() > 10**12:
        df["time"] = df["time"] / 1_000_000
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.dropna(subset=["time"] + required).sort_values("time").drop_duplicates("time")
    df = df.reset_index(drop=True)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df


# ----------------------------- INDICATORS -----------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    a = atr(df, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def supertrend_direction(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """ATR-based SuperTrend direction; uses closed candles only at caller."""
    a = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + multiplier * a
    lower = hl2 - multiplier * a
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction = pd.Series(index=df.index, dtype=float)
    direction.iloc[:] = np.nan
    for i in range(1, len(df)):
        if pd.isna(a.iloc[i]) or pd.isna(a.iloc[i - 1]):
            continue
        if upper.iloc[i] < final_upper.iloc[i - 1] or df["close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]
        if lower.iloc[i] > final_lower.iloc[i - 1] or df["close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]
        prev_dir = direction.iloc[i - 1]
        if pd.isna(prev_dir):
            prev_dir = 1 if df["close"].iloc[i] >= hl2.iloc[i] else -1
        if prev_dir == 1:
            direction.iloc[i] = -1 if df["close"].iloc[i] < final_lower.iloc[i] else 1
        else:
            direction.iloc[i] = 1 if df["close"].iloc[i] > final_upper.iloc[i] else -1
    return direction.ffill()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out, 14)
    out["adx"] = adx(out, 14)
    out["st_dir"] = supertrend_direction(out, 10, 3.0)
    out["macd"] = ema(out["close"], 12) - ema(out["close"], 26)
    out["macd_signal"] = ema(out["macd"], 9)
    out["roc"] = out["close"].pct_change(10) * 100
    out["vol_avg"] = out["volume"].rolling(20, min_periods=5).mean()
    return out


def candle_is_closed(df: pd.DataFrame, resolution: str) -> pd.DataFrame:
    """Drop the currently forming candle using timeframe duration."""
    if df.empty:
        return df
    seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}[resolution]
    now = pd.Timestamp.now(tz="UTC")
    # Candle time is converted to IST in dataframe; convert back for comparison.
    candle_open_utc = df["time"].dt.tz_convert("UTC")
    return df.loc[(candle_open_utc + pd.to_timedelta(seconds, unit="s")) <= now].reset_index(drop=True)


# ----------------------------- ANALYSIS --------------------------------
def direction_from_row(row: pd.Series) -> int:
    if pd.isna(row.get("st_dir")) or pd.isna(row.get("ema20")) or pd.isna(row.get("ema50")):
        return 0
    if row["st_dir"] > 0 and row["ema20"] > row["ema50"]:
        return 1
    if row["st_dir"] < 0 and row["ema20"] < row["ema50"]:
        return -1
    return 0


def maturity_analysis(daily: pd.DataFrame, hourly: pd.DataFrame) -> dict:
    """Combined Daily Movement 30%, Swing Movement 40%, ATR extension 30%."""
    if len(daily) < 20 or len(hourly) < 30:
        return {"score": np.nan, "label": "Insufficient data", "daily": np.nan, "swing": np.nan, "atr": np.nan,
                "continuation": "Not enough data", "pullback": "Not enough data", "remaining": "Not available"}
    d = add_indicators(daily)
    h = add_indicators(hourly)
    last_d, last_h = d.iloc[-1], h.iloc[-1]
    # Daily range progress: position within current exchange-provided daily candle.
    day_range = max(float(last_d["high"] - last_d["low"]), 0.0)
    daily_progress = abs(float(last_d["close"] - last_d["open"])) / day_range if day_range > 0 else 0
    daily_score = float(np.clip(daily_progress, 0, 1) * 100)

    # Swing: locate latest confirmed local pivot within last 80 closed 1H candles.
    recent = h.tail(80).reset_index(drop=True)
    lows = [i for i in range(2, len(recent) - 2)
            if recent["low"].iloc[i] <= recent["low"].iloc[i-1] and recent["low"].iloc[i] <= recent["low"].iloc[i-2]
            and recent["low"].iloc[i] < recent["low"].iloc[i+1] and recent["low"].iloc[i] < recent["low"].iloc[i+2]]
    highs = [i for i in range(2, len(recent) - 2)
             if recent["high"].iloc[i] >= recent["high"].iloc[i-1] and recent["high"].iloc[i] >= recent["high"].iloc[i-2]
             and recent["high"].iloc[i] > recent["high"].iloc[i+1] and recent["high"].iloc[i] > recent["high"].iloc[i+2]]
    close = float(last_h["close"])
    swing_move = 0.0
    if direction_from_row(last_h) >= 0 and lows:
        pivot = float(recent["low"].iloc[lows[-1]])
        swing_move = abs(close - pivot)
    elif highs:
        pivot = float(recent["high"].iloc[highs[-1]])
        swing_move = abs(pivot - close)
    swing_atr = float(last_h["atr"]) if pd.notna(last_h["atr"]) and last_h["atr"] > 0 else np.nan
    swing_score = float(np.clip(swing_move / (3 * swing_atr), 0, 1) * 100) if pd.notna(swing_atr) else 0

    # ATR extension: daily candle body/range normalized to daily ATR.
    daily_atr = float(last_d["atr"]) if pd.notna(last_d["atr"]) and last_d["atr"] > 0 else np.nan
    atr_ratio = day_range / daily_atr if pd.notna(daily_atr) else 0
    atr_score = float(np.clip(atr_ratio / 2.0, 0, 1) * 100)

    score = round(0.30 * daily_score + 0.40 * swing_score + 0.30 * atr_score, 1)
    if score <= 25:
        label = "Early / शुरुआती"
    elif score <= 50:
        label = "Medium / मध्यम"
    elif score <= 75:
        label = "Mature / काफी चाल"
    else:
        label = "Highly Mature / बहुत ज्यादा चाल"

    adx_value = float(last_h["adx"]) if pd.notna(last_h["adx"]) else 0
    rsi_value = float(last_h["rsi"]) if pd.notna(last_h["rsi"]) else 50
    momentum_ok = (adx_value >= 18 and 40 <= rsi_value <= 70) or (adx_value >= 18 and 30 <= rsi_value <= 60)
    continuation = "Medium" if momentum_ok else "Low"
    if adx_value >= 25 and score < 70:
        continuation = "Higher (not guaranteed)"
    pullback = "High" if score >= 76 or rsi_value >= 78 or rsi_value <= 22 else ("Medium" if score >= 51 else "Lower")
    rem = f"Daily ATR approx. {daily_atr:,.4g}; remaining move is uncertain" if pd.notna(daily_atr) else "ATR unavailable"
    return {
        "score": score, "label": label, "daily": round(daily_score, 1),
        "swing": round(swing_score, 1), "atr": round(atr_score, 1),
        "continuation": continuation, "pullback": pullback, "remaining": rem,
    }


def timeframe_direction(frames: Dict[str, pd.DataFrame]) -> dict:
    result = {}
    for tf in TIMEFRAMES:
        df = frames.get(tf, pd.DataFrame())
        if df.empty or len(df) < 55:
            result[tf] = 0
        else:
            x = add_indicators(df)
            result[tf] = direction_from_row(x.iloc[-1])
    return result


def score_candidate(frames: Dict[str, pd.DataFrame], directions: dict, maturity: dict) -> dict:
    """Transparent rules-based quality score; 1m is a final gate, not quality bonus."""
    main4 = directions.get("4h", 0)
    main1 = directions.get("1h", 0)
    d1 = directions.get("1d", 0)
    d30 = directions.get("30m", 0)
    d15 = directions.get("15m", 0)
    d5 = directions.get("5m", 0)
    d1m = directions.get("1m", 0)
    if main4 == 0 or main1 == 0 or main4 != main1:
        return {"side": "WAIT", "score": 0, "reason": "4H और 1H की दिशा एक नहीं है", "entry_gate": False}
    side = "BUY" if main1 == 1 else "SELL"
    agree = sum(1 for x in [d1, d30, d15, d5] if x == main1)
    # 100-point transparent diagnostic score; provisional, not a proven probability.
    score = 35  # 4H + 1H core alignment
    score += 15 if d1 == main1 else 0
    score += 15 if d15 == main1 else 0
    score += 10 if d30 == main1 else 0
    score += 10 if d5 == main1 else 0
    h = add_indicators(frames["1h"])
    last = h.iloc[-1]
    rsi_value = float(last["rsi"]) if pd.notna(last["rsi"]) else 50
    adx_value = float(last["adx"]) if pd.notna(last["adx"]) else 0
    score += 10 if adx_value >= 20 else 5 if adx_value >= 15 else 0
    score += 5 if ((side == "BUY" and 45 <= rsi_value <= 70) or (side == "SELL" and 30 <= rsi_value <= 55)) else 0
    score = int(min(score, 100))

    # Final 1-minute closed candle timing gate. Not added to score.
    entry_gate = d1m == main1
    if not entry_gate:
        return {"side": "WAIT", "score": score, "reason": "1M की आखिरी बंद Candle अभी मुख्य दिशा की पुष्टि नहीं करती", "entry_gate": False}
    if score < 80:
        return {"side": "WAIT", "score": score, "reason": f"Quality Score {score}/100 है; 80 की प्रारंभिक सीमा पूरी नहीं", "entry_gate": True}
    if maturity.get("score", 0) >= 76 and maturity.get("pullback") == "High":
        return {"side": "WAIT", "score": score, "reason": "Trend बहुत Mature है और Pullback Risk अधिक है", "entry_gate": True}
    return {"side": side, "score": score, "reason": "सभी मुख्य Signal Gates पास", "entry_gate": True}


def reference_levels(frames: Dict[str, pd.DataFrame], side: str) -> dict:
    h = add_indicators(frames["1h"])
    row = h.iloc[-1]
    entry = float(row["close"])
    a = float(row["atr"]) if pd.notna(row["atr"]) and row["atr"] > 0 else entry * 0.005
    risk = max(1.5 * a, entry * 0.001)
    if side == "BUY":
        sl = entry - risk
        return {"entry": entry, "sl": sl, "t1": entry + 2 * risk, "t2": entry + 3 * risk, "t3": entry + 4 * risk, "atr": a}
    sl = entry + risk
    return {"entry": entry, "sl": sl, "t1": entry - 2 * risk, "t2": entry - 3 * risk, "t3": entry - 4 * risk, "atr": a}


# ----------------------------- BACKTEST / LEARNING ---------------------
def simple_backtest(df: pd.DataFrame, fast: int, slow: int) -> dict:
    """Educational close-to-close EMA crossover test; fees/slippage assumed conservatively."""
    if len(df) < slow + 30:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_pct": 0.0, "max_dd": 0.0}
    x = df.copy()
    x["fast"] = ema(x["close"], fast)
    x["slow"] = ema(x["close"], slow)
    signal = np.where(x["fast"] > x["slow"], 1, np.where(x["fast"] < x["slow"], -1, 0))
    # Position held from next candle; avoid look-ahead. Approx round-trip costs 0.12%.
    pos = pd.Series(signal, index=x.index).shift(1).fillna(0)
    ret = x["close"].pct_change().fillna(0) * pos
    changes = pos.diff().abs().fillna(pos.abs())
    ret = ret - changes * 0.0006
    equity = (1 + ret).cumprod()
    peak = equity.cummax()
    dd = (equity / peak - 1).min()
    trade_returns = ret[changes > 0]
    wins = trade_returns[trade_returns > 0]
    losses = trade_returns[trade_returns < 0]
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else (99.0 if len(wins) else 0.0)
    return {
        "trades": int((changes > 0).sum()),
        "win_rate": float((trade_returns > 0).mean() * 100) if len(trade_returns) else 0.0,
        "profit_factor": pf,
        "net_pct": float((equity.iloc[-1] - 1) * 100),
        "max_dd": float(dd * 100),
    }


def learn_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """Small, transparent EMA parameter research; not a machine-learning guarantee."""
    rows = []
    for fast in [8, 10, 12, 15, 20]:
        for slow in [30, 40, 50, 60]:
            if fast >= slow:
                continue
            split = int(len(df) * 0.70)
            train = simple_backtest(df.iloc[:split].reset_index(drop=True), fast, slow)
            test = simple_backtest(df.iloc[split:].reset_index(drop=True), fast, slow)
            rows.append({
                "Fast EMA": fast, "Slow EMA": slow,
                "Train Net %": round(train["net_pct"], 2),
                "Test Net %": round(test["net_pct"], 2),
                "Test Win %": round(test["win_rate"], 1),
                "Test PF": round(test["profit_factor"], 2),
                "Test Max DD %": round(test["max_dd"], 2),
                "Test Trades": test["trades"],
                "Research Status": "Review" if test["trades"] >= 5 and test["net_pct"] > 0 and test["profit_factor"] > 1 else "Not approved",
            })
    return pd.DataFrame(rows).sort_values(["Research Status", "Test PF", "Test Net %"], ascending=[True, False, False])


# ----------------------------- SIGNAL HISTORY --------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                score INTEGER NOT NULL,
                entry REAL,
                stop_loss REAL,
                target1 REAL,
                target2 REAL,
                target3 REAL,
                reason TEXT
            )
        """)
        con.commit()


def save_signal(symbol: str, result: dict, levels: dict):
    # Save only a new selected signal state; never sends an order.
    with sqlite3.connect(DB_PATH) as con:
        latest = con.execute(
            "SELECT symbol, side, score FROM signals ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest and latest[0] == symbol and latest[1] == result["side"] and latest[2] == result["score"]:
            return
        con.execute(
            """INSERT INTO signals
            (created_at,symbol,side,score,entry,stop_loss,target1,target2,target3,reason)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                symbol, result["side"], int(result["score"]),
                levels["entry"], levels["sl"], levels["t1"], levels["t2"], levels["t3"],
                result["reason"],
            ),
        )
        con.commit()


def get_signal_history() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query("SELECT * FROM signals ORDER BY id DESC LIMIT 30", con)


# ----------------------------- MAIN SCAN -------------------------------
init_db()

with st.sidebar:
    st.header("Scanner Settings")
    st.write("**Fixed Contracts**")
    for s in SYMBOLS:
        st.write("• " + s)
    refresh = st.button("🔄 अभी Scan करें", use_container_width=True)
    st.caption("Auto trading: हमेशा बंद।")
    st.caption("Signal quality threshold: 80/100 (प्रारंभिक नियम)")
    st.caption("SuperTrend reference: ATR 10, Multiplier 3.0")
    st.caption("Daily candle: Delta API की 1d candle boundary")

if refresh:
    st.cache_data.clear()

st.info("चारों कॉन्ट्रैक्ट का डेटा लिया जा रहा है। किसी भी प्रकार का Order नहीं भेजा जाएगा।")

products = {}
tickers = {}
scan_rows = []
details = {}
errors = []

try:
    products = get_live_products()
except Exception as e:
    errors.append(f"Products API: {e}")

for symbol in SYMBOLS:
    try:
        if symbol not in products:
            raise RuntimeError("यह Symbol Delta की live perpetual product list में नहीं मिला। Contract availability जाँचें।")
        tickers[symbol] = get_ticker(symbol)
        frames = {}
        for tf in TIMEFRAMES:
            # More history for slower timeframes; API limit per call <= 2000.
            count = 260 if tf in ["1d", "4h", "1h"] else 180
            raw = get_candles(symbol, tf, count)
            closed = candle_is_closed(raw, tf)
            if len(closed) < 60:
                raise RuntimeError(f"{tf} में पर्याप्त बंद candles नहीं मिलीं ({len(closed)}).")
            frames[tf] = closed
        dirs = timeframe_direction(frames)
        maturity = maturity_analysis(frames["1d"], frames["1h"])
        candidate = score_candidate(frames, dirs, maturity)
        ticker = tickers[symbol]
        price = float(ticker.get("close") or ticker.get("last_price") or frames["1m"].iloc[-1]["close"])
        levels = reference_levels(frames, candidate["side"]) if candidate["side"] in ["BUY", "SELL"] else {}
        details[symbol] = {
            "frames": frames, "directions": dirs, "maturity": maturity,
            "candidate": candidate, "levels": levels, "ticker": ticker, "price": price,
        }
        scan_rows.append({
            "Contract": symbol,
            "Price": price,
            "1D": "BUY" if dirs["1d"] == 1 else "SELL" if dirs["1d"] == -1 else "NEUTRAL",
            "4H": "BUY" if dirs["4h"] == 1 else "SELL" if dirs["4h"] == -1 else "NEUTRAL",
            "1H": "BUY" if dirs["1h"] == 1 else "SELL" if dirs["1h"] == -1 else "NEUTRAL",
            "30M": "BUY" if dirs["30m"] == 1 else "SELL" if dirs["30m"] == -1 else "NEUTRAL",
            "15M": "BUY" if dirs["15m"] == 1 else "SELL" if dirs["15m"] == -1 else "NEUTRAL",
            "5M": "BUY" if dirs["5m"] == 1 else "SELL" if dirs["5m"] == -1 else "NEUTRAL",
            "1M": "BUY" if dirs["1m"] == 1 else "SELL" if dirs["1m"] == -1 else "NEUTRAL",
            "Maturity": maturity.get("score"),
            "Quality": candidate["score"],
            "Candidate": candidate["side"],
            "Reason": candidate["reason"],
        })
    except Exception as e:
        errors.append(f"{symbol}: {e}")

if errors:
    with st.expander(f"Data warnings / unavailable contracts ({len(errors)})", expanded=True):
        for err in errors:
            st.warning(err)

if scan_rows:
    scan_df = pd.DataFrame(scan_rows)
    st.subheader("चारों कॉन्ट्रैक्ट का Combined Scan")
    st.dataframe(scan_df, use_container_width=True, hide_index=True)

    eligible = [
        (sym, d["candidate"]["score"])
        for sym, d in details.items()
        if d["candidate"]["side"] in ["BUY", "SELL"]
    ]
    eligible.sort(key=lambda x: x[1], reverse=True)
    selected_symbol = eligible[0][0] if eligible else None

    if selected_symbol:
        selected = details[selected_symbol]
        cand = selected["candidate"]
        levels = selected["levels"]
        save_signal(selected_symbol, cand, levels)
        st.markdown("---")
        st.subheader("🏆 FINAL BEST SIGNAL")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Contract", selected_symbol)
        col2.metric("Signal", cand["side"])
        col3.metric("Quality Score", f'{cand["score"]}/100')
        col4.metric("Trend Maturity", f'{selected["maturity"]["score"]}/100')
        st.success("यह सबसे अच्छा योग्य संभावित Signal है — केवल विश्लेषण। कोई Order नहीं लगाया गया है।")

        lcol, rcol = st.columns([1, 1])
        with lcol:
            st.markdown("#### Analytical Price Levels")
            level_df = pd.DataFrame([
                ["Entry Reference", levels["entry"]],
                ["Stop Loss Reference", levels["sl"]],
                ["Target 1 (2R)", levels["t1"]],
                ["Target 2 (3R)", levels["t2"]],
                ["Target 3 (4R)", levels["t3"]],
            ], columns=["Level", "Price"])
            st.dataframe(level_df, use_container_width=True, hide_index=True)
            st.caption("ये केवल गणितीय Reference Levels हैं; Exchange को कोई Order नहीं भेजा जाता।")
        with rcol:
            st.markdown("#### Trend Maturity Breakdown")
            m = selected["maturity"]
            st.metric("Classification", m["label"])
            st.write(f"Daily Movement: **{m['daily']} / 100** (Weight 30%)")
            st.write(f"Swing Movement: **{m['swing']} / 100** (Weight 40%)")
            st.write(f"ATR Movement: **{m['atr']} / 100** (Weight 30%)")
            st.write(f"Trend Continuation: **{m['continuation']}**")
            st.write(f"Pullback Risk: **{m['pullback']}**")
            st.caption(m["remaining"])

        st.markdown("#### सात Timeframes की Confirmation")
        tf_rows = []
        for tf in TIMEFRAMES:
            val = selected["directions"][tf]
            tf_rows.append({"Timeframe": TF_LABELS[tf], "Direction": "BUY" if val == 1 else "SELL" if val == -1 else "NEUTRAL"})
        st.dataframe(pd.DataFrame(tf_rows), use_container_width=True, hide_index=True)

        # Chart of selected contract, 1H
        chart_df = selected["frames"]["1h"].tail(100)
        fig = go.Figure(data=[go.Candlestick(
            x=chart_df["time"], open=chart_df["open"], high=chart_df["high"],
            low=chart_df["low"], close=chart_df["close"], name="1H"
        )])
        fig.update_layout(title=f"{selected_symbol} — 1H Closed Candles", height=420, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("NO TRADE / NO QUALIFIED SIGNAL — चारों में से कोई भी सभी Final Conditions पास नहीं कर पाया।")
        st.caption("कम Score वाले Coin को जबरदस्ती Final Signal नहीं बनाया जाएगा।")

    st.markdown("---")
    st.subheader("पिछला Signal और Signal History")
    hist = get_signal_history()
    if not hist.empty:
        prev = hist.iloc[1] if len(hist) > 1 else None
        if prev is not None:
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Previous Contract", prev["symbol"])
            p2.metric("Previous Signal", prev["side"])
            p3.metric("Previous Score", f'{prev["score"]}/100')
            p4.metric("Previous Time", str(prev["created_at"])[:19])
            st.write(
                f"Previous Entry Reference: **{prev['entry']}** | "
                f"SL: **{prev['stop_loss']}** | T1: **{prev['target1']}**"
            )
            st.caption(f"Previous record reason: {prev['reason']}")
        else:
            st.info("अभी पहला Signal Record है; इससे पहले कोई Previous Signal उपलब्ध नहीं है।")
        st.dataframe(hist, use_container_width=True, hide_index=True)
    else:
        st.info("अभी कोई योग्य Signal History में दर्ज नहीं हुआ है।")

    st.markdown("---")
    st.subheader("Fixed Strategy Research / Self-Learning Research")
    st.caption(
        "यह सीमित EMA parameter research module है। यह अपने आप live strategy को approve नहीं करता। "
        "Out-of-sample परिणाम केवल research evidence हैं; इन्हें लाभ की गारंटी न मानें।"
    )
    research_symbol = st.selectbox("Research के लिए Contract", SYMBOLS, index=2)
    if st.button("Historical Strategy Research चलाएँ"):
        try:
            research_df = get_candles(research_symbol, "1h", 500)
            research_df = candle_is_closed(research_df, "1h")
            learned = learn_candidates(research_df)
            st.dataframe(learned.head(12), use_container_width=True, hide_index=True)
            st.warning("यह research table है; कोई Strategy live scanner में स्वतः activate नहीं होती।")
        except Exception as e:
            st.error(f"Research data error: {e}")

else:
    st.warning("Market data उपलब्ध नहीं हुआ। ऊपर Data warnings देखें।")

st.markdown("---")
st.subheader("System Rules — स्पष्ट जानकारी")
st.markdown("""
- Fixed contracts: **XAUTUSD, PAXGUSD, BTCUSD, ETHUSD**।
- Delta Exchange India की live perpetual product list से contract availability जाँची जाती है।
- Timeframes: **1D, 4H, 1H, 30M, 15M, 5M, 1M**।
- Main trend gate: **4H और 1H एक दिशा में**।
- 15M confirmation आवश्यक; 30M और 5M quality को support करते हैं।
- 1M की **बंद candle** अंतिम timing gate है; 1M quality score में अतिरिक्त अंक नहीं जोड़ता।
- Trend Maturity: Daily Movement 30% + Swing Movement 40% + ATR Movement 30%।
- शुरुआती quality threshold 80/100 है; यह validated win probability नहीं है।
- पिछला Signal SQLite में स्थानीय रूप से रिकॉर्ड होता है।
- Daily OHLC Delta के `1d` candle endpoint से आता है; ऐप मनमाने IST midnight को daily boundary नहीं बनाता।
- डेटा/API fail या candles अपर्याप्त हों तो Signal नहीं बनाया जाएगा।
- Entry, SL और Targets केवल analysis references हैं।
""")

st.caption(
    "Risk note: यह तकनीकी विश्लेषण उपकरण है, निवेश सलाह नहीं। Signals गलत हो सकते हैं। "
    "Backtest/indicator scores भविष्य के परिणाम की गारंटी नहीं देते।"
)
