"""Nasdaq Radar: kapanmış 1/5/10 dk mumlarıyla araştırma amaçlı momentum taraması."""
import math
import pandas as pd


def evaluate_timeframes(symbol, minute_bars, price_low=1.5, price_high=50.0,
                        min_dollar_volume=1_000_000, min_rvol=2.5, max_age=15):
    """Girdi: OHLCV içeren 1 dakikalık zaman damgalı DataFrame; çıktı: aday listesi.

    Not: son 1m mumunun kapanmış olduğu veri sağlayıcısı tarafından doğrulanmalıdır.
    Bu fonksiyon fiyat/spread verisi olmadan alım emri üretmez.
    """
    if minute_bars is None or len(minute_bars) < 60:
        return [], 'yetersiz 1m geçmiş'
    f = minute_bars.copy().sort_index()
    if not isinstance(f.index, pd.DatetimeIndex):
        return [], 'zaman indeksi yok'
    if f.index.tz is None:
        return [], 'zaman dilimi belirsiz'
    f = f[~f.index.duplicated(keep='last')]
    f = f[['Open', 'High', 'Low', 'Close', 'Volume']].apply(pd.to_numeric, errors='coerce').dropna()
    if f.empty or (f['Volume'] < 0).any():
        return [], 'geçersiz veri'
    # Seans ve gün sınırını New York saatine göre belirle.
    ny = f.tz_convert('America/New_York')
    last_day = ny.index[-1].date()
    today = ny[ny.index.date == last_day]
    if today.empty or (today['Close'] * today['Volume']).sum() < min_dollar_volume:
        return [], 'günlük dolar hacmi'
    candidates = []
    for mins in (1, 5, 10):
        # Günler arası mum birleştirmemek için yalnızca son seans gününü grupla.
        bars = today if mins == 1 else today.resample(f'{mins}min', origin='start_day', label='left', closed='left').agg(
            {'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        # Eksik veya açık mumları kullanma; yalnızca tam sayıda 1m bar içeren bloklar.
        if mins > 1:
            counts = today['Close'].resample(f'{mins}min', origin='start_day', label='left', closed='left').count()
            bars = bars.loc[counts.reindex(bars.index, fill_value=0) == mins]
        if len(bars) < 35:
            continue
        c,h,l,v = (bars[k].astype(float) for k in ('Close','High','Low','Volume'))
        price = float(c.iloc[-1])
        if not price_low <= price <= price_high: continue
        # Son mum ile önceki 20 mum karşılaştırılır; look-ahead yok.
        avg = float(v.iloc[-21:-1].mean())
        if avg <= 0: continue
        rvol = float(v.iloc[-1] / avg)
        resistance = float(h.iloc[-21:-1].max())
        ema9 = float(c.ewm(span=9, adjust=False).mean().iloc[-1])
        typical = (h+l+c)/3
        vwap = float((typical*v).sum()/v.sum()) if v.sum()>0 else math.nan
        tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr = float(tr.iloc[-14:].mean())
        if not (math.isfinite(atr) and atr > 0 and math.isfinite(vwap)): continue
        # Kırılım: son kapanış önceki 20 mum tepesinin üzerinde.
        breakout = price > resistance
        early = price >= resistance*0.992 and price < resistance
        if rvol < min_rvol or not (breakout or early) or price < ema9 or price < vwap: continue
        entry = max(price, resistance + max(.01, .1*atr)) if breakout else resistance + max(.01,.1*atr)
        stop = max(.01, min(float(l.iloc[-1]), entry-1.5*atr))
        risk = entry-stop
        if risk <= 0 or risk/entry > .08: continue
        target = entry+2*risk
        score = min(100, round(20+min(35, rvol*7)+(25 if breakout else 10)+min(20, max(0,(price/c.iloc[-2]-1)*100)*6)))
        candidates.append(dict(symbol=symbol,timeframe=f'{mins}m',price=price,rvol=rvol,
                               resistance=resistance,entry=entry,stop=stop,target=target,
                               rr=2.0,score=score,ema9=ema9,vwap=vwap,
                               state='KIRILIM TEYİDİ — İNCELE' if breakout else 'ALIM İÇİN BEKLE',
                               estimated_minutes='5–30 (istatistiksel tahmin değil)',
                               stamp=bars.index[-1].isoformat()))
    return candidates, 'aday' if candidates else 'momentum/filtre'
