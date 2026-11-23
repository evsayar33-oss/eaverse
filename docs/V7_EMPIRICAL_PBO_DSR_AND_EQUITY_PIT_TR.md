# V7 gerçek hizalanmış getiri yolları / PBO, DSR ve kaynak kanıtı

## Neden bu modül eklendi?
Önceki V7 raporu 64 hipotezin yalnızca dönem bazlı özetlerini saklıyordu.
Bu verilerden gerçek CSCV/PBO, DSR veya White Reality Check geri üretilemezdi.
Yeni `eaverse.empirical_trials` aynı doğrulanmış 2023–2025 36 aylık
Binance Vision 1h arşivini (resmî ZIP checksum kontrolünden sonra) kullanır.
32 uzun spot hipotezi ve 32 aşağı-yönlü risk hipotezi **ayrı** tutulur.

## Sayısal metodoloji
- Tüm **32 önceden kayıtlı uzun hipotez** (kazanan olmayanlar dahil) aynı
  saatlik fiyat serisi ve aynı keşif/doğrulama penceresinde yeniden oynatılır.
- Tamamlanmış sinyali takip eden saat açılışı, kayıtlı horizon kadar sonraki
  açılış ve sabit iki kat gidiş-dönüş maliyeti kullanılır. Gerçek emir/fill değildir.
- Aynı hipotez içindeki ardışık olaylar çakışmaz; tamamlanan olayın net
  getirisi, gerçek çıkış saatinin içinde bulunduğu haftaya kaydedilir.
  İşlem yoksa o haftanın nakit maruziyeti sıfır getiridir.
- Resmî 2023 bakım kesintisi ve araştırma embargo saatleri korunur.
  Düşük kapsamlı (<120 saat) sınır haftaları dışlanır.
- CSCV PBO **8 kronolojik blok ve 70 simetrik bölme** ile gerçek,
  kaynak kaynaklı haftalık net getiri matrisinden hesaplanır.
  CSCV bloklarının permütasyonu ileri tarihli OOS kanıtı değildir.
- DSR: keşifte en yüksek Sharpe'a sahip seçilmiş modelin ayrı
  doğrulama haftaları üzerindeki yaklaşık selection-adjusted Sharpe
  olasılığıdır. Planlı 8 × 32 = 256 uzun hipotez sayısı kullanılır.
  Korelasyonların etkin bağımsız deneme sayısı bilinmediğinden tam
  family-wise bağımsızlık güvencesi vermez.
- White-style Reality Check, seçilen model değil tüm 32 varyant için,
  birlikte örneklenen 400 adet geometrik 3 haftalık blok bootstrap ile
  raporlanır. Nakit sıfır-risk karşılaştırması piyasa betası eşleştirmez.
- **Holdout** bu hesaplarda hiçbir zaman okunmaz. Kaynak ilk yayın/vintage
  delili, bağımsız borsa fill'i, delist-aware hisse PIT verisi,
  gerçek future prospective test ve merkezî ACCEPT hâlâ yoktur.

`out/extended_flow/statistical_trials.json`: 32 hipotezin her biri için
aynı haftaların net getirileri, dönemin başlangıcı, kaynak/politika SHA,
haftalık panel SHA, PBO/DSR/Reality Check, açık proof eksikleri,
PAPER/LIVE=false. Ürün stratejisi, Telegram, PAPER ve lab kabul kuralları değişmez.

Çalışma çıktısı (tedarikçinin ham mum/tick dosyası değil; strateji
getiri vektörleri) ayrı ve hash-zincirli
`observations/extended_flow/statistical_trials.jsonl` üzerinde source-SHA
sabit şekilde kalır. Aynı kaynak+politika tekrar çalıştırıldığında yeni
prospective veri sayılmaz. Altta yatan arşivlerin kişisel/ticari olmayan
kullanım koşulları ayrıca geçerlidir; ham vendor dosyası yayımlanmaz.

## BIST/ABD tarihsel gerçekçilik
V6 SEC kayıtları daha eski `filings.files` referanslarını CIK bazında,
yalnız SEC'in yayımladığı güvenilir dosya isimleri üzerinden, en fazla iki
segment/CIK olarak tarayabilir; alınan her dosyanın SHA-256'sını,
acceptance+24h araştırma saatini ve daha eski olay kapsamını kaydeder.
SEC için hâlâ meşru iletişim User-Agent'ı gerekir, yoksa atlanır.
Bugünün CIK-ticker eşleşmesi geçmişe taşınmaz; Form 25 ve fiyat geçmişi
ile tam delist ve şirket birleşme kanıtı bu entegrasyonun dışında kalır.

BIST'de sabitlenmiş topluluk tarihsel üyelik arşivinde
`delisted_in_year` işaretli kaç farklı sembol olduğu ve veri belirsizliği
ayrıca raporlanır. Bu işaretler delist olmuş şirketlerin doğrulanmış OHLC,
split/dividend-adjusted getirileri, gerçek etki/yayın tarihi veya hak/lisans
delili değildir. BIST ve ABD için **gerçek bütünleşik fiyat/evren PIT
tamamlandı** iddiası yasaktır.

## Bağımsız karar laboratuvarı bağlantısı
`quant_superlab` salt-okunur kaynak-SHA sabitli 8 V7 istatistik
kanıt dalını ve orijinal V7 hipotez zincirini indirir, hash'leri ve model
kimliklerini doğrular, 8-blok PBO'yu kaynak return panelinden **bağımsız**
yeniden hesaplar; mevcut `portfolio_qualification.gate.review`
kanıt pasaportunu gerçek eksiklerle değerlendirir.

Bu, `lab.validation.validate` için kaynak PIT, fill modeli, bağımsız
3 yıllık OOS ve source-native strateji adaptörü sağlandığı anlamına
gelmez. Her aktarım **RESEARCH_ONLY**; merkezî ACCEPT, yeni PAPER veya
LIVE yetkisi üretmez.
