# BÀI TẬP 3 – Tối ưu hóa bảo mật đa lớp và cân bằng tải
## Campus Network 3 Lớp: Core – Distribution – Access + DMZ
### Phiên bản v2 – Đạt đủ tiêu chí Mức 2 → Mức 4 (Xuất sắc)

---

## 📁 Cấu trúc File

```
52300221_NguyenTanManh_CuoiKy/
│
├── topology.py           ← ⭐ Khởi động topology Mininet (HA + ECMP v2)
├── acl.sh                ← ACL đa lớp + attack simulation + placement logic
├── dropacl.sh            ← Bãi bỏ ACL
├── setup_ospf.sh         ← FRR/OSPF trên các router
├── nat_config.sh         ← PAT + Static NAT
│
├── load_balancer.py      ← Cân bằng tải: EWMA + hold-down + LR proactive
├── ml_security.py        ← [MỨC 4.2] Anomaly Detection + Dynamic ACL
├── ospf_convergence.py   ← [MỨC 3.1] Đo OSPF convergence time
├── dashboard.py          ← [MỨC 4.4] Flask Web Dashboard realtime
├── plot_charts.py        ← Vẽ 10 biểu đồ báo cáo
├── collect_data.sh       ← Thu thập số liệu
│
├── frr_core.conf         ← OSPF config Core
├── frr_dist1.conf        ← OSPF config Distribution 1
├── frr_dist2.conf        ← OSPF config Distribution 2
├── frr_dmz.conf          ← OSPF config DMZ router
├── frr_rout.conf         ← OSPF config ISP/Outside
│
└── charts/               ← Biểu đồ PNG xuất ra
```

---

## 🗺 Topology & IP Planning

### Sơ đồ phân lớp (3-Layer + DMZ + HA)

```
OUTSIDE (ISP)
  r_out (192.168.100.2) ── h_out (203.0.113.100)
        │ 192.168.100.0/30
  ┌─────┴──────────────────────────────────────────┐
  │ CORE LAYER  (core – 192.168.1.x)               │
  │  ├── core-eth1 [Primary]  → dist1 (1Gbps,1ms)  │
  │  ├── core-eth5 [ECMP/HA]  → dist1 (1Gbps,1ms)  │
  │  ├── core-eth2 [Primary]  → dist2 (1Gbps,1ms)  │
  │  ├── core-eth6 [ECMP/HA]  → dist2 (1Gbps,1ms)  │
  │  ├── core-eth3 → dmz_r   (1Gbps,1ms)           │
  │  └── core-eth4 → r_out   (500Mbps,10ms)        │
  └──────────────────────────────────────────────────┘
       │                   │
  ┌────┴────┐         ┌────┴────┐     ┌─────────────┐
  │ dist1   │         │ dist2   │     │  DMZ Zone   │
  │VLAN10   │         │VLAN20   │     │  dmz_r      │
  │172.16.10│         │172.16.20│     │  10.10.10.x │
  └────┬────┘         └────┬────┘     │  web1/web2  │
  ┌────┴────┐    ╲ ╱  ┌────┴────┐    └─────────────┘
  │  acc1   │     X   │  acc2   │  ← Cross-links HA
  └────┬────┘    ╱ ╲  └────┬────┘
  h1,printer1         h2,phone1
```

> **HA Cross-links**: acc1↔dist2 và acc2↔dist1 → OSPF tự tìm đường khi link chính down

### Bảng địa chỉ IP

