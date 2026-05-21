#!/bin/bash
# =============================================================================
# collect_data.sh – Thu thập số liệu thực nghiệm (Throughput, Latency, Logs)
# =============================================================================
# Kết quả lưu vào thư mục ./results/ để vẽ biểu đồ và điền báo cáo
#
# Cách chạy: sudo bash collect_data.sh
# =============================================================================
set -e

RESULTS_DIR="./results"
mkdir -p "$RESULTS_DIR"
TS=$(date +"%Y%m%d_%H%M%S")

GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[DATA]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   COLLECT_DATA.SH – Thu thập số liệu thực nghiệm        ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""

# =============================================================================
# 1. PING LATENCY – đo RTT giữa các cặp quan trọng
# =============================================================================
info "Đo Latency (ping RTT)..."

LATENCY_CSV="$RESULTS_DIR/latency_${TS}.csv"
echo "src,dst,dst_ip,rtt_min_ms,rtt_avg_ms,rtt_max_ms,packet_loss_pct" > "$LATENCY_CSV"

measure_ping() {
    local SRC="$1" DST_NAME="$2" DST_IP="$3"
    local RESULT
    RESULT=$(sudo m "$SRC" ping -c 10 -W 1 "$DST_IP" 2>/dev/null | tail -2)
    # Parse: rtt min/avg/max/mdev = X/X/X/X ms
    local STATS
    STATS=$(echo "$RESULT" | grep -oP 'rtt.*= \K[\d./]+' | tr '/' ' ')
    local MIN AVG MAX LOSS
    read -r MIN AVG MAX _ <<< "$STATS"
    LOSS=$(echo "$RESULT" | grep -oP '\d+(?=% packet loss)' || echo "100")
    MIN="${MIN:-999}"; AVG="${AVG:-999}"; MAX="${MAX:-999}"
    echo "${SRC},${DST_NAME},${DST_IP},${MIN},${AVG},${MAX},${LOSS}" >> "$LATENCY_CSV"
    printf "  %-10s → %-15s (%s): avg=%s ms, loss=%s%%\n" "$SRC" "$DST_NAME" "$DST_IP" "$AVG" "$LOSS"
}

# Kịch bản 1: Không có bảo mật (chạy trước khi bật ACL)
echo ""
echo -e "  ${BOLD}[Kịch bản A] Không bảo mật:${RESET}"
measure_ping "h1"    "web1"   "10.10.10.11"
measure_ping "h1"    "web2"   "10.10.10.12"
measure_ping "h1"    "h_out"  "203.0.113.100"
measure_ping "h_out" "web1"   "10.10.10.11"

success "Latency lưu tại: $LATENCY_CSV"

# =============================================================================
# 2. THROUGHPUT – đo bằng iperf3
# =============================================================================
info "Đo Throughput (iperf3, 10 giây mỗi luồng)..."

THPUT_CSV="$RESULTS_DIR/throughput_${TS}.csv"
echo "scenario,src,dst,direction,bandwidth_mbps,duration_sec" > "$THPUT_CSV"

measure_iperf() {
    local SCENARIO="$1" SERVER_NODE="$2" CLIENT_NODE="$3" SERVER_IP="$4"
    echo ""
    printf "  %-25s %s → %s (%s)\n" "[$SCENARIO]" "$CLIENT_NODE" "$SERVER_NODE" "$SERVER_IP"

    # Khởi động iperf3 server tạm thời
    sudo m "$SERVER_NODE" iperf3 -s -1 -D --logfile /tmp/iperf-server.log &>/dev/null
    sleep 0.5

    # Chạy iperf3 client 10 giây
    local OUTPUT
    OUTPUT=$(sudo m "$CLIENT_NODE" iperf3 -c "$SERVER_IP" -t 10 -f m 2>&1)

    # Parse kết quả: tìm dòng "sender" hoặc "receiver"
    local BW
    BW=$(echo "$OUTPUT" | grep -oP '[\d.]+ Mbits/sec' | tail -1 | grep -oP '[\d.]+' || echo "0")
    echo "${SCENARIO},${CLIENT_NODE},${SERVER_NODE},upload,${BW},10" >> "$THPUT_CSV"
    printf "  %-25s Throughput = %s Mbps\n" "" "$BW"
}

echo ""
echo -e "  ${BOLD}[Scenario 1] Inside → DMZ (HTTP traffic):${RESET}"
measure_iperf "Inside→DMZ_noACL"  "web1"  "h1"    "10.10.10.11"
measure_iperf "Inside→DMZ_noACL"  "web2"  "h1"    "10.10.10.12"

