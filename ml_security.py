#!/usr/bin/env python3
# =============================================================================
# ml_security.py – Phát hiện tấn công bằng ML + Dynamic ACL tự động
# MỨC 4.2: Anomaly Detection (Isolation Forest) + Fail2ban-style Dynamic ACL
# =============================================================================
# Cách chạy:
#   python3 ml_security.py --demo              # Chạy với dữ liệu giả lập
#   sudo python3 ml_security.py --live         # Phân tích dmesg thực
#   sudo python3 ml_security.py --live --inject # Inject iptables rule tự động
# =============================================================================

import os, sys, time, re, csv, signal, argparse, subprocess, random, math
from datetime import datetime
from collections import defaultdict, deque
from pathlib import Path

# Thư mục chứa script này (để resolve đường dẫn tuyệt đối)
_SCRIPT_DIR = Path(__file__).parent

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    print('[WARN] scikit-learn chưa cài. Dùng: pip install scikit-learn numpy')
    print('[WARN] Chạy ở chế độ rule-based thay thế.\n')

# ─── Cấu hình ────────────────────────────────────────────────────────────────
BLOCK_THRESHOLD     = 50      # Số gói bị DROP trong cửa sổ → nghi ngờ tấn công
WINDOW_SEC          = 30      # Cửa sổ thời gian phân tích (giây)
WHITELIST_IPS       = {'172.16.10.1', '172.16.20.1', '10.10.10.1'}
BLOCKED_IPS_FILE    = '/tmp/dynamic_blocked_ips.txt'
ATTACK_LOG_CSV      = str(_SCRIPT_DIR / 'attack_log.csv')  # Đường dẫn tuyệt đối
ANOMALY_SENSITIVITY = 0.1     # IsolationForest contamination (0.1 = 10% bất thường)
INTERVAL_SEC        = 3

ap = argparse.ArgumentParser(description='ML Security Monitor (Mức 4.2)')
ap.add_argument('--demo',   action='store_true', help='Dữ liệu giả lập')
ap.add_argument('--live',   action='store_true', help='Phân tích dmesg thực')
ap.add_argument('--inject', action='store_true', help='Tự động inject iptables rule')
ap.add_argument('--node',   default='r_out',     help='Mininet node áp ACL')
ap.add_argument('--pid',    default='',          help='PID node (nsenter)')
args = ap.parse_args()

# ─── Trạng thái ──────────────────────────────────────────────────────────────
blocked_ips:       set              = set()
ip_drop_counts:    defaultdict      = defaultdict(int)
ip_time_series:    defaultdict      = defaultdict(lambda: deque(maxlen=20))
traffic_history:   list             = []          # Feature vectors cho ML
total_blocked:     int              = 0


# =============================================================================
# 1. Đọc log từ dmesg (thực)
# =============================================================================
def parse_dmesg_drops() -> dict:
    """
    Trích xuất các gói bị DROP từ dmesg (kernel log).
    Trả về dict: {src_ip: count}
    """
    counts = defaultdict(int)
    try:
        output = subprocess.check_output(
            ['dmesg', '--time-format=iso'],
            stderr=subprocess.DEVNULL, text=True
        )
        # Pattern: log-prefix="EXT_ACL_DROP..." SRC=x.x.x.x
        pattern = re.compile(
            r'(?:EXT_ACL|FW_BORDER|FW_SCAN|STD_ACL).*?SRC=(\d+\.\d+\.\d+\.\d+)'
        )
        for line in output.split('\n'):
            m = pattern.search(line)
            if m:
                src_ip = m.group(1)
                if src_ip not in WHITELIST_IPS:
                    counts[src_ip] += 1
    except Exception as e:
        pass
    return counts


