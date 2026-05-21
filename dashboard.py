#!/usr/bin/env python3
# =============================================================================
# dashboard.py – Web Dashboard Realtime (Mức 4.4)
# Flask + Plotly.js – Tự cập nhật mỗi 2 giây, không cần Grafana/InfluxDB
# =============================================================================
# Cài đặt: pip install flask
# Chạy:    python3 dashboard.py
# Mở:      http://localhost:5000
# =============================================================================

import os, csv, json, time, random, math, threading
from datetime import datetime
from pathlib import Path
from collections import deque

try:
    from flask import Flask, render_template_string, jsonify, Response
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    print('[ERR] Flask chưa cài! Chạy: pip install flask')
    exit(1)

# ─── Cấu hình ────────────────────────────────────────────────────────────────
LOG_FILE      = Path(__file__).parent / 'load_log.csv'
ATTACK_LOG    = Path(__file__).parent / 'attack_log.csv'
PORT          = 5000
MAX_POINTS    = 60        # Số điểm hiển thị trên biểu đồ
REFRESH_MS    = 2000      # Tốc độ refresh (ms)

app = Flask(__name__)

# ─── Cache data (in-memory) ───────────────────────────────────────────────────
_data_cache = {
    'timestamps': deque(maxlen=MAX_POINTS),
    'web1_mbps':  deque(maxlen=MAX_POINTS),
    'web2_mbps':  deque(maxlen=MAX_POINTS),
    'ewma_pct':   deque(maxlen=MAX_POINTS),
    'active':     deque(maxlen=MAX_POINTS),
    'total_switches': 0,
    'current_active': 'WEB1',
    'blocked_ips':    [],
    'attack_events':  [],
    'last_update':    '',
}

# ─── Demo data generator (khi chưa có load_log.csv) ─────────────────────────
_demo_t      = 0
_demo_active = 'WEB1'
_demo_ewma   = 30.0

def _gen_demo_point():
    global _demo_t, _demo_active, _demo_ewma
    _demo_t += 1
    wave  = 55 * math.sin(_demo_t * 0.12) + 50
    noise = random.uniform(-5, 5)
    w1    = max(2, min(98, wave + noise))

    EWMA_ALPHA = 0.3
    _demo_ewma = EWMA_ALPHA * w1 + (1 - EWMA_ALPHA) * _demo_ewma

    if _demo_ewma > 80 and _demo_active == 'WEB1':
        _demo_active = 'WEB2'
    elif _demo_ewma < 20 and _demo_active == 'WEB2':
        _demo_active = 'WEB1'

    if _demo_active == 'WEB2':
        w2 = max(0, min(100, 100 - w1 * 0.3 + noise))
        w1 = max(0, min(100, w1 * 0.28 + noise))
    else:
        w2 = random.uniform(3, 12)

    return {
        'timestamp':    datetime.now().strftime('%H:%M:%S'),
        'web1_mbps':    round(w1, 1),
        'web2_mbps':    round(w2, 1),
        'ewma_pct':     round(_demo_ewma, 1),
        'active_server': _demo_active,
    }


def _read_csv_latest(n: int = MAX_POINTS) -> list:
    """Đọc n dòng cuối cùng từ load_log.csv."""
    if not LOG_FILE.exists():
        return []
    try:
        with open(LOG_FILE, 'r', newline='') as f:
            rows = list(csv.DictReader(f))
        return rows[-n:]
    except Exception:
        return []


def _read_attack_log_latest(n: int = 20) -> list:
    """Đọc n dòng cuối từ attack_log.csv, trả về full row cho các anomaly."""
    if not ATTACK_LOG.exists():
        return []
    try:
        with open(ATTACK_LOG, 'r', newline='') as f:
            rows = list(csv.DictReader(f))
        # is_anomaly có thể là '1' (string) hoặc 1 (int) tuỳ cách ghi
        return [
            r for r in rows[-n:]
            if str(r.get('is_anomaly', '0')).strip() == '1'
        ]
    except Exception:
        return []


