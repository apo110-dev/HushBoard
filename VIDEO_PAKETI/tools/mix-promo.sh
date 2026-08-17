#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SILENT="$ROOT/generated/promo-silent"
VOICE="$ROOT/generated/promo-voice"
MUSIC="$ROOT/generated/promo-music-raw/music.m4a"
OUT="$ROOT/generated/promo-final"
mkdir -p "$OUT"

for n in 1 2 3; do
  id="$(printf '%02d' "$n")"
  offset="$(( (n-1)*15 ))"
  [[ -f "$SILENT/$id.mp4" && -f "$VOICE/$id.wav" && -f "$MUSIC" ]] || {
    echo "Eksik stem: $id" >&2; exit 1;
  }
  filter="[1:a]loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000,asetpts=N/SR/TB,asplit=2[vo_sc][vo_mix];[2:a]loudnorm=I=-25:TP=-3:LRA=7,aresample=48000,asetpts=N/SR/TB[mus];[mus][vo_sc]sidechaincompress=threshold=0.035:ratio=8:attack=20:release=300[duck];[vo_mix][duck]amix=inputs=2:weights='1 0.55':normalize=0,apad,atrim=end_sample=720000,asetpts=N/SR/TB[a]"
  ffmpeg -hide_banner -loglevel error -y \
    -i "$SILENT/$id.mp4" -i "$VOICE/$id.wav" -ss "$offset" -t 15 -i "$MUSIC" \
    -filter_complex "$filter" -map 0:v:0 -map '[a]' -c:v copy \
    -c:a aac -b:a 256k -ar 48000 -t 15 -movflags +faststart "$OUT/$id.mp4"
done

cat > "$OUT/clips.txt" <<EOF
file '01.mp4'
file '02.mp4'
file '03.mp4'
EOF
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$OUT/clips.txt" \
  -c copy -movflags +faststart "$OUT/HushBoard-45s.mp4"


# Opsiyonel, erişilebilir altyazılı teslim.
ffmpeg -hide_banner -loglevel error -y -i "$OUT/HushBoard-45s.mp4" \
  -vf "subtitles='$ROOT/voice/HushBoard-45s.srt':fontsdir='$ROOT/source':force_style='FontName=IBM Plex Sans,FontSize=27,PrimaryColour=&H00FFFFFF,OutlineColour=&HCC17211B,BackColour=&HCC17211B,BorderStyle=3,Outline=1,Shadow=0,MarginV=42,Alignment=2'" \
  -c:v libx264 -preset fast -crf 17 -pix_fmt yuv420p -c:a copy -movflags +faststart \
  "$OUT/HushBoard-45s-subtitled.mp4"

ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,duration \
  -show_entries format=duration -of json "$OUT/HushBoard-45s.mp4"
