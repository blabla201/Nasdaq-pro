import io, json, os, re, time, urllib.parse, urllib.request
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

SURUM = "v3 (tek dosya)"

# ===== MOTOR =====

# ---------- Göstergeler ----------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    au = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ad = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + au / ad.replace(0, np.nan))).fillna(50)

def atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def vwap_roll(df, n=20):
    tp = (df.High + df.Low + df.Close) / 3
    return (tp * df.Volume).rolling(n).sum() / df.Volume.rolling(n).sum()

def session_vwap(intra):
    """Gün içi (5dk) veride her seansın kendi VWAP'ı."""
    tp = (intra.High + intra.Low + intra.Close) / 3
    day = intra.index.date
    return (tp * intra.Volume).groupby(day).cumsum() / intra.Volume.groupby(day).cumsum()

def prep(df):
    df = df.copy()
    c = df.Close
    df["e9"], df["e21"] = ema(c, 9), ema(c, 21)
    df["vw"], df["rsi"], df["atr"] = vwap_roll(df), rsi(c), atr(df)
    df["hh20"] = df.High.shift(1).rolling(20).max()
    df["ll10"] = df.Low.shift(1).rolling(10).min()
    return df.dropna()

# ---------- Stratejiler (sinyal: gün kapanışında 1=pozisyonda) ----------
def _latch(enter, exit_):
    out, p = [], 0
    for e, x in zip(enter.values, exit_.values):
        if p == 0 and e: p = 1
        elif p == 1 and x: p = 0
        out.append(p)
    return pd.Series(out, index=enter.index, dtype=float)

NAMES = {
    "ema9": "EMA9 (fiyat > EMA9)",
    "x921": "EMA9 / EMA21 kesişimi",
    "vwap": "VWAP (20 gün) üstü",
    "combo": "Birleşik: EMA9+VWAP+RSI",
    "rsi": "RSI dönüş (35/55)",
    "brk": "20 gün kırılım",
}
FUNCS = {
    "ema9": lambda d: (d.Close > d.e9).astype(float),
    "x921": lambda d: (d.e9 > d.e21).astype(float),
    "vwap": lambda d: (d.Close > d.vw).astype(float),
    "combo": lambda d: ((d.Close > d.e9) & (d.Close > d.vw) & (d.rsi > 50) & (d.rsi < 75)).astype(float),
    "rsi": lambda d: _latch(d.rsi < 35, d.rsi > 55),
    "brk": lambda d: _latch(d.Close > d.hh20, d.Close < d.ll10),
}

# ---------- Backtest ----------
def backtest(df, pos, cost=0.0005):
    """Sinyal t kapanışında oluşur, getiri t+1'den itibaren alınır (ileri bakış yok)."""
    ret = df.Close.pct_change().fillna(0)
    p = pos.shift(1).fillna(0)
    turn = p.diff().abs().fillna(p.abs())
    sret = p * ret - turn * cost
    eq, bh = (1 + sret).cumprod(), (1 + ret).cumprod()
    dd = eq / eq.cummax() - 1
    chg = pos.diff().fillna(pos)
    trades, entry = [], None
    for t, v in chg.items():
        if v == 1 and entry is None:
            entry = t
        elif v == -1 and entry is not None:
            trades.append((entry, t, df.Close[t] / df.Close[entry] - 1 - 2 * cost)); entry = None
    if entry is not None:
        trades.append((entry, df.index[-1], df.Close.iloc[-1] / df.Close[entry] - 1 - 2 * cost))
    tr = pd.DataFrame(trades, columns=["Giriş", "Çıkış", "Getiri"])
    w, l = tr[tr.Getiri > 0].Getiri, tr[tr.Getiri <= 0].Getiri
    sd = sret.std()
    tot = eq.iloc[-1] - 1
    return dict(
        eq=eq, bh=bh, dd=dd, trades=tr, tot=tot, bhr=bh.iloc[-1] - 1, mdd=dd.min(),
        n=len(tr), win=len(w) / len(tr) if len(tr) else 0,
        aw=w.mean() if len(w) else 0.0, al=l.mean() if len(l) else 0.0,
        pf=(w.sum() / -l.sum()) if len(l) and l.sum() != 0 else (9.99 if len(w) else 0.0),
        sh=(sret.mean() / sd * np.sqrt(252)) if sd else 0.0,
        rr=tot / abs(dd.min()) if dd.min() else 0.0, expo=p.mean(),
    )

