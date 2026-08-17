#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/generated/v2/voice-raw"
OUT="$ROOT/generated/v2/voice-synced"
mkdir -p "$OUT"

command -v ffmpeg >/dev/null || { echo "ffmpeg bulunamadı" >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "ffprobe bulunamadı" >&2; exit 1; }

# Ham konuşma süresi ve klip başındaki kasıtlı sessizlik.
ids=(01 02 03)
targets=(14.20 14.20 14.20)
delays=(300 300 300)

for n in "${!ids[@]}"; do
  id="${ids[$n]}"
  input=""
  for ext in wav flac mp3 m4a; do
    [[ -f "$RAW/$id.$ext" ]] && input="$RAW/$id.$ext" && break
  done
  [[ -n "$input" ]] || { echo "Eksik voice: $RAW/$id.(wav|flac|mp3|m4a)" >&2; exit 1; }

  duration="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$input")"
  target="${targets[$n]}"
  tempo="$(python3 - "$duration" "$target" <<'PY'
import sys
source=float(sys.argv[1]); target=float(sys.argv[2])
factor=source/target
if not 0.5 <= factor <= 2.0:
    raise SystemExit(f"atempo sınırı dışında: {factor:.4f}")
print(f"{factor:.8f}")
PY
)"

  echo "[$id] raw=${duration}s target=${target}s atempo=$tempo delay=${delays[$n]}ms"
  ffmpeg -hide_banner -loglevel error -y -i "$input" -vn \
    -af "atempo=$tempo,loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000,adelay=${delays[$n]}:all=1,apad,atrim=end_sample=720000,asetpts=N/SR/TB" \
    -ar 48000 -ac 2 -c:a pcm_s24le "$OUT/$id.wav"

  final="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/$id.wav")"
  python3 - "$final" "$id" <<'PY'
import sys
d=float(sys.argv[1])
if not 14.99 <= d <= 15.01:
    raise SystemExit(f"{sys.argv[2]} süresi 15 sn değil: {d:.6f}")
PY
done

echo "Voice senkronu hazır: $OUT (3 × 15,00 sn)"
