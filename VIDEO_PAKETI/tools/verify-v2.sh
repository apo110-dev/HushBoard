#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export HUSHBOARD_VIDEO_ROOT="$ROOT"
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
root=Path(os.environ['HUSHBOARD_VIDEO_ROOT']); gen=root/'generated'/'v2'
def probe(p):
 out=subprocess.run(['ffprobe','-v','error','-show_entries','stream=index,codec_name,codec_type,profile,width,height,pix_fmt,r_frame_rate,sample_rate,channels,duration:format=duration','-of','json',str(p)],text=True,capture_output=True,check=True).stdout
 return json.loads(out)
def video_stream(d):return next(s for s in d['streams'] if s['codec_type']=='video')
def audio_streams(d):return [s for s in d['streams'] if s['codec_type']=='audio']
for p in sorted((gen/'broll').glob('*.mp4')):
 d=probe(p);v=video_stream(d);assert (v['width'],v['height'])==(1920,1080);assert not audio_streams(d),f'Kling B-roll ses içeriyor: {p}'
for p in sorted((gen/'silent').glob('*.mp4')):
 d=probe(p);v=video_stream(d);assert (v['width'],v['height'],v['r_frame_rate'])==(1920,1080,'30/1');assert abs(float(v['duration'])-15)<.01;assert not audio_streams(d)
for p in sorted((gen/'final').glob('0?.mp4')):
 d=probe(p);v=video_stream(d);a=audio_streams(d);assert len(a)==1;assert abs(float(v['duration'])-15)<.01;assert a[0]['sample_rate']=='48000'
master=gen/'final'/'HushBoard-V2-45s.mp4';d=probe(master);v=video_stream(d);a=audio_streams(d)[0]
assert (v['width'],v['height'],v['r_frame_rate'],v['pix_fmt'])==(1920,1080,'30/1','yuv420p')
assert abs(float(v['duration'])-45)<.01;assert 45 <= float(d['format']['duration']) <= 45.05
assert a['sample_rate']=='48000' and a['channels']==2
print('FFPROBE OK: 3 sessiz Kling B-roll; 3×15 sn final; 1920×1080/30 fps; 48 kHz stereo; master ≈45.02 sn')
PY
MASTER="$ROOT/generated/v2/final/HushBoard-V2-45s.mp4"
CONTACT="$ROOT/generated/v2/final/CONTACT-SHEET.jpg"
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$MASTER" \
  -vf "fps=2/3,scale=480:270:flags=lanczos,tile=5x6" -frames:v 1 "$CONTACT"
ffmpeg -nostdin -hide_banner -i "$MASTER" -map 0:a:0 -filter:a ebur128=peak=true -f null - \
  2> "$ROOT/generated/v2/final/LOUDNESS.txt"
grep -A14 'Summary:' "$ROOT/generated/v2/final/LOUDNESS.txt" | tail -15
echo "CONTACT SHEET OK: $CONTACT"
