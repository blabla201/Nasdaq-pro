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