def run_all(df, cost=0.0005):
    return {k: backtest(df, f(df), cost) for k, f in FUNCS.items()}

# ---------- Skor ve işlem planı ----------
def score(df, best_sharpe):
    c, last = df.Close, df.iloc[-1]
    r, add = [], lambda t, p: r.append((t, p))
    add("Fiyat EMA9 üzerinde", 15) if last.Close > last.e9 else add("Fiyat EMA9 altında", -10)
    add("EMA9 > EMA21 (kısa trend yukarı)", 15) if last.e9 > last.e21 else add("EMA9 < EMA21 (trend zayıf)", -10)
    add("Fiyat 20g VWAP üzerinde", 15) if last.Close > last.vw else add("Fiyat VWAP altında", -10)
    r20 = c.iloc[-1] / c.iloc[-21] - 1
    add(f"20 günlük getiri %{r20*100:+.1f}", 20 if r20 > .08 else 10 if r20 > 0 else -10)
    R = last.rsi
    if 50 <= R <= 70: add(f"RSI {R:.0f}: sağlıklı güç bölgesi", 15)
    elif R > 75: add(f"RSI {R:.0f}: aşırı alım riski", -10)
    elif R < 30: add(f"RSI {R:.0f}: aşırı satım, tepki olası", 5)
    else: add(f"RSI {R:.0f}: nötr/zayıf", 0)
    vr = df.Volume.tail(5).mean() / df.Volume.tail(20).mean()
    add(f"Hacim ortalamanın {vr:.1f} katı", 10) if vr > 1.2 else add(f"Hacim normal ({vr:.1f}x)", 0)
    add(f"Geçmişte en iyi strateji Sharpe {best_sharpe:.1f}", 10) if best_sharpe > .8 else add("Geçmiş strateji sonuçları zayıf", -5)
    return float(np.clip(50 + sum(p for _, p in r) / 1.5, 0, 100)), r

def plan(df, sc):
    last = df.iloc[-1]
    c, hh, a = last.Close, last.hh20, last.atr
    done, far = c > hh, c > hh * 1.03
    entry = (max(hh, last.e9) if far else c) if done else hh * 1.002
    stop = entry - 1.5 * a
    R = entry - stop
    warn = []
    if last.rsi > 75: warn.append("RSI aşırı alım bölgesinde")
    if c < last.vw: warn.append("Fiyat VWAP altında")
    if far: warn.append("Fiyat kırılımdan uzaklaştı: geri çekilmede girin, kovalamayın")
    if c < 5: warn.append("Düşük fiyatlı hisse: spread ve oynaklık riski yüksek, küçük pozisyon kullanın")
    if R / entry > .06: warn.append(f"Stop mesafesi geniş (%{R/entry*100:.1f})")
    kind = "AL" if sc >= 70 and c > last.e21 else "IZLE" if sc >= 50 else "UZAK"
    if kind == "AL":
        label = ("GERİ ÇEKİLME BEKLE" if far else "ALIM BÖLGESİ") if done else f"KIRILIMI BEKLE (%{(hh/c-1)*100:.1f} uzakta)"
    else:
        label = "İZLE" if kind == "IZLE" else "UZAK DUR"
    return dict(price=c, hh=hh, entry=entry, stop=stop, R=R, t1=entry + 2 * R, t2=entry + 3 * R,
                kind=kind, label=label, warn=warn, done=done)

def lots(capital, risk_pct, p, fractional=False):
    """Risk yüzdesine göre adet. fractional=True: parçalı (kesirli) hisse alınabilir."""
    if p["R"] <= 0:
        return 0
    q = min(capital * risk_pct / 100 / p["R"], capital / p["entry"])
    return round(q, 2) if fractional else int(q)