| Lớp | Node | Interface | IP |
|-----|------|-----------|-----|
| Core | `core` | core-eth1 | 192.168.1.1/30 |
| Core | `core` | core-eth2 | 192.168.1.5/30 |
| Core | `core` | core-eth5 (ECMP) | 192.168.2.1/30 |
| Core | `core` | core-eth6 (ECMP) | 192.168.2.5/30 |
| Core | `core` | core-eth3 | 192.168.1.9/30 |
| Core | `core` | core-eth4 | 192.168.100.1/30 |
| Distribution | `dist1` | dist1-eth1 | 192.168.1.2/30 |
| Distribution | `dist1` | dist1-eth2 (GW VLAN10) | 172.16.10.1/24 |
| Distribution | `dist2` | dist2-eth1 | 192.168.1.6/30 |
| Distribution | `dist2` | dist2-eth2 (GW VLAN20) | 172.16.20.1/24 |
| DMZ Router | `dmz_r` | dmz-eth1 | 192.168.1.10/30 |
| DMZ Router | `dmz_r` | dmz-eth2 (GW DMZ) | 10.10.10.1/24 |
| ISP/Outside | `r_out` | rout-eth1 | 192.168.100.2/30 |
| ISP/Outside | `r_out` | rout-eth2 (GW Outside) | 203.0.113.1/24 |
| **Host** | `h1` | — | 172.16.10.10/24 |
| **Host** | `printer1` | — | 172.16.10.30/24 |
| **Host** | `h2` | — | 172.16.20.10/24 |
| **Host** | `phone1` | — | 172.16.20.20/24 |
| **DMZ Server** | `web1` | — | 10.10.10.11/24 |
| **DMZ Server** | `web2` | — | 10.10.10.12/24 |
| **Outside** | `h_out` | — | 203.0.113.100/24 |

### Bảng Static NAT (1-1 mapping)

| IP Public (Outside) | IP Private (DMZ) | Dịch vụ |
|--------------------|-----------------|---------| 
| 203.0.113.10 | 10.10.10.11 (web1) | HTTP (80) + HTTPS (443) |
| 203.0.113.11 | 10.10.10.12 (web2) | HTTP (80) + HTTPS (443) |

---

## 🚀 Hướng dẫn chạy

### Bước 0: Cài đặt môi trường

```bash
sudo apt update
sudo apt install -y mininet python3-pip frr frr-pythontools conntrack iperf3

# Bật OSPF daemon
sudo sed -i 's/ospfd=no/ospfd=yes/; s/zebra=no/zebra=yes/' /etc/frr/daemons
sudo systemctl restart frr

# Python dependencies
pip3 install matplotlib seaborn pandas numpy scikit-learn flask

chmod +x *.sh
```

---

### Bước 1: Khởi động Topology

```bash
# Terminal 1 – giữ cửa sổ này mở suốt
sudo python3 topology.py

# Kết quả mong đợi:
# *** [CONFIG] Gán IP gateway...
# *** [NAT]   Áp dụng PAT + Static NAT...
# *** [WEB]   Khởi động HTTP server...
# *** [TEST]  h1 → web1: ✓ OK
# mininet>
```

Các flag tuỳ chọn:
```bash
sudo python3 topology.py --acl        # Áp dụng ACL ngay khi khởi động
sudo python3 topology.py --lb         # Khởi động load balancer
sudo python3 topology.py --lb --demo  # LB chế độ demo (random load)
```

---

### Bước 2: Triển khai OSPF

```bash
# Terminal 2
sudo bash setup_ospf.sh

# Kiểm tra hội tụ:
sudo m core vtysh -c "show ip ospf neighbor"
# Kết quả: dist1, dist2, dmz_r, r_out ở trạng thái FULL

sudo m core vtysh -c "show ip route ospf"
# Thấy ký hiệu O (OSPF) trên các subnet

# Kiểm tra ECMP (đa đường):
py show_ecmp_routes(net)
# Thấy 2 đường đến dist1 qua core-eth1 và core-eth5
```

---

### Bước 3: Áp dụng ACL + Firewall

