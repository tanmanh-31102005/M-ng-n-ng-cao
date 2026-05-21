#!/usr/bin/env python3
# =============================================================================
# topology.py – Campus 3-Layer Network (Core / Distribution / Access + DMZ)
# MỨC 3+4: Thêm Redundant Links, ECMP, OSPF Convergence Demo, HA
# =============================================================================
import os, sys, time, subprocess, argparse, signal, traceback
from mininet.topo  import Topo
from mininet.net   import Mininet
from mininet.node  import Node
from mininet.log   import setLogLevel, info, error
from mininet.cli   import CLI
from mininet.link  import TCLink

parser = argparse.ArgumentParser(description='Campus 3-Layer Topology')
parser.add_argument('--acl',  action='store_true')
parser.add_argument('--nat',  action='store_true')
parser.add_argument('--lb',   action='store_true')
parser.add_argument('--demo', action='store_true', help='LB demo mode')
args, unknown = parser.parse_known_args()

# ─────────────────────────────────────────────────────────────────────────────
class LinuxRouter(Node):
    def config(self, **params):
        super().config(**params)
        self.cmd('sysctl -w net.ipv4.ip_forward=1')
    def terminate(self):
        self.cmd('sysctl -w net.ipv4.ip_forward=0')
        super().terminate()

# =============================================================================
# TOPOLOGY – Thêm redundant links cho HA (Mức 3.1)
#
#  OUTSIDE: r_out ── h_out
#       |
#  CORE: core ─────────────────────────────────────
#       |          |         |          |
#  dist1 ═══ dist2 (ECMP)  dmz_r     r_out
#   |    ╲  /  |
#  acc1  ╲/  acc2   ← Cross-links: acc1-dist2, acc2-dist1 (redundant)
#   |          |
#  h1,p1      h2,phone1
# =============================================================================
class CampusTopo(Topo):
    def build(self):
        # Routers
        core  = self.addNode('core',  cls=LinuxRouter, ip='192.168.1.1/30')
        dist1 = self.addNode('dist1', cls=LinuxRouter, ip='192.168.1.2/30')
        dist2 = self.addNode('dist2', cls=LinuxRouter, ip='192.168.1.6/30')
        dmz_r = self.addNode('dmz_r', cls=LinuxRouter, ip='192.168.1.10/30')
        r_out = self.addNode('r_out', cls=LinuxRouter, ip='192.168.100.2/30')

        # Access Switches
        acc1   = self.addSwitch('acc1',   dpid='0000000000000001', failMode='standalone')
        acc2   = self.addSwitch('acc2',   dpid='0000000000000002', failMode='standalone')
        sw_dmz = self.addSwitch('sw_dmz', dpid='0000000000000003', failMode='standalone')
        sw_out = self.addSwitch('sw_out', dpid='0000000000000004', failMode='standalone')

        # Hosts
        h1       = self.addHost('h1',       ip='172.16.10.10/24', defaultRoute='via 172.16.10.1')
        h2       = self.addHost('h2',       ip='172.16.20.10/24', defaultRoute='via 172.16.20.1')
        phone1   = self.addHost('phone1',   ip='172.16.20.20/24', defaultRoute='via 172.16.20.1')
        printer1 = self.addHost('printer1', ip='172.16.10.30/24', defaultRoute='via 172.16.10.1')
        web1     = self.addHost('web1',     ip='10.10.10.11/24',  defaultRoute='via 10.10.10.1')
        web2     = self.addHost('web2',     ip='10.10.10.12/24',  defaultRoute='via 10.10.10.1')
        h_out    = self.addHost('h_out',    ip='203.0.113.100/24',defaultRoute='via 203.0.113.1')

        # ── PRIMARY Links – Core Backbone ────────────────────────────────────
        self.addLink(core, dist1,
            intfName1='core-eth1', intfName2='dist1-eth1',
            params1={'ip':'192.168.1.1/30'}, params2={'ip':'192.168.1.2/30'},
            bw=1000, delay='1ms', use_htb=True)

        self.addLink(core, dist2,
            intfName1='core-eth2', intfName2='dist2-eth1',
            params1={'ip':'192.168.1.5/30'}, params2={'ip':'192.168.1.6/30'},
            bw=1000, delay='1ms', use_htb=True)

        # ── ECMP: Second links Core ↔ dist1/dist2 (Mức 3.1 + 4.3) ───────────
        self.addLink(core, dist1,
            intfName1='core-eth5', intfName2='dist1-eth3',
            params1={'ip':'192.168.2.1/30'}, params2={'ip':'192.168.2.2/30'},
            bw=1000, delay='1ms', use_htb=True)

        self.addLink(core, dist2,
            intfName1='core-eth6', intfName2='dist2-eth3',
            params1={'ip':'192.168.2.5/30'}, params2={'ip':'192.168.2.6/30'},
            bw=1000, delay='1ms', use_htb=True)

        self.addLink(core, dmz_r,
            intfName1='core-eth3', intfName2='dmz-eth1',
            params1={'ip':'192.168.1.9/30'}, params2={'ip':'192.168.1.10/30'},
            bw=1000, delay='1ms', use_htb=True)

        self.addLink(core, r_out,
            intfName1='core-eth4', intfName2='rout-eth1',
            params1={'ip':'192.168.100.1/30'}, params2={'ip':'192.168.100.2/30'},
            bw=500, delay='10ms', use_htb=True)

        # ── Distribution → Access (Primary) ─────────────────────────────────
        self.addLink(dist1, acc1, intfName1='dist1-eth2', bw=100, delay='2ms', use_htb=True)
        self.addLink(dist2, acc2, intfName1='dist2-eth2', bw=100, delay='2ms', use_htb=True)

        # ── REDUNDANT Cross-Links: acc1↔dist2, acc2↔dist1 (Mức 3.1 HA) ─────
        self.addLink(dist2, acc1, intfName1='dist2-eth4', bw=100, delay='2ms', use_htb=True)
        self.addLink(dist1, acc2, intfName1='dist1-eth4', bw=100, delay='2ms', use_htb=True)

        self.addLink(dmz_r, sw_dmz, intfName1='dmz-eth2',  bw=1000, delay='1ms', use_htb=True)
        self.addLink(r_out, sw_out, intfName1='rout-eth2', bw=500, delay='10ms', use_htb=True)

        # Hosts → Switches
        self.addLink(h1,       acc1,   bw=100, delay='2ms', use_htb=True)
        self.addLink(printer1, acc1,   bw=100, delay='2ms', use_htb=True)
        self.addLink(h2,       acc2,   bw=100, delay='2ms', use_htb=True)
        self.addLink(phone1,   acc2,   bw=100, delay='2ms', use_htb=True)
        self.addLink(web1,     sw_dmz, bw=1000, delay='1ms', use_htb=True)
        self.addLink(web2,     sw_dmz, bw=1000, delay='1ms', use_htb=True)
        self.addLink(h_out,    sw_out, bw=500, delay='10ms', use_htb=True)


