# Kiểm thử Rice Reborn

Chạy `python -m pytest tests/v1 -q`. Fixture chặn mạng; mọi credentials trong test đều giả.

- `test_engine.py`: preset VA, Decimal/lô, bộ nhớ EMA, priority thoát, lệnh/fill nguyên tử, PAPER và khóa thực thi REAL.
- `test_web.py`: đăng nhập, CSRF, IP:port, secret, REAL/PAPER và các thao tác UI/API.
- `test_integrations.py`: permission/signing/rate limit, Telegram retry/dedup/OFF, backtest từ dữ liệu giả đến Excel theo mẫu.
- `test_maintenance.py`: chẩn đoán chỉ đọc, VA giữ vốn, backup có checksum/quyền hạn và từ chối sửa runtime đang chạy.
- `test_deploy.py`: chỉ chạy Linux/WSL; giả lập systemctl để kiểm tra menu, chuyển môi trường, rollback và từ chối update checkout bẩn. Không quản lý service thật.

Kết quả offline không thay nghiệm thu tài khoản Read, Telegram hoặc lệnh thật. Script đo tạm và ảnh UI ở `.artifacts/`, không đưa vào Git.