# ===== TARAYICI =====

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
def load_daily(tickers, period="2y", chunk=100, minlen=150):
    out, tickers = {}, list(tickers)
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period=period, interval="1d", group_by="ticker",
                              auto_adjust=True, threads=True, progress=False)
        except Exception:
            continue
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

def liquid(symbols, topn, minvol, pmin=5, pmax=1e9):
    """Fiyatı pmin-pmax arasında ve 50 günlük ortalama $ hacmi yeterli olan en likit hisseler (12 saat önbellek)."""
    key = f"{len(symbols)}-{topn}-{int(minvol)}-{pmin}-{pmax}"
    try:
        c = json.load(open(LIQ))
        if c["key"] == key and time.time() - c["t"] < 12 * 3600:
            return c["syms"]
    except Exception:
        pass
    rows = []
    for s, d in load_daily(symbols, "3mo", 200, 30).items():
        dv = (d.Close * d.Volume).tail(50).mean()
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

# ===== ARAYÜZ =====


st.set_page_config("Nasdaq Strateji Laboratuvarı", page_icon="📈", layout="wide")
st.markdown("""<style>
.stApp{background:#0a1330;color:#e8edff}
[data-testid=stSidebar]{background:#0d1838}
[data-testid=stMetric]{background:#111d44;border:1px solid #223268;border-radius:12px;padding:10px}
[data-testid=stExpander]{background:#111d44;border:1px solid #223268;border-radius:14px}
button[data-baseweb=tab]{font-size:15px}
.block-container{padding-top:1.2rem}
</style>""", unsafe_allow_html=True)

DEFAULT = ("AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO COST NFLX AMD ADBE PEP CSCO TMUS INTC "
           "QCOM TXN AMGN INTU AMAT BKNG ISRG LRCX MU PANW ADI KLAC SBUX GILD")

# ---------- Veri ----------
def _clean(d):
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d[["Open", "High", "Low", "Close", "Volume"]].dropna()

@st.cache_data(ttl=60)
def load_intra(sym):
    d = yf.download(sym, period="5d", interval="5m", auto_adjust=True, progress=False)
    d = _clean(d)
    d["svwap"] = session_vwap(d)
    return d

@st.cache_data(ttl=300, show_spinner="Hisseler taranıyor… (geniş taramada 1-3 dk sürebilir)")
def scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat, pmin, pmax, frac):
    return run_scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat, pmin, pmax, frac)

# ---------- Kenar çubuğu ----------
with st.sidebar:
    st.header("Ayarlar")
    mode = st.radio("Hisseleri nasıl bulsun?", ["Nasdaq-100 (hızlı)", "Geniş Nasdaq taraması", "Elle liste"], index=1)
    topn, minvol, manual = 0, 0, ""
    if mode == "Geniş Nasdaq taraması":
        topn = st.slider("Analiz edilecek en likit hisse sayısı", 30, 300, 120, 10)
        minvol = st.number_input("Min. günlük işlem hacmi (milyon $)", 1, 1000, 10, 1) * 1e6
    elif mode == "Elle liste":
        manual = st.text_area("Hisseler (boşlukla ayır)", DEFAULT, height=100)
    capital = st.number_input("Sermaye ($)", 10, 10_000_000, 100, 10)
    risk = st.number_input("İşlem başı risk (%)", 0.1, 10.0, 1.0, 0.1)
    cost = st.number_input("İşlem maliyeti (her yön, %)", 0.0, 1.0, 0.05, 0.01) / 100
    c1, c2 = st.columns(2)
    pmin = c1.number_input("Min. fiyat ($)", 0.5, 10_000.0, 3.0, 0.5)
    pmax = c2.number_input("Maks. fiyat ($)", 1.0, 100_000.0, 30.0, 1.0)
    frac = st.toggle("Parçalı (kesirli) hisse alabilirim", False)
    with st.expander("🔔 Telegram uyarısı"):
        tok = st.text_input("Bot token", os.getenv("TELEGRAM_TOKEN", ""), type="password")
        chat = st.text_input("Chat ID", os.getenv("TELEGRAM_CHAT_ID", ""))
    auto = st.toggle("Otomatik tara (5 dk)", True)
    if st.button("🔄 Şimdi tara", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Veri: Yahoo Finance (yfinance), genelde ~15 dk gecikmeli. Telegram için @BotFather ile bot açıp token ve chat ID girin.")
if auto:
    st_autorefresh(interval=300_000, key="auto")

A, NEW, INFO = scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat, pmin, pmax, frac)
if not A:
    st.error("Bu fiyat aralığında analiz edilecek hisse bulunamadı. Fiyat aralığını genişletin, hacim şartını düşürün ya da internet bağlantısını kontrol edin.")
    st.stop()
