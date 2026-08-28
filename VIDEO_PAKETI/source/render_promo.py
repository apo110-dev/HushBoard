#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

W,H,FPS,DURATION=1920,1080,30,15
ROOT=Path(__file__).resolve().parents[1]
FRAMES=ROOT/'frames'
SRC=ROOT/'source'
OUT=ROOT/'generated'/'promo-silent'
PAPER=(244,241,229); WHITE=(253,252,247); INK=(24,33,27); YELLOW=(246,184,40)
TEAL=(20,105,89); RED=(184,66,55); MUTED=(105,105,93); PALE=(235,231,216)
ASSETS={
 'participant': Image.open(FRAMES/'01-participant-start.png').convert('RGB'),
 'operator': Image.open(FRAMES/'02-operator-proof-start.png').convert('RGB'),
}
LIT=SRC/'literata.ttf'; PLEX=SRC/'plex.ttf'
_rng=random.Random(1108)
_noise=Image.frombytes('L',(W,H),_rng.randbytes(W*H))

@lru_cache(maxsize=128)
def font(kind:str,size:int): return ImageFont.truetype(str(LIT if kind=='lit' else PLEX),size)
def clamp(x,a=0,b=1): return max(a,min(b,x))
def ease(x): x=clamp(x); return x*x*(3-2*x)
def out(x): x=clamp(x); return 1-(1-x)**3
def lerp(a,b,x): return a+(b-a)*x
def phase(t,a,b): return clamp((t-a)/(b-a)) if b>a else 1

def base(bg=PAPER):
 im=Image.new('RGB',(W,H),bg)
 # fixed subtle paper grain, never a gradient
 grain=Image.new('RGB',(W,H),bg); grain.putalpha(_noise.point(lambda v: 7 if v>128 else 0))
 return Image.alpha_composite(im.convert('RGBA'),grain.convert('RGBA'))

def txt(im,xy,text,size,color=INK,kind='plex',anchor='la',stroke=0,spacing=4):
 d=ImageDraw.Draw(im); d.multiline_text(xy,text,font=font(kind,size),fill=color,anchor=anchor,spacing=spacing,stroke_width=stroke,stroke_fill=color)

