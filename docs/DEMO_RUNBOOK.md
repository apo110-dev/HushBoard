# Demo runbook

## T-24 saat — hazırlık

- `docker ps` ile Zebra + iki Zallet'i kontrol et; cold sync'i bitir.
- `./scripts/preflight.sh` çalıştır.
- Operatör ve katılımcı cüzdanlarının güncel shielded bakiyelerini doğrula.
- Bakiye yetersizse TAZ'ı önceden <https://zcashfaucet.jinolabs.xyz/> üzerinden al; faucet'i sahne gününe bırakma.
- Yıkıcı, baştan sona refund provasını yalnız harcanabilir ayrı bir vaka ile tamamla.
- Ardından sahne için yeni bir inbound bond'u confirmation eşiğine getir; SQLite watcher ile
  receiver, amount, memo ve txid eşleşmesini doğrula.
- Sahne vakası `moderation` durumundayken `python3 scripts/capture-stage-fixture.py <public_id>`
  ve ardından `python3 scripts/verify-stage-fixture.py` çalıştır. Manifest one-shot'tır;
  capture'dan sonra bu vakanın refund/keep düğmesine provada basma.
- Uygulama bağımlılıklarını ve container imajlarını önceden indir; sahnede pull/build yapma.

## T-10 dakika

1. Güç adaptörü ve interneti kontrol et; bildirimleri kapat.
2. Ekran ölçeğini 100%, tarayıcı zoom'u 90–100% yap.
3. Bir terminalde `./START_DEMO.sh` çalıştır; ikinci terminalde `./scripts/preflight.sh`
   ve `python3 scripts/verify-stage-fixture.py` çalıştır. İkisi de başarılı değilse canlı
   ana akışa girme.
4. Health rozetinin `GERÇEK TESTNET · CANLI` olduğunu gör; bunun yalnız health kanıtı
   olduğunu, fixture kanıtının verifier olduğunu unutma.
5. Sağ üstten **Operatör paneline** geç; hazır vakanın hâlâ `moderation`
   durumunda, karar/refund alanlarının boş olduğunu kontrol et.
6. Explorer dış bağımlılıktır; golden path'te açma.

## Sahne sırası (75–85 sn)

- 0–8: sade katılımcı ekranı + problem + live health.
- 8–12: **Operatör paneline** geç.
- 12–24: verifier'dan geçmiş, önceden onaylanmış gerçek başvuruyu aç.
- 24–38: `Bond n/1 onay` sayacını göster; tutar/memo/alıcı/mined-height eşleşmesinin
  verifier tarafından yerel receipt'ten kontrol edildiğini söyle.
- 38–54: **Meşru · 0,01 TAZ iade et** kararını ver; gerçek-send onayını bir kez kabul et.
- 54–68: txid yoksa “henüz yayınlanmadı”; txid gelirse “yayınlandı, onay bekliyor” de.
- 68–85: **Karar ve saklama bizde** + **Tam anonimlik yok** sınırı ve kapanış.

## Fail-safe cümleleri

- **Node gecikiyorsa:** “Canlı bağlantı şu an geride; bunu başarılı göstermiyorum. Canlı akışı
  durdurup zaman/height damgalı kanıt slaydıyla devam ediyorum.”
- **Kanıt üretimi uzarsa:** “Zallet kanıtı oluşturuyor; txid olmadığı için henüz broadcast demiyorum.”
- **Txid gelip blok gelmezse:** “İşlem testnete yayınlandı ve onay bekliyor; onay uydurmuyoruz.”
- **Explorer açılmazsa:** “Explorer üçüncü taraf. Yerel Zebra ve wallet receipt bizim kanıtımız.”

## Asla yapma

- Testnet confirmation'ı CSS timer ile taklit etme.
- `refund_broadcast` durumuna gerçek txid olmadan geçme.
- `docker compose down -v` veya wallet volume silme.
- Aynı confirmed fixture'ı ikinci kez refund etme; provada refund/keep/reset'e basma.
- “Tüm maliyet iade” deme: yalnız `0,01 TAZ` teminat anaparası iade edilir, ağ ücreti değil.
- `FullPrivacy` hatasında sessizce daha gevşek gizlilik politikasına düşme.
- Cookie, seed, full refund UA veya raw RPC hatasını ekranda gösterme.

## Offline fallback

Offline replay sahneye çıkmadan `HUSHBOARD_MODE=mock ./START_DEMO.sh` ile seçilir ve yalnız
açık sarı `OFFLINE REPLAY · NO LIVE SENDS` banner'ıyla kullanılır.
Snapshot'ın `captured_at`, testnet height ve kanıt sınıfları görünür. Her satır
`GERÇEK KANIT · CONFIRMED …` veya `SENTETİK · WALKTHROUGH` diye ayrılır. Ödeme,
moderation, refund ve reset kontrolleri kapalıdır. Ayrı disposable SQLite kopyası
kullanılır; wallet RPC ve explorer linki yoktur.
Replay'i yeni live transaction gibi anlatma.
