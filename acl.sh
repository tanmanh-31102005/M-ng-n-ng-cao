#!/bin/bash
# =============================================================================
# acl.sh – ACL đa lớp (Standard + Extended + Firewall)
# MỨC 3.3: Thêm attack simulation + log chi tiết timestamp+port cho heatmap
# MỨC 3.3: Giải thích placement logic (Extended gần nguồn, Standard gần đích)
# =============================================================================
# Cách dùng:
#   sudo bash acl.sh            ← áp dụng ACL
#   sudo bash acl.sh drop       ← bãi bỏ ACL (gọi dropacl.sh)
#   sudo bash acl.sh simulate   ← chạy kịch bản tấn công giả lập (heatmap data)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${RESET}   $*"; }
info() { echo -e "  ${CYAN}[INFO]${RESET} $*"; }
warn() { echo -e "  ${YELLOW}[WARN]${RESET} $*"; }
err()  { echo -e "  ${RED}[ERR]${RESET}  $*"; }

# Helper chạy iptables trong namespace Mininet node
ipt() {
    local NODE="$1"; shift
    sudo m "$NODE" iptables "$@" 2>/dev/null || \
        warn "[$NODE] iptables $* → bỏ qua"
}

# =============================================================================
# Xử lý tham số
# =============================================================================
ACTION="${1:-apply}"

if [[ "$ACTION" == "drop" ]]; then
    exec sudo bash "$(dirname "$0")/dropacl.sh"
fi

if [[ "$ACTION" == "simulate" ]]; then
    # Kịch bản tấn công giả lập – sinh dữ liệu cho heatmap (Mức 3.3)
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}║  ATTACK SIMULATION – Ping Sweep / Port Scan vào DMZ ║${RESET}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""

    ATTACK_LOG="/tmp/attack_sim_$(date +%Y%m%d_%H%M%S).log"
    info "Log lưu tại: $ATTACK_LOG"

    # Kiểm tra Mininet
    if ! sudo m h_out hostname &>/dev/null; then
        err "Mininet chưa chạy! Chạy: sudo python3 topology.py trước."
        exit 1
    fi

    info "Bắt đầu Ping sweep từ h_out (203.0.113.100) vào DMZ..."

    # Giả lập các gói bị block sẽ xuất hiện trong dmesg
    for PORT in 22 23 3306 8080 6379 5432; do
        for IP in 10.10.10.11 10.10.10.12; do
            echo "[$(date '+%H:%M:%S')] SRC=203.0.113.100 DST=$IP DPORT=$PORT ACTION=DROP" >> "$ATTACK_LOG"
            sudo m h_out bash -c "timeout 1 bash -c 'cat < /dev/null > /dev/tcp/$IP/$PORT' 2>/dev/null || true" &
        done
    done

    # Ping sweep từ unknown source (giả lập)
    for i in $(seq 11 15); do
        IP="10.10.10.$i"
        echo "[$(date '+%H:%M:%S')] SRC=203.0.113.100 DST=$IP TYPE=PING ACTION=DROP" >> "$ATTACK_LOG"
        sudo m h_out ping -c 1 -W 1 "$IP" &>/dev/null || true
    done

    wait
    ok "Attack simulation hoàn tất. Log: $ATTACK_LOG"
    info "Dùng: python3 plot_charts.py --demo để vẽ heatmap từ dữ liệu này"
    exit 0
fi

# =============================================================================
# Kiểm tra Mininet
# =============================================================================
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   ACL.SH v2 – Bảo mật đa lớp Campus Network         ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""

info "Kiểm tra Mininet..."
if ! sudo m core hostname &>/dev/null; then
    err "Mininet chưa chạy! Chạy: sudo python3 topology.py trước."
    exit 1
fi
ok "Mininet đang hoạt động."
echo ""

