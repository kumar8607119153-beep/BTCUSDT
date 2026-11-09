# ============================================================
# SANJAY RANA - 1H COMBINED PRICE ENGINE (READ-ONLY TEST VERSION)
# Delta Exchange India | XRPUSD | SuperTrend ATR10 x 2.0 | HL2
#
# IMPORTANT:
# - This file is READ-ONLY / TEST MODE.
# - It fetches public market candles and ticker only.
# - It DOES NOT connect to private APIs, place orders, cancel orders,
#   close positions, or modify any existing account/trade.
# - Validate signals and historical behavior before adding live execution.
# ============================================================

import time
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import streamlit as st

BASE_URL = "https://api.india.delta.exchange"
SYMBOL = "XRPUSD"
TIMEFRAME = "1h"
CANDLE_SECONDS = 3600
ATR_PERIOD = 10
SUPERTREND_MULTIPLIER = 2.0
IST = ZoneInfo("Asia/Kolkata")
USER_AGENT = "Sanjay-Rana-Combined-Price-Test/1.0"

st.set_page_config(
    page_title="SANJAY RANA - Combined Price Engine",
    page_icon="📊",
    layout="wide",
)

# ------------------------- API (PUBLIC ONLY) -------------------------

@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles():
    """Fetch up to 2,000 1H candles from Delta public market data."""
    end_ts = int(time.time())
    rows = []
    chunk_seconds = 500 * CANDLE_SECONDS

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    for _ in range(4):
        start_ts = end_ts - chunk_seconds
        response = session.get(
            f"{BASE_URL}/v2/history/candles",
            params={
                "symbol": SYMBOL,
                "resolution": TIMEFRAME,
                "start": start_ts,
                "end": end_ts,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise RuntimeError(f"Delta candle API did not confirm success: {payload}")
        result = payload.get("result", [])
        if not isinstance(result, list):
            raise RuntimeError("Delta candle API returned an unexpected result format.")
        rows.extend(result)
        end_ts = start_ts
        if not result:
            break

    parsed = []
    for candle in rows:
        try:
            parsed.append({
                "time": int(candle["time"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", 0) or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue

    df = pd.DataFrame(parsed)
    if df.empty:
        return df

    df = (
        df.drop_duplicates("time")
          .sort_values("time")
          .reset_index(drop=True)
    )

    # Never calculate a signal from a still-forming 1H candle.
    now_ts = int(time.time())
    last_start = int(df["time"].iloc[-1])
    if last_start > 10_000_000_000:
        last_start //= 1000
    if last_start + CANDLE_SECONDS > now_ts:
        df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


@st.cache_data(ttl=5, show_spinner=False)
def fetch_ticker():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    response = session.get(
        f"{BASE_URL}/v2/tickers/{SYMBOL}",
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Delta ticker API did not confirm success.")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Delta ticker response format was not recognized.")
    for key in ("close", "last_price", "mark_price", "spot_price", "price"):
        try:
            value = float(result.get(key))
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return None


# ------------------------- INDICATOR CALCULATIONS -------------------------

def rma(series, period):
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def calculate_supertrend(df, period=10, multiplier=2.0):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = pd.Series(float("nan"), index=df.index, dtype=float)
    if len(df) >= period:
        atr.iloc[period - 1] = tr.iloc[:period].mean()
        for i in range(period, len(df)):
            atr.iloc[i] = (
                atr.iloc[i - 1] * (period - 1) + tr.iloc[i]
            ) / period

    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    final_upper = pd.Series(float("nan"), index=df.index, dtype=float)
    final_lower = pd.Series(float("nan"), index=df.index, dtype=float)
    st_line = pd.Series(float("nan"), index=df.index, dtype=float)
    st_dir = pd.Series(0, index=df.index, dtype=int)

    previous_upper = previous_lower = previous_st = None
    for i in range(len(df)):
        if pd.isna(atr.iloc[i]):
            continue

        bu = float(basic_upper.iloc[i])
        bl = float(basic_lower.iloc[i])
        c = float(close.iloc[i])

        if previous_upper is None:
            fu, fl = bu, bl
        else:
            pc = float(close.iloc[i - 1])
            fu = bu if (bu < previous_upper or pc > previous_upper) else previous_upper
            fl = bl if (bl > previous_lower or pc < previous_lower) else previous_lower

        if previous_st is None:
            direction = 1
            line = fu
        elif previous_st == previous_upper:
            if c <= fu:
                direction, line = 1, fu
            else:
                direction, line = -1, fl
        else:
            if c >= fl:
                direction, line = -1, fl
            else:
                direction, line = 1, fu

        final_upper.iloc[i] = fu
        final_lower.iloc[i] = fl
        st_line.iloc[i] = line
        st_dir.iloc[i] = direction
        previous_upper, previous_lower, previous_st = fu, fl, line

    # In this engine: -1 = bullish/BUY, +1 = bearish/SELL.
    out = df.copy()
    out["ATR_10"] = atr
    out["SUPERTREND"] = st_line
    out["ST_RAW_DIRECTION"] = st_dir
    out["ST_DIRECTION"] = st_dir.map({-1: "BUY", 1: "SELL"}).fillna("")
    return out


def calculate_parabolic_sar(df, step=0.02, max_step=0.20):
    high = df["high"].to_list()
    low = df["low"].to_list()
    close = df["close"].to_list()
    n = len(df)
    sar = [float("nan")] * n
    trend = [0] * n
    if n < 3:
        return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)

    bull = close[1] >= close[0]
    af = step
    ep = high[0] if bull else low[0]
    sar[0] = low[0] if bull else high[0]
    trend[0] = 1 if bull else -1

    for i in range(1, n):
        prev_sar = sar[i - 1]
        candidate = prev_sar + af * (ep - prev_sar)

        if bull:
            candidate = min(candidate, low[i - 1])
            if i >= 2:
                candidate = min(candidate, low[i - 2])
            if low[i] < candidate:
                bull = False
                candidate = ep
                ep = low[i]
                af = step
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(max_step, af + step)
        else:
            candidate = max(candidate, high[i - 1])
            if i >= 2:
                candidate = max(candidate, high[i - 2])
            if high[i] > candidate:
                bull = True
                candidate = ep
                ep = high[i]
                af = step
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(max_step, af + step)

        sar[i] = candidate
        trend[i] = 1 if bull else -1

    return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)


def calculate_volume_profile_poc(df, window=50, bins=24):
    """Rolling approximate POC using candle typical price weighted by volume."""
    result = pd.Series(float("nan"), index=df.index, dtype=float)
    for i in range(window - 1, len(df)):
        part = df.iloc[i - window + 1:i + 1]
        typical = ((part["high"] + part["low"] + part["close"]) / 3.0).to_numpy()
        volume = part["volume"].clip(lower=0).to_numpy()
        if len(typical) == 0 or float(volume.sum()) <= 0:
            continue
        lo, hi = float(part["low"].min()), float(part["high"].max())
        if not math.isfinite(lo + hi):
            continue
        if hi <= lo:
            result.iloc[i] = float(typical[-1])
            continue
        edges = pd.Series([lo + (hi - lo) * j / bins for j in range(bins + 1)]).to_numpy()
        idx = pd.cut(typical, bins=edges, labels=False, include_lowest=True)
        hist = [0.0] * bins
        for k, v in zip(idx, volume):
            if pd.notna(k):
                hist[int(k)] += float(v)
        best = max(range(bins), key=lambda j: hist[j])
        result.iloc[i] = (edges[best] + edges[best + 1]) / 2.0
    return result


def weighted_median(values, weights):
    pairs = []
    for value, weight in zip(values, weights):
        try:
            v, w = float(value), float(weight)
            if math.isfinite(v) and math.isfinite(w) and w > 0:
                pairs.append((v, w))
        except (TypeError, ValueError):
            continue
    if not pairs:
        return float("nan")
    pairs.sort(key=lambda item: item[0])
    half = sum(w for _, w in pairs) / 2.0
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= half:
            return value
    return pairs[-1][0]


def build_indicators(raw):
    df = raw.copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    typical = (high + low + close) / 3.0

    # 1. SuperTrend: mandatory direction + one of the Combined Price inputs.
    df = calculate_supertrend(df, ATR_PERIOD, SUPERTREND_MULTIPLIER)

    # 2. EMA 9 / 20 / 50.
    df["EMA_9"] = close.ewm(span=9, adjust=False, min_periods=9).mean()
    df["EMA_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    df["EMA_50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()

    # 3. RSI 14.
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = rma(gain, 14)
    avg_loss = rma(loss, 14)
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))
    df.loc[(avg_loss == 0) & (avg_gain > 0), "RSI_14"] = 100.0
    df.loc[(avg_loss == 0) & (avg_gain == 0), "RSI_14"] = 50.0

    # 4. MACD 12,26,9.
    df["MACD_LINE"] = close.ewm(span=12, adjust=False, min_periods=12).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    df["MACD_SIGNAL"] = df["MACD_LINE"].ewm(span=9, adjust=False, min_periods=9).mean()
    df["MACD_HIST"] = df["MACD_LINE"] - df["MACD_SIGNAL"]

    # 5. ADX + DMI 14.
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]
    # Use standard true range for DMI denominator.
    prev_c = close.shift(1)
    tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    atr_dmi = rma(tr, 14)
    df["PLUS_DI"] = 100.0 * rma(plus_dm, 14) / atr_dmi.replace(0, float("nan"))
    df["MINUS_DI"] = 100.0 * rma(minus_dm, 14) / atr_dmi.replace(0, float("nan"))
    dx = 100.0 * (df["PLUS_DI"] - df["MINUS_DI"]).abs() / (df["PLUS_DI"] + df["MINUS_DI"]).replace(0, float("nan"))
    df["ADX_14"] = rma(dx, 14)

    # 6. VWAP: rolling 24 completed hourly candles (not exchange session VWAP).
    pv = typical * volume.clip(lower=0)
    df["VWAP_24"] = pv.rolling(24, min_periods=12).sum() / volume.clip(lower=0).rolling(24, min_periods=12).sum().replace(0, float("nan"))

    # 7. OBV.
    obv_step = volume.where(close > close.shift(1), -volume.where(close < close.shift(1), 0.0)).fillna(0.0)
    df["OBV"] = obv_step.cumsum()
    df["OBV_EMA_20"] = df["OBV"].ewm(span=20, adjust=False, min_periods=20).mean()

    # 8. Bollinger Bands 20,2.
    df["BB_MID"] = close.rolling(20, min_periods=20).mean()
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    df["BB_UPPER"] = df["BB_MID"] + 2.0 * bb_std
    df["BB_LOWER"] = df["BB_MID"] - 2.0 * bb_std

    # 9. ATR 14 (volatility measurement).
    df["ATR_14"] = rma(tr, 14)

    # 10. Ichimoku Kijun (26).
    df["KIJUN_26"] = (high.rolling(26, min_periods=26).max() + low.rolling(26, min_periods=26).min()) / 2.0
    df["TENKAN_9"] = (high.rolling(9, min_periods=9).max() + low.rolling(9, min_periods=9).min()) / 2.0

    # 11. Stochastic RSI 14,14,3,3.
    rsi_min = df["RSI_14"].rolling(14, min_periods=14).min()
    rsi_max = df["RSI_14"].rolling(14, min_periods=14).max()
    stoch_raw = 100.0 * (df["RSI_14"] - rsi_min) / (rsi_max - rsi_min).replace(0, float("nan"))
    df["STOCH_RSI_K"] = stoch_raw.rolling(3, min_periods=3).mean()
    df["STOCH_RSI_D"] = df["STOCH_RSI_K"].rolling(3, min_periods=3).mean()

    # 12. MFI 14.
    raw_money = typical * volume.clip(lower=0)
    pos_money = raw_money.where(typical > typical.shift(1), 0.0)
    neg_money = raw_money.where(typical < typical.shift(1), 0.0)
    pos_sum = pos_money.rolling(14, min_periods=14).sum()
    neg_sum = neg_money.rolling(14, min_periods=14).sum()
    money_ratio = pos_sum / neg_sum.replace(0, float("nan"))
    df["MFI_14"] = 100.0 - (100.0 / (1.0 + money_ratio))
    df.loc[(neg_sum == 0) & (pos_sum > 0), "MFI_14"] = 100.0
    df.loc[(neg_sum == 0) & (pos_sum == 0), "MFI_14"] = 50.0

    # 13. CMF 20.
    mf_multiplier = ((close - low) - (high - close)) / (high - low).replace(0, float("nan"))
    mf_volume = mf_multiplier.fillna(0.0) * volume.clip(lower=0)
    df["CMF_20"] = mf_volume.rolling(20, min_periods=20).sum() / volume.clip(lower=0).rolling(20, min_periods=20).sum().replace(0, float("nan"))

    # 14. Parabolic SAR.
    df["PARABOLIC_SAR"], df["PSAR_TREND"] = calculate_parabolic_sar(df)

    # 15. CCI 20.
    tp_sma = typical.rolling(20, min_periods=20).mean()
    mean_dev = typical.rolling(20, min_periods=20).apply(lambda x: float((abs(x - x.mean())).mean()), raw=True)
    df["CCI_20"] = (typical - tp_sma) / (0.015 * mean_dev.replace(0, float("nan")))

    # 16. Williams %R 14.
    hh14 = high.rolling(14, min_periods=14).max()
    ll14 = low.rolling(14, min_periods=14).min()
    df["WILLIAMS_R"] = -100.0 * (hh14 - close) / (hh14 - ll14).replace(0, float("nan"))

    # 17. ROC 12.
    df["ROC_12"] = close.pct_change(12) * 100.0

    # 18. Keltner Channel 20, ATR 10, 2x.
    df["KELTNER_MID"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    df["KELTNER_UPPER"] = df["KELTNER_MID"] + 2.0 * df["ATR_14"]
    df["KELTNER_LOWER"] = df["KELTNER_MID"] - 2.0 * df["ATR_14"]

    # 19. Donchian Channel 20.
    df["DONCHIAN_UPPER"] = high.rolling(20, min_periods=20).max()
    df["DONCHIAN_LOWER"] = low.rolling(20, min_periods=20).min()
    df["DONCHIAN_MID"] = (df["DONCHIAN_UPPER"] + df["DONCHIAN_LOWER"]) / 2.0

    # 20. Rolling Volume Profile POC approximation (50 bars).
    df["VOLUME_PROFILE_POC"] = calculate_volume_profile_poc(df, 50, 24)

    # One final price from nine price-level indicators.
    # We use a weighted median rather than a plain average to reduce the
    # effect of one unusually distant level. SuperTrend has mandatory weight.
    price_inputs = [
        ("SUPERTREND", 2.0),
        ("EMA_20", 1.5),
        ("VWAP_24", 1.25),
        ("KIJUN_26", 1.0),
        ("PARABOLIC_SAR", 1.0),
        ("BB_MID", 1.0),
        ("KELTNER_MID", 1.0),
        ("DONCHIAN_MID", 0.75),
        ("VOLUME_PROFILE_POC", 0.75),
    ]
    combined = []
    for i in df.index:
        vals = [df.at[i, col] for col, _weight in price_inputs]
        weights = [weight for _col, weight in price_inputs]
        combined.append(weighted_median(vals, weights))
    df["COMBINED_PRICE"] = combined

    # Ten independent direction votes. SuperTrend is a hard gate separately.
    df["VOTE_EMA"] = ""
    df.loc[df["EMA_9"] > df["EMA_20"], "VOTE_EMA"] = "BUY"
    df.loc[df["EMA_9"] < df["EMA_20"], "VOTE_EMA"] = "SELL"
    df["VOTE_DMI"] = ""
    dmi_valid = df["ADX_14"] >= 18.0
    df.loc[dmi_valid & (df["PLUS_DI"] > df["MINUS_DI"]), "VOTE_DMI"] = "BUY"
    df.loc[dmi_valid & (df["MINUS_DI"] > df["PLUS_DI"]), "VOTE_DMI"] = "SELL"
    df["VOTE_RSI"] = ""
    df.loc[df["RSI_14"] > 50.0, "VOTE_RSI"] = "BUY"
    df.loc[df["RSI_14"] < 50.0, "VOTE_RSI"] = "SELL"
    df["VOTE_MACD"] = ""
    df.loc[df["MACD_LINE"] > df["MACD_SIGNAL"], "VOTE_MACD"] = "BUY"
    df.loc[df["MACD_LINE"] < df["MACD_SIGNAL"], "VOTE_MACD"] = "SELL"
    df["VOTE_CCI"] = ""
    df.loc[df["CCI_20"] > 0, "VOTE_CCI"] = "BUY"
    df.loc[df["CCI_20"] < 0, "VOTE_CCI"] = "SELL"
    df["VOTE_ROC"] = ""
    df.loc[df["ROC_12"] > 0, "VOTE_ROC"] = "BUY"
    df.loc[df["ROC_12"] < 0, "VOTE_ROC"] = "SELL"
    df["VOTE_OBV"] = ""
    df.loc[df["OBV"] > df["OBV_EMA_20"], "VOTE_OBV"] = "BUY"
    df.loc[df["OBV"] < df["OBV_EMA_20"], "VOTE_OBV"] = "SELL"
    df["VOTE_MFI"] = ""
    df.loc[df["MFI_14"] > 50.0, "VOTE_MFI"] = "BUY"
    df.loc[df["MFI_14"] < 50.0, "VOTE_MFI"] = "SELL"
    df["VOTE_CMF"] = ""
    df.loc[df["CMF_20"] > 0, "VOTE_CMF"] = "BUY"
    df.loc[df["CMF_20"] < 0, "VOTE_CMF"] = "SELL"
    df["VOTE_STOCH_RSI"] = ""
    stoch_bull = (df["STOCH_RSI_K"] > df["STOCH_RSI_D"]) & (df["STOCH_RSI_K"] > 50)
    stoch_bear = (df["STOCH_RSI_K"] < df["STOCH_RSI_D"]) & (df["STOCH_RSI_K"] < 50)
    df.loc[stoch_bull, "VOTE_STOCH_RSI"] = "BUY"
    df.loc[stoch_bear, "VOTE_STOCH_RSI"] = "SELL"

    vote_cols = [
        "VOTE_EMA", "VOTE_DMI", "VOTE_RSI", "VOTE_MACD", "VOTE_CCI",
        "VOTE_ROC", "VOTE_OBV", "VOTE_MFI", "VOTE_CMF", "VOTE_STOCH_RSI",
    ]
    buy_scores, sell_scores, valid_counts = [], [], []
    for i in df.index:
        votes = [str(df.at[i, c]) for c in vote_cols]
        valid = [v for v in votes if v in ("BUY", "SELL")]
        buy_scores.append(sum(v == "BUY" for v in valid))
        sell_scores.append(sum(v == "SELL" for v in valid))
        valid_counts.append(len(valid))
    df["BUY_SCORE"] = buy_scores
    df["SELL_SCORE"] = sell_scores
    df["VALID_VOTES"] = valid_counts

    # Option 3: price crossing + mandatory SuperTrend direction + broad
    # indicator agreement + at least one agreeing volume vote.
    df["PRICE_CROSS"] = ""
    prev_close = df["close"].shift(1)
    prev_combined = df["COMBINED_PRICE"].shift(1)
    cross_up = (prev_close <= prev_combined) & (df["close"] > df["COMBINED_PRICE"])
    cross_down = (prev_close >= prev_combined) & (df["close"] < df["COMBINED_PRICE"])
    df.loc[cross_up, "PRICE_CROSS"] = "BUY"
    df.loc[cross_down, "PRICE_CROSS"] = "SELL"

    volume_cols = ["VOTE_OBV", "VOTE_MFI", "VOTE_CMF"]
    signal_events = []
    for i in df.index:
        cross = str(df.at[i, "PRICE_CROSS"])
        st_dir = str(df.at[i, "ST_DIRECTION"])
        if cross not in ("BUY", "SELL") or st_dir != cross:
            signal_events.append("")
            continue

        valid = int(df.at[i, "VALID_VOTES"])
        aligned = int(df.at[i, "BUY_SCORE"] if cross == "BUY" else df.at[i, "SELL_SCORE"])
        volume_aligned = sum(str(df.at[i, c]) == cross for c in volume_cols)

        # Need at least 8 valid votes, >=70% in the proposed direction,
        # and at least one agreeing volume confirmation.
        if valid >= 8 and aligned / valid >= 0.70 and volume_aligned >= 1:
            signal_events.append(cross)
        else:
            signal_events.append("")
    df["SIGNAL_EVENT"] = signal_events

    # Persistent signal state: WAIT bars do not erase the last confirmed
    # direction. An opposite confirmed event changes the active direction.
    active = []
    state = ""
    for event in df["SIGNAL_EVENT"]:
        if event in ("BUY", "SELL"):
            state = event
        active.append(state)
    df["ACTIVE_SIGNAL"] = active

    return df, price_inputs, vote_cols


# ------------------------- FORMAT / UI -------------------------

def fmt(value, digits=5):
    try:
        v = float(value)
        if not math.isfinite(v):
            return "—"
        return f"{v:,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def ist_time(epoch):
    try:
        value = float(epoch)
        if value > 10_000_000_000:
            value /= 1000.0
        return datetime.fromtimestamp(value, timezone.utc).astimezone(IST).strftime("%d-%m-%Y %H:%M")
    except Exception:
        return "—"


st.markdown(
    """
    <div style="padding:16px;border:2px solid #38bdf8;border-radius:14px;
    background:linear-gradient(135deg,#0b1220,#172554);color:#f8fafc;">
      <div style="font-size:22px;font-weight:950;color:#7dd3fc;">
        SANJAY RANA — 1H COMBINED PRICE ENGINE
      </div>
      <div style="font-size:13px;font-weight:800;margin-top:5px;">
        DELTA EXCHANGE INDIA · XRPUSD · SUPERTREND ATR 10 / MULTIPLIER 2.0 / HL2
      </div>
      <div style="font-size:12px;color:#fbbf24;margin-top:8px;font-weight:900;">
        READ-ONLY TEST MODE — कोई वास्तविक ऑर्डर नहीं भेजा जाता
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

top_a, top_b, top_c = st.columns(3)
with top_a:
    st.metric("MAIN TIMEFRAME", "1H")
with top_b:
    st.metric("SUPERTREND", "ATR 10 × 2.0")
with top_c:
    st.metric("PRICE SOURCE", "HL2")

if st.button("🔄 Refresh Public Market Data", use_container_width=True):
    fetch_candles.clear()
    fetch_ticker.clear()
    st.rerun()

try:
    with st.spinner("Delta Exchange से वास्तविक 1H candles प्राप्त हो रही हैं..."):
        raw_df = fetch_candles()
        live_price = fetch_ticker()
except Exception as exc:
    st.error(f"Delta public market data उपलब्ध नहीं है: {type(exc).__name__}: {exc}")
    st.stop()

if raw_df.empty or len(raw_df) < 120:
    st.error(f"पर्याप्त बंद 1H candles उपलब्ध नहीं हैं। प्राप्त candles: {len(raw_df)}")
    st.stop()

df, price_inputs, vote_cols = build_indicators(raw_df)
last = df.iloc[-1]
previous = df.iloc[-2]

if pd.isna(last["COMBINED_PRICE"]) or not str(last["ST_DIRECTION"]):
    st.error("Combined Price या SuperTrend अभी गणना के लिए उपलब्ध नहीं है।")
    st.stop()

active_signal = str(last["ACTIVE_SIGNAL"] or "WAIT")
latest_event_rows = df[df["SIGNAL_EVENT"].isin(["BUY", "SELL"])]
latest_event = latest_event_rows.iloc[-1] if not latest_event_rows.empty else None
latest_event_signal = str(latest_event["SIGNAL_EVENT"]) if latest_event is not None else "WAIT"

price_cross = str(last["PRICE_CROSS"] or "NONE")
st_direction = str(last["ST_DIRECTION"] or "WAIT")
buy_score = int(last["BUY_SCORE"])
sell_score = int(last["SELL_SCORE"])
valid_votes = int(last["VALID_VOTES"])
score_text = f"BUY {buy_score}/{valid_votes} · SELL {sell_score}/{valid_votes}"

signal_color = "#22c55e" if latest_event_signal == "BUY" else "#ef4444" if latest_event_signal == "SELL" else "#facc15"
signal_label = latest_event_signal if latest_event_signal in ("BUY", "SELL") else "WAIT — NO NEW CONFIRMED CROSS"

st.markdown(
    f"""
    <div style="margin:12px 0;padding:16px;border:2px solid {signal_color};
    border-radius:14px;background:#111827;color:#f8fafc;">
      <div style="font-size:13px;color:#cbd5e1;font-weight:800;">LATEST CONFIRMED SIGNAL EVENT</div>
      <div style="font-size:30px;color:{signal_color};font-weight:1000;margin:4px 0;">{signal_label}</div>
      <div style="font-size:13px;color:#cbd5e1;">Active direction: <b>{active_signal}</b>
      &nbsp; | &nbsp; Vote score: <b>{score_text}</b></div>
    </div>
    """,
    unsafe_allow_html=True,
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("LIVE DELTA PRICE", fmt(live_price, 5))
m2.metric("LAST CONFIRMED CLOSE", fmt(last["close"], 5))
m3.metric("FINAL COMBINED PRICE", fmt(last["COMBINED_PRICE"], 5))
m4.metric("SUPERTREND LINE", fmt(last["SUPERTREND"], 5))

c1, c2, c3 = st.columns(3)
c1.metric("PRICE CROSS", price_cross)
c2.metric("SUPERTREND DIRECTION", st_direction)
c3.metric("LAST CANDLE (IST)", ist_time(last["time"]))

st.subheader("Combined Price — घटक")
price_rows = []
for col, weight in price_inputs:
    value = last[col]
    price_rows.append({
        "Price Indicator": col,
        "Calculated Price": fmt(value, 6),
        "Weight": weight,
        "Status": "IN CALCULATION" if pd.notna(value) else "WARM-UP / UNAVAILABLE",
    })
st.dataframe(pd.DataFrame(price_rows), use_container_width=True, hide_index=True)

st.subheader("20 Indicator Status")
indicator_rows = [
    ("1. SuperTrend", st_direction, fmt(last["SUPERTREND"], 6)),
    ("2. EMA 9/20", str(last["VOTE_EMA"] or "WAIT"), f"EMA9 {fmt(last['EMA_9'], 6)} · EMA20 {fmt(last['EMA_20'], 6)}"),
    ("3. ADX + DMI", str(last["VOTE_DMI"] or "WAIT"), f"ADX {fmt(last['ADX_14'], 2)} · +DI {fmt(last['PLUS_DI'], 2)} · -DI {fmt(last['MINUS_DI'], 2)}"),
    ("4. RSI 14", str(last["VOTE_RSI"] or "WAIT"), fmt(last["RSI_14"], 2)),
    ("5. MACD", str(last["VOTE_MACD"] or "WAIT"), f"MACD {fmt(last['MACD_LINE'], 6)} · Signal {fmt(last['MACD_SIGNAL'], 6)}"),
    ("6. VWAP 24H rolling", "PRICE LEVEL", fmt(last["VWAP_24"], 6)),
    ("7. OBV", str(last["VOTE_OBV"] or "WAIT"), f"OBV {fmt(last['OBV'], 2)}"),
    ("8. Bollinger Bands", "PRICE LEVEL", f"Mid {fmt(last['BB_MID'], 6)} · Upper {fmt(last['BB_UPPER'], 6)}"),
    ("9. ATR 14", "VOLATILITY", fmt(last["ATR_14"], 6)),
    ("10. Ichimoku Kijun", "PRICE LEVEL", fmt(last["KIJUN_26"], 6)),
    ("11. Stochastic RSI", str(last["VOTE_STOCH_RSI"] or "WAIT"), f"K {fmt(last['STOCH_RSI_K'], 2)} · D {fmt(last['STOCH_RSI_D'], 2)}"),
    ("12. MFI 14", str(last["VOTE_MFI"] or "WAIT"), fmt(last["MFI_14"], 2)),
    ("13. CMF 20", str(last["VOTE_CMF"] or "WAIT"), fmt(last["CMF_20"], 4)),
    ("14. Parabolic SAR", "PRICE LEVEL", fmt(last["PARABOLIC_SAR"], 6)),
    ("15. CCI 20", str(last["VOTE_CCI"] or "WAIT"), fmt(last["CCI_20"], 2)),
    ("16. Williams %R", "MOMENTUM", fmt(last["WILLIAMS_R"], 2)),
    ("17. ROC 12", str(last["VOTE_ROC"] or "WAIT"), fmt(last["ROC_12"], 3) + "%"),
    ("18. Keltner Channel", "PRICE LEVEL", f"Mid {fmt(last['KELTNER_MID'], 6)}"),
    ("19. Donchian Channel", "PRICE LEVEL", f"Mid {fmt(last['DONCHIAN_MID'], 6)}"),
    ("20. Volume Profile POC", "PRICE LEVEL", fmt(last["VOLUME_PROFILE_POC"], 6)),
]
st.dataframe(
    pd.DataFrame(indicator_rows, columns=["Indicator", "Direction / Role", "Value / Detail"]),
    use_container_width=True,
    hide_index=True,
)

st.subheader("Trend / Momentum / Volume Confirmation")
vote_display = []
for col in vote_cols:
    vote_display.append({"Confirmation": col.replace("VOTE_", ""), "Direction": str(last[col] or "WAIT")})
st.dataframe(pd.DataFrame(vote_display), use_container_width=True, hide_index=True)
st.caption(
    "Signal gate: confirmed 1H close crosses Combined Price + SuperTrend agrees + "
    "at least 8 valid votes + 70% votes agree + at least one volume vote agrees."
)

st.subheader("1H Price vs Combined Price")
plot_df = df.tail(250).copy()
plot_df["IST"] = pd.to_datetime(plot_df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
plot_df = plot_df.set_index("IST")
st.line_chart(plot_df[["close", "COMBINED_PRICE"]], height=320)

st.subheader("Recent confirmed signal events")
if latest_event_rows.empty:
    st.info("अभी तक उपलब्ध candle history में कोई नया Combined BUY/SELL event नहीं मिला।")
else:
    history = latest_event_rows.tail(20).copy()
    history["Candle Time (IST)"] = history["time"].apply(ist_time)
    history["Combined Price"] = history["COMBINED_PRICE"].map(lambda x: fmt(x, 6))
    history["Close"] = history["close"].map(lambda x: fmt(x, 6))
    history["Signal"] = history["SIGNAL_EVENT"]
    history["Votes"] = history.apply(
        lambda r: f"{int(r['BUY_SCORE'] if r['SIGNAL_EVENT']=='BUY' else r['SELL_SCORE'])}/{int(r['VALID_VOTES'])}",
        axis=1,
    )
    st.dataframe(
        history[["Candle Time (IST)", "Signal", "Close", "Combined Price", "Votes"]],
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Trade-state interpretation (simulation only)")
st.write(
    "एक BUY event के बाद अगला WAIT या BUY event active BUY state को नहीं बदलता। "
    "केवल नया opposite confirmed SELL event active direction को SELL करेगा। "
    "इसी तरह SELL से BUY reversal होगा। इस टेस्ट फाइल में यह केवल signal-state है; "
    "कोई वास्तविक position close/open नहीं होती।"
)

st.caption(
    f"Data: Delta Exchange India public API · Symbol: {SYMBOL} · "
    f"Completed candles loaded: {len(df)} · "
    f"Last completed candle: {ist_time(last['time'])} IST · "
    "This file is not a live trading bot."
)
