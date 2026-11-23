# Eaverse V6 — BIST ve ABD tarihsel kaynak zekâsı

Bu araştırma tamamen **Eaverse** repo'sunda çalışır. quant_superlab,
kullanılabilecek kaynakları önerebilir; ancak olayların, evren geçmişinin
ve veri bütünlüğünün yeri ve kararı Eaverse'ın kendi kanıt dallarıdır.

**Evrendeki açık eksikler hâlâ korunur.** Bir kamu kaynağının geçmiş
fiyatı bugün elde edilmiş olması, o tarihte kamuya hangi saat ulaşmış
olduğunun kesin kanıtı değildir. Kaynağın durumunu yalnızca
READINESS/BLOCKER olarak etiketliyoruz; teknik stratejileri veya PAPER
kabul kurallarını değiştirmiyoruz.

## Doğrudan ücretsiz tarihsel veri aileleri

### ABD — Cboe VIX
Resmî VIX 1990+ günlük OHLC CSV:
https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv

GitHub Actions gerçek dosyayı kontrol eder; 6000+ farklı gün,
tarih sırası, geçerli OHLC ve değiştirilemeyen kaynak SHA zorunludur.
Önceden sabitlenmiş 25 ve 35 VIX kapanış geçişlerini sayar.
**VIX günlük kapanışının bire bir yayımlanma saati henüz bu CSV
dosyasından ispatlanamaz**; bu olaylar aynı gün açılışında bilinen
sinyallermiş gibi motorla backtest edilmez. VIX tarihi, ücretli
tam opsiyon zinciri, IV yüzeyi veya orderflow'un yerine geçmez.

### BIST — tarihsel üyelik
Şu repo, BIST'in daha önce yayımlanmış yıllık endeks dosyalarını
2000–2026 için yeniden birleştirdiğini bildiriyor:
https://github.com/GocmenCo/bist-constituents

Git tam commit'i sabitlenir:
21baf355201841f03b2be49e25fc4f51e5c63e06.
CSV'nin **münhasır** XU030 / XU050 / XU100 alt katmanları,
önce birleştirilmiş 30+20+50 tarihsel üyelik olarak denetlenir.
Eksik son snapshot otomatik "geçerli 100 hisse" sayılmaz.
Bugünkü BIST üyeleri geçmiş evren diye yeniden kullanılamaz.
Kaynağın tarihsel üyelik günü; orijinal dosyanın tam yayın
anı anlamına gelmez. Delisted fiyatlar ve kurumsal eylem
getirileri de buradan çıkmaz. Kod lisansı ile altında
yatan borsa verisinin tekrar kullanımı ayrı haklardır.
Bu veri, ancak **sağkalım yanlılığı denetimi** olarak kabul edilir.

### ABD — SEC issuer olayı
Resmî API:
https://www.sec.gov/search-filings/edgar-application-programming-interfaces

İlk bağımsız CIK kaynakları: Apple 0000320193,
Microsoft 0000789019, Nvidia 0001045810.
Accept/8-K, 10-K, 10-Q, Form 4 olaylarının özgün
acceptanceDateTime bilgisi, mevcut
eaverse.pit_adapters.sec_events içinde **24 saat gecikmeli**
araştırma saatine çevrilir. FilingDate, mali raporun
çeyrek bitişi ve bugünkü ticker geçmişteki kamu yayın anı değildir.
İlk sürüm yalnız submissions API'nin recent kayıtlarını
okur; ek historical file segmentleri henüz eklenmedi.
Bu nedenle tam SEC geçmişi dendiğinde veri eksikliği açık yazılır.

SEC otomatik isteklerinde meşru tanımlayıcı firma/kişi ve
iletişim e-posta adresi içeren User-Agent gerekir.
Eaverse Actions Secret olarak SEC_RESEARCH_USER_AGENT
**zaten tanımlıysa** araştırma çalışır; yoksa Eaverse
istek atmaz ve SEC_REQUIRED_CONTACT_USER_AGENT_NOT_CONFIGURED
olarak açıkça kaydeder. Sahte iletişim kimliği üretilmez.
Herhangi bir broker hesabı/API key'i gerekmez.

