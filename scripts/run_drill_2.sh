#!/bin/bash
set -e
export PATH="$HOME/.lab_venv/bin:/usr/bin:/bin:$PATH"
cd /mnt/c/Users/Hi/OneDrive/Documents/GitHub/D23-NgoTuanTung-2A202602826

# Xoá log cũ của drill-2
rm -f reports/drill-2-withdr.jsonl reports/failover-events.jsonl reports/runbook-run.jsonl

# Seed stack
python3 state/seed_vectors.py --region a --docs 200
python3 state/seed_vectors.py --region b --docs 0 --weights-mb 0
printf a > edge/active_region

# Start services
bash scripts/down_bare.sh 2>/dev/null || true
bash scripts/up_bare.sh

# Ingest liên tục vào Region A
python3 state/ingest.py --region a --rate 0.5 --duration 150 &
INGEST_PID=$!

# Replicate liên tục sang replica
python3 state/replicate.py --every 30 --duration 150 --backend fs &
REPL_PID=$!

# Chờ chu kỳ replication đầu tiên hoàn tất
sleep 5

# Bật load generato
python3 loadgen/traffic.py --duration 100 --rps 2 --out reports/drill-2-withdr.jsonl &
LOADGEN_PID=$!

# Bật health checke
python3 dr/health_checker.py --interval 5 --threshold 3 --duration 100 --out reports/health-events.jsonl &
HEALTH_PID=$!

# Chờ 12s trước khi kill Region A
sleep 12
python3 chaos/kill_region.py --region a --mode netblock --mock

# Kích hoạt runbook failove
python3 dr/runbook.py --primary a --target b --backend fs --auto

# Đợi traffic generator chạy xong
wait $LOADGEN_PID 2>/dev/null || true

# Dọn dẹp các tiến trình nền
kill $INGEST_PID $REPL_PID $HEALTH_PID 2>/dev/null || true

# Đo RTO và in kết quả
python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300 > reports/measure-drill-2.json
cat reports/measure-drill-2.json

# Dừng stack
bash scripts/down_bare.sh
