# -*- coding: utf-8 -*-
"""XAUTUSD BUY + PAXGUSD SELL one-shot 330-lot entry / one-lot target grid.
DEMO ONLY: public Delta market data; simulated fills; no authenticated orders sent.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="XAUT BUY + PAXG SELL Grid", page_icon="🟡", layout="wide")
BASE_URL = "https://api.india.delta.exchange"
SYMBOLS = {"XAUT": "XAUTUSD", "PAXG": "PAXGUSD"}
REFRESH_SECONDS = 5
CANDLE_RESOLUTION = "5m"
CANDLE_COUNT = 180
INITIAL_LOTS = 330
LOT_PER_TARGET = 1
LEVERAGE_DISPLAY = 100  # display-only planning assumption
REENTRY_OFFSET = 5.0  # fixed $5 re-entry offset, as clarified by the user

# 330 target rungs: 176 x $5, 88 x $10, 44 x $20, 22 x $40.
# This carries forward the four agreed grid intervals (5/10/20/40).
def make_target_rungs() -> List[dict]:
    steps = [5.0] * 176 + [10.0] * 88 + [20.0] * 44 + [40.0] * 22
    total = 0.0
    out = []
    for i, step in enumerate(steps, 1):
        total += step
        zone = 1 if i <= 176 else 2 if i <= 264 else 3 if i <= 308 else 4
        out.append({"number": i, "step": step, "offset": total, "zone": zone})
    return out
RUNG_TEMPLATE = make_target_rungs()

st.markdown("""<style>
.block-container{padding-top:1rem;padding-bottom:1.5rem;max-width:1800px}
div[data-testid="stMetric"]{background:rgba(120,120,120,.07);padding:12px;border-radius:10px}
.small-note{font-size:.82rem;opacity:.78}
</style>""", unsafe_allow_html=True)
st.title("🟡 XAUTUSD BUY + PAXGUSD SELL — One-Entry Grid Dashboard")
st.caption("DEMO ONLY · Delta Exchange India public market data · 330-lot simulated initial entries · 1-lot target/re-entry cycles · No real orders sent")

@st.cache_data(ttl=5, show_spinner=False)
def get_ticker(symbol: str) -> dict:
    r = requests.get(f"{BASE_URL}/v2/tickers/{symbol}", headers={"Accept":"application/json"}, timeout=12)
    r.raise_for_status(); p = r.json()
    if not p.get("success"): raise RuntimeError(str(p.get("error", p)))
    v = p.get("result")
    if isinstance(v, list): v = v[0] if v else {}
    if not isinstance(v, dict): raise RuntimeError(f"Unexpected ticker response: {symbol}")
    return v

@st.cache_data(ttl=3600, show_spinner=False)
def get_product(symbol: str) -> dict:
    r = requests.get(f"{BASE_URL}/v2/products/{symbol}", headers={"Accept":"application/json"}, timeout=12)
    r.raise_for_status(); p = r.json()
    if not p.get("success"): raise RuntimeError(str(p.get("error", p)))
    v = p.get("result") or {}
    if isinstance(v, list): v = v[0] if v else {}
    if not isinstance(v, dict): raise RuntimeError(f"Unexpected product response: {symbol}")
    return v

@st.cache_data(ttl=30, show_spinner=False)
def get_candles(symbol: str, resolution: str, count: int) -> pd.DataFrame:
    end = int(time.time())
    sec = {"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,"1h":3600}.get(resolution,300)
    params = {"resolution":resolution,"symbol":symbol,"start":str(end-sec*count),"end":str(end)}
    r = requests.get(f"{BASE_URL}/v2/history/candles", params=params, headers={"Accept":"application/json"}, timeout=15)
    r.raise_for_status(); p = r.json()
    if not p.get("success"): raise RuntimeError(str(p.get("error", p)))
    df = pd.DataFrame(p.get("result") or [])
    if df.empty: return df
    for c in ["open","high","low","close","volume","time"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    return df.sort_values("time").tail(count).reset_index(drop=True)

def fnum(v: Any, default: float=0.0) -> float:
    try: return float(v)
    except (TypeError, ValueError): return default

def money(v: float) -> str:
    return f"${v:,.4f}" if abs(v) < 1 else f"${v:,.2f}"

def make_leg_state(key: str, initial_price: float, direction: str) -> dict:
    levels = []
    for rung in RUNG_TEMPLATE:
        target = initial_price + rung["offset"] if direction == "long" else initial_price - rung["offset"]
        levels.append({**rung, "status":"OPEN", "entry_price":initial_price,
                       "target":round(target, 8), "reentry_price":None,
                       "cycles":0, "entry_time":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")})
    return {"key":key,"direction":direction,"initial_price":initial_price,"initial_lots":INITIAL_LOTS,
            "levels":levels,"last_price":initial_price,"realized_gross":0.0,"realized_fees":0.0,
            "closed_trades":0,"reentries":0,"events":[],"started_at":datetime.now(timezone.utc).isoformat()}

def event(state: dict, text: str, price: float, status: str) -> None:
    state["events"].insert(0,{"Time (IST)":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                              "Symbol":state["key"],"Event":text,"Price":price,"Status":status})
    state["events"] = state["events"][:3000]

def process_leg(state: dict, price: float, product: dict) -> None:
    prev = state["last_price"]
    if prev is None or abs(price-prev) < 1e-12:
        state["last_price"] = price; return
    direction = state["direction"]
    cv = fnum(product.get("contract_value"), 0.0)
    maker = fnum(product.get("maker_commission_rate"), 0.0)
    if cv <= 0:
        state["last_price"] = price; return
    units = cv * LOT_PER_TARGET
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    up, down = price > prev, price < prev
    for item in state["levels"]:
        target = fnum(item["target"])
        if direction == "long":
            if item["status"] == "OPEN" and up and prev < target <= price:
                entry = fnum(item["entry_price"])
                gross = (target-entry)*units
                fee = (abs(entry)+abs(target))*units*maker
                state["realized_gross"] += gross; state["realized_fees"] += fee
                state["closed_trades"] += 1; item["cycles"] += 1
                # Re-entry is exactly one zone-step below this rung's target.
                item["reentry_price"] = round(target-REENTRY_OFFSET,8)
                item["status"] = "WAITING_REENTRY"
                event(state,f"BUY target hit · 1 LOT closed · re-entry waiting",target,"TARGET HIT")
            elif item["status"] == "WAITING_REENTRY":
                re = fnum(item["reentry_price"])
                if down and prev > re >= price:
                    item["entry_price"] = re; item["entry_time"] = now
                    item["status"] = "OPEN"; item["reentry_price"] = None
                    state["reentries"] += 1
                    event(state,f"BUY re-entry filled · 1 LOT · next target ${target:,.2f}",re,"RE-ENTRY")
        else:
            if item["status"] == "OPEN" and down and prev > target >= price:
                entry = fnum(item["entry_price"])
                gross = (entry-target)*units
                fee = (abs(entry)+abs(target))*units*maker
                state["realized_gross"] += gross; state["realized_fees"] += fee
                state["closed_trades"] += 1; item["cycles"] += 1
                item["reentry_price"] = round(target+REENTRY_OFFSET,8)
                item["status"] = "WAITING_REENTRY"
                event(state,f"SELL target hit · 1 LOT closed · re-entry waiting",target,"TARGET HIT")
            elif item["status"] == "WAITING_REENTRY":
                re = fnum(item["reentry_price"])
                if up and prev < re <= price:
                    item["entry_price"] = re; item["entry_time"] = now
                    item["status"] = "OPEN"; item["reentry_price"] = None
                    state["reentries"] += 1
                    event(state,f"SELL re-entry filled · 1 LOT · next target ${target:,.2f}",re,"RE-ENTRY")
    state["last_price"] = price

def calc_pnl(state: dict, price: float, product: dict) -> Tuple[float,float,int]:
    cv=fnum(product.get("contract_value"),0.0); fee_rate=fnum(product.get("maker_commission_rate"),0.0)
    gross=0.0; fees=0.0; count=0
    for item in state["levels"]:
        if item["status"] != "OPEN": continue
        count += 1; entry=fnum(item["entry_price"]); units=cv*LOT_PER_TARGET
        gross += ((price-entry) if state["direction"]=="long" else (entry-price))*units
        fees += (abs(entry)+abs(price))*units*fee_rate
    return gross,fees,count

def build_chart(df: pd.DataFrame, title: str, price: float, state: dict, start_grid: int, view_levels: int) -> go.Figure:
    fig=go.Figure()
    if not df.empty:
        fig.add_trace(go.Candlestick(x=df["datetime"],open=df["open"],high=df["high"],low=df["low"],close=df["close"],
                                     name="5m candles",increasing_line_color="#16a34a",decreasing_line_color="#dc2626"))
    # Visible grid window expands/contracts with the slider; Plotly drag/scroll also pans and zooms.
    count=max(1,min(view_levels,len(state["levels"])))
    first=max(1,min(start_grid,len(state["levels"])))
    selected=state["levels"][first-1:first-1+count]
    offsets=[fnum(x["offset"]) for x in selected]
    steps=[fnum(x["step"]) for x in selected] or [5.0]
    pad=max(steps)
    if state["direction"]=="long":
        ylo=state["initial_price"]+(min(offsets) if offsets else 0)-pad
        yhi=state["initial_price"]+(max(offsets) if offsets else 5)+pad
    else:
        ylo=state["initial_price"]-(max(offsets) if offsets else 5)-pad
        yhi=state["initial_price"]-(min(offsets) if offsets else 0)+pad
    if yhi<=ylo: ylo=price*0.99; yhi=price*1.01
    x0=df["datetime"].iloc[0] if not df.empty else datetime.now().astimezone()
    x1=df["datetime"].iloc[-1] if not df.empty else datetime.now().astimezone()
    if x0==x1: x1=pd.Timestamp(x1)+pd.Timedelta(minutes=5)
    # Legend keys clarify the permanent line colors.
    fig.add_trace(go.Scatter(x=[None],y=[None],mode="lines",name="Target (RED)",line=dict(color="#ef4444",width=1.5,dash="dash")))
    fig.add_trace(go.Scatter(x=[None],y=[None],mode="lines",name="Re-Entry (WHITE)",line=dict(color="#ffffff",width=1.7,dash="dot")))
    fig.add_trace(go.Scatter(x=[None],y=[None],mode="lines",name="Initial Entry (BLUE)",line=dict(color="#3b82f6",width=1.4,dash="dash")))
    fig.add_trace(go.Scatter(x=[None],y=[None],mode="lines",name="Live Price (YELLOW)",line=dict(color="#f59e0b",width=1.6)))
    # Always-red target lines; show only rungs in the selected view and price window.
    for item in selected:
        tp=fnum(item["target"])
        if ylo<=tp<=yhi:
            fig.add_trace(go.Scatter(x=[x0,x1],y=[tp,tp],mode="lines",name="Target (RED)",
                                     line=dict(color="#ef4444",width=1,dash="dash"),showlegend=False,
                                     hovertemplate=f"Target · {tp:,.2f}<extra></extra>"))
        re=item.get("reentry_price")
        if re is not None and ylo<=fnum(re)<=yhi:
            fig.add_trace(go.Scatter(x=[x0,x1],y=[fnum(re),fnum(re)],mode="lines",name="Re-Entry (WHITE)",
                                     line=dict(color="#ffffff",width=1.6,dash="dot"),showlegend=False,
                                     hovertemplate=f"Re-Entry · {fnum(re):,.2f}<extra></extra>"))
    fig.add_hline(y=state["initial_price"],line_color="#3b82f6",line_dash="dash",line_width=1.4,
                  annotation_text=f"Initial market entry · {state['initial_price']:,.2f}",annotation_position="bottom right")
    fig.add_hline(y=price,line_color="#f59e0b",line_width=1.6,
                  annotation_text=f"LIVE · {price:,.2f}",annotation_position="top right")
    fig.update_layout(title=title,height=520,margin=dict(l=8,r=18,t=42,b=8),template="plotly_dark",
                      xaxis_rangeslider_visible=False,hovermode="x unified",
                      legend=dict(orientation="h",y=1.08,x=0,font=dict(size=10)),
                      yaxis=dict(side="right",title="Price (USD)",tickformat=",.2f",range=[ylo,yhi],fixedrange=False),
                      xaxis=dict(title="Time (IST)",fixedrange=False),showlegend=False)
    return fig

@st.fragment(run_every=REFRESH_SECONDS)
def live_fragment():
    st.subheader("Live Market & Grid Monitor")
    st.caption(f"Auto-refresh: {REFRESH_SECONDS}s · Candle timeframe: {CANDLE_RESOLUTION} · Timestamps: IST")
    try:
        xt=get_ticker(SYMBOLS["XAUT"]); pt=get_ticker(SYMBOLS["PAXG"])
        xp=get_product(SYMBOLS["XAUT"]); pp=get_product(SYMBOLS["PAXG"])
        xprice=fnum(xt.get("close") or xt.get("mark_price")); pprice=fnum(pt.get("close") or pt.get("mark_price"))
        if xprice<=0 or pprice<=0: raise RuntimeError("Delta ticker returned an invalid live price")
    except Exception as exc:
        st.error(f"Delta public market data unavailable: {exc}"); st.info("No dummy prices are shown."); return
    if "xaut_state" not in st.session_state:
        # One simulated market entry per side, 330 lots at the first available current price.
        st.session_state.xaut_state=make_leg_state("XAUTUSD",xprice,"long")
        st.session_state.paxg_state=make_leg_state("PAXGUSD",pprice,"short")
        st.session_state.demo_started_at=datetime.now(timezone.utc).isoformat()
        event(st.session_state.xaut_state,"INITIAL MARKET BUY · 330 LOT",xprice,"INITIAL ENTRY")
        event(st.session_state.paxg_state,"INITIAL MARKET SELL · 330 LOT",pprice,"INITIAL ENTRY")
    xs=st.session_state.xaut_state; ps=st.session_state.paxg_state
    process_leg(xs,xprice,xp); process_leg(ps,pprice,pp)
    xopen,xfees,xcount=calc_pnl(xs,xprice,xp); popen,pfees,pcount=calc_pnl(ps,pprice,pp)
    xreal=xs["realized_gross"]-xs["realized_fees"]; preal=ps["realized_gross"]-ps["realized_fees"]
    xnet=xreal+xopen-xfees; pnet=preal+popen-pfees; combined=xnet+pnet
    st.markdown("### Combined Profit & Loss")
    c=st.columns(4); c[0].metric("Combined Net P&L",money(combined)); c[1].metric("Combined Realized",money(xreal+preal)); c[2].metric("Combined Open P&L",money(xopen-xfees+popen-pfees)); c[3].metric("Open Lots",f"{xcount+pcount} / 660")
    st.markdown("### Initial Entries & Live Prices")
    q=st.columns(4); q[0].metric("XAUTUSD Live",money(xprice)); q[1].metric("PAXGUSD Live",money(pprice)); q[2].metric("XAUT Initial BUY",f"330 LOT @ {money(xs['initial_price'])}"); q[3].metric("PAXG Initial SELL",f"330 LOT @ {money(ps['initial_price'])}")
    st.markdown("### Separate Leg Performance")
    a,b=st.columns(2)
    with a:
        st.markdown("#### 🟢 XAUTUSD — BUY / LONG"); st.metric("XAUT Net P&L",money(xnet))
        st.write(f"Realized: **{money(xreal)}** · Open: **{money(xopen-xfees)}**")
        st.write(f"Open lots: **{xcount}** · Target hits: **{xs['closed_trades']}** · Re-entries: **{xs['reentries']}**")
    with b:
        st.markdown("#### 🔴 PAXGUSD — SELL / SHORT"); st.metric("PAXG Net P&L",money(pnet))
        st.write(f"Realized: **{money(preal)}** · Open: **{money(popen-pfees)}**")
        st.write(f"Open lots: **{pcount}** · Target hits: **{ps['closed_trades']}** · Re-entries: **{ps['reentries']}**")
    st.markdown("### Chart Controls")
    ctrl1,ctrl2=st.columns([2,1])
    with ctrl1:
        view_levels=st.slider("Ek baar mein kitne Grid Levels dikhane hain",min_value=10,max_value=330,value=60,step=10,key="visible_grid_levels")
    with ctrl2:
        timeframe=st.selectbox("Candle timeframe",["1m","3m","5m","15m","30m","1h"],index=2,key="chart_timeframe")
    max_start_grid=max(1,331-view_levels)
    start_grid=st.slider("Kaun-se Grid Number se dekhna hai (pichhla / agla Grid)",min_value=1,max_value=max_start_grid,value=1,step=1,key="start_grid_number")
    st.caption("Chart par mouse wheel/pinch se zoom aur drag se pan karein. Grid Number slider se pehle ya aage ke levels dekhein; visible count slider se range chhoti-badi karein.")
    st.markdown("### दोनों Live Charts — Target RED · Re-Entry WHITE")
    col1,col2=st.columns(2,gap="small")
    try:
        xdf=get_candles(SYMBOLS["XAUT"],timeframe,CANDLE_COUNT); pdf=get_candles(SYMBOLS["PAXG"],timeframe,CANDLE_COUNT)
        with col1: st.plotly_chart(build_chart(xdf,"XAUTUSD · BUY Grid",xprice,xs,start_grid,view_levels),use_container_width=True,config={"scrollZoom":True,"displayModeBar":True,"responsive":True})
        with col2: st.plotly_chart(build_chart(pdf,"PAXGUSD · SELL Grid",pprice,ps,start_grid,view_levels),use_container_width=True,config={"scrollZoom":True,"displayModeBar":True,"responsive":True})
    except Exception as exc: st.warning(f"Live prices available, candle chart unavailable: {exc}")
    st.markdown("### Target / Re-Entry Grid Status")
    tx,tp=st.tabs(["XAUT BUY Levels","PAXG SELL Levels"])
    with tx:
        rows=[{"Grid":i["number"],"Zone":i["zone"],"Step":i["step"],"Target (RED)":i["target"],"Status":i["status"],"Re-Entry (WHITE)":i["reentry_price"],"Cycles":i["cycles"]} for i in xs["levels"]]
        st.dataframe(pd.DataFrame(rows),use_container_width=True,height=340,hide_index=True)
    with tp:
        rows=[{"Grid":i["number"],"Zone":i["zone"],"Step":i["step"],"Target (RED)":i["target"],"Status":i["status"],"Re-Entry (WHITE)":i["reentry_price"],"Cycles":i["cycles"]} for i in ps["levels"]]
        st.dataframe(pd.DataFrame(rows),use_container_width=True,height=340,hide_index=True)
    st.markdown("### Trade History")
    events=xs["events"]+ps["events"]
    if events: st.dataframe(pd.DataFrame(events).sort_values("Time (IST)",ascending=False),use_container_width=True,height=300,hide_index=True)
    else: st.info("No target/re-entry event yet.")
    with st.expander("Strategy & Demo limitations",expanded=False):
        st.markdown("""
