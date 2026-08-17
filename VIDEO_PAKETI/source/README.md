# Motion source

- `render_promo.py`: ilk deterministik motion animatic kaynağı.
- `build_hybrid_v2.py`: sessiz Kling B-roll ile gerçek UI/evidence katmanlarını 3×15 saniyelik
  hibrit V2'de birleştirir.
- `make_v2_sfx.py`: her hard cut için ayrı stereo paper-hit/UI-tick stem'i üretir.
- `03-closing.html`: kapanış frame kaynağı.

Video modeli okunabilir HushBoard metni üretmez. `build_hybrid_v2.py`, gerçek ekran PNG'lerini
post-prod'da ekler ve tüm V2 klipleri 1920×1080/30 fps/tam 15 saniye olarak normalize eder.

`literata.ttf` ve `plex.ttf`, Pillow/FFmpeg renderer için Google Fonts variable TTF dosyalarıdır.
Lisanslar [`../../static/fonts/LITERATA-OFL.txt`](../../static/fonts/LITERATA-OFL.txt) ve
[`../../static/fonts/IBM-PLEX-SANS-OFL.txt`](../../static/fonts/IBM-PLEX-SANS-OFL.txt)
dosyalarındadır.