st.caption(f"🔎 {INFO} · {len(A)} hisse analiz edildi")

pc = lambda x, d=1: f"{x*100:+.{d}f}%"
f = lambda v: f"${v:,.2f}"
def layout(fig, h=380):
    fig.update_layout(template="plotly_dark", paper_bgcolor="#0a1330", plot_bgcolor="#0a1330", height=h,
                      margin=dict(l=8, r=8, t=30, b=8), legend=dict(orientation="h", y=1.08),
                      xaxis_rangeslider_visible=False)
    return fig

st.title("📈 Nasdaq Strateji Laboratuvarı")
st.caption("Sürüm: " + SURUM)
t1, t2, t3, t4 = st.tabs(["Sinyal", "Strateji Lab", "Stratejiler", "Backtest & Performans"])

# ---------- 1) Sinyal ----------
with t1:
    L = sorted(A.items(), key=lambda x: -x[1]["score"])
    if NEW:
        st.error("🔔 **YENİ SİNYAL:** " + ", ".join(n["sym"] for n in NEW))
        st.toast("Yeni sinyal: " + ", ".join(n["sym"] for n in NEW), icon="🔔")
    top = [s for s, a in L if a["plan"]["kind"] == "AL"]
    if top:
        st.success(f"⚠ {len(top)} hisse alım adayı: " + ", ".join(f"**{s}** ({A[s]['score']:.0f})" for s in top) +
                   ". Kırılım, alış ve stop seviyelerine uyun; stop olmadan işleme girmeyin.")
    else:
        st.warning("⚠ Şu an güçlü aday yok (skorlar 70’in altında). Beklemek de bir pozisyondur.")
    for s, a in L[:15]:
        p = a["plan"]
        icon = "🟢" if p["kind"] == "AL" else "🟡" if p["kind"] == "IZLE" else "🔴"
        with st.expander(f"{icon} {s} · {f(p['price'])} · skor {a['score']:.0f} · {p['label']}", expanded=p["kind"] == "AL"):
            c = st.columns(4)
            c[0].metric("Kırılım noktası", f(p["hh"]))
            c[1].metric("Alış noktası", f(p["entry"]))
            c[2].metric("Stop noktası", f(p["stop"]), pc(p["stop"] / p["entry"] - 1))
            c[3].metric("Hedef 2R / 3R", f(p["t1"]), f(p["t2"]), delta_color="off")
            q = lots(capital, risk, p, frac)
            st.caption(f"Hisse başı risk {f(p['R'])} (%{p['R']/p['entry']*100:.1f}) · Lot: **{q}** · "
                       f"Maks. kayıp ≈ ${q*p['R']:,.0f} · Pozisyon büyüklüğü ≈ ${q*p['entry']:,.0f}")
            for w in p["warn"]:
                st.warning(w, icon="⚠️")
            st.markdown("**Neden bu skor?**")
            for t, pts in a["why"]:
                st.write(f"{'🟢' if pts > 0 else '🔴' if pts < 0 else '⚪'} {t} ({pts:+d})")
    if len(L) > 15:
        st.subheader("Diğer hisseler")
        st.dataframe(pd.DataFrame([{"Sembol": s, "Skor": round(a["score"]), "Durum": a["plan"]["label"],
                                    "Fiyat": a["plan"]["price"], "Kırılım": a["plan"]["hh"], "Alış": a["plan"]["entry"],
                                    "Stop": a["plan"]["stop"]} for s, a in L[15:]]), hide_index=True, use_container_width=True)
    st.caption("Kırılım = önceki 20 günün zirvesi. Alış = kırılımın %0,2 üstü (teyit) ya da uzaklaşmışsa EMA9 geri çekilmesi. "
               "Stop = alış − 1,5×ATR(14). Bu bir yatırım tavsiyesi değildir; geçmiş performans geleceği garanti etmez.")

