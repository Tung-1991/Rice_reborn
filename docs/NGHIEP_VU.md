# Rice Reborn v1 · nghiệp vụ hiện hành

## Nguồn chuẩn

Nghiệp vụ port từ MayinTien: EMA/RSI Wilder, VA/X phiên, P1, Priority/vốn, khóa lỗ, WHIPSAW, SL/TP/E/ARM/DYNAMIC, queue và chống khớp trùng. Các hàm toán thuần được giữ trong `maths.py`; preset hiệu lực là `presets/va_crypto.json`, lấy default cộng JSON VA hiện tại, không dùng phần README preset đã lỗi thời.

Scope: Binance Spot USDT, một profile API mỗi instance, REAL/PAPER riêng. Không có CKVN, ATO/ATC, lịch nghỉ/giờ sàn, T+2, lô 100, phí/thuế DNSE, Futures, Margin hoặc chuyển/rút tiền. Coin `locked` là số dư bị khóa, không phải cổ chờ về.

## Giao diện theo MayinTien

UI lấy bố cục từ `dashboard/panels.py`, màu/chữ từ `dashboard/view.py` và cột bảng từ `dashboard/tables.py` của MayinTien: cột trái 420 px chứa NAV, bốn dòng CẶP/MODE/TOOLS/E/M và phiếu lệnh; bên phải là LỆNH ĐANG CHẠY, hai sổ REAL/PAPER và khối HỆ THỐNG/PREVIEW phía dưới. Giữ bảng nhiều cột, màu trạng thái, chọn nhiều dòng và các cửa sổ INFO/DANH MỤC/LỊCH SỬ/BACKTEST. Không thêm dashboard biểu đồ hoặc các thẻ thị trường lớn thay cho cấu trúc này.

RULE giữ các tab NGHIỆP VỤ (PHASE 1/2/3), E/M và THỰC THI. Kết nối giữ ba tab, đổi thành BINANCE/CẶP GIAO DỊCH/TELEGRAM. Sàn/trần của CKVN đổi thành thấp/cao nến 1D UTC; tiền và khối lượng dùng USDT/coin. Trình duyệt hẹp chuyển sang cuộn và bố cục dọc.

TP/SL và nút E/M trên phiếu lệnh áp dụng cho lượt BUY tay mới, lưu kèm yêu cầu và giữ qua sửa lệnh/restart. E/M của phiếu tay khởi động OFF như MayinTien; cấu hình vị thế BUY BOT vẫn theo preset. Để trống TP/SL theo RULE, nhập 0 tắt nhánh đó. Vị thế đang giữ chỉnh riêng qua Override; nút E/M trên phiếu không tự sửa vị thế cũ.

Để trống KL dùng AUTO cả ở preview và khi bấm đặt. LIMIT tính khối lượng/giữ vốn theo giá nhập và LOT_SIZE; MARKET theo giá mới và đồng thời LOT_SIZE/MARKET_LOT_SIZE. SELL dùng bảng vị thế hoặc BÁN ĐÃ CHỌN, kiểm tra số lượng trước khi xác nhận.

## Preset và ngày nến

VA mặc định: BUY EMA2/6 + RSI14 tăng, cần cắt lên; VA quét trước X=2, gồm hôm nay và hôm qua UTC. SELL EMA3/6; RSI SELL OFF, E CŨ OFF; E AUTO 100% còn lại, lặp theo đợt. TP +9% bán 35% gốc một lần; ARM +4,5%, lùi 2,8 điểm % lãi, AUTO 35% gốc, lặp ON, lặp dưới ARM OFF. DYNAMIC ALERT 35% gốc, giữ lãi 72%, công thức ATR kế thừa. SL đầu −3,5%; SL vào lại −2,8%. MAX 4 mã, WHIPSAW 3/7, chuỗi lỗ 3 lần/24 giờ; P1 override 100%.

Không mang mã/vốn/khung giờ CKVN sang. REAL ban đầu vốn 0; PAPER mặc định 1.000 USDT, demo có danh mục/vốn mẫu riêng. BUY window OFF. P1 AUTO tùy chọn theo BTC, ETH chỉ đối chiếu.

Nến 1D UTC đóng lúc 07:00 UTC+7; cả cuối tuần đều có ngày nến. REALTIME sửa close của nến ngày đang chạy bằng giá mới; TICK/1M/2M/5M là nhịp quan sát, không phải khung EMA. CLOSED dùng ngày đã hoàn tất. Thiếu ngày/nền/giá mới phải hiện chờ dữ liệu. Không dùng dữ liệu quá 10 giây để gửi lệnh.

Chuỗi EMA/RSI giữ đủ độ chính xác, không làm tròn từng bước. Chỉ báo có đơn vị giá so sánh ở độ chính xác cặp; RSI/% dùng half-up hai số lẻ. Giá/tiền/khối lượng thực thi dùng Decimal; LOT_SIZE và MARKET_LOT_SIZE áp dụng đồng thời. Metadata Binance và kiểm tra phía sàn là nguồn giới hạn cuối cùng.

## Tín hiệu và thực thi

Nhanh lớn hơn chậm không tự chứng minh giao cắt. Cắt live cần hai quan sát; phục hồi dùng giá phút và EMA nền nến ngày. Replay phút không chứng minh mọi dao động tick. VA giữ cắt chưa dùng; mode X NGÀY giới hạn tuổi cắt. Preview/bộ đếm không tự cấp quyền BUY.