```bash
# Cách 1: Từ Mininet CLI (khuyến nghị)
# mininet> py apply_acl(net)

# Cách 2: Script trực tiếp
sudo bash acl.sh

# Kiểm tra ACL:
sudo m dmz_r iptables -L FORWARD -n --line-numbers
sudo m r_out iptables -L FORWARD -n --line-numbers

# Test:
# h1 curl http://10.10.10.11   → OK (port 80 được phép)
# h1 curl http://10.10.10.11:22 → BLOCKED (SSH bị DROP)
# h_out curl http://203.0.113.10 → OK (Static NAT)

# Xem log bị chặn:
sudo dmesg | grep -E "EXT_ACL|FW_BORDER|STD_ACL|FW_SCAN" | tail -20

# Kịch bản tấn công giả lập (sinh dữ liệu cho heatmap):
sudo bash acl.sh simulate

# Bãi bỏ ACL:
sudo bash dropacl.sh
# hoặc: py drop_acl(net)
```

**Logic đặt ACL (Mức 3.3):**
| Loại ACL | Đặt tại | Lý do |
|----------|---------|-------|
| Standard ACL | Distribution (gần đích) | Chỉ lọc IP nguồn → đặt gần đích tránh block nhầm traffic hợp lệ đến đích khác |
| Extended ACL | r_out + dmz_r (gần nguồn) | Lọc IP+Port+Protocol → loại bỏ gói rác ngay tại biên, tiết kiệm bandwidth backbone |

---

### Bước 4: Kiểm tra NAT

```bash
# PAT (Inside → Outside):
# mininet> h1 curl http://203.0.113.100

# Static NAT (Outside → DMZ):
# mininet> h_out curl http://203.0.113.10  → "Server web1 OK"
# mininet> h_out curl http://203.0.113.11  → "Server web2 OK"

# Xem bảng NAT đầy đủ:
# mininet> py show_nat_table(net)
sudo m r_out iptables -t nat -L -n -v
sudo m r_out conntrack -L
```

**Phân tích overhead NAT (Mức 3.2):**

Router biên (`r_out`) phải duy trì **bảng trạng thái PAT** (conntrack table) trong RAM:
- Mỗi phiên kết nối = 1 entry: `[src_ip:src_port → public_ip:pub_port → dst_ip:dst_port]`
- **Truy vết nguồn khi sự cố**: Kết hợp `conntrack -L` + `dmesg | grep NAT_EVENT` → tìm `Inside Local IP:Port` tương ứng với `Inside Global Port` → xác định máy nội bộ tấn công

```bash
# Ví dụ truy vết: Public port 10241 → máy nào?
sudo m r_out conntrack -L | grep 10241
# → src=172.16.10.10 sport=1024 dst=203.0.113.100 dport=80
```

---

### Bước 5: Cân bằng tải

```bash
# Chế độ Demo (không cần traffic thực):
python3 load_balancer.py --demo --algorithm ewma

# Chế độ thực tế (khi Mininet đang chạy):
sudo python3 load_balancer.py \
  --pid $(sudo m dmz_r echo '$$' | head -1) \
  --r_out_pid $(sudo m r_out echo '$$' | head -1) \
  --iface dmz-eth2 \
  --algorithm ewma

# Các thuật toán hỗ trợ (--algorithm):
#   threshold  → Ngưỡng tĩnh 80%/20% (Mức 2 cơ bản)
#   ewma       → EWMA α=0.3 chống flapping (Mức 3.4) [mặc định]
#   proactive  → Linear Regression dự đoán trước (Mức 4.1)
#   least_conn → Least Connections (Mức 4.1)

# Sinh traffic để trigger switching:
# mininet> h_out iperf3 -c 203.0.113.10 -t 60 -b 95M &
```

**Giải thích chống Flapping (Mức 3.4):**
- **EWMA**: Làm mượt nhiễu load, tránh ngưỡng dao động 79%↔81% trigger switch liên tục
- **Hold-down timer**: Sau mỗi lần switch, khóa 30 giây không switch ngược lại

---

### Bước 6: HA – Kiểm tra OSPF Convergence (Mức 3.1)

