# HushBoard V2 — 45 saniyelik hibrit tanıtım

## Yönetmen ilkesi

Video modeli yalnız **sessiz, gerçekçi hareket ve B-roll** üretir. Okunabilir HushBoard metni,
UI ve testnet evidence daima gerçek ekranlardan deterministik post-prod ile eklenir. Ortalama
plan uzunluğu 1,4–3 saniyedir; hard cut ve fiziksel paper-hit sesleri kullanılır.

Görsel sınırlar: doğal ışık, nötr sıcak kâğıt, ink, sarı ve teal. Neon, gradient, hologram,
kripto coin, glassmorphism, dağıtık node diyagramı ve AI üretimi floating UI yoktur.

## Film 01 · Problemden ürüne · 00:00–00:15

| Lokal | Kaynak | Beat |
|---|---|---|
| 00:00–00:02.00 | Kling B-roll | Hesap formları fiziksel bir duvar gibi yükselir |
| 00:02–00:03.50 | Deterministik type | `FİKRİNİ SÖYLE. HESAP AÇMA.` |
| 00:03.50–00:05.50 | Kling B-roll | İstenmeyen mesaj kâğıtları masayı doldurur |
| 00:05.50–00:06.70 | Deterministik type | `BEDAVA SPAM.` problem beat'i |
| 00:06.70–00:08.70 | Kling B-roll | Tek sarı kart kalabalığın içinden yol açar |
| 00:08.70–00:11.20 | Gerçek UI | Katılımcı ekranı okunur ürün kanıtı olarak girer |
| 00:11.20–00:13.20 | Hibrit composite | Gerçek HushBoard ekranı Kling laptop ekranına post-prod eklenir |
| 00:13.20–00:15.00 | Gerçek UI | `0,01 TAZ` ve iade edilebilir teminat callout'u |

**Türkçe voice**

> En dürüst yorumlar hesap duvarında kayboluyor. Hiç sürtünme olmayınca istenmeyen mesajlar
> çoğalıyor. HushBoard'da başka bir yol var. Fikrini söyle. Hesap açma.

## Film 02 · Mekanizma · 00:15–00:30

| Lokal | Kaynak | Beat |
|---|---|---|
| 00:00–00:01.40 | Gerçek UI | Mesaj alanı |
| 00:01.40–00:03.00 | Kling B-roll | Mesaj kartı masaya iner |
| 00:03.00–00:04.80 | Gerçek UI | Ayrı ve maskeli iade adresi |
| 00:04.80–00:06.20 | Kling B-roll | Teal iade etiketi ayrı konumlanır |
| 00:06.20–00:07.80 | Deterministik type | `0,01 TAZ GÖNDER.` |
| 00:07.80–00:10.80 | Kling B-roll | Tek rail üzerinde fiziksel doğrulama kapıları |
| 00:10.80–00:12.70 | Deterministik evidence | `1.000.000 zat + HB1 memo + 1/1` |
| 00:12.70–00:15.00 | Gerçek UI | Operatör ekranında `ZİNCİRDE ONAYLI` |

**Türkçe voice**

> Mesajını yaz. İade adresini ayrı ver. Sıfır virgül sıfır bir TAZ gönder. Cüzdan, bir milyon
> zat ile HB1 memosunu zincirde eşleştirir.

## Film 03 · İade, sınır ve kapanış · 00:30–00:45

| Lokal | Kaynak | Beat |
|---|---|---|
| 00:00–00:02.20 | Gerçek UI | Merkezî operatör ve custody bandı |
| 00:02.20–00:04.40 | Kling B-roll | Boş receipt fiziksel olarak çıkar |
| 00:04.40–00:05.70 | Deterministik evidence | Ana para iadesi |
| 00:05.70–00:07.50 | Kling B-roll | Teal doğrulama damgası |
| 00:07.50–00:09.00 | Deterministik evidence | İlk ağ ücretinin iade edilmediği ayrım |
| 00:09.00–00:11.00 | Deterministik type | `TAM ANONİMLİK VAADİ YOK.` |
| 00:11.00–00:12.80 | Kling B-roll | Ink/teal paneller kapanıp sarı karta açılır |
| 00:12.80–00:15.00 | Post-prod closing | `HESAP YOK. SİHİRLİ VAAT YOK. ÇALIŞAN AKIŞ VAR.` |

**Türkçe voice**

> Karar ve saklama operatörde. Meşru mesajda ana para geri döner. İlk gönderimdeki ağ ücreti
> geri dönmez. Tam anonimlik vaadi yok. Çalışan, doğrulanmış bir Zcash testnet akışı var.

## Ses mimarisi

- Kling B-roll: `sound=off`, audio stream yok.
- Voice: Higgsfield Seed Audio, preset `Raina`, üç ayrı WAV.
- Müzik: Sonilo, 132 BPM, voice altında yaklaşık `-27 LUFS` bed.
- SFX: her hard cut'ta düşük seviyeli paper hit / UI tick; ayrı stereo WAV stem.
- Final hedef: yaklaşık `-16 LUFS`, true peak `-1,5 dBFS`.
