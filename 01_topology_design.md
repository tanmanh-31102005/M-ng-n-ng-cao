# Kế hoạch thiết kế hệ thống mạng Campus 3 lớp

## 1. Giả định kịch bản thiết kế
Doanh nghiệp Vừa, văn phòng 2 tầng cần 1 hệ thống mạng đáp ứng các yêu cầu: Tính sẵn sàng cao (High Availability), độ tin cậy, an toàn bảo mật, và tiết kiệm chi phí.
- **Tầng 1 (VLAN 10):** Bộ phận IT/Dev (Khoảng 50 thiết bị, PC/Phone/Printer).
- **Tầng 2 (VLAN 20):** Bộ phận HR/Sales (Khoảng 50 thiết bị).
- **Phòng Server (DMZ):** Chứa 2 Web Server nội bộ + Public (Tiết kiệm chi phí, sử dụng Load Balancing tự dựng thay vì mua Hardware LB).

## 2. Mô hình Logic & Vật lý
```text
[ Internet / Outside ]
          | (203.0.113.0/24)
  +-------+-------+
  |    r_out      | (Router biên ngoài, giả lập ISP)
  +-------+-------+
          | 192.168.100.0/30
  +-------+-------+
  |    core_r     | (Core Router/Switch L3 - Đảm bảo định tuyến lõi)
  +-------+-------+
       |     |      (Mô hình 2 Distribution đảm bảo High Availability)
       |     |
  +----+     +----+
  | dist1|   | dist2| (Distribution Switches L3 - Chạy OSPF)
  +-+-+--+   +-+-+--+
    | |        | |  
    | +--------+ |   
    |   Cross    |   
    | +--------+ |   
    | |        | |  
  +-+-+--+   +-+-+--+      +----------+
  | acc1 |   | acc2 |      |  dmz_r   | (Chặn giữa mạng nội bộ và DMZ)
  +---+--+   +---+--+      +----+-----+
      |          |              |
   [VLAN 10]  [VLAN 20]      +--+--+
    (h1)       (h2)          |     |
                          (web1) (web2)
```
*(Trong Mininet, do STP khá phức tạp nên kết nối Access-Dist ở mô hình code dưới sẽ dùng link đơn giản hoá, nhưng thực tế bạn có thể cấu hình Trunking OVS. Tại đây ta dùng IP Routing OSPF từ Distribution trở lên cốt lõi).*

## 3. Bảng quy hoạch IP

| Thiết bị / Vùng | Vai trò | Subnet | Gateway Interface |
|---|---|---|---|
| **Inside (VLAN 10)** | IT/Dev | `172.16.10.0/24` | `172.16.10.1` (trên dist1) |
| **Inside (VLAN 20)** | HR/Sales | `172.16.20.0/24` | `172.16.20.1` (trên dist2) |
| **DMZ** | Web Servers | `10.10.10.0/24` | `10.10.10.1` (trên dmz_r) |
| **Outside** | Internet | `203.0.113.0/24` | `203.0.113.1` (trên r_out) |
| **Link: Core-Dist1** | Point-to-Point | `192.168.1.0/30` | Lần lượt `.1` và `.2` |
| **Link: Core-Dist2** | Point-to-Point | `192.168.1.4/30` | Lần lượt `.5` và `.6` |
| **Link: Core-DMZ** | Point-to-Point | `192.168.1.8/30` | Lần lượt `.9` và `.10` |
| **Link: Core-ISP** | Point-to-Point | `192.168.100.0/30` | Lần lượt `.1` và `.2` |

## 4. Danh sách Node trong Mininet (Mapping physical)

| Loại Node Mininet | Tên trong Code Mininet | Logic Layer |
|---|---|---|
| Host | `h1` (VLAN 10), `h2` (VLAN 20), `h_out` | Thiết bị End-user |
| Host (làm Server)| `web1`, `web2` | DMZ Servers |
| OVS Layer 2 | `acc1`, `acc2` | Access Layer |
| Linux Router (Host)| `dist1`, `dist2`, `core`, `dmz`, `r_out` | Core / Dist / Biên (FRR OSPF) |