# =============================================================================
# Cấu hình IP và định tuyến
# =============================================================================
def configure_interfaces(net):
    info('*** [CONFIG] Gán IP gateway...\n')
    net.get('dist1').cmd('ip addr add 172.16.10.1/24 dev dist1-eth2')
    net.get('dist2').cmd('ip addr add 172.16.20.1/24 dev dist2-eth2')
    net.get('dmz_r').cmd('ip addr add 10.10.10.1/24  dev dmz-eth2')
    net.get('r_out').cmd('ip addr add 203.0.113.1/24  dev rout-eth2')

    # IP cho redundant interfaces
    net.get('dist2').cmd('ip addr add 172.16.10.254/24 dev dist2-eth4 2>/dev/null || true')
    net.get('dist1').cmd('ip addr add 172.16.20.254/24 dev dist1-eth4 2>/dev/null || true')


def configure_static_routes(net):
    info('*** [CONFIG] Static routes (fallback khi OSPF chưa hội tụ)...\n')
    core  = net.get('core')
    dist1 = net.get('dist1')
    dist2 = net.get('dist2')
    dmz_r = net.get('dmz_r')
    r_out = net.get('r_out')

    core.cmd('ip route add 172.16.10.0/24 via 192.168.1.2')
    core.cmd('ip route add 172.16.20.0/24 via 192.168.1.6')
    core.cmd('ip route add 10.10.10.0/24  via 192.168.1.10')
    core.cmd('ip route add 203.0.113.0/24 via 192.168.100.2')

    dist1.cmd('ip route add default        via 192.168.1.1')
    dist1.cmd('ip route add 172.16.20.0/24 via 192.168.1.1')
    dist1.cmd('ip route add 10.10.10.0/24  via 192.168.1.1')
    dist1.cmd('ip route add 203.0.113.0/24 via 192.168.1.1')

    dist2.cmd('ip route add default        via 192.168.1.5')
    dist2.cmd('ip route add 172.16.10.0/24 via 192.168.1.5')
    dist2.cmd('ip route add 10.10.10.0/24  via 192.168.1.5')
    dist2.cmd('ip route add 203.0.113.0/24 via 192.168.1.5')

    dmz_r.cmd('ip route add default        via 192.168.1.9')
    dmz_r.cmd('ip route add 172.16.10.0/24 via 192.168.1.9')
    dmz_r.cmd('ip route add 172.16.20.0/24 via 192.168.1.9')
    dmz_r.cmd('ip route add 203.0.113.0/24 via 192.168.1.9')

    r_out.cmd('ip route add 172.16.0.0/16  via 192.168.100.1')
    r_out.cmd('ip route add 10.10.10.0/24  via 192.168.100.1')
    r_out.cmd('ip route add 192.168.1.0/24 via 192.168.100.1')
    info('    [OK] Static routes OK\n')