- **DEMO ONLY:** this file uses public market data and simulated fills. It does not send real orders or use API keys.
- First dashboard load simulates one market BUY of 330 LOT XAUTUSD and one market SELL of 330 LOT PAXGUSD at their respective live prices.
- After initial entry, 330 separate one-lot target rungs are tracked independently in each instrument.
- The 330 rung distances use the agreed schedule: 176 steps of $5, then 88 of $10, 44 of $20, and 22 of $40.
- Each target is red. When a 1-lot target fills, only that 1 lot is closed; its one-lot re-entry is placed exactly $5 behind the target (BUY: below; SELL: above), shown white. After re-entry fills, that rung targets its same target price again.
- 5-second polling can miss brief intraperiod touches. This is not tick-perfect and not suitable for live execution.
- P&L is an estimate using product contract value and maker fee metadata; funding, slippage, taker fees on initial market orders, margin/liquidation and partial fills are not fully modelled.
- State is stored in Streamlit session memory and resets if the session/app restarts.
""")
    st.caption(f"Demo session started: {st.session_state.demo_started_at}")

live_fragment()
st.divider()
st.warning("REAL ORDER EXECUTION is disabled in this file. This is a demo simulator; no authenticated Delta orders are placed.")
if st.button("Reset Demo & Start Again",type="secondary"):
    for k in ["xaut_state","paxg_state","demo_started_at","visible_grid_levels","chart_timeframe","start_grid_number"]:
        if k in st.session_state: del st.session_state[k]
    st.rerun()
