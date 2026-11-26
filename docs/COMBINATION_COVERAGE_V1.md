# Ortak kombinasyon araştırma kapsamı

Bu kuyruk **55 isim alanlı teknik kural türünü** bir araya getirir: 24 yeni
indikatör ailesi ve 31 eski Foundry/parametre yüzeyi türü. Bazı türler aynı
indikatörün farklı kural veya hesaplama sürümüdür; bunlar 55 benzersiz klasik
indikatör olarak sunulmaz. Önceki Foundry, PAPER ve dondurulmuş kohort kodları
değiştirilmez.

## Kayıtlı tasarım

- Her teknik kural türü tek başına.
- Yeni-yeni (276), yeni-eski (744), eski-eski (465) unordered aile çiftleri;
  ayrıca 55 aynı aile ikili farklı ayar tasarımı.
- Üçlüler, 4–7 kural ve 8–55 kural grupları ayrı rotasyonlarla ele alınır.
  Kayıtlı alan bütün farklı kural alt kümelerini kapsar; alt kümeler
  çalışma anında rank/unrank ile üretilir, devasa liste belleğe alınmaz.
- Günlükte 29 olay türü: 8 makro açıklama tarihi, 9 takvim koşulu ve 12
  OHLCV olayı (fiyat şoku, gap, hacim sıçraması, volatilite genişlemesi/
  daralması, kırılım, yön dönüşü, drawdown). Haftalıkta 22 olay türü
  (hafta günü koşulları kaldırılır).
- Olayların tekli, ikili, daha büyük alt kümeleri ve teknik alt kümelerle
  çapraz ürünleri ayrı kayıtlı katmanlardır. Olay birleşimi: AND, OR,
  uygun olduğunda çoğunluk; yalnızca olayda/olay dışında filtre. Teknik
  birleşim: AND, OR, üç veya daha fazla kuralda çoğunluk. Her kayıtlı
  teknik tasarım long/short/iki yön, yalnız olay tasarımları long/short
  olarak değerlendirilir. Short gerçekleşebilirlik varsayımıdır.
- Ayrı sürekli ayar kuyruğu; bağımsız periyot, kaynak, eşik, geçiş,
  yön, sıralı/filtre/ağırlıklı mantık, risk boyutu, ATR SL/TP/trailing ve
  kademeli kurallar. Makro sinyal ofseti ve geriye dönük olay penceresi
  de değişebilir. Bu bölüm sınırsız sayısal parametre uzayının eksiksiz
  taranması değil, tekrarlanabilir örneklemedir.

## Süre ve kayıt koruması

Her varlık/zaman diliminin ayrı kalıcı rank göstergesi vardır. Her ziyarette
iki teknik ve olay araştırması açıksa bir olay/karma kuyruk slotu kullanılır.
Çiftler ve temel kombinasyonlar, çok büyük tasarım katmanlarından bağımsız
ve daha sık ilerler. Sıra varlık bazında bijektif permütasyonla değişir;
performans sonucuna göre bir aile dışlanmaz. Yeni iş akışı veya ücret yok;
mevcut yürütme süresi sınırı uygulanır.

Eksik geçmiş/kaynak, tanımsız ölçüm veya yetersiz bağımsız olay sayısı
tamamlanmış deneme değildir. Engellenen rank saklanır ve değişen veriyle
yeniden incelenir; başarısız tekrar diğer yeni tasarımları aç bırakmaz.
Bekleyen ranklar nedenlerine göre 1.024 bitlik parçalar halinde kayıpsız
sıkıştırılır; önceki 64 kayıt sınırı kaldırılmıştır. Eski kayıtlar doğrulanarak
taşınır. Her veri değişiminde başlangıçta sabitlenmiş sıra sonuna kadar ayrı
yeniden kontrol turu yürür; yeni sıranın ilerlemesi eski borcu aç bırakmaz.
Bir engellenen tekrar sonrası yeni tasarım denenebilir. Her varlıkta henüz
ziyaret edilmemiş ve veri/örneklem bekleyen ranklar ayrı raporlanır.
Yürütme bütçesi korunur; çok büyük teorik alan belleğe veya veritabanına
baştan yazılmaz. Tamamlanan tasarım ve gerçek tekil backtest
sayısı ayrıdır; tekrar kimliği yeniden sayılmaz. Eski arşivler korunur.

