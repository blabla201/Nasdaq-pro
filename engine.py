"""Nasdaq Strateji Laboratuvarı - hesaplama motoru (yalnızca pandas/numpy)."""
import numpy as np
import pandas as pd

VERSION = "v10"


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

def _retest(d):
    """Kırılım sonrası seviyeye geri dönüp tutunursa gir; seviye kaybedilirse ya da EMA21 altına inerse çık."""
    c, l, lv, at, e21 = d.Close.values, d.Low.values, d.hh20.values, d.atr.values, d.e21.values
    out, st, lvl, bo = [], 0, 0.0, 0
    for i in range(len(d)):
        if st == 0 and c[i] > lv[i]:
            st, lvl, bo = 1, lv[i], i
        elif st == 1:
            if c[i] < lvl * 0.99 or i - bo > 10: st = 0
            elif i > bo and l[i] <= lvl * 1.015 and c[i] > lvl: st = 2
        elif st == 2 and (c[i] < lvl - at[i] or c[i] < e21[i]):
            st = 0
        out.append(1.0 if st == 2 else 0.0)
    return pd.Series(out, index=d.index)

NAMES = {
    "ema9": "EMA9 (fiyat > EMA9)",
    "x921": "EMA9 / EMA21 kesişimi",
    "vwap": "VWAP (20 gün) üstü",
    "combo": "Birleşik: EMA9+VWAP+RSI",
    "rsi": "RSI dönüş (35/55)",
    "brk": "20 gün kırılım",
    "retest": "Kırılım + geri test",
}
FUNCS = {
    "ema9": lambda d: (d.Close > d.e9).astype(float),
    "x921": lambda d: (d.e9 > d.e21).astype(float),
    "vwap": lambda d: (d.Close > d.vw).astype(float),
    "combo": lambda d: ((d.Close > d.e9) & (d.Close > d.vw) & (d.rsi > 50) & (d.rsi < 75)).astype(float),
    "rsi": lambda d: _latch(d.rsi < 35, d.rsi > 55),
    "brk": lambda d: _latch(d.Close > d.hh20, d.Close < d.ll10),
    "retest": _retest,
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
        rr=tot / abs(dd.min()) if dd.min() else 0.0, expo=p.mean(), sret=sret,
    )

def run_all(df, cost=0.0005):
    return {k: backtest(df, f(df), cost) for k, f in FUNCS.items()}

# ---------- Skor ve işlem planı ----------
def trendline(df, look=80, w=4):
    """Son iki alçalan tepeden geçen düşen trend çizgisi. Kırıldıysa (son 5 gün içinde yukarı) broken=True."""
    h, c, n = df.High.values, df.Close.values, len(df)
    piv = [i for i in range(max(w, n - look), n - w) if h[i] == h[i - w:i + w + 1].max()]
    for a in range(len(piv) - 1, 0, -1):
        i2, i1 = piv[a], piv[a - 1]
        if h[i2] < h[i1] and i2 - i1 >= 5:
            m = (h[i2] - h[i1]) / (i2 - i1)
            ln = lambda k: h[i1] + m * (k - i1)
            now = bool(c[n - 1] > ln(n - 1))
            was_below = any(c[k] <= ln(k) for k in range(n - 6, n - 1))
            return dict(i1=int(i1), y1=float(h[i1]), m=float(m), above=now, broken=now and was_below)
    return None

def oos(res, frac=0.6):
    """Veri dışı doğrulama: ilk %60'ta en iyi stratejiyi seç, hiç görmediği son %40'ta sına."""
    def sh(x):
        sd = x.std()
        return float(x.mean() / sd * np.sqrt(252)) if sd else 0.0
    n = len(next(iter(res.values()))["sret"]); cut = int(n * frac)
    tr = {k: sh(r["sret"].iloc[:cut]) for k, r in res.items()}
    k = max(tr, key=tr.get); r = res[k]; te = r["sret"].iloc[cut:]
    return dict(k=k, train_sh=tr[k], test_sh=sh(te), test_tot=float((1 + te).prod() - 1),
                test_bh=float(r["bh"].iloc[-1] / r["bh"].iloc[cut - 1] - 1))

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
    tl = trendline(df)
    if tl and tl["broken"]: add("Düşen trend çizgisi son 5 günde yukarı kırıldı", 10)
    add(f"Veri dışı testte strateji Sharpe {best_sharpe:.1f}", 10) if best_sharpe > .8 else add("Veri dışı testte strateji zayıf", -5)
    return float(np.clip(50 + sum(p for _, p in r) / 1.5, 0, 100)), r