```bash
# Đo thời gian hội tụ khi link down:
python3 ospf_convergence.py --demo   # Chế độ demo
sudo python3 ospf_convergence.py     # Thực tế (Mininet đang chạy)

# Từ Mininet CLI:
# mininet> py demo_link_failure(net)              # Test link core↔dist1
# mininet> py demo_link_failure(net,'core','dist2') # Test link core↔dist2

# Kết quả điển hình với FRR OSPF:
# Convergence Time: ~2500ms (dead-interval=40s mặc định)
# → Dùng BFD để giảm xuống <500ms

# Biểu đồ convergence:
# → chart_ospf_convergence.png
```

---

### Bước 7: ML Security – Phát hiện tấn công (Mức 4.2)

```bash
# Demo (không cần Mininet):
python3 ml_security.py --demo

# Live với dmesg thực:
sudo python3 ml_security.py --live

# Live + tự động block IP tấn công:
sudo python3 ml_security.py --live --inject \
  --pid $(sudo m r_out echo '$$' | head -1)

# Kết quả:
# 🔴 ATTACK  203.0.113.100 – Isolation Forest score=-0.312
# 🚫 AUTO-BLOCK: 203.0.113.100 → DROP rule injected
```

**Thuật toán**: Isolation Forest (scikit-learn)
- Thu thập features: `[drop_count, rate_of_change, burst_factor, is_external]`
- `contamination=0.1` → giả định 10% traffic là bất thường
- Fallback rule-based nếu chưa cài scikit-learn

---

### Bước 8: Web Dashboard Realtime (Mức 4.4)

```bash
# Cài Flask (một lần):
pip install flask

# Khởi động dashboard:
python3 dashboard.py

# Mở trình duyệt:
# → http://localhost:5000
```

**Dashboard hiển thị (cập nhật mỗi 2 giây):**
- 📈 Line chart throughput Web1/Web2 theo thời gian thực
- ⚡ Gauge chart % tải Web1 (EWMA)
- 🛡 Danh sách IP bị ML auto-block
- 🔄 Log sự kiện chuyển server
- Cảnh báo đỏ khi tải vượt 80%

---

### Bước 9: Thu thập số liệu & Vẽ biểu đồ

```bash
# Thu thập số liệu (khi Mininet đang chạy):
sudo bash collect_data.sh

# Vẽ 10 biểu đồ:
python3 plot_charts.py --demo --out ./charts/

# Danh sách biểu đồ:
# chart0  – Sơ đồ logic topology
# chart1  – Load Web1/Web2 theo thời gian
# chart2  – Heatmap ACL theo loại port
# chart3  – Throughput bar chart
# chart4  – Latency RTT comparison
# chart5  – Stacked area tổng tải
# chart6  – Bảng NAT translation
# chart7  – [MỨC 3.3] Heatmap tấn công IP×Khung giờ ← ĐẸP NHẤT
# chart8  – [MỨC 3.5] Cross-validation NAT+ACL overhead
# chart9  – [MỨC 3.5] Data alignment – Server2 tăng đúng lúc
```

---

## 🔍 Lệnh Debug thường dùng

```bash
# ── Routing ──────────────────────────────────────────────
sudo m core  ip route
sudo m dist1 ip route
sudo m h1    ip route

# ── OSPF ─────────────────────────────────────────────────
sudo m core vtysh -c "show ip ospf neighbor"
sudo m core vtysh -c "show ip ospf database"
sudo m core vtysh -c "show ip route ospf"

# ── NAT ──────────────────────────────────────────────────
sudo m r_out iptables -t nat -L -n -v
sudo m r_out conntrack -L
sudo dmesg | grep NAT_

# ── ACL/Firewall ─────────────────────────────────────────
sudo m dmz_r iptables -L FORWARD -n -v
sudo m dist1 iptables -L FORWARD -n -v
sudo dmesg | grep -E "EXT_ACL|FW_BORDER|FW_SCAN|STD_ACL"

# ── Connectivity ─────────────────────────────────────────
sudo m h1    ping -c 3 10.10.10.11
sudo m h_out wget -qO- http://203.0.113.10
sudo m h1    iperf3 -c 10.10.10.11 -t 10

# ── ECMP multi-path ──────────────────────────────────────
sudo m core ip route show    # Thấy 2 đường đến dist1

# ── Dashboard ────────────────────────────────────────────
# http://localhost:5000/api/data   ← Raw JSON data
```