# =============================================================================
# Background thread: cập nhật cache mỗi 2s
# =============================================================================
def _update_cache_loop():
    global _data_cache
    EWMA_ALPHA = 0.3
    STALE_SEC  = 15   # Nếu CSV không cập nhật quá 15s → dùng demo mode

    while True:
        rows       = _read_csv_latest(MAX_POINTS)
        use_demo   = False

        # Kiểm tra CSV có đang được cập nhật không (stale check)
        if LOG_FILE.exists():
            age = time.time() - LOG_FILE.stat().st_mtime
            if age > STALE_SEC:
                use_demo = True   # File tồn tại nhưng đã cũ → demo mode

        if rows and not use_demo:
            _data_cache['timestamps'] = deque([r.get('timestamp','') for r in rows], maxlen=MAX_POINTS)
            _data_cache['web1_mbps']  = deque([float(r.get('web1_mbps', 0)) for r in rows], maxlen=MAX_POINTS)
            _data_cache['web2_mbps']  = deque([float(r.get('web2_mbps', 0)) for r in rows], maxlen=MAX_POINTS)

            # Tính ewma_pct on-the-fly nếu CSV thiếu cột này
            ewma_val = 0.0
            ewma_list = []
            for r in rows:
                raw = r.get('ewma_pct', '').strip()
                if raw and raw != '0':
                    ewma_val = float(raw)
                    ewma_list.append(ewma_val)
                else:
                    # Không có cột ewma_pct → tự tính từ web1_mbps
                    w1 = float(r.get('web1_mbps', 0))
                    ewma_val = EWMA_ALPHA * w1 + (1 - EWMA_ALPHA) * ewma_val
                    ewma_list.append(round(ewma_val, 2))

            _data_cache['ewma_pct']   = deque(ewma_list, maxlen=MAX_POINTS)
            _data_cache['active']     = deque([r.get('active_server','WEB1') for r in rows], maxlen=MAX_POINTS)
            _data_cache['current_active'] = rows[-1].get('active_server', 'WEB1')

            # Đếm switches
            sw = sum(1 for r in rows if 'SWITCH' in r.get('action', ''))
            _data_cache['total_switches'] = sw
        else:
            # Demo mode: thêm điểm giả lập liên tục
            pt = _gen_demo_point()
            _data_cache['timestamps'].append(pt['timestamp'])
            _data_cache['web1_mbps'].append(pt['web1_mbps'])
            _data_cache['web2_mbps'].append(pt['web2_mbps'])
            _data_cache['ewma_pct'].append(pt['ewma_pct'])
            _data_cache['active'].append(pt['active_server'])
            _data_cache['current_active'] = pt['active_server']

        # Attack log – lưu full row để hiển thị chi tiết
        attacks = _read_attack_log_latest()
        # Deduplicate theo IP, ưu tiên dòng mới nhất
        seen = {}
        for r in attacks:
            seen[r.get('src_ip', '')] = r
        _data_cache['blocked_ips']    = list(seen.keys())
        _data_cache['attack_events']  = list(seen.values())

        _data_cache['last_update'] = datetime.now().strftime('%H:%M:%S')
        time.sleep(2)


