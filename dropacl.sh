#!/bin/bash
# =============================================================================
# dropacl.sh – Bãi bỏ toàn bộ ACL / Firewall rules
# Vị trí: cùng thư mục với topology.py và acl.sh
#
# Cách dùng:
#   sudo bash dropacl.sh
#   hoặc từ Mininet CLI: py drop_acl(net)
# =============================================================================
set -euo pipefail

run_in_ns() {
    local PID="$1"; shift
    if [ -n "${PID}" ] && [ "${PID}" != "0" ]; then
        nsenter -t "${PID}" -n -- "$@"
    else
        echo "[WARN] PID không hợp lệ, bỏ qua: $*"
    fi
}

DIST1_PID="${DIST1_PID:-0}"
DIST2_PID="${DIST2_PID:-0}"
DMZ_R_PID="${DMZ_R_PID:-0}"
R_OUT_PID="${R_OUT_PID:-0}"

if [ "$DIST1_PID" = "0" ]; then
    echo "[ERR] Không có PID. Gọi từ topology.py: py drop_acl(net)"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   DROPACL.SH – Bãi bỏ toàn bộ ACL campus network   ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Flush và reset về ACCEPT cho FORWARD chain
for PID in "$DIST1_PID" "$DIST2_PID" "$DMZ_R_PID" "$R_OUT_PID"; do
    run_in_ns "$PID" iptables -F FORWARD 2>/dev/null || true
    run_in_ns "$PID" iptables -P FORWARD ACCEPT      2>/dev/null || true
    run_in_ns "$PID" iptables -F INPUT  2>/dev/null || true
    run_in_ns "$PID" iptables -F OUTPUT 2>/dev/null || true
done

echo "  ✅ Đã bãi bỏ toàn bộ ACL trên dist1, dist2, dmz_r, r_out."
echo "  Lưu ý: NAT rules vẫn còn hiệu lực."
echo "  Để xem lại: py apply_acl(net) trong Mininet CLI."
echo ""