## Yürütme ve kanıt

Kaynakta tamamlanmış OHLCV, geçmiş referanslar ve kesinleşmiş yayın tarihi
kullanılır. Makro olay ilk sonraki tamamlanmış barda görünür, işlem bir
sonraki açılıştadır; en erken kaynak kesim tarihinden sonra test yapılmaz.
En az 120 kullanılabilir bar, iki kronolojik tarihsel bölüm, her bölümde
en az üç ayrı yürütülebilir olay epizodu ve olay dışı bar gereklidir.
Olay filtresi aynı filtresiz teknik strateji ile eş maliyetli kıyaslanır.
Getiri yolu, hash, kaynak/kod kimliği ve kontrol ölçümleri kaydedilir.
Mevcut Superlab sözleşmesi aritmetik kanıtı inceleyebilir; bu ACCEPT,
bağımsız OHLCV backtest, ileri sonuç, PAPER veya LIVE onayı değildir.

Streamlit ve Actions raporunda varlık/zaman dilimi bazında ana katalog ve
temel katalog için kapsanan, kayıtlı, kalan ve veri bekleyen borç gösterilir.
Katalog sayıları çok büyüktür ve tamamlanmış işlem/backtest değildir.
Tüm matematiksel kombinasyonların kısa sürede bitmesi gerçekçi değildir.

## Hariç tutulan kaynaklar

Bağımsız saatlik Binance akış olayları, SEC/KAP, short-interest, L2,
funding/OI ve vintage gibi kaynaklar kendi saat/veri sözleşmelerine sahiptir.
Bunlar ana günlük/haftalık OHLCV kataloguna kanıtlanmamış bir join ile
dahil edilmez. Açık kalan kanallar ayrı kaynak kapsamı tablosunda gösterilir;
bunlar tamamlanmış karma test olarak sayılmaz. Bu sürüm bu kaynakların
eksik tarihsel kapsamını tamamlamaz.


## Olumsuz sonuç hafızası ve arşiv

Tamamlanmış bir çalışmanın (araştırma sürümü, varlık, strateji kimliği)
anahtarı kalıcıdır; arşivleme sonrası da aynı kimlik yeniden test edilmez.
Eksik veri/örneklem nedeniyle engellenen adaylar bundan farklıdır.
Eski olumsuz sonuçlar (gerçek keşif/doğrulama kapısı reddi, tarihsel negatif
getiri veya iflas stresi) ve işlem kanıtı olmayan kayıtlar 256'lık eşikte
daha erken arşivlenir. Diğer tamamlanmış ayrıntılar 30.000 kayıt veya
16 MiB ayrıntı eşiğinde arşivlenir; tur başına en fazla dört parti işlenir.
Büyük getiri yolları ve tam kurallar, hash ile doğrulanmış sıkıştırılmış
GitHub Release dosyalarında korunur; ancak başarılı yükleme sonrası yerel
ayrıntılar kaldırılır ve SQLite fiziksel alanı boşaltılır. Anahtar, sürüm,
varlık, tarih, veri/kod referansı, sonuç etiketi ve özet metrikler kalır.
Foundry'nin keşif ebeveynlerini seçmek için kullandığı kural ve keşif
ölçümleri de korunur. Araştırma amaçlı `pass=false` tek başına başarısızlık
değildir. Eski etiketsiz arşivler, her turda bir dosya hash ve kayıt sayısı doğrulanarak yeniden indekslenene kadar belirsiz olarak raporlanır. Legacy takvim
araştırmasında çeyrek kimliğin parçasıdır; yeni çeyrek yeni tasarımdır.
Bu süreç başka varlıkları, ayarları veya yeni araştırma sürümlerini
karalisteye almaz; PAPER ve LIVE kararları değişmez.