# ---------- 2) Strateji Lab ----------
with t2:
    c1, c2 = st.columns(2)
    sym = c1.selectbox("Hisse", list(A), key="lab_sym")
    sid = c2.selectbox("Strateji", list(NAMES), format_func=NAMES.get, key="lab_st")
    a = A[sym]; df = a["df"].tail(160)
    pos = FUNCS[sid](a["df"]).loc[df.index]
    ent, ext = df[(pos.diff() == 1)], df[(pos.diff() == -1)]
    fig = go.Figure(go.Candlestick(x=df.index, open=df.Open, high=df.High, low=df.Low, close=df.Close, name="Fiyat",
                                   increasing_line_color="#22c58b", decreasing_line_color="#ff5d73"))
    fig.add_scatter(x=df.index, y=df.e9, name="EMA9", line=dict(color="#f5b942", width=1.5))
    fig.add_scatter(x=df.index, y=df.vw, name="VWAP (20g)", line=dict(color="#4d8dff", width=1.5))
    fig.add_scatter(x=ent.index, y=ent.Low * .985, mode="markers", name="Alım", marker=dict(symbol="triangle-up", size=11, color="#22c58b"))
    fig.add_scatter(x=ext.index, y=ext.High * 1.015, mode="markers", name="Satım", marker=dict(symbol="triangle-down", size=11, color="#ff5d73"))
    st.plotly_chart(layout(fig, 430), use_container_width=True)
    if st.toggle("Gün içi (5 dk) seans VWAP’ını göster"):
        try:
            d5 = load_intra(sym); day = d5.index.date[-1]; d5 = d5[d5.index.date == day]
            fi = go.Figure(go.Scatter(x=d5.index, y=d5.Close, name="Fiyat", line=dict(color="#e8edff")))
            fi.add_scatter(x=d5.index, y=d5.svwap, name="Seans VWAP", line=dict(color="#4d8dff", width=2))
            st.plotly_chart(layout(fi, 300), use_container_width=True)
        except Exception:
            st.info("Gün içi veri alınamadı (piyasa kapalı olabilir).")
    rows = [{"Strateji": NAMES[k], "Getiri": r["tot"], "Al-tut": r["bhr"], "Kazanç %": r["win"], "İşlem": r["n"],
             "Profit factor": r["pf"], "Maks. düşüş": r["mdd"], "Sharpe": r["sh"]} for k, r in a["bt"].items()]
    T = pd.DataFrame(rows).sort_values("Sharpe", ascending=False)
    st.subheader("Strateji karşılaştırması")
    st.dataframe(T.style.format({"Getiri": "{:+.1%}", "Al-tut": "{:+.1%}", "Kazanç %": "{:.0%}", "Profit factor": "{:.2f}",
                                 "Maks. düşüş": "{:.1%}", "Sharpe": "{:.2f}"}), hide_index=True, use_container_width=True)
    st.caption("İlk satır en yüksek Sharpe’a sahip stratejidir.")