# =============================================================================
# GIẢI THÍCH PLACEMENT LOGIC (Mức 3.3)
# ─────────────────────────────────────────────────────────────────────────────
# WHY: Standard ACL chỉ lọc theo IP nguồn → đặt GẦN ĐÍCH để tránh chặn nhầm
#      traffic hợp lệ đến đích khác (nếu đặt gần nguồn sẽ block toàn bộ)
#
# WHY: Extended ACL lọc IP+Port+Protocol → đặt GẦN NGUỒN để loại bỏ gói rác
#      NGAY TẠI NƠI PHÁT SINH, tiết kiệm băng thông backbone (Cost-efficiency)
#
# Minh chứng bằng số:
#   - Nếu Extended ACL đặt ở dmz_r (gần đích):
#     Gói rác vẫn tốn bandwidth: Access→Dist→Core→dmz_r → DROP
#     Waste: 3 hops × bandwidth
#   - Nếu Extended ACL đặt ở r_out (gần nguồn):
#     DROP ngay tại biên: r_out → không vào mạng nội bộ
#     Save: 3 hops × bandwidth ≈ 0 overhead nội bộ
# =============================================================================

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${CYAN}  PLACEMENT LOGIC (Mức 3.3):${RESET}"
echo    "  Standard ACL  → đặt GẦN ĐÍCH   (dist1/dist2 = Distribution Layer)"
echo    "  Extended ACL  → đặt GẦN NGUỒN  (r_out biên, dmz_r vùng DMZ)"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

# =============================================================================
# LỚP 1: STANDARD ACL – Distribution Layer (gần ĐÍCH)
# =============================================================================
echo -e "${BOLD}▶ [LỚP 1] Standard ACL – Distribution Layer (gần đích)${RESET}"

ipt dist1 -F FORWARD
ipt dist1 -P FORWARD ACCEPT

# LOG + DROP thiết bị xấu giả lập (với timestamp trong prefix)
ipt dist1 -A FORWARD -s 172.16.10.50 \
    -j LOG --log-prefix "STD_ACL_BLOCK_VLAN10 SRC=172.16.10.50: " --log-level 4
ipt dist1 -A FORWARD -s 172.16.10.50 -j DROP

# Cho phép VLAN10
ipt dist1 -A FORWARD -s 172.16.10.0/24 -j ACCEPT
ipt dist1 -A FORWARD -d 172.16.10.0/24 -j ACCEPT

ok "dist1: Block 172.16.10.50, ACCEPT /24 còn lại."

ipt dist2 -F FORWARD
ipt dist2 -P FORWARD ACCEPT

ipt dist2 -A FORWARD -s 172.16.20.50 \
    -j LOG --log-prefix "STD_ACL_BLOCK_VLAN20 SRC=172.16.20.50: " --log-level 4
ipt dist2 -A FORWARD -s 172.16.20.50 -j DROP

ipt dist2 -A FORWARD -s 172.16.20.0/24 -j ACCEPT
ipt dist2 -A FORWARD -d 172.16.20.0/24 -j ACCEPT

ok "dist2: Block 172.16.20.50, ACCEPT /24 còn lại."

# =============================================================================
# LỚP 2: EXTENDED ACL – DMZ Router (gần NGUỒN = biên của DMZ)
# =============================================================================
echo ""
echo -e "${BOLD}▶ [LỚP 2] Extended ACL – DMZ Router (IP+Port+Protocol)${RESET}"

ipt dmz_r -F FORWARD
ipt dmz_r -P FORWARD DROP    # Default deny tại DMZ

# Stateful – established connections
ipt dmz_r -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT

# Inside → DMZ: HTTP/HTTPS + ICMP (hợp lệ)
ipt dmz_r -A FORWARD -s 172.16.0.0/16 -d 10.10.10.0/24 -p tcp --dport 80  -j ACCEPT
ipt dmz_r -A FORWARD -s 172.16.0.0/16 -d 10.10.10.0/24 -p tcp --dport 443 -j ACCEPT
ipt dmz_r -A FORWARD -s 172.16.0.0/16 -d 10.10.10.0/24 -p icmp           -j ACCEPT

# Outside → DMZ: HTTP/HTTPS (qua Static NAT)
ipt dmz_r -A FORWARD -s 203.0.113.0/24 -d 10.10.10.0/24 -p tcp --dport 80  -j ACCEPT
ipt dmz_r -A FORWARD -s 203.0.113.0/24 -d 10.10.10.0/24 -p tcp --dport 443 -j ACCEPT

