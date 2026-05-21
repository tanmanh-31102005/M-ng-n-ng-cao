#!/bin/bash
# =============================================================================
# nat_config.sh – Cấu hình NAT/PAT và Static NAT cho Campus Network
# =============================================================================
# Cách chạy:  sudo bash nat_config.sh [--show-table]
#   --show-table : in bảng NAT conntrack sau khi cấu hình
#
# Thực hiện PAT (Source NAT / Masquerade) trên r_out cho:
#   - Inside  172.16.0.0/16  → Internet (PAT)
#   - DMZ    10.10.10.0/24  → Internet (PAT cho outbound)
#
# Thực hiện Static NAT (DNAT) tại r_out:
#   - 203.0.113.10:80/443  → 10.10.10.11 (web1)
#   - 203.0.113.11:80/443  → 10.10.10.12 (web2)
# =============================================================================
set -e

SHOW_TABLE=false
[[ "${1:-}" == "--show-table" ]] && SHOW_TABLE=true

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[NAT]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║    NAT_CONFIG.SH – PAT + Static NAT Campus Network       ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""

# =============================================================================
# 1. Bật IP Forwarding & xoá rule cũ
# =============================================================================
info "Bước 1: Reset bảng NAT trên r_out..."

sudo m r_out sysctl -w net.ipv4.ip_forward=1

# Xoá rule cũ để tránh trùng lặp
sudo m r_out iptables -t nat -F PREROUTING  2>/dev/null || true
sudo m r_out iptables -t nat -F POSTROUTING 2>/dev/null || true

success "Đã reset bảng NAT."

# =============================================================================
# 2. PAT – Source NAT (MASQUERADE) cho mạng Inside và DMZ
# =============================================================================
info "Bước 2: Cấu hình PAT (MASQUERADE) – Inside + DMZ → Internet..."

# VLAN 10 + VLAN 20 (172.16.0.0/16) → ra rout-eth2 (203.0.113.1)
sudo m r_out iptables -t nat -A POSTROUTING \
    -s 172.16.0.0/16 -o rout-eth2 -j MASQUERADE

# DMZ outbound (server chủ động kết nối ra ngoài, vd cập nhật)
sudo m r_out iptables -t nat -A POSTROUTING \
    -s 10.10.10.0/24 -o rout-eth2 -j MASQUERADE

# Link P2P backbone (nếu cần debug)
sudo m r_out iptables -t nat -A POSTROUTING \
    -s 192.168.0.0/16 -o rout-eth2 -j MASQUERADE

success "PAT đã cấu hình cho 172.16.0.0/16 và 10.10.10.0/24."

# =============================================================================
# 3. Static NAT – Thêm IP Public ảo lên rout-eth2
# =============================================================================
info "Bước 3: Thêm IP Public ảo (203.0.113.10, .11) lên rout-eth2..."

sudo m r_out ip addr add 203.0.113.10/24 dev rout-eth2 2>/dev/null || true
sudo m r_out ip addr add 203.0.113.11/24 dev rout-eth2 2>/dev/null || true

success "IP Public ảo đã gán."

# =============================================================================
# 4. Static NAT – DNAT cho web1 và web2
# =============================================================================
info "Bước 4: Cấu hình DNAT (Static NAT) cho DMZ servers..."

# web1 → 10.10.10.11
sudo m r_out iptables -t nat -A PREROUTING \
    -d 203.0.113.10 -p tcp --dport 80 \
    -j DNAT --to-destination 10.10.10.11:80
sudo m r_out iptables -t nat -A PREROUTING \
    -d 203.0.113.10 -p tcp --dport 443 \
    -j DNAT --to-destination 10.10.10.11:443

# web2 → 10.10.10.12
sudo m r_out iptables -t nat -A PREROUTING \
    -d 203.0.113.11 -p tcp --dport 80 \
    -j DNAT --to-destination 10.10.10.12:80
sudo m r_out iptables -t nat -A PREROUTING \
    -d 203.0.113.11 -p tcp --dport 443 \
    -j DNAT --to-destination 10.10.10.12:443

success "Static NAT: 203.0.113.10 → web1 (10.10.10.11)"
success "Static NAT: 203.0.113.11 → web2 (10.10.10.12)"

# =============================================================================
# 5. LOG cho bảng thống kê NAT (parse bằng dmesg sau)
# =============================================================================
info "Bước 5: Bật LOG cho NAT events..."

sudo m r_out iptables -t nat -A POSTROUTING \
    -j LOG --log-prefix "NAT_PAT_EVENT: " --log-level 4

sudo m r_out iptables -t nat -A PREROUTING \
    -d 203.0.113.10 \
    -j LOG --log-prefix "NAT_STATIC_WEB1: " --log-level 4 || true
sudo m r_out iptables -t nat -A PREROUTING \
    -d 203.0.113.11 \
    -j LOG --log-prefix "NAT_STATIC_WEB2: " --log-level 4 || true

success "LOG NAT đã bật (xem: sudo dmesg | grep NAT_)"

# =============================================================================
# 6. Hiển thị bảng NAT hiện tại
# =============================================================================
echo ""
info "Bước 6: Xem cấu hình NAT hiện tại trên r_out..."
echo ""
echo -e "  ${BOLD}─── PREROUTING (DNAT / Static NAT) ───${RESET}"
sudo m r_out iptables -t nat -L PREROUTING -n -v --line-numbers
echo ""
echo -e "  ${BOLD}─── POSTROUTING (PAT / Masquerade) ───${RESET}"
sudo m r_out iptables -t nat -L POSTROUTING -n -v --line-numbers

# =============================================================================
# 7. Tuỳ chọn: hiển thị bảng conntrack (NAT translation table)
# =============================================================================
if $SHOW_TABLE; then
    echo ""
    info "Bảng NAT conntrack (cần gói conntrack-tools):"
    sudo m r_out conntrack -L 2>/dev/null || \
        echo -e "  ${YELLOW}[HINT] Cài thêm: sudo apt install conntrack${RESET}"
fi

# =============================================================================
# 8. Test nhanh Static NAT
# =============================================================================
echo ""
info "Bước 7: Test nhanh NAT từ h_out → web1 qua Static NAT..."
RESULT=$(sudo m h_out wget -qO- --timeout=3 http://203.0.113.10 2>/dev/null || echo "TIMEOUT")
if echo "$RESULT" | grep -qi "web1\|OK\|html"; then
    success "Static NAT hoạt động! h_out → 203.0.113.10 → web1"
else
    echo -e "  ${YELLOW}[HINT] Có thể web server chưa khởi động."
    echo -e "         Chạy topology.py trước rồi thử lại.${RESET}"
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ✅ NAT/PAT đã cấu hình xong!${RESET}"
echo    ""
echo    "  Lệnh kiểm tra thêm:"
echo    "    sudo m h1 curl http://10.10.10.11     # Inside → DMZ trực tiếp"
echo    "    sudo m h_out curl http://203.0.113.10 # Outside → DMZ (Static NAT)"
echo    "    sudo m h1 curl http://203.0.113.100   # Inside → Outside (PAT)"
echo    "    sudo dmesg | grep NAT_                # Xem log NAT events"
echo    "    sudo m r_out conntrack -L             # Bảng NAT conntrack"
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo ""
