# Bảo mật triển khai Rice Reborn

## Whitelist IP và mã hóa

Cấu hình sử dụng đã chốt: mode direct, mở UI bằng IP public VPS:8080; whitelist IP public nhà tại Cloud Firewall. Khi IP nhà đổi, người vận hành cập nhật rule cloud. RICE_ALLOWED_IPS để trống để không phải cập nhật IP hai nơi. Không dùng tunnel trong luồng vận hành này; script không tự thay firewall.

Whitelist ở security group/firewall VPS giới hạn nguồn kết nối. IP public nhà thường đại diện cả mạng nhà sau NAT, không định danh riêng một máy. Dùng đúng IP public/32 và cập nhật khi ISP đổi IP; kiểm tra rule IPv4/IPv6 và các cổng đang lắng nghe.

HTTP trực tiếp vẫn truyền mật khẩu, cookie và credentials nhập vào UI mà không mã hóa. Whitelist không chống được đọc/sửa traffic trên đường truyền. Dùng HTTPS hoặc SSH tunnel khi quản trị qua Internet.

| Điểm kiểm soát | IP cần cho phép |
|---|---|
| Firewall cổng UI/SSH của VPS | IP public nhà người vận hành |
| Trusted IP của Binance API key | IP outbound VPS dùng gọi Binance |

Binance dùng HTTPS riêng ở chiều outbound. Whitelist UI không tự giới hạn việc dùng key bị đánh cắp; trusted IP ở Binance là cấu hình độc lập.

## Ba mode trong launcher

- **direct**: HTTP, bind 0.0.0.0, mặc định port 8080. Giữ cách truy cập IP:port đã yêu cầu; người vận hành quản lý firewall. Không coi HTTP là đường truyền bí mật.
- **ssh**: bind 127.0.0.1, HTTP ở hai đầu loopback; SSH mã hóa đoạn Internet. Không cần domain hoặc mở public 8080.
- **https**: bind 127.0.0.1, bật Secure cookie; reverse proxy TLS/chứng chỉ cấu hình riêng. Whitelist cổng public của proxy và chặn backend từ Internet.

```bash
sudo rice-rebornctl access-set ssh
sudo rice-rebornctl access-set direct
sudo rice-rebornctl access-set https
```

Mỗi lệnh tạm dừng/restart backend, BOT khởi động OFF. Không đổi firewall, SSH, SELinux, timezone hoặc tự cài chứng chỉ.

Trên máy nhà:

```powershell
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 user@IP-VPS
```

Đăng nhập `http://127.0.0.1:18080`. Bind 127.0.0.1 ở máy nhà giúp không chia sẻ tunnel cho cả LAN. Dùng SSH key; firewall SSH cũng giới hạn IP nhà.

RICE_ALLOWED_IPS là lớp phụ ở app, hỗ trợ IP/CIDR. App không tin X-Forwarded-For do client gửi. Loopback được phép cho health check/tunnel/proxy. Khi dùng proxy local, phải whitelist ở firewall/proxy; không dựa riêng vào allowlist của app.

## Các lớp bảo vệ trong mã

- CLI tạo mật khẩu bằng hash scrypt/Werkzeug; giới hạn thử đăng nhập, session 8 giờ. Đổi mật khẩu qua launcher yêu cầu dừng backend; session cũ bị thu hồi khi chạy lại.
- Cookie HttpOnly, SameSite=Strict; Secure ở mode HTTPS. CSRF cho mọi thao tác thay đổi; CSP/no-store và escape dữ liệu UI.
- Backend xác minh quyền Binance độc lập UI. Read/UNKNOWN/AUTH_ERROR không được đặt/hủy REAL. Gateway chỉ triển khai ghi lệnh Spot, không có luồng rút/chuyển tiền, Futures hoặc Margin.
- Service user riêng rice, code/venv thuộc root. Unit có NoNewPrivileges, bỏ capabilities, chỉ cho ghi data, chặn home/devices/kernel và giới hạn RAM/tasks.
- Runtime lock chặn hai backend dùng cùng dữ liệu. Update dựng venv riêng, kiểm tra phiên bản/import, backup trước chuyển và kiểm tra health trước báo thành công. Health lỗi thì phục hồi môi trường/unit cũ; không rollback sổ tài chính.

## Giới hạn còn lại

- API secret, Telegram token, session signing key và backup là plaintext được bảo vệ bằng quyền filesystem, **chưa mã hóa at rest**. .env/session/backup quyền 600, thư mục backup 700. Root hoặc process chiếm quyền user app vẫn đọc được secret; sandbox không thay bảo vệ host.
- API Read giảm hậu quả tài chính nhưng vẫn đọc được dữ liệu tài khoản theo quyền key. Khi cấp Trade, key bị lộ có thể gây mất vốn qua giao dịch dù không bật rút tiền.
- Lock package cố định phiên bản đã kiểm thử, không bảo đảm không có CVE. Chỉ cài source/kho package tin cậy và tiếp tục cập nhật bảo mật.
- Launcher giữ các venv và backup cũ; người vận hành theo dõi dung lượng và tự dọn. Backup có credentials, không gửi công khai hoặc đưa lên Git. Backup chứa env/session và database v1; không gồm logs, backtest hoặc dữ liệu Rice cũ ngoài v1.
- Chưa xác nhận firewall từ Internet hoặc chạy unit sandbox trên VPS của người dùng. Linux/WSL, fake systemctl/rollback và syntax check không thay nghiệm thu VPS thật.

Nguồn: [Ubuntu firewall](https://documentation.ubuntu.com/server/how-to/security/firewalls/), [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html), [Binance API key](https://www.binance.com/en/support/faq/detail/360002502072), [systemd sandbox](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).
