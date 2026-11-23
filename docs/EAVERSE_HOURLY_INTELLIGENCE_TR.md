# Eaverse V5 — Saatlik erken akış araştırması ve bağımsız kanıt

Bu tasarımda Eaverse, kendi araştırma/karar motorudur. quant_superlab,
yalnızca karşılaştırma ve gerektiğinde kaynak keşif desteği sağlar.
Eaverse'ın mevcut Autonomous research, SQLite Release checkpoint,
34.636 teknik aday evreni, PANEL, Telegram, PAPER/90 bar/8 işlem ve
önceki PAPER işlemleri **değiştirilmez**.

## 1. Eski pilotun neden yeterli olmadığı

Eaverse V4, 2024–2025 BTCUSDT günlük 731 gerçek Binance spot barından,
sabit %20 kline net taker BUY/Sell eşiğiyle yalnız **3** sinyal olayı
çıkarmıştı. Bu, tüm ileri ufuklarda strateji seçmek için yetersizdir.

Günlük en yüksek/son fiyat hareketine bakarak çoklu teknik gösterge
denemek olayın öncül nedenini ölçmek değildir. Bu nedenle V5, yalnız
**tamamlanmış saatlik Binance spot mumlarının taker-buy base volume**
içeriklerini kullanıp, henüz fiyat kırılımı gerekmeyen öncü hipotezleri
ayrı bir sabit deney alanında ölçer.

## 2. Gerçek veriler ve doğrulama

İlk kapsam bilinçli şekilde sabitlenmiştir: BTCUSDT ve ETHUSDT,
2024-01-01 ile 2025-12-31 arasındaki 24'er Binance spot 1h aylık ZIP'i.
Her ZIP'in aynı isimli resmi SHA256 CHECKSUM'u indirilip doğrulanır,
içeride tek doğru CSV zorunludur; 2024 milisaniye / 2025
mikrosaniye UTC açılış-kapanış zamanları, 12 alan, OHLC,
base hacim, quote hacim, taker-buy base hacim ve **her saatlik
kesintisiz sıra** kontrol edilir. Uydurma bar, 404 için sessiz
yedek sağlayıcı, başka enstrümandan ekleme veya eski datayı
prospective first_seen gibi gösterme yoktur.

Veriler Eaverse'ın çalıştığı Actions runner'da değerlendirilir.
Vendor CSV/ZIP dosyaları GitHub'a yayımlanmaz. Yalnız SHA değerleri,
veri kapsamı, deney sayıları ve araştırma kararları görünür olur.

## 3. Sabit araştırma hipotezleri

Her kaynak için dört ekonomik bilgi ailesi denenir:

- flow_pressure: gerçek taker net alım/satım yönü ile
  *önceki* 168 saat hacim medyanına kıyasla likidite aktivitesi;
- flow_divergence: alıcı/satıcı agresifliği belirgin,
  fakat fiyat henüz aynı yönü izlememiş;
- flow_acceleration: taker agresifliği **önceki** 24 saat
  ortalamasından belirgin sapmış ve aktivite artmış;
- flow_tail: taker yönünün, yalnız **önceki** 168 saatlik
  %15/%85 dağılımına kıyasla uçta olması.

Ek filtre, işlemin gerçekleştiği saatin açılış-kapanış
değişiminin mutlak %0,4'ü aşmamasıdır; yani **kırılım veya
fiyat yön teyidi aranmaz**. Bununla birlikte, kapanan saatlik mum
henüz saat içinde görülemeyen bir hareketin öncesi demek
değildir. Gerçek t-anı emir defteri ve likidasyon akışı
ayrıca gereklidir.

4 aile × 2 yön × 4 ufuk (1/4/12/24 saat) = **32 ön kayıtlı
hipotez**; sembol bazında toplam 64 desen. Eşikler geçmiş
sonuçlara bakılarak otomatik optimize edilmez. Kontroller
**aynı UTC saatinden, daha önce oluşmuş, olgunlaşmış ve
yakın aktiviteye sahip** sakin piyasa örneklerinden seçilir.

## 4. Doğrulama ve karar mantığı

