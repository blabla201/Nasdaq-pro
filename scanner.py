"""Hisseleri kendi bulan tarayıcı + uyarı modülü (Streamlit'ten bağımsız)."""
import io, json, os, re, time, urllib.parse, urllib.request
import pandas as pd
import yfinance as yf
from engine import lots, plan, prep, run_all, score

VERSION = "v4"


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alerts_state.json")
LIQ = os.path.join(HERE, "liquid_cache.json")
NDX_FALLBACK = ("AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO COST NFLX AMD ADBE PEP CSCO TMUS INTC QCOM TXN AMGN INTU "
                "AMAT BKNG ISRG LRCX MU PANW ADI KLAC SBUX GILD MRVL CRWD ABNB ADP CDNS SNPS ORLY MELI PYPL").split()

# ---------- Evren ----------
def universe_ndx():
    try:
        t = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100", match="Ticker")[0]
        return [s.replace(".", "-") for s in t["Ticker"].astype(str)]
    except Exception:
        return NDX_FALLBACK

def universe_all():
    """Nasdaq'ın resmi sembol listesi: test/ETF/varant/hak/imtiyazlı hisseler elenir."""
    raw = urllib.request.urlopen("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", timeout=25).read().decode()
    d = pd.read_csv(io.StringIO(raw), sep="|")
    d = d[d["Symbol"].astype(str).str.match(r"^[A-Z]{1,5}$") & (d["Test Issue"] == "N") & (d["ETF"] == "N")]
    d = d[~d["Security Name"].str.contains(r"Warrant|Right|Unit|Preferred|Notes|Depositary", case=False, regex=True)]
    return d["Symbol"].tolist()

# ---------- Veri ----------
def load_daily(tickers, period="2y", chunk=100, minlen=150, progress=None):
    out, tickers = {}, list(tickers)
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period=period, interval="1d", group_by="ticker",
                              auto_adjust=True, threads=True, progress=False)
        except Exception:
            continue
        if progress:
            progress(min(1.0, (i + chunk) / max(1, len(tickers))))
        for t in part:
            try:
                d = raw[t] if isinstance(raw.columns, pd.MultiIndex) and t in raw.columns.get_level_values(0) else raw
                d = d.copy()
                if isinstance(d.columns, pd.MultiIndex):
                    d.columns = d.columns.get_level_values(0)
                d = d[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(d) >= minlen:
                    out[t] = d
            except Exception:
                pass
    return out

def liquid(symbols, topn, minvol, pmin=5, pmax=1e9, progress=None):
    """Fiyatı pmin-pmax arasında ve 50 günlük ortalama $ hacmi yeterli olan en likit hisseler (12 saat önbellek)."""
    key = f"{len(symbols)}-{topn}-{int(minvol)}-{pmin}-{pmax}"
    try:
        c = json.load(open(LIQ))
        if c["key"] == key and time.time() - c["t"] < 12 * 3600:
            return c["syms"]
    except Exception:
        pass
    rows = []
    for s, d in load_daily(symbols, "1mo", 300, 15, progress).items():
        dv = (d.Close * d.Volume).tail(20).mean()
        if pmin <= d.Close.iloc[-1] <= pmax and dv >= minvol:
            rows.append((s, dv))
    syms = [s for s, _ in sorted(rows, key=lambda x: -x[1])[:topn]]
    try:
        json.dump(dict(key=key, t=time.time(), syms=syms), open(LIQ, "w"))
    except Exception:
        pass
    return syms

# ---------- Uyarılar ----------
def sig_type(p):
    return "kirilim" if not p["done"] else "geri" if "GERİ" in p["label"] else "alim"

def fmt(sym, a, capital, risk, frac=False):
    p = a["plan"]
    q = lots(capital, risk, p, frac)
    w = (" ⚠ " + "; ".join(p["warn"])) if p["warn"] else ""
    return (f"🟢 {sym} — {p['label']}\nSkor {a['score']:.0f} · Fiyat ${p['price']:.2f}\n"
            f"Kırılım ${p['hh']:.2f} · Alış ${p['entry']:.2f} · Stop ${p['stop']:.2f}\n"
            f"Hedef ${p['t1']:.2f} / ${p['t2']:.2f} · Lot {q}{w}\nYatırım tavsiyesi değildir.")

def detect_new(A, capital, risk, frac=False):
    cur = {f"{s}:{sig_type(a['plan'])}": s for s, a in A.items() if a["plan"]["kind"] == "AL"}
    try:
        old = set(json.load(open(STATE)))
    except Exception:
        old = set()
    try:
        json.dump(sorted(cur), open(STATE, "w"))
    except Exception:
        pass
    return [dict(sym=s, msg=fmt(s, A[s], capital, risk, frac)) for k, s in cur.items() if k not in old]

def telegram(token, chat, text):
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15)
        return True
    except Exception:
        return False

# ---------- Ana tarama ----------
def run_scan(mode, manual, topn, minvol, cost, capital, risk, tok="", chat="", pmin=0, pmax=1e9, frac=False):
    if mode.startswith("Nasdaq-100"):
        syms, info = universe_ndx(), "Nasdaq-100 hisseleri tarandı"
    elif mode.startswith("Geniş"):
        allsyms = universe_all()
        syms = liquid(allsyms, topn, minvol, pmin, pmax)
        info = f"Nasdaq’ta {len(allsyms)} hisse bulundu, en likit {len(syms)} tanesi analiz edildi"
    else:
        syms, info = re.findall(r"[A-Za-z.\-]+", manual.upper()), "Elle girilen liste"
    A = {}
    for t, raw in load_daily(dict.fromkeys(syms)).items():
        try:
            if not pmin <= raw.Close.iloc[-1] <= pmax:
                continue
            df = prep(raw)
            if len(df) < 100:
                continue
            bt = run_all(df, cost)
            sc, why = score(df, max(r["sh"] for r in bt.values()))
            A[t] = dict(df=df, bt=bt, score=sc, why=why, plan=plan(df, sc))
        except Exception:
            continue
    new = detect_new(A, capital, risk, frac) if A else []
    if tok and chat:
        for n in new:
            telegram(tok, chat, n["msg"])
    return A, new, info

