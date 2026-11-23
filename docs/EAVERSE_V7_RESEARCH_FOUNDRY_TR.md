# Eaverse V7: Çeşitlendirilmiş tarihsel araştırma

**Yalnız araştırma. PAPER, LIVE, Telegram, eski SQLite/Release ve strateji
kuralları değiştirilmez.** Bu sistem quant_superlab'ın genel envanterinden
bağımsız olarak Eaverse içinde çalışır.

## Gerçek geçmiş derinliği ve varlıklar

Binance Vision spot üzerinde BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT,
BNBUSDT, ADAUSDT, LINKUSDT, AVAXUSDT önceden belirlenmiştir.
2023–2025 döneminde her biri için **36 aylık 1h kaynak arşivi**
denenecektir. Tam kapsam varsa 26.304 saat/varlık, toplam 210.432
saat söz konusudur. Bunlar gerçekleşmiş indirme sayıları değil,
gerekli doğrulama koşullarıdır.

Aylık her orijinal ZIP için Binance'in aynı ada sahip SHA256
CHECKSUM dosyası doğrulanır. UTC saat sıra ve boşluk, 2025
mikrosaniye/önceki yıllar milisaniye birimleri, OHLC,
taker-buy base hacim, toplam base/quote hacim tutarlılığı
kontrol edilir. Eksik ay olduğunda dış kaynaktan uydurma
barla otomatik tamamlama yapılmaz.

Binance kullanım şartları:
https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md
Bu çalışma yalnız kişisel, ticari olmayan tarihsel araştırma
kapsamındadır. LIVE emir ve ticari veri yeniden dağıtımı yoktur.

## Stratejide farklı olay aileleri

Önceki dört aile: spot flow pressure, divergence,
acceleration ve geçmiş 168h göre flow tail.
Yeni aileler:
1. Absorption: taker akışı artıyor ama fiyat henüz aynı
   yönde kapanmıyor.
2. Regime flip: kendi önceki 24h taker ortalamasına
   ters yönde net akış değişimi.
3. Compressed flow: önceki 24 saat düşük volatilite
   rejimindeyken net işlem akışı ve hacim artışı.
4. Persistence: önceden tamamlanmış en az üç saatin
   net akış yönü devam ediyor.

Sekiz aile × iki yön × dört ufuk (1/4/12/24h)
= **varlık başına 64; 8 varlıkta en fazla 512
ön kayıtlı araştırma varyantı**. Bu sayı,
512 bağımsız alpha bulunduğu anlamına gelmez.
Birçok hipotez aynı veri ailesinden türediği için
çoklu test yanlılığı riski raporlanır.

Her sinyal, ilgili saat TAMAMLANDIKTAN sonra
bilinir. Test girişi sonraki saat açılışındadır.
Yalnız geçmiş hareketli referanslar kullanılır.
İlk iki kronolojik discovery/validation dönemi
örnek ve eşleşmiş saat kontrolü ile net maliyet
sonrası ölçümleri geçmeden son holdout okunmaz.
Spot aşağı yön sinyalleri otomatik short değildir.
Baz 24 bps / iki kat stres 48 bps gidiş dönüş
maliyetleri varsayımdır, gerçek fill garantisi değil.

## Kaynak ailesi konseyi ve yedek kaynak

Eaverse içine eklenen 30 kayıtlı kaynak/adayı BIST,
ABD ve kriptoya ayrılmıştır. Örnekler: Binance
Vision spot ve perp funding, Kraken trade/OHLCVT,
OKX tick/L2/funding, Coinbase, Deribit, DefiLlama,
Coin Metrics, SEC/XBRL/oldloads, FINRA short
volume/interest, Cboe, CFTC, ALFRED/FRED,
Treasury, New York Fed, Nasdaq, KAP/SPK, TCMB,
TEFAS ve BIST geçmiş üyelikleri.

Sağlayıcı sınıfları ayrılmıştır:
mevcut çalışan ve doğrulanmış arşiv,
koşullu adapötör, belgelenmiş fakat henüz
indirilmemiş arşiv, yalnız araştırma adayı.
Bir borsanın farklı saat hacmini diğer
borsanın asli verisiymiş gibi koymak,
COINBASE BTC-USD ile BINANCE BTCUSDT
fiyatlarını veya volume/CVD hesaplarını
karıştırmak **yasaktır**. Yedek kaynak
için yeni kimlik, hak, timestamp ve
ayrı ön kayıtlı hipotez gereklidir.

Kaynak konseyi, erişimi kanıtlanmamış
sağlayıcıları aktive etmez ve bunlara
dayalı PAPER açmaz. SEC için gerçek User-Agent,
BIST için KAP yayın saati ve delist fiyatları,
Kraken/OKX için ayrı kaynak doğrulaması
gerekir.

## Devam eden, kendi üreten araştırma

Hangi adayın discovery/validation aşamasında
olgunlaşmadığı ve hangi bilgi ailesinin
eksik olduğu kaydedilir. Hipotez kıtlığı
durumunda başka RSI eşiği çoğaltmak yerine
farklı bilgi kaynağı gereken yeni fikirler
SHA kimliğiyle önerilir: funding sürprizi,
farklı borsa trade tape'i, L2 emir yenilenmesi.

Bu fikirler otomatik gerçek emir veya
PAPER kararı değildir. Yeni fikrin gerçekten
denenebilmesi için o kaynağın tarihsel
yayın, veri lisansı ve maliyet sözleşmesi
ayrı sınanmalıdır.

## Çalışma ve kayıt

GitHub Actions iş adı: Eaverse V7
three-year multi-asset event-foundry (RESEARCH ONLY).
PR'da offline regresyon, main push/manual/
haftalık en fazla iki paralel varlıkta gerçek
SHA'lı arşiv denemesi çalışır.
Her varlığın tam hipotez sonuçları
research/eaverse-v7-<sembol>-evidence
dalında SHA bağlantılı kalıcı metaveri
olarak saklanır; ham borsa ZIP/CSV
dosyaları git içine konmaz. 90 günlük
artifakt raporları ayrı tutulur.

Aynı kaynak/politika tekrar çalıştırılırsa
yeni forward gözlem uydurulmaz.
Strateji araştırma başarısı, kârlılık
kanıtı veya live işlem izni değildir.
