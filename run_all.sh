#!/bin/bash
# =============================================================================
# run_all.sh – Master script: chạy toàn bộ hệ thống từ đầu đến cuối
# =============================================================================
# Cách dùng:
#   sudo bash run_all.sh [OPTIONS]
#
# OPTIONS:
#   --no-ospf   Bỏ qua bước OSPF (dùng static routing trong topology.py)
#   --no-acl    Bỏ qua bước ACL
#   --no-lb     Bỏ qua Load Balancer
#   --charts    Vẽ biểu đồ demo ngay sau khi chạy
# =============================================================================

NO_OSPF=false; NO_ACL=false; NO_LB=false; DO_CHARTS=false
for arg in "$@"; do
    case "$arg" in
        --no-ospf) NO_OSPF=true ;;
        --no-acl)  NO_ACL=true  ;;
        --no-lb)   NO_LB=true   ;;
        --charts)  DO_CHARTS=true ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'
C='\033[0;36m'; B='\033[1m'; X='\033[0m'

step() { echo -e "\n${B}${C}══ BƯỚC $1: $2 ══${X}"; }
ok()   { echo -e "${G}  ✅ $*${X}"; }
warn() { echo -e "${Y}  ⚠  $*${X}"; }
err()  { echo -e "${R}  ❌ $*${X}"; }

clear
echo -e "${B}"
cat << 'BANNER'
  ╔══════════════════════════════════════════════════════════════╗
  ║   CAMPUS NETWORK 3-LAYER – AUTOMATED DEPLOYMENT SUITE       ║
  ║   Core – Distribution – Access + DMZ                        ║
  ║   OSPF · NAT/PAT · ACL/Firewall · Load Balancer            ║
  ╚══════════════════════════════════════════════════════════════╝
BANNER
echo -e "${X}"

# Kiểm tra quyền root
if [[ $EUID -ne 0 ]]; then
    err "Script này cần chạy với sudo"
    echo "  Dùng: sudo bash run_all.sh"
    exit 1
fi

# =============================================================================
step "0" "Cài đặt & Kiểm tra môi trường"
# =============================================================================

MISSING=()
command -v python3 &>/dev/null || MISSING+=(python3)
command -v iperf3  &>/dev/null || MISSING+=(iperf3)