# =============================================================================
# NAT / PAT
# =============================================================================
def apply_nat(net):
    info('*** [NAT] Áp dụng PAT + Static NAT...\n')
    r_out = net.get('r_out')
    r_out.cmd('iptables -P FORWARD ACCEPT')
    r_out.cmd('iptables -t nat -A POSTROUTING -s 172.16.0.0/16 -o rout-eth2 -j MASQUERADE')
    r_out.cmd('iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o rout-eth2 -j MASQUERADE')
    r_out.cmd('iptables -t nat -A POSTROUTING -s 192.168.0.0/16 -o rout-eth2 -j MASQUERADE')

    r_out.cmd('ip addr add 203.0.113.10/24 dev rout-eth2 2>/dev/null || true')
    r_out.cmd('ip addr add 203.0.113.11/24 dev rout-eth2 2>/dev/null || true')

    r_out.cmd('iptables -t nat -A PREROUTING -d 203.0.113.10 -p tcp --dport 80  -j DNAT --to-destination 10.10.10.11:80')
    r_out.cmd('iptables -t nat -A PREROUTING -d 203.0.113.10 -p tcp --dport 443 -j DNAT --to-destination 10.10.10.11:443')
    r_out.cmd('iptables -t nat -A PREROUTING -d 203.0.113.11 -p tcp --dport 80  -j DNAT --to-destination 10.10.10.12:80')
    r_out.cmd('iptables -t nat -A PREROUTING -d 203.0.113.11 -p tcp --dport 443 -j DNAT --to-destination 10.10.10.12:443')
    r_out.cmd('iptables -t nat -A POSTROUTING -j LOG --log-prefix "NAT_EVENT: " --log-level 4')
    info('    [OK] NAT/PAT OK\n')


def show_nat_table(net):
    r_out = net.get('r_out')
    SEP = '─' * 62
    print(f'\n{SEP}')
    print('  [1] iptables NAT rules trên r_out')
    print(SEP)
    print(r_out.cmd('iptables -t nat -L -n -v --line-numbers 2>&1'))
    print(f'{SEP}')
    print('  [2] IP aliases trên rout-eth2')
    print(SEP)
    print(r_out.cmd('ip addr show dev rout-eth2 2>&1'))
    print(f'{SEP}')
    print('  [3] Conntrack sessions')
    print(SEP)
    check = r_out.cmd('which conntrack 2>/dev/null')
    if check.strip():
        print(r_out.cmd('conntrack -L 2>&1'))
    else:
        proc = r_out.cmd('cat /proc/net/nf_conntrack 2>/dev/null | head -20')
        print(proc if proc.strip() else '  (Chưa có session – hãy sinh traffic trước)')
    print(f'{SEP}\n')


# =============================================================================
# ACL
# =============================================================================
def apply_acl(net):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'acl.sh')
    if not os.path.exists(script):
        error(f'[ACL] Không tìm thấy {script}!\n'); return
    info('*** [ACL] Áp dụng ACL + Firewall...\n')
    pids = {
        'DIST1_PID': str(net.get('dist1').pid),
        'DIST2_PID': str(net.get('dist2').pid),
        'DMZ_R_PID': str(net.get('dmz_r').pid),
        'R_OUT_PID': str(net.get('r_out').pid),
    }
    result = subprocess.run(['bash', script], env={**os.environ, **pids},
                            capture_output=True, text=True)
    if result.returncode == 0:
        info('    [OK] ACL OK\n')
    else:
        error(f'    [ERR] acl.sh:\n{result.stderr}\n')


