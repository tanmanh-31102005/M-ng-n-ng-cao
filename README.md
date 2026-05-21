# 🌐 Campus Network – Tối ưu hóa Bảo mật Đa lớp & Cân bằng Tải

> **Môn học:** Mạng Máy Tính Nâng Cao  
> **Sinh viên:** Nguyễn Tấn Mạnh – MSSV: 52300221  
> **Bài tập:** 3 – Thiết kế và triển khai mạng Campus 3 lớp với HA, OSPF, ACL, NAT, Load Balancing và ML Security

---

## 📖 Mô tả dự án

Dự án mô phỏng một **hệ thống mạng doanh nghiệp vừa** (campus network) gồm 3 lớp phân cấp **Core – Distribution – Access** kết hợp vùng **DMZ** (Demilitarized Zone) và kết nối **Internet/ISP**. Toàn bộ hạ tầng được ảo hoá bằng **Mininet** chạy trên Linux, định tuyến động bằng **FRR/OSPF**, bảo mật bằng ACL đa lớp + iptables, và trang bị khả năng phát hiện tấn công bằng **Machine Learning (Isolation Forest)**.

### ✨ Tính năng nổi bật

| Tính năng | Mô tả |
|-----------|-------|
| **3-Tier Topology** | Core → Distribution → Access với VLAN 10 và VLAN 20 |
| **High Availability** | Redundant links + ECMP 2 đường Core↔Distribution |
| **OSPF Dynamic Routing** | FRR daemon, hội tụ tự động khi link down |
| **NAT/PAT** | PAT cho inside→internet; Static NAT 1-1 cho DMZ |
| **ACL Multi-layer** | Standard ACL + Extended ACL đặt đúng vị trí tối ưu |
| **Load Balancer** | EWMA, Proactive (Linear Regression), Least Connections |
| **ML Security** | Isolation Forest phát hiện anomaly + tự động block IP |
| **Web Dashboard** | Flask realtime dashboard cập nhật mỗi 2 giây |
| **Data Visualization** | 10 biểu đồ báo cáo (Matplotlib/Seaborn) |

---

## 🗺️ Kiến trúc / Topology

### Sơ đồ phân lớp (3-Tier + DMZ + HA)

```
                    ┌──────────────────────────┐
                    │    INTERNET / OUTSIDE     │
                    │  h_out (203.0.113.100)    │
                    └────────────┬─────────────┘
                                 │ 203.0.113.0/24
                    ┌────────────┴─────────────┐
                    │    r_out  (ISP/Border)    │
                    │   192.168.100.2           │
                    └────────────┬─────────────┘
                                 │ 192.168.100.0/30
          ┌──────────────────────┴──────────────────────┐
          │              CORE LAYER                      │
          │              core (192.168.1.x)              │
          │  eth1/eth5 ──→ dist1   eth2/eth6 ──→ dist2  │
          │  eth3 ──→ dmz_r        eth4 ──→ r_out        │
          └──────┬──────────────────────┬───────────────┘
                 │  ECMP (2 paths)      │  ECMP (2 paths)
       ┌─────────┴──────┐       ┌───────┴────────┐    ┌─────────────────┐
       │   dist1        │       │    dist2        │    │   DMZ Zone      │
       │ VLAN 10        │  ╲ ╱  │  VLAN 20        │    │   dmz_r         │
       │ 172.16.10.1/24 │   X   │  172.16.20.1/24 │    │  10.10.10.1/24  │
       └──────┬─────────┘  ╱ ╲  └─────────┬───────┘    │  web1/web2      │
              │   Cross-links HA            │            └─────────────────┘
       ┌──────┴─────────┐       ┌─────────┴───────┐
       │     acc1       │       │      acc2        │
       │ (Access L2)    │       │  (Access L2)     │
       └──────┬─────────┘       └─────────┬───────┘
         h1, printer1               h2, phone1
      (172.16.10.x/24)          (172.16.20.x/24)
```

> **HA Cross-links**: acc1 ↔ dist2 và acc2 ↔ dist1 — OSPF tự động tìm đường thay thế khi link chính bị down.

### Bảng địa chỉ IP

