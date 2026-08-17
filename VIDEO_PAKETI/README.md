# HushBoard · 3×15 saniyelik hibrit tanıtım paketi

## Nihai teslim

```text
generated/v2/final/01.mp4
                    02.mp4
                    03.mp4
generated/v2/final/HushBoard-V2-45s.mp4
generated/v2/final/HushBoard-V2-45s-subtitled.mp4
generated/v2/final/CONTACT-SHEET.jpg
generated/v2/final/LOUDNESS.txt
```

Ana master:

- `1920×1080`, 16:9, 30 fps
- üç ayrı tam 15 saniyelik klip; master yaklaşık 45,02 saniye
- H.264 High / `yuv420p`
- Türkçe Higgsfield Seed Audio voice: `Raina` preset
- ayrı Sonilo müzik bed'i ve ayrı deterministik SFX stem'leri
- 48 kHz AAC stereo
- doğrulanan mix: yaklaşık `-15,8 LUFS`, true peak `-2,5 dBFS`

`generated/` Git tarafından bilinçli olarak yok sayılır; final medya yerel teslimdir.

## V2 yöntemi

Video modeli yalnız gerçekçi hareket/B-roll üretir. Kling 3.0 Pro job'larının tamamı
`sound=off` ile üretildi ve hiçbirinde audio stream yoktur. AI modeline okunabilir UI, marka
metni veya testnet evidence çizdirilmez.

Gerçek HushBoard katılımcı ve operatör ekranları deterministik post-prod ile eklenir. Bir sahnede
gerçek participant UI, Kling'in boş laptop ekranına ayrıca composite edilir; Kling bu metni
üretmez. Tüm okunabilir başlıklar, sayılar ve kapanış grafikleri proje fontlarıyla render edilir.

Detaylı senaryo: [`STORYBOARD.md`](STORYBOARD.md)

## Görsel kurallar

- Ortalama 1,4–3 saniyelik planlar ve kısa hard cut'lar
- Doğal ışık, nötr kâğıt, ink, sarı ve teal
- Gerçek UI yalnız okunur crop veya screen composite olarak kullanılır
- Neon, gradient, hologram, kripto coin, glassmorphism ve dağıtık node grafiği yok
- AI üretimi okunabilir yazı ve floating UI yok

## Kaynak yapısı

```text
frames/                    gerçek 1920×1080 participant/operator kareleri
prompts/V2-*.txt           sessiz Kling B-roll promptları
source/build_hybrid_v2.py  3×15 sn deterministik hibrit edit
source/make_v2_sfx.py      ayrı paper-hit / UI-tick stem'leri
voice/                     ayrı Türkçe voice metinleri, timeline ve SRT
tools/sync-voice.sh        ham TTS'yi 3×15 sn stem'e oturtur
tools/mix-v2.sh            voice + düşük music bed + SFX + videoyu birleştirir
tools/verify-v2.sh         ffprobe, loudness ve contact-sheet doğrulaması
```

## Tekrar üretim

Higgsfield'dan sessiz B-roll ve ayrı voice/music dosyaları indirildikten sonra:

```bash
uv run python source/build_hybrid_v2.py
uv run python source/make_v2_sfx.py
./tools/sync-voice.sh
./tools/mix-v2.sh
```

Son komut otomatik olarak:

1. Üç voice stem'ini ilgili sessiz videoya ekler.
2. Müzik bed'ini voice altında duck eder.
3. Ayrı sound-design stem'ini düşük seviyede ekler.
4. 45 saniyelik master ve opsiyonel altyazılı sürümü üretir.
5. `ffprobe`, EBU R128 loudness ve 5×6 contact sheet doğrulamasını çalıştırır.

## Önceki denemeler

İlk Kling start/end zoom'ları ve ilk motion animatic nihai teslim değildir. Yerel olarak
`generated/rejected-kling-static/` ve `generated/promo-final/` altında korunur. V2 bunları
teslim adı olarak kullanmaz.

## Güvenlik ve doğruluk

- `#JYNG·ZTSX` fixture'ına refund, keep veya reset uygulanmadı.
- Full Unified Address, seed, cookie, wallet kimliği ve raw RPC gösterilmez.
- `0,01 TAZ` yalnız testnet teminatıdır; maddi değeri yoktur.
- İade ana parayı kapsar; ilk ağ ücretini kapsamaz.
- Saklama ve moderasyon kararı merkezî operatördedir.
- Tam anonimlik veya trustless escrow iddiası yoktur.
