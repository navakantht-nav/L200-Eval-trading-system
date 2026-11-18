"""Embedded dark-mode A2UI v0.9 & Multi-Protocol Inspector Console (`/console`)."""

CONSOLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AlphaPulse | ADKv2, MCP, A2A, A2UI & AP2 Live Desk</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: #131b33;
      --border: #25335a;
      --text: #e8eefc;
      --muted: #93a4c8;
      --accent: #4f8cff;
      --green: #22c55e;
      --amber: #f59e0b;
      --red: #ef4444;
    }
    * { box-sizing: border-box; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; background: var(--bg); color: var(--text); padding: 20px; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
    .badges span { background: #1d294d; border: 1px solid var(--border); padding: 4px 10px; border-radius: 999px; font-size: 12px; margin-left: 6px; color: #9ec1ff; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
    h2 { margin: 0 0 10px 0; font-size: 16px; color: #bcd3ff; }
    button, select, input { background: #1b2647; color: var(--text); border: 1px solid var(--border); padding: 8px 12px; border-radius: 8px; font-size: 13px; }
    button { background: var(--accent); border-color: var(--accent); font-weight: 600; cursor: pointer; }
    button:hover { opacity: 0.92; }
    pre { background: #080c18; border: 1px solid #1b2542; padding: 10px; border-radius: 8px; overflow-x: auto; font-size: 12px; max-height: 260px; color: #c9d8f4; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
    .a2ui-card { border: 1px solid #3b82f6; background: #0f1936; border-radius: 10px; padding: 12px; margin-top: 8px; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1 style="margin:0;font-size:20px;">AlphaPulse Realtime Multi-Agent Trading Desk</h1>
      <div style="color:var(--muted);font-size:13px;margin-top:4px;">Google ADKv2 Graph Workflow + GEAP Agent Identity + MCP + A2A + A2UI v0.9 + AP2 v0.2</div>
    </div>
    <div class="badges">
      <span>ADKv2 2.x</span><span>MCP /mcp</span><span>A2A</span><span>A2UI v0.9</span><span>AP2 Double-Lock</span><span>OAuth 2.1</span>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <h2>1. Live SSE Tick Stream &amp; A2UI v0.9 Surface</h2>
      <div class="row">
        <select id="symSelect">
          <option value="GOOGL">GOOGL</option>
          <option value="NVDA">NVDA</option>
          <option value="MSFT">MSFT</option>
          <option value="SPY">SPY</option>
        </select>
        <button onclick="startSSE()">Stream SSE Ticks</button>
        <button onclick="loadA2UI()">Render A2UI Pulse</button>
      </div>
      <div id="a2uiRender" class="a2ui-card">Click "Render A2UI Pulse" to materialize declarative A2UI v0.9 components.</div>
      <pre id="sseOut">Waiting for SSE stream...</pre>
    </div>

    <div class="card">
      <h2>2. OAuth 2.1 + MCP Streamable HTTP (/mcp)</h2>
      <div class="row">
        <button onclick="callMcpTools()">Mint OAuth Token &amp; Call MCP</button>
        <button onclick="fetchAgentCard()">Inspect A2A AgentCard</button>
      </div>
      <pre id="mcpOut">Click above to mint a scoped OAuth 2.1 JWT and invoke POST /mcp JSON-RPC 2.0...</pre>
    </div>

    <div class="card">
      <h2>3. Pre-Trade VaR &amp; AP2 Cryptographic Settlement</h2>
      <div class="row">
        <input id="qtyInput" type="number" value="50" style="width:90px;" />
        <select id="sideInput"><option value="BUY">BUY</option><option value="SELL">SELL</option></select>
        <button onclick="stageAndSettle()">Sign AP2 Mandates &amp; Settle</button>
      </div>
      <pre id="ap2Out">Executes pre-trade 95% VaR check -> Broker CartMandate -> User CheckoutMandate &amp; PaymentMandate -> Paper Fill.</pre>
    </div>
  </div>

  <script>
    let evtSource = null;
    function startSSE() {
      if (evtSource) evtSource.close();
      const sym = document.getElementById('symSelect').value;
      const out = document.getElementById('sseOut');
      out.textContent = 'Connecting to /api/v1/stream/ticks/' + sym + ' ...\\n';
      evtSource = new EventSource('/api/v1/stream/ticks/' + sym + '?max_events=5');
      evtSource.onmessage = (e) => {
        out.textContent = e.data + '\\n' + out.textContent;
      };
      evtSource.onerror = () => { evtSource.close(); };
    }
    async function loadA2UI() {
      const sym = document.getElementById('symSelect').value;
      const r = await fetch('/api/v1/a2ui/' + sym);
      const data = await r.json();
      const dm = data.a2ui_messages[2].updateDataModel.value;
      document.getElementById('a2uiRender').innerHTML =
        '<strong>' + dm.header.title + '</strong><div style="color:#93a4c8;font-size:12px;">' + dm.header.subtitle + '</div>' +
        '<div style="margin-top:8px;font-size:15px;color:#22c55e;">' + dm.metrics.lastPriceLabel + ' | ' + dm.metrics.vwapLabel + '</div>' +
        '<div style="margin-top:4px;font-size:12px;">' + dm.metrics.imbalanceLabel + '</div>' +
        '<div style="margin-top:6px;font-size:12px;color:#9ec1ff;">' + dm.signals.regimeBadge + ' — ' + dm.signals.indicatorSummary + '</div>';
    }
    async function callMcpTools() {
      const tokRes = await fetch('/api/v1/oauth/token', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({scopes:['trading:read','trading:simulate']})});
      const tok = await tokRes.json();
      const sym = document.getElementById('symSelect').value;
      const mcpRes = await fetch('/mcp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok.access_token},
        body: JSON.stringify({jsonrpc:'2.0', id: 1, method:'tools/call', params:{name:'compute_technical_signals', arguments:{symbol: sym}}})
      });
      document.getElementById('mcpOut').textContent = JSON.stringify(await mcpRes.json(), null, 2);
    }
    async function fetchAgentCard() {
      const r = await fetch('/.well-known/agent.json');
      document.getElementById('mcpOut').textContent = JSON.stringify(await r.json(), null, 2);
    }
    async function stageAndSettle() {
      const sym = document.getElementById('symSelect').value;
      const qty = parseInt(document.getElementById('qtyInput').value || '50', 10);
      const side = document.getElementById('sideInput').value;
      const r = await fetch('/api/v1/ap2/stage-and-settle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({symbol: sym, side: side, quantity: qty})
      });
      document.getElementById('ap2Out').textContent = JSON.stringify(await r.json(), null, 2);
    }
  </script>
</body>
</html>
"""
