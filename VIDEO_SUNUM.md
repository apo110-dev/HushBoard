# HushBoard V2 — 45 saniyelik tanıtım videosu

Yeniden üretim paketi: [`VIDEO_PAKETI/`](VIDEO_PAKETI/README.md)
Yönetmen planı: [`VIDEO_PAKETI/STORYBOARD.md`](VIDEO_PAKETI/STORYBOARD.md)

## Yerel çıktı (Git'e dahil değildir)

Ara ve final render'lar bilerek `VIDEO_PAKETI/generated/` altında ignore edilir. Üretim akışı
başarıyla tamamlandığında beklenen ana çıktılar:

```text
VIDEO_PAKETI/generated/v2/final/HushBoard-V2-45s.mp4
VIDEO_PAKETI/generated/v2/final/HushBoard-V2-45s-subtitled.mp4
```

## Format

- Üç ayrı `15 saniye` klip
- `1920×1080`, 16:9, 30 fps
- Toplam yaklaşık `45,02 saniye`
- Türkçe voice ayrı Higgsfield Seed Audio üretimi
- Kling B-roll job'ları tamamen sessiz; finalde Kling sesi yok
- Müzik ve sound design ayrı stem'ler olarak düşük seviyede mikslenir

## V2 yaklaşımı

Kling yalnız yazısız, gerçekçi fiziksel B-roll üretir: hesap formu yığını, mesaj kartları,
tek doğrulama rail'i, boş receipt, damga ve sarı kapanış kâğıdı. Gerçek HushBoard UI ve tüm
okunabilir metinler deterministik olarak post-prod'da eklenir.

Planlar 1,4–3 saniye aralığındadır. Hard cut, paper hit ve UI tick kullanılır; neon, gradient,
hologram, coin, glassmorphism, dağıtık node grafiği veya AI üretimi floating UI kullanılmaz.

## Hikâye

1. **00:00–00:15:** Hesap duvarı ve istenmeyen mesaj problemi → HushBoard katılımcı ekranı.
2. **00:15–00:30:** Mesaj → ayrı iade adresi → `0,01 TAZ` → miktar/HB1 eşleşmesi.
3. **00:30–00:45:** Merkezî karar/custody → ana para/ağ ücreti ayrımı → gizlilik sınırı → kapanış.

## Doğrulama

`tools/verify-v2.sh` şu kontrolleri otomatik yapar:

- Kling B-roll'larda audio stream bulunmaması
- Üç final klibin tam 15 saniye olması
- Master'ın 1080p/30 fps ve yaklaşık 45,02 saniye olması
- 48 kHz stereo audio
- EBU R128 loudness raporu
- 5×6 final contact sheet

Stage fixture `#JYNG·ZTSX` yalnız statik evidence olarak kullanıldı; refund, keep veya reset
işlemi uygulanmadı.
