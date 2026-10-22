import json
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

import engine
import scanner
from engine import NAMES, FUNCS, lots, session_vwap

SURUM = "v6 (3 dosya)"



st.set_page_config("Nasdaq Strateji Laboratuvarı", page_icon="📈", layout="wide")
st.markdown("<style>.block-container{padding-top:1.2rem}</style>", unsafe_allow_html=True)
_ph = st.empty()
_ph.caption("⏳ Program yükleniyor…")

if getattr(scanner, "VERSION", "") != "v6" or getattr(engine, "VERSION", "") != "v6":
    st.error("GitHub'daki engine.py veya scanner.py eski sürümde. app.py, engine.py ve scanner.py dosyalarının üçünü de yeniden yükleyin.")
    st.stop()

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
def scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat, pmin, pmax, frac, maxatr, use_mkt, use_earn):
    return scanner.run_scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat, pmin, pmax, frac, maxatr, use_mkt, use_earn)

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
    maxatr = st.slider("Maks. günlük oynaklık (ATR %)", 2, 20, 8) / 100
    use_mkt = st.toggle("Piyasa filtresi (QQQ düşüşteyse alım önerme)", True)
    use_earn = st.toggle("Bilanço yakınsa uyar", True)
    with st.expander("🔔 Telegram uyarısı"):
        tok = st.text_input("Bot token", os.getenv("TELEGRAM_TOKEN", ""), type="password")
        chat = st.text_input("Chat ID", os.getenv("TELEGRAM_CHAT_ID", ""))
    auto = st.toggle("Otomatik tara (5 dk)", True)
    if st.button("🔄 Şimdi tara", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Veri: Yahoo Finance (yfinance), genelde ~15 dk gecikmeli. Telegram için @BotFather ile bot açıp token ve chat ID girin.")

st.title("📈 Nasdaq Strateji Laboratuvarı")
st.caption("Sürüm: " + SURUM)
_ph.empty()
if not st.session_state.get("go"):
    st.info("Ayarlar sol üstteki **>>** menüsündedir (fiyat aralığı, sermaye, tarama türü). Hazırsanız taramayı başlatın.")
    if st.button("▶ Taramayı başlat", type="primary", use_container_width=True):
        st.session_state["go"] = True
        st.rerun()
    st.stop()
mode_eff, manual_eff, info_pre = mode, manual, ""
if mode == "Geniş Nasdaq taraması":
    bar = st.progress(0.0, text="Nasdaq hisseleri taranıyor… (ilk seferde birkaç dakika sürebilir)")
    try:
        allsyms = scanner.universe_all()
        syms = scanner.liquid(allsyms, topn, minvol, pmin, pmax,
                      progress=lambda x: bar.progress(min(x, 1.0), text=f"Nasdaq hisseleri taranıyor… %{int(x * 100)}"))
    except Exception as e:
        bar.empty()
        st.error("Nasdaq hisse listesi alınamadı: " + str(e)[:150] + ". Birkaç dakika sonra yeniden deneyin ya da sol menüden Nasdaq-100’ü seçin.")
        st.stop()
    bar.empty()
    if not syms:
        st.error("Bu fiyat ve hacim şartlarına uyan hisse bulunamadı. Fiyat aralığını genişletin ya da hacim şartını düşürün.")
        st.stop()
    mode_eff, manual_eff = "Elle liste", " ".join(syms)
    info_pre = f"Nasdaq’ta {len(allsyms)} hisse bulundu, en likit {len(syms)} tanesi analiz edildi"
A, NEW, INFO, MKT = scan(mode_eff, manual_eff, topn, minvol, cost, capital, risk, tok, chat, pmin, pmax, frac, maxatr, use_mkt, use_earn)
if info_pre:
    INFO = info_pre
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

t1, t2, t3, t4, t5 = st.tabs(["Sinyal", "Strateji Lab", "Stratejiler", "Backtest & Performans", "Günlük"])

# ---------- 1) Sinyal ----------
with t1:
    L = sorted(A.items(), key=lambda x: -x[1]["score"])
    (st.success if MKT[0] else st.warning)("📊 Piyasa: " + MKT[1])
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
            st.caption(f"⏱ Zaman stopu: {p['max_days']} işlem günü içinde hedefe gitmezse çıkın. Stop’u aşağı çekmeyin.")
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
    o = A[sym4]["oos"]
    st.info(f"**Veri dışı doğrulama:** ilk %60’lık dönemde en iyi çıkan strateji **{NAMES[o['k']]}** (Sharpe {o['train_sh']:.2f}). "
            f"Hiç görmediği son %40’lık dönemde: getiri {pc(o['test_tot'])}, Sharpe {o['test_sh']:.2f}, al-tut {pc(o['test_bh'])}. "
            "İki sonuç arasındaki fark, backtestin ne kadar iyimser olduğunu gösterir.")
    fd = go.Figure(go.Scatter(x=r["dd"].index, y=r["dd"], fill="tozeroy", line=dict(color="#ff5d73"), fillcolor="rgba(255,93,115,.2)"))
    st.subheader("Düşüş (drawdown)")
    st.plotly_chart(layout(fd, 220), use_container_width=True)
    st.subheader("İşlemler")
    tr = r["trades"].iloc[::-1].copy()
    if len(tr):
        tr["Giriş"], tr["Çıkış"] = tr["Giriş"].dt.date, tr["Çıkış"].dt.date
    st.dataframe(tr.style.format({"Getiri": "{:+.2%}"}), hide_index=True, use_container_width=True)

# ---------- 5) Günlük (sinyal sonuçları) ----------
with t5:
    LG = scanner.update_log(A)
    done = [r for r in LG if r["durum"] in ("hedefe ulaştı", "stopa değdi", "süre doldu")]
    wins = [r for r in done if r["sonuc"] is not None and r["sonuc"] > 0]
    m = st.columns(4)
    m[0].metric("Toplam kayıt", len(LG)); m[1].metric("Sonuçlanan", len(done))
    m[2].metric("Kazanç oranı", f"{len(wins) / len(done):.0%}" if done else "—")
    m[3].metric("Ort. sonuç", pc(sum(r["sonuc"] for r in done) / len(done), 2) if done else "—")
    if len(done) < 30:
        st.caption("Güvenilir bir başarı oranı için en az 30-50 sonuçlanmış sinyal gerekir. O zamana kadar bu rakamlara tek başına güvenmeyin.")
    if LG:
        T = pd.DataFrame(LG[::-1])
        T["sonuc"] = T["sonuc"].map(lambda x: "" if x is None or x != x else f"{x:+.1%}")
        cols = ["sym", "tarih", "kaynak", "durum", "entry", "stop", "t1", "giris_tarihi", "cikis_tarihi", "sonuc"]
        T = T[cols].rename(columns={"sym": "Sembol", "tarih": "Sinyal tarihi", "kaynak": "Kaynak", "durum": "Durum", "entry": "Alış",
                                    "stop": "Stop", "t1": "Hedef", "giris_tarihi": "Giriş", "cikis_tarihi": "Çıkış", "sonuc": "Sonuç"})
        st.dataframe(T, hide_index=True, use_container_width=True)
    else:
        st.info("Henüz kayıt yok. Program yeni bir alım sinyali ürettiğinde burada otomatik görünür. Kendi aldığınız hisseyi aşağıdan elle ekleyebilirsiniz.")
    st.caption("Durumlar: bekliyor = alış seviyesine henüz değmedi, açık = girildi, girilmedi = 5 günde alış seviyesine değmedi. "
               "Aynı gün hem stop hem hedef görülürse stop sayılır (temkinli). Sonuçlar işlem maliyetini içerir.")
    with st.expander("➕ Aldığım hisseyi elle kaydet"):
        with st.form("manuel"):
            ms = st.text_input("Sembol (örn. MAT)").upper().strip()
            k1, k2, k3 = st.columns(3)
            me = k1.number_input("Alış fiyatı", 0.0, 100000.0, 0.0, 0.01)
            mst = k2.number_input("Stop", 0.0, 100000.0, 0.0, 0.01)
            mt = k3.number_input("Hedef", 0.0, 100000.0, 0.0, 0.01)
            md = st.date_input("Alış tarihi")
            if st.form_submit_button("Kaydet"):
                if ms and 0 < mst < me < mt:
                    scanner.log_add(dict(sym=ms, tarih=str(md), kaynak="elle", etiket="elle", skor=None, entry=me, stop=mst, t1=mt,
                                         max_days=10, durum="açık", giris_tarihi=str(md), cikis_tarihi=None, sonuc=None))
                    st.success(ms + " kaydedildi.")
                else:
                    st.error("Sembol girin ve stop < alış < hedef olacak şekilde doldurun.")
    st.download_button("⬇ Günlüğü indir (JSON)", json.dumps(LG, ensure_ascii=False, indent=1), "sinyal_gunlugu.json", "application/json")
    up = st.file_uploader("Günlüğü geri yükle (JSON)", type="json")
    if up is not None and st.button("Yükle ve birleştir"):
        try:
            scanner.merge_log(json.load(up))
            st.rerun()
        except Exception:
            st.error("Dosya okunamadı.")
    st.caption("⚠ Streamlit Cloud uygulama yeniden başladığında günlük silinebilir. Düzenli olarak indirin; Telegram mesajları da ayrı bir kayıt tutar.")

if auto and st_autorefresh:
    st_autorefresh(interval=300_000, key="auto")