def pill(im,box,label,fill=WHITE,color=INK,border=INK,size=28):
 d=ImageDraw.Draw(im);d.rounded_rectangle(box,radius=999,fill=fill,outline=border,width=2)
 txt(im,((box[0]+box[2])//2,(box[1]+box[3])//2),label,size,color,'plex','mm')

def header(im,step,label,dark=False):
 c=WHITE if dark else INK;d=ImageDraw.Draw(im)
 d.ellipse((66,55,118,107),outline=c,width=2);txt(im,(92,81),'HB',16,c,'plex','mm')
 txt(im,(137,65),'HushBoard',23,c,'plex');txt(im,(137,92),'Zcash testnet',13,c,'plex')
 d.line((1590,80,1655,80),fill=c,width=3);txt(im,(1680,80),f'{step:02d}  {label}',16,c,'plex','lm')

def footer_progress(im,t,clip):
 d=ImageDraw.Draw(im);d.rectangle((65,1035,1855,1038),fill=(80,75,58))
 d.rectangle((65,1035,int(65+1790*clamp(t/15)),1041),fill=TEAL)
 txt(im,(65,1010),f'{clip:02d} / 03',13,MUTED,'plex')

def scaled(asset,w):
 im=ASSETS[asset];h=round(w*im.height/im.width);return im.resize((w,h),Image.Resampling.LANCZOS)

def card(im,asset,x,y,w,shadow=14,border=2):
 pic=scaled(asset,int(w));h=pic.height;d=ImageDraw.Draw(im)
 d.rounded_rectangle((x+shadow,y+shadow,x+w+shadow,y+h+shadow),radius=12,fill=(18,25,21,95))
 d.rounded_rectangle((x-2,y-2,x+w+2,y+h+2),radius=10,fill=INK)
 im.paste(pic,(int(x),int(y)))
 return h

def reveal_title(im,text,x,y,size,p,color=INK,kind='lit',maxw=1500):
 layer=Image.new('RGBA',(W,H));txt(layer,(x,y+int((1-out(p))*90)),text,size,color,kind)
 mask=Image.new('L',(W,H));ImageDraw.Draw(mask).rectangle((x-10,y-15,x+maxw,y+size*2),fill=int(255*ease(p)))
 layer.putalpha(ImageChops.multiply(layer.getchannel('A'),mask));im.alpha_composite(layer)

def message_card(im,x,y,w,h,color,textline,p=1):
 d=ImageDraw.Draw(im);x=int(x);y=int(y);w=int(w);h=int(h)
 d.rounded_rectangle((x+8,y+8,x+w+8,y+h+8),radius=10,fill=(20,28,23,70))
 d.rounded_rectangle((x,y,x+w,y+h),radius=10,fill=WHITE,outline=color,width=3)
 d.ellipse((x+24,y+24,x+52,y+52),fill=color)
 d.rounded_rectangle((x+72,y+24,x+w-30,y+35),radius=5,fill=PALE)
 d.rounded_rectangle((x+72,y+49,x+w-100,y+60),radius=5,fill=PALE)
 txt(im,(x+24,y+h-28),textline,16,color,'plex','ls')

def clip1(t):
 # Beat 1: the hook
 if t<1.75:
  im=base(PAPER);header(im,1,'Problem')
  reveal_title(im,'FİKRİNİ SÖYLE.',82,250,132,phase(t,.05,.65),INK,'lit')
  reveal_title(im,'HESAP AÇMA.',82,405,132,phase(t,.28,.95),TEAL,'lit')
  d=ImageDraw.Draw(im);p=ease(phase(t,.65,1.35));d.rectangle((84,590,84+int(920*p),608),fill=YELLOW)
  txt(im,(88,665),'Hesapsız geri bildirim.',40,INK,'plex')
 elif t<3.65:
  im=base(INK);header(im,1,'Problem',True)
  reveal_title(im,'HESAP',80,185,155,phase(t,1.75,2.25),WHITE,'lit')
  reveal_title(im,'DUVARI.',80,345,155,phase(t,1.95,2.55),YELLOW,'lit')
  p=out(phase(t,2.05,3.25))
  # stacked signup cards slam in
  for i,label in enumerate(['E-posta','Şifre','Profil adı']):
   yy=170+i*170;xx=int(1120+(1-p)*(600+i*100))
   d=ImageDraw.Draw(im);d.rounded_rectangle((xx,yy,xx+660,yy+118),radius=12,fill=WHITE)
   txt(im,(xx+34,yy+32),label,22,MUTED,'plex');d.line((xx+34,yy+84,xx+620,yy+84),fill=INK,width=3)
 elif t<5.6:
  im=base((248,235,228));header(im,1,'Problem')
  reveal_title(im,'BEDAVA SPAM.',80,145,126,phase(t,3.65,4.15),RED,'lit')
  p=out(phase(t,3.8,5.35))
  positions=[(80,390),(500,275),(950,450),(1330,260),(690,680),(1250,700)]
  for i,(x,y) in enumerate(positions):
   q=phase(t,3.75+i*.12,4.55+i*.12);message_card(im,x,int(y+(1-out(q))*420),380,130,RED,'istenmeyen mesaj')
  d=ImageDraw.Draw(im);d.rectangle((0,1000,int(W*p),1080),fill=RED)
 else:
  im=base(PAPER);header(im,1,'Çözüm')
  # fast yellow wipe at transition
  if t<6.25:
   p=out(phase(t,5.6,6.25));ImageDraw.Draw(im).rectangle((0,0,int(W*p),H),fill=YELLOW)
   reveal_title(im,'HUSHBOARD',82,410,148,phase(t,5.68,6.15),INK,'lit')
  else:
   p=out(phase(t,6.15,7.1));x=int(760+(1-p)*1150);w=1080;card(im,'participant',x,185,w)
   reveal_title(im,'HESAP YOK.',75,190,94,phase(t,6.25,6.95),INK,'lit')
   txt(im,(80,310),'Mesaj için profil değil,\nölçülü bir sürtünme.',34,MUTED,'plex',spacing=10)
   if t>9.5:
    labels=['İsim yok','E-posta yok','Profil yok']
    for i,label in enumerate(labels):
     q=out(phase(t,9.5+i*.22,10.15+i*.22));yy=455+i*86
     pill(im,(80-int((1-q)*360),yy,360-int((1-q)*360),yy+62),label,WHITE,TEAL,TEAL,23)
   if t>12.05:
    q=out(phase(t,12.05,12.8));d=ImageDraw.Draw(im);y=int(760+(1-q)*280)
    d.rectangle((70,y,700,y+190),fill=YELLOW,outline=INK,width=3)
    txt(im,(105,y+45),'0,01 TAZ',58,INK,'lit');txt(im,(105,y+125),'İade edilebilir testnet teminatı',23,INK,'plex')
 footer_progress(im,t,1);return im

def clip2(t):
 im=base(PAPER);header(im,2,'Nasıl çalışır')
 # timeline labels always anchor the motion
 d=ImageDraw.Draw(im);d.line((130,170,1790,170),fill=INK,width=3)
 for i,(n,label) in enumerate([('01','YAZ'),('02','ADRES'),('03','GÖNDER'),('04','EŞLEŞTİR')]):
  x=130+i*450;active=t>=i*2.15;d.ellipse((x-18,145,x+32,195),fill=YELLOW if active else PALE,outline=INK,width=2)
  txt(im,(x+7,170),n,15,INK,'plex','mm');txt(im,(x+56,170),label,20,TEAL if active else MUTED,'plex','lm')
 if t<3.1:
  reveal_title(im,'MESAJINI YAZ.',92,280,112,phase(t,.1,.75),INK,'lit')
  q=out(phase(t,.65,1.35));d.rounded_rectangle((92,520,1820,875),radius=14,fill=WHITE,outline=INK,width=3)
  txt(im,(130,565),'Mesajın',24,INK,'plex');
  # exact stage message revealed as lines, not fake typing
  msg='Gece yürüyüş yolundaki iki lamba çalışmıyor;\nkaranlık kalan bölüm için bakım kaydı açılabilir mi?'
  txt(im,(130,650),msg,38,INK,'plex',spacing=16)
  d.rectangle((130,810,130+int(1300*q),822),fill=YELLOW)
 elif t<6.2:
  reveal_title(im,'İADE ADRESİNİ',92,280,104,phase(t,3.1,3.75),INK,'lit')
  reveal_title(im,'AYRI VER.',92,390,104,phase(t,3.35,4.0),TEAL,'lit')
  q=out(phase(t,3.75,4.45));d.rounded_rectangle((92,600,1760,765),radius=14,fill=WHITE,outline=INK,width=3)
  txt(im,(140,648),'utest1… yalnızca iade için',32,MUTED,'plex')
  d.ellipse((1635,635,1687,687),outline=TEAL,width=5);d.arc((1649,649,1673,679),180,360,fill=TEAL,width=4)
  txt(im,(95,840),'Operatör ekranında adresin tamamı görünmez.',25,MUTED,'plex')
 elif t<8.65:
  q=out(phase(t,6.2,6.8));d.rectangle((0,210,W,950),fill=YELLOW)
  reveal_title(im,'0,01 TAZ',110,350,150,phase(t,6.25,6.9),INK,'lit')
  reveal_title(im,'GÖNDER.',110,510,150,phase(t,6.5,7.1),TEAL,'lit')
  pill(im,(1130,390,1770,510),'1.000.000 zat',WHITE,INK,INK,35)
  txt(im,(1135,565),'Testnet · maddi değeri yok',27,INK,'plex')
 elif t<11.55:
  reveal_title(im,'CÜZDAN EŞLEŞTİRİR.',88,250,92,phase(t,8.65,9.2),INK,'lit')
  # animated evidence rails
  p=ease(phase(t,8.8,10.85));
  d.line((160,600,1760,600),fill=PALE,width=12);d.line((160,600,int(160+1600*p),600),fill=TEAL,width=12)
  for x,label in [(220,'1.000.000 zat'),(860,'HB1 memo'),(1500,'1/1+')]:
   q=out(phase(t,8.9+(x/1920)*.8,9.8+(x/1920)*.8));r=int(47*q);d.ellipse((x-r,600-r,x+r,600+r),fill=YELLOW,outline=INK,width=3)
   if q>.5:txt(im,(x,710),label,27,INK,'plex','ma')
  if t>10.45:pill(im,(720,850,1200,930),'✓  EŞLEŞTİ',TEAL,WHITE,TEAL,34)
 else:
  p=out(phase(t,11.55,12.2));x=int(115+(1-p)*W);card(im,'operator',x,235,1690)
  d=ImageDraw.Draw(im)
  # true-status callout, editorial rather than modifying UI
  if t>12.45:
   q=out(phase(t,12.45,13.15));box=(1200,260,1780,355);d.rounded_rectangle(box,radius=48,fill=TEAL,outline=WHITE,width=3)
   txt(im,(1490,307),'ZİNCİRDE ONAYLI',29,WHITE,'plex','mm')
  if t>13.25:
   q=out(phase(t,13.25,13.8));pill(im,(1020,865,1775,950),'MODERASYON MASASINA GELDİ',YELLOW,INK,INK,27)
 footer_progress(im,t,2);return im

def clip3(t):
 if t<3.35:
  im=base(PAPER);header(im,3,'Kanıt')
  p=out(phase(t,.05,.7));card(im,'operator',int(95+(1-p)*W),205,1730)
  d=ImageDraw.Draw(im);q=out(phase(t,.8,1.45));d.rectangle((95,205,1825,300),fill=(246,184,40,int(235*q)))
  txt(im,(130,250),'KARAR VE SAKLAMA OPERATÖRDE',31,INK,'plex','lm')
  pill(im,(112,875,650,950),'MERKEZÎ MVP',WHITE,RED,RED,26)
 elif t<7.3:
  im=base(PAPER);header(im,3,'İade')
  reveal_title(im,'ANA PARA',85,185,112,phase(t,3.35,3.9),INK,'lit')
  reveal_title(im,'GERİ GİDER.',85,305,112,phase(t,3.55,4.15),TEAL,'lit')
  q=out(phase(t,4.0,4.8));d=ImageDraw.Draw(im)
  # receipt card
  x=int(920+(1-q)*1000);d.rounded_rectangle((x,200,x+820,830),radius=16,fill=WHITE,outline=INK,width=3)
  txt(im,(x+55,260),'İADE MAKBUZU',25,MUTED,'plex');d.line((x+55,315,x+765,315),fill=INK,width=2)
  txt(im,(x+55,390),'0,01 TAZ',76,TEAL,'lit');pill(im,(x+55,535,x+500,610),'✓ İADE ONAYLI',TEAL,WHITE,TEAL,26)
  txt(im,(x+55,705),'Gerçek testnet kaydı',24,MUTED,'plex')
  if t>5.55:
   pill(im,(85,650,760,735),'İLK AĞ ÜCRETİ HARİÇ',fill=(248,235,228),color=RED,border=RED,size=27)
   txt(im,(88,775),'İade, teminatın ana parasını kapsar.',25,MUTED,'plex')
 elif t<10.35:
  im=base(INK);header(im,3,'Sınır',True)
  reveal_title(im,'TAM ANONİMLİK',75,220,112,phase(t,7.3,7.95),WHITE,'lit')
  reveal_title(im,'VAADİ YOK.',75,340,112,phase(t,7.55,8.15),YELLOW,'lit')
  d=ImageDraw.Draw(im);p=out(phase(t,8.15,8.8));d.line((80,565,int(80+1740*p),565),fill=TEAL,width=5)
  if t>8.55:
   txt(im,(85,640),'Zincir açıkça görmez',31,WHITE,'plex');txt(im,(990,640),'HushBoard yine de bilir',31,YELLOW,'plex')
   txt(im,(85,705),'tutar · memo · receiver',23,(180,187,178),'plex')
   txt(im,(990,705),'mesaj · adres · zaman · metadata',23,(180,187,178),'plex')
 else:
  im=base(YELLOW);header(im,3,'Kapanış')
  reveal_title(im,'HESAP YOK.',75,185,110,phase(t,10.35,10.9),INK,'lit')
  reveal_title(im,'SİHİRLİ VAAT YOK.',75,305,110,phase(t,10.65,11.25),TEAL,'lit')
  reveal_title(im,'ÇALIŞAN AKIŞ VAR.',75,425,110,phase(t,10.95,11.55),INK,'lit')
  d=ImageDraw.Draw(im);p=out(phase(t,11.4,12.15));d.rectangle((75,610,int(75+1770*p),615),fill=INK)
  labels=[('GERÇEK TESTNET','Zcash'),('SHIELDED ÖDEME','0,01 TAZ'),('DOĞRULANMIŞ İADE','ana para')]
  for i,(a,b) in enumerate(labels):
   q=out(phase(t,11.75+i*.2,12.45+i*.2));x=75+i*590
   if q>0:txt(im,(x,690+int((1-q)*55)),a,20,INK,'plex');txt(im,(x,735+int((1-q)*55)),b,33,INK,'plex')
 footer_progress(im,t,3);return im

def render(clip:int,path:Path):
 path.parent.mkdir(parents=True,exist_ok=True)
 cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgba','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-movflags','+faststart',str(path)]
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
 fn={1:clip1,2:clip2,3:clip3}[clip]
 try:
  for n in range(FPS*DURATION): proc.stdin.write(fn(n/FPS).tobytes())
  proc.stdin.close();rc=proc.wait()
  if rc: raise SystemExit(rc)
 except Exception:
  proc.kill();raise

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--clip',type=int,choices=(1,2,3));ap.add_argument('--all',action='store_true');ap.add_argument('--preview',action='store_true');args=ap.parse_args()
 if args.preview:
  preview_dir=ROOT/'generated'/'promo-preview';preview_dir.mkdir(parents=True,exist_ok=True)
  for c,fn in [(1,clip1),(2,clip2),(3,clip3)]:
   for t in (.5,2.5,4.5,6.5,9,12,14.5): fn(t).convert('RGB').save(preview_dir/f'{c:02d}-{t:04.1f}.jpg',quality=92)
 elif args.all:
  for c in (1,2,3):render(c,OUT/f'{c:02d}.mp4')
 else: render(args.clip,OUT/f'{args.clip:02d}.mp4')
