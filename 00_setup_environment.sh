#!/bin/bash
# 00_setup_environment.sh
# Môi trường: Ubuntu 22.04 LTS

echo "=== Cập nhật hệ thống ==="
sudo apt update -y

echo "=== Cài đặt Mininet và Open vSwitch ==="
sudo apt install mininet openvswitch-switch openvswitch-common -y

echo "=== Cài đặt FRRouting (FRR) cho OSPF ==="
# Cài bản đi kèm kho Ubuntu 22.04 là đủ dùng cho OSPF
sudo apt install frr frr-pythontools -y

echo "=== Cài đặt các công cụ mạng: iperf3, iptables ==="
sudo apt install iperf3 iptables iproute2 net-tools curl wget -y

echo "=== Cài đặt Python và các thư viện === "
sudo apt install python3 python3-pip -y
sudo pip3 install matplotlib seaborn pandas

echo "=== Cấu hình bật IP Forwarding trên Kernel ==="
sudo sysctl -w net.ipv4.ip_forward=1
# Ghi vào sysctl.conf để cố định
sudo sed -i '/net.ipv4.ip_forward/d' /etc/sysctl.conf
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

echo "=== Kiểm tra tiến trình cài đặt ==="
echo "- Phiên bản Mininet:"
mn --version
echo "- Phiên bản FRR:"
frr --version
echo "==========================================="
echo " Cài đặt KHÁM PHÁ MÔI TRƯỜNG THÀNH CÔNG!   "
echo "==========================================="