| Lớp | Node | Interface | Địa chỉ IP |
|-----|------|-----------|------------|
| Core | `core` | core-eth1 | 192.168.1.1/30 |
| Core | `core` | core-eth2 | 192.168.1.5/30 |
| Core | `core` | core-eth5 *(ECMP)* | 192.168.2.1/30 |
| Core | `core` | core-eth6 *(ECMP)* | 192.168.2.5/30 |
| Core | `core` | core-eth3 | 192.168.1.9/30 |
| Core | `core` | core-eth4 | 192.168.100.1/30 |
| Distribution | `dist1` | dist1-eth1 | 192.168.1.2/30 |
| Distribution | `dist1` | dist1-eth2 *(GW VLAN10)* | 172.16.10.1/24 |
| Distribution | `dist2` | dist2-eth1 | 192.168.1.6/30 |
| Distribution | `dist2` | dist2-eth2 *(GW VLAN20)* | 172.16.20.1/24 |
| DMZ Router | `dmz_r` | dmz-eth1 | 192.168.1.10/30 |
| DMZ Router | `dmz_r` | dmz-eth2 *(GW DMZ)* | 10.10.10.1/24 |
| ISP/Outside | `r_out` | rout-eth1 | 192.168.100.2/30 |
| ISP/Outside | `r_out` | rout-eth2 *(GW Outside)* | 203.0.113.1/24 |
| Host | `h1` | — | 172.16.10.10/24 |
| Host | `printer1` | — | 172.16.10.30/24 |
| Host | `h2` | — | 172.16.20.10/24 |
| Host | `phone1` | — | 172.16.20.20/24 |
| DMZ Server | `web1` | — | 10.10.10.11/24 |
| DMZ Server | `web2` | — | 10.10.10.12/24 |
| Outside | `h_out` | — | 203.0.113.100/24 |

### Bảng Static NAT (1-1 Mapping)

| IP Public (Outside) | IP Private (DMZ) | Dịch vụ |
|--------------------|-----------------|---------|
| 203.0.113.10 | 10.10.10.11 (web1) | HTTP (80) + HTTPS (443) |
| 203.0.113.11 | 10.10.10.12 (web2) | HTTP (80) + HTTPS (443) |

---

## 🛠️ Công nghệ sử dụng