## Otonom kaynak kararı ve kalıcı kanıt

Workflow: Eaverse V6 equity historical-source intelligence (NO ORDERS).

- Gerçek indirme sırasında sunucunun döndürdüğü dosyanın
  SHA-256'sı, tarih aralığı, örnek sayısı, eksik kaynak hatası
  ve kaynak sınıflaması kaydedilir.
- Ham vendor dosyaları, kullanıcı portföyü veya kapanmış
  işlem kayıtları GitHub'a aktarılmaz.
- Ayrı kalıcı SHA kanıtı:
  research/eaverse-equity-source-evidence /
  observations/equity_sources/source_log.jsonl.
- Her yeni kaynak revizyonu ayrı giriş olur. Aynı kaynak
  kapsamı tekrar tarandığında sahte yeni gözlem oluşmaz.
- Sadece veri ailesi ve kimlik **test_ready** olduğunda,
  ileride ayrı Eaverse hipotezlerine girdi olabilir.
  Herhangi bir testte gerçek ilk yayın saati, fiyat,
  delist/corporate action düzeltmesi veya maliyet yoksa,
  trade kararına bağlanmaz.

## Kalan en önemli veri mühendisliği işleri

BIST'te KAP/SPK olay sürümleri ve delisted fiyatlarının
PIT uyumlu bütünleştirilmesi; ABD'de SEC'in historical
filings-file segmentleri, FINRA short-interest ile
short-sale-volume ayrımı, şirket değişim/CIK-vintage
haritaları; kriptoda OKX L2, Kraken bağımsız spot trade
ve Deribit OI/funding geçmişinin aynı ekonomik anda
bilinebilir biçimde tekrar oynatılması.

Bu sürümün başarısı, **source readiness kayıtlarının
gerçek kaynak üzerinde çalışmasıdır**; yeni alpha,
PAPER veya LIVE performans onayı değildir.

### Gerçek kaynak temizliği ve hatalı görünür başarı kontrolü (23 Kasım 2026)

İlk GitHub Actions çalışması işlem olarak başarılı olsa da Cboe VIX ile
BIST üyeliğinin her ikisi doğrulamada reddedilmişti. Bu bir **veri
doğrulaması başarısı değildir**. Yeni kontrol, kaynak geri dönmüşse
SHA-256/byte sayısını ve güvenli, kısa doğrulama-hatası kodunu da kanıt
dosyasına yazar. Sadece workflow yeşili kaynak başarılı demek değildir.

GocmenCo kaynağının gerçek sabit Git blob'u incelendi: **54.008 veri
satırı**, **927 sembol**, **134 tarihsel kesit** ve bunların
**133 tanesinde** münhasır 30/20/50 endeks katmanı mevcut. Ancak geçmiş
kesitlerde BYSAN, TUMTK ve TSGYO gibi tekrar eden aynı-gün ticker
kimlikleri var. Bunları sanki bağımsız iki hisseymiş gibi saymak veya
sessizce silip evreni kusursuz ilan etmek hatalıdır; güncellenen
denetim tam miktarını, farklı tier uyuşmazlıklarını ve
**UNIVERSE_PIT_TRADE_ELIGIBLE=false** durumunu kaydeder.
2026-10-01 kesitinin 0/0/0 üyeliği 2026-07-01 sonrası
tam bir endeks yenilemesi sayılmaz.

BIST üzerinde gerçek geçmiş trade/backtest için ayrıca şirket kimliği,
duyuru saati, fiyat/split/delist ve orijinal kaynak hakları gerekir.
Cboe VIX doğrulamasında reddedilen alan/sıra/OHLC nedeni, tam ham
arşiv yayımlanmadan kodla birlikte raporlanır ve doğrulayıcı
yalnız gerçek dosya biçimine göre düzeltilebilir.
