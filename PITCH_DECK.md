# HushBoard — 5 slaytta anlatım

Tarayıcı sunumu: [`/static/deck.html`](http://127.0.0.1:4173/static/deck.html)

## 1 · Hesap açmadan söyle

İnsanlar hassas bir konuda yeni bir profil açmak istemeyebilir. Tamamen sürtünmesiz bir form
ise seri spam'i kolaylaştırır. HushBoard, hesap istemeyen geri bildirime küçük ve iade
edilebilir bir testnet teminatı ekler.

> “Bir sorunu söylemek için yeni bir kimlik kurmak zorunda olmamalıyım.”

## 2 · Tek fikir, bir teminat, net sonuç

1. Mesajı ve testnet iade adresini yaz.
2. `0,01 TAZ` teminatı gönder.
3. Operatör mesajı incelesin.
4. Meşruysa `0,01 TAZ` ana parayı geri al.

Kararı moderatör verir. Spam veya kötüye kullanımda teminat operatör cüzdanında kalır.
İlk gönderimin ağ ücreti geri gelmez.

## 3 · Shielded ödeme, sınırlı gizlilik

Kamusal zincir ödeme adresini, tutarı ve memoyu açıkça göstermez. HushBoard ise mesajı,
iade adresini, zamanı ve bağlantı verisini bilir. Bu yüzden “tam anonim” demiyoruz.

## 4 · Teminat zincirde, karar ekranda

Canlı demoda önceden onaylanmış ve henüz kullanılmamış sahne kaydını açarız. Doğrulayıcı;
yerel Zallet makbuzundaki alıcıyı, tam `1.000.000 zat` tutarı ve `HB1:<id>` memosunu
kontrol eder. Explorer yalnız işlemin zincire dahil edildiğini gösterir; shielded alanların
içeriğini göstermez.

İade düğmesine yalnız gerçek sunumda bir kez basılır. Txid oluşmadıysa “hazırlanıyor”, txid
oluştuysa “gönderildi, onay bekliyor” denir. Sahnede yeni blok beklenmez.

## 5 · Hesap yok, sihirli vaat yok

**Yaptık:** Gerçek Zcash testnetinde shielded teminat, eşleştirme, merkezî karar ve iade
akışını uçtan uca çalıştırdık.

**İddia etmiyoruz:** Sistem trustless değil, tam anonim değil, production'a hazır değil ve
spam'i tek başına bitirmiyor. TAZ'ın maddi değeri yok.

**Tek cümle:** “Hesap açmadan yaz; meşruysa testnet teminatını geri al.”

Konuşma metni ve güvenli tıklama sırası: [`SUNUM.md`](SUNUM.md).
