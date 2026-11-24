"""Original explanatory blocks, not a claimed application screenshot or experiment."""
from pathlib import Path
from PIL import Image,ImageDraw
from .media import _font,_wrap

def render_explanation(title,blocks,path):
    if not isinstance(title,str) or not title.strip() or len(title)>70 or not 2<=len(blocks)<=5:
        raise ValueError('Açıklayıcı çizim kısa başlık ve 2–5 blok ister.')
    canvas=Image.new('RGB',(840,1),'#f6f3ec');probe=ImageDraw.Draw(canvas)
    heading=_wrap(probe,title,_font(54),728)
    if len(heading)>2:raise ValueError('Başlık 2 satıra sığmalı.')
    layouts=[]
    for label,body in blocks:
        if not isinstance(label,str) or not isinstance(body,str) or len(label)>35 or len(body)>240:raise ValueError('Açıklama bloğu çok uzun.')
        lines=_wrap(probe,body,_font(40),696)
        if len(lines)>4 or len(_wrap(probe,label,_font(40),696))>1:raise ValueError('Blok okunaklı boyuta sığmıyor; metin kesilmedi.')
        layouts.append((label,lines,110+len(lines)*50))
    header=104+len(heading)*66;height=header+sum(h for _,_,h in layouts)+32*len(layouts)+100
    image=Image.new('RGB',(840,height),'#f6f3ec');draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,15,height),fill='#b7351a')
    draw.text((48,28),'KARINCAAI · ÖZGÜN AÇIKLAMA',font=_font(26),fill='#4b5c59')
    y=78
    for line in heading:draw.text((48,y),line,font=_font(54),fill='#182e2c');y+=66
    y=header
    for index,(label,lines,block_height) in enumerate(layouts,1):
        draw.rounded_rectangle((40,y,800,y+block_height),radius=10,fill='#ffffff',outline='#c7cdc4',width=2)
        draw.text((64,y+20),f'{index:02d}  {label}',font=_font(40),fill='#9f301a')
        line_y=y+82
        for line in lines:draw.text((64,line_y),line,font=_font(40),fill='#182e2c');line_y+=50
        y+=block_height+32
    draw.text((48,height-72),'Yöntem önerisi · uygulama ekranı değildir',font=_font(26),fill='#4b5c59')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);image.save(path,format='PNG',optimize=True)
    return {'path':str(path),'width':840,'height':height,'limitation':'Özgün açıklama; uygulama ekranı değildir. Deney sonucu veya kullanıcı testi iddiası içermez.','font_note':'Mevcut sistem fontu raster çıktı için kullanıldı; font dosyası dağıtılmaz. Ticari hak incelemesi insan bekler.'}
