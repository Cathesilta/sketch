#!/usr/bin/env python3
"""Fetch one year of global macro data for the static investor dashboard."""
from __future__ import annotations
import json, math, os, tempfile, time
from datetime import datetime, timedelta, timezone
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
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
DEFI_FORUM_URLS = {
    "morpho": "https://forum.morpho.org",
    "euler": "https://forum.euler.finance",
    "uniswap": "https://gov.uniswap.org",
    "lido-dao": "https://research.lido.fi",
}
FORUM_RECENT_DAYS = 7
FORUM_NEW_TOPIC_LIMIT = 5
FORUM_REFRESHED_TOPIC_LIMIT = 3
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
 {"rank":11,"market":"Bitcoin","signal":"Crypto market price","source":"Yahoo","symbol":"BTC-USD","column":"Bitcoin","unit":"USD"},
 {"rank":12,"market":"Morpho","signal":"Crypto market price","source":"CoinGecko","symbol":"morpho","column":"Morpho","unit":"USD"},
 {"rank":13,"market":"Euler","signal":"Crypto market price","source":"CoinGecko","symbol":"euler","column":"Euler","unit":"USD"},
 {"rank":14,"market":"Uniswap","signal":"Crypto market price","source":"CoinGecko","symbol":"uniswap","column":"Uniswap","unit":"USD"},
 {"rank":15,"market":"Lido DAO","signal":"Crypto market price","source":"CoinGecko","symbol":"lido-dao","column":"Lido DAO","unit":"USD"},
]

def fetch_fred(series_id, start=START, end=TODAY):
    """Fetch FRED through its API in Actions, or the reference CSV locally."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if api_key:
        try:
            response = requests.get(
                FRED_API_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "observation_start": start.strftime("%Y-%m-%d"),
                    "observation_end": end.strftime("%Y-%m-%d"),
                },
                timeout=30,
                headers={"User-Agent": "global-macro-dashboard/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
            frame = pd.DataFrame(payload.get("observations", []))
            if not {"date", "value"}.issubset(frame.columns):
                raise ValueError(payload.get("error_message", "response has no observations"))
            frame = frame[["date", "value"]]
            frame.columns = ["Date", series_id]
        except Exception as exc:
            # A requests exception can contain the query string. Do not copy the
            # repository secret into the generated JSON or an Actions log.
            safe_error = str(exc).replace(api_key, "***")
            raise RuntimeError(f"FRED API {series_id} failed: {safe_error}") from None
    else:
        frame = fetch_fred_csv(series_id, start, end)

    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame[series_id] = pd.to_numeric(frame[series_id], errors="coerce")
    values = frame.dropna().set_index("Date")[series_id].sort_index()
    if values.empty:
        raise ValueError(f"FRED returned no observations for {series_id}")
    return values


def fetch_fred_csv(series_id, start=START, end=TODAY):
    """Use the public CSV method from global_macro_dashboard.py."""
    url = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={quote(series_id)}&cosd={start:%Y-%m-%d}&coed={end:%Y-%m-%d}"
    )
    response = requests.get(
        url, timeout=30, headers={"User-Agent": "global-macro-dashboard/1.0"}
    )
    response.raise_for_status()
    frame = pd.read_csv(pd.io.common.StringIO(response.text))
    frame.columns = ["Date", series_id]
    return frame

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

def fetch_coingecko_history(coin_id, days=365, attempts=3):
    """Fetch daily USD prices using CoinGecko's stable coin ID."""
    url = COINGECKO_MARKET_CHART_URL.format(coin_id=quote(coin_id))
    last = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
                timeout=30,
                headers={"User-Agent": "global-macro-dashboard/1.0"},
            )
            response.raise_for_status()
            prices = response.json().get("prices", [])
            if not prices:
                raise ValueError(f"CoinGecko returned no prices for {coin_id}")
            values = pd.Series(
                [price for _, price in prices],
                index=pd.to_datetime([timestamp for timestamp, _ in prices], unit="ms", utc=True).tz_localize(None).normalize(),
                dtype=float,
            )
            return values.groupby(level=0).last().sort_index()
        except Exception as exc:
            last = exc
        if attempt + 1 < attempts:
            time.sleep(2**attempt * 5)
    raise RuntimeError(f"CoinGecko {coin_id} failed after {attempts} attempts: {last}")

def _forum_timestamp_is_recent(value, cutoff):
    if not isinstance(value, str):
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp >= cutoff

def _forum_topic(topic, forum_url):
    topic_id = topic.get("id")
    slug = topic.get("slug")
    posts_count = topic.get("posts_count")
    return {
        "id": topic_id,
        "title": topic.get("title") or "",
        "url": f"{forum_url}/t/{slug}/{topic_id}" if slug and topic_id else None,
        "created_at": topic.get("created_at"),
        "last_activity_at": topic.get("bumped_at") or topic.get("last_posted_at"),
        "replies": max(posts_count - 1, 0) if isinstance(posts_count, int) else None,
        "views": topic.get("views"),
    }

