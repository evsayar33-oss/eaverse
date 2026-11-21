# Eaverse

Eaverse birden çok piyasada teknik strateji ve takvim hipotezlerini araştırır. Uygun kripto adaylarını güncel fiyatlarla PAPER portföyde sınar. Tüm hesaplar sanaldır.

## Varlık evreni ve sürekli tarama

| Piyasa | Varlık listesi | Günlük veri | En fazla varlık/tur |
|---|---|---|---:|
| ABD | SEC borsa kotu listesi, Nasdaq Trader yedeği ve başlangıç ETF'leri | Yahoo | 40 |
| BIST | KAP BIST şirketleri | Yahoo | 32 |
| Kripto | Coinbase Exchange aktif USD spot pariteleri | Coinbase Exchange | 24 |

Günde altı tur planlanır. Liste kaynakları günlük yenilenir. Canlı ABD listesi erişilemezse son başarılı liste veya tarihli SEC kopyası kullanılır. Rapor canlı liste, önbellek ve bu kopyayı ayırt eder. Kuyruk her turda aktif PAPER varlıklarına öncelik verir. Kalan kapasite yeni sembollere ve daha önce işlem verisi doğrulanan varlıklara ayrılır. Her aday en az 600 ardışık tamamlanmış günlük bar ister. Yeni kotlar ve fiyat verisi eksik semboller raporda ayrı görünür. Listenin tamamı birden çok turda taranır. Mevcut BIST ve ABD listeleri geçmiş endeks üyeliğini, delist verisini ve geçmişteki tüm şirketleri kapsamaz.

## Teknik deneyler

- Temel katalogda 34.636 yapı vardır. Araştırma SMA, EMA, momentum, kırılım, RSI, hacim, oynaklık, ATR ve Bollinger'a ek olarak MACD, stokastik, ADX, OBV, hacim ağırlıklı ortalama ve ortalamaya dönüş kurallarını dener.
- Keşif aynı göstergeyi farklı pencere ve eşiklerle, tek başına veya birden çok koşulla sınar. Oy eşiği, sinyal teyidi ve pozisyon boyutu da değişebilir. Uygun keşif sonuçları, aynı varlık veya başka piyasalarda benzer kuralları denemek için başlangıç olarak kullanılır.
- Varlık başına bir turda en fazla 24 yeni teknik deneme yapılır. Deneme kimlikleri tekrarları önlemek için saklanır. Aday seçimi keşif dönemine dayanır. Üç kronolojik bölümde çift maliyet ve gecikme uygulanır. Aday daha yakın 180 günlük pencerede yeniden taranır. Tarihsel dönemler tekrar kullanılabilir ve bu tarama ileri tarihli kanıtın öncesindeki elemedir.
- Kripto adayında kural, maliyet, veri parmak izi ve ilk sanal işlem tarihi sabitlenir. Bir varlıkta aynı anda bir PAPER deneyi ve depoda en fazla 12 aktif PAPER deneyi olabilir. Piyasanın günlük kapanışı yayımlandıktan sonra bir bar beklenir. PAPER doğrulaması 90 getiri barı, en az sekiz tamamlanmış işlem, çift maliyet sonrası nakit ve al-tut kıyaslarının aşılması, en fazla %25 düşüş ve çoklu testleri hesaba katan ön kayıtlı yaklaşık HAC eşiği ister.

## Haber ve takvim deneyleri

ABD TÜFE, ÜFE, istihdam, GSYH, kişisel gelir ve harcamalar (PCE), JOLTS ve perakende satış açıklamaları ile FOMC kararları taranır. BLS ve Fed arşivleri ile St. Louis Fed'in ALFRED yayımlanma tarihleri kullanılır. Kaynak kesintisi uzarsa olay testi bilinen arşiv tarihinde biter. Her piyasanın ilk uygun işlem açılışından başlayarak takip eden birinci, ikinci ve üçüncü işlem günleri ayrı sınanır. Veri, duyuru günü açılışının sonradan bilinen haberle etkilenmesini önlemek için bu açılışı testin dışında bırakır.

Aşağıdaki hipotezler her varlık için dönüşümlü sınanır:

- Tek olay, iki olayın kesişimi veya birleşimi ve bir önceki olayın yakın geçmişte olması.
- Haftanın günü, ayın ilk ve son üç takvim günü ile haber ve haftanın günü kesişimleri.
- İşlemi olay penceresiyle sınırlama veya olay penceresinde pozisyonu kapatma.
- Al-tut, sabit teknik kurallar ve keşif adayları üzerinde filtreli versiyonu aynı filtresiz stratejiyle kıyaslama.

Her test net getiri, düşüş, işlem sayısı ve olay gözlemi sayısı kaydeder. Actions çıktısındaki `event-daily.jsonl`, son tarihsel pencerenin gün gün masraf sonrası strateji ve kıyas getirisini taşır. Aynı olay tasarımı yeni çeyrek verisiyle yeniden sınanabilir. Testler duyuru tarihini kullanır. Beklentiye göre sürpriz büyüklüğü, gün içi fiyat tepkisi ve şirket haberleri bu sürümde kapsama dahil değildir. Olay sonuçları tarihsel araştırmadır. Otomatik PAPER kabul kapısı teknik adaylar içindir.

## Çalıştırma ve koruma

- [Autonomous research](../../actions/workflows/research.yml) yerleşik kayıt noktasını açar. Güncellemeler bu kaydı devralır. `bootstrap` seçeneği yalnızca boş depo kurulumu içindir. Eksik veya bozuk yerleşik kayıt, araştırmayı durdurur.
- [Research watchdog](../../actions/workflows/watchdog.yml) tekrarlayan hata ve çalışma boşluklarını izler. Telegram için Actions secrets içinde `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` tanımlanır. Gönderim yapılamadığında önemli olaylar GitHub Issue kaydına düşer.
- [Research state](../../releases/tag/research-state-v1) son üç sıkıştırılmış SQLite kayıt noktasını taşır. 30.000 ayrıntılı kayıt toplandıkça özetler, kurallar ve doğrulama ayrıntıları tarihli Release arşivine yazılır. Yükleme özeti doğrulanınca aktif kayıt, deneme kimliğini, öğrenme için gerekli sonucu ve arşiv bağlantısını saklar. PAPER kayıtları aktif dosyada kalır. Günlük rapor çıktıları yedi gün tutulur.
- `reference/quant_superlab` kaynak depoyu sabit bir sürümde gösterir. `git submodule update --init` ile açılabilir. Tarihsel araştırma veya PAPER sonucu tek başına gelecekteki getiri hakkında garanti vermez.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m eaverse.engine --market CRYPTO --assets-per-market 5 --budget 6 --event-budget 4
```

## Streamlit PAPER paneli

Streamlit Community Cloud'da depo olarak `evsayar33-oss/eaverse`, dal olarak `main` ve giriş dosyası olarak `app.py` seçilir. Python 3.12 ile yerelde `streamlit run app.py` çalışır.

Panel, araştırma işi kalıcı kaydı doğruladıktan sonra yayımlanan küçük bir JSON dosyasını okur. Panel için Streamlit secret'ı veya kişisel GitHub token'ı gerekmez. Açık panel, yayımlanan kaydı beş dakikada bir kontrol eder. Ölçümler araştırma turlarında ilerler. Telegram için GitHub Actions'ta yalnızca `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` gerekir. `github.token` GitHub tarafından iş çalışırken verilir.

Aktif ve tamamlanan adaylar ayrı görüntülenebilir. PAPER sekmesi son tamamlanmış getiri barına kadar maliyet sonrası kümülatif getiri, al-tut ve nakit kıyası, düşüş ve işlem istatistiklerini gösterir. Yeterli seri oluştuğunda Sharpe, Sortino ve oynaklık görünür. İşlem kazanma oranı, beklenti ve kâr faktöründe tam kapanan pozisyon dönemleri sayılır. Geçmiş test sekmesi adayın izlemeye alınmadan önceki keşif ve yakın dönem testlerinin dondurulmuş değerlerini gösterir. Eski kayıtların saklamadığı alanlarda veya hesaplama için gözlem yetersizse değer boş kalır.