| Thành phần | Công nghệ / Phiên bản |
|------------|----------------------|
| **Ảo hoá mạng** | [Mininet](http://mininet.org/) 2.3.x |
| **Hệ điều hành** | Ubuntu 20.04 / 22.04 LTS |
| **Định tuyến động** | [FRR](https://frrouting.org/) (Free Range Routing) – OSPF daemon |
| **Firewall / ACL** | `iptables` (Standard + Extended rules) |
| **NAT** | `iptables -t nat` (PAT + Static NAT) + `conntrack` |
| **Ngôn ngữ lập trình** | Python 3.8+ |
| **Load Balancing** | Custom Python – EWMA, Linear Regression, Least Connections |
| **Machine Learning** | scikit-learn – Isolation Forest (Anomaly Detection) |
| **Web Dashboard** | Flask + Chart.js |
| **Trực quan hoá** | Matplotlib 3.x, Seaborn |
| **Đo lường** | iperf3, ping, conntrack |

---

## 📁 Cấu trúc thư mục

```
52300221_NguyenTanManh_CuoiKy/
│
├── topology.py            ⭐ Khởi động topology Mininet (HA + ECMP v2)
├── load_balancer.py          Cân bằng tải: EWMA / Proactive / Least-Conn
├── ml_security.py            [Mức 4.2] Anomaly Detection + Dynamic ACL
├── ospf_convergence.py       [Mức 3.1] Đo OSPF convergence time
├── dashboard.py              [Mức 4.4] Flask Web Dashboard realtime
├── plot_charts.py            Vẽ 10 biểu đồ báo cáo
│
├── acl.sh                    ACL đa lớp + attack simulation
├── dropacl.sh                Bãi bỏ toàn bộ ACL rules
├── setup_ospf.sh             Cấu hình FRR/OSPF trên tất cả router
├── nat_config.sh             PAT + Static NAT
├── collect_data.sh           Thu thập số liệu tải mạng
├── run_all.sh                Chạy tất cả các bước tự động
├── install_m.sh              Cài đặt nhanh các gói phụ thuộc
├── 00_setup_environment.sh   Cài đặt môi trường đầy đủ
│
├── frr_core.conf             OSPF config – Core router
├── frr_dist1.conf            OSPF config – Distribution 1
├── frr_dist2.conf            OSPF config – Distribution 2
├── frr_dmz.conf              OSPF config – DMZ router
├── frr_rout.conf             OSPF config – ISP/Outside router
│
├── 01_topology_design.md     Tài liệu thiết kế hệ thống
├── load_log.csv              Log dữ liệu cân bằng tải mẫu
└── charts/                   Biểu đồ PNG xuất ra (10 charts)
```

---

## ⚙️ Cài đặt môi trường

### Yêu cầu hệ thống

- **OS:** Ubuntu 20.04 hoặc 22.04 LTS (khuyến nghị dùng VMware Workstation hoặc VirtualBox)
- **RAM:** Tối thiểu 2 GB (khuyến nghị 4 GB)
- **Python:** 3.8+

### Cài đặt tự động

```bash
# Cấp quyền thực thi và chạy script cài đặt
chmod +x 00_setup_environment.sh
sudo bash 00_setup_environment.sh
```

### Cài đặt thủ công

```bash
# 1. Cài các gói hệ thống
sudo apt update
sudo apt install -y mininet python3-pip frr frr-pythontools \
                   conntrack iperf3 net-tools

# 2. Bật OSPF và Zebra daemon trong FRR
sudo sed -i 's/ospfd=no/ospfd=yes/; s/zebra=no/zebra=yes/' /etc/frr/daemons
sudo systemctl restart frr

# 3. Cài Python dependencies
pip3 install matplotlib seaborn pandas numpy scikit-learn flask

# 4. Cấp quyền thực thi cho tất cả script
chmod +x *.sh
```

---

## 🚀 Hướng dẫn chạy

### Bước 1 – Khởi động Topology

```bash
# Terminal 1 – giữ cửa sổ này mở suốt quá trình thực hành
sudo python3 topology.py

# Kết quả mong đợi:
# *** [CONFIG] Gán IP gateway...
# *** [NAT]   Áp dụng PAT + Static NAT...
# *** [WEB]   Khởi động HTTP server...
# *** [TEST]  h1 → web1: ✓ OK
# mininet>

# Các flag tuỳ chọn:
sudo python3 topology.py --acl          # Áp dụng ACL ngay khi khởi động
sudo python3 topology.py --lb           # Khởi động load balancer
sudo python3 topology.py --lb --demo    # LB chế độ demo (random load)
```

### Bước 2 – Triển khai OSPF

```bash
# Terminal 2
sudo bash setup_ospf.sh

# Kiểm tra trạng thái hội tụ:
sudo m core vtysh -c "show ip ospf neighbor"
# → dist1, dist2, dmz_r, r_out đều ở trạng thái FULL

sudo m core vtysh -c "show ip route ospf"
# → Thấy ký hiệu O (OSPF) trên các subnet

# Kiểm tra ECMP (đa đường):
# mininet> py show_ecmp_routes(net)
# → Thấy 2 đường đến dist1 qua core-eth1 và core-eth5
```

### Bước 3 – Áp dụng ACL / Firewall

```bash
# Từ Mininet CLI (khuyến nghị):
# mininet> py apply_acl(net)

# Hoặc script trực tiếp:
sudo bash acl.sh

# Kiểm tra rules:
sudo m dmz_r iptables -L FORWARD -n --line-numbers
sudo m r_out  iptables -L FORWARD -n --line-numbers

# Kiểm tra connectivity:
# h1 curl http://10.10.10.11      → ✅ OK (port 80 được phép)
# h1 curl http://10.10.10.11:22   → ❌ BLOCKED (SSH bị DROP)
# h_out curl http://203.0.113.10  → ✅ OK (Static NAT)

# Xem log bị chặn:
sudo dmesg | grep -E "EXT_ACL|FW_BORDER|STD_ACL|FW_SCAN" | tail -20

# Giả lập tấn công (sinh dữ liệu heatmap):
sudo bash acl.sh simulate

# Bãi bỏ ACL:
sudo bash dropacl.sh
```

> **Logic đặt ACL tối ưu:**
> | Loại ACL | Vị trí đặt | Lý do |
> |----------|------------|-------|
> | Standard ACL | Distribution (gần đích) | Chỉ lọc IP nguồn → gần đích tránh chặn nhầm traffic hợp lệ |
> | Extended ACL | r_out + dmz_r (gần nguồn) | Lọc IP+Port+Protocol → loại gói rác ngay tại biên, tiết kiệm bandwidth |

### Bước 4 – Kiểm tra NAT

```bash
# PAT (Inside → Outside):
# mininet> h1 curl http://203.0.113.100

# Static NAT (Outside → DMZ):
# mininet> h_out curl http://203.0.113.10  → "Server web1 OK"
# mininet> h_out curl http://203.0.113.11  → "Server web2 OK"

# Xem bảng NAT:
# mininet> py show_nat_table(net)
sudo m r_out iptables -t nat -L -n -v
sudo m r_out conntrack -L

# Truy vết IP nguồn (khi sự cố):
sudo m r_out conntrack -L | grep 10241
# → src=172.16.10.10 sport=1024 dst=203.0.113.100 dport=80
```

### Bước 5 – Cân bằng tải

```bash
# Chế độ Demo (không cần traffic thực):
python3 load_balancer.py --demo --algorithm ewma

# Chế độ thực tế (Mininet đang chạy):
sudo python3 load_balancer.py \
  --pid $(sudo m dmz_r echo '$$' | head -1) \
  --r_out_pid $(sudo m r_out echo '$$' | head -1) \
  --iface dmz-eth2 \
  --algorithm ewma

# Các thuật toán hỗ trợ:
#   threshold  → Ngưỡng tĩnh 80%/20% (cơ bản)
#   ewma       → EWMA α=0.3 chống flapping ✅ (mặc định)
#   proactive  → Linear Regression dự đoán trước (Mức 4.1)
#   least_conn → Least Connections (Mức 4.1)

# Sinh traffic để trigger switching:
# mininet> h_out iperf3 -c 203.0.113.10 -t 60 -b 95M &
```

### Bước 6 – HA / OSPF Convergence

```bash
# Đo thời gian hội tụ khi link down:
python3 ospf_convergence.py --demo   # Chế độ demo
sudo python3 ospf_convergence.py     # Thực tế

# Từ Mininet CLI:
# mininet> py demo_link_failure(net)               # Test core↔dist1
# mininet> py demo_link_failure(net,'core','dist2') # Test core↔dist2

# Kết quả điển hình với FRR OSPF:
# Convergence Time: ~2500ms (dead-interval=40s mặc định)
# Dùng BFD để giảm xuống <500ms
```

### Bước 7 – ML Security (Phát hiện tấn công)

```bash
# Demo (không cần Mininet):
python3 ml_security.py --demo

# Live với dmesg thực:
sudo python3 ml_security.py --live

# Live + tự động block IP tấn công:
sudo python3 ml_security.py --live --inject \
  --pid $(sudo m r_out echo '$$' | head -1)

# Output mẫu:
# 🔴 ATTACK  203.0.113.100 – Isolation Forest score=-0.312
# 🚫 AUTO-BLOCK: 203.0.113.100 → DROP rule injected
```

> **Thuật toán:** Isolation Forest (scikit-learn)  
> Features: `[drop_count, rate_of_change, burst_factor, is_external]`  
> `contamination=0.1` → giả định 10% traffic là bất thường  

### Bước 8 – Web Dashboard Realtime

```bash
# Cài Flask (một lần):
pip install flask

# Khởi động dashboard:
python3 dashboard.py

# Mở trình duyệt tại: http://localhost:5000
```

Dashboard cập nhật **mỗi 2 giây** và hiển thị:
- 📈 Throughput Web1/Web2 theo thời gian thực
- ⚡ Gauge chart % tải Web1 (EWMA)
- 🛡 Danh sách IP bị ML auto-block
- 🔄 Log sự kiện chuyển server
- 🔴 Cảnh báo khi tải vượt 80%

### Bước 9 – Thu thập số liệu & Vẽ biểu đồ

```bash
# Thu thập số liệu (khi Mininet đang chạy):
sudo bash collect_data.sh

# Vẽ 10 biểu đồ báo cáo:
python3 plot_charts.py --demo --out ./charts/

# Danh sách biểu đồ xuất ra:
# chart0  – Sơ đồ logic topology
# chart1  – Load Web1/Web2 theo thời gian
# chart2  – Heatmap ACL theo loại port
# chart3  – Throughput bar chart
# chart4  – Latency RTT comparison
# chart5  – Stacked area tổng tải
# chart6  – Bảng NAT translation
# chart7  – Heatmap tấn công IP × Khung giờ
# chart8  – Cross-validation NAT+ACL overhead
# chart9  – Data alignment – Server2 tăng đúng lúc
```

---

## 🔍 Lệnh Debug thường dùng

```bash
# ── Routing ─────────────────────────────────────────────────
sudo m core  ip route
sudo m dist1 ip route
sudo m h1    ip route

# ── OSPF ────────────────────────────────────────────────────
sudo m core vtysh -c "show ip ospf neighbor"
sudo m core vtysh -c "show ip ospf database"
sudo m core vtysh -c "show ip route ospf"

# ── NAT ─────────────────────────────────────────────────────
sudo m r_out iptables -t nat -L -n -v
sudo m r_out conntrack -L
sudo dmesg | grep NAT_

# ── ACL / Firewall ──────────────────────────────────────────
sudo m dmz_r iptables -L FORWARD -n -v
sudo m dist1 iptables -L FORWARD -n -v
sudo dmesg | grep -E "EXT_ACL|FW_BORDER|FW_SCAN|STD_ACL"

# ── Connectivity ────────────────────────────────────────────
sudo m h1    ping -c 3 10.10.10.11
sudo m h_out wget -qO- http://203.0.113.10
sudo m h1    iperf3 -c 10.10.10.11 -t 10

# ── ECMP multi-path ─────────────────────────────────────────
sudo m core ip route show     # Thấy 2 đường đến dist1

# ── Dashboard API ────────────────────────────────────────────
# http://localhost:5000/api/data   ← Raw JSON
```

---

## 🐛 Xử lý sự cố thường gặp

| Lỗi | Triệu chứng | Cách khắc phục |
|-----|-------------|----------------|
| NAT không hoạt động | `h_out curl 203.0.113.10` timeout | `sudo m r_out iptables -t nat -L PREROUTING -n` |
| ACL chặn nhầm | `h1 curl web1:80` bị block | `sudo m dmz_r iptables -L FORWARD -n` – kiểm tra rule order |
| OSPF không hội tụ | `show ospf neighbor` rỗng | Kiểm tra FRR log: `/tmp/frr-*.log` |
| LB không switch server | Tải >80% nhưng không chuyển | Đợi hold-down timer (30s); thử `--algorithm threshold` |
| Dashboard lỗi | `localhost:5000` không load | `pip install flask`; kiểm tra port 5000 không bị chiếm |
| ML false positive | IP hợp lệ bị block | Thêm IP vào `WHITELIST_IPS` trong `ml_security.py` |
| ECMP không hoạt động | Chỉ 1 đường trong `ip route` | Bật multipath FRR: `ip multipath` trong `frr_core.conf` |

---

## 📊 Tóm tắt tiêu chí đánh giá

| Mức | Tiêu chí | File / Lệnh | Trạng thái |
|-----|----------|-------------|------------|
| 2.1 | Topology 3 lớp + IP planning | `topology.py` | ✅ |
| 2.2 | OSPF + NAT/PAT | `setup_ospf.sh`, `nat_config.sh` | ✅ |
| 2.3 | ACL đa lớp Standard + Extended | `acl.sh` | ✅ |
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

## 👤 Tác giả

| Thông tin | Chi tiết |
|-----------|---------|
| **Họ và tên** | Nguyễn Tấn Mạnh |
| **MSSV** | 52300221 |
| **Môn học** | Mạng Máy Tính Nâng Cao |
| **Trường** | *(Điền tên trường của bạn)* |
| **Năm học** | 2024 – 2025 |

---

*© 2025 Nguyễn Tấn Mạnh – 52300221 | Mạng Máy Tính Nâng Cao*
