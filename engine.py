"""Nasdaq Strateji Laboratuvarı - hesaplama motoru (yalnızca pandas/numpy)."""
import numpy as np
import pandas as pd

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
    if R / entry > .06: warn.append(f"Stop mesafesi geniş (%{R/entry*100:.1f})")
    kind = "AL" if sc >= 70 and c > last.e21 else "IZLE" if sc >= 50 else "UZAK"
    if kind == "AL":
        label = ("GERİ ÇEKİLME BEKLE" if far else "ALIM BÖLGESİ") if done else f"KIRILIMI BEKLE (%{(hh/c-1)*100:.1f} uzakta)"
    else:
        label = "İZLE" if kind == "IZLE" else "UZAK DUR"
    return dict(price=c, hh=hh, entry=entry, stop=stop, R=R, t1=entry + 2 * R, t2=entry + 3 * R,
                kind=kind, label=label, warn=warn, done=done)

def lots(capital, risk_pct, p):
    q = int(capital * risk_pct / 100 // p["R"]) if p["R"] > 0 else 0
    return min(q, int(capital // p["entry"]))
