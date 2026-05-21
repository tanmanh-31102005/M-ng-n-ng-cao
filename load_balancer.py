#!/usr/bin/env python3
# =============================================================================
# load_balancer.py – Giám sát tải và điều hướng theo ngưỡng
# MỨC 3.4 + 4.1: EWMA, Hold-down Timer, Linear Regression Proactive LB,
#                 Least-Connections counter, chống Flapping
# =============================================================================
import os, sys, time, subprocess, csv, argparse, signal, random, math
from datetime import datetime
from collections import deque

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ─── Tham số cấu hình ────────────────────────────────────────────────────────
WEB1_IP        = '10.10.10.11'
WEB2_IP        = '10.10.10.12'
PUBLIC_VIP     = '203.0.113.10'
THRESHOLD_HIGH = 80.0        # % – chuyển sang web2 khi vượt
THRESHOLD_LOW  = 20.0        # % – chuyển về web1 khi giảm
EWMA_ALPHA     = 0.3         # Hệ số EWMA (0=bỏ qua history, 1=chỉ dùng hiện tại)
HOLD_DOWN_SEC  = 30          # Giây khóa sau mỗi lần switch (chống Flapping)
PREDICT_WIN    = 10          # Số điểm dùng để dự đoán xu hướng (Linear Regression)
PROACTIVE_MARGIN = 5.0       # Kích hoạt sớm khi dự đoán sẽ vượt ngưỡng+margin
LOG_FILE       = 'load_log.csv'
INTERVAL_SEC   = 2
HISTORY_LEN    = 60          # Giữ lại 60 điểm lịch sử

# ─── Trạng thái toàn cục ─────────────────────────────────────────────────────
CURRENT_ACTIVE  = 'WEB1'
LAST_SWITCH_TS  = 0.0        # Timestamp lần switch gần nhất (hold-down)
EWMA_VALUE      = 0.0        # Giá trị EWMA hiện tại
TOTAL_SWITCHES  = 0
WEB1_CONNS      = 0          # Least-connections counter
WEB2_CONNS      = 0
LOAD_HISTORY    = deque(maxlen=HISTORY_LEN)   # Lịch sử load % theo thời gian

# ─── Argparse ────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser(description='Load Balancer Monitor (Mức 3.4+4.1)')
ap.add_argument('--node',      default='r_out')
ap.add_argument('--iface',     default='rout-eth2')
ap.add_argument('--pid',       default='')
ap.add_argument('--r_out_pid', default='')
ap.add_argument('--demo',      action='store_true')
ap.add_argument('--maxbw',     type=float, default=100.0)
ap.add_argument('--algorithm', choices=['threshold','ewma','proactive','least_conn'],
                default='ewma', help='Thuật toán cân bằng tải')
args = ap.parse_args()


# =============================================================================
# 1. Đọc băng thông từ /sys namespace
# =============================================================================
_last_bytes: dict = {}
_last_ts:    dict = {}

def read_iface_bytes(pid: str, iface: str, rx: bool = True) -> int:
    stat = 'rx_bytes' if rx else 'tx_bytes'
    try:
        if pid:
            out = subprocess.check_output(
                ['nsenter', '-t', pid, '-n', '--', 'cat',
                 f'/sys/class/net/{iface}/statistics/{stat}'],
                stderr=subprocess.DEVNULL, text=True).strip()
        else:
            with open(f'/sys/class/net/{iface}/statistics/{stat}') as f:
                out = f.read().strip()
        return int(out)
    except Exception:
        return -1


def get_throughput_mbps(pid: str, iface: str, rx: bool = True) -> float:
    key = f'{pid}_{iface}_{"rx" if rx else "tx"}'
    now = time.time()
    cur = read_iface_bytes(pid, iface, rx)
    if cur < 0:
        return -1.0
    if key in _last_bytes:
        delta_b = cur - _last_bytes[key]
        delta_t = now - _last_ts[key]
        mbps = (delta_b * 8) / (delta_t * 1_000_000) if delta_t > 0 else 0.0
        _last_bytes[key] = cur
        _last_ts[key]    = now
        return max(0.0, mbps)
    _last_bytes[key] = cur
    _last_ts[key]    = now
    return 0.0


