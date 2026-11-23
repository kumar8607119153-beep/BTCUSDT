# -*- coding: utf-8 -*-
"""XAUTUSD SELL + PAXGUSD BUY one-shot 330-lot entry / one-lot target grid.
DEMO ONLY: public Delta market data; simulated fills; no authenticated orders sent.
"""
from __future__ import annotations
import time
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import pandas as pd
import streamlit.components.v1 as components
import requests
import streamlit as st

st.set_page_config(page_title="XAUT SELL + PAXG BUY Grid", page_icon="🟡", layout="wide")
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
st.title("🟡 XAUTUSD SELL + PAXGUSD BUY — One-Entry Grid Dashboard")
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

def render_original_style_chart(df: pd.DataFrame, symbol: str, title: str, price: float,
                                state: dict, start_grid: int, view_levels: int,
                                timeframe: str) -> None:
    'Restore the previous Lightweight Charts appearance with current grid state.'
    if df.empty:
        st.warning(f"{symbol}: candle data is not available yet.")
        return
    candles = []
    for row in df.itertuples(index=False):
        try:
            candles.append({"time": int(row.time), "open": float(row.open),
                            "high": float(row.high), "low": float(row.low),
                            "close": float(row.close)})
        except (TypeError, ValueError, AttributeError):
            continue
    if not candles:
        st.warning(f"{symbol}: valid OHLC candles are not available.")
        return

    levels = state.get("levels", [])
    first = max(1, min(int(start_grid), max(1, len(levels))))
    selected = levels[first - 1:first - 1 + max(1, int(view_levels))]
    target_lines = []
    for item in selected:
        target = fnum(item.get("target"))
        if target > 0:
            target_lines.append({"price": target, "color": "#ef4444", "width": 1,
                                 "style": 2, "axisLabelVisible": False,
                                 "title": f"TARGET · Z{int(item.get('zone', 1))} · L{int(item.get('number', 0)):03d}"})
    reentry_lines = []
    for item in selected:
        reentry = item.get("reentry_price")
        if reentry is not None and fnum(reentry) > 0:
            reentry_lines.append({"price": fnum(reentry), "color": "#f8fafc", "width": 2,
                                  "style": 0, "axisLabelVisible": True,
                                  "title": f"RE-ENTRY · Z{int(item.get('zone', 1))} · L{int(item.get('number', 0)):03d}"})
    payload = {
        "symbol": symbol, "title": title, "timeframe": timeframe,
        "candles": candles, "currentPrice": float(price),
        "targetLines": target_lines, "reentryLines": reentry_lines,
        "initialPrice": float(state.get("initial_price", price)),
        "gridFirst": first, "gridLast": first + len(selected) - 1,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    chart_html = r'''<!doctype html>
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
const fmt=v=>Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4});
document.getElementById('live').textContent='LIVE $'+fmt(D.currentPrice);
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
 candles.createPriceLine({price:Number(L.price),color:L.color,lineWidth:Number(L.width||1),lineStyle:Number(L.style||0),axisLabelVisible:L.axisLabelVisible!==false,title:L.title||''});}
D.targetLines.forEach(addLine);D.reentryLines.forEach(addLine);
candles.createPriceLine({price:D.initialPrice,color:'#22c55e',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'INITIAL / ACTIVE ENTRY'});
const liveLine=candles.createPriceLine({price:D.currentPrice,color:'#e5e7eb',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'LIVE'});
const key='sanjay_demo_chart_'+D.symbol;let restored=false;
try{const r=JSON.parse(localStorage.getItem(key)||'null');if(r&&Number.isFinite(r.from)&&Number.isFinite(r.to)&&r.to>r.from){chart.timeScale().setVisibleLogicalRange(r);restored=true;}}catch(e){}
if(!restored)chart.timeScale().fitContent();
chart.timeScale().subscribeVisibleLogicalRangeChange(r=>{if(r&&Number.isFinite(r.from)&&Number.isFinite(r.to)){try{localStorage.setItem(key,JSON.stringify(r));}catch(e){}}});
document.getElementById('fullscreen').onclick=()=>{const target=document.documentElement;if(!document.fullscreenElement){if(target.requestFullscreen)target.requestFullscreen();}else if(document.exitFullscreen)document.exitFullscreen();};
new ResizeObserver(()=>chart.applyOptions({width:el.clientWidth,height:el.clientHeight})).observe(el);
document.getElementById('status').textContent='Grid '+D.gridFirst+'–'+D.gridLast+' · TARGET RED · RE-ENTRY WHITE · INITIAL ENTRY GREEN · Zoom: mouse wheel/pinch · Pan: drag';
let ws=null,pingTimer=null,reconnectTimer=null;
const secs={'1m':60,'3m':180,'5m':300,'15m':900,'30m':1800,'1h':3600}[D.timeframe]||300;
function connectMarketSocket(){
 if(ws){try{ws.close();}catch(e){}} if(pingTimer)clearInterval(pingTimer); if(reconnectTimer)clearTimeout(reconnectTimer);
 ws=new WebSocket('wss://public-socket.india.delta.exchange');
 ws.onopen=()=>{ws.send(JSON.stringify({type:'subscribe',payload:{channels:[{name:'candlestick_'+D.timeframe,symbols:[D.symbol]}]}}));
  pingTimer=setInterval(()=>{if(ws&&ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'ping'}));},25000);
  document.getElementById('status').textContent='Connected · Delta live candles · '+D.symbol+' · '+D.timeframe;};
 ws.onmessage=(event)=>{try{const msg=JSON.parse(event.data);if(!msg.type||!msg.type.startsWith('candlestick_'))return;
  let ts=msg.ts!=null?Number(msg.ts):Math.floor(Date.now()/1000);if(ts>100000000000)ts=Math.floor(ts/1000000);
  const bucket=Math.floor(ts/secs)*secs;const c={time:bucket,open:Number(msg.o),high:Number(msg.h),low:Number(msg.l),close:Number(msg.c)};
  if(![c.open,c.high,c.low,c.close].every(Number.isFinite))return;const last=D.candles[D.candles.length-1];
  if(last&&last.time===bucket){c.open=Number(last.open);c.high=Math.max(Number(last.high),c.high,c.open);c.low=Math.min(Number(last.low),c.low,c.open);D.candles[D.candles.length-1]=c;}
  else if(!last||bucket>last.time){D.candles.push(c);if(D.candles.length>350)D.candles.shift();}else return;
  candles.update(c);liveLine.applyOptions({price:c.close});document.getElementById('live').textContent='LIVE $'+fmt(c.close);
 }catch(e){console.error('Delta candle socket message',e);}};
 ws.onerror=()=>{document.getElementById('status').textContent='WebSocket error · reconnecting';};
 ws.onclose=()=>{if(pingTimer)clearInterval(pingTimer);reconnectTimer=setTimeout(connectMarketSocket,3000);};
}
connectMarketSocket();
</script></body></html>'''.replace('__PAYLOAD__', payload_json)
    components.html(chart_html, height=550, scrolling=False)

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
        st.session_state.xaut_state=make_leg_state("XAUTUSD",xprice,"short")
        st.session_state.paxg_state=make_leg_state("PAXGUSD",pprice,"long")
        st.session_state.demo_started_at=datetime.now(timezone.utc).isoformat()
        event(st.session_state.xaut_state,"INITIAL MARKET SELL · 330 LOT",xprice,"INITIAL ENTRY")
        event(st.session_state.paxg_state,"INITIAL MARKET BUY · 330 LOT",pprice,"INITIAL ENTRY")
    xs=st.session_state.xaut_state; ps=st.session_state.paxg_state
    process_leg(xs,xprice,xp); process_leg(ps,pprice,pp)
    xopen,xfees,xcount=calc_pnl(xs,xprice,xp); popen,pfees,pcount=calc_pnl(ps,pprice,pp)
    xreal=xs["realized_gross"]-xs["realized_fees"]; preal=ps["realized_gross"]-ps["realized_fees"]
    xnet=xreal+xopen-xfees; pnet=preal+popen-pfees; combined=xnet+pnet
    st.markdown("### Combined Profit & Loss")
    c=st.columns(4); c[0].metric("Combined Net P&L",money(combined)); c[1].metric("Combined Realized",money(xreal+preal)); c[2].metric("Combined Open P&L",money(xopen-xfees+popen-pfees)); c[3].metric("Open Lots",f"{xcount+pcount} / 660")
    st.markdown("### Initial Entries & Live Prices")
    q=st.columns(4); q[0].metric("XAUTUSD Live",money(xprice)); q[1].metric("PAXGUSD Live",money(pprice)); q[2].metric("XAUT Initial SELL",f"330 LOT @ {money(xs['initial_price'])}"); q[3].metric("PAXG Initial BUY",f"330 LOT @ {money(ps['initial_price'])}")
    st.markdown("### Separate Leg Performance")
    a,b=st.columns(2)
    with a:
        st.markdown("#### 🔴 XAUTUSD — SELL / SHORT"); st.metric("XAUT Net P&L",money(xnet))
        st.write(f"Realized: **{money(xreal)}** · Open: **{money(xopen-xfees)}**")
        st.write(f"Open lots: **{xcount}** · Target hits: **{xs['closed_trades']}** · Re-entries: **{xs['reentries']}**")
    with b:
        st.markdown("#### 🟢 PAXGUSD — BUY / LONG"); st.metric("PAXG Net P&L",money(pnet))
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
        with col1: render_original_style_chart(xdf,"XAUTUSD","XAUTUSD · SELL / SHORT",xprice,xs,start_grid,view_levels,timeframe)
        with col2: render_original_style_chart(pdf,"PAXGUSD","PAXGUSD · BUY / LONG",pprice,ps,start_grid,view_levels,timeframe)
    except Exception as exc: st.warning(f"Live prices available, candle chart unavailable: {exc}")
    st.markdown("### Target / Re-Entry Grid Status")
    tx,tp=st.tabs(["XAUT SELL Levels","PAXG BUY Levels"])
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
- First dashboard load simulates one market SELL of 330 LOT XAUTUSD and one market BUY of 330 LOT PAXGUSD at their respective live prices.
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
