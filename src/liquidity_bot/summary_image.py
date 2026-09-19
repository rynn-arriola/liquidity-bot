"""Optional Pillow presentation and one-shot upload, used off the trading loop."""
from __future__ import annotations
import io
import json
from pathlib import Path
import urllib.request
import urllib.parse
import urllib.error
import uuid
from .summary_report import stamp
from .reward_calendar import NEW_YORK


def font(size, bold=False):
    from PIL import ImageFont
    paths = (['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 'C:/Windows/Fonts/arialbd.ttf']
             if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'C:/Windows/Fonts/arial.ttf'])
    for path in paths:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render(model):
    from PIL import Image, ImageDraw
    # One authoritative text report feeds both presentation routes.
    def legacy_rows(block):
        return [line.strip() for line in block.splitlines() if line.strip() and not line.startswith('```')]
    slots = legacy_rows(model['slots'])
    health = legacy_rows(model['health'])
    capital = legacy_rows(model['capital'])
    # Compact display-only label aliases; underlying text retains every detail.
    aliases = {'Rewarded in window':'Rewarded', 'Refused, tight book':'Tight books',
               'Resting side-hours':'Rest hours', 'awaiting rotation':'Awaiting rotation',
               'Free to withdraw':'Free to withdraw', 'Market stream':'Book age',
               'Private stream':'Private age', 'REST rate':'REST ceiling',
               'Ledger on disk':'Ledger', 'Remain est.':'Est. remaining'}
    def compact(line):
        for old,new in aliases.items(): line=line.replace(old,new)
        return line
    slots, health, capital = [[compact(line) for line in group] for group in (slots,health,capital)]
    if model['source'] == 'rest':
        health = [line for line in health if not line.startswith('Private age')]
    health.insert(0, 'Account / book   '+model['source'].upper()+' / WS')
    bottom = max(len(slots),len(capital)+len(health)+1)
    height = 1193+65+bottom*40+95
    image = Image.new('RGB', (1200,height), '#101720')
    d = ImageDraw.Draw(image)
    white,muted,accent = '#EEF3FA','#A5B5C9','#6ABAFF'
    def text(value,x,y,size=28,color=white,bold=False,right=False,width=1080):
        value=str(value)
        while d.textlength(value,font=font(size,bold)) > width and size>18: size-=1
        if d.textlength(value,font=font(size,bold)) > width: raise ValueError('summary label too wide')
        d.text((x,y),value,font=font(size,bold),fill=color,anchor='ra' if right else 'la')
    def card(x,y,w,title,rows,h):
        d.rounded_rectangle((x,y,x+w,y+h),radius=16,fill='#1A2533')
        text(title,x+22,y+18,30,accent,True,width=w-44)
        for i,(label,value) in enumerate(rows):
            text(label,x+22,y+66+i*43,25,muted,width=w*.54)
            color='#FF959B' if '$-' in value else white
            text(value,x+w-22,y+66+i*43,28,color,True,True,width=w*.41)
    text(model['title'].upper(),36,25,36,bold=True)
    text(stamp(model['timestamp']).astimezone(NEW_YORK).strftime('%m/%d · %I:%M %p ET'),36,78,24,muted)
    # Text never relies on color alone; errors and pause details remain explicit.
    state=model['status'].replace('\n',' · ')
    if len(state)>145: raise ValueError('long safety status requires text')
    text(state,36,122,27,accent,width=1128)
    card(36,178,552,'STATUS & WINDOW',model['risk'],433)
    card(612,178,552,'TODAY · CLOSED FILLS',model['today'],433)
    card(36,631,1128,'LIFETIME RESULTS · PARTIAL',model['lifetime'][:5],290)
    label,value=model['lifetime'][5]
    d.rounded_rectangle((58,933,1142,1009),radius=12,fill='#35262E' if '$-' in value else '#20362D')
    text('OVERALL NET · '+label.replace(' · partial','').upper(),80,953,29,bold=True,width=780)
    text(value,1120,944,40,'#FF959B' if '$-' in value else '#7BDFB2',True,True,width=310)
    # Pending is deliberately INSIDE the same Lifetime Results section.
    text('Pending · not yet paid',58,1029,28,muted)
    text(model['lifetime'][6][1],1142,1029,30,bold=True,right=True,width=340)
    text(model['coverage'],58,1081,23,muted)
    text(model['reward_note']+' · Paid: bot-era/date-scoped · Since '+model['since'],58,1114,22,muted)
    for x,title,lines in ((36,'MARKETS',slots),(612,'CAPITAL & HEALTH',capital+['—']+health)):
        d.rounded_rectangle((x,1173,x+552,height-75),radius=16,fill='#1A2533')
        text(title,x+22,1191,30,accent,True,width=508)
        for i,line in enumerate(lines): text(line,x+22,1241+i*40,25,width=508)
    text('Net = closed-fill P&L + paid · Pending excluded · + partial hour split',38,height-51,23,muted)
    output=io.BytesIO()
    image.save(output,format='PNG',optimize=True)
    result=output.getvalue()
    if len(result)>2_000_000: raise ValueError('image too large')
    return result


def post(url,png,payload):
    """No retry after any possibly delivered request; never log secret URLs."""
    if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png)>2_000_000:
        raise ValueError('invalid summary image')
    embed=payload['embeds'][0]
    body={'allowed_mentions':{'parse':[]},'embeds':[{'color':embed['color'],
          'image':{'url':'attachment://summary.png'}}],
          'attachments':[{'id':0,'filename':'summary.png','description':
            (embed['title']+' '+embed['description']+' '+embed['footer']['text'])[:1024]}]}
    boundary='summary-'+uuid.uuid4().hex
    data=(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode()+json.dumps(body).encode()
    data+=(f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="summary.png"\r\nContent-Type: image/png\r\n\r\n').encode()+png+f'\r\n--{boundary}--\r\n'.encode()
    parts=urllib.parse.urlsplit(url)
    query=dict(urllib.parse.parse_qsl(parts.query));query['wait']='true'
    url=urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    request=urllib.request.Request(url,data=data,method='POST',headers={
        'Content-Type':f'multipart/form-data; boundary={boundary}',
        'User-Agent':'LiquidityBotDiscordWebhook/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            return 200<=response.status<300
    except (urllib.error.URLError,TimeoutError,OSError): return False
