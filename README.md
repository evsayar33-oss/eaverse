# Eaverse

Eaverse planlı olarak strateji hipotezleri üretir, BIST, ABD ve kripto verisinde tarar ve uygun kripto adaylarını ileri tarihli sanal portföyde sınar. Tüm hesaplar sanaldır. Broker bağlantısı ve işlem yetkisi yoktur.

## Araştırma döngüsü

1. İlk tarama listesi 18 varlıktır. Coinbase günlük spot mumları kriptoyu, Yahoo günlük fiyatları BIST ve ABD'yi besler. İş akışı günde üç kez çalışır. Her turda varlık başına en fazla 24 yeni yapı sınanır.
2. Arama kataloğu 34.636 yapı içerir. SMA, EMA, momentum, fiyat kırılımı, RSI, hacim, düşük oynaklık, ATR ve Bollinger koşullarını tekli, ikili ve üçlü gruplarda dener. Oy eşiği, sinyal teyidi ve pozisyon boyutu varyasyonları vardır. Bir varlıkta denenmiş yapı veritabanında saklanır. Sonraki tur, önceki keşif isabetini ve az denenmiş kural ailelerini birlikte kullanır.
3. Bir turun adayı yalnızca keşif verisinden seçilir. Üç kronolojik dönem, çift maliyet ve ek bir bar gecikme kullanılır. Aday 180 günlük daha yakın tarihli bir pencerede aynı elemeden geçer. Bu pencere zaman içinde başka adaylar tarafından da görülebilir. Asıl bağımsız kanıt, seçimden sonraki fiyatlarla ölçülür.
4. Kriptoda uygun aday için kurallar, maliyet, veri parmak izi ve başlangıç tarihi sabitlenir. Bir varlıkta aynı anda bir PAPER deneyi ilerleyebilir. Sinyal günlük kapanışa dayanır. Kripto piyasasında kapanış ve sonraki açılış aynı ana denk geldiği için sanal uygulama bir ek günlük bar bekler. Bu süre, fiyat verisinin yayımlanması ve zamanlanmış araştırmanın çalışması için ayrılmıştır. PAPER 90 bar getirisi ve en az sekiz tamamlanmış işlem ister. Aday çift maliyet sonrası nakdi ve aynı varlığın al-tut sonucunu aşmalı, %25 düşüş sınırında kalmalı ve çoklu testleri hesaba katan önceden belirlenmiş yaklaşık HAC testini geçmelidir. Kesintiler ve veri revizyonları ilerlemeyi durdurur.
5. BIST ve ABD başlangıçta araştırma kapsamındadır. İzleme listeleri sabittir. Yahoo fiyatları geriye dönük düzeltilmiştir ve geçmiş evreni o günkü üyelikle yeniden kurmaz. Eski bir veri boşluğu varsa ve en az 600 tam seans kalıyorsa tarama son kesintisiz aralığı kullanır. Bu iki piyasanın otomatik PAPER yetkisi veri doğrulaması tamamlanana kadar kapalıdır.

PAPER sonucu, denenen varlık ve döneme ilişkin simülasyon kanıtıdır. Gerçekleşebilir emir, işlem kapasitesi ve gelecekteki getiri ayrı konulardır.

## Kurulum ve devamlılık

- [Autonomous research](../../actions/workflows/research.yml) ilk kurulum gönderiminde veritabanını başlatır. İlk çalıştırma başarısız olursa iş akışını bir kez **bootstrap** seçeneği açıkken manuel çalıştır. Sonraki çalıştırmalar kaldığı yerden devam eder. Eksik veya bozuk kontrol noktası veri kaybını önlemek için işi durdurur.
- Telegram için bu depoya `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` adlı Actions secret'larını ekle. Eski deponun secret'ları buraya geçmez. Telegram'a ulaşılamadığında olay GitHub Issue olarak kaydedilir. Bildirimler PAPER doğrulaması veya kalıcı çalışma/veri hatası için üretilir. İletim onayı ile kayıt arasındaki kesinti nadiren tekrar mesaja yol açabilir.
- [Research watchdog](../../actions/workflows/watchdog.yml) araştırma işinden ayrı çalışır ve ardışık başarısızlıkları veya 20 saati aşan çalışma boşluğunu bildirir. İki iş de GitHub Actions zamanlayıcısını kullanır.
- Git geçmişinde kod ve yapılandırma bulunur. Deneme ve PAPER kayıtları [research-state-v1](../../releases/tag/research-state-v1) sürümünde, son üç sıkıştırılmış SQLite kontrol noktasında tutulur. Rapor çıktılarının ömrü yedi gündür. Dağıtım kamuya açıktır. Ledger yalnızca pazar kimliği, test ölçümü ve simülasyon kaydı için kullanılır. Ledger 768 MiB sınırına ulaşırsa çalışma durur ve daha büyük bir saklama hedefi gerekir.
- Kaynak `quant_superlab` ağacı `reference/quant_superlab` altında sabitlenmiştir. `git submodule update --init` ile açılır. Kaynak stratejiler, ICT/SMC, terminal ve H119–H122 iş akışları bu kopyadan incelenebilir. Eaverse zamanlayıcısı kaynak iş akışlarını çalıştırmaz ve mevcut botlara yazmaz.

## Yerel çalıştırma

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m eaverse.engine --market CRYPTO --budget 6
```

`runtime/report.json` deneme sayılarını ve adayları gösterir. `runtime/research.sqlite` tüm denemeleri, PAPER durumlarını ve bildirim kuyruğunu içerir.
