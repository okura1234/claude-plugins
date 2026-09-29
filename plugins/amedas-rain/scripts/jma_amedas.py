#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
気象庁 公式データ 雨量＋警報見通しツール（bosai AMeDAS 速報値 / 防災情報XML / 概況）。
認証不要・公式エンドポイントのみ。

使い方:
  jma_amedas.py 東京                 # 地名で直近4日の雨量
  jma_amedas.py 千葉 --days 7
  jma_amedas.py 東京 --outlook        # 雨量＋現況警報＋気象台見通し文
  jma_amedas.py 千葉 --outlook --pref 千葉
  jma_amedas.py 東京 --list           # 曖昧地名→候補
  jma_amedas.py 東京 --wbgt --days 7  # 推定WBGT(8:30/13:00) 過去日はetrn確定値

注意: 速報値は直近1週間程度まで。解除時刻は予測しない（土壌雨量指数の残留で下限のみ推測可）。
"""
import sys, json, os, re, math, urllib.request, urllib.parse, datetime, argparse

DATA = "https://www.jma.go.jp/bosai/amedas/data"
XMLFEED = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"
OVERVIEW = "https://www.jma.go.jp/bosai/forecast/data/overview_forecast"
TABLE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amedastable.json")

# 府県予報区コード（主要・必要に応じ --pref で指定可）
PREF = {"北海道":"016000","青森":"020000","岩手":"030000","宮城":"040000","秋田":"050000","山形":"060000","福島":"070000",
        "茨城":"080000","栃木":"090000","群馬":"100000","埼玉":"110000","千葉":"120000","東京":"130000","神奈川":"140000",
        "新潟":"150000","富山":"160000","石川":"170000","福井":"180000","山梨":"190000","長野":"200000","岐阜":"210000",
        "静岡":"220000","愛知":"230000","三重":"240000","滋賀":"250000","京都":"260000","大阪":"270000","兵庫":"280000",
        "奈良":"290000","和歌山":"300000","鳥取":"310000","島根":"320000","岡山":"330000","広島":"340000","山口":"350000",
        "徳島":"360000","香川":"370000","愛媛":"380000","高知":"390000",
        "長崎":"420000","佐賀":"410000","福岡":"400000","熊本":"430000",
        "大分":"440000","宮崎":"450000","鹿児島":"460100","沖縄":"471000"}

def _get(url, asjson=True):
    req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r) if asjson else r.read().decode("utf-8").strip()

def load_table():
    if os.path.exists(TABLE_CACHE):
        try: return json.load(open(TABLE_CACHE, encoding="utf-8"))
        except Exception: pass
    t=_get(DATA.replace("/data","/const")+"/amedastable.json")
    try: json.dump(t, open(TABLE_CACHE,"w",encoding="utf-8"), ensure_ascii=False)
    except Exception: pass
    return t

def resolve(place):
    t=load_table(); hits=[]
    for code,v in t.items():
        kj=v.get("kjName") or ""; kn=v.get("knName") or ""
        if place in kj or place in kn: hits.append((code,kj,kn))
    return hits


# ---- 郵便番号 → 住所(zipcloud) → 座標(国土地理院) → 最寄りアメダス ----
ZIPAPI="https://zipcloud.ibsnet.co.jp/api/search?zipcode={z}"
GSIGEO="https://msearch.gsi.go.jp/address-search/AddressSearch?q={q}"
def is_zip(s): return bool(re.fullmatch(r"〒?\d{3}-?\d{4}", s or ""))
def is_latlon(s): return bool(re.fullmatch(r"-?\d{1,2}(\.\d+)?\s*,\s*-?\d{1,3}(\.\d+)?", s or ""))
def _nearest_rain_station(lat,lon):
    best=None
    for code,v in load_table().items():
        e=str(v.get("elems",""))
        if len(e)<2 or e[1]!="1": continue   # 雨量計あり
        la=v["lat"][0]+v["lat"][1]/60; lo=v["lon"][0]+v["lon"][1]/60
        d=math.hypot((la-lat)*111, (lo-lon)*111*math.cos(math.radians(lat)))
        if best is None or d<best[0]: best=(d,code,v.get("kjName",""))
    return best
def _pref_from_addr(addr):
    for nm,cd in PREF.items():
        if addr.startswith(nm): return cd
    return None
def locate(q):
    """郵便番号 / 緯度,経度 / 住所 → (code, 観測所名, lat, lon, 表示ラベル, 府県コード or None)。雨量観測のある最寄り観測所を選ぶ"""
    if is_zip(q):
        z=re.sub(r"\D","",q); r=_get(ZIPAPI.format(z=z))
        if not r.get("results"): sys.exit(f"郵便番号が見つからない: {z}")
        a=r["results"][0]; addr=a["address1"]+a["address2"]+a["address3"]
        g=_get(GSIGEO.format(q=urllib.parse.quote(addr)))
        if not g: sys.exit(f"住所の座標が引けない: {addr}")
        lon,lat=g[0]["geometry"]["coordinates"]; label=f"〒{z[:3]}-{z[3:]} {addr}"
        pc=a.get("prefcode",""); pref={"01":"016000","46":"460100","47":"471000"}.get(pc, pc+"0000")
    elif is_latlon(q):
        lat,lon=[float(v) for v in q.split(",")]
        addr=""; label=f"位置情報"; pref=None
    else:
        g=_get(GSIGEO.format(q=urllib.parse.quote(q)))
        if not g: return None
        lon,lat=g[0]["geometry"]["coordinates"]; addr=g[0]["properties"].get("title",q)
        label=f"住所 {addr}"; pref=_pref_from_addr(addr)
    muni=None
    try:
        rg=_get("https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lon=%s&lat=%s"%(lon,lat))
        muni=(rg.get("results") or {}).get("muniCd") or None
        if muni and not pref: pref={"01":"016000","46":"460100","47":"471000"}.get(muni[:2], muni[:2]+"0000")
    except Exception: pass
    best=_nearest_rain_station(lat,lon)
    print(f"{label} ({lat:.4f},{lon:.4f}) → 最寄り観測所 {best[2]}({best[1]}) 約{best[0]:.1f}km")
    return best[1],best[2],lat,lon,addr,pref,muni

def val(x):
    return None if (not isinstance(x,list) or x[0] is None) else x[0]

def latest_time():
    return datetime.datetime.fromisoformat(_get(DATA+"/latest_time.txt", asjson=False))

def collect_10m(code,start,end):
    data={}; d=start
    while d<=end:
        for hh in (0,3,6,9,12,15,18,21):
            try: j=_get(f"{DATA}/point/{code}/{d:%Y%m%d}_{hh:02d}.json")
            except Exception: continue
            for ts,rec in j.items():
                p=val(rec.get("precipitation10m"))
                if p is not None: data[ts]=p
        d+=datetime.timedelta(days=1)
    return data

# code → (要素, レベル, 名称)。気象庁警報ページ内JSから取得（2026-09-27）
# 旧 bosai/warning/data/warning/*.json と XML VPWW53 系は2026-05-29の新・防災気象情報移行後は使わない
WARN_CODES={
 "33":("大雨",5,"レベル5大雨特別警報"),"43":("大雨",4,"レベル4大雨危険警報"),"03":("大雨",3,"レベル3大雨警報"),"10":("大雨",2,"レベル2大雨注意報"),
 "39":("土砂災害",5,"レベル5土砂災害特別警報"),"49":("土砂災害",4,"レベル4土砂災害危険警報"),"09":("土砂災害",3,"レベル3土砂災害警報"),"29":("土砂災害",2,"レベル2土砂災害注意報"),
 "38":("高潮",5,"レベル5高潮特別警報"),"48":("高潮",4,"レベル4高潮危険警報"),"08":("高潮",3,"レベル3高潮警報"),"19":("高潮",2,"レベル2高潮注意報"),
 "35":("暴風",5,"暴風特別警報"),"05":("暴風",3,"暴風警報"),"15":("暴風",2,"強風注意報"),
 "32":("暴風雪",5,"暴風雪特別警報"),"02":("暴風雪",3,"暴風雪警報"),"13":("暴風雪",2,"風雪注意報"),
 "36":("大雪",5,"大雪特別警報"),"06":("大雪",3,"大雪警報"),"12":("大雪",2,"大雪注意報"),
 "37":("波浪",5,"波浪特別警報"),"07":("波浪",3,"波浪警報"),"16":("波浪",2,"波浪注意報"),
 "14":("雷",2,"雷注意報"),"17":("融雪",2,"融雪注意報"),"20":("濃霧",2,"濃霧注意報"),"21":("乾燥",2,"乾燥注意報"),
 "22":("なだれ",2,"なだれ注意報"),"23":("低温",2,"低温注意報"),"24":("霜",2,"霜注意報"),"25":("着氷",2,"着氷注意報"),"26":("着雪",2,"着雪注意報"),
}
def get_warnings(pref, place, muni=None):
    """新・防災気象情報JSON(r8)から、市町村名に place を含む地区の発表中 警報/注意報 を返す。
    戻り: (rdt, (名称リスト, 地区名リスト))。取得失敗 (None,None)"""
    try:
        cls=_get("https://www.jma.go.jp/bosai/common/const/area.json")["class20s"]
        codes={}
        if muni:
            codes={k:v["name"] for k,v in cls.items() if k.startswith(muni)}
            if not codes: codes={k:v["name"] for k,v in cls.items() if k.startswith(muni[:3]+"00")}   # 政令市の区→市
        if not codes: codes={k:v["name"] for k,v in cls.items() if k.startswith(pref[:2]) and place and (place in v["name"] or v["name"] in place)}
        data=_get(f"https://www.jma.go.jp/bosai/warning/data/r8/{pref}.json")
    except Exception as e:
        print(f"(警報取得失敗 {e})"); return None,None
    if not codes: return "?", ([], [])
    rdt="?"; cur={}
    for r in data:
        for it in r.get("warning",{}).get("class20Items",[]):
            if it.get("areaCode") not in codes: continue
            rd=r.get("reportDatetime","?"); rdt=rd if rdt=="?" else max(rdt,rd)
            for k in it.get("kinds",[]):
                c=k.get("code")
                if c not in WARN_CODES or k.get("status")=="解除": continue
                el,lv,nm=WARN_CODES[c]
                if el not in cur or lv>cur[el][0]: cur[el]=(lv,nm)
    kinds=[v[1] for v in sorted(cur.values(),key=lambda v:-v[0])]
    return rdt, (kinds, list(codes.values()))

def get_overview(pref):
    try:
        d=_get(f"{OVERVIEW}/{pref}.json")
        return d.get("reportDatetime"), d.get("publishingOffice"), d.get("text","").strip()
    except Exception as e:
        return None,None,f"(取得失敗 {e})"

WD="月火水木金土日"
def fmt_amedas(code,name,now,days):
    print(f"{name}（{code}）/ 速報値最新 {now:%Y-%m-%d %H:%M} JST")
    bh=(now.hour//3)*3
    try:
        j=_get(f"{DATA}/point/{code}/{now:%Y%m%d}_{bh:02d}.json")
        last=sorted(j.keys())[-1]; r=j[last]
        def g(k):
            v=val(r.get(k)); return "—" if v is None else f"{v}"
        print(f"[最新 {last[8:10]}:{last[10:12]}] 10分={g('precipitation10m')}mm "
              f"1時間={g('precipitation1h')}mm 24時間積算={g('precipitation24h')}mm")
    except Exception as e:
        print("最新値取得失敗:",e)
    start=(now-datetime.timedelta(days=days-1)).replace(hour=0,minute=0,second=0,microsecond=0)
    data=collect_10m(code,start,now); daily={}; cnt={}
    for ts,mm in data.items():
        dd=ts[:8]; daily[dd]=daily.get(dd,0.0)+mm; cnt[dd]=cnt.get(dd,0)+1
    print(f"日別降水量（直近{days}日・速報値）:")
    for dd in sorted(daily):
        dt=datetime.datetime.strptime(dd,"%Y%m%d")
        note="" if cnt[dd]>=140 else f"  ※{cnt[dd]}コマ(当日途中/欠測)"
        print(f"  {dt:%m/%d}({WD[dt.weekday()]}) {daily[dd]:6.1f} mm{note}")
    print(f"期間合計(参考): {sum(daily.values()):.1f} mm")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("place", nargs="?")
    ap.add_argument("--code"); ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--outlook", action="store_true", help="警報現況＋気象台見通し文も表示")
    ap.add_argument("--pref", help="府県予報区コード or 県名（既定:東京130000）")
    ap.add_argument("--site", nargs="?", const="", default=None,
                    help="現場グリッド1kmの解析雨量＋キキクル。省略時は現場既定座標、'lat,lon'指定可")
    ap.add_argument("--wbgt", action="store_true",
                    help="推定WBGT(屋外)。既定時刻8:30/13:00、--times 'H:MM,H:MM'で変更可")
    ap.add_argument("--times", default="8:30,13:00", help="--wbgt の対象時刻(カンマ区切り)")
    a=ap.parse_args()
    ziploc=None; zipcity=None; zipmuni=None
    def _apply(loc):
        nonlocal ziploc, zipcity, zipmuni
        c,n,la,lo,addr,pc,mu=loc
        a.code=a.code or c; ziploc=(la,lo); zipcity=addr; zipmuni=mu
        if a.pref is None and pc: a.pref=pc
        if a.site=="": a.site=f"{la},{lo}"
    if a.place and (is_zip(a.place) or is_latlon(a.place)):
        _apply(locate(a.place))
    elif a.place and not a.code and not a.list and not resolve(a.place):
        loc=locate(a.place)   # 観測所名に無ければ住所として解決
        if loc: _apply(loc)

    if a.wbgt:
        code=a.code
        if not code and a.place:
            hits=resolve(a.place)
            exact=[h for h in hits if h[1]==a.place]
            if len(hits)==1: code=hits[0][0]
            elif len(exact)==1: code=exact[0][0]
            elif hits:
                print(f"「{a.place}」候補 {len(hits)}件 → --code で指定:")
                for c,kj,kn in hits[:20]: print(f"  {c}  {kj}（{kn}）")
                return
            else:
                print(f"観測所が見つからない: {a.place}"); sys.exit(1)
        if not re.match(r'^\d{1,2}:\d{2}(,\d{1,2}:\d{2})*$', a.times.replace(" ","")):
            print(f"--times 形式エラー: {a.times}（例: 8:30,13:00）"); sys.exit(1)
        wbgt_report(code or "44132", a.days, a.times)
        return

    code=a.code; name=a.code
    if ziploc and not a.list: name=next((v.get("kjName") for k,v in load_table().items() if k==code), code)
    if not code and a.place:
        hits=resolve(a.place)
        if not hits: print(f"観測所が見つからない: {a.place}"); sys.exit(1)
        if a.list or len(hits)>1:
            print(f"「{a.place}」候補 {len(hits)}件:")
            for c,kj,kn in hits[:40]: print(f"  {c}  {kj}（{kn}）")
            if len(hits)>1 and not a.list: print("→ --code で1つ指定")
            return
        code,name,_=hits[0]
    if not code: print("地名 か --code を指定"); sys.exit(1)

    now=latest_time()
    print("="*56)
    fmt_amedas(code,name,now,a.days)

    if a.outlook:
        pref=a.pref or "130000"
        if pref in PREF: pref=PREF[pref]
        print("="*56)
        wplace=zipcity or a.place
        if not wplace and code:   # --code指定時は観測所の漢字名で市町村を引く
            wplace=(load_table().get(str(code),{}).get("kjName") or "")
        rdt,w=get_warnings(pref, wplace, zipmuni)
        if w is None:
            print("【現況 警報・注意報】取得失敗")
        else:
            kinds,areas=w
            ar="／".join(areas) if areas else "(該当地区未特定)"
            print(f"【現況 警報・注意報】{rdt}  対象: {ar}")
            print("  " + ("・".join(kinds) if kinds else "発表なし"))
        print("-"*56)
        odt,off,text=get_overview(pref)
        print(f"【気象台 見通し】{off or ''} {odt or ''}")
        print(text[:1000])
        print("-"*56)
        print("※解除時刻は予測不可（土壌雨量指数の残留で『最短でも当面下がらない』下限のみ推測）")

    if a.site is not None:
        print("="*56)
        if a.site=="":
            if GENBA is None:
                sys.exit("--site には lat,lon を指定してください（例: --site 33.26,129.64）")
            lat,lon=GENBA
        else:
            lat,lon=[float(v) for v in a.site.split(",")]
        site_report(lat,lon)

# ============ --site: 現場座標の1kmタイル画素サンプリング ============
import math, io
try:
    from PIL import Image
    _PIL=True
except Exception:
    _PIL=False

GENBA=None  # 既定座標なし。--site lat,lon で指定
TILEZ=10

# 解析雨量(hrpns) 色→mm/h
PRECIP=[((160,210,255),"1〜5mm/h"),((33,140,255),"5〜10mm/h"),((0,65,255),"10〜20mm/h"),
        ((250,245,0),"20〜30mm/h"),((255,153,0),"30〜50mm/h"),((255,40,0),"50〜80mm/h"),
        ((180,0,104),"80mm/h〜")]
# キキクル 色→警戒レベル
KIKI=[((242,231,0),"レベル2 注意(黄)"),((255,40,0),"レベル3 警戒(赤)=警報級"),
      ((170,0,255),"レベル4 非常に危険(紫)=警戒情報級"),((12,0,64),"レベル5 極めて危険(黒紫)=特別警報級")]

def _tilexy(lat,lon,z):
    n=2**z
    xt=(lon+180)/360*n
    yt=(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n
    tx,ty=int(xt),int(yt)
    return tx,ty,int((xt-tx)*256),int((yt-ty)*256)

def _nearest(rgb,table,thr=70):
    best=None;bd=1e9
    for c,lab in table:
        d=sum((a-b)**2 for a,b in zip(rgb,c))**0.5
        if d<bd:bd=d;best=lab
    return best if bd<=thr else None

def _sample_color(lat,lon,z,url):
    if not _PIL: return ("__noPIL__",None)
    tx,ty,px,py=_tilexy(lat,lon,z)
    u=url.format(z=z,x=tx,y=ty)
    try:
        raw=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=20).read()
        img=Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as e:
        return ("__err__",str(e))
    px=min(px,img.width-1);py=min(py,img.height-1)
    # 3x3で最頻の不透明色
    cnt={}
    for dx in (-1,0,1):
        for dy in (-1,0,1):
            p=img.getpixel((min(max(px+dx,0),img.width-1),min(max(py+dy,0),img.height-1)))
            if p[3]>0: cnt[p[:3]]=cnt.get(p[:3],0)+1
    if not cnt: return (None,None)  # 透明=値なし
    return (max(cnt,key=cnt.get),None)

def _jst(ymdhms):
    dt=datetime.datetime.strptime(ymdhms,"%Y%m%d%H%M%S")+datetime.timedelta(hours=9)
    return dt.strftime("%m/%d %H:%M")

def site_report(lat,lon):
    if not _PIL:
        print("PIL未導入のため --site 不可"); return
    nowc=_get("https://www.jma.go.jp/bosai/jmatile/data/nowc/targetTimes_N1.json")[0]
    risk=_get("https://www.jma.go.jp/bosai/jmatile/data/risk/targetTimes.json")[0]
    nb,nv=nowc["basetime"],nowc["validtime"]
    rb,rm,rv=risk["basetime"],risk.get("member","immed0"),risk["validtime"]
    print(f"【現場グリッド 1km】({lat},{lon})  解析雨量 {_jst(nv)} / キキクル {_jst(rv)} JST")
    # 解析雨量
    c,err=_sample_color(lat,lon,TILEZ,
        f"https://www.jma.go.jp/bosai/jmatile/data/nowc/{nb}/none/{nv}/surf/hrpns/{{z}}/{{x}}/{{y}}.png")
    if c is None: rain="降水なし(<1mm/h)"
    elif isinstance(c,str): rain=f"取得不可({err})"
    else: rain=_nearest(c,PRECIP) or f"不明色{c}"
    print(f"  解析雨量(現場格子) : {rain}")
    # キキクル3種
    for elem,jp in (("rain_mesh","表面雨量(大雨・浸水害)"),("flood_mesh","流域雨量(洪水)"),("land","土壌雨量(土砂災害)")):
        c,err=_sample_color(lat,lon,TILEZ,
            f"https://www.jma.go.jp/bosai/jmatile/data/risk/{rb}/{rm}/{rv}/surf/{elem}/{{z}}/{{x}}/{{y}}.png")
        if c is None: lv="レベル1以下(色なし)"
        elif isinstance(c,str): lv=f"取得不可({err})"
        else: lv=_nearest(c,KIKI) or f"不明色{c}"
        print(f"  {jp:<22}: {lv}")
    print("※タイル色のビン値。正確な数値ではないが現場ピンポイントの警戒レベル把握用")

# ============ --wbgt: 推定WBGT(屋外) ============
# 気温・湿度の観測がない地点は近傍の観測所で代替できる（例: 諫早→大村/長崎）。
# 式: WBGT = 0.7×Tw + 0.2×Tg + 0.1×Ta (Tg=Ta基礎) + 日射補正Δ
#   Tw = Stull(2011)近似(気温+湿度)
#   Δ  = 環境省公式WBGT実況 − 同式で計算した値 (負は0)。地域の日射効果を転用。
# 精度: 曇雨天日は公式とΔ±0.2で一致確認済(2026-07-03)。推定値につき現場管理の正はWBGT計実測。
WBGT_PROXY={
    # 対象code: (気温src bosai, etrn(type,prec_no,block), 湿度src bosai, etrn(type,prec_no,block), 公式WBGT地点)
    "44132": ("44132", ("s1","44","47662"), "44132", ("s1","44","47662"), "44132"),  # 東京
    "45212": ("45212", ("s1","45","47682"), "45212", ("s1","45","47682"), "45212"),  # 千葉
    "84496": ("84496", ("s1","84","47817"), "84496", ("s1","84","47817"), "84496"),  # 長崎
    "84441": ("84371", ("a1","84","1084"), "84496", ("s1","84","47817"), "84496"),  # 諫早(雨量計のみ→大村/長崎で代替)
}
ETRN="https://www.data.jma.go.jp/obd/stats/etrn/view/10min_{t}.php?prec_no={p}&block_no={b}&year={y}&month={m:02d}&day={d:02d}&view="
ENVCSV="https://www.wbgt.env.go.jp/est15WG/dl/wbgt_{code}_{ym}.csv"

def _stull_tw(t,h):
    return (t*math.atan(0.151977*math.sqrt(h+8.313659))
            +math.atan(t+h)-math.atan(h-1.676331)
            +0.00391838*h**1.5*math.atan(0.023101*h)-4.686035)

def _wbgt_base(t,h):
    if t is None or h is None: return None
    return 0.7*_stull_tw(t,h)+0.3*t  # Tg=Ta時: 0.7Tw+0.2Ta+0.1Ta

def _wbgt_rank(v):
    if v is None: return ""
    for lim,lab in ((31,"危険"),(28,"厳重警戒"),(25,"警戒"),(21,"注意")):
        if v>=lim: return lab
    return "ほぼ安全"

def _etrn_10min(typ,prec,block,day):
    html=urllib.request.urlopen(urllib.request.Request(
        ETRN.format(t=typ,p=prec,b=block,y=day.year,m=day.month,d=day.day),
        headers={"User-Agent":"Mozilla/5.0"}),timeout=20).read().decode("utf-8",errors="replace")
    rows={}
    for tr in re.findall(r'<tr[^>]*class="mtx"[^>]*>(.*?)</tr>', html, re.S):
        cells=[re.sub(r'<[^>]+>','',c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if cells and re.match(r'^\d{2}:\d{2}$', cells[0]): rows[cells[0]]=cells
    return rows

def _fnum(s):
    m=re.match(r'^-?\d+(\.\d+)?', s or '')
    return float(m.group(0)) if m else None

def _etrn_th(typ,rows,hm):
    """etrn 10分値行から(気温,湿度)。a1: 気温=2 湿度なし / s1: 気温=4 湿度=5"""
    c=rows.get(hm)
    if not c: return None,None
    if typ=="a1": return _fnum(c[2]), None
    return _fnum(c[4]), _fnum(c[5])

def _bosai_th(code,day,hm):
    hh,mm=hm.split(":")
    blk=(int(hh)//3)*3
    try: j=_get(f"{DATA}/point/{code}/{day:%Y%m%d}_{blk:02d}.json")
    except Exception: return None,None
    r=j.get(f"{day:%Y%m%d}{int(hh):02d}{mm}00",{})
    return val(r.get("temp")), val(r.get("humidity"))

def _env_official(code,months):
    """環境省公式WBGT実況CSV {(YYYY/M/D,'H:00'):val}。サービス期間外(11-3月)は空。"""
    off={}
    for ym in months:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(
                ENVCSV.format(code=code,ym=ym),headers={"User-Agent":"Mozilla/5.0"}),timeout=20).read()
            for line in raw.decode("utf-8-sig",errors="replace").splitlines()[1:]:
                p=[x.strip() for x in line.split(",")]
                if len(p)>=3 and p[2]:
                    try: off[(p[0],p[1])]=float(p[2])
                    except ValueError: pass
        except Exception: pass
    return off

def wbgt_report(code,days,times):
    t=load_table()
    cfg=WBGT_PROXY.get(code)
    if not cfg:
        # 汎用: 対象観測所に気温(elems[0])と湿度(elems[5])があれば自前、無ければ最寄りの気温湿度観測所で代替。過去日も bosai 速報値(約10日)で賄う
        def _th_ok(v):
            e=str(v.get("elems","")); return len(e)>=6 and e[0]=="1" and e[5]=="1"
        v0=t.get(code) or {}
        if _th_ok(v0): src=code
        else:
            la0=v0["lat"][0]+v0["lat"][1]/60; lo0=v0["lon"][0]+v0["lon"][1]/60; best=None
            for c,v in t.items():
                if not _th_ok(v): continue
                la=v["lat"][0]+v["lat"][1]/60; lo=v["lon"][0]+v["lon"][1]/60
                d=math.hypot((la-la0)*111,(lo-lo0)*111*math.cos(math.radians(la0)))
                if best is None or d<best[0]: best=(d,c)
            if not best: print("気温・湿度を観測する近傍観測所が見つからない"); return
            src=best[1]; print(f"※{v0.get('kjName',code)}は気温/湿度の観測なし→最寄り {t[src]['kjName']}({src}) 約{best[0]:.0f}km で代替")
        cfg=(src,None,src,None,src)
    tsrc,tetrn,hsrc,hetrn,offcode=cfg
    names={c:(t.get(c) or {}).get("kjName",c) for c in (code,tsrc,hsrc)}
    today=latest_time().date()
    start=today-datetime.timedelta(days=days-1)
    months=set(); d=start.replace(day=1)
    while d<=today:
        months.add(f"{d.year}{d.month:02d}")
        d=(d+datetime.timedelta(days=32)).replace(day=1)
    months=sorted(months)
    off=_env_official(offcode,months)
    tlist=[s.strip() for s in times.split(",") if s.strip()]
    print(f"推定WBGT(屋外) 対象={names[code]}  気温={names[tsrc]}/湿度={names[hsrc]}で代替  {start:%m/%d}〜{today:%m/%d}")
    print(f"式: 0.7Tw+0.2Tg+0.1Ta (Tw=Stull近似, Tg=Ta) ＋ 日射補正(環境省公式{names[hsrc]}比)")
    hdr="  ".join(f"{tm:>5}" for tm in tlist)
    print(f"{'日付':<9}   {hdr}   区分(最終時刻)")
    day=start
    while day<=today:
        cells=[]
        use_etrn = day<today and tetrn is not None
        erows_t=erows_h=None
        if use_etrn:
            try:
                erows_t=_etrn_10min(*tetrn,day)
                erows_h=erows_t if (tsrc==hsrc) else _etrn_10min(*hetrn,day)
            except Exception:
                use_etrn=False
        for tm in tlist:
            hm=f"{int(tm.split(':')[0]):02d}:{tm.split(':')[1]}"
            if use_etrn and erows_t is not None:
                ta,_=_etrn_th(tetrn[0],erows_t,hm)
                th,hu=_etrn_th(hetrn[0],erows_h,hm)
            else:
                ta,_=_bosai_th(tsrc,day,hm)
                th,hu=_bosai_th(hsrc,day,hm)
            base=_wbgt_base(ta,hu)
            # 日射補正: 公式(前後正時の平均) − 同式(湿度観測点の気温で計算)
            est=base
            if base is not None:
                ref=_wbgt_base(th,hu)
                h0=int(hm.split(":")[0]); mnt=int(hm.split(":")[1])
                keys=[f"{h0}:00"] if mnt==0 else [f"{h0}:00",f"{h0+1}:00"]
                ds=f"{day.year}/{day.month}/{day.day}"
                ov=[off.get((ds,k)) for k in keys]; ov=[v for v in ov if v is not None]
                if ref is not None and ov:
                    est=base+max(0.0,sum(ov)/len(ov)-ref)
            cells.append(est)
        s="  ".join(f"{v:5.1f}" if v is not None else "   --" for v in cells)
        lastv=next((v for v in reversed(cells) if v is not None),None)
        print(f"{day:%m/%d}({WD[day.weekday()]})  {s}   {_wbgt_rank(lastv)}")
        day+=datetime.timedelta(days=1)
    print(f"※推定値({names[tsrc]}気温×{names[hsrc]}湿度合成)。区分: 〜21ほぼ安全/21〜25注意/25〜28警戒/28〜31厳重警戒/31〜危険")
    print("※観測所の気温・湿度から算出した推定値であり、指定地点そのものの観測ではない（近隣でも数度ずれうる）。参考程度にとどめ、現場の管理基準は黒球付きWBGT計の実測が正。環境省CSVは4〜10月のみ(期間外は日射補正なし)")

if __name__=="__main__":
    main()