# =============================================================================
# 2. Demo load generator (pattern tự nhiên)
# =============================================================================
def get_load_demo() -> tuple:
    if not hasattr(get_load_demo, '_t'):
        get_load_demo._t = 0
    get_load_demo._t += 1
    t = get_load_demo._t

    wave  = 55 * math.sin(t * 0.12) + 50
    noise = random.uniform(-5, 5)
    w1_raw = max(2, min(98, wave + noise))

    if CURRENT_ACTIVE == 'WEB2':
        w1 = max(0, min(100, w1_raw * 0.28 + noise))
        w2 = max(0, min(100, 100 - w1_raw * 0.28 + noise))
    else:
        w1 = round(w1_raw, 1)
        w2 = round(max(2, random.uniform(3, 12)), 1)
    return round(w1, 1), round(w2, 1)


# =============================================================================
# 3. MỨC 3.4 – EWMA (Exponentially Weighted Moving Average)
#    Làm mượt nhiễu, chống Flapping tốt hơn ngưỡng tĩnh
# =============================================================================
def update_ewma(new_value: float) -> float:
    """
    EWMA: S_t = α × X_t + (1-α) × S_{t-1}
    α nhỏ → phản ứng chậm, mượt hơn (ít flap)
    α lớn → phản ứng nhanh, nhạy hơn
    """
    global EWMA_VALUE
    EWMA_VALUE = EWMA_ALPHA * new_value + (1 - EWMA_ALPHA) * EWMA_VALUE
    return EWMA_VALUE


# =============================================================================
# 4. MỨC 4.1 – Proactive LB bằng Linear Regression (dự đoán xu hướng)
#    Kích hoạt switch TRƯỚC KHI đạt ngưỡng
# =============================================================================
def predict_next_load(history: deque) -> float:
    """
    Dùng Linear Regression trên cửa sổ PREDICT_WIN điểm gần nhất
    để dự đoán giá trị tải tiếp theo.
    Trả về -1 nếu chưa đủ dữ liệu.
    """
    if len(history) < PREDICT_WIN:
        return -1.0

    recent = list(history)[-PREDICT_WIN:]

    if HAS_NUMPY:
        x = np.arange(len(recent), dtype=float)
        y = np.array(recent, dtype=float)
        # Least-squares fit: y = ax + b
        A = np.vstack([x, np.ones(len(x))]).T
        result = np.linalg.lstsq(A, y, rcond=None)
        a, b = result[0]
        # Dự đoán bước tiếp theo (x = PREDICT_WIN)
        predicted = a * PREDICT_WIN + b
    else:
        # Fallback không cần numpy: hồi quy đơn giản
        n  = len(recent)
        sx = sum(range(n))
        sy = sum(recent)
        sx2 = sum(i*i for i in range(n))
        sxy = sum(i*v for i, v in enumerate(recent))
        denom = n * sx2 - sx * sx
        if denom == 0:
            return recent[-1]
        a = (n * sxy - sx * sy) / denom
        b = (sy - a * sx) / n
        predicted = a * n + b

    return max(0.0, min(100.0, predicted))


# =============================================================================
# 5. MỨC 4.1 – Least Connections
# =============================================================================
def get_least_conn_target() -> str:
    """Trả về server có ít kết nối hơn."""
    return 'WEB1' if WEB1_CONNS <= WEB2_CONNS else 'WEB2'


def simulate_conn_update():
    """Cập nhật counter kết nối giả lập theo active server."""
    global WEB1_CONNS, WEB2_CONNS
    if CURRENT_ACTIVE == 'WEB1':
        WEB1_CONNS = max(0, WEB1_CONNS + random.randint(-2, 5))
        WEB2_CONNS = max(0, WEB2_CONNS + random.randint(-3, 1))
    else:
        WEB2_CONNS = max(0, WEB2_CONNS + random.randint(-2, 5))
        WEB1_CONNS = max(0, WEB1_CONNS + random.randint(-3, 1))


# =============================================================================
# 6. Chuyển NAT rule (không thay đổi logic core)
# =============================================================================
def _run_ns(pid: str, *cmd_parts):
    if not pid:
        return
    try:
        subprocess.run(['nsenter', '-t', pid, '-n', '--', *cmd_parts],
                       capture_output=True, timeout=5)
    except Exception as e:
        print(f'  [ERR] nsenter: {e}')


