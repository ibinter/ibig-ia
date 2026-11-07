"""Audience : réception des visites envoyées par les sites du groupe + page d'analyse (direction).

Routes publiques volontairement limitées : `GET /t.js` (le petit script posé sur chaque site) et
`POST /api/t` (réception d'un événement, réponse vide). Tout le reste exige une session.
"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from ..auth import Principal
from ..runtime import Runtime

SCRIPT = r"""(function(){
var E="__ENDPOINT__",S=location.hostname;
if(navigator.doNotTrack==="1"||/[?&]notrack=1/.test(location.search))return;
function send(ev,lab){try{var b=JSON.stringify({s:S,p:location.pathname,e:ev,l:(lab||"").slice(0,120),r:document.referrer||""});
if(navigator.sendBeacon){navigator.sendBeacon(E,new Blob([b],{type:"text/plain"}));}
else{fetch(E,{method:"POST",body:b,keepalive:true,mode:"no-cors"});}}catch(x){}}
var last="";function view(){var k=location.pathname+location.search;if(k===last)return;last=k;send("pageview","");}
view();
["pushState","replaceState"].forEach(function(m){var o=history[m];history[m]=function(){var r=o.apply(this,arguments);setTimeout(view,50);return r;};});
addEventListener("popstate",function(){setTimeout(view,50);});
document.addEventListener("click",function(ev){var a=ev.target.closest&&ev.target.closest("a,button,[data-track]");if(!a)return;
var t=a.getAttribute("data-track"),h=a.getAttribute("href")||"";
if(t){send("click",t);}
else if(/^tel:/i.test(h)){send("click","appel");}
else if(/^mailto:/i.test(h)){send("click","email");}
else if(/wa\.me|whatsapp/i.test(h)){send("click","whatsapp");}
else if(a.tagName==="A"&&/\.(pdf|docx?|xlsx?|zip)(\?|$)/i.test(h)){send("click","telechargement");}},true);
document.addEventListener("submit",function(ev){var f=ev.target;send("form",(f.getAttribute("data-track")||f.getAttribute("id")||f.getAttribute("name")||"formulaire"));},true);
})();"""


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    @app.get("/t.js")
    def tracker_script(request: Request):
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        base = f"{proto}://{request.headers.get('host', request.url.netloc)}"
        body = SCRIPT.replace("__ENDPOINT__", base + "/api/t")
        return Response(body, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=3600",
                                 "Access-Control-Allow-Origin": "*"})

    @app.post("/api/t")
    async def receive(request: Request):
        raw = (await request.body())[:2000]
        try:
            d = json.loads(raw.decode("utf-8", "ignore"))
            ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                  or (request.client.host if request.client else ""))
            rt.analytics.record(str(d.get("s", "")), str(d.get("p", "/")), str(d.get("e", "pageview")),
                                str(d.get("l", "")), str(d.get("r", "")),
                                request.headers.get("user-agent", ""), ip)
        except (ValueError, TypeError, AttributeError):
            pass
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})

    @app.get("/audience", response_class=HTMLResponse)
    def audience(request: Request, site: str = "", days: int = 7, who: Principal = Depends(user)):
        if who.role == "valideur":
            raise HTTPException(403)
        days = days if days in (1, 7, 30, 90) else 7
        rows = rt.analytics.overview(days)
        selected = site if any(r["site"] == site for r in rows) else (rows[0]["site"] if rows else "")
        detail = rt.analytics.detail(selected, days) if selected else None
        return page(request, "audience.html", rows=rows, selected=selected, detail=detail, days=days,
                    total_views=sum(r["views"] for r in rows), total_visitors=sum(r["visitors"] for r in rows))

    @app.get("/audience/installation", response_class=PlainTextResponse)
    def install_snippet(request: Request, who: Principal = Depends(user)):
        if who.role == "valideur":
            raise HTTPException(403)
        host = request.headers.get("host", request.url.netloc)
        return f'<script defer src="https://{host}/t.js"></script>'
