# HushBoard — sahnede böyle anlat

Video teslimi için ana plan [`VIDEO_SUNUM.md`](VIDEO_SUNUM.md); bu dosya canlı anlatım veya yedek akış içindir.

Bunu kelimesi kelimesine ezberlemeye çalışma. Akışı öğren; kendi sesinle anlat.

## Sahneden önce üç kontrol

1. `./scripts/preflight.sh` sonucu **0 kritik hata** olmalı.
2. `python3 scripts/verify-stage-fixture.py` sonunda **stage fixture OK** yazmalı.
3. `#JYNG·ZTSX` satırındaki iade/ret düğmelerine provada **basma**. Bu gerçek ve tek
   kullanımlık sahne vakası.

Yeşil `GERÇEK TESTNET · CANLI` rozeti sistemin hazır olduğunu gösterir. Hazır teminatın kanıtı doğrulama komutudur.

## 60–75 saniyelik ana metin

Köşeli parantezler senin hareketin; sesli okuma.

> Şöyle bir problem var: İnsanlardan hesap açmalarını isteyince en dürüst yorumları
> kaçırıyoruz. Hiç sürtünme koymayınca da spam atmak bedava.
>
> HushBoard ortayı bulmaya çalışıyor: hesap yok; küçük, iade edilebilir bir teminat var.
> Buradaki 0,01 TAZ gerçek Zcash testnetinde kullanılıyor. TAZ test parası; maddi değeri yok.
>
> **[Operatör paneline geç; hazır bildirimi aç.]** Bu bildirimin teminatı sahneden önce
> testnette onaylandı. Bunu, doğrulama komutu Zallet kaydındaki alıcıyı, tam 0,01 TAZ'ı,
> `HB1` kodunu ve işlemin zincire girdiğini kontrol ettiği için söylüyorum.
>
> **[Meşru'ya bas ve tarayıcı uyarısını bir kez onayla.]** Bildirim meşru; iadeyi tek sefer
> başlatıyorum.
>
> **[Ekrandaki duruma uyan tek cümleyi söyle.]** Geri giden, 0,01 TAZ teminatın kendisi;
> kullanıcının ilk gönderimde ödediği ağ ücreti değil.
>
> **[Karar ve saklama operatörde bölümünü aç.]** Bir de net olalım: Kararı biz veriyoruz,
> teminat operatör cüzdanında duruyor. Yani sistem merkezî; saklama da operatörde. Ödeme
> shielded ama HushBoard mesajı, iade adresini ve zamanı biliyor. Tam anonimlik vaat etmiyoruz.
>
> Bugün kanıtladığımız şey spam'in bittiği değil; bu akışın gerçek testnette çalıştığı.

## Tıklama sırası

1. `http://127.0.0.1:4173/` — formu göster, doldurma.
2. Sağ üstten **Operatör paneli →** seç.
3. **#JYNG·ZTSX** / “Gece yürüyüş yolundaki iki lamba…” satırını aç.
4. `Zincirde onaylı · Moderasyonda` ve `Bond n/1 onay` alanlarını göster.
5. Yalnız gerçek sunumda **Meşru / 0,01 TAZ iade et** düğmesine bir kez bas.
6. Tarayıcı uyarısını okuyup `Tamam/OK` seç. Bir daha basma.
7. Durum cümlesini söyle, ardından **Karar ve saklama operatörde** bölümünü aç.

Üst çubuktaki dönen-ok simgesi reset açar. Sunumda ona dokunma.

## İadeden sonra hangi cümleyi söyleyeceğim?

- **Txid yok / `Henüz yayınlanmadı`:**
  “Zallet işlemi hazırlıyor; henüz testnete gönderildi demiyorum.”
- **Txid var / `Onay bekliyor`:**
  “İade testnete gönderildi; şu an onay bekliyor.”
- **`Zincirde onaylandı`:**
  “İade artık zincirde onaylandı.”

Yeni blok bekleme. Hazırlama uzarsa gizlilik sınırını anlatıp bitir.

## Bu laptopta ne lazım?

- İnternet ve güç adaptörü
- Docker + Compose, Git, `curl`, Python 3.11+ ve `uv`
- Önceden senkron olmuş Zebra testnet düğümü
- Çalışan iki Zallet testnet cüzdanı
- İşlem ve ağ ücretine yetecek kadar TAZ
- `.runtime/` içindeki yerel wallet dosyaları — bunlar GitHub'a gitmez

Birinci terminal:

```bash
cd HushBoard
./START_DEMO.sh
```

İkinci terminal:

```bash
cd HushBoard
./scripts/preflight.sh
python3 scripts/verify-stage-fixture.py
```

Yeni bir bilgisayarda Zebra'nın ilk senkronu saatler sürebilir. “Tek komut” sözü yalnız
önceden hazırlanmış ve senkron laptop için geçerli.

## Jüri sorarsa kısa cevaplar

**“Bu anonim mi?”**
Tam anonim değil. Zcash ödeme ayrıntılarını kamusal zincirde açık göstermez; HushBoard ise
mesajı, zamanı ve iade adresini bilir.

**“Parayı siz mi tutuyorsunuz?”**
Evet. Bu MVP'de cüzdan ve karar bizde; yani sistem merkezî ve custodial. Trustless escrow
iddiamız yok.

**“Mesaj zincirde mi?”**
Hayır. Mesaj yerel veritabanında. Zincirdeki encrypted memo yalnız `HB1:<id>` eşleştirme
kodunu taşıyor.

**“Explorer tutarı ve memoyu gösteriyor mu?”**
Hayır. Explorer yalnız işlemin zincire girdiğini gösterir. Tutar, memo ve alıcı eşleşmesi
Zallet'in yerel cüzdan kaydından gelir.

**“Tam iade mi?”**
`0,01 TAZ` teminat anaparası geri gider. Kullanıcının ilk gönderimde ödediği ağ ücreti geri
gelmez.

**“TAZ değersizse spam nasıl pahalı oluyor?”**
Bu demo ekonomik sonucu değil, mekanizmanın teknik olarak çalıştığını gösteriyor. Gerçek
kullanımda varlık ve tutar ölçülerek belirlenmeli.

**“Spam tamamen biter mi?”**
Hayır. Amacımız spam'i sihirli biçimde bitirmek değil; seri spam'e ayarlanabilir bir maliyet
eklemek.

**“Uygulama kapanırsa iki kez iade yollar mı?”**
Zallet işlem kimliği ve txid veritabanına yazılıyor. Yeniden başlayınca mevcut işlem bulunup devam
ediliyor; otomatik ikinci gönderim yapılmıyor.

**“Production'a hazır mı?”**
Hayır. Bu testnet ve hackathon MVP'si; gerçek ZEC veya gerçek kullanıcılar için hazır değil.

## Bunları söyleme

- “Mesajı yayınladık.” → **“Bildirimi meşru işaretleyip iadeyi başlattık.”**
- “Tam anonim / izlenemez.” → **“Ödeme ayrıntıları kamusal zincirde açık değil.”**
- “Trustless escrow / smart contract.” → **“Karar ve cüzdan operatörde.”**
- “Spam'i çözdük.” → **“Spam'e maliyet ekleyen bir mekanizma kurduk.”**
- “Tüm maliyet geri geliyor.” → **“0,01 TAZ teminat geri; ağ ücreti hariç.”**
- Pending iken “iade edildi.” → **“Gönderildi, onay bekliyor.”**
- “Explorer tutarı ve memoyu kanıtlıyor.” → **“Explorer yalnız işlemin zincire girdiğini gösteriyor.”**

## Bir şey bozulursa

- Health kırmızıysa: **“Canlı bağlantı şu an sağlıklı değil; başarılıymış gibi göstermiyorum.”**
- Txid yoksa: **“İşlem hazırlanıyor; henüz gönderildi demiyorum.”**
- Txid var ama blok yoksa: **“Testnete gönderildi; onay bekliyor.”**
- Explorer açılmazsa: **“Explorer dış servis; asıl eşleşme yerel cüzdan kaydında.”**

Offline sunum gerekiyorsa bunu sahneye çıkmadan seç:

```bash
HUSHBOARD_MODE=mock ./START_DEMO.sh
```

Ekranda `OFFLINE REPLAY · NO LIVE SENDS` yazmıyorsa offline capture diye anlatma.

## Panik anında dört cümle

1. HushBoard hesap istemeyen geri bildirime `0,01 TAZ` iade edilebilir teminat ekliyor.
2. Bu bond gerçek testnette önceden onaylandı; yeni iadeyi onay beklerken öyle gösteriyoruz.
3. Kararı moderatör veriyor; sistem merkezî ve tam anonim değil.
4. TAZ'ın gerçek değeri yok; demo mekanizmanın çalıştığını gösteriyor.
