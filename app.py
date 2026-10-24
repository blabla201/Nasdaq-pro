import io, math, time
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title='Nasdaq Radar v5.4',page_icon='📈',layout='wide')
st.title('📈 Nasdaq Radar v5.4 — Momentum')
st.caption('5–30 dakikalık erken hareket tarayıcısı • Eğitim/araştırma amaçlı • Canlı emir veya kesin alım tavsiyesi değildir')

@st.cache_data(ttl=21600)
def symbols_list():
    sources=['https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt','https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_tickers.txt']
    errors=[]
    for url in sources:
        try:
            r=requests.get(url,timeout=15,headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status()
            if 'nasdaqlisted' in url:
                d=pd.read_csv(io.StringIO(r.text),sep='|');d=d[(d['ETF']=='N')&(d['Test Issue']=='N')];s=d['Symbol'].tolist()
            else:s=r.text.splitlines()
            out=sorted({x.strip().upper() for x in s if isinstance(x,str) and x.strip().isascii() and x.strip().isalpha() and 1<=len(x.strip())<=5})
            if len(out)>500:return out, url, errors
            errors.append('Liste eksik: '+str(len(out)))
        except Exception as e:errors.append(str(e)[:140])
    return [],'',errors

@st.cache_data(ttl=120,show_spinner=False)
def fetch_batch(tickers):
    try:
        data=yf.download(list(tickers),period='5d',interval='5m',group_by='ticker',auto_adjust=True,progress=False,threads=True,timeout=25)
    except Exception as e:return {},str(e)
    frames={}
    for t in tickers:
        try:
            f=(data[t] if isinstance(data.columns,pd.MultiIndex) else data).dropna(subset=['Open','High','Low','Close','Volume'])
            if len(f)>=40:frames[t]=f
        except (KeyError,TypeError,ValueError):pass
    return frames,''

def evaluate(t,f,price_low,price_high,min_turnover,max_age):
    try:
        c=f['Close'].astype(float);h=f['High'].astype(float);l=f['Low'].astype(float);v=f['Volume'].astype(float)
        p=float(c.iloc[-1]);ts=pd.Timestamp(f.index[-1]);ts=ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')
        age=(pd.Timestamp.now(tz='UTC')-ts).total_seconds()/60
        if not price_low<=p<=price_high:return None,'fiyat'
        if not -5<=age<=max_age:return None,'eski veri'
        daily=(c*v).groupby(pd.DatetimeIndex(f.index).date).sum()
        if len(daily)<2 or daily.iloc[:-1].mean()<min_turnover:return None,'likidite'
        if v.iloc[-1]<=0:return None,'hacim yok'
        idx=pd.DatetimeIndex(f.index);slot=(idx.hour==idx[-1].hour)&(idx.minute==idx[-1].minute)&(idx.date!=idx[-1].date)
        base=v.loc[slot]
        if len(base)<2 or base.mean()<=0:return None,'RVOL geçmişi yok'
        rvol=float(v.iloc[-1]/base.mean());today=idx.date==idx[-1].date
        if v.loc[today].sum()<=0:return None,'VWAP hesaplanamadı'
        vwap=float((((h+l+c)/3).loc[today]*v.loc[today]).sum()/v.loc[today].sum())
        ema9=float(c.ewm(span=9,adjust=False).mean().iloc[-1]);change5=(p/c.iloc[-2]-1)*100;change15=(p/c.iloc[-4]-1)*100
        resistance=float(h.iloc[-13:-1].max());atr=float(pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1).tail(14).mean())
        if not math.isfinite(atr) or atr<=0:return None,'ATR'
        if rvol<1.5 or change15<0.3:return None,'momentum/hacim'
        if p<vwap or p<ema9:return None,'VWAP/EMA9'
        if p<resistance*0.985:return None,'dirence uzak'
        entry=max(p,resistance+max(.01,atr*.1));risk=max(atr*1.5,entry*.0125,.03);stop=entry-risk
        if stop<=0 or risk/entry>.08:return None,'geniş stop'
        target=entry+2*risk;rr=(target-entry)/risk
        breakout=p>resistance;score=min(100,round((30 if breakout else 12)+min(25,rvol*5)+min(20,max(0,change15)*5)+15+(10 if change5>0 else 0)))
        state='KIRILIM TEYİDİ — İNCELE' if breakout else 'ALIM İÇİN BEKLE'
        if change15>12 or p>vwap*1.12:state='GEÇ KALINMIŞ / RİSKLİ'
        return dict(symbol=t,price=p,age=age,rvol=rvol,change5=change5,change15=change15,ema9=ema9,vwap=vwap,resistance=resistance,entry=entry,stop=stop,target=target,rr=rr,score=score,state=state,stamp=ts.isoformat()),'aday'
    except Exception:return None,'hesaplama hatası'

