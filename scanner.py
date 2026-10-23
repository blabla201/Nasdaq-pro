"""Hisseleri kendi bulan tarayıcı + uyarı modülü (Streamlit'ten bağımsız)."""
import io, json, os, re, time, urllib.parse, urllib.request
import numpy as np
import pandas as pd
import yfinance as yf
from engine import lots, oos, plan, prep, run_all, score, trendline

VERSION = "v10"


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alerts_state.json")
LIQ = os.path.join(HERE, "liquid_cache.json")
LOGF = os.path.join(HERE, "signals_log.json")
RADARF = os.path.join(HERE, "radar_cache.json")
SURGEF = os.path.join(HERE, "surge_cache.json")
STATE2 = os.path.join(HERE, "surge_state.json")
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

def liquid_table(symbols, progress=None):
    """Tüm hisselerin son fiyatı ve 20 günlük ortalama $ hacmi (12 saat önbellek, tek indirme)."""
    key = f"tab-{len(symbols)}"
    try:
        c = json.load(open(LIQ))
        if c.get("key") == key and time.time() - c["t"] < 12 * 3600:
            return c["rows"]
    except Exception:
        pass
    rows = []
    for s, d in load_daily(symbols, "1mo", 300, 15, progress).items():
        try:
            rows.append([s, float(d.Close.iloc[-1]), float((d.Close * d.Volume).tail(20).mean())])
        except Exception:
            continue
    try:
        json.dump(dict(key=key, t=time.time(), rows=rows), open(LIQ, "w"))
    except Exception:
        pass
    return rows

def liquid(symbols, topn, minvol, pmin=5, pmax=1e9, progress=None):
    """Fiyatı pmin-pmax arasında ve ortalama $ hacmi yeterli olan en likit hisseler."""
    sel = [(s, dv) for s, p, dv in liquid_table(symbols, progress) if pmin <= p <= pmax and dv >= minvol]
    return [s for s, _ in sorted(sel, key=lambda x: -x[1])[:topn]]

# ---------- Uyarılar ----------
def sig_type(p):
    return {"pre": "kirilim", "extended": "bekle", "retest": "geri_test"}.get(p.get("stage"), "diger")

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

# ---------- Ani hareket radarı ve hisse inceleme ----------
def _tags(price, ch, vx, dollar):
    t = []
    if price < 1: t.append("1$ altı (çıkarılma riski)")
    elif price < 3: t.append("3$ altı")
    if ch > 1.0: t.append("tek günde %100+")
    if vx > 20: t.append("hacim 20x+")
    if dollar < 5e6: t.append("düşük likidite")
    return t

def radar(symbols, min_move=0.3, min_volx=5.0, min_dollar=1e6, progress=None):
    """Bugün fiyatı ve hacmi birlikte sıçrayan hisseler. Alım listesi değil, izleme listesidir."""
    rows = []
    for s, d in load_daily(symbols, "2mo", 300, 25, progress).items():
        try:
            c, v = d.Close, d.Volume
            ch = float(c.iloc[-1] / c.iloc[-2] - 1)
            vx = float(v.iloc[-1] / max(v.iloc[-21:-1].mean(), 1))
            dv = float(c.iloc[-1] * v.iloc[-1])
            if ch >= min_move and vx >= min_volx and dv >= min_dollar and c.iloc[-1] >= 0.3:
                rows.append(dict(Sembol=s, Fiyat=float(c.iloc[-1]), Bugün=ch, Gün5=float(c.iloc[-1] / c.iloc[-6] - 1),
                                 Hacimx=vx, GünlükDolar=dv, Tarih=str(d.index[-1].date()), Uyarı=", ".join(_tags(float(c.iloc[-1]), ch, vx, dv))))
        except Exception:
            continue
    return sorted(rows, key=lambda r: -r["Bugün"])

