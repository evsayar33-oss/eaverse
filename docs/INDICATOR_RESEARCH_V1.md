# Genişletilmiş indikatör araştırması

Bu bağımsız kuyruk 24 gösterge ailesini OHLCV verisiyle araştırır. Önceki Foundry,
PAPER, ileri gözlem ve strateji kimlikleri değişmez. Yeni testlerin sürümü
`coverage-indicators-v1` olup ana teknik sayaçtan ayrıdır. Yeni veri sağlayıcı,
ücret, secret veya yeni zamanlama gerekmez.

| Aile | Araştırılan ayarlar / ölçümler |
|---|---|
| Ichimoku | Tenkan/Kijun/Span B, pozitif bulut kaydırması, bulut konumu, Tenkan-Kijun, Span A-B, gecikmeli fiyat karşılaştırması |
| Supertrend | ATR penceresi, katsayı, SMA/EMA/Wilder/WMA, çizgiye göre kapanış |
| WaveTrend | Kanal, ortalama, kaynak, WT çizgisi ve sinyal farkı |
| Bollinger | Periyot, 12 bant katsayısı, ortalama yöntemi, kaynak, %B ve bant genişliği |
| CCI | Periyot, fiyat kaynağı, ortalama mutlak sapma, ölçek sabiti |
| RSI | Periyot, fiyat kaynağı, dört ortalama yöntemi, RSI ve sinyal farkı |
| Stochastic RSI | Bağımsız RSI ve stokastik pencere, K/D, kaynak, RSI ortalaması |
| Williams %R, MFI, Aroon | Periyot, eşik, yön/kesişim/eğim kuralları |
| DMI, ADX | Periyot, ortalama, ADX ikinci pencere, DI farkı |
| Keltner, Donchian | Kanal periyodu, Keltner ATR/katsayı/ortalama/kaynak; Donchian önceki kanal konumu |
| PPO, MACD, TRIX, TSI | Hızlı/yavaş veya üçlü EMA, kaynak, sinyal farkı |
| CMF, Vortex, Chaikin Oscillator | Hacim akışı, periyot; Chaikin hızlı/yavaş/sinyal |
| KAMA | Verimlilik penceresi, hızlı/yavaş sabitleri, fiyat kaynağı |
| Ultimate Oscillator | Sıralı üç bağımsız pencere |
| Stochastic | Fiyat penceresi, K/D, fiyat kaynağı |

Günlük pencere adayları 3–200 bar arasında 20 ayrık ayardır; haftalık pencereler
60 bar ile sınırlıdır. İndikatörün formülüne göre anlamlı alt seçenekler
kullanılır. Eşikler, eşik kesişimleri ve yükselme/düşme seçenekleri ayrıdır.
Wilder ortalaması ilk tam SMA ile başlar. WaveTrend sinyal farkı SMA,
diğer çizgi-sinyal farkları EMA kullanır. Ölçek karşılaştırması için MACD
anlık fiyatla, Chaikin Oscillator yavaş pencere hacim toplamıyla normalize
edilir. Supertrend başlangıç yönü ilk geçerli ATR barındaki kapanış/orta fiyatla
belirlenir. Bulut içi Ichimoku mesafesi sıfırdır; geçmişe çizilen Chikou
gelecek değer olarak kullanılmaz.

## Gerçek testler ve kontrol

- Her varlık için günlük ve haftalık kuyruklar: her ziyarette zaman bütçesi
  uygunsa en fazla bir planlanmış deneme/zaman dilimi; kuyruk kalıcıdır.
- İlk rotasyon tüm ailelerin varsayılan ayarını tek başına ve istenen yedi
  indikatörü aynı anda çoğunluk/AND/OR ile sınar. Diğer birleşimler 2–7
  göstergeden oluşur. Sonraki rotasyonlar farklı ayarlar, long/short/iki yön,
  sıralı ve ağırlıklı oylama, teyit, pozisyon boyutu, ATR stop/TP/trailing,
  kademeli giriş ve kısmi çıkış seçeneklerini kullanır.
- Tekrarlanabilir, tohumlu, sınırlı örnekleme; tüm matematiksel ayarların
  veya tüm kombinasyonların eksiksiz tarandığı iddia edilmez.
- Keşif ve ayrı kronolojik tarihsel doğrulama, sonraki açılışta işlem,
  Foundry masraf ve stop/TP modeli, azami 1x brüt pozisyon. Short ödünç
  maliyeti varsayımdır. En az 120 kullanılabilir bar istenir; bekleyen
  geçmiş gereksinimleri zaman dilimi başına en fazla 12 kayıtla sınırlıdır.
  Geçmiş, tanımlı sinyal veya süre yetersizliği deneme sayısına girmez.
- Gerçek tamamlanan denemeler, kaynak ve kod kanıtı, iki net getiri yolu,
  farklı strateji kimliği ve sonuçlarıyla mevcut SQLite/arşiv akışına yazılır.
  Mevcut Superlab araştırma makbuzu her yeni kaydı inceleyebilir; bu, bağımsız
  OHLCV backtesti, ACCEPT, PAPER onayı veya yeni PBO/DSR kohortu değildir.
- Streamlit, yeni denemeleri gösterge ve zaman dilimi bazında sayar; çoklu
  göstergeli denemeler ayrı birleşim kategorisindedir. Arşivlenmiş kimlikler
  sayılır. Mevcut log/Telegram davranışı değiştirilmez.

Günlük/haftalık fiyatlar mevcut veri sağlayıcılarından gelir. Daha kısa zaman
dilimleri ve yeni borsalar bu değişikliğin kapsamı değildir. Tarihsel sonuçlar
gelecekte kârlılık kanıtı değildir.
