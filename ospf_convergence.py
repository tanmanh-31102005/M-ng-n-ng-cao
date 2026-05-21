#!/usr/bin/env python3
# =============================================================================
# ospf_convergence.py – Đo thời gian hội tụ OSPF khi link down (Mức 3.1)
# =============================================================================
# Cách chạy:
#   # Khi Mininet đang chạy:
#   sudo python3 ospf_convergence.py
#
#   # Hoặc chạy từ Mininet CLI:
#   py demo_link_failure(net)
#
#   # Standalone với dữ liệu demo:
#   python3 ospf_convergence.py --demo
# =============================================================================

import sys, time, subprocess, argparse, csv, os
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_OUT = _SCRIPT_DIR / 'charts'

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

ap = argparse.ArgumentParser(description='OSPF Convergence Measurement (Mức 3.1)')
ap.add_argument('--demo',     action='store_true', help='Dùng dữ liệu giả lập')
ap.add_argument('--src',      default='core',      help='Node nguồn của link')
ap.add_argument('--dst',      default='dist1',     help='Node đích của link')
ap.add_argument('--target',   default='10.10.10.11',help='IP đích để ping test')
ap.add_argument('--probe-src',default='h1',        help='Node dùng để ping')
ap.add_argument('--interval', type=float, default=0.5, help='Polling interval (s)')
ap.add_argument('--timeout',  type=int,   default=60,  help='Timeout (s)')
ap.add_argument('--out', default=str(_DEFAULT_OUT), help='Thư mục xuất kết quả (mặc định: charts/)')
args = ap.parse_args()