with st.sidebar:
    st.header('⚙️ Tarama')
    price_low,price_high=st.slider('Fiyat aralığı ($)',.5,50.,(.5,20.),.5)
    min_turnover=st.number_input('Önceki gün ortalama $ hacmi',100000,100000000,1000000,100000)
    batch_size=st.slider('Grup büyüklüğü',10,80,60,10)
    max_age=st.slider('En fazla veri yaşı (dk)',5,30,15,5)
    interval=st.select_slider('Tarama aralığı (sn)',options=[30,60,90,120],value=60)
    auto=st.toggle('Otomatik tara (uygulama açıkken)',value=True)
    st.caption('Ücretsiz Yahoo verisi gecikmeli olabilir. Bu sistem gerçek zamanlı işlem sinyali değildir.')

symbols,source,errors=symbols_list()
if not symbols:
    st.error('Nasdaq sembol listesi alınamadı');st.write(errors)
    if st.button('Listeyi yeniden dene'):symbols_list.clear();st.rerun()
    st.stop()
st.caption(f'{len(symbols)} sembol • Kaynak: {source}')
for key,default in [('cursor54',0),('rows54',{}),('scans54',0),('last54',0.),('report54',{}),('counts54',{})]:
    if key not in st.session_state:st.session_state[key]=default

@st.fragment(run_every='15s')
def radar():
    manual=st.button('🔎 Şimdi tara',use_container_width=True)
    due=auto and time.monotonic()-st.session_state.last54>=interval
    if manual or due:
        st.session_state.last54=time.monotonic();cur=st.session_state.cursor54
        batch=[symbols[(cur+i)%len(symbols)] for i in range(min(batch_size,len(symbols)))]
        st.session_state.cursor54=(cur+len(batch))%len(symbols)
        counts={'denenen':len(batch),'veri alınan':0,'aday':0};reasons={}
        with st.spinner('Hisseler analiz ediliyor...'):
            for j in range(0,len(batch),10):
                frames,err=fetch_batch(tuple(batch[j:j+10]));counts['veri alınan']+=len(frames)
                for t in batch[j:j+10]:
                    if t not in frames:reasons['veri alınamadı']=reasons.get('veri alınamadı',0)+1;continue
                    row,reason=evaluate(t,frames[t],price_low,price_high,min_turnover,max_age)
                    reasons[reason]=reasons.get(reason,0)+1
                    if row:st.session_state.rows54[t]=row;counts['aday']+=1
                    else:st.session_state.rows54.pop(t,None)
        st.session_state.scans54+=1;st.session_state.report54=counts;st.session_state.counts54=reasons
    if st.session_state.report54:
        a=st.session_state.report54;st.info(f"Son grup: {a['denenen']} denendi • {a['veri alınan']} veri alındı • {a['aday']} aday")
        with st.expander('🔬 Neden hisseler elendi?',expanded=False):st.json(st.session_state.counts54)
    now=datetime.now(timezone.utc)
    valid=[r for r in st.session_state.rows54.values() if -300<=(now-datetime.fromisoformat(r['stamp'])).total_seconds()<=max_age*60]
    valid.sort(key=lambda r:(r['state']!='GEÇ KALINMIŞ / RİSKLİ',r['score'],r['rvol']),reverse=True)
    st.caption(f"{st.session_state.scans54} grup tarandı • {len(valid)} güncel aday • Piyasa kapalıyken sıfır aday normaldir")
    if not valid:st.warning('Şu anda doğrulanmış güncel aday yok. Veri gecikmesi ve eleme nedenlerini kontrol et.')
    for r in valid[:6]:
        with st.container(border=True):
            st.subheader(f"{r['symbol']} — {r['state']}")
            st.write(f"Fiyat **${r['price']:.3f}** · Puan **{r['score']}/100** · 5 dk **{r['change5']:+.1f}%** · RVOL **{r['rvol']:.1f}x** · Veri yaşı **{r['age']:.0f} dk**")
            a,b,c,d=st.columns(4)
            a.metric('Kırılım',f"${r['resistance']:.3f}");b.metric('Olası giriş',f"${r['entry']:.3f}")
            c.metric('Teknik stop',f"${r['stop']:.3f}");d.metric('Varsayımsal hedef',f"${r['target']:.3f}")
            st.caption(f"EMA9 ${r['ema9']:.3f} · VWAP ${r['vwap']:.3f} · Risk/ödül {r['rr']:.1f}:1. Giriş, stop ve hedef otomatik senaryodur; garanti değildir.")
    if valid:st.download_button('📥 Adayları CSV indir',pd.DataFrame(valid).to_csv(index=False).encode('utf-8-sig'),'nasdaq_v5_4.csv','text/csv')
    st.warning('⚠️ Bu uygulama emir vermez. Ücretsiz 5 dakikalık veriler gecikebilir; 5–30 dk işlem için broker ekranında gerçek zamanlı fiyat, spread ve hacmi doğrula. Stop emri garanti değildir.')
radar()
