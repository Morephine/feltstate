"""feltstate.dashboard — a read-only window onto a Canon: the web, walkable.

Run it against your own store and open the page::

    python -m feltstate.dashboard --canon canon.jsonl [--state state.json] [--port 8765]

Three views, one file, zero writes:

* **Web** — the key web as a force graph: node size is current salience,
  edges are judged ``relates`` kinships (hover shows the judge's *why*).
  Click a node and its side panel opens — 5W1H, keys, kin — and every kin
  row is a step: click through the graph the way recall walks it.
* **Search** — words collide with keys (``Canon.reach``): hits light up in
  the graph, ordered by event time, the newest standing as the present.
* **State** — if you pass your engine's ``state.json``, the persisted affect
  (mood, pressure bars, traits, relationship) renders as bars.

This is deliberately a *viewer*, not a console: it reads the store the same
way recall does and can change nothing. The graph uses vis-network from a
CDN; without network the web tab degrades to a message while search and
state keep working.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .memory.canon import Canon

__all__ = ["graph_payload", "main", "node_payload"]


# --------------------------------------------------------------------------- #
# Payload builders — pure functions over rendered rows, tested directly       #
# --------------------------------------------------------------------------- #
def _rows(canon: Canon) -> list[dict]:
    return canon.view(include_archived=True)


def graph_payload(rows: list[dict]) -> dict[str, Any]:
    """Nodes for every keyed fact, edges for every judged kinship among them."""
    keyed = [r for r in rows if r.get("keys")]
    byid = {r["id"]: r for r in keyed}
    nodes, edges, seen = [], [], set()
    for r in keyed:
        nodes.append(
            {
                "id": r["id"],
                "date": str(r.get("valid_at") or r.get("ts") or "")[:10],
                "label": (r.get("object") or r.get("action") or "")[:18],
                "value": max(1.0, float(r.get("intensity") or 0.3) * 10),
                "group": r.get("actor") or "?",
                "title": (
                    f"[{str(r.get('valid_at') or '')[:16]}] {r.get('actor', '')} "
                    f"{r.get('action', '')}\n{r.get('object', '')}\n"
                    f"keys: {' '.join(r.get('keys') or [])}"
                ),
            }
        )
        for edge in r.get("relates") or []:
            if not isinstance(edge, dict):
                continue
            to = str(edge.get("to") or "")
            if to in byid:
                pair = tuple(sorted((r["id"], to)))
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append(
                    {
                        "from": r["id"],
                        "to": to,
                        "date": str(edge.get("ts") or "")[:10],
                        "title": str(edge.get("why") or ""),
                    }
                )
    return {"nodes": nodes, "edges": edges, "count": len(nodes), "edge_count": len(edges)}


def node_payload(rows: list[dict], cid: str) -> dict[str, Any]:
    """One fact's card plus its kin list (both edge directions), walk-ready."""
    byid = {r["id"]: r for r in rows}
    me = byid.get(cid)
    if me is None:
        return {"brief": None, "kin": []}
    kin: list[dict] = []
    seen: set[str] = set()

    def _add(other_id: str, why: str) -> None:
        other = byid.get(other_id)
        if other is None or other_id in seen:
            return
        seen.add(other_id)
        kin.append(
            {
                "cid": other_id,
                "when": str(other.get("valid_at") or "")[:16],
                "actor": other.get("actor") or "",
                "object": (other.get("object") or "")[:70],
                "why": why,
            }
        )

    for edge in me.get("relates") or []:
        if isinstance(edge, dict) and edge.get("to"):
            _add(str(edge["to"]), str(edge.get("why") or ""))
    for r in rows:
        for edge in r.get("relates") or []:
            if isinstance(edge, dict) and str(edge.get("to")) == cid:
                _add(r["id"], str(edge.get("why") or ""))
    kin.sort(key=lambda k: k["when"])
    return {"brief": me, "kin": kin}