OUT_DIR = Path(args.out)
OUT_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# 1. Helpers Mininet
# =============================================================================
def mn_cmd(node: str, cmd: str) -> str:
    """Chạy lệnh trên Mininet node qua 'sudo m'."""
    try:
        result = subprocess.run(
            ['sudo', 'm', node, 'bash', '-c', cmd],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout + result.stderr
    except Exception as e:
        return f'ERROR: {e}'


def set_link_status(src: str, dst: str, status: str):
    """Thay đổi trạng thái link qua Mininet Python API (gọi qua subprocess)."""
    cmd = (
        f"python3 -c \""
        f"from mininet.util import quietRun; "
        f"import sys; "
        f"# Sử dụng ip link để down/up interface\""
    )
    # Thực tế dùng ifconfig down trên interface
    if status == 'down':
        mn_cmd(src, f'ip link set {src}-eth1 down 2>/dev/null || true')
        mn_cmd(dst, f'ip link set {dst}-eth1 down 2>/dev/null || true')
    else:
        mn_cmd(src, f'ip link set {src}-eth1 up 2>/dev/null || true')
        mn_cmd(dst, f'ip link set {dst}-eth1 up 2>/dev/null || true')


def ping_test(probe_node: str, target_ip: str) -> bool:
    """Trả về True nếu ping thành công."""
    output = mn_cmd(probe_node, f'ping -c 1 -W 1 {target_ip}')
    return '1 received' in output or '0% packet loss' in output


# =============================================================================
# 2. Đo thời gian hội tụ thực
# =============================================================================
def measure_convergence_live() -> dict:
    """
    Thực thi đo convergence time trên Mininet thực.
    Returns dict với kết quả đo.
    """
    print(f'\n{"═"*60}')
    print(f'  OSPF CONVERGENCE TEST – {args.src} ↔ {args.dst}')
    print(f'  Probe: {args.probe_src} → {args.target}')
    print(f'{"═"*60}')

    timeline = []  # (elapsed_sec, ping_ok, event)

    # Baseline ping
    print('\n  [1] Kiểm tra baseline...')
    baseline_ok = ping_test(args.probe_src, args.target)
    print(f'      Baseline ping: {"✓ OK" if baseline_ok else "✗ FAIL"}')
    if not baseline_ok:
        print('  ⚠ Baseline đã lỗi – kiểm tra topology trước')

    timeline.append((0.0, baseline_ok, 'BASELINE'))

    # Link down
    print(f'\n  [2] Down link: {args.src} ↔ {args.dst}')
    t_start = time.time()
    set_link_status(args.src, args.dst, 'down')
    print(f'      Link DOWN tại t=0.00s')

    # Polling
    print('\n  [3] Polling kết nối...')
    convergence_time  = None
    first_fail_time   = None
    consecutive_ok    = 0
    CONFIRM_REQUIRED  = 3  # Số lần OK liên tiếp để xác nhận hội tụ

    for step in range(int(args.timeout / args.interval)):
        time.sleep(args.interval)
        elapsed = time.time() - t_start
        ok = ping_test(args.probe_src, args.target)
        status = '✓' if ok else '✗'

        if not ok and first_fail_time is None:
            first_fail_time = elapsed
            timeline.append((elapsed, False, 'LINK_FAIL_DETECTED'))
            print(f'      t={elapsed:.1f}s  {status}  ← Đường chính mất')
        else:
            timeline.append((elapsed, ok, ''))

        print(f'      t={elapsed:.2f}s  {status}  {"OK" if ok else "Unreachable"}')

        if ok:
            consecutive_ok += 1
            if consecutive_ok >= CONFIRM_REQUIRED and convergence_time is None:
                convergence_time = elapsed
                timeline[-1] = (elapsed, True, 'CONVERGENCE')
                print(f'\n  ✅ OSPF CONVERGED ở t={elapsed:.2f}s ({elapsed*1000:.0f}ms)!')
                break
        else:
            consecutive_ok = 0

    if convergence_time is None:
        print(f'\n  ⚠ Không hội tụ trong {args.timeout}s')
        convergence_time = args.timeout

    # Link up
    print(f'\n  [4] Khôi phục link {args.src} ↔ {args.dst}...')
    time.sleep(1)
    set_link_status(args.src, args.dst, 'up')
    time.sleep(3)
    final_ok = ping_test(args.probe_src, args.target)
    print(f'      Sau khôi phục: {"✓ OK" if final_ok else "✗ FAIL"}')
    timeline.append((convergence_time + 5, final_ok, 'LINK_RESTORED'))

    result = {
        'src_node':        args.src,
        'dst_node':        args.dst,
        'convergence_sec': round(convergence_time, 3),
        'convergence_ms':  round(convergence_time * 1000, 1),
        'first_fail_sec':  round(first_fail_time, 3) if first_fail_time else 0,
        'restore_ok':      final_ok,
        'timeline':        timeline,
    }
    return result


# =============================================================================
# 3. Dữ liệu giả lập (demo mode)
# =============================================================================
def generate_demo_data() -> dict:
    """Sinh dữ liệu hội tụ OSPF giả lập thực tế."""
    import random, math
    random.seed(42)

    # Thực tế OSPF với FRR hội tụ ~2-5s (dead interval 40s), hoặc ~1s với BFD
    convergence_time = random.uniform(2.1, 4.8)
    first_fail_time  = random.uniform(0.3, 0.8)

    timeline = [(0.0, True, 'BASELINE')]
    t = 0.0
    converged = False

    while t < convergence_time + 6:
        t += 0.5
        if t < first_fail_time:
            ok    = True
            event = ''
        elif t < convergence_time:
            ok    = False
            event = 'LINK_FAIL_DETECTED' if abs(t - first_fail_time) < 0.6 else ''
        elif not converged:
            ok       = True
            event    = 'CONVERGENCE'
            converged = True
        else:
            ok    = True
            event = ''
        timeline.append((round(t, 1), ok, event))

    timeline.append((convergence_time + 5, True, 'LINK_RESTORED'))

    print('\n  [DEMO] Dữ liệu OSPF Convergence giả lập:')
    print(f'  Link: {args.src} ↔ {args.dst}')
    print(f'  Convergence time: {convergence_time:.2f}s ({convergence_time*1000:.0f}ms)')
    print(f'  Thời gian phát hiện lỗi: {first_fail_time:.2f}s')
    print('\n  Timeline:')
    for t, ok, event in timeline:
        status = '✓' if ok else '✗'
        tag    = f'  ← {event}' if event else ''
        print(f'    t={t:.1f}s  {status}  {"OK" if ok else "Unreachable"}{tag}')

    return {
        'src_node':        args.src,
        'dst_node':        args.dst,
        'convergence_sec': round(convergence_time, 3),
        'convergence_ms':  round(convergence_time * 1000, 1),
        'first_fail_sec':  round(first_fail_time, 3),
        'restore_ok':      True,
        'timeline':        timeline,
    }


# =============================================================================
# 4. Vẽ biểu đồ kết quả
# =============================================================================
def plot_convergence(result: dict):
    if not HAS_MPL:
        print('[WARN] matplotlib chưa cài – bỏ qua vẽ biểu đồ')
        return

    timeline = result['timeline']
    t_vals   = [r[0] for r in timeline]
    ok_vals  = [1 if r[1] else 0 for r in timeline]
    events   = {r[0]: r[2] for r in timeline if r[2]}

    conv_t   = result['convergence_sec']
    fail_t   = result['first_fail_sec']

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7),
                                    gridspec_kw={'height_ratios': [2, 1]})

    # ── Subplot 1: Ping status timeline ────────────────────────────────────
    colors = ['#43A047' if v else '#E53935' for v in ok_vals]
    ax1.bar(t_vals, ok_vals, width=0.4,
            color=colors, alpha=0.85, label='Ping status (1=OK, 0=FAIL)')
    ax1.step(t_vals, ok_vals, where='post', color='#1565C0', lw=2.5, label='Kết nối')

    # Đánh dấu các sự kiện quan trọng
    if fail_t > 0:
        ax1.axvline(fail_t, color='#E53935', ls='--', lw=2, label=f'Link DOWN (t={fail_t:.1f}s)')
        ax1.annotate('Link\nDOWN', xy=(fail_t, 0.5),
                     xytext=(fail_t + 0.3, 0.7),
                     arrowprops=dict(arrowstyle='->', color='#E53935'),
                     color='#E53935', fontsize=9)

    ax1.axvline(conv_t, color='#43A047', ls='--', lw=2,
                label=f'Convergence (t={conv_t:.1f}s / {result["convergence_ms"]:.0f}ms)')
    ax1.annotate('OSPF\nConverged', xy=(conv_t, 0.5),
                 xytext=(conv_t + 0.3, 0.3),
                 arrowprops=dict(arrowstyle='->', color='#43A047'),
                 color='#43A047', fontsize=9)

    ax1.axvspan(fail_t, conv_t, alpha=0.08, color='#E53935', label='Downtime')
    ax1.set_ylabel('Kết nối (1=OK / 0=FAIL)')
    ax1.set_title(
        f'OSPF Convergence Test – Link {result["src_node"]} ↔ {result["dst_node"]}\n'
        f'Convergence Time: {result["convergence_ms"]:.0f}ms | Downtime: '
        f'{(conv_t - fail_t)*1000:.0f}ms',
        fontsize=12, fontweight='bold'
    )
    ax1.set_ylim(-0.1, 1.3)
    ax1.legend(loc='upper right', fontsize=8)
    ax1.set_yticks([0, 1])
    ax1.set_yticklabels(['FAIL', 'OK'])

    # ── Subplot 2: Downtime bar ─────────────────────────────────────────────
    categories  = ['Phát hiện lỗi', 'Hội tụ OSPF', 'Tổng downtime']
    values_ms   = [
        fail_t * 1000,
        (conv_t - fail_t) * 1000,
        conv_t * 1000,
    ]
    bar_colors  = ['#FF7043', '#FFA726', '#E53935']
    bars        = ax2.barh(categories, values_ms, color=bar_colors, alpha=0.85)

    for bar, val in zip(bars, values_ms):
        ax2.text(bar.get_width() + 20, bar.get_y() + bar.get_height()/2,
                 f'{val:.0f}ms', va='center', fontsize=10, fontweight='bold')

    ax2.set_xlabel('Thời gian (ms)')
    ax2.set_title('Phân tích thời gian OSPF Recovery', fontsize=10)
    ax2.set_xlim(0, max(values_ms) * 1.3)

    plt.tight_layout()
    out_path = OUT_DIR / 'chart_ospf_convergence.png'
    plt.savefig(out_path, bbox_inches='tight', dpi=130)
    plt.close()
    print(f'\n  [OK] Biểu đồ lưu tại: {out_path}')


