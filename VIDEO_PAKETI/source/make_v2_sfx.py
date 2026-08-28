#!/usr/bin/env python3
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

SR=48000;DUR=15;N=SR*DUR
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'generated'/'v2'/'sfx';OUT.mkdir(parents=True,exist_ok=True)
CUTS={1:[2.0,3.5,5.5,6.7,8.7,11.2,13.2],2:[1.4,3.0,4.8,6.2,7.8,10.8,12.7],3:[2.2,4.4,5.7,7.5,9.0,11.0,12.8]}

def add_hit(buf,t,amp=.15,pan=0.0,seed=1):
 r=random.Random(seed);start=max(0,int(t*SR));last=0.0
 for k in range(int(.14*SR)):
  if start+k>=N:break
  raw=r.uniform(-1,1);smooth=.78*last+.22*raw;hp=raw-smooth;last=smooth
  env=math.exp(-k/(SR*.026));thump=math.sin(2*math.pi*92*k/SR)*math.exp(-k/(SR*.055))
  s=amp*(.72*hp*env+.28*thump)
  buf[0][start+k]+=s*(1-pan*.35);buf[1][start+k]+=s*(1+pan*.35)

def add_tick(buf,t,amp=.055,pan=0.0):
 start=max(0,int(t*SR))
 for k in range(int(.055*SR)):
  if start+k>=N:break
  env=math.exp(-k/(SR*.012));s=amp*math.sin(2*math.pi*(1450-450*k/(.055*SR))*k/SR)*env
  buf[0][start+k]+=s*(1-pan*.5);buf[1][start+k]+=s*(1+pan*.5)

def add_whoosh(buf,t,amp=.035,seed=2):
 r=random.Random(seed);length=int(.26*SR);start=max(0,int((t-.22)*SR));last=0.0
 for k in range(length):
  if start+k>=N:break
  raw=r.uniform(-1,1);last=.92*last+.08*raw
  x=k/length;env=math.sin(math.pi*x)**1.7;s=amp*last*env
  buf[0][start+k]+=s*(1-.25*x);buf[1][start+k]+=s*(.75+.25*x)

def render(clip):
 buf=[[0.0]*N,[0.0]*N];add_hit(buf,.03,.20,0,clip*100);add_tick(buf,.03,.07,0)
 for i,t in enumerate(CUTS[clip]):
  pan=(-.55 if i%2==0 else .55);add_whoosh(buf,t,.045,clip*1000+i);add_hit(buf,t,.13,pan,clip*100+i);add_tick(buf,t+.035,.048,-pan)
 add_hit(buf,14.72,.17,0,clip*999);add_tick(buf,14.75,.06,0)
 peak=max(max(abs(v) for v in buf[0]),max(abs(v) for v in buf[1]),.001);gain=min(1,.82/peak)
 path=OUT/f'{clip:02d}.wav'
 with wave.open(str(path),'wb') as w:
  w.setnchannels(2);w.setsampwidth(2);w.setframerate(SR)
  data=bytearray()
  for i in range(N):
   l=int(max(-1,min(1,buf[0][i]*gain))*32767);r=int(max(-1,min(1,buf[1][i]*gain))*32767)
   data.extend(struct.pack('<hh',l,r))
  w.writeframes(data)
if __name__=='__main__':
 for c in (1,2,3):render(c)
