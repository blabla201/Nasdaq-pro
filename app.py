import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from engine import NAMES, FUNCS, lots, session_vwap
import scanner
from streamlit_autorefresh import st_autorefresh

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
def scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat):
    return scanner.run_scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat)

# ---------- Kenar çubuğu ----------
with st.sidebar:
    st.header("Ayarlar")
    mode = st.radio("Hisseleri nasıl bulsun?", ["Nasdaq-100 (hızlı)", "Geniş Nasdaq taraması", "Elle liste"])
    topn, minvol, manual = 0, 0, ""
    if mode == "Geniş Nasdaq taraması":
        topn = st.slider("Analiz edilecek en likit hisse sayısı", 30, 300, 120, 10)
        minvol = st.number_input("Min. günlük işlem hacmi (milyon $)", 5, 1000, 50, 5) * 1e6
    elif mode == "Elle liste":
        manual = st.text_area("Hisseler (boşlukla ayır)", DEFAULT, height=100)
    capital = st.number_input("Sermaye ($)", 100, 10_000_000, 10_000, 500)
    risk = st.number_input("İşlem başı risk (%)", 0.1, 10.0, 1.0, 0.1)
    cost = st.number_input("İşlem maliyeti (her yön, %)", 0.0, 1.0, 0.05, 0.01) / 100
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

A, NEW, INFO = scan(mode, manual, topn, minvol, cost, capital, risk, tok, chat)
if not A:
    st.error("Veri alınamadı. İnternet bağlantısını kontrol edin.")
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
            q = lots(capital, risk, p)
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