def switch_to(target: str, pid: str = '', r_out_pid: str = ''):
    global CURRENT_ACTIVE, LAST_SWITCH_TS, TOTAL_SWITCHES, WEB1_CONNS, WEB2_CONNS
    if CURRENT_ACTIVE == target:
        return

    # Hold-down timer check (chống Flapping)
    elapsed_since_switch = time.time() - LAST_SWITCH_TS
    if elapsed_since_switch < HOLD_DOWN_SEC:
        remaining = HOLD_DOWN_SEC - elapsed_since_switch
        print(f'  ⏳ Hold-down timer: {remaining:.0f}s còn lại – bỏ qua switch')
        return

    dest_ip = WEB1_IP if target == 'WEB1' else WEB2_IP
    old_ip  = WEB1_IP if CURRENT_ACTIVE == 'WEB1' else WEB2_IP
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'\n  ⚡ [{ts}] SWITCH: {CURRENT_ACTIVE} → {target}  (→{dest_ip})')

    nat_pid = r_out_pid or pid
    if nat_pid:
        _run_ns(nat_pid, 'iptables', '-t', 'nat', '-D', 'PREROUTING',
                '-d', PUBLIC_VIP, '-p', 'tcp', '--dport', '80',
                '-j', 'DNAT', '--to-destination', f'{old_ip}:80')
        _run_ns(nat_pid, 'iptables', '-t', 'nat', '-I', 'PREROUTING', '1',
                '-d', PUBLIC_VIP, '-p', 'tcp', '--dport', '80',
                '-j', 'DNAT', '--to-destination', f'{dest_ip}:80')

    CURRENT_ACTIVE   = target
    LAST_SWITCH_TS   = time.time()
    TOTAL_SWITCHES  += 1
    # Reset least-conn khi chuyển
    if target == 'WEB2':
        WEB2_CONNS = max(0, WEB1_CONNS // 2)
    else:
        WEB1_CONNS = max(0, WEB2_CONNS // 2)


# =============================================================================
# 7. Vòng lặp giám sát chính
# =============================================================================
def monitor_loop():
    global CURRENT_ACTIVE

    pid      = args.pid
    rout_pid = args.r_out_pid
    algo     = args.algorithm

    # Header
    print('╔══════════════════════════════════════════════════════════════════╗')
    print('║  LOAD BALANCER MONITOR v2 – Campus 3-Layer (Mức 3.4 + 4.1)      ║')
    print(f'║  Thuật toán: {algo:<10}  Ngưỡng: {THRESHOLD_HIGH}%/{THRESHOLD_LOW}%'
          f'  Hold-down: {HOLD_DOWN_SEC}s          ║')
    print(f'║  EWMA α={EWMA_ALPHA}   Dự đoán cửa sổ={PREDICT_WIN} bước'
          f'   Proactive margin={PROACTIVE_MARGIN}%        ║')
    print('╚══════════════════════════════════════════════════════════════════╝')
    print(f'  {"Thời gian":<10} {"Web1(Mbps)":<12} {"Web2(Mbps)":<12}'
          f' {"EWMA%":<8} {"Predict%":<10} {"Active":<8} Hành động')
    print('  ' + '─' * 78)

    with open(LOG_FILE, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[
            'timestamp', 'web1_mbps', 'web2_mbps',
            'ewma_pct', 'predicted_pct', 'active_server',
            'w1_conns', 'w2_conns', 'action', 'algorithm'
        ])
        writer.writeheader()

        step = 0
        while True:
            step += 1
            ts = datetime.now().strftime('%H:%M:%S')

            # ── Đọc tải ──────────────────────────────────────────────────
            if args.demo:
                web1_mbps, web2_mbps = get_load_demo()
                web1_mbps = web1_mbps * args.maxbw / 100
                web2_mbps = web2_mbps * args.maxbw / 100
            else:
                web1_mbps = get_throughput_mbps(pid, args.iface, rx=True)
                web2_mbps = get_throughput_mbps(pid, args.iface, rx=False)
                web1_mbps = max(0.0, web1_mbps)
                web2_mbps = max(0.0, web2_mbps)

            load_pct = (web1_mbps / args.maxbw * 100) if args.maxbw > 0 else 0

            # ── EWMA ─────────────────────────────────────────────────────
            ewma_pct = update_ewma(load_pct)
            LOAD_HISTORY.append(load_pct)

            # ── Dự đoán (Linear Regression) ──────────────────────────────
            predicted = predict_next_load(LOAD_HISTORY)

            # ── Cập nhật Least-Conn ───────────────────────────────────────
            simulate_conn_update()

            # ── Quyết định chuyển đổi theo thuật toán ────────────────────
            action = 'HOLD'
            effective_load = ewma_pct  # Mặc định dùng EWMA

            if algo == 'threshold':
                # Ngưỡng tĩnh thuần túy (Mức 2 gốc)
                effective_load = load_pct

            elif algo == 'ewma':
                # EWMA làm mượt – chống flapping (Mức 3.4)
                effective_load = ewma_pct

            elif algo == 'proactive':
                # Dự đoán xu hướng – switch trước khi đạt ngưỡng (Mức 4.1)
                if predicted >= 0:
                    effective_load = max(ewma_pct, predicted)
                    if predicted >= (THRESHOLD_HIGH - PROACTIVE_MARGIN) and CURRENT_ACTIVE == 'WEB1':
                        action = f'PROACTIVE-SWITCH (predict={predicted:.0f}%)'
                        switch_to('WEB2', pid, rout_pid)
                    elif predicted < THRESHOLD_LOW and CURRENT_ACTIVE == 'WEB2':
                        action = f'PROACTIVE-RESTORE (predict={predicted:.0f}%)'
                        switch_to('WEB1', pid, rout_pid)

            elif algo == 'least_conn':
                # Least Connections (Mức 4.1)
                best = get_least_conn_target()
                if best != CURRENT_ACTIVE and load_pct > 50:
                    action = f'LEAST-CONN→{best} (w1={WEB1_CONNS},w2={WEB2_CONNS})'
                    switch_to(best, pid, rout_pid)

            # Switch dựa trên effective_load (trừ proactive/least_conn tự xử lý)
            if algo in ('threshold', 'ewma'):
                if effective_load > THRESHOLD_HIGH and CURRENT_ACTIVE == 'WEB1':
                    switch_to('WEB2', pid, rout_pid)
                    action = f'SWITCH→WEB2 ({algo}={effective_load:.0f}%>{THRESHOLD_HIGH}%)'
                elif effective_load < THRESHOLD_LOW and CURRENT_ACTIVE == 'WEB2':
                    switch_to('WEB1', pid, rout_pid)
                    action = f'SWITCH→WEB1 ({algo}={effective_load:.0f}%<{THRESHOLD_LOW}%)'

            # ── Hiển thị ─────────────────────────────────────────────────
            bar_len = min(20, int(ewma_pct / 5))
            bar = ('█' * bar_len + '░' * (20 - bar_len))[:20]
            active_str = '⚡WEB1' if CURRENT_ACTIVE == 'WEB1' else '⚡WEB2'
            pred_str = f'{predicted:.0f}%' if predicted >= 0 else 'N/A'

            print(f'  {ts:<10} {web1_mbps:<12.1f} {web2_mbps:<12.1f}'
                  f' {ewma_pct:<8.1f} {pred_str:<10} {active_str:<8} [{bar}] {action}')

            # ── Ghi CSV ──────────────────────────────────────────────────
            writer.writerow({
                'timestamp':    ts,
                'web1_mbps':    round(web1_mbps, 2),
                'web2_mbps':    round(web2_mbps, 2),
                'ewma_pct':     round(ewma_pct, 2),
                'predicted_pct': round(predicted, 2) if predicted >= 0 else -1,
                'active_server': CURRENT_ACTIVE,
                'w1_conns':     WEB1_CONNS,
                'w2_conns':     WEB2_CONNS,
                'action':       action,
                'algorithm':    algo,
            })
            csvfile.flush()
            time.sleep(INTERVAL_SEC)


# =============================================================================
# Signal handler
# =============================================================================
def _sigint_handler(sig, frame):
    print(f'\n\n  [✓] Dừng. Log: {LOG_FILE}')
    print(f'  [✓] Tổng switches: {TOTAL_SWITCHES}')
    print(f'  [✓] EWMA cuối: {EWMA_VALUE:.1f}%')
    print('  Chạy: python3 plot_charts.py --demo để vẽ biểu đồ\n')
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint_handler)

if __name__ == '__main__':
    monitor_loop()
