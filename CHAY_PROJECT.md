# Chạy dự án bằng một lệnh

Mở PowerShell:

```powershell
cd D:\shopify-loyalty
powershell -ExecutionPolicy Bypass -File .\run-project.ps1
```

Script tự kiểm tra Django/database/migration, build Vue khi source mới hơn bản
build, rồi chạy **Django + django-q worker + Cloudflare Tunnel** ở nền.
Không cần kích hoạt `.venv`, không cần mở ba terminal. Sau đó mở app đã cài
trong Shopify Admin. Django admin: <http://127.0.0.1:8000/admin/>.

Tham số `ExecutionPolicy Bypass` chỉ áp dụng cho tiến trình PowerShell này,
không đổi policy toàn máy. Script không tự cài package, migrate hoặc chạy
Shopify CLI/deploy/release.

## Kiểm tra, xem trạng thái và dừng

```powershell
# Chỉ kiểm tra cấu hình, không khởi động service
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 -Action Check

# Xem service nào đang chạy và vị trí file log
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 -Action Status

# Dừng các tiến trình do script này khởi động, gồm worker con
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 -Action Stop
```

Chạy Start lần nữa sẽ giữ các service mà script đã chạy, không mở trùng.
Script kiểm tra PID, thời điểm khởi động và executable trước khi dừng, để không
dừng nhầm tiến trình khác sau khi PID được dùng lại. Nếu bạn đã tự chạy
runserver/qcluster ở terminal khác, dừng chúng trước khi dùng script này.

Log/PID ở `D:\shopify-loyalty\.localrun\`, đã loại khỏi Git. `Status` in đúng
đường dẫn stdout/stderr của từng service. Tunnel khởi động thành công chưa đồng
nghĩa với đã kết nối Cloudflare: xem log tunnel để kiểm tra kết nối.

## Các chế độ khác

```powershell
# Chạy local, không mở tunnel
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 -SkipTunnel

# Chỉ chạy Django để xem giao diện, không xử lý job/webhook
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 -SkipWorker -SkipTunnel

# Ép build lại Vue
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 -BuildFrontend
```

Django chạy `--noreload` để script quản lý tiến trình chính xác. Sau khi sửa
Python: Stop rồi Start lại. Sau khi sửa Vue: Stop rồi Start (script tự build khi
cần), hoặc tự `npm run build` trong frontend rồi F5. Không chạy thêm Vite trong
luồng này vì Django đã phục vụ bản build của Vue tại `/app/`.

## Điều kiện trên máy

- PostgreSQL đang chạy; `backend/.env` và `.venv` đã thiết lập như hiện tại.
- Có Node/npm nếu cần build Vue. Dependency đã cài; nếu thiếu, chạy `npm ci`
  trong frontend trước.
- Có cloudflared trên PATH và named tunnel hiện có:
  `C:\Users\Admin\.cloudflared\config.yml`.
- Origin trong cấu hình tunnel là **http://127.0.0.1:8000**, phù hợp Django.

Nếu đổi tên/ID tunnel hoặc vị trí file cấu hình:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-project.ps1 `
  -TunnelConfig "C:\Users\Admin\.cloudflared\config.yml" `
  -TunnelId "4151c9cf-0d7e-4cc8-b50a-bfebfc7a6763"
```

Nếu Check báo migration chưa áp dụng, sao lưu database rồi chạy trong backend:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py setup_schedules
```

Lệnh khởi động không tự cấp điểm hoặc bật chính sách: worker xử lý theo cấu
hình đã lưu của merchant và các lịch đã thiết lập. Email dev vẫn dùng console
đã che dữ liệu riêng tư; chỉ gửi thư thật sau khi cấu hình SMTP và bật loại thư.
Hướng dẫn nghiệp vụ và test từng bước nằm trong `docs/`.

Đã kiểm tra Check, Start local, Start lại không trùng tiến trình và Stop trên
máy này ngày 13/10/2026. Backend trả HTTP 200; Stop giải phóng cổng 8000.
Worker và tunnel chưa được khởi động trong lần kiểm tra script này.
