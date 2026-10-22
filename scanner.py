"""Hisseleri kendi bulan tarayıcı + uyarı modülü (Streamlit'ten bağımsız)."""
import io, json, os, re, time, urllib.parse, urllib.request
import pandas as pd
import yfinance as yf
from engine import lots, oos, plan, prep, run_all, score

VERSION = "v6"


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alerts_state.json")
LIQ = os.path.join(HERE, "liquid_cache.json")
LOGF = os.path.join(HERE, "signals_log.json")
COST = 0.0005
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
            f"Hedef ${p['t1']:.2f} / ${p['t2']:.2f} · Lot {q}{w}\n⏱ {p['max_days']} işlem günü içinde hedefe gitmezse çık. Stop'u aşağı çekme.\nYatırım tavsiyesi değildir.")

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
    return [dict(sym=s, msg=fmt(s, A[s], capital, risk, frac), plan=A[s]["plan"], score=A[s]["score"],
                 date=str(A[s]["df"].index[-1].date())) for k, s in cur.items() if k not in old]

def telegram(token, chat, text):
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15)
        return True
    except Exception:
        return False

# ---------- Piyasa filtresi, bilanço, sinyal günlüğü ----------
def market_status():
    """QQQ 50 günlük ortalamanın üstündeyse piyasa yükselişte sayılır."""
    try:
        c = load_daily(["QQQ"], "1y", 100, 100)["QQQ"].Close
        ok = bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1])
        return ok, f"QQQ {c.iloc[-1]:.2f}, 50 günlük ortalamanın {'üstünde (yükseliş)' if ok else 'altında (düşüş)'}"
    except Exception:
        return True, "QQQ verisi alınamadı, piyasa filtresi uygulanmadı"

def earnings_days(sym):
    """Bir sonraki bilançoya kaç gün var (bilinmiyorsa None)."""
    try:
        cal = yf.Ticker(sym).calendar
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ed:
            d = ed[0] if isinstance(ed, (list, tuple)) else ed
            return (pd.Timestamp(d).normalize() - pd.Timestamp.today().normalize()).days
    except Exception:
        pass
    return None

CLOSED = ("hedefe ulaştı", "stopa değdi", "süre doldu", "girilmedi")

def load_log():
    try:
        return json.load(open(LOGF, encoding="utf-8"))
    except Exception:
        return []

def save_log(L):
    try:
        json.dump(L, open(LOGF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

def log_add(rec):
    L = load_log(); L.append(rec); save_log(L)

def merge_log(data):
    L = load_log()
    keys = {(r["sym"], r["tarih"], r["kaynak"], r["entry"]) for r in L}
    for r in data:
        if (r.get("sym"), r.get("tarih"), r.get("kaynak"), r.get("entry")) not in keys:
            L.append(r)
    save_log(L)

def log_new(new):
    L = load_log()
    for n in new:
        p = n["plan"]
        if any(r["sym"] == n["sym"] and r["tarih"] == n["date"] and r["kaynak"] == "program" for r in L):
            continue
        L.append(dict(sym=n["sym"], tarih=n["date"], kaynak="program", etiket=p["label"], skor=round(n["score"]),
                      entry=round(float(p["entry"]), 2), stop=round(float(p["stop"]), 2), t1=round(float(p["t1"]), 2),
                      max_days=p["max_days"], durum="bekliyor", giris_tarihi=None, cikis_tarihi=None, sonuc=None))
    save_log(L)

def evaluate(rec, df):
    """Kayıttan sonraki günlük barlara bakıp sonucu belirler (aynı gün stop ve hedef varsa stop sayılır)."""
    d = df[df.index > pd.Timestamp(rec["tarih"])]
    state = "açık" if rec["kaynak"] == "elle" else "bekliyor"
    gir = rec["tarih"] if state == "açık" else None
    cik = sonuc = None
    wait = opn = 0
    for t, r in d.iterrows():
        day = str(t.date())
        if state == "bekliyor":
            wait += 1
            if r.High >= rec["entry"]:
                state, gir = "açık", day
            elif wait >= 5:
                state, cik = "girilmedi", day
                break
            else:
                continue
        opn += 1
        if r.Low <= rec["stop"]:
            state, sonuc, cik = "stopa değdi", rec["stop"] / rec["entry"] - 1, day; break
        if r.High >= rec["t1"]:
            state, sonuc, cik = "hedefe ulaştı", rec["t1"] / rec["entry"] - 1, day; break
        if opn >= rec["max_days"]:
            state, sonuc, cik = "süre doldu", r.Close / rec["entry"] - 1, day; break
    if sonuc is not None:
        sonuc -= 2 * COST
    rec.update(durum=state, giris_tarihi=gir, cikis_tarihi=cik, sonuc=sonuc)

def update_log(A):
    L = load_log()
    need = [r for r in L if r["durum"] not in CLOSED]
    miss = sorted({r["sym"] for r in need if r["sym"] not in A})
    extra = load_daily(miss, "6mo", 100, 20) if miss else {}
    for r in need:
        df = A[r["sym"]]["df"] if r["sym"] in A else extra.get(r["sym"])
        if df is not None:
            try:
                evaluate(r, df)
            except Exception:
                pass
    save_log(L)
    return L

# ---------- Ana tarama ----------
def run_scan(mode, manual, topn, minvol, cost, capital, risk, tok="", chat="", pmin=0, pmax=1e9, frac=False,
             maxatr=1.0, use_mkt=True, use_earn=True):
    if mode.startswith("Nasdaq-100"):
        syms, info = universe_ndx(), "Nasdaq-100 hisseleri tarandı"
    elif mode.startswith("Geniş"):
        allsyms = universe_all()
        syms = liquid(allsyms, topn, minvol, pmin, pmax)
        info = f"Nasdaq’ta {len(allsyms)} hisse bulundu, en likit {len(syms)} tanesi analiz edildi"
    else:
        syms, info = re.findall(r"[A-Za-z.\-]+", manual.upper()), "Elle girilen liste"
    mkt_ok, mkt_txt = market_status() if use_mkt else (True, "Piyasa filtresi kapalı")
    A = {}
    for t, raw in load_daily(dict.fromkeys(syms)).items():
        try:
            if not pmin <= raw.Close.iloc[-1] <= pmax:
                continue
            df = prep(raw)
            if len(df) < 100:
                continue
            bt = run_all(df, cost)
            o = oos(bt)
            sc, why = score(df, o["test_sh"])
            A[t] = dict(df=df, bt=bt, oos=o, score=sc, why=why, plan=plan(df, sc, mkt_ok, maxatr))
        except Exception:
            continue
    if use_earn:
        for s in [s for s, a in A.items() if a["plan"]["kind"] == "AL"][:20]:
            d = earnings_days(s)
            if d is not None and 0 <= d <= 7:
                p = A[s]["plan"]
                p["kind"], p["label"] = "IZLE", "İZLE (bilanço yakın)"
                p["warn"].append(f"Bilanço {d} gün sonra: sinyal güvenilmez")
    new = detect_new(A, capital, risk, frac) if A else []
    if A:
        log_new(new)
        update_log(A)
    if tok and chat:
        for n in new:
            telegram(tok, chat, n["msg"])
    return A, new, info, (mkt_ok, mkt_txt)