# =============================================================================
# 5. Xuất CSV
# =============================================================================
def export_csv(result: dict):
    csv_path    = OUT_DIR / 'ospf_convergence.csv'
    summary_path = OUT_DIR / 'ospf_convergence_summary.txt'

    try:
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'timestamp', 'elapsed_sec', 'ping_ok', 'event'
            ])
            writer.writeheader()
            ts_base = datetime.now().strftime('%Y-%m-%d')
            for elapsed, ok, event in result['timeline']:
                writer.writerow({
                    'timestamp':   ts_base,
                    'elapsed_sec': elapsed,
                    'ping_ok':     int(ok),
                    'event':       event,
                })
        print(f'  [OK] CSV lưu tại: {csv_path}')
    except PermissionError:
        print(f'  [WARN] Không ghi được {csv_path} (Permission denied)')
        print(f'  [FIX]  Chạy: sudo rm -f {csv_path}')

    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write('=' * 50 + '\n')
            f.write('OSPF CONVERGENCE TEST SUMMARY\n')
            f.write('=' * 50 + '\n')
            f.write(f'Link tested:       {result["src_node"]} <-> {result["dst_node"]}\n')
            f.write(f'Convergence time:  {result["convergence_ms"]:.1f}ms\n')
            f.write(f'First fail detect: {result["first_fail_sec"]*1000:.1f}ms\n')
            f.write(f'OSPF recovery:     {(result["convergence_sec"]-result["first_fail_sec"])*1000:.1f}ms\n')
            f.write(f'Link restored OK:  {"Yes" if result["restore_ok"] else "No"}\n')
            f.write('\nNote: OSPF dead-interval=40s binh thuong → dung BFD de <1s\n')
        print(f'  [OK] Summary: {summary_path}')
    except PermissionError:
        print(f'  [WARN] Không ghi được {summary_path} (Permission denied)')
        print(f'  [FIX]  Chạy: sudo rm -f {summary_path}')


# =============================================================================
# Main
# =============================================================================
def main():
    print('\n' + '═'*60)
    print('  OSPF CONVERGENCE MEASUREMENT (Mức 3.1)')
    print('═'*60)

    if args.demo:
        result = generate_demo_data()
    else:
        # Kiểm tra Mininet đang chạy
        test = subprocess.run(
            ['sudo', 'm', args.probe_src, 'hostname'],
            capture_output=True, text=True
        )
        if test.returncode != 0:
            print('[ERR] Mininet chưa chạy! Dùng --demo hoặc chạy topology.py trước.')
            sys.exit(1)
        result = measure_convergence_live()

    # Xuất kết quả
    print('\n  [RESULT]')
    print(f'  Convergence time: {result["convergence_ms"]:.1f}ms')
    print(f'  (OSPF với FRR thường 2-5s; dùng BFD giảm xuống <1s)')

    export_csv(result)
    plot_convergence(result)

    print('\n  ✅ Hoàn tất đo OSPF Convergence\n')


if __name__ == '__main__':
    main()
