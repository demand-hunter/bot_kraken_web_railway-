import os
from flask import Flask, jsonify, request, render_template_string
from bot_engine import TradingBot

app = Flask(__name__)
bot = TradingBot()

HTML = r"""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Turbo 4.0 — Paper Trading</title>
<style>
:root { --bg:#0b1020; --card:#131a2a; --muted:#8e9ab5; --text:#eef3ff; --ok:#42d392; --bad:#ff6b6b; --accent:#5b8cff; }
* { box-sizing:border-box; }
body { margin:0; font-family:Arial,Helvetica,sans-serif; background:var(--bg); color:var(--text); }
.wrap { max-width:1180px; margin:auto; padding:24px; }
.top { display:flex; justify-content:space-between; gap:16px; align-items:center; flex-wrap:wrap; }
h1 { margin:0; font-size:26px; }
.badge { padding:7px 12px; border-radius:999px; background:#202a43; font-weight:700; }
.grid { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-top:20px; }
.card { background:var(--card); border:1px solid #25304a; border-radius:16px; padding:16px; }
.label { color:var(--muted); font-size:13px; margin-bottom:7px; }
.value { font-size:22px; font-weight:700; word-break:break-word; }
.wide { grid-column:span 2; }
.actions { display:flex; gap:10px; flex-wrap:wrap; margin:18px 0; }
button { border:0; border-radius:10px; padding:11px 17px; font-weight:700; cursor:pointer; }
.start { background:var(--ok); }
.stop { background:#ffc857; }
.reset { background:var(--bad); color:white; }
.log { height:340px; overflow:auto; background:#080c17; padding:14px; border-radius:12px; font-family:monospace; font-size:12px; line-height:1.6; white-space:pre-wrap; }
.status-ok { color:var(--ok); }
.status-bad { color:var(--bad); }
.small { font-size:12px; color:var(--muted); margin-top:10px; }
@media(max-width:800px){ .grid{grid-template-columns:repeat(2,1fr)} .wide{grid-column:span 2} }
@media(max-width:480px){ .grid{grid-template-columns:1fr} .wide{grid-column:span 1} }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>Trading Turbo 4.0 — Paper Trading</h1>
      <div class="small">Kraken • Radar 100 → Top 25 dinâmico • decisão 15M • saldo fictício</div>
    </div>
    <div class="badge" id="mode">PAPER</div>
  </div>

  <div class="actions">
    <button class="start" onclick="act('/api/start')">INICIAR BOT</button>
    <button class="stop" onclick="act('/api/stop')">PARAR BOT</button>
    <button class="reset" onclick="resetBot()">RESETAR SIMULAÇÃO</button>
  </div>

  <div class="grid">
    <div class="card"><div class="label">STATUS</div><div class="value" id="running">—</div></div>
    <div class="card"><div class="label">SALDO</div><div class="value" id="balance">—</div></div>
    <div class="card"><div class="label">PREÇO / PAR EM FOCO</div><div class="value" id="price">—</div></div>
    <div class="card"><div class="label">TENDÊNCIA CONFIRMADA</div><div class="value" id="trend">—</div></div>

    <div class="card"><div class="label">SUPORTE</div><div class="value" id="support">—</div></div>
    <div class="card"><div class="label">RESISTÊNCIA</div><div class="value" id="resistance">—</div></div>
    <div class="card"><div class="label">OPERAÇÕES</div><div class="value" id="trades">0</div></div>
    <div class="card"><div class="label">TAXA DE ACERTO</div><div class="value" id="hit">0%</div></div>

    <div class="card"><div class="label">WIN / LOSS</div><div class="value" id="wl">0 / 0</div></div>
    <div class="card"><div class="label">PNL ACUMULADO</div><div class="value" id="pnl">R$0,00</div></div>
    <div class="card wide"><div class="label">POSIÇÃO ABERTA</div><div class="value" id="position">Nenhuma</div></div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="label">RADAR 100 → TOP 25 DINÂMICO</div>
    <div id="radar" class="small">Carregando radar...</div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="label">ÚLTIMO ERRO</div>
    <div id="error">Nenhum</div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="label">LOG EM TEMPO REAL</div>
    <div class="log" id="logs">Carregando...</div>
  </div>
</div>
<script>
const brl = v => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v ?? 0);
const num = v => v == null ? '—' : Number(v).toLocaleString('pt-BR',{maximumFractionDigits:2});

async function act(url){
  await fetch(url,{method:'POST'});
  await refresh();
}
async function resetBot(){
  if(!confirm('Resetar saldo e histórico para R$ 1.000?')) return;
  const r = await fetch('/api/reset',{method:'POST'});
  const j = await r.json();
  if(!j.ok) alert(j.message);
  await refresh();
}
async function refresh(){
  try{
    const r = await fetch('/api/status');
    const s = await r.json();
    document.getElementById('running').textContent = s.running ? 'RODANDO' : 'PARADO';
    document.getElementById('running').className = 'value ' + (s.running ? 'status-ok':'status-bad');
    document.getElementById('balance').textContent = brl(s.balance);
    document.getElementById('price').textContent = s.last_price == null ? '—' : `${s.symbol} | $ ${num(s.last_price)}`;
    document.getElementById('trend').textContent = s.trend;
    document.getElementById('support').textContent = s.support == null ? '—' : '$ ' + num(s.support);
    document.getElementById('resistance').textContent = s.resistance == null ? '—' : '$ ' + num(s.resistance);
    document.getElementById('trades').textContent = s.stats.trades;
    document.getElementById('hit').textContent = num(s.stats.hit_rate) + '%';
    document.getElementById('wl').textContent = s.stats.wins + ' / ' + s.stats.losses;
    document.getElementById('pnl').textContent = brl(s.stats.pnl);
    document.getElementById('mode').textContent = s.mode + ' • ' + (s.decision_timeframe || '15m');
    const f=(s.radar && s.radar.focus)||[];
    document.getElementById('radar').textContent = `Universo ${s.radar?.universe_size||0} • foco ${s.radar?.focus_size||0} • varreduras ${s.radar?.scan_count||0}\n` + f.slice(0,10).map((x,i)=>`${i+1}. ${x.symbol} — ${num(x.score||0)}`).join(' | ');
    document.getElementById('error').textContent = s.last_error || 'Nenhum';
    document.getElementById('position').textContent = s.position
      ? `${s.position.side.toUpperCase()} | entrada ${num(s.position.entry)} | stop ${num(s.position.stop)} | alvo ${num(s.position.target)}`
      : 'Nenhuma';
    document.getElementById('logs').textContent = s.logs.join('\n') || 'Sem logs ainda.';
  }catch(e){
    document.getElementById('error').textContent = e.toString();
  }
}
refresh();
setInterval(refresh,5000);
</script>
</body>
</html>
"""

@app.get("/")
def index():
    return render_template_string(HTML)

@app.get("/health")
def health():
    return {"ok": True, "service": "bot-kraken-web"}

@app.get("/api/status")
def status():
    return jsonify(bot.snapshot())

@app.post("/api/start")
def start():
    ok = bot.start()
    return jsonify({"ok": True, "started": ok})

@app.post("/api/stop")
def stop():
    bot.stop()
    return jsonify({"ok": True})

@app.post("/api/reset")
def reset():
    ok, message = bot.reset()
    return jsonify({"ok": ok, "message": message}), (200 if ok else 409)

if os.getenv("AUTOSTART", "true").lower() in ("1","true","yes","on"):
    bot.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
