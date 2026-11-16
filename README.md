# Rice Reborn

Web terminal nhẹ cho **Binance Spot**, chuyển nghiệp vụ MayinTien sang Crypto: preset VA, P1/P2/P3, REAL/PAPER, mua bán tay/BOT, SL/TP/E/ARM/DYNAMIC, Telegram, lịch sử và backtest.

API **Read** dùng đầy đủ cho theo dõi/thông báo. Chức năng đặt/sửa/hủy REAL có sẵn và chỉ thực thi khi API được xác minh có quyền Spot Trade. Cấp quyền không tự bật BOT. Không có endpoint Futures, Margin, chuyển hoặc rút tiền.

## Mở trên Windows

Python 3.11 trở lên:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m rice_reborn.main --demo --port 8090
```

Mở [demo tại localhost:8090](http://127.0.0.1:8090). Demo chỉ bind localhost, không nạp `.env`, không gọi Binance/Telegram; dữ liệu mẫu và PAPER tách khỏi LIVE.

Chạy với dữ liệu thật:

```powershell
.\.venv\Scripts\python.exe -m rice_reborn.main --set-password
.\.venv\Scripts\python.exe -m rice_reborn.main
```

Đăng nhập [localhost:8080](http://127.0.0.1:8080), mở **Kết nối**, nhập API key/secret và Telegram bot token/chat ID. Để trống các ô secret để giữ giá trị đã lưu. API Read không cần nâng quyền để nhận tín hiệu. Dữ liệu thị trường công khai hoạt động khi chưa có API tài khoản.

## VPS Linux

Ubuntu 24.04/26.04 hoặc AlmaLinux 9/10:

```bash
sudo bash deploy/rice-reborn.sh
```

Chọn **1. Cài đặt / mở Rice**. Lần đầu script tự cài Python, thư viện, preset VA và systemd, hỏi mật khẩu đăng nhập web rồi khởi động Rice. Những lần sau mục 1 chỉ bật service và hiện hướng dẫn truy cập. Không tự bật bot giao dịch.

Menu chính chỉ có **1 mở Rice, 2 cập nhật, 3 xem trạng thái/lỗi, 4 cài đặt khác/bảo trì**. Mục 4 chứa restart, tắt Rice, đổi mật khẩu web, đổi cách truy cập, backup, nạp VA, chẩn đoán và cài lại môi trường. Thoát menu không tắt Rice.

Cài mới mặc định dùng **SSH tunnel**, web chỉ nghe `127.0.0.1:8080`. Script hiện lệnh chạy trên Windows và địa chỉ mở web `http://127.0.0.1:18080`; khi đang SSH, địa chỉ VPS, tài khoản và cổng SSH được điền từ phiên hiện tại. Cloud Firewall chỉ cần mở cổng SSH cho IP nhà, không mở public 8080. Script không tự thay firewall, SSH hoặc SELinux. Khi cập nhật, giữ nguyên mode truy cập đã lưu; đổi mode qua **4 → 4 → 1** nếu muốn dùng SSH tunnel.

```bash
sudo rice-rebornctl start
sudo rice-rebornctl stop
sudo rice-rebornctl restart
sudo rice-rebornctl status
sudo rice-rebornctl logs
sudo rice-rebornctl update
sudo rice-rebornctl check
sudo rice-rebornctl state-check
sudo rice-rebornctl preset-va
sudo rice-rebornctl backup
```

Update dùng Git fast-forward, dừng khi checkout có thay đổi; không reset/stash. Môi trường mới dựng riêng trong `/opt/rice-reborn/venvs/`; pip không ghi đè bản đang chạy. Trước khi chuyển môi trường, service dừng ngắn để backup SQLite/credentials. Nếu bản mới không qua health check khởi động, launcher trả môi trường và unit về bản cũ, giữ nguyên sổ tài chính. Backup chứa secret, quyền 600 trong thư mục 700; không đưa lên Git. Các môi trường/backup cũ được giữ để vận hành tự dọn.