# =============================================================================
# HTML Template
# =============================================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Campus Network Monitor</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #0d1117; color: #e6edf3;
      min-height: 100vh;
    }
    header {
      background: linear-gradient(135deg, #161b22, #21262d);
      border-bottom: 1px solid #30363d;
      padding: 16px 24px;
      display: flex; align-items: center; justify-content: space-between;
    }
    header h1 { font-size: 1.2rem; color: #58a6ff; }
    header .subtitle { font-size: 0.8rem; color: #8b949e; }
    #status-bar {
      background: #161b22; border-bottom: 1px solid #30363d;
      padding: 8px 24px; display: flex; gap: 24px; align-items: center;
      font-size: 0.85rem;
    }
    .stat-badge {
      background: #21262d; border: 1px solid #30363d;
      border-radius: 6px; padding: 4px 12px;
      display: flex; gap: 8px; align-items: center;
    }
    .stat-badge .label { color: #8b949e; font-size: 0.75rem; }
    .stat-badge .value { color: #58a6ff; font-weight: 700; }
    .active-web1 .value { color: #3fb950; }
    .active-web2 .value { color: #f85149; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px; padding: 16px;
    }
    .grid-full { grid-column: 1 / -1; }
    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 10px; padding: 16px;
    }
    .card h3 {
      font-size: 0.9rem; color: #8b949e;
      margin-bottom: 12px; border-bottom: 1px solid #30363d;
      padding-bottom: 8px;
    }
    .gauge-row { display: flex; gap: 16px; justify-content: center; }
    .gauge-item { text-align: center; }
    .gauge-label { font-size: 0.75rem; color: #8b949e; margin-top: 4px; }
    #alert-banner {
      display: none;
      background: linear-gradient(90deg, #da3633, #b91c1c);
      color: white; padding: 10px 24px; font-size: 0.9rem;
      border-left: 4px solid #ff6b6b; animation: pulse 1s infinite;
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.8} }
    #attack-list { font-size: 0.8rem; color: #f85149; max-height: 120px; overflow-y:auto; }
    .attack-entry {
      background: #21262d; border-radius: 4px;
      padding: 4px 8px; margin: 2px 0;
      border-left: 3px solid #f85149;
    }
    #last-update { font-size: 0.75rem; color: #8b949e; text-align: right;
                   padding: 4px 24px; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>🖥 Campus Network Monitor – Mức 4.4</h1>
      <div class="subtitle">Real-time Load Balancer &amp; Security Dashboard</div>
    </div>
    <div style="font-size:0.8rem;color:#3fb950" id="conn-status">● LIVE</div>
  </header>

  <div id="alert-banner">⚠ CẢNH BÁO: Tải Web1 vượt 80%! Đang chuyển sang Web2...</div>

  <div id="status-bar">
    <div class="stat-badge" id="badge-active">
      <span class="label">Active Server</span>
      <span class="value" id="active-server">WEB1</span>
    </div>
    <div class="stat-badge">
      <span class="label">Web1 Load</span>
      <span class="value" id="web1-now">--</span>
    </div>
    <div class="stat-badge">
      <span class="label">Web2 Load</span>
      <span class="value" id="web2-now">--</span>
    </div>
    <div class="stat-badge">
      <span class="label">EWMA %</span>
      <span class="value" id="ewma-now">--</span>
    </div>
    <div class="stat-badge">
      <span class="label">Switches</span>
      <span class="value" id="switch-count">0</span>
    </div>
    <div class="stat-badge">
      <span class="label">Blocked IPs</span>
      <span class="value" id="blocked-count" style="color:#f85149">0</span>
    </div>
  </div>

  <div class="grid">
    <!-- Main chart: throughput timeline -->
    <div class="card grid-full">
      <h3>📈 Throughput Web1 / Web2 (Mbps) – Cập nhật mỗi 2 giây</h3>
      <div id="chart-main" style="height:300px"></div>
    </div>

    <!-- EWMA gauge-style line -->
    <div class="card">
      <h3>📊 EWMA Load % (Smoothed)</h3>
      <div id="chart-ewma" style="height:200px"></div>
    </div>

    <!-- Attack/Security -->
    <div class="card">
      <h3>🛡 Security – IP bị block tự động (ML)</h3>
      <div id="attack-list"></div>
    </div>

    <!-- Threshold indicators -->
    <div class="card">
      <h3>⚡ Trạng thái ngưỡng Load Balancer</h3>
      <div id="chart-gauge" style="height:200px"></div>
    </div>

    <!-- Switch events -->
    <div class="card">
      <h3>🔄 Sự kiện chuyển Server gần nhất</h3>
      <div id="switch-log" style="font-size:0.8rem;max-height:200px;overflow-y:auto"></div>
    </div>
  </div>
  <div id="last-update">Cập nhật lần cuối: --</div>

<script>
const REFRESH_MS  = {{ refresh_ms }};
let   switchLog   = [];

// ── Khởi tạo biểu đồ Plotly ────────────────────────────────────────────────
const mainLayout = {
  paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
  font: { color: '#e6edf3', size: 11 },
  xaxis: { gridcolor: '#30363d', tickfont: { size: 9 } },
  yaxis: { gridcolor: '#30363d', range: [0, 110], title: 'Mbps' },
  shapes: [
    { type:'line', x0:0, x1:1, xref:'paper', y0:80, y1:80,
      line:{color:'#f85149', dash:'dash', width:1.5}, name:'Ngưỡng cao' },
    { type:'line', x0:0, x1:1, xref:'paper', y0:20, y1:20,
      line:{color:'#3fb950', dash:'dash', width:1.5}, name:'Ngưỡng thấp' },
  ],
  legend: { bgcolor:'#21262d', bordercolor:'#30363d', borderwidth:1 },
  margin: { t:10, b:40, l:50, r:10 },
};

const ewmaLayout = {
  paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
  font: { color: '#e6edf3', size: 10 },
  xaxis: { gridcolor: '#30363d', tickfont:{size:8} },
  yaxis: { gridcolor: '#30363d', range:[0,110], title:'%' },
  margin: { t:10, b:30, l:45, r:10 },
};

Plotly.newPlot('chart-main', [
  { x:[], y:[], name:'Web1 (Mbps)', mode:'lines', line:{color:'#4C8BF5',width:2.5},
    fill:'tozeroy', fillcolor:'rgba(76,139,245,0.1)' },
  { x:[], y:[], name:'Web2 (Mbps)', mode:'lines', line:{color:'#F5854C',width:2.5},
    fill:'tozeroy', fillcolor:'rgba(245,133,76,0.1)' },
], mainLayout, {responsive:true, displayModeBar:false});

Plotly.newPlot('chart-ewma', [
  { x:[], y:[], name:'EWMA %', mode:'lines+markers',
    line:{color:'#AB47BC',width:2}, marker:{size:4} },
], ewmaLayout, {responsive:true, displayModeBar:false});

Plotly.newPlot('chart-gauge', [
  { type:'indicator', mode:'gauge+number+delta',
    value:0,
    delta: { reference:80, increasing:{color:'#f85149'} },
    gauge: {
      axis: { range:[0,100] },
      bar:  { color:'#4C8BF5' },
      steps: [
        { range:[0,20],  color:'rgba(63,185,80,0.2)' },
        { range:[20,80], color:'rgba(255,255,255,0.05)' },
        { range:[80,100],color:'rgba(248,81,73,0.2)' },
      ],
      threshold: { line:{color:'#f85149',width:3}, thickness:0.75, value:80 },
    },
    title: { text:'Web1 Load %', font:{color:'#e6edf3',size:12} },
    number: { suffix:'%', font:{color:'#58a6ff',size:32} },
  }
], {
  paper_bgcolor:'#161b22',
  font:{color:'#e6edf3'},
  margin:{t:30,b:10,l:20,r:20},
}, {responsive:true, displayModeBar:false});

// ── Fetch và cập nhật dữ liệu ──────────────────────────────────────────────
async function fetchAndUpdate() {
  try {
    const resp = await fetch('/api/data');
    const d    = await resp.json();

    // Cập nhật status bar
    document.getElementById('active-server').textContent = d.current_active;
    document.getElementById('web1-now').textContent      = (d.web1_mbps.slice(-1)[0]||0).toFixed(1)+' Mbps';
    document.getElementById('web2-now').textContent      = (d.web2_mbps.slice(-1)[0]||0).toFixed(1)+' Mbps';
    document.getElementById('ewma-now').textContent      = (d.ewma_pct.slice(-1)[0]||0).toFixed(1)+'%';
    document.getElementById('switch-count').textContent  = d.total_switches;
    document.getElementById('blocked-count').textContent = d.blocked_ips.length;
    document.getElementById('last-update').textContent   = 'Cập nhật: '+d.last_update;

    // Badge màu theo active server
    const badge = document.getElementById('badge-active');
    badge.className = 'stat-badge '+(d.current_active==='WEB1'?'active-web1':'active-web2');

    // Alert banner khi load cao
    const ewmaLast = d.ewma_pct.slice(-1)[0]||0;
    document.getElementById('alert-banner').style.display = ewmaLast>80?'block':'none';

    // Cập nhật biểu đồ chính
    Plotly.react('chart-main', [
      { x:d.timestamps, y:d.web1_mbps, name:'Web1 (Mbps)',
        mode:'lines', line:{color:'#4C8BF5',width:2.5},
        fill:'tozeroy', fillcolor:'rgba(76,139,245,0.1)' },
      { x:d.timestamps, y:d.web2_mbps, name:'Web2 (Mbps)',
        mode:'lines', line:{color:'#F5854C',width:2.5},
        fill:'tozeroy', fillcolor:'rgba(245,133,76,0.1)' },
    ], mainLayout);

    // Cập nhật EWMA chart
    Plotly.react('chart-ewma', [
      { x:d.timestamps, y:d.ewma_pct, name:'EWMA %',
        mode:'lines+markers', line:{color:'#AB47BC',width:2}, marker:{size:4} },
    ], ewmaLayout);

    // Cập nhật gauge
    Plotly.update('chart-gauge', {'value': ewmaLast}, {}, [0]);

    // Attack list – hiển thị chi tiết từ attack_events
    const attackDiv = document.getElementById('attack-list');
    const events = d.attack_events || [];
    if (events.length > 0) {
      attackDiv.innerHTML = events.map(ev => {
        const score = parseFloat(ev.anomaly_score || 0).toFixed(3);
        const ts    = ev.timestamp  || '--:--:--';
        const ip    = ev.src_ip     || 'unknown';
        const drops = ev.drop_count || '?';
        const algo  = ev.algorithm  || 'ML';
        return `<div class="attack-entry">
          🚫 <b>${ip}</b> &nbsp;|&nbsp; ${ts}
          &nbsp;|&nbsp; Drops: <b>${drops}</b>
          &nbsp;|&nbsp; Score: <b>${score}</b>
          &nbsp;|&nbsp; <span style="color:#ff9800">[${algo}]</span>
        </div>`;
      }).join('');
    } else {
      attackDiv.innerHTML = '<div style="color:#8b949e;padding:8px">✅ Chưa phát hiện tấn công</div>';
    }

    // Switch log
    if (d.switch_events && d.switch_events.length > 0) {
      const logDiv = document.getElementById('switch-log');
      logDiv.innerHTML = d.switch_events.map(e =>
        `<div style="padding:3px 0;border-bottom:1px solid #30363d;color:#f0883e">⚡ ${e}</div>`
      ).join('');
    }

  } catch(e) {
    document.getElementById('conn-status').textContent = '● OFFLINE';
    document.getElementById('conn-status').style.color = '#f85149';
  }
}

// Cập nhật ngay lần đầu, sau đó mỗi REFRESH_MS
fetchAndUpdate();
setInterval(fetchAndUpdate, REFRESH_MS);
</script>
</body>
</html>
"""


# =============================================================================
# API Routes
# =============================================================================
@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML, refresh_ms=REFRESH_MS)


@app.route('/api/data')
def api_data():
    cache = _data_cache
    ts    = list(cache['timestamps'])
    w1    = list(cache['web1_mbps'])
    w2    = list(cache['web2_mbps'])
    ewma  = list(cache['ewma_pct'])
    act   = list(cache['active'])

    # Tìm các sự kiện switch
    switch_events = []
    prev_active = None
    for i, a in enumerate(act):
        if prev_active and a != prev_active:
            t = ts[i] if i < len(ts) else '?'
            switch_events.append(f'[{t}] {prev_active} → {a}')
        prev_active = a

    return jsonify({
        'timestamps':     ts,
        'web1_mbps':      w1,
        'web2_mbps':      w2,
        'ewma_pct':       ewma,
        'active':         act,
        'current_active': cache['current_active'],
        'total_switches': cache['total_switches'],
        'blocked_ips':    cache['blocked_ips'],
        'attack_events':  cache.get('attack_events', []),
        'switch_events':  switch_events[-10:],
        'last_update':    cache['last_update'],
    })


# =============================================================================
# Main
# =============================================================================
if __name__ == '__main__':
    print('\n' + '═'*60)
    print('  CAMPUS NETWORK DASHBOARD (Mức 4.4)')
    print('  Flask + Plotly.js – Real-time Web Monitor')
    print('═'*60)
    print(f'  Dashboard:  http://localhost:{PORT}')
    print(f'  API:        http://localhost:{PORT}/api/data')
    print(f'  Log file:   {LOG_FILE}')
    print(f'  Refresh:    mỗi {REFRESH_MS}ms')
    print('  Ctrl+C để dừng\n')

    if not LOG_FILE.exists():
        print(f'  [INFO] Chưa có {LOG_FILE.name} → Dùng demo data tự động\n')

    # Background thread cập nhật cache
    t = threading.Thread(target=_update_cache_loop, daemon=True)
    t.start()

    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
