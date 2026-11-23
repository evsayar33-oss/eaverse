# Eaverse V9 — Kanıta Bağlı Çok-Kaynaklı Araştırma Geçidi

Ana araştırma sistemi Eaverse'tır. Bu katman, V8'in 148 ekonomik hipotezini
**gerçek arşiv, tarihsel varlık kimliği, açıklama/revizyon saati, orijinal
borsa verisi, bağımsız kaynak ve deney protokolü** koşullarına bağlar.

## Ne gerçekten uygulanmıştır?

- \`research_integrity.py\`: yerel ham dosyanın SHA-256'sını bizzat okur;
  tarihsel ticker/issuer geçerliliğini ve delist tarihini kontrol eder;
  observation date, first publication, \`first_seen\` ve sonraki
  revision/vintage zamanlarını ayırır; yalnız karar anında kamuya açık
  revizyonu seçer; bir borsanın L2 snapshot + sıralı delta zincirinde
  eksik sequence, ters zaman, çapraz book, farklı venue/instrument
  splice işlemini reddeder.
- Aynı ekonomik olayın SEC/XBRL gibi aynı temel kaynaktan iki kez
  onaylanması geçerli corroboration değildir. Farklı yayıncı ve bağımsız
  \`origin_lineage_root\` koşulu aranır. Bağımsız kanıt da ayrıca
  dosya SHA'sı, publication/vintage ve kullanım hakkı denetiminden geçer.
- \`causal_discovery.py\`: tasarladığı her hipoteze V9 kontrol sonucu
  ekler; tasarım üretmek veri/alpha/PAPER doğrulaması değildir.
- \`integrity_audit.py\`: \`<evidence-root>/bundles/<id>.json\`
  dosyalarını yalnız varsa okur; belirtilen arşivleri gerçekten
  hash'ler, \`audit.json\` üzerinden gerekçeli statü verir.
- \`oos_prospective.py\`: kayıtlı deney ailesi için maliyet sonrası
  olay-kontrol eşleştirmesi, kronolojik ayrılmış fold, embargo,
  eşleşmiş sign testi ve Bonferroni düzeltmesi yapar. Keşif ve
  doğrulama geçmezse holdout **okunmaz**. Holdout geçmeden prospective
  açılmaz. Prospective olayda tarihsel veriden uydurulmuş first_seen
  veya kayıt öncesi olay kullanımı reddedilir.

## Doğrulanması gereken kritik alanlar

Her gerçek bundle kendi özellikleri için aşağıdakilerin özgün kanıtına
sahip olmalıdır:

\`\`\`text
archive bytes + sha256
  -> market/venue/instrument identity + verified research rights
  -> historical stable entity + original membership/delist/corporate actions
  -> observation_at != publication_at; first_seen >= publication_at
  -> revision_id, vintage calendar and exact original release evidence
  -> native order book snapshot+gap-free deltas, if the feature is L2
  -> separate source archive and different origin_lineage_root corroboration
  -> fixed protocol SHA + non-overlapping purged chronological folds
  -> after-cost paired controls + familywise testing
  -> future-only first_seen prospective evidence
  -> independent human execution/source audit (never auto PAPER or LIVE)
\`\`\`

İlgili source family'nin Eaverse'de *katalogda bulunması*, internetten
bugün erişilebilir olması veya indirme ZIP'inin SHA'dan geçmesi,
o tarihteki yayın anını ya da delist-aware fiyat dizisini **kanıtlamaz**.
Kaynak hakları ve ticari olmayan araştırma lisansları ayrıca
denetlenmelidir. Kod içindeki \`...verified=True\` alanlarının
bağımsız kanıt/audit olmadan yazılmış olması gerçek veriyi doğrulamaz.
Bu nedenle V9'un \`PIT_RESEARCH_READY_NOT_ALPHA\` sonucu bile sadece
**yapısal olarak denetlenebilir araştırma girişi** anlamına gelir.

## Çalıştırma

\`\`\`bash
python -m unittest discover -s tests -p 'test_research_integrity.py' -v
python -m unittest discover -s tests -p 'test_causal_discovery.py' -v
python -m eaverse.integrity_audit --out out/pit_integrity/audit.json
# Gerçek lisans ve arşiv doğrulaması yapılmış özel dataset ile:
python -m eaverse.integrity_audit --evidence-root /secure/research-archive --out out/pit_integrity/audit.json
\`\`\`

Bu PR gerçek yeni 20 yıllık BIST/delisted fiyat serisi, gerçek full
ABD listed/delisted security master, Kraken/OKX L2, ALFRED vintage,
SPK/KAP tam otomatik historik olay toplayıcı veya 148 hipotezin
**gerçek bağımsız kârlılık backtestini** ürettiğini iddia etmez.
Bunların kalıcı, izinli ve zaman damgası kanıtlı ingester'ları
sonraki veri-mühendisliği kapsamıdır. Özellikle BIST L2 veya hakları
belirsiz ücretli veriler hiçbir zaman sentetik olarak doldurulmaz.

### Önemli fail-closed sınırları

- 148 hipotez için ana repoda gerçek tam PIT bundle yoksa 148/148
  blocked çıkmalıdır. Bu bir testin başarısıdır, alpha değildir.
- Sahte pozitif testler için üretilen fixture'lar gerçek piyasa
  performansı değildir. \`oos_prospective.evaluate\` tüm kademeleri
  geçse dahi \`prospective_validated=false\`,
  \`paper_eligible=false\`, \`live_allowed=false\`.
- V7.1 ve mevcut PAPER/Telegram/işlem kararları değiştirilmez.
  Ham lisanslı piyasa ZIP'leri veya kullanıcı portföyleri Action
  artifaktına ya da GitHub kanıt dalına yüklenmez.
