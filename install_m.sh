#!/bin/bash
# install_m.sh
# Cài đặt tiện ích 'm' của Mininet vào /usr/local/bin/ để chạy lệnh trên node dễ dàng

echo "=== Đang cài đặt tiện ích 'm' cho Mininet ==="

sudo bash -c 'cat > /usr/local/bin/m << "EOF"
#!/bin/bash
if [ -z "$1" ]; then
  echo "usage: $0 node cmd [args...]"
  exit 1
fi
node=$1
shift
pid=$(ps -eo pid,cmd | grep "mininet:$node$" | grep -v grep | awk "{print \$1}")
if [ -z "$pid" ]; then
  echo "Cannot find node $node"
  exit 1
fi
exec mnexec -a $pid "$@"
EOF'

sudo chmod +x /usr/local/bin/m

echo "✅ Đã cài đặt thành công tại /usr/local/bin/m"
echo "Bây giờ bạn có thể sử dụng 'sudo m <node_name> <command>'"