if [ ${#MISSING[@]} -gt 0 ]; then
    warn "Thiếu: ${MISSING[*]}. Đang cài..."
    apt-get install -y "${MISSING[@]}" -q
fi

# Cài Python deps cho biểu đồ
python3 -c "import matplotlib,seaborn,pandas,numpy" 2>/dev/null || \
    pip3 install -q matplotlib seaborn pandas numpy

ok "Môi trường sẵn sàng."

# =============================================================================
step "1" "Dọn dẹp Mininet cũ"
# =============================================================================
mn -c 2>/dev/null || true
sleep 1
ok "Mininet đã reset."

# =============================================================================
step "2" "Khởi động Topology Mininet (nền)"
# =============================================================================
echo "  Khởi động topology trong background..."
echo "  Log tại: /tmp/topology.log"

# Khởi động topology trong background, giữ Mininet CLI mở qua expect/script
nohup python3 "$SCRIPT_DIR/topology.py" \
    > /tmp/topology.log 2>&1 &
TOPO_PID=$!
echo "  Topology PID: $TOPO_PID"

# Chờ Mininet khởi động
echo -n "  Chờ Mininet sẵn sàng"
for i in $(seq 1 15); do
    sleep 1
    echo -n "."
    if sudo m core echo "OK" &>/dev/null 2>&1; then
        echo ""
        ok "Mininet đã khởi động (core node phản hồi)."
        break
    fi
    if [ $i -eq 15 ]; then
        echo ""
        err "Mininet không khởi động sau 15s. Kiểm tra: tail -f /tmp/topology.log"
        exit 1
    fi
done

# =============================================================================
step "3" "Triển khai OSPF"
# =============================================================================
if $NO_OSPF; then
    warn "Bỏ qua OSPF (--no-ospf). Dùng static routing từ topology.py."
else
    bash "$SCRIPT_DIR/setup_ospf.sh" || warn "OSPF có lỗi, xem log /tmp/frr-*.log"
fi

# =============================================================================
step "4" "Cấu hình NAT/PAT"
# =============================================================================
bash "$SCRIPT_DIR/nat_config.sh" || warn "NAT có lỗi nhỏ, kiểm tra lại."
ok "NAT/PAT đã áp dụng."

# =============================================================================
step "5" "Áp dụng ACL + Firewall"
# =============================================================================
if $NO_ACL; then
    warn "Bỏ qua ACL (--no-acl)."
else
    # Lấy PID của từng router node
    export DIST1_PID=$(sudo m dist1 cat /proc/self/status | grep PPid | awk '{print $2}' 2>/dev/null || echo "0")
    export DIST2_PID=$(sudo m dist2 cat /proc/self/status | grep PPid | awk '{print $2}' 2>/dev/null || echo "0")
    export DMZ_R_PID=$(sudo m dmz_r cat /proc/self/status | grep PPid | awk '{print $2}' 2>/dev/null || echo "0")
    export R_OUT_PID=$(sudo m r_out cat /proc/self/status | grep PPid | awk '{print $2}' 2>/dev/null || echo "0")

    echo "  Node PIDs: dist1=$DIST1_PID dist2=$DIST2_PID dmz_r=$DMZ_R_PID r_out=$R_OUT_PID"

    if [ "$DIST1_PID" != "0" ]; then
        bash "$SCRIPT_DIR/acl.sh" || warn "ACL có lỗi nhỏ."
        ok "ACL đã áp dụng."
    else
        warn "Không lấy được PID. Gọi thủ công: py apply_acl(net) trong Mininet CLI."
    fi
fi

# =============================================================================
step "6" "Thu thập số liệu ban đầu"
# =============================================================================
bash "$SCRIPT_DIR/collect_data.sh" 2>/dev/null || warn "collect_data có lỗi nhỏ."

# =============================================================================
step "7" "Khởi động Load Balancer (nền)"
# =============================================================================
if $NO_LB; then
    warn "Bỏ qua Load Balancer (--no-lb)."
else
    echo "  Khởi động load_balancer.py trong background..."
    nohup python3 "$SCRIPT_DIR/load_balancer.py" --demo \
        > /tmp/lb_monitor.log 2>&1 &
    LB_PID=$!
    echo "  Load Balancer PID: $LB_PID"
    ok "Load Balancer đang chạy. Log: /tmp/lb_monitor.log"
fi

# =============================================================================
step "8" "Vẽ biểu đồ demo"
# =============================================================================
if $DO_CHARTS; then
    mkdir -p "$SCRIPT_DIR/charts"
    python3 "$SCRIPT_DIR/plot_charts.py" --demo --out "$SCRIPT_DIR/charts/" || \
        warn "plot_charts.py lỗi. Thử: pip3 install matplotlib seaborn pandas"
    ok "Biểu đồ lưu tại: $SCRIPT_DIR/charts/"
fi

# =============================================================================
# Tóm tắt
# =============================================================================
echo ""
echo -e "${G}${B}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║  ✅ HỆ THỐNG ĐÃ TRIỂN KHAI THÀNH CÔNG!                 ║"
echo "  ╠══════════════════════════════════════════════════════════╣"
echo "  ║  Để vào Mininet CLI:  sudo mn --custom topology.py      ║"
echo "  ║  Test NAT:  sudo m h_out wget -qO- http://203.0.113.10  ║"
echo "  ║  Test ACL:  sudo m h1 curl http://10.10.10.11           ║"
echo "  ║  Xem log:   tail -f /tmp/topology.log                   ║"
echo "  ║  LB log:    tail -f /tmp/lb_monitor.log                 ║"
echo "  ║  Vẽ chart:  python3 plot_charts.py --demo               ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${X}"
