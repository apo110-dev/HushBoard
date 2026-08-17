#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIDEO="$ROOT/generated/v2/silent"
VOICE="$ROOT/generated/v2/voice-synced"
MUSIC="$ROOT/generated/v2/music/music.m4a"
SFX="$ROOT/generated/v2/sfx"
OUT="$ROOT/generated/v2/final"
mkdir -p "$OUT"

for n in 1 2 3; do
  id="$(printf '%02d' "$n")"; offset="$(( (n-1)*15 ))"
  for f in "$VIDEO/$id.mp4" "$VOICE/$id.wav" "$MUSIC" "$SFX/$id.wav"; do
    [[ -f "$f" ]] || { echo "Eksik stem: $f" >&2; exit 1; }
  done
  filter="[1:a]aresample=48000,asetpts=N/SR/TB,asplit=2[vo_sc][vo_mix];[2:a]loudnorm=I=-27:TP=-4:LRA=8,aresample=48000,asetpts=N/SR/TB[mus];[mus][vo_sc]sidechaincompress=threshold=0.028:ratio=9:attack=18:release=280[duck];[3:a]aresample=48000,volume=0.55,asetpts=N/SR/TB[sfx];[vo_mix][duck][sfx]amix=inputs=3:weights='1 0.55 0.62':normalize=0,alimiter=limit=0.93:attack=5:release=40,volume=0.89,apad,atrim=end_sample=720000,asetpts=N/SR/TB[a]"
  ffmpeg -hide_banner -loglevel error -y -i "$VIDEO/$id.mp4" -i "$VOICE/$id.wav" \
    -ss "$offset" -t 15 -i "$MUSIC" -i "$SFX/$id.wav" -filter_complex "$filter" \
    -map 0:v:0 -map '[a]' -c:v copy -c:a aac -b:a 256k -ar 48000 -t 15 \
    -movflags +faststart "$OUT/$id.mp4"
done

cat > "$OUT/clips.txt" <<EOF
file '01.mp4'
file '02.mp4'
file '03.mp4'
EOF
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$OUT/clips.txt" \
  -c copy -movflags +faststart "$OUT/HushBoard-V2-45s.mp4"

# Opsiyonel altyazılı sürüm; ana master altyazısızdır.
ffmpeg -hide_banner -loglevel error -y -i "$OUT/HushBoard-V2-45s.mp4" \
  -vf "subtitles='$ROOT/voice/HushBoard-45s.srt':fontsdir='$ROOT/source':force_style='FontName=IBM Plex Sans,FontSize=27,PrimaryColour=&H00FFFFFF,OutlineColour=&HCC17211B,BackColour=&HCC17211B,BorderStyle=3,Outline=1,Shadow=0,MarginV=42,Alignment=2'" \
  -c:v libx264 -preset fast -crf 17 -pix_fmt yuv420p -c:a copy -movflags +faststart \
  "$OUT/HushBoard-V2-45s-subtitled.mp4"

"$ROOT/tools/verify-v2.sh"