def state_payload(state_path: str | None) -> dict[str, Any]:
    if not state_path:
        return {"state": None}
    try:
        return {"state": json.loads(Path(state_path).read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"state": None}


# --------------------------------------------------------------------------- #
# The page                                                                    #
# --------------------------------------------------------------------------- #
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>feltstate · memory window</title>
<script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
<style>
 body{margin:0;background:#14121f;color:#d8d4ee;font:14px/1.5 system-ui}
 header{padding:10px 18px;background:#1d1930;display:flex;gap:14px;align-items:center;border-bottom:1px solid #322a52}
 header h1{font-size:16px;margin:0;color:#b9a6ff}
 main{padding:12px 18px}
 .row{display:flex;gap:8px;margin-bottom:10px;align-items:center}
 input{background:#221d3a;border:1px solid #3a2f63;color:#eee;border-radius:6px;padding:6px 10px;width:260px}
 button{background:#4b3a8c;color:#fff;border:0;border-radius:6px;padding:6px 14px;cursor:pointer}
 .wrap{display:flex;gap:12px}
 #net{flex:1;height:72vh;border:1px solid #322a52;border-radius:8px;background:#171426}
 #side{width:400px;max-height:72vh;overflow:auto;border:1px solid #322a52;border-radius:8px;background:#1a1630;padding:12px;display:none}
 #side h3{margin:4px 0;color:#b9a6ff}
 .kv{font-size:13px;margin:2px 0}.kv b{color:#ffd166}
 .kin{border-left:3px solid #4b3a8c;background:#221d3a;margin:5px 0;padding:5px 9px;border-radius:0 6px 6px 0;cursor:pointer}
 .kin:hover{background:#2c2550}.kin small{color:#8f86b8}
 .bar{height:10px;background:#2a2347;border-radius:5px;overflow:hidden;margin:2px 0 8px}
 .bar i{display:block;height:100%;background:linear-gradient(90deg,#6c5ce7,#b9a6ff)}
 .muted{color:#8f86b8;font-size:12px}
 #state{max-width:520px}
</style></head><body>
<header><h1>feltstate · memory window</h1><span class="muted" id="meta"></span>
 <button style="margin-left:auto" onclick="boot()">refresh</button></header>
<main>
 <div class="row">
  <input id="q" placeholder="words collide with keys — Enter" onkeydown="if(event.key=='Enter')hunt()">
  <button onclick="hunt()">light up</button>
 </div>
 <div class="wrap"><div id="net"></div><div id="side"></div></div>
 <div id="state"></div>
 <p class="muted">read-only · node size = current salience · edges = judged kinship (hover: the why) ·
 click a node, then walk its kin</p>
</main>
<script>
async function j(u){const r=await fetch(u);return r.json();}
let NET=null,DS=null;
async function boot(){
 const g=await j('/api/graph');
 document.getElementById('meta').textContent=g.count+' facts · '+g.edge_count+' edges';
 if(typeof vis=='undefined'){document.getElementById('net').innerHTML=
  '<p class=muted style="padding:20px">vis-network CDN unreachable — search and state still work.</p>';}
 else{
  DS={nodes:new vis.DataSet(g.nodes),edges:new vis.DataSet(g.edges)};
  const opt={nodes:{shape:'dot',font:{color:'#d8d4ee',size:12}},edges:{color:{color:'#4b3a8c'},width:1.5},
   physics:{solver:'forceAtlas2Based',stabilization:{iterations:120}},interaction:{hover:true}};
  if(NET)NET.destroy();
  NET=new vis.Network(document.getElementById('net'),DS,opt);
  NET.on('click',p=>{if(p.nodes.length)openSide(p.nodes[0]);});
 }
 loadState();
}
async function hunt(){
 const q=document.getElementById('q').value.trim();if(!q)return;
 const r=await j('/api/reach?q='+encodeURIComponent(q));
 const ids=(r.chain||[]).map(c=>c.id).filter(id=>DS&&DS.nodes.get(id));
 document.getElementById('meta').textContent=(r.chain||[]).length+' in chain · newest is the present';
 if(NET&&ids.length){NET.selectNodes(ids);NET.fit({nodes:ids,animation:true});openSide(ids[ids.length-1],true);}
}
async function openSide(cid,isCurrent){
 const d=await j('/api/node?cid='+encodeURIComponent(cid));
 const b=d.brief;if(!b)return;
 const s=document.getElementById('side');s.style.display='block';
 let h='<h3>'+(isCurrent?'⭐ present · ':'')+(b.object||b.action)+'</h3>';
 h+='<div class=kv><b>when</b> '+String(b.valid_at||'').slice(0,16)+' · <b>who</b> '+(b.actor||'')+
    ' · <b>salience</b> '+(b.intensity??'?')+'</div>';
 if(b.why)h+='<div class=kv><b>why</b> '+b.why+'</div>';
 if(b.keys&&b.keys.length)h+='<div class=kv><b>keys</b> '+b.keys.join(' · ')+'</div>';
 if(b.valence!=null)h+='<div class=kv><b>felt</b> valence '+b.valence+' · charge '+(b.charge??'?')+'</div>';
 if(b.sources&&b.sources.length)h+='<div class=kv><b>sources</b> '+b.sources.join(', ')+'</div>';
 h+='<h3>kin ('+d.kin.length+')</h3>';
 for(const k of d.kin){
  h+='<div class=kin onclick="walk(\\''+k.cid+'\\')"><small>'+k.when+' · '+k.actor+
     (k.why?' · '+k.why:'')+'</small><br>'+k.object+'</div>';
 }
 s.innerHTML=h;
}
function walk(cid){
 if(NET&&DS&&DS.nodes.get(cid)){NET.selectNodes([cid]);NET.focus(cid,{scale:1.1,animation:true});}
 openSide(cid);
}
async function loadState(){
 const s=await j('/api/state');const box=document.getElementById('state');
 if(!s.state){box.innerHTML='';return;}
 function bar(lab,v){v=Math.max(0,Math.min(1,Number(v)||0));return '<div>'+lab+
  ' <span class=muted>'+v.toFixed(2)+'</span><div class=bar><i style="width:'+(v*100)+'%"></i></div></div>';}
 function walkObj(prefix,o,out){
  for(const k of Object.keys(o||{})){
   const v=o[k];
   if(typeof v=='number'&&!k.endsWith('_ts')&&!k.startsWith('_'))out.push(bar(prefix+k,v));
   else if(v&&typeof v=='object'&&!Array.isArray(v)&&prefix.split('·').length<2)walkObj(prefix+k+'·',v,out);
  }
 }
 const out=['<h3>persisted state</h3>'];walkObj('',s.state,out);
 box.innerHTML=out.slice(0,40).join('');
}
boot();
</script></body></html>"""


# --------------------------------------------------------------------------- #
# Server — thin, read-only                                                    #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="read-only window onto a feltstate Canon")
    ap.add_argument("--canon", required=True, help="path to the canon JSONL store")
    ap.add_argument("--state", default=None, help="optional engine state.json to render")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    canon = Canon(args.canon)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    body = PAGE.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif u.path == "/api/graph":
                    body = json.dumps(graph_payload(_rows(canon))).encode()
                    ctype = "application/json"
                elif u.path == "/api/node":
                    cid = (q.get("cid", [""])[0] or "").strip()
                    body = json.dumps(node_payload(_rows(canon), cid)).encode()
                    ctype = "application/json"
                elif u.path == "/api/reach":
                    word = (q.get("q", [""])[0] or "").strip()
                    got = canon.reach(*word.split()) if word else {"chain": []}
                    body = json.dumps(got).encode()
                    ctype = "application/json"
                elif u.path == "/api/state":
                    body = json.dumps(state_payload(args.state)).encode()
                    ctype = "application/json"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"feltstate memory window: http://{args.host}:{args.port}/  (read-only)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