# ---------- 3) Stratejiler (tüm hisselerde) ----------
with t3:
    st.caption(f"{len(A)} hissede eşit ağırlıklı ortalama. Sıralama: Sharpe.")
    cards = []
    for k in NAMES:
        eq = pd.concat([x["bt"][k]["eq"] for x in A.values()], axis=1).ffill().mean(axis=1)
        rs = [x["bt"][k] for x in A.values()]
        nt = sum(r["n"] for r in rs)
        g = lambda d: eq.iloc[-1] / eq.iloc[-1 - d] - 1
        cards.append(dict(k=k, eq=eq, nt=nt, win=sum(r["win"] * r["n"] for r in rs) / nt if nt else 0,
                          pf=sum(r["pf"] for r in rs) / len(rs), mdd=sum(r["mdd"] for r in rs) / len(rs),
                          sh=sum(r["sh"] for r in rs) / len(rs), d7=g(7), d30=g(30), d90=g(90)))
    for i, c in enumerate(sorted(cards, key=lambda x: -x["sh"])):
        with st.container(border=True):
            st.subheader(f"{NAMES[c['k']]}  {'🏆' if i == 0 else ''}")
            fg = go.Figure(go.Scatter(x=c["eq"].index, y=c["eq"], fill="tozeroy", line=dict(color="#4d8dff"),
                                      fillcolor="rgba(77,141,255,.15)"))
            fg.update_yaxes(range=[c["eq"].min() * .98, c["eq"].max() * 1.02])
            st.plotly_chart(layout(fg, 160), use_container_width=True, key=f"m{c['k']}")
            m = st.columns(4)
            m[0].metric("Toplam işlem", c["nt"]); m[1].metric("Kazanç oranı", f"{c['win']:.0%}")
            m[2].metric("Profit factor", f"{c['pf']:.2f}"); m[3].metric("Ort. maks. düşüş", pc(c["mdd"]))
            m = st.columns(3)
            m[0].metric("7 gün", pc(c["d7"])); m[1].metric("30 gün", pc(c["d30"])); m[2].metric("90 gün", pc(c["d90"]))
    st.subheader("Son aktiviteler")
    act = [{"Sembol": s, "Karar": {"AL": "İşlem aç", "IZLE": "Beklet", "UZAK": "Reddedildi"}[a["plan"]["kind"]],
            "Skor": round(a["score"]), "Açıklama": ", ".join([t for t, p in a["why"] if p > 0][:2]) or a["why"][0][0]}
           for s, a in L[:40]]
    st.dataframe(pd.DataFrame(act), hide_index=True, use_container_width=True)

# ---------- 4) Backtest & Performans ----------
with t4:
    c1, c2 = st.columns(2)
    sym4 = c1.selectbox("Hisse", list(A), key="bt_sym")
    sid4 = c2.selectbox("Strateji", list(NAMES), format_func=NAMES.get, key="bt_st")
    r = A[sym4]["bt"][sid4]
    fe = go.Figure()
    fe.add_scatter(x=r["eq"].index, y=r["eq"], name="Strateji", line=dict(color="#e8edff", width=2))
    fe.add_scatter(x=r["bh"].index, y=r["bh"], name="Al-tut", line=dict(color="#4d8dff"))
    st.plotly_chart(layout(fe, 340), use_container_width=True)
    K = [("Toplam getiri", pc(r["tot"])), ("Al-tut", pc(r["bhr"])), ("Kazanma oranı", f"{r['win']:.0%}"), ("İşlem sayısı", r["n"]),
         ("Ort. kazanç", pc(r["aw"], 2)), ("Ort. kayıp", pc(r["al"], 2)), ("Profit factor", f"{r['pf']:.2f}"),
         ("Maks. düşüş", pc(r["mdd"])), ("Sharpe", f"{r['sh']:.2f}"), ("Risk/Getiri", f"{r['rr']:.2f}"),
         ("Piyasada kalma", f"{r['expo']:.0%}"), ("Beklenti/işlem", pc(r["win"] * r["aw"] + (1 - r["win"]) * r["al"], 2))]
    for i in range(0, 12, 4):
        cc = st.columns(4)
        for j, (k, v) in enumerate(K[i:i + 4]):
            cc[j].metric(k, v)
    fd = go.Figure(go.Scatter(x=r["dd"].index, y=r["dd"], fill="tozeroy", line=dict(color="#ff5d73"), fillcolor="rgba(255,93,115,.2)"))
    st.subheader("Düşüş (drawdown)")
    st.plotly_chart(layout(fd, 220), use_container_width=True)
    st.subheader("İşlemler")
    tr = r["trades"].iloc[::-1].copy()
    if len(tr):
        tr["Giriş"], tr["Çıkış"] = tr["Giriş"].dt.date, tr["Çıkış"].dt.date
    st.dataframe(tr.style.format({"Getiri": "{:+.2%}"}), hide_index=True, use_container_width=True)