# =============================================================================
# 2. Demo: sinh traffic giả lập (normal + attack)
# =============================================================================
class TrafficSimulator:
    """Sinh lưu lượng giả lập: một số IP bình thường, một số IP tấn công."""

    NORMAL_IPS  = ['172.16.10.10', '172.16.20.10', '172.16.10.30']
    ATTACK_IPS  = ['203.0.113.100', '10.0.0.99', '198.51.100.5']
    PROBE_IPS   = ['192.168.99.1',  '192.168.99.2']

    def __init__(self):
        self.t       = 0
        self.phase   = 'normal'  # normal → ramp_up → attack → cool_down
        self.phase_t = 0

    def next_tick(self) -> dict:
        self.t       += 1
        self.phase_t += 1

        # Chuyển phase mỗi 15 bước
        if self.phase_t > 15:
            self.phase_t = 0
            phases = ['normal', 'ramp_up', 'attack', 'cool_down']
            idx = phases.index(self.phase)
            self.phase = phases[(idx + 1) % len(phases)]

        counts = defaultdict(int)

        if self.phase == 'normal':
            for ip in self.NORMAL_IPS:
                counts[ip] += random.randint(0, 3)

        elif self.phase == 'ramp_up':
            for ip in self.NORMAL_IPS:
                counts[ip] += random.randint(0, 5)
            counts[self.ATTACK_IPS[0]] += random.randint(5, 20)

        elif self.phase == 'attack':
            for ip in self.NORMAL_IPS:
                counts[ip] += random.randint(0, 4)
            # Tấn công dồn dập
            counts[self.ATTACK_IPS[0]] += random.randint(40, 80)
            counts[self.ATTACK_IPS[1]] += random.randint(20, 50)
            counts[self.PROBE_IPS[0]]  += random.randint(10, 30)

        elif self.phase == 'cool_down':
            for ip in self.NORMAL_IPS:
                counts[ip] += random.randint(0, 3)
            counts[self.ATTACK_IPS[0]] += random.randint(0, 8)

        return dict(counts)


# =============================================================================
# 3. Feature engineering cho ML
# =============================================================================
def extract_features(ip: str, counts: dict, window_history: list) -> list:
    """
    Trích xuất đặc trưng từ traffic của một IP:
    [drop_count, rate_of_change, burst_factor, is_external]
    """
    current = counts.get(ip, 0)
    hist    = ip_time_series[ip]
    hist.append(current)

    # Rate of change (delta so với bước trước)
    rate = (hist[-1] - hist[-2]) if len(hist) >= 2 else 0

    # Burst factor (so với trung bình lịch sử)
    avg_hist = sum(hist) / len(hist) if hist else 1
    burst    = current / avg_hist if avg_hist > 0 else 0

    # Is external IP (không thuộc private range)
    parts = ip.split('.')
    is_ext = 0
    if parts[0] not in ('10', '172', '192'):
        is_ext = 1
    elif parts[0] == '172' and not (16 <= int(parts[1]) <= 31):
        is_ext = 1

    return [current, rate, burst, is_ext]


# =============================================================================
# 4. Isolation Forest – Phát hiện bất thường
# =============================================================================
_model = None
_scaler = None
_model_trained: bool    = False

def train_or_update_model(feature_matrix: list):
    """Huấn luyện/cập nhật Isolation Forest từ dữ liệu lịch sử."""
    global _model, _scaler, _model_trained

    if not HAS_SKLEARN or len(feature_matrix) < 10:
        return

    X = np.array(feature_matrix, dtype=float)
    _scaler = StandardScaler()
    X_scaled = _scaler.fit_transform(X)
    _model = IsolationForest(
        contamination=ANOMALY_SENSITIVITY,
        n_estimators=100,
        random_state=42,
        n_jobs=-1
    )
    _model.fit(X_scaled)
    _model_trained = True


def is_anomaly(features: list) -> tuple:
    """
    Kiểm tra xem features có phải bất thường không.
    Trả về (is_anomaly: bool, score: float)
    score < 0 → anomaly, gần -1 → rất bất thường
    """
    if not HAS_SKLEARN or not _model_trained or _model is None:
        # Rule-based fallback
        drop_count = features[0]
        burst      = features[2]
        return drop_count > BLOCK_THRESHOLD, -(drop_count / 100)

    X = np.array([features], dtype=float)
    X_scaled = _scaler.transform(X)
    pred   = _model.predict(X_scaled)[0]    # 1=normal, -1=anomaly
    score  = _model.score_samples(X_scaled)[0]
    return (pred == -1), score


