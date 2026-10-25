# Nasdaq Radar v5.4 — erken momentum deneme sürümü

Bu sürüm v5.3'ten esinlenerek yeniden düzenlenmiş bağımsız Streamlit uygulamasıdır.

## Kurulum
1. ZIP'i açın.
2. `app.py`, `requirements.txt`, `README.md` dosyalarını GitHub deponuzun ana dizinine yükleyin (önce mevcut sürümü yedekleyin).
3. Streamlit Community Cloud uygulamanızda `app.py` dosyasını çalıştırın.

Yerelde: `pip install -r requirements.txt` ve `streamlit run app.py`.

## Neler değişti?
- EMA 9 ve VWAP kontrolü, erken direnç yaklaşımı, RVOL 1.5x.
- Kırılım, olası giriş, ATR temelli stop, 2R varsayımsal hedef.
- Grup bazında veri alınan/elenen hisse ve nedenleri.
- Verisi eski adaylar ekranda tutulmaz; geçmiş sonuçlar canlı sinyal değildir.

## Sınırlamalar
- Yahoo Finance 5 dakikalık verileri gecikebilir, kesilebilir veya kota hatası verebilir.
- Piyasa kapalıyken ve düşük hacimde hiç aday çıkmayabilir.
- 1 ve 3 dakikalık gerçek zamanlı veri bu sürümde yoktur. Hızlı işlem için güvenilir canlı veri kaynağı gerekir.
- Sinyaller geçmiş testten geçirilmedi; kâr garantisi veya kişisel alım tavsiyesi değildir.

## Güncelleme: deneysel çoklu periyot motoru
- `momentum_engine.py` eklendi; Streamlit yan menüsünde **Deneysel 1m / 5m / 10m** seçeneği var.
- 1 dakikalık Yahoo verisinden 5 ve 10 dakikalık mumlar oluşturulur. Eksik 5/10 dakika blokları dışlanır.
- Önceki 20 kapanmış mum hacim ortalaması, 20 mum direnç, EMA9, VWAP, ATR, 2R varsayımsal hedef kullanılır.
- Veri yetersizse aday oluşturulmaz; 10m için aynı gün en az 35 kapanmış mum gereklidir.
- Yahoo veri kaynağı gerçek zamanlı garanti sağlamaz. 1m veri Yahoo sınırları/kotaları nedeniyle alınamayabilir.
- Bu güncelleme **push bildirim, bulut sunucu, SIP canlı veri veya iOS native uygulama içermez**. Bunlar API erişimi, APNs anahtarı ve sunucu kurulumuyla sonraki aşamada yapılmalıdır.
- Adaylar yatırım önerisi değildir. Alış/satış spread kontrolü için gerçek zamanlı bid/ask gereklidir.
- Streamlit Community Cloud'da dosyaları birlikte yükleyin; `app.py` ve `momentum_engine.py` aynı klasörde olmalı.