BUY có khớp đầu tiên tiêu thụ tín hiệu. SELL một phần giữ bộ nhớ; SELL hết mới reset. Mỗi cắt BOT chỉ thử đặt một lần; hủy/từ chối khớp 0 không tự thử lại. Bỏ cắt là override tạm theo mã/sổ, vẫn qua các điều kiện khác và hết sau một lần thử/restart.

API quyền Read không tạo yêu cầu giao dịch REAL đang chờ cấp quyền. Giao dịch REAL cần quyền key được xác minh, tài khoản cho phép, dữ liệu mới và các điều kiện nghiệp vụ; giao diện bị vô hiệu hóa không phải lớp kiểm tra duy nhất. Permission UNKNOWN/AUTH_ERROR khóa thực thi. BOT ON/OFF độc lập với Telegram; BOT OFF chặn lệnh tự động chưa gửi, tiếp tục quan sát/đối soát.

Mỗi lượt BUY có vị thế riêng; sửa phần dư cùng lượt giữ ID vị thế. SELL gắn đúng vị thế, không vượt khối lượng chưa phân bổ cho SELL khác. Không suy có khớp từ tín hiệu, giá LIMIT hoặc snapshot số dư. Timeout là UNKNOWN; không POST lại trước đối soát. Fill receipt, số lượng, vốn và lệnh cập nhật cùng transaction. Lệnh ngoài app hiển thị riêng; không đoán gán vào vị thế chỉ dựa trên mã.

RECHECK xác minh lại nhánh/đợt E/quyền trước gửi SELL chưa lên sàn. SL ưu tiên cao nhất; tiếp theo TP 100%, E, TP một phần, PROTECT. SL/TP không phụ thuộc việc EMA sẵn sàng. E thiếu dữ liệu giữ trạng thái đợt, không coi là đã hết điều kiện. ARM/DYNAMIC chỉ bán khi giá còn trên vốn; không chặn E/SL khi lỗ. Khối lượng tính theo gốc/còn lại rồi làm tròn xuống bước cặp; phần không giao dịch được ghi dust.

## Giá vốn, phí và lưu trữ

Số dư Binance không chứa giá vốn đầy đủ của mọi tài sản. Lịch sử riêng tải theo cặp và lưu bằng ID sàn, không tự dựng fill/vị thế khi có nạp/rút ngoài hoặc thiếu lịch sử. Vị thế nhập tay phải dùng số lượng chưa được phân bổ và giá vốn do người vận hành xác minh; chưa nhận quản lý tự động.

Phí thực thu lưu nguyên số lượng và tài sản thu phí. USDT dùng trực tiếp; phí base làm giảm inventory; BNB/tài sản khác được quy đổi theo mẫu thị trường gần thời điểm khớp hoặc close phút lịch sử, có nhãn ước tính và nguồn giá. Giá quy đổi là ước tính, không phải một lần khớp BNB. Thiếu nguồn quy đổi thì giữ THIẾU PHÍ, không thay bằng 0 và không xác nhận PnL ròng. Khi tải được giá phút còn thiếu, backend tính lại giá vốn/PnL và chuỗi lỗ từ các fill đã lưu. PnL phần bán phân bổ cả chi phí mua, không cộng lại PnL toàn lượt vào từng phần.

Các cửa sổ UI đọc snapshot và lịch sử phân trang. Đóng trình duyệt không dừng backend. Khóa runtime của hệ điều hành chặn hai backend cùng mở một thư mục dữ liệu. TRACE 2 phút/lần mặc định, giữ 7 ngày; không gửi Telegram. Lệnh/fill/lịch sử tài chính không tự xóa. Database dùng WAL/synchronous FULL, nguồn mạng không gọi bên trong financial transaction.

## Telegram và backtest

Telegram có outbox bền vững, dedup riêng sự kiện/sổ/mã, chỉ xác nhận sau delivery thành công. Gửi lỗi được retry có giãn cách. Telegram OFF với gửi bù OFF bỏ tin phát sinh khi tắt; bật lại chỉ nhận tin mới. Thông báo không tiêu thụ quyền giao cắt hoặc được diễn giải là đã mua/bán.

Backtest worker tách process và credentials, chạy tuần tự/ưu tiên thấp, dùng chung Strategy và Execution PAPER. Dữ liệu khởi động đủ nền, ghép nến phút thành nến ngày từ những mẫu đã xuất hiện; lệnh ENTRY thực hiện ở open quan sát kế tiếp. Đường OHLC bảo thủ open → low → high → close không là thứ tự tick thật. Phí mô phỏng 0,1% USDT, slippage 0,05%; báo cáo ghi rõ giả định. Có DAILY/REPLAY; MAX LỆNH=1 được kiểm tra rõ. Excel một cặp/một file/một sheet, bảng lượt theo mẫu CTS đã bỏ các cột T+2 và có INFO/mốc ngày thực tế.

## Giới hạn nghiệm thu

Offline/broker giả xác minh logic, quyền, restart, dedup, fill và báo cáo; không chứng minh lợi nhuận, latency sàn hay giao dịch REAL. GET public, đối chiếu tài khoản Read, gửi thử Telegram và giao dịch REAL là các mức kiểm tra khác nhau. Không chạy lệnh thật hoặc gửi tin chỉ để backtest/kiểm thử offline.

Nguồn API: [Binance Spot REST](https://developers.binance.com/en/docs/products/spot/rest-api), [filters](https://developers.binance.com/en/docs/products/spot/filters), [User Data Streams](https://github.com/binance/binance-spot-api-docs/blob/master/user-data-stream.md).