# =============================================================================
# 5. Dynamic ACL – Inject rule tự động (Fail2ban-style)
# =============================================================================
def inject_block_rule(ip: str):
    """Chèn DROP rule vào iptables của r_out (hoặc node chỉ định)."""
    global total_blocked

    if ip in blocked_ips or ip in WHITELIST_IPS:
        return

    ts = datetime.now().strftime('%H:%M:%S')
    print(f'\n  🚫 [{ts}] AUTO-BLOCK: {ip} → DROP rule injected')

    if args.inject and args.pid:
        try:
            subprocess.run([
                'nsenter', '-t', args.pid, '-n', '--',
                'iptables', '-I', 'FORWARD', '1',
                '-s', ip, '-j', 'DROP'
            ], check=True, capture_output=True, timeout=5)
            print(f'       iptables: -I FORWARD 1 -s {ip} -j DROP [OK]')
        except Exception as e:
            print(f'       nsenter failed: {e}')
    elif args.inject:
        # Thử qua sudo m
        try:
            subprocess.run(
                ['sudo', 'm', args.node, 'iptables', '-I', 'FORWARD', '1',
                 '-s', ip, '-j', 'DROP'],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

    blocked_ips.add(ip)
    total_blocked += 1

    # Ghi vào file để theo dõi
    with open(BLOCKED_IPS_FILE, 'a') as f:
        f.write(f'{ts} BLOCKED {ip}\n')


# =============================================================================
# 6. Vòng lặp giám sát chính
# =============================================================================
def monitor_loop():
    sim = TrafficSimulator() if args.demo else None

    print('╔══════════════════════════════════════════════════════════════════╗')
    print('║  ML SECURITY MONITOR – Anomaly Detection + Dynamic ACL (Mức 4.2)║')
    mode = 'DEMO (giả lập)' if args.demo else 'LIVE (dmesg thực)'
    inject_str = 'AUTO-INJECT' if args.inject else 'LOG-ONLY'
    print(f'║  Mode: {mode:<20}  ACL: {inject_str:<15}               ║')
    algo = 'Isolation Forest (scikit-learn)' if HAS_SKLEARN else 'Rule-based fallback'
    print(f'║  Algorithm: {algo:<53}║')
    print('╚══════════════════════════════════════════════════════════════════╝')
    print(f'\n  {"Thời gian":<10} {"IP Nguồn":<18} {"Drops":<8}'
          f' {"Score":<8} {"Verdict":<12} Hành động')
    print('  ' + '─' * 70)

    with open(ATTACK_LOG_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'timestamp', 'src_ip', 'drop_count', 'anomaly_score',
            'is_anomaly', 'action', 'algorithm'
        ])
        writer.writeheader()

        feature_buffer = []
        step = 0

        while True:
            step += 1
            ts = datetime.now().strftime('%H:%M:%S')

            # ── Thu thập dữ liệu ─────────────────────────────────────────
            if args.demo:
                counts = sim.next_tick()
            else:
                counts = parse_dmesg_drops()

            # Cập nhật tổng
            for ip, cnt in counts.items():
                ip_drop_counts[ip] += cnt

            # ── Phân tích từng IP ─────────────────────────────────────────
            all_ips = set(counts.keys()) | set(ip_drop_counts.keys())

            for ip in sorted(all_ips):
                if ip in blocked_ips:
                    continue

                features = extract_features(ip, counts, feature_buffer)
                feature_buffer.append(features)

                # Huấn luyện model khi đủ dữ liệu
                if len(feature_buffer) >= 20 and step % 5 == 0:
                    train_or_update_model(feature_buffer[-100:])

                anomaly, score = is_anomaly(features)
                drop_now = counts.get(ip, 0)

                if drop_now == 0 and not anomaly:
                    continue  # Bỏ qua IP không có activity

                verdict  = '🔴 ATTACK' if anomaly else '🟢 NORMAL'
                action   = 'HOLD'

                if anomaly:
                    inject_block_rule(ip)
                    action = 'AUTO-BLOCKED' if args.inject else 'DETECTED'

                score_str = f'{score:.3f}'
                print(f'  {ts:<10} {ip:<18} {drop_now:<8}'
                      f' {score_str:<8} {verdict:<12} {action}')

                writer.writerow({
                    'timestamp':     ts,
                    'src_ip':        ip,
                    'drop_count':    drop_now,
                    'anomaly_score': round(score, 4),
                    'is_anomaly':    int(anomaly),
                    'action':        action,
                    'algorithm':     'IsolationForest' if HAS_SKLEARN else 'RuleBased',
                })
                f.flush()

            # ── Trạng thái tổng ───────────────────────────────────────────
            print(f'  {"─"*70}')
            print(f'  Step={step}  Blocked IPs: {len(blocked_ips)}  '
                  f'Total blocks: {total_blocked}  '
                  f'Model: {"TRAINED" if _model_trained else "PENDING"}')
            if blocked_ips:
                print(f'  Blocked: {", ".join(sorted(blocked_ips))}')
            print()

            time.sleep(INTERVAL_SEC)


# =============================================================================
# Signal handler
# =============================================================================
def _sigint_handler(sig, frame):
    print(f'\n\n  [✓] Dừng giám sát.')
    print(f'  [✓] Tổng IP bị block: {total_blocked}')
    print(f'  [✓] Log: {ATTACK_LOG_CSV}')
    if blocked_ips:
        print(f'  [✓] Danh sách IP bị block: {", ".join(sorted(blocked_ips))}')
    print('  Chạy: python3 plot_charts.py --demo để vẽ heatmap tấn công\n')
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint_handler)

if __name__ == '__main__':
    if not args.demo and not args.live:
        print('[INFO] Không có --demo hay --live → tự bật --demo')
        args.demo = True
    monitor_loop()
