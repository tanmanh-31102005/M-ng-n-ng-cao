#!/bin/bash
# =============================================================================
# setup_ospf.sh – Khởi động FRRouting (OSPF) trên tất cả router Mininet
# =============================================================================
# Các fix đã áp dụng:
#   1. Copy config sang /tmp/ vì user 'frr' không đọc được ~/MNC/
#   2. Sửa "frr version 9.0" → "frr version 8.4.4" (version thực tế)
#   3. Dùng --vty_socket riêng từng node để tránh socket conflict
#   4. Dùng chown frr:frr cho thư mục vty socket
#
# Cách chạy (sau khi sudo python3 topology.py đang chạy):
#   sudo bash setup_ospf.sh
#
# Kiểm tra kết quả:
#   sudo vtysh --vty_socket /tmp/frr-core-vty  -c "show ip ospf neighbor"
#   sudo vtysh --vty_socket /tmp/frr-core-vty  -c "show ip route ospf"
#   sudo vtysh --vty_socket /tmp/frr-dist1-vty -c "show ip ospf neighbor"
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err()     { echo -e "${RED}[ERR]${RESET}  $*"; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║      SETUP_OSPF.SH – FRR/OSPF Campus Network           ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""

# =============================================================================
# BƯỚC 1: Kiểm tra môi trường
# =============================================================================
info "Bước 1: Kiểm tra môi trường..."

FRR_ZEBRA=$(find /usr/lib/frr /usr/sbin -name "zebra" 2>/dev/null | head -1)
FRR_OSPFD=$(find /usr/lib/frr /usr/sbin -name "ospfd" 2>/dev/null | head -1)

[ -z "$FRR_ZEBRA" ] && { err "Không tìm thấy zebra. Cài: sudo apt install frr"; exit 1; }
[ -z "$FRR_OSPFD" ] && { err "Không tìm thấy ospfd. Cài: sudo apt install frr"; exit 1; }

# Lấy version FRR thực tế đang cài
FRR_VERSION=$("$FRR_ZEBRA" --version 2>&1 | grep -oP '\d+\.\d+[\.\d]*' | head -1)
[ -z "$FRR_VERSION" ] && FRR_VERSION="8.4.4"
success "FRR version: $FRR_VERSION | zebra: $FRR_ZEBRA"

# Kiểm tra Mininet đang chạy
sudo m core hostname &>/dev/null || { err "Mininet chưa chạy! Hãy chạy: sudo python3 topology.py trước."; exit 1; }
success "Mininet đang chạy."

# =============================================================================
# BƯỚC 2: Dọn dẹp FRR cũ
# =============================================================================
info "Bước 2: Dọn dẹp FRR cũ..."
sudo killall -9 zebra ospfd 2>/dev/null || true
sudo rm -f /tmp/zserv-*.api /tmp/zebra-*.pid /tmp/ospfd-*.pid
sleep 1
success "Đã dọn dẹp."

# =============================================================================
# BƯỚC 3: Copy config sang /tmp/ và sửa version
# =============================================================================
# FIX 1: user 'frr' (sau khi daemon drop privilege) không đọc được ~/MNC/
# FIX 2: config ghi "frr version 9.0" nhưng thực tế cài 8.4.4 → phải sửa
# =============================================================================
info "Bước 3: Chuẩn bị config files trong /tmp/..."

declare -A CONF_MAP=(
    ["core"]="frr_core.conf"
    ["dist1"]="frr_dist1.conf"
    ["dist2"]="frr_dist2.conf"
    ["dmz"]="frr_dmz.conf"
    ["rout"]="frr_rout.conf"
)

for SUFFIX in core dist1 dist2 dmz rout; do
    SRC="$SCRIPT_DIR/${CONF_MAP[$SUFFIX]}"
    DST="/tmp/${CONF_MAP[$SUFFIX]}"

    if [ ! -f "$SRC" ]; then
        warn "  Không tìm thấy: $SRC → Bỏ qua"
        continue
    fi

    sudo cp "$SRC" "$DST"
    # Sửa version cho khớp với FRR đang cài
    sudo sed -i "s/frr version [0-9][0-9.]*/frr version $FRR_VERSION/" "$DST"
    sudo chown frr:frr "$DST"
    sudo chmod 644 "$DST"
    success "  /tmp/${CONF_MAP[$SUFFIX]}  (version → $FRR_VERSION)"
done

# =============================================================================
# BƯỚC 4: Tạo thư mục vty socket riêng từng node
# =============================================================================
# FIX 3: Nhiều ospfd dùng chung 1 socket → "OSPF not enabled"
# FIX 4: Thư mục phải thuộc frr:frr vì daemon drop privilege
# =============================================================================
info "Bước 4: Tạo vty socket directories..."

for SUFFIX in core dist1 dist2 dmz rout; do
    sudo mkdir -p /tmp/frr-${SUFFIX}-vty
    sudo chown frr:frr /tmp/frr-${SUFFIX}-vty 2>/dev/null || sudo chmod 777 /tmp/frr-${SUFFIX}-vty
done
success "Đã tạo /tmp/frr-{core,dist1,dist2,dmz,rout}-vty/ (owner: frr)"

# =============================================================================
# BƯỚC 5: Hàm khởi động zebra + ospfd trên 1 node
# =============================================================================
start_frr_node() {
    local NODE="$1"
    local CONF_FILE="/tmp/$2"
    local SUFFIX="$3"

    local API="/tmp/zserv-${SUFFIX}.api"
    local ZPID="/tmp/zebra-${SUFFIX}.pid"
    local OPID="/tmp/ospfd-${SUFFIX}.pid"
    local VTYDIR="/tmp/frr-${SUFFIX}-vty"

    if [ ! -f "$CONF_FILE" ]; then
        warn "  Config không tồn tại: $CONF_FILE → Bỏ qua $NODE"
        return 1
    fi

    info "  [$NODE] zebra..."
    sudo m "$NODE" "$FRR_ZEBRA" \
        -f "$CONF_FILE" -d \
        -z "$API" -i "$ZPID" \
        --vty_socket "$VTYDIR" \
        2>/dev/null
    sleep 1

    info "  [$NODE] ospfd..."
    sudo m "$NODE" "$FRR_OSPFD" \
        -f "$CONF_FILE" -d \
        -z "$API" -i "$OPID" \
        --vty_socket "$VTYDIR" \
        2>/dev/null
    sleep 1

    success "  $NODE → OK  (vty: $VTYDIR)"
}

# =============================================================================
# BƯỚC 6: Khởi động từng router
# =============================================================================
info "Bước 5: Khởi động FRR..."
echo ""

start_frr_node "core"  "frr_core.conf"  "core"
start_frr_node "dist1" "frr_dist1.conf" "dist1"
start_frr_node "dist2" "frr_dist2.conf" "dist2"
start_frr_node "dmz_r" "frr_dmz.conf"  "dmz"
start_frr_node "r_out" "frr_rout.conf" "rout"

# =============================================================================
# BƯỚC 7: Chờ OSPF hội tụ
# =============================================================================
echo ""
info "Bước 6: Chờ OSPF hội tụ (25 giây)..."
for i in $(seq 25 -1 1); do
    printf "\r  Còn lại: %2d giây...  " "$i"
    sleep 1
done
echo ""

# =============================================================================
# BƯỚC 8: Kiểm tra kết quả
# =============================================================================
info "Bước 7: Kiểm tra OSPF..."
echo ""

check_node() {
    local NODE="$1"; local SUFFIX="$2"
    echo -e "  ${BOLD}─── OSPF Neighbor [$NODE] ───${RESET}"
    sudo vtysh --vty_socket "/tmp/frr-${SUFFIX}-vty" \
        -c "show ip ospf neighbor" 2>/dev/null || \
        warn "  $NODE: vtysh chưa kết nối được"
    echo ""
}

check_node "core"  "core"
check_node "dist1" "dist1"

echo -e "  ${BOLD}─── IP Route OSPF [core] ───${RESET}"
sudo vtysh --vty_socket /tmp/frr-core-vty -c "show ip route ospf" 2>/dev/null

echo ""
echo -e "  ${BOLD}─── Ping: h1 → web1 (10.10.10.11) ───${RESET}"
sudo m h1 ping -c 3 -W 1 10.10.10.11 2>/dev/null || warn "Ping thất bại."

# =============================================================================
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ✅ FRR/OSPF đã triển khai xong!${RESET}"
echo    ""
echo    "  Lệnh kiểm tra:"
echo    "    sudo vtysh --vty_socket /tmp/frr-core-vty  -c 'show ip ospf neighbor'"
echo    "    sudo vtysh --vty_socket /tmp/frr-core-vty  -c 'show ip route ospf'"
echo    "    sudo vtysh --vty_socket /tmp/frr-dist1-vty -c 'show ip ospf database'"
echo    "    sudo m h1 ping -c 3 10.10.10.11"
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo ""