# LOG + DROP: SSH/Telnet/DB vào DMZ (heatmap data)
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -p tcp --dport 22 \
    -j LOG --log-prefix "EXT_ACL_DROP_SSH DST=DMZ: " --log-level 4
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -p tcp --dport 22   -j DROP
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -p tcp --dport 23 \
    -j LOG --log-prefix "EXT_ACL_DROP_TELNET DST=DMZ: " --log-level 4
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -p tcp --dport 23   -j DROP
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -p tcp --dport 3306 \
    -j LOG --log-prefix "EXT_ACL_DROP_MYSQL DST=DMZ: " --log-level 4
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -p tcp --dport 3306 -j DROP

# LOG + DROP phần còn lại vào DMZ
ipt dmz_r -A FORWARD -d 10.10.10.0/24 \
    -j LOG --log-prefix "EXT_ACL_DROP_DMZ OTHER: " --log-level 4
ipt dmz_r -A FORWARD -d 10.10.10.0/24 -j DROP

ok "dmz_r: HTTP/HTTPS vào DMZ OK. SSH/Telnet/MySQL DROP + LOG."

# =============================================================================
# LỚP 3: FIREWALL BIÊN – r_out (Extended ACL gần NGUỒN = biên Outside)
# =============================================================================
echo ""
echo -e "${BOLD}▶ [LỚP 3] Firewall biên – r_out (gần nguồn Outside)${RESET}"

ipt r_out -F FORWARD
ipt r_out -P FORWARD DROP

# Stateful
ipt r_out -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT

# Inside → Outside: HTTP/HTTPS + DNS + ICMP
ipt r_out -A FORWARD -s 172.16.0.0/16 -o rout-eth2 -p tcp -m multiport --dports 80,443 -j ACCEPT
ipt r_out -A FORWARD -s 172.16.0.0/16 -o rout-eth2 -p udp --dport 53   -j ACCEPT
ipt r_out -A FORWARD -s 172.16.0.0/16 -o rout-eth2 -p icmp              -j ACCEPT

# Outside → DMZ: HTTP/HTTPS (sau DNAT)
ipt r_out -A FORWARD -i rout-eth2 -d 10.10.10.0/24 -p tcp --dport 80  -j ACCEPT
ipt r_out -A FORWARD -i rout-eth2 -d 10.10.10.0/24 -p tcp --dport 443 -j ACCEPT

# Outside → Inside: BLOCK TUYỆT ĐỐI (với log chi tiết)
ipt r_out -A FORWARD -i rout-eth2 -d 172.16.0.0/16 \
    -j LOG --log-prefix "FW_BORDER_BLOCK OUTSIDE->INSIDE: " --log-level 4
ipt r_out -A FORWARD -i rout-eth2 -d 172.16.0.0/16 -j DROP

# Port scan detection: LOG các port bất thường từ Outside
ipt r_out -A FORWARD -i rout-eth2 -p tcp --dport 22 \
    -j LOG --log-prefix "FW_SCAN_DETECT SSH_FROM_OUTSIDE: " --log-level 4
ipt r_out -A FORWARD -i rout-eth2 -p tcp --dport 22 -j DROP
ipt r_out -A FORWARD -i rout-eth2 -p tcp --dport 23 \
    -j LOG --log-prefix "FW_SCAN_DETECT TELNET_FROM_OUTSIDE: " --log-level 4
ipt r_out -A FORWARD -i rout-eth2 -p tcp --dport 23 -j DROP

# LOG phần còn lại
ipt r_out -A FORWARD \
    -j LOG --log-prefix "FW_BORDER_DROP OTHER: " --log-level 4

ok "r_out: Inside→Outside OK. Outside→Inside BLOCK. SSH/Telnet scan LOG."

# =============================================================================
# Kết quả
# =============================================================================
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ✅ ACL đa lớp v2 đã áp dụng!${RESET}"
echo ""
echo    "  Kiểm tra:"
echo    "    sudo m dist1 iptables -L FORWARD -n --line-numbers"
echo    "    sudo m dmz_r iptables -L FORWARD -n --line-numbers"
echo    "    sudo m r_out iptables -L FORWARD -n --line-numbers"
echo ""
echo    "  Xem log bị chặn:"
echo    "    sudo dmesg | grep -E 'EXT_ACL|FW_BORDER|STD_ACL|FW_SCAN'"
echo ""
echo    "  Kịch bản tấn công giả lập (heatmap data):"
echo    "    sudo bash acl.sh simulate"
echo ""
echo    "  Bãi bỏ ACL:"
echo    "    sudo bash dropacl.sh"
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo ""
