#!/usr/bin/env python3
from __future__ import annotations
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GEN=ROOT/'generated'
BROLL=GEN/'v2'/'broll'
GRAPHICS=GEN/'promo-silent'
FRAMES=ROOT/'frames'
OUT=GEN/'v2'/'silent'
SHOTS=GEN/'v2'/'shots'
FONT_LIT=ROOT/'source'/'literata.ttf'

# clip -> (source, start, duration, optional treatment)
PLAN={
 1:[
  ('b01',0.00,2.00,''), ('g01',0.15,1.50,''), ('b01',3.00,2.00,''),
  ('g01',3.85,1.20,''), ('b01',6.00,2.00,''), ('g01',6.25,2.50,''),
  ('b01',13.00,2.00,'laptop_participant'), ('g01',12.15,1.80,''),
 ],
 2:[
  ('g02',0.20,1.40,''), ('b02',0.00,1.60,''), ('g02',3.40,1.80,''),
  ('b02',1.80,1.40,''), ('g02',6.35,1.60,''), ('b02',4.50,3.00,''),
  ('g02',8.80,1.90,''), ('g02',12.00,2.30,''),
 ],
 3:[
  ('g03',0.25,2.20,''), ('b03',0.80,2.20,''), ('g03',3.70,1.30,''),
  ('b03',3.80,1.80,''), ('g03',5.60,1.50,''), ('g03',7.50,2.00,''),
  ('b03',8.30,1.80,''), ('b03',12.84,2.20,'closing'),
 ],
}

def src(key:str)->Path:
 return (BROLL/f'{key[1:]}.mp4') if key.startswith('b') else (GRAPHICS/f'{key[1:]}.mp4')

def run(cmd): subprocess.run(cmd,check=True)

def common_encode(output:Path):
 return ['-an','-c:v','libx264','-preset','fast','-crf','16','-profile:v','high','-level','4.0','-pix_fmt','yuv420p','-g','60','-sc_threshold','0','-movflags','+faststart',str(output)]

def make_shot(clip:int,index:int,key:str,start:float,dur:float,treatment:str)->Path:
 output=SHOTS/f'{clip:02d}-{index:02d}.mp4'; frames=str(round(dur*30)); source=src(key)
 if treatment=='laptop_participant':
  # Kling supplies only the physical laptop/camera move. Exact HushBoard pixels are overlaid here.
  filt=("[0:v]fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS[bg];"
        "[1:v]scale=w='1100+4*n':h=-1:eval=frame,format=rgba,colorchannelmixer=aa=0.985[ui];"
        "[bg][ui]overlay=x='(W-w)/2':y='170-0.4*n':eval=frame:shortest=1,format=yuv420p[v]")
  cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(start),'-t',str(dur),'-i',str(source),
       '-loop','1','-t',str(dur),'-i',str(FRAMES/'01-participant-start.png'),'-filter_complex',filt,
       '-map','[v]','-frames:v',frames]+common_encode(output)
 elif treatment=='closing':
  a1="if(lt(t,0.15),0,if(lt(t,0.42),(t-0.15)/0.27,1))"
  a2="if(lt(t,0.48),0,if(lt(t,0.75),(t-0.48)/0.27,1))"
  a3="if(lt(t,0.82),0,if(lt(t,1.12),(t-0.82)/0.30,1))"
  vf=("fps=30,scale=1920:1080,setsar=1,setpts=PTS-STARTPTS,"
      f"drawtext=fontfile='{FONT_LIT}':text='HESAP YOK.':fontsize=106:fontcolor=0x17211b:x=105:y=210:alpha='{a1}',"
      f"drawtext=fontfile='{FONT_LIT}':text='SİHİRLİ VAAT YOK.':fontsize=106:fontcolor=0x176b5b:x=105:y=355:alpha='{a2}',"
      f"drawtext=fontfile='{FONT_LIT}':text='ÇALIŞAN AKIŞ VAR.':fontsize=106:fontcolor=0x17211b:x=105:y=500:alpha='{a3}',"
      "drawbox=x=105:y=685:w=1710:h=5:color=0x17211b@0.95:t=fill:enable='gte(t,1.05)',"
      "drawtext=fontfile='"+str(ROOT/'source'/'plex.ttf')+"':text='GERÇEK TESTNET · SHIELDED ÖDEME · DOĞRULANMIŞ İADE':fontsize=31:fontcolor=0x17211b:x=108:y=735:enable='gte(t,1.12)',format=yuv420p")
  cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(start),'-t',str(dur),'-i',str(source),
       '-vf',vf,'-frames:v',frames]+common_encode(output)
 else:
  vf='fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS,format=yuv420p'
  cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(start),'-t',str(dur),'-i',str(source),
       '-vf',vf,'-frames:v',frames]+common_encode(output)
 run(cmd);return output

def main():
 SHOTS.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
 for clip,plan in PLAN.items():
  files=[make_shot(clip,n,*shot) for n,shot in enumerate(plan,1)]
  listing=SHOTS/f'{clip:02d}.txt';listing.write_text(''.join(f"file '{p}'\n" for p in files))
  assembled=SHOTS/f'{clip:02d}-assembled.mp4'
  run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(assembled)])
  target=OUT/f'{clip:02d}.mp4'
  vf='fps=30,tpad=stop_mode=clone:stop_duration=0.2,trim=duration=15,setpts=PTS-STARTPTS,format=yuv420p'
  run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(assembled),'-vf',vf,'-frames:v','450','-an','-c:v','libx264','-preset','fast','-crf','16','-profile:v','high','-level','4.0','-pix_fmt','yuv420p','-movflags','+faststart',str(target)])

if __name__=='__main__':main()