---

## 📊 Tóm tắt theo tiêu chí đánh giá

| Mức | Tiêu chí | File/Lệnh | Trạng thái |
|-----|----------|-----------|------------|
| 2.1 | Topology 3 lớp + IP planning | `topology.py` | ✅ |
| 2.2 | OSPF + NAT/PAT | `setup_ospf.sh`, `nat_config.sh` | ✅ |
| 2.3 | ACL đa lớp Standard+Extended | `acl.sh` | ✅ |
| 2.4 | Load balancer theo ngưỡng | `load_balancer.py` | ✅ |
| 2.5 | Biểu đồ Matplotlib | `plot_charts.py` | ✅ |
| 3.1 | HA Redundant links + OSPF convergence | `topology.py` + `ospf_convergence.py` | ✅ |
| 3.2 | Overhead NAT + truy vết Syslog | README Bước 4 + `show_nat_table()` | ✅ |
| 3.3 | ACL placement logic + Heatmap IP×time | `acl.sh` + chart7 | ✅ |
| 3.4 | EWMA + hold-down timer chống flapping | `load_balancer.py --algorithm ewma` | ✅ |
| 3.5 | Cross-validation + Data alignment | chart8 + chart9 | ✅ |
| 4.1 | Proactive LB (Linear Regression) | `load_balancer.py --algorithm proactive` | ✅ |
| 4.2 | Anomaly Detection + Dynamic ACL | `ml_security.py` | ✅ |
| 4.3 | ECMP multi-path (2 đường Core↔Dist) | `topology.py` core-eth5/eth6 | ✅ |
| 4.4 | Web Dashboard realtime | `dashboard.py` → localhost:5000 | ✅ |

---

## 📋 Bảng Quản lý Sự cố

| Lỗi | Triệu chứng | Cách khắc phục |
|-----|-------------|----------------|
| NAT không hoạt động | `h_out curl 203.0.113.10` timeout | `sudo m r_out iptables -t nat -L PREROUTING -n` |
| ACL chặn nhầm | `h1 curl web1 port 80` bị block | `sudo m dmz_r iptables -L FORWARD -n` kiểm tra rule |
| OSPF không hội tụ | `show ospf neighbor` rỗng | Kiểm tra FRR log: `/tmp/frr-*.log` |
| Load balancer không switch | Tải >80% nhưng không chuyển | Đợi hold-down timer (30s); dùng `--algorithm threshold` để test |
| Dashboard không load | `localhost:5000` lỗi | `pip install flask`; kiểm tra port không bị chiếm |
| ML security false positive | IP hợp lệ bị block | Thêm IP vào `WHITELIST_IPS` trong `ml_security.py` |
| ECMP không hoạt động | Chỉ thấy 1 đường trong `ip route` | Bật `multipath` trong FRR: `ip multipath` |

---

## 🏗 Kiến trúc SDN (Mức 4.3 – Hướng phát triển)

Hệ thống hiện dùng **OSPF truyền thống + ECMP**. Để tích hợp SDN Ryu Controller:

```bash
# Cài Ryu:
pip install ryu

# Thay controller=None bằng RemoteController trong topology.py:
# net = Mininet(topo=topo, link=TCLink, controller=RemoteController)

# Chạy Ryu controller:
# ryu-manager ryu.app.simple_switch_13 --ofp-tcp-listen-port 6633
```

Lợi ích SDN so với OSPF truyền thống:
- Điều phối luồng tập trung thay vì phân tán
- Cập nhật routing rule không cần reconvergence
- Kết hợp với ML: controller tự inject flow rule khi phát hiện tấn công

---

*Bài tập 3 – Môn Mạng Máy Tính Nâng Cao | 52300221 – Nguyễn Tấn Mạnh*