def fetch_defi_forum(forum_url):
    """Fetch a DeFi project's newest and recently refreshed Discourse topics."""
    forum_url = forum_url.rstrip("/")
    response = requests.get(
        f"{forum_url}/latest.json",
        params={"no_definitions": "true"},
        timeout=30,
        headers={
            "Accept": "application/json",
            "User-Agent": "global-macro-dashboard/1.0 (+public Discourse data reader)",
        },
    )
    response.raise_for_status()
    topics = response.json().get("topic_list", {}).get("topics")
    if not isinstance(topics, list):
        raise ValueError("Morpho forum response has no topic list")
    topics = [topic for topic in topics if isinstance(topic, dict)]
    cutoff = datetime.now(timezone.utc) - timedelta(days=FORUM_RECENT_DAYS)
    new_topics = sorted(
        (topic for topic in topics if _forum_timestamp_is_recent(topic.get("created_at"), cutoff)),
        key=lambda topic: str(topic.get("created_at") or ""),
        reverse=True,
    )[:FORUM_NEW_TOPIC_LIMIT]
    new_ids = {topic.get("id") for topic in new_topics}
    refreshed_topics = sorted(
        (
            topic for topic in topics
            if topic.get("id") not in new_ids
            and _forum_timestamp_is_recent(topic.get("bumped_at") or topic.get("last_posted_at"), cutoff)
        ),
        key=lambda topic: str(topic.get("bumped_at") or topic.get("last_posted_at") or ""),
        reverse=True,
    )[:FORUM_REFRESHED_TOPIC_LIMIT]
    return {
        "source": "Discourse",
        "url": forum_url,
        "recent_days": FORUM_RECENT_DAYS,
        "new_topics": [_forum_topic(topic, forum_url) for topic in new_topics],
        "recently_refreshed_topics": [_forum_topic(topic, forum_url) for topic in refreshed_topics],
    }

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

def cached_defi_forums():
    if not OUTPUT_PATH.exists(): return {}
    try:
        payload=json.loads(OUTPUT_PATH.read_text())
        return {
            item["symbol"]: item.get("forum")
            for item in payload.get("series", [])
            if item.get("symbol") in DEFI_FORUM_URLS and item.get("forum")
        }
    except (json.JSONDecodeError,KeyError,TypeError,OSError): return {}

def number(v):
    x=float(v); return round(x,6) if math.isfinite(x) else None

def chart_filename(item):
    return f"{item['column'].lower().replace(' ', '_').replace('&', 'and')}.png"

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
    old=cache(); old_forums=cached_defi_forums(); yahoo=[x for x in SERIES if x["source"]=="Yahoo"]; batch=None; yerr=None
    try: batch=fetch_yahoo_batch(yahoo)
    except Exception as exc: yerr=exc
    out=[]; fresh=0; cutoff=START.strftime("%Y-%m-%d")
    for item in SERIES:
        status="fresh"; error=None
        try:
            if item["source"] == "FRED":
                vals = fetch_fred(item["symbol"])
            elif item["source"] == "Yahoo":
                if batch is None:
                    raise yerr or RuntimeError("Yahoo unavailable")
                vals = yahoo_close(batch, item["symbol"], len(yahoo))
            else:
                vals = fetch_coingecko_history(item["symbol"])
            history={d.strftime("%Y-%m-%d"):number(v) for d,v in vals.items()}; fresh+=1
        except Exception as exc:
            history=old.get(item["column"],{}); status="cached" if history else "error"; error=str(exc)
        history={d:v for d,v in history.items() if d>=cutoff and v is not None}; points=[{"date":d,"value":v} for d,v in sorted(history.items())]
        result={**item,"chart":chart_filename(item),"status":status,"error":error,"latest":points[-1] if points else None,"returns":calculate_returns(points),"history":points}
        if item["symbol"] in DEFI_FORUM_URLS:
            try:
                result["forum"]=fetch_defi_forum(DEFI_FORUM_URLS[item["symbol"]]); result["forum_status"]="fresh"; result["forum_error"]=None
            except Exception as exc:
                old_forum=old_forums.get(item["symbol"])
                result["forum"]=old_forum; result["forum_status"]="cached" if old_forum else "error"; result["forum_error"]=str(exc)
        out.append(result)
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
        fig.savefig(CHART_DIR / item["chart"], bbox_inches="tight")
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
    payload=build_payload()
    stale_fred=[item for item in payload["series"] if item["source"]=="FRED" and item["status"]!="fresh"]
    if os.environ.get("REQUIRE_FRESH_FRED", "").lower() in {"1", "true", "yes"} and stale_fred:
        details="; ".join(f"{item['symbol']}: {item['error']}" for item in stale_fred)
        raise RuntimeError(f"FRED refresh failed: {details}")
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=OUTPUT_PATH.parent,prefix="macro-",suffix=".json")
    try:
        with os.fdopen(fd,"w") as f: json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False); f.write("\n")
        os.replace(tmp,OUTPUT_PATH)
        write_charts(payload)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise
    print(f"Saved {payload['fresh_series']}/{payload['total_series']} freshly fetched series to {OUTPUT_PATH}")
if __name__=="__main__": main()
