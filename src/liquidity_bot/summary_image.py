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

CANVAS_WIDTH = 1200
# Match the complete seven-day rewards report's attachment proportions.
CANVAS_HEIGHT = 1382
VALUE_FONT_SIZE = 34

METRIC_LABELS = frozenset({
    'Quoting', 'Stuck', 're-quoting', 'awaiting rotation', 'free, no supply',
    'Eligible sides', 'Resting side-hours', 'Viable now', 'Rewarded in window',
    'Refused, tight book', 'Lost to errors', 'Remain est. >=$1',
    'Remain est. >=$1.50', 'Settled cash', 'Buying power', 'Retained target',
    'Free to withdraw', 'Market stream', 'Private stream', 'Unresolved',
    'Unknown orders', 'REST rate', 'REST ceiling', 'Ledger on disk',
})


def metric_rows(block):
    """Decode the existing fixed-width text rows without changing values.

    Do this before stripping indentation or abbreviating labels. Long money
    values need not have whitespace between the 19-column label and value.
    Unrecognized diagnostic notes stay whole rather than being misparsed.
    """
    rows = []
    for line in block.splitlines():
        if not line.strip() or line.startswith('```'):
            continue
        label, value = line[:19].strip(), line[19:].strip()
        if label in METRIC_LABELS and value:
            rows.append((label, value))
        elif line.startswith('      ') and line[21:].strip().isdigit():
            rows.append((line[6:21].strip(), line[21:].strip()))
        else:
            rows.append((line.strip(), None))
    return rows


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
    slots = metric_rows(model['slots'])
    health = metric_rows(model['health'])
    capital = metric_rows(model['capital'])
    # Compact display-only label aliases; underlying text retains every detail.
    aliases = {'Rewarded in window':'Rewarded', 'Refused, tight book':'Tight books',
               'Resting side-hours':'Rest hours', 'awaiting rotation':'Awaiting rotation',
               'Free to withdraw':'Free to withdraw', 'Market stream':'Book age',
               'Private stream':'Private age', 'REST rate':'REST ceiling',
               'Ledger on disk':'Ledger', 'Remain est.':'Est. remaining'}
    def compact(line):
        for old,new in aliases.items(): line=line.replace(old,new)
        return line
    slots, health, capital = [[(compact(label), value) for label,value in group]
                              for group in (slots,health,capital)]
    if model['source'] == 'rest':
        health = [row for row in health if row[0] != 'Private age']
    health.insert(0, ('Account / book', model['source'].upper()+' / WS'))
    bottom = max(len(slots),len(capital)+len(health)+1)
    # Never silently omit a diagnostic to meet the fixed mobile canvas.
    if bottom > 12 or len(model['risk']) > 7 or len(model['today']) > 8:
        raise ValueError('expanded summary requires text')
    height = CANVAS_HEIGHT
    image = Image.new('RGB', (CANVAS_WIDTH,height), '#101720')
    d = ImageDraw.Draw(image)
    white,muted,accent = '#EEF3FA','#A5B5C9','#6ABAFF'
    def text(value,x,y,size=28,color=white,bold=False,right=False,width=1080,baseline=False):
        value=str(value)
        while d.textlength(value,font=font(size,bold)) > width and size>18: size-=1
        if d.textlength(value,font=font(size,bold)) > width: raise ValueError('summary label too wide')
        anchor = ('rs' if right else 'ls') if baseline else ('ra' if right else 'la')
        d.text((x,y),value,font=font(size,bold),fill=color,anchor=anchor)
    labels = {'Trading gains':'Gains', 'Trading losses':'Losses',
              'Fees · net rebates':'Fees · net', 'Day-of / daily h':'Day / daily h',
              'Net profit · partial':'Net profit*', 'Net loss · partial':'Net loss*',
              'Overall net · partial':'Overall net*'}
    def metric(x,y,label,value,size=VALUE_FONT_SIZE):
        # One column edge and baseline in EVERY section, independent of label
        # length, proportional fonts, signs, currency symbols or unknown values.
        text(labels.get(label,label),x+22,y,27,muted,width=268,baseline=True)
        text(value,x+534,y,size,'#FF959B' if '$-' in value else white,
             True,True,width=240,baseline=True)
    def card(x,y,w,title,rows,h):
        d.rounded_rectangle((x,y,x+w,y+h),radius=16,fill='#1A2533')
        text(title,x+22,y+18,30,accent,True,width=w-44)
        for i,(label,value) in enumerate(rows):
            metric(x,y+90+i*41,label,value)
    text(model['title'].upper(),32,25,38,bold=True)
    text(stamp(model['timestamp']).astimezone(NEW_YORK).strftime('%m/%d · %I:%M %p ET'),32,78,24,muted)
    # Text never relies on color alone; errors and pause details remain explicit.
    state=model['status'].replace('\n',' · ')
    if len(state)>145: raise ValueError('long safety status requires text')
    text(state,32,115,27,accent,width=1136)
    card(32,156,556,'STATUS & WINDOW',model['risk'],400)
    # Keep the loss dollars above and add an explicitly labelled visual meter.
    # Unknown is not zero; overshoot stays numeric while fill width is capped.
    percent = model.get('hard_stop_meter')
    label = 'HARD · unavailable' if percent is None else f'HARD · {percent:.0f}% used'
    text(label,54,522,23,muted,width=252)
    d.rounded_rectangle((316,520,566,540),radius=6,fill='#394657')
    if percent is not None and percent > 0:
        fill = '#FF959B' if percent >= 80 else '#6ABAFF'
        d.rounded_rectangle((316,520,316+250*min(percent,100)/100,540),
                            radius=6,fill=fill)
    card(612,156,556,'TODAY · CLOSED FILLS',model['today'],400)
    # Two columns remove dead space without reducing type or dropping values.
    d.rounded_rectangle((32,576,1168,876),radius=16,fill='#1A2533')
    text('LIFETIME RESULTS · PARTIAL',54,594,30,accent,True)
    for x,rows in ((32,model['lifetime'][:4]),(612,model['lifetime'][4:])):
        for i,(label,value) in enumerate(rows):
            y=642+i*41
            if label.endswith(' · partial'):
                d.rounded_rectangle((x+12,y-4,x+544,y+37),radius=8,
                                    fill='#35262E' if '$-' in value else '#20362D')
            metric(x,y+30,label,value)
    # Pending remains alongside paid and overall net, inside Lifetime Results.
    text(model['coverage'],54,817,23,muted)
    text(model['reward_note']+' · Paid: bot-era/date-scoped · Since '+model['since'],54,847,22,muted)
    for x,title,lines in ((32,'MARKETS',slots),(612,'CAPITAL & HEALTH',capital+[('—',None)]+health)):
        d.rounded_rectangle((x,896,x+556,1316),radius=16,fill='#1A2533')
        text(title,x+22,914,30,accent,True,width=512)
        for i,(label,value) in enumerate(lines):
            if value is None:
                text(label,x+22,956+i*30,27,width=512)
            else:
                metric(x,980+i*30,label,value,size=27)
    text('* Net = closed-fill P&L + paid · Pending excluded · + partial hour split',32,1340,23,muted)
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