def drop_acl(net):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dropacl.sh')
    if not os.path.exists(script):
        error(f'[ACL] Không tìm thấy {script}!\n'); return
    info('*** [ACL] Bãi bỏ ACL...\n')
    pids = {
        'DIST1_PID': str(net.get('dist1').pid),
        'DIST2_PID': str(net.get('dist2').pid),
        'DMZ_R_PID': str(net.get('dmz_r').pid),
        'R_OUT_PID': str(net.get('r_out').pid),
    }
    subprocess.run(['bash', script], env={**os.environ, **pids})
    info('    [OK] ACL đã bãi bỏ\n')


# =============================================================================
# Web Servers
# =============================================================================
def start_web_servers(net):
    info('*** [WEB] Khởi động HTTP server giả lập...\n')
    web1 = net.get('web1')
    web2 = net.get('web2')
    web1.cmd('echo "Server web1 OK" > /tmp/index.html')
    web2.cmd('echo "Server web2 OK" > /tmp/index.html')
    web1.cmd('python3 -m http.server 80 --directory /tmp &> /tmp/web1.log &')
    web2.cmd('python3 -m http.server 80 --directory /tmp &> /tmp/web2.log &')
    time.sleep(1)
    info('    web1: 10.10.10.11:80\n')
    info('    web2: 10.10.10.12:80\n')


# =============================================================================
# Load Balancer
# =============================================================================
def start_load_balancer(net, demo: bool = False):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'load_balancer.py')
    if not os.path.exists(script):
        error(f'[LB] Không tìm thấy {script}!\n'); return
    dmz_r = net.get('dmz_r')
    r_out = net.get('r_out')
    cmd = [
        'python3', script,
        '--node', 'dmz_r', '--iface', 'dmz-eth2',
        '--pid',  str(dmz_r.pid),
        '--r_out_pid', str(r_out.pid),
        '--maxbw', '100',
    ]
    if demo:
        cmd.append('--demo')
    try:
        subprocess.Popen(['xterm', '-title', 'Load Balancer', '-e', ' '.join(cmd)])
        info('    [OK] Load Balancer đang chạy trong xterm\n')
    except FileNotFoundError:
        log_path = '/tmp/lb_monitor.log'
        subprocess.Popen(cmd, stdout=open(log_path, 'w'), stderr=subprocess.STDOUT)
        info(f'    [OK] Load Balancer chạy nền → {log_path}\n')


# =============================================================================
# MỨC 3.1 – Demo Link Failure & OSPF Convergence Measurement
# =============================================================================
def demo_link_failure(net, src='core', dst='dist1'):
    """
    Đánh sập link giữa src↔dst, đo thời gian OSPF hội tụ,
    sau đó khôi phục link.

    Dùng trong CLI: py demo_link_failure(net)
    hoặc: py demo_link_failure(net, 'core', 'dist2')
    """
    import threading

    h1   = net.get('h1')
    web1 = net.get('web1')

    print(f'\n{"═"*60}')
    print(f'  [HA TEST] Đánh sập link: {src} ↔ {dst}')
    print(f'{"═"*60}')

    # Ping baseline trước khi down
    print('  [1] Ping baseline (trước khi down):')
    result = h1.cmd('ping -c 2 -W 1 10.10.10.11')
    loss_before = '0% packet loss' in result
    print(f'      h1 → web1: {"OK" if loss_before else "FAIL"}')

    # Đo thời gian hội tụ
    t_start = time.time()
    net.configLinkStatus(src, dst, 'down')
    print(f'\n  [2] Link {src}↔{dst} → DOWN tại t=0s')
    print('  [3] Đang polling routing...')

    convergence_time = None
    for i in range(60):  # poll tối đa 60s
        time.sleep(0.5)
        result = h1.cmd('ping -c 1 -W 1 10.10.10.11 2>&1')
        elapsed = time.time() - t_start
        ok = '1 received' in result or '0% packet loss' in result
        status = '✓ Reachable' if ok else '✗ Unreachable'
        print(f'      t={elapsed:.1f}s  {status}')
        if ok and elapsed > 0.5:
            convergence_time = elapsed
            break

    if convergence_time:
        print(f'\n  ✅ OSPF Convergence Time: {convergence_time:.2f}s ({convergence_time*1000:.0f}ms)')
    else:
        print(f'\n  ⚠ Không hội tụ trong 30s (kiểm tra OSPF đã chạy chưa)')

    # Khôi phục link
    time.sleep(2)
    net.configLinkStatus(src, dst, 'up')
    print(f'\n  [4] Link {src}↔{dst} → UP (khôi phục)')
    time.sleep(2)

    result_after = h1.cmd('ping -c 2 -W 1 10.10.10.11')
    ok_after = '0% packet loss' in result_after
    print(f'  [5] Ping sau khôi phục: {"✓ OK" if ok_after else "✗ FAIL"}')
    print(f'{"═"*60}\n')

    return convergence_time