echo ""
echo -e "  ${BOLD}[Scenario 2] Inside → Outside (PAT NAT):${RESET}"
measure_iperf "Inside→Out_NAT"    "h_out" "h1"    "203.0.113.100"

success "Throughput lưu tại: $THPUT_CSV"

# =============================================================================
# 3. THU THẬP LOG NAT
# =============================================================================
info "Thu thập NAT log từ dmesg..."

NAT_LOG="$RESULTS_DIR/nat_log_${TS}.txt"
sudo dmesg | grep -E "NAT_PAT|NAT_STATIC" > "$NAT_LOG" 2>/dev/null || true
echo "Số dòng NAT log: $(wc -l < "$NAT_LOG")"
success "NAT log lưu tại: $NAT_LOG"

# =============================================================================
# 4. THU THẬP LOG ACL / FIREWALL
# =============================================================================
info "Thu thập ACL/Firewall log từ dmesg..."

ACL_LOG="$RESULTS_DIR/acl_log_${TS}.txt"
sudo dmesg | grep -E "STD_ACL|EXT_ACL|FW_BORDER|FW_DROP" > "$ACL_LOG" 2>/dev/null || true
echo "Số dòng ACL log: $(wc -l < "$ACL_LOG")"
success "ACL log lưu tại: $ACL_LOG"

# =============================================================================
# 5. BẢNG TỔNG HỢP IPTABLES COUNTERS
# =============================================================================
info "Xuất bảng iptables counters..."

IPTABLES_CSV="$RESULTS_DIR/iptables_stats_${TS}.csv"
echo "node,chain,rule,pkts,bytes" > "$IPTABLES_CSV"

for NODE in dist1 dist2 dmz_r r_out; do
    # Lấy FORWARD chain với counter
    sudo m "$NODE" iptables -L FORWARD -n -v --line-numbers 2>/dev/null | \
    grep -v '^Chain\|^pkts\|^$' | \
    awk -v NODE="$NODE" '{print NODE",FORWARD,"$0}' | \
    awk '{print $1","$2","$3","$4","$5}' >> "$IPTABLES_CSV" || true
done
success "iptables stats lưu tại: $IPTABLES_CSV"

# =============================================================================
# 6. BẢNG CONNTRACK (NAT Translation Table)
# =============================================================================
info "Xuất bảng NAT conntrack..."

CONNTRACK_CSV="$RESULTS_DIR/nat_conntrack_${TS}.csv"
echo "proto,src_local,src_global,dst_local,dst_global,state" > "$CONNTRACK_CSV"

CONNTRACK_RAW=$(sudo m r_out conntrack -L 2>/dev/null || echo "")
if [ -n "$CONNTRACK_RAW" ]; then
    echo "$CONNTRACK_RAW" | grep -E "tcp|udp" | while read -r line; do
        PROTO=$(echo "$line" | awk '{print $1}')
        SRC=$(echo   "$line" | grep -oP 'src=\K\S+' | head -1)
        DST=$(echo   "$line" | grep -oP 'dst=\K\S+' | head -1)
        SPORT=$(echo "$line" | grep -oP 'sport=\K\d+' | head -1)
        DPORT=$(echo "$line" | grep -oP 'dport=\K\d+' | head -1)
        STATE=$(echo "$line" | grep -oP '\bESTABLISHED\b|\bSYN_SENT\b|\bTIME_WAIT\b' || echo "OTHER")
        echo "${PROTO},${SRC}:${SPORT},-,${DST}:${DPORT},-,${STATE}" >> "$CONNTRACK_CSV"
    done
    success "conntrack lưu tại: $CONNTRACK_CSV"
else
    echo "  (conntrack chưa cài hoặc không có kết nối nào. apt install conntrack)"
fi

# =============================================================================
# 7. TÓM TẮT KẾT QUẢ
# =============================================================================
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ✅ Thu thập số liệu hoàn tất!${RESET}"
echo    ""
echo    "  Files đã tạo trong ${RESULTS_DIR}/:"
ls -lh "$RESULTS_DIR/"* 2>/dev/null | awk '{print "    "$NF" ("$5")"}'
echo    ""
echo    "  Bước tiếp theo:"
echo    "  1. python3 plot_charts.py --demo   # Vẽ biểu đồ"
echo    "  2. Điền số liệu từ CSV vào báo cáo Chương 5"
echo -e "${GREEN}════════════════════════════════════════════════════════${RESET}"
echo ""