Kronolojik keşif/doğrulama/dokunulmamış test: 50/25/25.
En uzun 24h ufuk için 25 saat embargo. Bir sinyal,
kapanmış saatlik mum sonunda üretilebilir; yalnızca sonraki
saat açılışından sonraki 1/4/12/24 saat getirisi ölçülür.
Sinyal pencerelerinin birbiriyle iç içe geçtiği araştırma
örnekleri elenir.

Kıyas: aynı saatte sakin piyasa, gözlem sayısı, yönlü isabet,
olay başına bps ve uygun kontrol sayısı. Binance spot long
için maliyet varsayımı tek yönde 12 bps (ücret+kayma),
iki kat stresle gidiş dönüş toplam **48 bps**; ücret gerçek
müşteri hesabı/fill değil deney varsayımıdır. Aşağı yönlü
bulgu, Binance spot üzerinde açılmış vadeli short diye
etiketlenmez: yalnız **riskten kaçınma yönlü tanılama**dır.

Yeterli aday sayısı yoksa DEFER_MORE_INDEPENDENT_CASES,
sonuç maliyet/ayrı dönem açısından olumsuzsa
NO_REPLICATED_AFTER_COST_EDGE_OR_DOWNSIDE_DIAGNOSTIC,
yeterli pozitif tarihsel adayda yalnız
EXPLORATORY_FORWARD_RESEARCH_CANDIDATE_NOT_PAPER kararı
üretilir. Hepsi paper_eligible=false ve live_allowed=false.
Çoklu test aile düzeyinde istatistiksel alfa kanıtlanmış
değildir; geçmişten seçilen aday ayrı gerçek forward
çalışması görmeden PAPER/LIVE'e bağlanmaz.

## 5. Bağımsız ve kalıcı kanıt

Actions adı: **Eaverse V5 hourly causal-flow intelligence
(RESEARCH ONLY)**. Testler PR'da, gerçek kaynak sınaması main
üzerinde push/dispatch ve pazartesi best-effort UTC çalışır.
Bütünleşik geçmiş sonuçları her sembol için ayrı
research/eaverse-hourly-btcusdt-evidence veya
research/eaverse-hourly-ethusdt-evidence kanıt dalında
SHA bağlantılı özetler olarak biriktirilir. Ham veriler
piyasa veri lisansına uymak için bu dallara taşınmaz.
Artifaktlar 90 gün saklanır. Aynı kaynak SHA + aynı
hipotez politikası yeniden çalıştığında yeni gözlem
oluşmaz, kaynak revizyonu ayrı etiketlenir.

## 6. Henüz tamamlanmayan kaynaklar

BIST'te KAP/SPK yayın saatli olayların ve tarihsel
üyelik/delist fiyatlarının tam uyumlu kamuya açık
araştırma girdileri; ABD'de SEC CIK tabanlı olayların
fiyat PIT evreniyle birleşimi; kriptoda OKX L2,
Kraken bağımsız işlem akışı, Deribit OI/funding/IV,
ve gerçek dizi-derinlik delta analizi **bu PR ile
tamamlandı denemez**.

Binance Vision tarihsel veri kullanım hakları,
lisans ve otomasyon koşulları:
https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md.
Ticari/üretim yeniden kullanımı otomatik izinli varsayılmaz.
2024–2025 arşivleri günümüzde indirilmiş dosyalar olduğu
için kaynak geçmiş-vintage düzeltmesi riski sürer; hiçbir
eski rapor ilk sefer görüldüğü zamana taşınmaz.

## Özet

Eaverse'ın strateji keşfini yalnız teknik kombinasyon
üretiminden çıkarıp **veri yeterliliği kontrolü + asof
özellik + eşzamanlı karşılaştırma + ayrı tutulan test
dönemi + karara bağlı araştırma kuyruğu** ilkelerine
yaklaştırır. Bu çekirdek aynı mimariyle daha sonra
meşru ücretsiz ABD/BIST olay adaptörlerinin gerçek
verileriyle genişletilebilir; burada doğrulanmayan
kaynak ve başarı iddiaları yapılmaz.