# =============================================================================
# MỨC 4.3 – Hiển thị trạng thái ECMP
# =============================================================================
def show_ecmp_routes(net):
    """In bảng định tuyến ECMP trên core router."""
    core = net.get('core')
    print('\n  [ECMP] Bảng định tuyến Core (đa đường):')
    print(core.cmd('ip route show'))
    print('  Lưu ý: 2 đường core-eth1 và core-eth5 đến dist1 = ECMP')


# =============================================================================
# Kiểm tra kết nối
# =============================================================================
def run_connectivity_test(net):
    info('\n*** [TEST] Kiểm tra kết nối cơ bản...\n')
    pairs = [
        ('h1',    'h2'),
        ('h1',    'web1'),
        ('h1',    'web2'),
        ('h_out', 'web1'),
    ]
    for src_name, dst_name in pairs:
        src = net.get(src_name)
        dst = net.get(dst_name)
        result = src.cmd(f'ping -c 2 -W 1 {dst.IP()}')
        ok = '0% packet loss' in result
        info(f'    {src_name} → {dst_name} ({dst.IP()}): {"✓ OK" if ok else "✗ FAIL"}\n')


# =============================================================================
# Custom CLI
# =============================================================================
class CampusCLI(CLI):
    _CAMPUS_FUNCS = {
        'show_nat_table':        show_nat_table,
        'apply_acl':             apply_acl,
        'drop_acl':              drop_acl,
        'apply_nat':             apply_nat,
        'start_web_servers':     start_web_servers,
        'run_connectivity_test': run_connectivity_test,
        'start_load_balancer':   start_load_balancer,
        'demo_link_failure':     demo_link_failure,
        'show_ecmp_routes':      show_ecmp_routes,
    }

    def do_py(self, line):
        ns = {'net': self.mn}
        ns.update(self._CAMPUS_FUNCS)
        try:
            result = eval(line, ns)
            if result is not None:
                print(repr(result))
        except SyntaxError:
            try:
                exec(line, ns)
            except Exception as exc:
                print(f'Error: {exc}'); traceback.print_exc()
        except Exception as exc:
            print(f'Error: {exc}'); traceback.print_exc()


# =============================================================================
# Main
# =============================================================================
def run():
    topo = CampusTopo()
    net  = Mininet(topo=topo, link=TCLink, controller=None)
    net.start()

    configure_interfaces(net)
    configure_static_routes(net)
    start_web_servers(net)
    apply_nat(net)

    if args.acl:
        apply_acl(net)
    if args.lb:
        start_load_balancer(net, demo=args.demo)

    run_connectivity_test(net)

    info('\n')
    info('╔══════════════════════════════════════════════════════════════╗\n')
    info('║       CAMPUS 3-LAYER NETWORK v2 – MININET CLI (Mức 3+4)    ║\n')
    info('╠══════════════════════════════════════════════════════════════╣\n')
    info('║  Lệnh cơ bản:                                               ║\n')
    info('║   py apply_acl(net)               – Áp dụng ACL            ║\n')
    info('║   py drop_acl(net)                – Bãi bỏ ACL             ║\n')
    info('║   py show_nat_table(net)          – Xem bảng NAT           ║\n')
    info('║   py start_load_balancer(net)     – Khởi động LB           ║\n')
    info('║   py start_load_balancer(net, demo=True)  – LB demo        ║\n')
    info('╠══════════════════════════════════════════════════════════════╣\n')
    info('║  Mức 3 – HA & ECMP:                                         ║\n')
    info('║   py demo_link_failure(net)       – Test OSPF convergence  ║\n')
    info('║   py demo_link_failure(net,"core","dist2")                  ║\n')
    info('║   py show_ecmp_routes(net)        – Xem multi-path routes  ║\n')
    info('╠══════════════════════════════════════════════════════════════╣\n')
    info('║  Test Load Balancer:                                        ║\n')
    info('║   h_out iperf3 -c 203.0.113.10 -t 60 -b 95M &             ║\n')
    info('╚══════════════════════════════════════════════════════════════╝\n')

    CampusCLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
