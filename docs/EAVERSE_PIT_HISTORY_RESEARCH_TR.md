# Eaverse yerel tarihsel kaynak entegrasyonu — PIT replay v1

**Kapsam:** Yeni kod strateji keşfinin ana deposu Eaverse içindedir; yalnız quant_superlab kataloğu değildir. Mevcut keşif motoru, PAPER durum kayıtları, risk limitleri, Telegram ve üretim stratejisi kuralları değiştirilmez.

## Çalışan eaverse-native araştırma yolu

1. Kaynak adaptörleri gerçek yayıncı/borsa kayıtlarını normalize eder:
   - SEC submissions: kalıcı CIK, accession, form ve acceptance zamanını işler. Acceptance zamanına 24 saat eklenen **muhafazakâr sinyal zamanı**, kesin kamuya duyuru anı olarak sunulmaz. Finansal rapor dönemi ya da filingDate, yayın zamanı değildir.
   - Binance Vision günlük spot kline: OHLCV ile gerçek kline taker-buy base volume değerini işler. %20 sabit net taker-buy/sell eşik ailesi bir **proxy**'dir; tick CVD veya L2 derinlik değildir. Binance BTCUSDT ile Coinbase BTC-USD verisi sessizce birleştirilmez.
   - SPK/KAP/CFTC olayları: her kayıt özgün **yayın zamanı** ve resmi belge URL'siyle gelmelidir. CFTC salı pozisyon tarihi cuma yayını yerine geçmez.
2. Onaylı araştırma veri paketi bars.csv, events.jsonl ve manifest.json dosyalarından oluşur. Manifest gerçek indirme zamanını ve her dosyanın SHA-256 değerini kaydeder. Yeni revizyon yeni manifest hash'i gerektirir.
3. Komut: python -m eaverse.pit_research --manifest DIZIN/manifest.json. Kod kaynak hak statüsünü, SHA değerlerini, takvim/sıra/boşluk kontrollerini, OHLC tutarlılığını ve olayın yayın anı sonrasında bilinmesini doğrular. Eksik veriyi doldurmaz.
4. Sabit keşif uzayı: 1 ve 3 bar olay penceresi × sadece olay / olay+SMA50 / olay+momentum30. Keşif, doğrulama ve dokunulmamış holdout kronolojik 50/25/25 ayrılır ve arada 5 bar embargo uygulanır. Emir varsayımı yalnız daha sonraki günlük açılıştır. Maliyetler en az iki kat streslidir.
5. Çıktılar deney kimliği, kaynak SHA, üç ayrı dönem, işlem masrafı, olay sayısı ve veri eksikliği nedenlerini saklar. paper_eligible=false, alpha_verified=false ve forward_evidence=false değerleri zorunludur. İyi görünen holdout sonucu bile prospective kabul değildir.

Kod Eaverse'ın mevcut research.features, research.target, research.simulation, research.stats fonksiyonlarını kullanır. Dolayısıyla tamamen bağımsız bir fikir listesi değil, aynı hesaplama çekirdeğiyle çalışan araştırma yoludur.

## Kısıtlar

- BIST: eski endeks üyeleri, ISIN geçmişi, split/delist fiyatları, KAP otomasyon hakkı ve gerçek tarihsel L2 hâlâ tamamlanmamış kaynak aileleridir.
- ABD: bugünkü ticker listesi geçmişteki tüm şirketler değildir. SEC CIK eşlemesi issuer düzeyindedir; Yahoo'nun revize fiyat serisi PIT evren kanıtı sayılmaz.
- Kripto: Binance kline net-taker hacmi gerçek raporlanmış veri olsa da tick-level CVD veya order book yerine konmaz. Bağımsız borsa OOS hâlâ gerekir.

quant_superlab kaynak manifestleri ve önerileri sağlayabilir; ham özel/lisanslı veri kendi yetkili ortamında kalır.

## GitHub Actions

Eaverse PIT historical research (NO ORDERS), PR ve zamanlanmış çalışmalarda adversarial offline testler çalıştırır. Main dalında yalnız doğrulanmış araştırma verisi staged ise dosyaları gerçek engine ile tekrar oynatır. Veri paketi yoksa NO_VERIFIED_HISTORICAL_INPUT yazar; geçmiş test yapılmış gibi davranmaz. Kanıt çıktılarını 90 günlük Actions artifact olarak verir. Emirlere, Telegram'a veya PAPER kabul mantığına dokunmaz.

## Zorunlu tamamlanmamış aşamalar

Kaynak lisans/otomasyon hakkı, tüm arşivlerin özgün checksum'ları, revizyonlar ve tam yayın saati, BIST/US delist ve corporate actions, fiyat yürütülebilirliği, spread/kayma/kapasite, multiple-testing düzeltmesi, untouched OOS ve dondurulmuş prospective gözlemler doğrulanmadan herhangi bir yeni strateji otomatik PAPER/LIVE'e geçemez.

Bu PR, **çalışan offline araştırma replay altyapısı ve kaynak adaptörleridir**; 36+ kaynağın tamamen indirilip stratejilere bağlandığı veya kârlı strateji bulunduğu anlamına gelmez.
