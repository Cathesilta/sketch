#!/usr/bin/env python3
"""Fetch one year of global macro data for the static investor dashboard."""
from __future__ import annotations
import json, math, os, tempfile, time
from pathlib import Path
from urllib.parse import quote
import pandas as pd
import requests
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

TODAY = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
START = TODAY - pd.DateOffset(years=1)
END_EXCLUSIVE = TODAY + pd.Timedelta(days=1)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "macro.json"
CHART_DIR = DATA_DIR / "charts"
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

def fetch_fred_batch(items, start=START, end=TODAY, attempts=3):
    """Fetch all FRED series in one request to avoid rate limits/timeouts."""
    series_ids = [item["symbol"] for item in items]
    ids = quote(",".join(series_ids))
    url=("https://fred.stlouisfed.org/graph/fredgraph.csv"
         f"?id={ids}&cosd={start:%Y-%m-%d}&coed={end:%Y-%m-%d}")
    headers={"User-Agent":"sketch-global-macro-dashboard/1.0"}
    last = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=(15, 90), headers=headers)
            response.raise_for_status()
            frame = pd.read_csv(pd.io.common.StringIO(response.text))
            date_column = frame.columns[0]
            frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
            result = {}
            for series_id in series_ids:
                if series_id not in frame.columns:
                    raise ValueError(f"FRED response is missing {series_id}")
                values = pd.to_numeric(frame[series_id], errors="coerce")
                result[series_id] = values.where(frame[date_column].notna())
                result[series_id].index = frame[date_column]
                result[series_id] = result[series_id].dropna().sort_index()
                if result[series_id].empty:
                    raise ValueError(f"FRED returned no observations for {series_id}")
            return result
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"FRED batch failed after {attempts} attempts: {last}")

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

def calculate_returns(points):
    """Calculate percentage changes from the latest available observation."""
    if not points:
        return {"1D": None, "1W": None, "1M": None}

    latest = points[-1]
    latest_date = pd.Timestamp(latest["date"])
    # Use the previous observation for 1D because the prior calendar day
    # may be a non-trading day, matching the reference dashboard.
    previous_day = points[-2]["value"] if len(points) > 1 else None

    def value_asof(target):
        target_date = target.strftime("%Y-%m-%d")
        for point in reversed(points):
            if point["date"] <= target_date:
                return point["value"]
        return None

    def pct_change(previous):
        if previous is None or previous == 0:
            return None
        return number((latest["value"] / previous - 1) * 100)

    return {
        "1D": pct_change(previous_day),
        "1W": pct_change(value_asof(latest_date - pd.Timedelta(days=7))),
        "1M": pct_change(value_asof(latest_date - pd.DateOffset(months=1))),
    }

def build_payload():
    old=cache(); yahoo=[x for x in SERIES if x["source"]=="Yahoo"]; fred=[x for x in SERIES if x["source"]=="FRED"]
    batch=None; yerr=None; fred_values=None; ferr=None
    try: batch=fetch_yahoo_batch(yahoo)
    except Exception as exc: yerr=exc
    try: fred_values=fetch_fred_batch(fred)
    except Exception as exc: ferr=exc
    out=[]; fresh=0; cutoff=START.strftime("%Y-%m-%d")
    for item in SERIES:
        status="fresh"; error=None
        try:
            vals=fred_values[item["symbol"]] if item["source"]=="FRED" and fred_values is not None else yahoo_close(batch,item["symbol"],len(yahoo)) if batch is not None else (_ for _ in ()).throw(yerr or ferr or RuntimeError("Market data unavailable"))
            history={d.strftime("%Y-%m-%d"):number(v) for d,v in vals.items()}; fresh+=1
        except Exception as exc:
            history=old.get(item["column"],{}); status="cached" if history else "error"; error=str(exc)
        history={d:v for d,v in history.items() if d>=cutoff and v is not None}; points=[{"date":d,"value":v} for d,v in sorted(history.items())]
        out.append({**item,"status":status,"error":error,"latest":points[-1] if points else None,"returns":calculate_returns(points),"history":points})
    if not any(x["history"] for x in out): raise RuntimeError("Every download failed and no cached data is available")
    return {"generated_at":pd.Timestamp.now(tz="UTC").isoformat(),"period":{"start":cutoff,"end":TODAY.strftime("%Y-%m-%d")},"fresh_series":fresh,"total_series":len(SERIES),"series":out}

def write_charts(payload: dict) -> None:
    """Render notebook-style one-year charts as static PNGs for GitHub Pages."""
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({"figure.dpi": 120, "axes.titleweight": "bold"})
    for item in payload["series"]:
        points = item["history"]
        fig, ax = plt.subplots(figsize=(10, 4.8), constrained_layout=True)
        if points:
            dates = pd.to_datetime([p["date"] for p in points])
            values = [p["value"] for p in points]
            ax.plot(dates, values, linewidth=1.7)
            ax.scatter(dates[-1], values[-1], s=22, zorder=3)
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax.tick_params(axis="x", rotation=30)
        else:
            ax.text(0.5, 0.5, "Data unavailable", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(f"{item['rank']}. {item['market']} — {item['signal']}", loc="left", fontsize=11)
        ax.set_ylabel(item["unit"])
        fig.savefig(CHART_DIR / f"{item['column'].lower().replace(' ', '_').replace('&', 'and')}.png", bbox_inches="tight")
        plt.close(fig)

    # Match the notebook's optional cross-market comparison view.
    fig, ax = plt.subplots(figsize=(14, 7), constrained_layout=True)
    plotted = False
    for item in payload["series"]:
        if not item["history"]: continue
        values = pd.Series({p["date"]: p["value"] for p in item["history"]}, dtype=float)
        values.index = pd.to_datetime(values.index)
        ax.plot(values.index, values / values.iloc[0] * 100, linewidth=1.2, label=item["column"])
        plotted = True
    if plotted:
        ax.axhline(100, color="black", linewidth=0.8, alpha=0.5)
        ax.legend(ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.14), loc="upper center")
    ax.set(title="One-year macro cross-market comparison (start = 100)", ylabel="Rebased level", xlabel="")
    fig.savefig(CHART_DIR / "comparison.png", bbox_inches="tight")
    plt.close(fig)


def main():
    payload=build_payload(); OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=OUTPUT_PATH.parent,prefix="macro-",suffix=".json")
    try:
        with os.fdopen(fd,"w") as f: json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False); f.write("\n")
        os.replace(tmp,OUTPUT_PATH)
        write_charts(payload)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise
    print(f"Saved {payload['fresh_series']}/{payload['total_series']} freshly fetched series to {OUTPUT_PATH}")
if __name__=="__main__": main()