Đổi mật khẩu:

```bash
sudo rice-rebornctl password
```

## Sử dụng

Cách truy cập hiện tại dùng SSH tunnel: chọn mục 1 và làm theo lệnh Windows được hiện sau khi Rice chạy. Khi IP nhà đổi, cập nhật rule Cloud Firewall của cổng SSH. Binance API whitelist IP đầu ra của VPS. Chi tiết: [bảo mật triển khai](docs/SECURITY.md).

- **REAL/PAPER:** đổi sổ để xem dữ liệu. Mỗi sổ có lệnh, vốn, vị thế, bộ nhớ EMA và khóa riêng. BOT OFF khi khởi động; chỉ một sổ bật tạo BUY mới cùng lúc.
- **RULE:** chỉnh preset, mode realtime/nến đóng, nhịp quan sát, vốn USDT, P1, P2, WHIPSAW và khóa lỗ. Nạp lại VA chỉ đổi ngưỡng, không mang vốn/mã CKVN sang.
- **Watchlist/Priority:** cặp USDT; AUTO hoặc ALERT, hạn mức, % dùng vốn và MAX lượt. ALERT vẫn được theo dõi, không giữ slot BUY mới.
- **Ví Spot:** số dư `free`/`locked`. Nhận một phần thành vị thế theo dõi và nhập giá vốn đã xác minh. Giá vốn 0 chỉ cho phân tích kỹ thuật; không dựng SL/PnL từ số liệu thiếu.
- **Vị thế:** chọn SL/TP/E/PROTECT, quyền BOT/tự bán, cấu hình thoát riêng hoặc về RULE. Đo lại MAE/MFE từ giá phút sau lúc mua; thao tác hoàn tất tạm tắt tự bán để kiểm tra.
- **Phiếu lệnh:** MARKET, LIMIT hoặc LIMIT kích hoạt tại bot. Để trống KL dùng AUTO theo ngân sách; LIMIT tính theo giá nhập. AUTO chưa xác nhận tín hiệu. TP/SL và E/M trên phiếu chỉ áp dụng lượt BUY tay mới. SELL ở bảng lệnh hoặc BÁN ĐÃ CHỌN. REAL hỏi xác nhận trước gửi; backend luôn kiểm tra quyền độc lập với UI.
- **Telegram:** nhóm tín hiệu/thoát/lệnh/hệ thống, gom BUY, cooldown và gửi bù. Gửi thử chỉ chạy khi bấm nút tương ứng.
- **Backtest:** một worker riêng, DAILY hoặc REPLAY phút, cùng rule và bộ thực thi PAPER. Một file Excel/một sheet mỗi cặp theo cấu trúc CTS, có các mốc kiểm tra ngày. MAX LỆNH=1 như MayinTien; không giả kết quả cho MAX>1.

## Runtime và kiểm tra

`data/v1/` chứa database theo fingerprint API, session key, logs và backtest; `data/demo/` dành riêng demo. Đổi sang API key khác mở profile riêng; không tự gộp sổ tài chính của hai key. Nâng quyền trên cùng key giữ nguyên profile. `.env` không được trả về UI/log. API/settings v0.3 không tự ghi đè preset v1.

Một backend, một WebSocket dữ liệu, một WebSocket tài khoản nếu dùng được; REST đối soát dự phòng. Worker backtest chỉ dùng public data, không nhận credentials. SQLite commit lệnh/fill nguyên tử, khóa chống trùng tồn tại qua restart. TRACE giữ 7 ngày; log giữ 30 ngày; lịch sử giao dịch không tự xóa.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q rice_reborn
.\.venv\Scripts\python.exe -m pip check
```

Kiểm thử offline không chứng minh lợi nhuận hoặc nghiệm thu lệnh thật. Chỉ dùng key Read ở giai đoạn kiểm tra tài khoản; việc thử giao dịch REAL cần quyền và quyết định riêng của người vận hành.

Chi tiết: [nghiệp vụ và các giới hạn](docs/NGHIEP_VU.md).