def run_radar(min_move, min_volx, progress=None):
    key = f"{min_move}-{min_volx}"
    try:
        c = json.load(open(RADARF, encoding="utf-8"))
        if c["key"] == key and time.time() - c["t"] < 15 * 60:
            return c["rows"]
    except Exception:
        pass
    rows = radar(universe_all(), min_move, min_volx, 1e6, progress)
    try:
        json.dump(dict(key=key, t=time.time(), rows=rows), open(RADARF, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return rows

def inspect_symbol(sym):
    """Tek bir hisseyi incele: fiyat hareketi, risk işaretleri ve programın görüşü."""
    raw = load_daily([sym], "1y", 100, 30).get(sym)
    if raw is None:
        return None
    c, v = raw.Close, raw.Volume
    X = dict(sym=sym, raw=raw, price=float(c.iloc[-1]), ch1=float(c.iloc[-1] / c.iloc[-2] - 1),
             ch5=float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else 0.0,
             ch20=float(c.iloc[-1] / c.iloc[-21] - 1) if len(c) > 21 else 0.0,
             vx=float(v.iloc[-1] / max(v.iloc[-21:-1].mean(), 1)), hi=float(raw.High.tail(252).max()), lo=float(raw.Low.tail(252).min()),
             from_peak=float(c.iloc[-1] / c.tail(60).max() - 1), dollar=float(c.iloc[-1] * v.iloc[-1]))
    fl = []
    p = X["price"]
    if p < 1: fl.append("1$ altında: Nasdaq’tan çıkarılma (delisting) riski ve çok geniş alış-satış farkı var.")
    elif p < 3: fl.append("3$ altında: program normalde bu hisseleri önermez.")
    big = float((c.pct_change().tail(30)).max())
    if big > 1.0: fl.append(f"Son 30 günde tek günde %{big*100:.0f} sıçrama olmuş: spekülatif hareket, genelde sonrasında sert geri çekilme gelir.")
    if X["vx"] > 10: fl.append(f"Bugünkü hacim ortalamanın {X['vx']:.0f} katı.")
    if X["from_peak"] < -0.4: fl.append(f"Son 60 günün zirvesinden %{abs(X['from_peak'])*100:.0f} aşağıda: yükselişin büyük kısmı geri verilmiş.")
    if X["dollar"] < 5e5: fl.append("Günlük işlem hacmi düşük: istediğiniz fiyattan çıkamayabilirsiniz.")
    if X["ch1"] > 0.3: fl.append("Bugün çok sert yükselmiş: kâr çoktan oluşmuş olabilir, sonradan girenler çoğu zaman tepeden alır.")
    X["flags"] = fl
    try:
        df = prep(raw)
        if len(df) >= 60:
            bt = run_all(df, COST)
            o = oos(bt)
            sc, why = score(df, o["test_sh"])
            X.update(df=df, score=sc, why=why, plan=plan(df, sc, True, 1.0))
    except Exception:
        pass
    return X

# ---------- Ani sıçrama: kısa vade, geçmiş istatistikle karar ----------
SURGE = dict(minmove=.08, maxmove=.6, minvx=3.0, pos=.75)
S_SLIP, S_COST, S_GAP = .005, .01, .03
CFG_DAYS = (1, 2, 3, 5)
CFG_STOPS = (.04, .06, .08)
EMPTY = dict(n=0, win=0.0, exp=0.0, med=0.0, p10=0.0, train_exp=0.0, test_exp=0.0, test_n=0, days=1, stop=.06, tgt=.12, skipped=0, ok=False)

def surge_flags(d):
    """Erken sıçrama: %8-60 yükseliş, hacim >= 3x, günü zirveye yakın kapanış, 20 günlük zirveyi geçmiş."""
    c, h, l, v = d.Close, d.High, d.Low, d.Volume
    ch = c.pct_change()
    vx = v / v.shift(1).rolling(20).mean()
    pos = (c - l) / (h - l).replace(0, np.nan)
    hh = h.shift(1).rolling(20).max()
    f = (ch >= SURGE["minmove"]) & (ch <= SURGE["maxmove"]) & (vx >= SURGE["minvx"]) & (pos >= SURGE["pos"]) & (c > hh) & (c * v >= 1e6) & (c >= 1)
    return f.fillna(False)

def _outcome(d, i, stop_pct, days):
    """Ertesi gün açılıştan gir (açılış kapanışın %3'ünden fazla yukarıdaysa girme); stop, 2x hedef, zaman stopu; kayma+maliyet dahil.
    Aynı gün hem stop hem hedef görülürse stop sayılır (temkinli). None = girilmedi."""
    if float(d.Open.iloc[i + 1]) > float(d.Close.iloc[i]) * (1 + S_GAP):
        return None
    entry = float(d.Open.iloc[i + 1]) * (1 + S_SLIP)
    stop, tgt = entry * (1 - stop_pct), entry * (1 + 2 * stop_pct)
    last = min(i + days, len(d) - 1)
    for j in range(i + 1, last + 1):
        if d.Low.iloc[j] <= stop: return -stop_pct - S_COST
        if d.High.iloc[j] >= tgt: return 2 * stop_pct - S_COST
    return float(d.Close.iloc[last]) / entry - 1 - S_COST

def _stats(res):
    a = np.array([o for _, o in res], float); n = len(a)
    if n == 0:
        return dict(EMPTY)
    cut = int(n * 2 / 3)
    tr, te = a[:cut], a[cut:]
    return dict(n=n, win=float((a > 0).mean()), exp=float(a.mean()), med=float(np.median(a)), p10=float(np.percentile(a, 10)),
                train_exp=float(tr.mean()) if len(tr) else 0.0, test_exp=float(te.mean()) if len(te) else 0.0, test_n=int(len(te)))

def surge_study(data, days_allowed=CFG_DAYS):
    """Her (vade, stop) ayarını geçmiş olaylarda dener. Ayar seçimi ilk 2/3'te, doğrulama son 1/3'te yapılır."""
    events = []
    for s, d in data.items():
        try:
            f = surge_flags(d).values
            i, n = 25, len(d)
            while i < n - 6:
                if f[i]:
                    events.append((d.index[i], s, i)); i += 6
                else:
                    i += 1
        except Exception:
            continue
    events.sort(key=lambda e: e[0])
    cfgs = []
    for days in days_allowed:
        for sp in CFG_STOPS:
            res = []
            for dt, s, i in events:
                o = _outcome(data[s], i, sp, days)
                if o is not None:
                    res.append((dt, o))
            st_ = _stats(res)
            st_.update(days=days, stop=sp, tgt=2 * sp, skipped=len(events) - len(res))
            st_["ok"] = bool(st_["n"] >= 30 and st_["win"] >= .35 and st_["exp"] > .005 and st_["train_exp"] > 0
                             and st_["test_exp"] > 0 and st_["test_n"] >= 10)
            cfgs.append(st_)
    best = max(cfgs, key=lambda c: (c["ok"], c["train_exp"])) if cfgs else dict(EMPTY)
    return dict(cfgs=cfgs, best=best, n_events=len(events))

def surge_levels(price, stop_pct=.06):
    return dict(entry=price, limit=price * (1 + S_GAP), stop=price * (1 - stop_pct), t1=price * (1 + 2 * stop_pct), R=price * stop_pct)

def run_surge(symbols, pmax, days_allowed=(1,), progress=None):
    """Son 1 yılda benzer sıçramaların sonucunu ölçer, bugünkü adayları bulur. Sonuç 6 saat saklanır."""
    key = f"{pmax}-{len(symbols)}-{days_allowed}-{time.strftime('%Y%m%d')}-v10"
    try:
        c = json.load(open(SURGEF, encoding="utf-8"))
        if c["key"] == key and time.time() - c["t"] < 6 * 3600:
            return c["res"]
    except Exception:
        pass
    data = load_daily(symbols, "1y", 300, 60, progress)
    study = surge_study(data, days_allowed)
    rows = []
    for s, d in data.items():
        try:
            if surge_flags(d).iloc[-1] and d.Close.iloc[-1] <= pmax:
                c_, v_ = d.Close, d.Volume
                rows.append(dict(sym=s, price=float(c_.iloc[-1]), change=float(c_.iloc[-1] / c_.iloc[-2] - 1),
                                 volx=float(v_.iloc[-1] / v_.iloc[-21:-1].mean()), date=str(d.index[-1].date())))
        except Exception:
            continue
    rows.sort(key=lambda r: -r["volx"])
    best = study["best"]
    res = dict(stats=best, cfgs=study["cfgs"], rows=rows, ok=bool(best["ok"]), ts=time.strftime("%H:%M"),
               n_syms=len(data), n_events=study.get("n_events", 0))
    try:
        json.dump(dict(key=key, t=time.time(), res=res), open(SURGEF, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return res

def surge_msg(r, stats, capital, risk, frac=False):
    lv = surge_levels(r["price"], stats["stop"])
    q = lots(capital, risk, dict(R=lv["R"], entry=lv["entry"]), frac)
    cikis = "Yarın kapanışta mutlaka çık." if stats["days"] == 1 else f"{stats['days']} işlem günü içinde hedefe gitmezse çık."
    return (f"🚀 {r['sym']} — erken sıçrama adayı\nFiyat ${r['price']:.2f} · bugün %{r['change']*100:+.0f} · hacim {r['volx']:.0f}x\n"
            f"Alış: yarın açılışta, en fazla ${lv['limit']:.2f} · Stop alıştan %{stats['stop']*100:.0f} aşağı (≈${lv['stop']:.2f}) · "
            f"Hedef %{stats['tgt']*100:.0f} yukarı (≈${lv['t1']:.2f}) · Lot {q}\n⏱ {cikis} Açılış bugünkü kapanışın %3'ünden fazla üstündeyse alma.\n"
            f"Geçmiş {stats['n']} benzer olay: kazanç %{stats['win']*100:.0f}, ortalama %{stats['exp']*100:+.1f}\nYatırım tavsiyesi değildir.")

def surge_alerts(SG, tok, chat, capital, risk, frac=False):
    """Yeni (bildirilmemiş) ve istatistiği pozitif sıçrama adaylarını Telegram'a gönderir ve günlüğe yazar."""
    if not (SG and SG["ok"]):
        return []
    try:
        old = set(json.load(open(STATE2)))
    except Exception:
        old = set()
    keys = {f"{r['sym']}:{r['date']}" for r in SG["rows"]}
    new = [r for r in SG["rows"] if f"{r['sym']}:{r['date']}" not in old]
    try:
        json.dump(sorted(old | keys), open(STATE2, "w"))
    except Exception:
        pass
    st_ = SG["stats"]
    for r in new:
        lv = surge_levels(r["price"], st_["stop"])
        log_add(dict(sym=r["sym"], tarih=r["date"], kaynak="sıçrama", etiket="erken sıçrama", skor=None, entry=round(r["price"], 2),
                     stop=round(lv["stop"], 2), t1=round(lv["t1"], 2), max_days=int(st_["days"]), durum="bekliyor",
                     giris_tarihi=None, cikis_tarihi=None, sonuc=None))
        if tok and chat:
            telegram(tok, chat, surge_msg(r, st_, capital, risk, frac))
    return new

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
            A[t] = dict(df=df, bt=bt, oos=o, score=sc, why=why, plan=plan(df, sc, mkt_ok, maxatr), trend=trendline(df))
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