def _breakout(df, look=10):
    """Son `look` gün içindeki ilk kırılım: (konum, seviye) ya da None."""
    c, h, n = df.Close.values, df.hh20.values, len(df)
    for i in range(n - look, n):
        if c[i] > h[i]:
            return i, float(h[i])
    return None

def plan(df, sc, mkt_ok=True, maxatr=1.0):
    """Kırılımı kovalamaz: önce kırılımı bekler, sonra seviyeye geri dönüşü (geri test) bekler."""
    last = df.iloc[-1]
    c, a = float(last.Close), float(last.atr)
    atrp = a / c
    bo = _breakout(df)
    warn = []
    if bo is None:
        stage, lvl = "pre", float(last.hh20)
        entry = lvl * 1.002
        stop = entry - 1.5 * a
    else:
        i, lvl = bo
        if c < lvl * 0.99:
            stage = "fail"; entry = float(last.hh20) * 1.002; stop = entry - 1.5 * a
            warn.append("Sahte kırılım: fiyat kırılım seviyesinin altına döndü")
        else:
            stage = "retest" if c <= lvl * 1.02 else "extended"
            entry = c if stage == "retest" else lvl * 1.005
            stop = min(lvl - 1.0 * a, entry - 0.5 * a)
            if stage == "extended":
                warn.append("Kırılım gerçekleşti ama fiyat uzaklaştı: kovalamayın, seviyeye geri dönüşü (geri test) bekleyin")
            vol = float(df.Volume.iloc[i]); avg = float(df.Volume.iloc[max(0, i - 20):i].mean()) or 1.0
            if vol < avg:
                warn.append("Kırılım ortalamanın altında hacimle geldi: zayıf kırılım")
    R = entry - stop
    if last.rsi > 75: warn.append("RSI aşırı alım bölgesinde")
    if c < last.vw: warn.append("Fiyat VWAP altında")
    if c < 5: warn.append("Düşük fiyatlı hisse: spread ve oynaklık riski yüksek, küçük pozisyon kullanın")
    if R / entry > .06: warn.append(f"Stop mesafesi geniş (%{R/entry*100:.1f})")
    if atrp > maxatr: warn.append(f"Aşırı oynak: günlük ortalama hareket %{atrp*100:.1f}")
    if not mkt_ok: warn.append("Piyasa filtresi: QQQ düşüş trendinde, yeni alım önerilmez")
    blocked = atrp > maxatr or not mkt_ok
    strong = sc >= 70 and c > last.e21 and stage != "fail"
    kind = "AL" if strong and not blocked else "IZLE" if sc >= 50 else "UZAK"
    if stage == "fail":
        label = "SAHTE KIRILIM"
    elif kind == "AL":
        label = {"pre": f"KIRILIMI BEKLE (%{(lvl/c-1)*100:.1f} uzakta)",
                 "extended": f"GERİ TEST BEKLE (seviye ${lvl:.2f})",
                 "retest": "GERİ TEST BÖLGESİ: ALIM"}[stage]
    else:
        label = ("İZLE (filtre)" if strong and blocked else "İZLE") if kind == "IZLE" else "UZAK DUR"
    return dict(price=c, hh=lvl, entry=entry, stop=stop, R=R, t1=entry + 2 * R, t2=entry + 3 * R,
                kind=kind, label=label, warn=warn, done=stage in ("extended", "retest"), stage=stage, atrp=atrp, max_days=10)

def lots(capital, risk_pct, p, fractional=False):
    """Risk yüzdesine göre adet. fractional=True: parçalı (kesirli) hisse alınabilir."""
    if p["R"] <= 0:
        return 0
    q = min(capital * risk_pct / 100 / p["R"], capital / p["entry"])
    return round(q, 2) if fractional else int(q)

