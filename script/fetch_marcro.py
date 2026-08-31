#!/usr/bin/env python3
"""Fetch one year of global macro data for the static investor dashboard."""
from __future__ import annotations
import json, math, os, tempfile, time
from pathlib import Path
from urllib.parse import quote
import pandas as pd
import requests
import yfinance as yf

TODAY = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
START = TODAY - pd.DateOffset(years=1)
END_EXCLUSIVE = TODAY + pd.Timedelta(days=1)
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "macro.json"
SERIES = [
 {"rank":1,"market":"U.S. 10Y Treasury","signal":"Global valuation","source":"FRED","symbol":"DGS10","column":"US 10Y Treasury","unit":"Yield (%)"},
 {"rank":2,"market":"U.S. Dollar (DXY)","signal":"Global liquidity","source":"Yahoo","symbol":"DX-Y.NYB","column":"DXY","unit":"Index"},
 {"rank":3,"market":"U.S. 2Y Treasury","signal":"Fed expectations","source":"FRED","symbol":"DGS2","column":"US 2Y Treasury","unit":"Yield (%)"},
 {"rank":4,"market":"Credit spreads","signal":"Financial stress","source":"FRED","symbol":"BAMLH0A0HYM2","column":"US HY OAS","unit":"Spread (%)"},
 {"rank":5,"market":"Oil","signal":"Inflation + demand","source":"Yahoo","symbol":"CL=F","column":"WTI Crude","unit":"USD/barrel"},
 {"rank":6,"market":"Copper","signal":"Industrial growth","source":"Yahoo","symbol":"HG=F","column":"COMEX Copper","unit":"USD/lb"},
 {"rank":7,"market":"Gold","signal":"Fear + currency confidence","source":"Yahoo","symbol":"GC=F","column":"COMEX Gold","unit":"USD/troy oz"},
 {"rank":8,"market":"VIX","signal":"Risk appetite","source":"Yahoo","symbol":"^VIX","column":"VIX","unit":"Index"},
 {"rank":9,"market":"U.S. 30Y Treasury","signal":"Long-term fiscal confidence","source":"FRED","symbol":"DGS30","column":"US 30Y Treasury","unit":"Yield (%)"},
 {"rank":10,"market":"S&P 500","signal":"Market response","source":"Yahoo","symbol":"^GSPC","column":"S&P 500","unit":"Index"},
 {"rank":10,"market":"Nasdaq Composite","signal":"Market response","source":"Yahoo","symbol":"^IXIC","column":"Nasdaq Composite","unit":"Index"},
]

def fetch_fred(series_id, start=START, end=TODAY):
    url=("https://fred.stlouisfed.org/graph/fredgraph.csv" f"?id={quote(series_id)}&cosd={start:%Y-%m-%d}&coed={end:%Y-%m-%d}")
    r=requests.get(url, timeout=30, headers={"User-Agent":"sketch-global-macro-dashboard/1.0"}); r.raise_for_status()
    f=pd.read_csv(pd.io.common.StringIO(r.text)); f.columns=["Date",series_id]
    f["Date"]=pd.to_datetime(f["Date"],errors="coerce"); f[series_id]=pd.to_numeric(f[series_id],errors="coerce")
    return f.dropna().set_index("Date")[series_id].sort_index()

def fetch_yahoo_batch(items, attempts=3):
    symbols=[x["symbol"] for x in items]; last=None
    for attempt in range(attempts):
        try:
            f=yf.download(symbols,start=START,end=END_EXCLUSIVE,interval="1d",auto_adjust=False,actions=False,progress=False,threads=False,timeout=30,repair=False,group_by="ticker")
            if not f.empty: return f
            last=ValueError("Yahoo returned an empty batch")
        except Exception as exc: last=exc
        if attempt+1<attempts: time.sleep(2**attempt*5)
    raise RuntimeError(f"Yahoo batch failed after {attempts} attempts: {last}")

def yahoo_close(batch,symbol,count):
    v=batch["Close"] if count==1 else batch[(symbol,"Close")]; v=pd.to_numeric(v,errors="coerce").dropna()
    v.index=pd.to_datetime(v.index).tz_localize(None)
    if v.empty: raise ValueError(f"No daily closes returned for {symbol}")
    return v.sort_index()

def cache():
    if not OUTPUT_PATH.exists(): return {}
    try:
        p=json.loads(OUTPUT_PATH.read_text()); return {x["column"]:{y["date"]:y["value"] for y in x["history"]} for x in p.get("series",[])}
    except (json.JSONDecodeError,KeyError,TypeError,OSError): return {}

def number(v):
    x=float(v); return round(x,6) if math.isfinite(x) else None

def build_payload():
    old=cache(); yahoo=[x for x in SERIES if x["source"]=="Yahoo"]; batch=None; yerr=None
    try: batch=fetch_yahoo_batch(yahoo)
    except Exception as exc: yerr=exc
    out=[]; fresh=0; cutoff=START.strftime("%Y-%m-%d")
    for item in SERIES:
        status="fresh"; error=None
        try:
            vals=fetch_fred(item["symbol"]) if item["source"]=="FRED" else yahoo_close(batch,item["symbol"],len(yahoo)) if batch is not None else (_ for _ in ()).throw(yerr or RuntimeError("Yahoo unavailable"))
            history={d.strftime("%Y-%m-%d"):number(v) for d,v in vals.items()}; fresh+=1
        except Exception as exc:
            history=old.get(item["column"],{}); status="cached" if history else "error"; error=str(exc)
        history={d:v for d,v in history.items() if d>=cutoff and v is not None}; points=[{"date":d,"value":v} for d,v in sorted(history.items())]
        out.append({**item,"status":status,"error":error,"latest":points[-1] if points else None,"history":points})
    if not any(x["history"] for x in out): raise RuntimeError("Every download failed and no cached data is available")
    return {"generated_at":pd.Timestamp.now(tz="UTC").isoformat(),"period":{"start":cutoff,"end":TODAY.strftime("%Y-%m-%d")},"fresh_series":fresh,"total_series":len(SERIES),"series":out}

def main():
    payload=build_payload(); OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=OUTPUT_PATH.parent,prefix="macro-",suffix=".json")
    try:
        with os.fdopen(fd,"w") as f: json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False); f.write("\n")
        os.replace(tmp,OUTPUT_PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise
    print(f"Saved {payload['fresh_series']}/{payload['total_series']} freshly fetched series to {OUTPUT_PATH}")
if __name__=="__main__": main()
