"""Mini App front end, served inline by bot.py."""

PAGE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>SST</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{
  --bg:#0b0e14; --bg2:#111621; --bg3:#161d2b; --line:#1e2738;
  --fg:#c8d3e6; --dim:#6b7a94; --faint:#3c485c;
  --accent:#4a9eff; --accent2:#2d7fd8;
  --ok:#3ddc84; --warn:#ffb340; --err:#ff5c5c;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;
  --ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;
  --r:12px; --safe:env(safe-area-inset-bottom,0px);
}
html[data-theme="light"]{
  --bg:#f6f8fc; --bg2:#fff; --bg3:#eef2f8; --line:#dde4ee;
  --fg:#1b2432; --dim:#69778c; --faint:#aab5c5;
  --accent:#1d6fd0; --accent2:#155bb0;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0;overscroll-behavior:none}
body{
  background:var(--bg);color:var(--fg);font-family:var(--ui);
  display:flex;flex-direction:column;overflow:hidden;
}

/* ---------- top bar ---------- */
.bar{
  display:flex;align-items:center;gap:10px;padding:10px 14px;
  background:var(--bg2);border-bottom:1px solid var(--line);flex:0 0 auto;
}
.dot{width:8px;height:8px;border-radius:50%;background:var(--faint);flex:0 0 auto;
     transition:background .25s, box-shadow .25s}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.busy{background:var(--warn);box-shadow:0 0 8px var(--warn);animation:pulse 1s infinite}
.dot.off{background:var(--err);box-shadow:0 0 8px var(--err)}
@keyframes pulse{50%{opacity:.35}}
.title{font-size:13px;font-weight:600;letter-spacing:.2px;white-space:nowrap;
       overflow:hidden;text-overflow:ellipsis;flex:1}
.title small{display:block;font-weight:400;font-size:11px;color:var(--dim);
             font-family:var(--mono)}
.iconbtn{
  background:var(--bg3);border:1px solid var(--line);color:var(--dim);
  width:34px;height:34px;border-radius:9px;font-size:15px;display:grid;
  place-items:center;cursor:pointer;flex:0 0 auto;transition:.15s;
}
.iconbtn:active{transform:scale(.92);background:var(--accent);color:#fff}

/* ---------- tabs ---------- */
.tabs{display:flex;background:var(--bg2);border-bottom:1px solid var(--line);flex:0 0 auto}
.tab{
  flex:1;padding:11px 0;text-align:center;font-size:12.5px;font-weight:600;
  color:var(--dim);cursor:pointer;position:relative;transition:color .18s;
  letter-spacing:.3px;
}
.tab.sel{color:var(--accent)}
.tab.sel::after{
  content:"";position:absolute;left:22%;right:22%;bottom:0;height:2px;
  background:var(--accent);border-radius:2px 2px 0 0;
}

/* ---------- panes ---------- */
.pane{flex:1;min-height:0;display:none;flex-direction:column}
.pane.sel{display:flex}

/* ---------- terminal ---------- */
#screen{
  flex:1;min-height:0;margin:0;padding:12px 12px 4px;overflow:auto;
  font-family:var(--mono);font-size:12px;line-height:1.42;
  white-space:pre;color:var(--fg);background:var(--bg);
  -webkit-overflow-scrolling:touch;
}
#screen::-webkit-scrollbar{width:6px;height:6px}
#screen::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}
#screen .cur{background:var(--accent);color:var(--bg);border-radius:1px}

.keys{
  display:flex;gap:6px;padding:8px 10px;overflow-x:auto;background:var(--bg2);
  border-top:1px solid var(--line);flex:0 0 auto;scrollbar-width:none;
}
.keys::-webkit-scrollbar{display:none}
.key{
  background:var(--bg3);border:1px solid var(--line);color:var(--fg);
  padding:7px 12px;border-radius:8px;font-size:12px;font-family:var(--mono);
  white-space:nowrap;cursor:pointer;transition:.12s;flex:0 0 auto;
}
.key:active{background:var(--accent);border-color:var(--accent);color:#fff;transform:scale(.94)}
.key.hot{color:var(--warn)}

.inputrow{
  display:flex;gap:8px;padding:9px 10px calc(9px + var(--safe));
  background:var(--bg2);border-top:1px solid var(--line);flex:0 0 auto;
}
#cmd{
  flex:1;background:var(--bg);border:1px solid var(--line);border-radius:10px;
  color:var(--fg);font-family:var(--mono);font-size:13px;padding:10px 12px;
  outline:none;transition:border-color .18s;
}
#cmd:focus{border-color:var(--accent)}
.send{
  background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;color:#fff;
  width:42px;border-radius:10px;font-size:16px;cursor:pointer;transition:.12s;
}
.send:active{transform:scale(.92)}

/* ---------- files ---------- */
.path{
  display:flex;align-items:center;gap:6px;padding:9px 12px;background:var(--bg2);
  border-bottom:1px solid var(--line);font-family:var(--mono);font-size:11.5px;
  color:var(--dim);overflow-x:auto;white-space:nowrap;flex:0 0 auto;scrollbar-width:none;
}
.path::-webkit-scrollbar{display:none}
.crumb{color:var(--accent);cursor:pointer}
.crumb:active{opacity:.6}
#list{flex:1;min-height:0;overflow:auto;-webkit-overflow-scrolling:touch}
.row{
  display:flex;align-items:center;gap:11px;padding:11px 14px;
  border-bottom:1px solid var(--line);cursor:pointer;transition:background .12s;
}
.row:active{background:var(--bg3)}
.ico{width:22px;text-align:center;font-size:15px;flex:0 0 auto}
.nm{flex:1;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.meta{font-size:11px;color:var(--dim);font-family:var(--mono);flex:0 0 auto}
.dl{
  background:var(--bg3);border:1px solid var(--line);color:var(--dim);
  border-radius:7px;padding:5px 9px;font-size:11px;cursor:pointer;flex:0 0 auto;
}
.dl:active{background:var(--accent);color:#fff}

#viewer{
  position:fixed;inset:0;background:var(--bg);z-index:40;display:none;
  flex-direction:column;
}
#viewer.on{display:flex}
#vbody{
  flex:1;min-height:0;overflow:auto;margin:0;padding:12px;
  font-family:var(--mono);font-size:11.5px;line-height:1.5;white-space:pre-wrap;
  word-break:break-word;-webkit-overflow-scrolling:touch;
}

.empty{padding:40px 20px;text-align:center;color:var(--dim);font-size:13px}
.toast{
  position:fixed;left:50%;transform:translateX(-50%) translateY(-16px);
  top:calc(12px + env(safe-area-inset-top,0px));background:var(--bg3);
  border:1px solid var(--line);color:var(--fg);padding:9px 16px;border-radius:10px;
  font-size:12.5px;z-index:60;opacity:0;pointer-events:none;transition:.25s;
  max-width:88vw;text-align:center;
}
.toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
.spin{
  width:15px;height:15px;border:2px solid var(--line);border-top-color:var(--accent);
  border-radius:50%;animation:sp .7s linear infinite;margin:34px auto;
}
@keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div class="bar">
  <div class="dot" id="dot"></div>
  <div class="title" id="title">SST<small id="sub">connecting…</small></div>
  <button class="iconbtn" id="btnFont" title="Text size">Aa</button>
  <button class="iconbtn" id="btnTheme" title="Theme">◐</button>
</div>

<div class="tabs">
  <div class="tab sel" data-p="term">TERMINAL</div>
  <div class="tab" data-p="files">FILES</div>
</div>

<div class="pane sel" id="p-term">
  <pre id="screen">connecting…</pre>
  <div class="keys">
    <button class="key hot" data-k="sigint">^C</button>
    <button class="key" data-k="tab">TAB</button>
    <button class="key" data-k="esc">ESC</button>
    <button class="key" data-k="up">↑</button>
    <button class="key" data-k="down">↓</button>
    <button class="key" data-k="left">←</button>
    <button class="key" data-k="right">→</button>
    <button class="key" data-k="sigtstp">^Z</button>
    <button class="key" data-k="eof">^D</button>
    <button class="key" data-k="ff">^L</button>
    <button class="key" data-t="|">|</button>
    <button class="key" data-t="~">~</button>
    <button class="key" data-t="/">/</button>
    <button class="key" data-t="-">-</button>
  </div>
  <div class="inputrow">
    <input id="cmd" placeholder="type a command…" autocomplete="off"
           autocapitalize="off" autocorrect="off" spellcheck="false">
    <button class="send" id="send">➤</button>
  </div>
</div>

<div class="pane" id="p-files">
  <div class="path" id="crumbs"></div>
  <div id="list"><div class="spin"></div></div>
</div>

<div id="viewer">
  <div class="bar">
    <button class="iconbtn" id="vclose">✕</button>
    <div class="title" id="vname"></div>
    <button class="iconbtn" id="vdl">⤓</button>
  </div>
  <pre id="vbody"></pre>
</div>

<div class="toast" id="toast"></div>

<script>
(function(){
"use strict";
var TG = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (TG) { try { TG.ready(); TG.expand(); } catch(e){} }

var BASE  = location.pathname.replace(/\/app\/?$/, "");
var token = null, ws = null, retry = 0, fontPx = 12, alive = false;

var $ = function(id){ return document.getElementById(id); };
var screenEl = $("screen"), dot = $("dot"), sub = $("sub");

function toast(msg){
  var t = $("toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toast._t); toast._t = setTimeout(function(){ t.classList.remove("on"); }, 2200);
}
function haptic(kind){
  try { TG && TG.HapticFeedback && TG.HapticFeedback.impactOccurred(kind || "light"); } catch(e){}
}
function esc(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

/* ---------------- auth ---------------- */
function authPayload(){
  var q = new URLSearchParams(location.search);
  return {
    init_data: (TG && TG.initData) ? TG.initData : "",
    key: q.get("k") || ""
  };
}
function authenticate(cb){
  fetch(BASE + "/api/auth", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(authPayload())
  }).then(function(r){ return r.json(); }).then(function(j){
    if (!j.ok) { fail(j.error || "not authorised"); return; }
    token = j.token;
    $("title").firstChild.nodeValue = j.server || "SST";
    sub.textContent = j.user + "@" + j.host;
    if (TG) { try { TG.setHeaderColor && TG.setHeaderColor("#0b0e14"); } catch(e){} }
    cb();
  }).catch(function(){ fail("cannot reach the server"); });
}
function fail(msg){
  dot.className = "dot off"; sub.textContent = msg;
  screenEl.textContent = "\n  " + msg + "\n";
}

/* ---------------- terminal ---------------- */
function connect(){
  var proto = location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(proto + location.host + BASE + "/ws?t=" + encodeURIComponent(token));
  ws.onopen = function(){
    retry = 0; alive = true; dot.className = "dot on";
    send({t:"hello", cols: cols(), rows: 40});
  };
  ws.onmessage = function(ev){
    var m; try { m = JSON.parse(ev.data); } catch(e){ return; }
    if (m.t === "screen") paint(m);
  };
  ws.onclose = function(){
    alive = false; dot.className = "dot off";
    retry = Math.min(retry + 1, 6);
    setTimeout(connect, 400 * retry);
  };
  ws.onerror = function(){ try { ws.close(); } catch(e){} };
}
function send(o){ if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
function cols(){
  var probe = document.createElement("span");
  probe.style.cssText = "position:absolute;visibility:hidden;font-family:var(--mono);font-size:" + fontPx + "px";
  probe.textContent = "0".repeat(80);
  document.body.appendChild(probe);
  var w = probe.getBoundingClientRect().width / 80;
  document.body.removeChild(probe);
  return Math.max(40, Math.min(200, Math.floor((screenEl.clientWidth - 24) / (w || 7))));
}
function paint(m){
  var atBottom = screenEl.scrollHeight - screenEl.scrollTop - screenEl.clientHeight < 40;
  screenEl.innerHTML = esc(m.d);
  dot.className = "dot " + (m.busy ? "busy" : "on");
  if (atBottom) screenEl.scrollTop = screenEl.scrollHeight;
}
function type(txt){ send({t:"in", d:txt}); haptic(); }

/* ---------------- files ---------------- */
var cwd = "/";
function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign(opts.headers || {}, {"X-SST-Token": token});
  return fetch(BASE + path, opts).then(function(r){ return r.json(); });
}
function icon(e){
  if (e.dir) return "📁";
  if (e.link) return "🔗";
  var n = e.name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|ico)$/.test(n)) return "🖼";
  if (/\.(zip|gz|tar|xz|bz2|7z|rar)$/.test(n)) return "📦";
  if (/\.(sh|bash|zsh|py|js|rb|pl|go|rs|c|h|cpp)$/.test(n)) return "📜";
  if (/\.(json|ya?ml|toml|ini|conf|cfg|env)$/.test(n)) return "⚙️";
  if (/\.(log|txt|md)$/.test(n)) return "📄";
  if (e.exec) return "⚡";
  return "📄";
}
function human(n){
  if (n == null) return "";
  var u = ["B","K","M","G","T"], i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + u[i];
}
function crumbs(){
  var el = $("crumbs"); el.innerHTML = "";
  var parts = cwd.split("/").filter(Boolean), acc = "";
  var root = document.createElement("span");
  root.className = "crumb"; root.textContent = "/";
  root.onclick = function(){ browse("/"); };
  el.appendChild(root);
  parts.forEach(function(p, i){
    acc += "/" + p;
    var here = acc;
    el.appendChild(document.createTextNode(i ? " / " : ""));
    var c = document.createElement("span");
    c.className = "crumb"; c.textContent = p;
    c.onclick = function(){ browse(here); };
    el.appendChild(c);
  });
}
function browse(path){
  $("list").innerHTML = '<div class="spin"></div>';
  api("/api/ls?path=" + encodeURIComponent(path)).then(function(j){
    if (!j.ok) { $("list").innerHTML = '<div class="empty">' + esc(j.error || "cannot read") + '</div>'; return; }
    cwd = j.path; crumbs();
    var box = $("list"); box.innerHTML = "";
    if (cwd !== "/") box.appendChild(rowEl({name:"..", dir:true, up:true}));
    if (!j.entries.length && cwd === "/") { box.innerHTML = '<div class="empty">empty</div>'; return; }
    j.entries.forEach(function(e){ box.appendChild(rowEl(e)); });
    if (!j.entries.length) box.appendChild(Object.assign(document.createElement("div"),
        {className:"empty", textContent:"empty folder"}));
  }).catch(function(){ $("list").innerHTML = '<div class="empty">request failed</div>'; });
}
function join(a, b){
  if (b === "..") { var p = a.replace(/\/+$/,"").split("/"); p.pop(); return p.join("/") || "/"; }
  return (a === "/" ? "" : a.replace(/\/+$/,"")) + "/" + b;
}
function rowEl(e){
  var r = document.createElement("div"); r.className = "row";
  var i = document.createElement("div"); i.className = "ico"; i.textContent = e.up ? "↩" : icon(e);
  var n = document.createElement("div"); n.className = "nm"; n.textContent = e.name;
  r.appendChild(i); r.appendChild(n);
  if (!e.dir) {
    var m = document.createElement("div"); m.className = "meta"; m.textContent = human(e.size);
    r.appendChild(m);
    var d = document.createElement("button"); d.className = "dl"; d.textContent = "⤓";
    d.onclick = function(ev){ ev.stopPropagation(); download(join(cwd, e.name)); };
    r.appendChild(d);
  }
  r.onclick = function(){
    haptic();
    if (e.dir) browse(join(cwd, e.name));
    else view(join(cwd, e.name), e.name);
  };
  return r;
}
function view(path, name){
  $("vname").textContent = name;
  $("vbody").textContent = "loading…";
  $("viewer").classList.add("on");
  $("vdl").onclick = function(){ download(path); };
  api("/api/cat?path=" + encodeURIComponent(path)).then(function(j){
    $("vbody").textContent = j.ok ? j.data : (j.error || "cannot read this file");
  }).catch(function(){ $("vbody").textContent = "request failed"; });
}
function download(path){
  var url = BASE + "/api/dl?path=" + encodeURIComponent(path) + "&t=" + encodeURIComponent(token);
  if (TG && TG.openLink) TG.openLink(location.origin + url);
  else window.open(url, "_blank");
  toast("downloading " + path.split("/").pop());
}

/* ---------------- wiring ---------------- */
document.querySelectorAll(".tab").forEach(function(t){
  t.onclick = function(){
    document.querySelectorAll(".tab").forEach(function(x){ x.classList.remove("sel"); });
    document.querySelectorAll(".pane").forEach(function(x){ x.classList.remove("sel"); });
    t.classList.add("sel");
    $("p-" + t.dataset.p).classList.add("sel");
    haptic();
    if (t.dataset.p === "files" && !$("list").dataset.loaded) {
      $("list").dataset.loaded = "1"; browse("/");
    }
  };
});
var KEYS = {
  sigint:"\u0003", tab:"\t", esc:"\u001b", eof:"\u0004", sigtstp:"\u001a",
  ff:"\u000c", up:"\u001b[A", down:"\u001b[B", left:"\u001b[D", right:"\u001b[C"
};
document.querySelectorAll(".key").forEach(function(b){
  b.onclick = function(){
    if (b.dataset.k) type(KEYS[b.dataset.k] || "");
    else { var el = $("cmd"); el.value += b.dataset.t; el.focus(); haptic(); }
  };
});
$("send").onclick = function(){
  var el = $("cmd");
  type(el.value + "\r"); el.value = ""; el.focus();
};
$("cmd").addEventListener("keydown", function(e){
  if (e.key === "Enter") { e.preventDefault(); $("send").onclick(); }
});
$("btnTheme").onclick = function(){
  var h = document.documentElement;
  h.dataset.theme = h.dataset.theme === "dark" ? "light" : "dark";
  haptic();
};
$("btnFont").onclick = function(){
  fontPx = fontPx >= 16 ? 10 : fontPx + 1;
  screenEl.style.fontSize = fontPx + "px";
  send({t:"resize", cols: cols(), rows: 40});
  toast(fontPx + "px");
};
$("vclose").onclick = function(){ $("viewer").classList.remove("on"); };
window.addEventListener("resize", function(){
  clearTimeout(window._rz);
  window._rz = setTimeout(function(){ send({t:"resize", cols: cols(), rows: 40}); }, 250);
});

authenticate(function(){ connect(); });
})();
</script>
</body>
</html>
"""
