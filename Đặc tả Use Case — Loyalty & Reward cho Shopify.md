# Đặc tả Use Case — Hệ thống Loyalty & Reward cho Shopify

Phiên bản 0.1 (bản nháp để rà soát) · 11/10/2026 · Backend Django · Giao diện Vue · Tích hợp Shopify Admin API và webhook

## 1. Giới thiệu

### 1.1 Mục đích

Tài liệu mô tả đầy đủ các use case của hệ thống Loyalty & Reward: ai làm gì, trong điều kiện nào, kết quả ra sao, và hệ thống phải xử lý thế nào khi có sự cố hoặc tình huống bất thường. Đây là đầu vào cho thiết kế dữ liệu, đặc tả API, backlog và kịch bản kiểm thử.

### 1.2 Phạm vi

**Trong phạm vi:** tích điểm theo đơn hàng, thu hồi điểm khi hủy hoặc hoàn tiền, đổi điểm lấy mã giảm giá, hết hạn điểm, sổ cái điểm, cấu hình chương trình và phần thưởng, tra cứu khách hàng, điều chỉnh điểm thủ công, đối soát dữ liệu, báo cáo cơ bản.

**Ngoài phạm vi (giai đoạn này):** phát hành lên App Store, thu phí merchant (billing), nhiều cửa hàng, xử lý thanh toán. Giới thiệu bạn bè, điểm sinh nhật, phân hạng chỉ liệt kê ở mức tổng quan (Phase 2).

### 1.3 Quy ước

- UC-xx: use case. BR-xx: quy tắc nghiệp vụ. A-xx: giả định. R-xx: ràng buộc từ Shopify. Q-xx: câu hỏi mở cần chủ shop quyết định. AC-xx.y: tiêu chí chấp nhận.
- Ưu tiên theo MoSCoW (Must, Should, Could, Won't). Giai đoạn: MVP hoặc Phase 2.
- Giá trị ghi “đề xuất” là mặc định gợi ý, cần chủ shop xác nhận trước khi chốt.
- Mỗi use case mô tả theo mẫu: tác nhân, mục tiêu, kích hoạt, tiền điều kiện, hậu điều kiện, luồng chính, luồng thay thế, ngoại lệ, quy tắc liên quan, tiêu chí chấp nhận.

## 2. Bối cảnh, giả định và ràng buộc

### 2.1 Giả định

- A-01: Hệ thống phục vụ một cửa hàng Shopify duy nhất (development store). Mọi bản ghi vẫn gắn mã cửa hàng để mở rộng sau này.
- A-02: Ứng dụng là app phát triển riêng cho cửa hàng, lấy token bằng client credentials grant (token sống khoảng 24 giờ, tự làm mới).
- A-03: Shopify là nguồn sự thật về đơn hàng và khách hàng. Hệ thống loyalty là nguồn sự thật duy nhất về điểm.
- A-04: Webhook của Shopify có thể đến trùng, sai thứ tự hoặc bị mất. Hệ thống phải idempotent và có cơ chế đối soát.
- A-05: Chỉ khách có tài khoản (Customer ID) mới tích điểm. Khách mua không đăng nhập (guest) không tích điểm ở MVP (xem Q-03).
- A-06: Điểm tính theo tiền tệ gốc của cửa hàng (shop currency), không theo tiền tệ hiển thị của khách.
- A-07: Nhân sự cửa hàng dùng trang quản trị riêng (Vue) có đăng nhập. Khách xem điểm qua widget trên storefront (làm ở giai đoạn sau).
- A-08: Mã giảm giá khi đổi điểm do hệ thống tạo qua Admin API, không dùng Shopify Functions.

### 2.2 Ràng buộc từ Shopify

- R-01: Webhook phải được phản hồi nhanh (lưu xong trả 200), việc nặng chạy nền.
- R-02: Admin API có giới hạn tốc độ. Thao tác hàng loạt (đồng bộ khách cũ) phải chia lô, có nghỉ, hoặc dùng bulk operation.
- R-03: Mặc định app chỉ đọc được đơn trong khoảng 60 ngày gần nhất nếu không có quyền đọc toàn bộ lịch sử. Nhập lịch sử đơn cũ cần xin thêm quyền.
- R-04: Thứ tự giao webhook không được đảm bảo. Trạng thái đơn hiện tại phải được xác nhận lại bằng API trước khi ghi sổ.
- R-05: Mỗi lần thay đổi scope phải phát hành phiên bản mới của app.

### 2.3 Câu hỏi mở cần chủ shop quyết định

- Q-01: Điểm tính trên tổng tiền hàng sau giảm giá, có trừ thuế và phí vận chuyển không?
- Q-02: Điểm có ở trạng thái Chờ vài ngày trước khi dùng được không, và bao lâu?
- Q-03: Khách guest có được tích điểm bù khi sau này tạo tài khoản bằng cùng email không?
- Q-04: Điểm có hết hạn không, hết hạn sau bao lâu?
- Q-05: Có cho phép số dư âm khi hoàn tiền sau khi điểm đã được dùng?
- Q-06: Có phân hạng thành viên ngay ở MVP hay để Phase 2?

## 3. Tác nhân

- **Shopper (Khách hàng):** người mua có tài khoản. Xem điểm, đổi thưởng.
- **Merchant Admin (Quản trị cửa hàng):** chủ shop hoặc quản lý. Cấu hình chương trình, phần thưởng, điều chỉnh điểm, xem báo cáo.
- **Merchant Staff (Nhân viên):** tra cứu khách, xem sổ cái. Quyền điều chỉnh điểm bị giới hạn (BR-21).
- **Shopify (hệ thống ngoài):** nguồn sự kiện (webhook) và API đơn hàng, khách, mã giảm giá.
- **Hệ thống (Scheduler):** các tác vụ định kỳ: kích hoạt điểm chờ, hết hạn điểm, đối soát, thử lại tác vụ lỗi.
- **Vận hành (Developer):** theo dõi lỗi, chạy lại sự kiện thất bại.

## 4. Thuật ngữ

- **Điểm (Points):** đơn vị thưởng do hệ thống quản lý, là số nguyên.
- **Giao dịch điểm:** một bản ghi trong sổ cái, không sửa, không xóa. Loại: EARN (tích), REVOKE (thu hồi), REDEEM (đổi), REDEEM\_REFUND (hoàn điểm đổi), EXPIRE (hết hạn), ADJUST (điều chỉnh).
- **Sổ cái (Ledger):** chuỗi giao dịch điểm bất biến (chỉ thêm). Số dư là kết quả tổng hợp từ sổ cái.
- **Lô điểm (Points Lot):** phần điểm của một lần tích, có hạn dùng riêng. Dùng theo nguyên tắc lô sắp hết hạn trước.
- **Trạng thái lô điểm:** Chờ (pending), Khả dụng (available), Hết hạn (expired), Đã dùng hết (consumed), Đã hủy (voided).
- **Reward (Phần thưởng):** mặt hàng trong danh mục đổi điểm (ví dụ giảm 50.000đ).
- **Redemption (Lượt đổi):** một lần khách đổi điểm lấy reward, có vòng đời riêng.
- **Khóa idempotency:** khóa nhận diện duy nhất một sự kiện nghiệp vụ để không ghi sổ hai lần.
- **Sự kiện webhook:** bản ghi thô của một thông báo từ Shopify.
- **Đối soát (Reconciliation):** so khớp sổ cái với dữ liệu đơn hàng thực tế trên Shopify.

## 5. Quy tắc nghiệp vụ

### 5.1 Tích điểm

- BR-01: Điểm = làm tròn xuống (số tiền hợp lệ ÷ N) × hệ số hạng. N cấu hình được (ví dụ 1 điểm cho mỗi 10.000đ).
- BR-02 (đề xuất): Số tiền hợp lệ = tổng tiền hàng sau giảm giá, không gồm thuế, phí vận chuyển, phần trả bằng thẻ quà tặng, và dòng hàng thuộc sản phẩm loại trừ (Q-01).
- BR-03: Tích điểm khi đơn chuyển sang đã thanh toán (paid). Đơn mới ủy quyền (authorized) hoặc thanh toán một phần chưa tích.
- BR-04: Mỗi đơn chỉ tích điểm đúng một lần. Khóa duy nhất: cửa hàng + mã đơn + loại giao dịch.
- BR-05 (đề xuất): Điểm ở trạng thái Chờ trong K ngày (mặc định 7, bằng 0 là dùng ngay) để phù hợp thời hạn đổi trả (Q-02).
- BR-06: Đơn test, đơn không có Customer ID, đơn từ kênh bị loại trừ không tích điểm.

### 5.2 Thu hồi

- BR-07: Đơn bị hủy: thu hồi toàn bộ điểm đã tích của đơn.
- BR-08: Hoàn tiền (toàn phần hay một phần): tính lại điểm đáng hưởng trên phần giá trị còn lại, rồi thu hồi phần chênh lệch so với điểm đã tích trừ điểm đã thu hồi trước đó. Cách này tránh sai số làm tròn tích lũy qua nhiều lần hoàn.
- BR-09: Thu hồi ưu tiên trừ vào lô gốc của đơn. Nếu lô đã dùng hoặc hết hạn thì trừ vào số dư khả dụng hiện tại. Nếu vẫn thiếu, số dư có thể âm (Q-05) và được bù bằng điểm tích sau.
- BR-10: Đơn được chỉnh sửa giá trị (order edit): tính lại như hoàn tiền (nếu giảm) hoặc bổ sung điểm theo chênh lệch (nếu tăng).

### 5.3 Hết hạn

- BR-11 (đề xuất): Điểm hết hạn sau M tháng kể từ ngày tích, không gia hạn (Q-04). Giá trị 0 là không hết hạn.
- BR-12: Dùng điểm theo thứ tự lô sắp hết hạn trước.
- BR-13: Nhắc khách trước X ngày khi điểm sắp hết hạn (Should).

### 5.4 Đổi điểm

- BR-14: Mỗi reward có số điểm cần, giá trị giảm (cố định hoặc phần trăm), hạn dùng của mã (đề xuất 30 ngày), điều kiện áp dụng, số lượng tối đa (nếu có).
- BR-15: Khách chỉ đổi khi số dư khả dụng đủ điểm cần. Điểm Chờ không dùng được.
- BR-16: Mã giảm giá dùng một lần, gắn riêng cho khách đã đổi, không cộng dồn với mã khác (cấu hình được).
- BR-17: Trừ điểm và phát hành mã là một thao tác toàn vẹn: không để khách mất điểm mà không có mã, hoặc có mã mà không bị trừ điểm.
- BR-18 (đề xuất): Mã hết hạn chưa dùng thì hoàn điểm một lần (cấu hình được).
- BR-19 (đề xuất): Đơn dùng mã đổi điểm bị hủy toàn bộ thì hoàn điểm đã đổi nếu mã chưa quá hạn. Hoàn tiền một phần thì không hoàn điểm đổi.
- BR-20: Giới hạn chống lạm dụng: tối đa Y lượt đổi mỗi ngày mỗi khách.

### 5.5 Kiểm soát và vận hành

- BR-21: Điều chỉnh điểm thủ công bắt buộc có lý do, ghi người thực hiện, có giới hạn theo vai trò. Nhân viên (Staff) không được điều chỉnh vượt ngưỡng.
- BR-22: Sổ cái bất biến. Sửa sai bằng giao dịch bù trừ, không sửa hay xóa bản ghi cũ.
- BR-23: Thay đổi cấu hình chỉ có hiệu lực từ thời điểm lưu, không hồi tố. Mỗi giao dịch lưu bản chụp quy tắc đã áp dụng.
- BR-24: Xử lý sự kiện phải idempotent và xác nhận lại trạng thái đơn bằng API trước khi ghi sổ.
- BR-25: Mọi thao tác của người dùng quản trị đều ghi nhật ký (ai, khi nào, làm gì, giá trị trước và sau).
- BR-26: Chỉ lưu dữ liệu cá nhân tối thiểu cần thiết. Hỗ trợ xóa hoặc ẩn danh theo yêu cầu (xem UC-16).

## 6. Tổng quan use case

| Mã | Tên use case | Tác nhân chính | Ưu tiên | Giai đoạn |
| --- | --- | --- | --- | --- |
| UC-01 | Tích điểm khi đơn hàng được thanh toán | Shopify, Hệ thống | Must | MVP |
| UC-02 | Thu hồi điểm khi đơn hàng bị hủy | Shopify, Hệ thống | Must | MVP |
| UC-03 | Thu hồi điểm khi hoàn tiền | Shopify, Hệ thống | Must | MVP |
| UC-04 | Đồng bộ và đăng ký khách hàng | Shopify, Hệ thống | Must | MVP |
| UC-05 | Kích hoạt điểm chờ thành khả dụng | Hệ thống | Must | MVP |
| UC-06 | Hết hạn điểm và nhắc trước hạn | Hệ thống | Must | MVP |
| UC-07 | Tiếp nhận và xử lý webhook | Shopify, Hệ thống | Must | MVP |
| UC-08 | Đối soát sổ cái với Shopify | Hệ thống | Should | MVP |
| UC-09 | Khách xem số dư, hạng và lịch sử điểm | Shopper | Must | MVP |
| UC-10 | Khách đổi điểm lấy mã giảm giá | Shopper | Must | MVP |
| UC-11 | Cấu hình chương trình tích điểm | Merchant Admin | Must | MVP |
| UC-12 | Quản lý danh mục phần thưởng | Merchant Admin | Must | MVP |
| UC-13 | Tra cứu khách hàng và sổ cái điểm | Merchant Admin, Staff | Must | MVP |
| UC-14 | Điều chỉnh điểm thủ công | Merchant Admin | Must | MVP |
| UC-15 | Xem báo cáo tổng quan chương trình | Merchant Admin | Should | Phase 2 |
| UC-16 | Xử lý yêu cầu dữ liệu cá nhân và xóa khách | Merchant Admin, Hệ thống | Should | Phase 2 |
| UC-17 | Xử lý khi gỡ cài đặt app | Shopify, Hệ thống | Should | MVP |
| UC-18 | Chạy lại sự kiện thất bại | Vận hành | Should | MVP |
| UC-19 | Phân hạng thành viên (tier) | Hệ thống | Could | Phase 2 |
| UC-20 | Giới thiệu bạn bè | Shopper | Could | Phase 2 |
| UC-21 | Điểm sinh nhật và sự kiện | Hệ thống | Could | Phase 2 |
| UC-22 | Chặn khách và phát hiện gian lận | Merchant Admin | Could | Phase 2 |

## 7. Đặc tả chi tiết use case

Nguyên tắc chung cho mọi use case do webhook kích hoạt: sự kiện đã đi qua UC-07, trạng thái đơn được xác nhận lại bằng API (BR-24), và việc ghi sổ nằm trong một transaction có khóa idempotency (BR-04). Ký hiệu: A = luồng thay thế, E = ngoại lệ, AC = tiêu chí chấp nhận.

### UC-01 — Tích điểm khi đơn hàng được thanh toán

**Tác nhân:** Shopify (webhook orders/paid), Hệ thống · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Ghi nhận đúng số điểm cho khách, đúng một lần, khi đơn đã thanh toán.

**Kích hoạt:** Webhook orders/paid, hoặc đối soát (UC-08) phát hiện đơn đã thanh toán nhưng chưa có giao dịch EARN.

**Tiền điều kiện:** Sự kiện đã qua UC-07 (chữ ký hợp lệ, không trùng). Chương trình đang bật và cấu hình hợp lệ.

**Hậu điều kiện (thành công):** Có một giao dịch EARN và một lô điểm (Chờ hoặc Khả dụng) gắn với đơn và khách, kèm bản chụp quy tắc đã áp dụng. Số dư được cập nhật. Sự kiện được đánh dấu đã xử lý.

**Luồng chính:**

1. Tác vụ nền nhận sự kiện orders/paid.
2. Gọi API lấy lại đơn: trạng thái thanh toán, khách, dòng hàng, giảm giá, thẻ quà tặng, hoàn tiền. Không chỉ tin nội dung webhook.
3. Kiểm tra đủ điều kiện theo BR-03 và BR-06.
4. Tìm hoặc tạo hồ sơ khách loyalty (UC-04).
5. Tính số tiền hợp lệ (BR-02), tính điểm (BR-01), áp hệ số hạng nếu có.
6. Trong một transaction: khóa dòng hồ sơ khách, kiểm tra khóa idempotency (BR-04), tạo giao dịch EARN và lô điểm (Chờ hoặc Khả dụng theo BR-05), cập nhật số dư.
7. Đánh dấu sự kiện đã xử lý.

**Luồng thay thế:**

- A1 Điểm tính ra bằng 0 (đơn quá nhỏ hoặc 0đ): không tạo giao dịch. Sự kiện được đánh dấu đã xử lý kèm lý do.
- A2 Khách guest (không có Customer ID): bỏ qua, ghi lý do vào sự kiện (Q-03).
- A3 Hồ sơ khách chưa tồn tại vì customers/create chưa đến: tự tạo hồ sơ từ dữ liệu đơn, UC-04 bổ sung sau.
- A4 Đơn dùng mã giảm giá hoặc mã đổi điểm: tính trên số tiền sau giảm.
- A5 Đơn trả một phần bằng thẻ quà tặng: loại phần đó khỏi cơ sở tính (BR-02).
- A6 Đơn có sản phẩm loại trừ: loại các dòng hàng đó.
- A7 Đơn thanh toán một phần hoặc mới ủy quyền: không tích, chờ đến khi paid.
- A8 Đơn từ draft order hoặc POS: xử lý như đơn thường nếu kênh được phép.
- A9 Khách thanh toán bằng tiền tệ khác: dùng giá trị theo tiền tệ gốc của cửa hàng (A-06).
- A10 Đơn đã bị hủy hoặc hoàn toàn bộ tại thời điểm xử lý (webhook đến sai thứ tự): không tích, ghi nhận để UC-02 không thu hồi điểm chưa từng tích.
- A11 Chương trình đang tắt hoặc khách bị chặn (UC-22): bỏ qua. Không tự tích bù khi bật lại, chỉ tích bù thủ công qua UC-14.

**Ngoại lệ:**

- E1 Webhook trùng: đã chặn ở UC-07. Nếu lọt qua, khóa idempotency chặn lần hai.
- E2 Hai tiến trình xử lý cùng một đơn cùng lúc: khóa dòng khách và ràng buộc unique, một bên dừng an toàn.
- E3 API Shopify lỗi tạm thời (429, 5xx, timeout): thử lại với giãn cách tăng dần. Hết số lần thử thì đánh dấu Lỗi và hiện ở UC-18.
- E4 Token hết hạn hoặc bị từ chối (401): lấy token mới, thử lại một lần.
- E5 Dữ liệu đơn thiếu hoặc không hợp lệ: không ghi sổ, đánh dấu Lỗi, cảnh báo.
- E6 Đơn thuộc cửa hàng khác với cấu hình: từ chối và ghi nhật ký bảo mật.
- E7 Lỗi cơ sở dữ liệu giữa chừng: rollback, không để giao dịch dở dang, thử lại.
- E8 Cấu hình đổi giữa lúc nhận và lúc xử lý: dùng cấu hình có hiệu lực tại thời điểm đơn được thanh toán (BR-23).

**Quy tắc liên quan:** BR-01 đến BR-06, BR-23, BR-24.

**Tiêu chí chấp nhận:**

- AC-01.1 Đơn 100.000đ, tỷ lệ 1 điểm cho 10.000đ: tạo đúng 10 điểm, một giao dịch, một lô.
- AC-01.2 Đơn 95.000đ: 9 điểm (làm tròn xuống).
- AC-01.3 Cùng một webhook gửi hai lần: vẫn chỉ một giao dịch.
- AC-01.4 Đơn guest: không có giao dịch, sự kiện ghi lý do.
- AC-01.5 Đơn có phần trả bằng thẻ quà tặng: điểm chỉ tính trên phần còn lại.
- AC-01.6 Tổng sổ cái luôn bằng số dư hiển thị.

### UC-02 — Thu hồi điểm khi đơn hàng bị hủy

**Tác nhân:** Shopify (webhook orders/cancelled), Hệ thống · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Thu hồi đúng điểm của đơn bị hủy, không thu hồi quá và không thu hồi hai lần.

**Kích hoạt:** Webhook orders/cancelled, hoặc đối soát (UC-08).

**Tiền điều kiện:** Sự kiện đã qua UC-07.

**Hậu điều kiện:** Điểm của đơn được thu hồi toàn bộ (hoặc ghi nhận là không có gì để thu hồi). Đơn được đánh dấu đã hủy trong hệ thống để chặn việc tích điểm đến muộn.

**Luồng chính:**

1. Lấy lại đơn bằng API, xác nhận trạng thái hủy.
2. Tìm giao dịch EARN của đơn.
3. Nếu có và chưa thu hồi: tính điểm cần thu hồi (BR-07, BR-09).
4. Trong một transaction: tạo giao dịch REVOKE, trừ vào lô gốc, cập nhật số dư. Khóa idempotency theo mã đơn và loại REVOKE.
5. Nếu đơn có dùng mã đổi điểm: xử lý hoàn điểm theo BR-19 (UC-10).
6. Đánh dấu sự kiện đã xử lý.

**Luồng thay thế:**

- A1 Chưa có EARN (hủy trước khi thanh toán): không có gì để thu hồi. Đánh dấu đơn đã hủy để UC-01 bỏ qua nếu orders/paid đến muộn.
- A2 Điểm đang ở trạng thái Chờ: hủy lô chờ (voided), không bao giờ vào Khả dụng.
- A3 Điểm Khả dụng chưa dùng: thu hồi trực tiếp từ lô gốc.
- A4 Khách đã tiêu một phần hoặc toàn bộ điểm của đơn: trừ phần thiếu vào số dư khả dụng khác (BR-09). Nếu vẫn thiếu: số dư âm hoặc xử lý thủ công theo Q-05, đồng thời gắn cờ cho merchant.
- A5 Điểm đã hết hạn: không thu hồi phần đã hết hạn, ghi nhận trong giao dịch.
- A6 Đơn hủy kèm hoàn tiền: chỉ thu hồi một lần. Webhook hoàn tiền đến sau sẽ tính ra điểm đáng hưởng bằng 0 nên không thu hồi thêm (UC-03).
- A7 Đơn hủy rồi khách đặt lại: đơn mới tích như đơn mới, không liên quan đơn cũ.

**Ngoại lệ:**

- E1 Sự kiện trùng hoặc đơn đã được thu hồi: bỏ qua và ghi nhận.
- E2 orders/cancelled đến trước orders/paid: xử lý theo A1. Nếu hai sự kiện chạy cùng lúc, khóa dòng khách và trạng thái đơn quyết định thứ tự.
- E3 Không tìm thấy đơn trên Shopify: đánh dấu Lỗi và cảnh báo.
- E4 Lỗi API hoặc cơ sở dữ liệu: thử lại như UC-01 (E3, E4, E7).
- E5 Số dư âm vượt ngưỡng cảnh báo: gửi cảnh báo cho merchant.

**Quy tắc liên quan:** BR-04, BR-07, BR-09, BR-19, BR-22, BR-24.

**Tiêu chí chấp nhận:**

- AC-02.1 Đơn đã tích 10 điểm, khách chưa dùng, bị hủy: số dư giảm đúng 10, một giao dịch REVOKE.
- AC-02.2 Webhook hủy gửi hai lần: chỉ một REVOKE.
- AC-02.3 Hủy khi điểm còn Chờ: lô chuyển sang voided, số dư khả dụng không đổi.
- AC-02.4 Hủy khi khách đã tiêu hết số điểm đó: hệ thống xử lý theo quyết định Q-05 và có cảnh báo.
- AC-02.5 orders/paid đến sau orders/cancelled: không tích điểm.

### UC-03 — Thu hồi điểm khi hoàn tiền

**Tác nhân:** Shopify (webhook refunds/create), Hệ thống · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Thu hồi điểm tương ứng phần giá trị đã hoàn, vẫn chính xác sau nhiều lần hoàn một phần.

**Kích hoạt:** Webhook refunds/create, hoặc đối soát (UC-08).

**Tiền điều kiện:** Sự kiện đã qua UC-07. Đơn đã có EARN (nếu chưa, xem A5).

**Hậu điều kiện:** Tổng điểm đã thu hồi của đơn bằng điểm đã tích trừ điểm đáng hưởng trên phần giá trị còn lại.

**Luồng chính:**

1. Lấy lại đơn và toàn bộ các lần hoàn tiền từ API (không chỉ lần hoàn hiện tại).
2. Tính số tiền hợp lệ còn lại = số tiền hợp lệ ban đầu trừ phần hoàn của các dòng hàng hợp lệ (BR-02, BR-08).
3. Tính điểm đáng hưởng mới theo BR-01 trên số tiền còn lại.
4. Điểm cần thu hồi = điểm đã tích trừ điểm đáng hưởng mới trừ điểm đã thu hồi trước đó.
5. Nếu lớn hơn 0: tạo giao dịch REVOKE theo BR-09, khóa idempotency theo mã hoàn tiền.
6. Đánh dấu sự kiện đã xử lý.

**Luồng thay thế:**

- A1 Hoàn một phần nhiều lần: mỗi lần tính lại theo bước 2 đến 4, tổng thu hồi không bao giờ vượt điểm đã tích.
- A2 Chỉ hoàn phí vận chuyển hoặc thuế: số tiền hợp lệ không đổi, không thu hồi.
- A3 Hoàn sản phẩm thuộc diện loại trừ: không thu hồi.
- A4 Hoàn toàn bộ đơn: tương đương hủy, thu hồi hết (BR-07).
- A5 Hoàn tiền khi chưa có EARN (webhook sai thứ tự): bỏ qua. Khi orders/paid được xử lý, UC-01 tính theo trạng thái hiện tại đã bao gồm các lần hoàn.
- A6 Hoàn vào thẻ quà tặng hoặc store credit: vẫn thu hồi như hoàn tiền thường.
- A7 Hoàn có hoặc không nhập lại kho: không ảnh hưởng điểm.
- A8 Điểm gốc đã tiêu hoặc đã hết hạn: theo BR-09 và UC-02 (A4, A5).
- A9 Đơn dùng mã đổi điểm và chỉ hoàn một phần: không hoàn điểm đã đổi (BR-19).

**Ngoại lệ:**

- E1 Sự kiện trùng: khóa theo mã hoàn tiền chặn lại.
- E2 Hai lần hoàn đến sát nhau và xử lý đồng thời: khóa dòng khách, lần sau tính lại trên dữ liệu đầy đủ nên kết quả vẫn đúng.
- E3 Tranh chấp thanh toán (chargeback): MVP chưa tự xử lý. Merchant dùng UC-14 hoặc phát hiện qua UC-08. Nên bổ sung ở Phase 2 (xử lý như hoàn toàn bộ).
- E4 Lỗi API hoặc cơ sở dữ liệu: thử lại như UC-01.

**Quy tắc liên quan:** BR-02, BR-08, BR-09, BR-19, BR-24.

**Tiêu chí chấp nhận:**

- AC-03.1 Đơn 100.000đ (10 điểm), hoàn 50.000đ: thu hồi 5 điểm.
- AC-03.2 Hoàn tiếp 50.000đ: thu hồi thêm 5, tổng 10, không hơn.
- AC-03.3 Chỉ hoàn phí vận chuyển: không phát sinh giao dịch.
- AC-03.4 Hoàn 33.000đ trên đơn 100.000đ: điểm đáng hưởng là 6 (67.000đ chia 10.000 làm tròn xuống), thu hồi 4.
- AC-03.5 Hoàn tiền đến trước orders/paid: không thu hồi. Sau khi paid được xử lý, điểm tích theo giá trị đã trừ phần hoàn.

### UC-04 — Đồng bộ và đăng ký khách hàng

**Tác nhân:** Shopify (customers/create, customers/update), Hệ thống, Merchant Admin (đồng bộ ban đầu) · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Mọi khách hợp lệ có đúng một hồ sơ loyalty gắn với Customer ID, thông tin cơ bản luôn khớp Shopify.

**Kích hoạt:** Webhook khách tạo hoặc cập nhật. UC-01 gặp khách chưa có hồ sơ. Merchant khởi chạy đồng bộ ban đầu.

**Tiền điều kiện:** Token hợp lệ, đã được cấp quyền đọc khách hàng.

**Hậu điều kiện:** Mỗi Customer ID có một hồ sơ (số dư 0 nếu mới). Tên, email, trạng thái tài khoản khớp Shopify.

**Luồng chính (sự kiện đơn lẻ):**

1. Nhận sự kiện khách tạo hoặc cập nhật (qua UC-07).
2. Tìm hồ sơ theo Customer ID.
3. Chưa có thì tạo hồ sơ số dư 0. Đã có thì cập nhật phần thay đổi.
4. Nếu chương trình có điểm chào mừng (Could), tạo EARN với khóa idempotency theo Customer ID.

**Luồng chính (đồng bộ ban đầu cho khách cũ):**

1. Merchant Admin khởi chạy. Hệ thống đọc khách theo trang hoặc bulk operation, tạo hồ sơ còn thiếu, có nghỉ giữa các lô theo giới hạn tốc độ (R-02).
2. Có thể chạy lại nhiều lần mà không tạo trùng. Hiển thị tiến độ và kết quả.
3. Nhập điểm lịch sử chỉ làm khi merchant yêu cầu và bị giới hạn bởi R-03. Mỗi đơn cũ đi qua UC-01 để dùng chung quy tắc và chống trùng.

**Luồng thay thế:**

- A1 Khách đổi email hoặc tên: cập nhật hồ sơ, không ảnh hưởng điểm.
- A2 Khách chưa kích hoạt tài khoản: vẫn tạo hồ sơ và tích điểm bình thường (đề xuất).
- A3 Hai khách bị gộp trong Shopify (merge): Customer ID cũ không còn. Hệ thống phát hiện qua cập nhật hoặc đối soát, hợp nhất hồ sơ: cộng gộp số dư, giữ nguyên lịch sử, ghi giao dịch ADJUST nêu rõ nguồn (Should).
- A4 Khách bị xóa trên Shopify: không xóa sổ cái, chuyển hồ sơ sang ẩn danh theo UC-16.
- A5 Khách guest trùng email với khách có tài khoản: không tự gộp ở MVP (Q-03).

**Ngoại lệ:**

- E1 customers/update đến trước customers/create: xử lý như tạo mới.
- E2 Hai sự kiện tạo cùng lúc: ràng buộc unique theo Customer ID, bên thua đọc lại bản ghi vừa tạo.
- E3 Đồng bộ ban đầu bị gián đoạn: lưu con trỏ trang cuối, chạy tiếp từ đó.
- E4 API bị giới hạn tốc độ: giãn cách tăng dần rồi tiếp tục.
- E5 Khách thiếu email hoặc dữ liệu phụ: vẫn tạo hồ sơ theo Customer ID.

**Quy tắc liên quan:** BR-04, BR-06, BR-26.

**Tiêu chí chấp nhận:**

- AC-04.1 Tạo khách mới trên Shopify: trong vòng 1 phút có hồ sơ số dư 0.
- AC-04.2 Chạy đồng bộ ban đầu hai lần liên tiếp: số hồ sơ không đổi ở lần hai.
- AC-04.3 Đổi email trên Shopify: hồ sơ cập nhật, điểm giữ nguyên.

### UC-05 — Kích hoạt điểm chờ thành khả dụng

**Tác nhân:** Hệ thống (Scheduler) · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Chuyển lô điểm Chờ sang Khả dụng đúng thời điểm, chỉ khi đơn gốc vẫn hợp lệ.

**Kích hoạt:** Chạy định kỳ (đề xuất mỗi giờ).

**Tiền điều kiện:** Có lô Chờ đã đến ngày kích hoạt.

**Hậu điều kiện:** Lô đủ điều kiện chuyển Khả dụng, số dư khả dụng tăng tương ứng. Hạn hết hạn của lô vẫn tính từ ngày tích (BR-11).

**Luồng chính:**

1. Chọn các lô Chờ có ngày kích hoạt nhỏ hơn hoặc bằng hiện tại (tính theo UTC).
2. Với từng lô, trong một transaction: khóa dòng khách, kiểm tra lô còn ở trạng thái Chờ và đơn gốc chưa hủy hoặc hoàn toàn bộ.
3. Chuyển trạng thái sang Khả dụng, cập nhật số dư khả dụng, ghi sự kiện chuyển trạng thái vào nhật ký lô (không làm đổi tổng điểm).

**Luồng thay thế:**

- A1 Đơn gốc đã hoàn một phần: lô đã được UC-03 giảm tương ứng, chỉ kích hoạt phần còn lại.
- A2 Đơn gốc đã hủy hoặc hoàn toàn bộ: lô đã ở trạng thái voided, bỏ qua.
- A3 K bằng 0: lô được tạo ra đã Khả dụng, UC này không xử lý.
- A4 Merchant đổi K sau khi lô được tạo: lô cũ giữ ngày kích hoạt đã tính, chỉ lô mới theo K mới (BR-23).

**Ngoại lệ:**

- E1 Tác vụ chạy trùng hoặc hai bản chạy song song: kiểm tra trạng thái trong transaction, lô đã chuyển thì bỏ qua.
- E2 Máy chủ tắt nhiều giờ: lần chạy kế tiếp xử lý bù mọi lô quá hạn, không bỏ sót.
- E3 Lỗi ở một khách: ghi lỗi, tiếp tục các khách khác, thử lại ở lần chạy sau.

**Quy tắc liên quan:** BR-05, BR-11, BR-23.

**Tiêu chí chấp nhận:**

- AC-05.1 Lô Chờ đến hạn được chuyển Khả dụng ở lần chạy gần nhất.
- AC-05.2 Chạy hai lần: số dư không tăng hai lần.
- AC-05.3 Lô của đơn đã hủy không bao giờ trở thành Khả dụng.

### UC-06 — Hết hạn điểm và nhắc trước hạn

**Tác nhân:** Hệ thống (Scheduler) · **Ưu tiên:** Must (hết hạn), Should (nhắc trước hạn) · **Giai đoạn:** MVP

**Mục tiêu:** Hết hạn đúng lô, đúng ngày, không làm mất điểm khách đang dùng.

**Kích hoạt:** Chạy định kỳ mỗi ngày vào giờ ít tải.

**Tiền điều kiện:** Chương trình bật hết hạn (M lớn hơn 0).

**Hậu điều kiện:** Phần điểm còn lại của lô quá hạn bị trừ bằng giao dịch EXPIRE, lô chuyển sang expired, số dư cập nhật.

**Luồng chính:**

1. Chọn các lô Khả dụng có ngày hết hạn nhỏ hơn hoặc bằng hiện tại và còn điểm.
2. Với từng khách, trong một transaction: khóa dòng khách, đọc lại phần còn lại của lô.
3. Tạo giao dịch EXPIRE bằng phần còn lại, khóa idempotency theo mã lô và loại EXPIRE. Chuyển lô sang expired, cập nhật số dư.
4. Nhắc trước hạn (Should): tìm lô sẽ hết hạn trong X ngày tới, tạo thông báo (email hoặc hiển thị trong widget). Mỗi lô chỉ nhắc một lần cho mỗi mốc.

**Luồng thay thế:**

- A1 Lô đã dùng một phần: chỉ hết hạn phần còn lại.
- A2 Lô đã dùng hết: bỏ qua.
- A3 Merchant đổi M: chỉ áp dụng cho lô tạo sau đó, lô cũ giữ hạn đã tính (BR-23).
- A4 Lần đầu bật hết hạn khi đã có điểm cũ: không hết hạn hồi tố. Lô cũ nhận hạn do merchant chọn khi bật (ví dụ M tháng kể từ ngày bật).
- A5 Khách đã bị ẩn danh hoặc đã xóa khỏi Shopify: lô vẫn hết hạn bình thường.

**Ngoại lệ:**

- E1 Khách đang đổi điểm đúng lúc lô hết hạn: khóa dòng khách, giao dịch nào giành khóa trước thì thắng, bên sau đọc lại số dư thật.
- E2 Tác vụ chạy trùng: khóa idempotency chặn EXPIRE lần hai.
- E3 Chạy lỗi giữa chừng: xử lý độc lập từng khách, lần chạy sau tiếp tục phần còn lại.
- E4 Múi giờ: tính theo UTC, hạn có hiệu lực hết ngày đó. Hiển thị cho khách theo múi giờ cửa hàng.
- E5 Lỗi khi gửi thông báo nhắc: không ảnh hưởng việc hết hạn, ghi lỗi và thử lại.

**Quy tắc liên quan:** BR-11, BR-12, BR-13, BR-23.

**Tiêu chí chấp nhận:**

- AC-06.1 Lô 10 điểm tích ngày D, M bằng 12 tháng: sau hạn, 10 điểm hết hạn bằng một giao dịch EXPIRE 10.
- AC-06.2 Lô 10 điểm đã dùng 4: hết hạn 6.
- AC-06.3 Chạy hai lần: chỉ một giao dịch EXPIRE.
- AC-06.4 Điểm sắp hết hạn luôn được dùng trước điểm mới (BR-12).
- AC-06.5 Thông báo nhắc gửi đúng một lần cho mỗi mốc.

### UC-07 — Tiếp nhận và xử lý webhook

**Tác nhân:** Shopify, Hệ thống, Vận hành · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Mọi webhook hợp lệ được lưu bền vững và xử lý đúng một lần về mặt nghiệp vụ, kể cả khi trùng, sai thứ tự hoặc gặp lỗi.

**Kích hoạt:** Shopify gửi HTTP POST tới điểm nhận webhook.

**Tiền điều kiện:** Webhook đã đăng ký với địa chỉ công khai HTTPS. Client secret đã được cấu hình.

**Hậu điều kiện:** Sự kiện hợp lệ được lưu thô, đưa vào hàng đợi và trả 200. Sự kiện không hợp lệ bị từ chối, không để lại tác động nghiệp vụ.

**Luồng chính:**

1. Nhận request, đọc raw body (chưa parse).
2. Tính HMAC-SHA256 trên raw body bằng client secret, so sánh an toàn với header chữ ký. Sai thì trả 403.
3. Đọc header: topic, cửa hàng, webhook ID, event ID. Thiếu webhook ID thì trả 400.
4. Kiểm tra cửa hàng khớp cấu hình.
5. Lưu sự kiện thô với webhook ID duy nhất. Nếu đã tồn tại thì trả 200 và không xử lý lại.
6. Đưa tác vụ xử lý vào hàng đợi và trả 200 ngay.
7. Tác vụ nền định tuyến theo topic tới UC-01, 02, 03, 04, 17. Đánh dấu Đã xử lý hoặc Lỗi.

**Luồng thay thế:**

- A1 Webhook trùng (Shopify gửi lại hoặc lỗi mạng): trả 200, không xử lý lại.
- A2 Topic chưa hỗ trợ: lưu và đánh dấu bỏ qua, không coi là lỗi.
- A3 Cùng một sự kiện gốc giao qua nhiều lần với webhook ID khác nhau: khóa idempotency nghiệp vụ của từng use case vẫn chặn trùng.
- A4 Sự kiện đến sai thứ tự: tác vụ xác nhận trạng thái hiện tại bằng API (BR-24), không dựa vào thứ tự đến.
- A5 Hàng đợi chậm: sự kiện ở trạng thái Đã nhận cho tới khi worker rảnh. Cảnh báo khi tồn đọng quá ngưỡng.

**Ngoại lệ:**

- E1 Chữ ký sai hoặc thiếu: trả 403, ghi nhật ký bảo mật, không lưu payload.
- E2 Payload không phải JSON hợp lệ: trả 400.
- E3 Lỗi ghi cơ sở dữ liệu: trả 5xx để Shopify gửi lại.
- E4 Lưu xong nhưng đưa vào hàng đợi thất bại: sự kiện vẫn ở trạng thái Đã nhận, một tác vụ quét định kỳ đưa lại vào hàng đợi.
- E5 Xử lý nền thất bại: thử lại với giãn cách tăng dần. Quá số lần cho phép thì chuyển sang Lỗi, hiện ở UC-18 và cảnh báo vận hành.
- E6 Shopify không nhận được phản hồi thành công nhiều lần liên tiếp: có thể ngừng gửi hoặc gỡ đăng ký. Cần kiểm tra tình trạng đăng ký webhook định kỳ và đăng ký lại khi thiếu (Should).
- E7 Địa chỉ nhận thay đổi (ví dụ đổi địa chỉ tunnel khi phát triển): đăng ký phải cập nhật theo. Môi trường thật dùng địa chỉ cố định.
- E8 Giả mạo hoặc phát lại request: chữ ký sai bị chặn, request hợp lệ lặp lại bị chặn bằng webhook ID. Giới hạn kích thước body.

**Quy tắc liên quan:** BR-04, BR-24.

**Tiêu chí chấp nhận:**

- AC-07.1 Webhook hợp lệ: trả 200 trong dưới 1 giây (đề xuất) và có đúng một bản ghi sự kiện.
- AC-07.2 Gửi lại cùng webhook ID: vẫn một bản ghi, trả 200.
- AC-07.3 Chữ ký sai: trả 403, không có bản ghi.
- AC-07.4 Worker tắt rồi bật lại: mọi sự kiện tồn đọng được xử lý hết.
- AC-07.5 Sự kiện lỗi quá số lần cho phép: ở trạng thái Lỗi và xuất hiện trong UC-18.

### UC-08 — Đối soát sổ cái với Shopify

**Tác nhân:** Hệ thống, Merchant Admin · **Ưu tiên:** Should · **Giai đoạn:** MVP

**Mục tiêu:** Phát hiện và (tùy cấu hình) sửa các sai lệch do webhook bị mất, trễ hoặc lỗi xử lý.

**Kích hoạt:** Chạy hằng đêm, hoặc merchant bấm chạy thủ công.

**Tiền điều kiện:** Token hợp lệ.

**Hậu điều kiện:** Có báo cáo đối soát. Các sai lệch tự sửa được đã được sửa, phần còn lại được liệt kê để xử lý.

**Luồng chính:**

1. Xác định khoảng thời gian đối soát (đề xuất 7 ngày gần nhất, trong giới hạn R-03).
2. Lấy từ API danh sách đơn đã thanh toán, đã hủy và các lần hoàn tiền trong khoảng đó.
3. So khớp với sổ cái: đơn đã paid thiếu EARN, đơn đã hủy hoặc hoàn còn điểm chưa thu hồi, EARN của đơn không tồn tại, số điểm lệch so với công thức.
4. Kiểm tra toàn vẹn nội bộ: số dư của từng khách bằng tổng sổ cái, tổng các lô còn lại bằng số dư.
5. Chế độ báo cáo: chỉ liệt kê. Chế độ tự sửa: phát lại qua UC-01, 02, 03 (idempotent) để tạo giao dịch còn thiếu.
6. Lưu kết quả, cảnh báo nếu số sai lệch vượt ngưỡng.

**Luồng thay thế:**

- A1 Đơn vừa phát sinh trong vài phút gần đây: bỏ qua ở lượt này để tránh báo sai khi webhook còn trong hàng đợi.
- A2 Sai lệch do cấu hình đã đổi: so với bản chụp quy tắc của giao dịch, không so với cấu hình hiện tại.
- A3 Điều chỉnh thủ công (UC-14): không coi là sai lệch.
- A4 Đơn quá cũ vượt giới hạn đọc của API: ghi chú không thể đối soát.

**Ngoại lệ:**

- E1 API giới hạn tốc độ hoặc lỗi: dừng, lưu con trỏ, tiếp tục ở lần sau.
- E2 Số dư lệch tổng sổ cái: lỗi nghiêm trọng, cảnh báo ngay, không tự sửa số dư. Chỉ sửa bằng giao dịch bù trừ (BR-22).
- E3 Hai lượt đối soát chạy chồng nhau: khóa tác vụ, lượt sau bỏ qua.

**Quy tắc liên quan:** BR-22, BR-24.

**Tiêu chí chấp nhận:**

- AC-08.1 Cố ý bỏ sót một webhook paid: đối soát tạo EARN còn thiếu (hoặc liệt kê, tùy chế độ).
- AC-08.2 Chạy hai lần liên tiếp: không tạo trùng.
- AC-08.3 Phát hiện số dư lệch tổng sổ cái: cảnh báo.

### UC-09 — Khách xem số dư, hạng và lịch sử điểm

**Tác nhân:** Shopper · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Khách thấy chính xác số điểm, điểm đang chờ, điểm sắp hết hạn, lịch sử, và cách kiếm hoặc đổi điểm.

**Kích hoạt:** Khách mở trang tài khoản hoặc widget trên storefront.

**Tiền điều kiện:** Khách đã đăng nhập. Yêu cầu đến qua kênh đã xác thực (App Proxy có chữ ký của Shopify).

**Hậu điều kiện:** Hiển thị dữ liệu của chính khách, không thay đổi dữ liệu.

**Luồng chính:**

1. Widget gọi API qua App Proxy. Hệ thống kiểm tra chữ ký của Shopify và lấy Customer ID đã đăng nhập từ yêu cầu được ký, không lấy từ tham số do trình duyệt tự gửi.
2. Đọc hồ sơ: số dư khả dụng, điểm Chờ (kèm ngày kích hoạt), điểm sắp hết hạn, hạng nếu có.
3. Đọc lịch sử giao dịch phân trang (mới nhất trước), mô tả dễ hiểu (ví dụ: Đơn #1001, cộng 10 điểm).
4. Hiển thị các reward có thể đổi và số điểm còn thiếu để đạt reward gần nhất.

**Luồng thay thế:**

- A1 Chưa đăng nhập: hiển thị lời mời đăng nhập hoặc đăng ký, không hiển thị dữ liệu.
- A2 Khách chưa có hồ sơ (vừa đăng ký): hiển thị 0 điểm và hướng dẫn cách kiếm điểm.
- A3 Số dư âm (BR-09): khách thấy 0 kèm thông báo đã điều chỉnh sau hoàn tiền. Số âm chỉ merchant nhìn thấy.
- A4 Chương trình đang tắt: thông báo tạm ngưng, vẫn cho xem lịch sử.
- A5 Lịch sử dài: phân trang, giới hạn số bản ghi mỗi trang.

**Ngoại lệ:**

- E1 Chữ ký App Proxy sai: từ chối 403.
- E2 Khách thử xem dữ liệu người khác bằng cách đổi ID: không thể, vì ID lấy từ yêu cầu đã ký. Mọi tham số ID do trình duyệt gửi đều bị bỏ qua.
- E3 Backend lỗi hoặc chậm: widget hiển thị thông báo thân thiện, không làm vỡ trang mua hàng.
- E4 Dữ liệu trễ do sự kiện còn trong hàng đợi: hiển thị số liệu mới nhất đã ghi sổ, có thể kèm ghi chú điểm cho đơn mới đang được cập nhật.
- E5 Gọi liên tục quá mức: giới hạn tần suất theo khách và địa chỉ IP.

**Quy tắc liên quan:** BR-05, BR-09, BR-11.

**Tiêu chí chấp nhận:**

- AC-09.1 Số dư hiển thị bằng số dư trong sổ cái.
- AC-09.2 Chưa đăng nhập: không thấy dữ liệu nào.
- AC-09.3 Khách A không xem được dữ liệu của khách B.
- AC-09.4 Điểm Chờ hiển thị riêng kèm ngày kích hoạt.
- AC-09.5 Backend chậm không làm chậm hay chặn việc thanh toán.

### UC-10 — Khách đổi điểm lấy mã giảm giá

**Tác nhân:** Shopper, Shopify (Admin API tạo mã), Hệ thống · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Khách đổi điểm lấy một mã giảm giá dùng được ngay. Không bao giờ để khách mất điểm mà không có mã, hoặc có mã mà không bị trừ điểm (BR-17).

**Kích hoạt:** Khách chọn một reward và xác nhận đổi.

**Tiền điều kiện:** Khách đã đăng nhập (kênh đã ký). Reward đang bật, còn hiệu lực, còn số lượng. Chương trình đang bật.

**Hậu điều kiện (thành công):** Redemption ở trạng thái ISSUED. Giao dịch REDEEM đã trừ điểm. Mã giảm giá tồn tại trên Shopify, gắn riêng cho khách, có hạn dùng. Khách nhìn thấy mã.

**Vòng đời Redemption:** PENDING\_CODE (đã trừ điểm, đang tạo mã) → ISSUED (mã sẵn sàng) → USED (đã dùng trong đơn) hoặc EXPIRED (quá hạn chưa dùng) hoặc CANCELLED (bị hủy). Nhánh lỗi: FAILED (tạo mã thất bại, đã hoàn điểm).

**Luồng chính:**

1. Khách chọn reward và xác nhận. Giao diện gửi kèm một khóa idempotency sinh ngẫu nhiên cho lần bấm đó.
2. Hệ thống xác thực khách từ yêu cầu đã ký, kiểm tra reward và giới hạn đổi trong ngày (BR-20).
3. Giai đoạn 1, trong một transaction cơ sở dữ liệu: khóa dòng khách, kiểm tra số dư khả dụng đủ (BR-15), tạo Redemption PENDING\_CODE, tạo giao dịch REDEEM trừ điểm theo lô sắp hết hạn trước (BR-12). Khóa idempotency chặn bấm đúp.
4. Giai đoạn 2: gọi Admin API tạo mã giảm giá. Mã sinh từ mã lượt đổi (duy nhất, khó đoán), gắn riêng khách, dùng một lần, giá trị và hạn theo reward (BR-14, BR-16).
5. Giai đoạn 3: thành công thì cập nhật Redemption sang ISSUED, lưu mã, ID mã trên Shopify và hạn dùng.
6. Hiển thị mã cho khách kèm nút sao chép và hướng dẫn áp mã khi thanh toán. Gửi email nếu được cấu hình.

**Luồng thay thế:**

- A1 Không đủ điểm: từ chối trước giai đoạn 1, báo số điểm còn thiếu.
- A2 Tổng điểm đủ nhưng một phần đang ở trạng thái Chờ: chỉ tính điểm Khả dụng (BR-15) và báo rõ cho khách.
- A3 Reward giảm theo phần trăm: tạo mã giảm phần trăm, có trần giảm tối đa nếu được cấu hình.
- A4 Reward có điều kiện (đơn tối thiểu, giới hạn sản phẩm hoặc bộ sưu tập): điều kiện được cấu hình ngay trên mã trong Shopify.
- A5 Khách đã có mã chưa dùng của cùng reward: cho phép nhiều mã nhưng giới hạn số mã chưa dùng mỗi khách (BR-20), cấu hình được.
- A6 Merchant đổi giá điểm của reward lúc khách đang xem: dùng giá tại thời điểm xác nhận ở giai đoạn 1. Nếu khác với giá khách đã thấy thì yêu cầu khách xác nhận lại.
- A7 Reward hết số lượng hoặc bị tắt giữa chừng: từ chối ở bước 2 hoặc 3, không trừ điểm.
- A8 Khách mở nhiều tab đổi cùng lúc: khóa dòng khách xếp các yêu cầu tuần tự, yêu cầu sau kiểm tra lại số dư thực.

**Ngoại lệ (đảm bảo toàn vẹn):**

- E1 Tạo mã thất bại rõ ràng (API trả lỗi): bù trừ ngay. Tạo giao dịch REDEEM\_REFUND hoàn đúng số điểm về đúng các lô cũ, Redemption sang FAILED, khách thấy thông báo thử lại.
- E2 Tạo mã bị timeout hoặc không rõ kết quả: không hoàn điểm vội. Truy vấn Shopify theo mã đã sinh. Mã tồn tại thì hoàn tất thành ISSUED. Mã chưa có thì thử tạo lại với cùng mã. Hết số lần thử tối đa mới hoàn điểm và chuyển FAILED. Cách này tránh tình huống vừa hoàn điểm vừa còn mã dùng được.
- E3 Máy chủ sập giữa giai đoạn 1 và 2: Redemption kẹt ở PENDING\_CODE. Tác vụ quét định kỳ (mỗi vài phút) xử lý các lượt kẹt quá X phút theo đúng logic của E2.
- E4 Mã bị trùng trên Shopify: sinh mã khác và thử lại, giữ nguyên Redemption.
- E5 Bị giới hạn tốc độ API: xếp hàng thử lại, khách thấy trạng thái đang xử lý.
- E6 Điểm bị thu hồi (UC-02, UC-03) giữa giai đoạn 1 và 2: không ảnh hưởng lượt đổi vì điểm đã trừ ở giai đoạn 1. Số dư sau đó có thể âm theo BR-09.
- E7 Khách bấm đúp hoặc tải lại trang: cùng khóa idempotency trả về kết quả cũ, không tạo lượt thứ hai.
- E8 Lạm dụng (đổi hàng loạt, nhiều tài khoản): giới hạn theo BR-20, cảnh báo merchant, có thể chặn khách (UC-22).

**Sau khi phát hành mã:**

- Mã được dùng: khi nhận orders/paid có mã này, Redemption chuyển USED và gắn mã đơn. Mã gắn riêng cho khách nên người khác dùng sẽ bị Shopify từ chối.
- Mã hết hạn chưa dùng: tác vụ định kỳ chuyển EXPIRED. Nếu BR-18 bật thì tạo REDEEM\_REFUND đúng một lần.
- Đơn dùng mã bị hủy toàn bộ: hoàn điểm đã đổi nếu mã chưa quá hạn (BR-19), Redemption sang CANCELLED. Hoàn tiền một phần thì không hoàn điểm.
- Merchant xóa hoặc vô hiệu mã trực tiếp trên Shopify: phát hiện qua đối soát (UC-08), Redemption sang CANCELLED và hoàn điểm theo chính sách (Should).
- Khách muốn hủy mã đã đổi: chưa hỗ trợ ở MVP (Q-07).

**Quy tắc liên quan:** BR-12, BR-14 đến BR-20, BR-22, BR-24.

**Tiêu chí chấp nhận:**

- AC-10.1 Đủ điểm: có đúng một giao dịch REDEEM, một Redemption ISSUED, mã tồn tại trên Shopify gắn đúng khách, số dư giảm đúng.
- AC-10.2 Bấm đúp cùng khóa idempotency: một Redemption, một mã.
- AC-10.3 API tạo mã trả lỗi: điểm được hoàn đủ, Redemption FAILED, khách không mất điểm.
- AC-10.4 Giả lập timeout nhưng mã vẫn được tạo: sau xử lý còn đúng một mã và điểm chỉ trừ một lần.
- AC-10.5 Hai yêu cầu song song khi chỉ đủ điểm cho một lần đổi: đúng một yêu cầu thành công.
- AC-10.6 Mã hết hạn chưa dùng (BR-18 bật): hoàn điểm đúng một lần.
- AC-10.7 Điểm Chờ không dùng được để đổi.

### UC-11 — Cấu hình chương trình tích điểm

**Tác nhân:** Merchant Admin · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Chủ shop tự cấu hình quy tắc tích điểm và vận hành mà không cần lập trình, an toàn và có kiểm soát.

**Kích hoạt:** Admin mở trang cấu hình.

**Tiền điều kiện:** Admin đã đăng nhập và có quyền cấu hình.

**Hậu điều kiện:** Cấu hình mới được lưu thành một phiên bản có hiệu lực từ thời điểm lưu. Nhật ký ghi lại thay đổi.

**Tham số cấu hình:** bật hoặc tắt chương trình; tỷ lệ tích điểm (BR-01); cơ sở tính, gồm tính hay không thuế và phí vận chuyển, danh sách sản phẩm, bộ sưu tập, kênh bán bị loại trừ (BR-02, BR-06); thời gian chờ K (BR-05); hạn điểm M và mốc nhắc X (BR-11, BR-13); giới hạn đổi mỗi ngày Y (BR-20); điểm chào mừng; chính sách hoàn điểm khi mã hết hạn và khi đơn bị hủy (BR-18, BR-19); cho phép số dư âm và ngưỡng cảnh báo (Q-05).

**Luồng chính:**

1. Admin mở trang, thấy cấu hình hiện hành kèm ví dụ minh họa (đơn 100.000đ được bao nhiêu điểm).
2. Chỉnh sửa. Hệ thống kiểm tra hợp lệ ngay (số dương, K không âm, M không âm...).
3. Xem trước tác động bằng ví dụ tính lại.
4. Lưu: tạo phiên bản cấu hình mới có hiệu lực từ lúc lưu (BR-23), ghi nhật ký (BR-25).

**Luồng thay thế:**

- A1 Giá trị không hợp lệ: báo lỗi theo từng trường, không lưu.
- A2 Hai admin sửa cùng lúc: khóa lạc quan theo số phiên bản. Người lưu sau được báo xung đột và xem thay đổi của người trước.
- A3 Tắt chương trình: không tích điểm mới, sự kiện đến trong lúc tắt bị bỏ qua (UC-01 A11). Việc xem và đổi điểm theo cấu hình.
- A4 Giảm mạnh tỷ lệ hoặc rút ngắn hạn điểm: cảnh báo tác động và yêu cầu xác nhận lần hai. Không áp dụng hồi tố.
- A5 Khôi phục phiên bản cũ: tạo phiên bản mới có nội dung cũ, không sửa lịch sử.

**Ngoại lệ:**

- E1 Mất kết nối khi lưu: không lưu nửa vời, người dùng thấy rõ trạng thái chưa lưu.
- E2 Người không đủ quyền: giao diện chỉ đọc, từ chối ở backend.
- E3 Cấu hình mâu thuẫn (ví dụ hạn M ngắn hơn thời gian chờ K): chặn lưu.

**Quy tắc liên quan:** BR-01, BR-02, BR-05, BR-06, BR-11, BR-13, BR-18 đến BR-20, BR-23, BR-25.

**Tiêu chí chấp nhận:**

- AC-11.1 Đổi tỷ lệ: đơn mới tính theo tỷ lệ mới, đơn trước đó không đổi.
- AC-11.2 Nhập giá trị âm: không lưu được.
- AC-11.3 Hai admin lưu đồng thời: người sau nhận thông báo xung đột.
- AC-11.4 Mỗi lần lưu có một dòng nhật ký (ai, khi nào, giá trị trước và sau).

### UC-12 — Quản lý danh mục phần thưởng

**Tác nhân:** Merchant Admin · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Tạo và quản lý các reward khách có thể đổi.

**Tiền điều kiện:** Admin đã đăng nhập và có quyền.

**Hậu điều kiện:** Reward được lưu, hiển thị cho khách đúng theo trạng thái và lịch hiệu lực.

**Luồng chính:**

1. Admin tạo reward gồm: tên, mô tả, số điểm cần, loại giảm (số tiền cố định hoặc phần trăm), giá trị giảm, trần giảm, đơn tối thiểu, phạm vi áp dụng, số ngày hiệu lực của mã, số lượng tối đa, giới hạn mỗi khách, lịch hiệu lực, bật hoặc tắt.
2. Hệ thống kiểm tra hợp lệ.
3. Lưu và hiển thị trong danh mục.

**Luồng thay thế:**

- A1 Sửa reward đã có lượt đổi: chỉ ảnh hưởng các lượt đổi sau, mã đã phát giữ nguyên điều kiện.
- A2 Xóa reward đã có lượt đổi: không cho xóa, chỉ vô hiệu hóa để giữ liên kết lịch sử.
- A3 Hết số lượng: tự chuyển trạng thái hết hàng, UC-10 từ chối.
- A4 Có lịch hiệu lực: tự bật và tắt theo thời gian.
- A5 Sắp xếp thứ tự hiển thị cho khách.

**Ngoại lệ:**

- E1 Phần trăm giảm lớn hơn 100 hoặc giá trị âm: chặn.
- E2 Quy đổi bất hợp lý (điểm quá rẻ so với giá trị giảm): cảnh báo tỷ lệ giá trị trên điểm so với giá trị điểm tích trung bình và yêu cầu xác nhận.
- E3 Hai admin sửa cùng lúc: xử lý như UC-11 (A2).

**Quy tắc liên quan:** BR-14, BR-23, BR-25.

**Tiêu chí chấp nhận:**

- AC-12.1 Reward đang bật xuất hiện cho khách ở UC-09 và UC-10.
- AC-12.2 Reward bị vô hiệu: không đổi được nhưng mã đã phát vẫn dùng được.
- AC-12.3 Sửa số điểm cần: không ảnh hưởng mã đã phát.
- AC-12.4 Hết số lượng: không cho đổi.

### UC-13 — Tra cứu khách hàng và sổ cái điểm

**Tác nhân:** Merchant Admin, Merchant Staff · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Nhân sự tìm được khách và hiểu vì sao số dư như hiện tại.

**Tiền điều kiện:** Đã đăng nhập, có quyền xem.

**Hậu điều kiện:** Không thay đổi dữ liệu. Truy cập hồ sơ được ghi nhật ký.

**Luồng chính:**

1. Nhập email, tên hoặc Customer ID vào ô tìm kiếm.
2. Hiển thị danh sách khớp, có phân trang.
3. Mở hồ sơ: số dư khả dụng, điểm Chờ, điểm sắp hết hạn, hạng, các lượt đổi và trạng thái mã, thông tin cơ bản.
4. Xem sổ cái: mọi giao dịch theo thời gian, lọc theo loại, đơn hàng, khoảng ngày. Mỗi giao dịch có liên kết tới đơn trên Shopify, lý do, người thực hiện (nếu thủ công) và bản chụp quy tắc đã áp dụng.

**Luồng thay thế:**

- A1 Không có kết quả: gợi ý kiểm tra lại, hoặc đồng bộ khách (UC-04).
- A2 Nhiều khách trùng tên: hiển thị thêm email và ngày tạo để phân biệt.
- A3 Staff chỉ đọc: ẩn các nút điều chỉnh.
- A4 Xuất CSV (Should): giới hạn theo quyền, ghi nhật ký mỗi lần xuất.
- A5 Khách đã ẩn danh: hiển thị mã nội bộ thay cho thông tin cá nhân.

**Ngoại lệ:**

- E1 Số dư hiển thị lệch tổng giao dịch: cảnh báo lỗi toàn vẹn (UC-08 E2).
- E2 Dữ liệu quá lớn: phân trang phía máy chủ, giới hạn kích thước khi xuất.
- E3 Truy cập không đủ quyền: trả 403 và ghi nhật ký.
- E4 Nguy cơ lộ dữ liệu cá nhân: che một phần email và số điện thoại theo vai trò (Should), ghi nhật ký truy cập hồ sơ (BR-25, BR-26).

**Quy tắc liên quan:** BR-22, BR-25, BR-26.

**Tiêu chí chấp nhận:**

- AC-13.1 Tìm theo email trả về đúng khách.
- AC-13.2 Tổng các giao dịch bằng số dư hiển thị.
- AC-13.3 Staff không thấy nút điều chỉnh điểm.
- AC-13.4 Mỗi giao dịch truy ngược được tới đơn nguồn.

### UC-14 — Điều chỉnh điểm thủ công

**Tác nhân:** Merchant Admin (Staff trong hạn mức được cấp) · **Ưu tiên:** Must · **Giai đoạn:** MVP

**Mục tiêu:** Sửa sai, tặng thưởng, bù đắp sự cố, có kiểm soát và truy vết được.

**Kích hoạt:** Admin chọn Điều chỉnh điểm trong hồ sơ khách.

**Tiền điều kiện:** Có quyền điều chỉnh. Khách đã có hồ sơ.

**Hậu điều kiện:** Có một giao dịch ADJUST (cộng hoặc trừ) gắn người thực hiện và lý do. Số dư cập nhật. Có nhật ký.

**Luồng chính:**

1. Chọn cộng hoặc trừ, nhập số điểm nguyên khác 0.
2. Chọn lý do từ danh mục (bù đắp sự cố, quà tặng, sửa sai, khiếu nại, khác) và nhập ghi chú bắt buộc.
3. Với cộng điểm: chọn trạng thái ban đầu (Khả dụng ngay hoặc Chờ) và hạn dùng (mặc định theo BR-11).
4. Hệ thống kiểm tra hạn mức theo vai trò (BR-21) và hiển thị số dư trước và sau để xác nhận.
5. Xác nhận: trong một transaction, khóa dòng khách, tạo giao dịch ADJUST, cập nhật số dư và lô, ghi nhật ký (BR-25).

**Luồng thay thế:**

- A1 Trừ vượt số dư khả dụng: từ chối, hoặc cho phép số dư âm kèm cảnh báo nếu Q-05 cho phép và người dùng đủ quyền.
- A2 Vượt hạn mức của Staff: chuyển thành yêu cầu chờ Admin duyệt (Should), hoặc từ chối.
- A3 Số điểm lớn bất thường (vượt ngưỡng cảnh báo): yêu cầu xác nhận lần hai và có thể thông báo cho chủ shop.
- A4 Nhập nhầm: không sửa hay xóa. Tạo giao dịch ADJUST ngược dấu, lý do Sửa sai, tham chiếu giao dịch gốc (BR-22).
- A5 Tích bù cho đơn bị bỏ qua (do chương trình tắt hoặc sự cố): thực hiện bằng ADJUST kèm mã đơn tham chiếu. Hệ thống cảnh báo nếu đơn đó đã có EARN để tránh tích hai lần.
- A6 Điều chỉnh hàng loạt bằng CSV (Could): xem trước, kiểm tra từng dòng, áp dụng nguyên khối hoặc báo lỗi theo dòng.
- A7 Thông báo cho khách về điều chỉnh (Should).

**Ngoại lệ:**

- E1 Gửi trùng (bấm đúp): khóa idempotency theo yêu cầu, chỉ một giao dịch.
- E2 Hai admin điều chỉnh cùng khách cùng lúc: khóa dòng khách, số dư đã xác nhận ở bước 4 được kiểm tra lại, nếu đã đổi thì yêu cầu xác nhận lại.
- E3 Thiếu lý do hoặc ghi chú: không cho gửi.
- E4 Lỗi giữa chừng: rollback, không để số dư và lô bị lệch.
- E5 Lạm dụng nội bộ (tự cộng điểm cho tài khoản liên quan): nhật ký không thể xóa, có báo cáo điều chỉnh theo người thực hiện (Should).

**Quy tắc liên quan:** BR-09, BR-11, BR-21, BR-22, BR-25.

**Tiêu chí chấp nhận:**

- AC-14.1 Cộng 50 điểm có lý do: một ADJUST +50, số dư tăng 50, nhật ký đủ người thực hiện và lý do.
- AC-14.2 Để trống lý do: không gửi được.
- AC-14.3 Staff vượt hạn mức: bị chặn hoặc chuyển chờ duyệt.
- AC-14.4 Sửa nhầm bằng giao dịch ngược, giao dịch gốc còn nguyên vẹn.
- AC-14.5 Bấm đúp: chỉ một giao dịch.

### UC-17 — Xử lý khi gỡ cài đặt app

**Tác nhân:** Shopify (webhook app/uninstalled), Hệ thống · **Ưu tiên:** Should · **Giai đoạn:** MVP

**Mục tiêu:** Dừng hoạt động an toàn, không mất dữ liệu điểm, cho phép khôi phục khi cài lại.

**Luồng chính:**

1. Nhận app/uninstalled qua UC-07.
2. Đánh dấu cửa hàng không hoạt động, vô hiệu token đang lưu, dừng các tác vụ định kỳ của cửa hàng.
3. Giữ nguyên dữ liệu điểm trong thời gian lưu giữ N ngày (cấu hình).
4. Thông báo cho vận hành.

**Luồng thay thế:**

- A1 Cài lại trong thời gian lưu giữ: khôi phục hoạt động, chạy đối soát (UC-08) để bù khoảng thời gian gián đoạn.
- A2 Hết thời gian lưu giữ: xóa hoặc ẩn danh dữ liệu theo chính sách (UC-16).
- A3 Gỡ nhầm: xử lý như A1, khách vẫn thấy đủ điểm khi ứng dụng được cài lại.

**Ngoại lệ:**

- E1 Webhook gỡ cài đặt bị mất: lần gọi API kế tiếp trả lỗi xác thực, hệ thống coi như đã gỡ và ngừng thử lại vô hạn.
- E2 Webhook khác đến trong lúc đã gỡ: bị từ chối hoặc bỏ qua theo trạng thái cửa hàng.
- E3 Widget còn trên theme sau khi gỡ: ẩn hoặc hiển thị thông báo tạm ngưng, không làm vỡ trang.

**Tiêu chí chấp nhận:**

- AC-17.1 Sau khi gỡ không còn tác vụ định kỳ nào chạy cho cửa hàng.
- AC-17.2 Cài lại: dữ liệu điểm còn nguyên.
- AC-17.3 Trang storefront không lỗi sau khi gỡ.

### UC-18 — Chạy lại sự kiện thất bại

**Tác nhân:** Vận hành (Developer), Merchant Admin (xem) · **Ưu tiên:** Should · **Giai đoạn:** MVP

**Mục tiêu:** Tìm và xử lý các sự kiện bị lỗi mà không can thiệp trực tiếp vào cơ sở dữ liệu.

**Luồng chính:**

1. Mở danh sách sự kiện, lọc theo trạng thái (Lỗi, Đã nhận quá lâu), topic, thời gian.
2. Xem chi tiết: payload, thông báo lỗi, số lần đã thử.
3. Chạy lại một hoặc nhiều sự kiện (idempotent), xem kết quả.
4. Hoặc bỏ qua có chủ đích kèm lý do.

**Luồng thay thế:**

- A1 Chạy lại hàng loạt: giới hạn tốc độ, theo thứ tự thời gian.
- A2 Lỗi do dữ liệu chứ không do hệ thống: sửa nguyên nhân hoặc bỏ qua có lý do, ghi nhật ký.
- A3 Cảnh báo tự động khi số sự kiện lỗi vượt ngưỡng hoặc tồn đọng quá lâu.

**Ngoại lệ:**

- E1 Chạy lại gây trùng: không xảy ra nhờ idempotency. Giao dịch đã tồn tại thì đánh dấu đã xử lý.
- E2 Chạy lại sự kiện cũ khi trạng thái đơn đã đổi: tác vụ xác nhận trạng thái hiện tại bằng API (BR-24) nên kết quả vẫn đúng.
- E3 Người chạy lại không đủ quyền: từ chối.

**Tiêu chí chấp nhận:**

- AC-18.1 Sự kiện lỗi chạy lại thành công thì chuyển sang Đã xử lý.
- AC-18.2 Chạy lại hai lần không tạo giao dịch trùng.
- AC-18.3 Mọi thao tác bỏ qua đều có lý do và người thực hiện.

## 8. Use case Phase 2 (tóm tắt)

- **UC-15 Báo cáo tổng quan:** điểm đã phát hành, đã đổi, đã hết hạn, nợ điểm còn lại (liability), doanh thu từ đơn dùng mã đổi điểm, số khách hoạt động. Lưu ý số liệu phải khớp sổ cái và thống nhất múi giờ.
- **UC-16 Dữ liệu cá nhân và xóa khách:** xử lý yêu cầu truy xuất hoặc xóa dữ liệu. Hướng đề xuất: ẩn danh hóa hồ sơ (xóa tên, email), giữ sổ cái ở dạng không định danh để bảo toàn tính toàn vẹn của điểm. Đối chiếu quy định bảo vệ dữ liệu cá nhân hiện hành tại Việt Nam (Nghị định 13/2023/NĐ-CP) và các sự kiện về dữ liệu khách mà Shopify gửi.
- **UC-19 Phân hạng thành viên:** ngưỡng theo điểm tích lũy trong kỳ (ví dụ 12 tháng cuốn chiếu), hệ số nhân điểm theo hạng, nâng hạng tức thì, hạ hạng ở cuối kỳ, hoàn tiền có thể kéo hạng xuống, thông báo khi đổi hạng.
- **UC-20 Giới thiệu bạn bè:** mã giới thiệu riêng, thưởng khi bạn bè hoàn tất đơn đầu tiên và đơn đã qua thời gian chờ. Chặn tự giới thiệu, trùng thiết bị hoặc email, giới hạn số lần thưởng. Thu hồi thưởng khi đơn bị hủy hoặc hoàn.
- **UC-21 Điểm sinh nhật và chiến dịch:** cần khách đồng ý cung cấp ngày sinh. Tác vụ chạy hằng ngày, mỗi khách một lần mỗi năm, xử lý múi giờ. Chiến dịch nhân điểm theo lịch với quy tắc ưu tiên khi nhiều chiến dịch trùng thời gian.
- **UC-22 Chặn khách và phát hiện gian lận:** cờ chặn có lý do và hiệu lực. Nhận diện mẫu bất thường (nhiều tài khoản cùng thiết bị, đổi điểm hàng loạt, hoàn tiền liên tục sau khi nhận thưởng), cảnh báo và xem xét thủ công.

## Phụ lục

### A. Vòng đời trạng thái

- **Lô điểm:** Chờ → Khả dụng → Đã dùng hết hoặc Hết hạn. Chờ → Đã hủy (voided) khi đơn bị hủy hoặc hoàn toàn bộ. Lô Khả dụng giảm dần do đổi điểm và thu hồi.
- **Redemption:** PENDING\_CODE → ISSUED → USED, EXPIRED hoặc CANCELLED. PENDING\_CODE → FAILED khi tạo mã thất bại.
- **Sự kiện webhook:** Đã nhận → Đang xử lý → Đã xử lý, Bỏ qua hoặc Lỗi (Lỗi có thể chạy lại).
- **Giao dịch điểm:** chỉ thêm, không đổi trạng thái. Sửa sai bằng giao dịch bù trừ.

### B. Yêu cầu phi chức năng chính

- NFR-01 Idempotency: không sự kiện nghiệp vụ nào được ghi sổ hai lần (kiểm thử bằng gửi trùng và chạy song song).
- NFR-02 Hiệu năng: webhook trả lời dưới 1 giây (đề xuất). Điểm xuất hiện trong sổ cái trong vòng 1 phút cho 95% đơn khi hệ thống hoạt động bình thường.
- NFR-03 Toàn vẹn: số dư luôn bằng tổng sổ cái, kiểm tra tự động hằng đêm (UC-08).
- NFR-04 Bảo mật: xác thực chữ ký webhook và App Proxy, mã hóa token lưu trữ, phân quyền Admin và Staff, không để lộ client secret, ghi nhật ký truy cập dữ liệu cá nhân.
- NFR-05 Sẵn sàng và phục hồi: sao lưu cơ sở dữ liệu định kỳ. Ngừng máy chủ không làm mất sự kiện (Shopify gửi lại, hàng đợi lưu bền, đối soát bù).
- NFR-06 Quan sát: nhật ký có cấu trúc, mã tương quan theo webhook và đơn, cảnh báo khi sự kiện lỗi tăng, hàng đợi tồn đọng hoặc token lỗi.
- NFR-07 Mở rộng nhiều cửa hàng: mọi bảng có mã cửa hàng, không dùng biến toàn cục cho dữ liệu theo cửa hàng.
- NFR-08 Khả năng sử dụng: giao diện tiếng Việt (Q-08), thông báo lỗi dễ hiểu, widget không làm chậm trang.

### C. Ma trận truy vết sơ bộ

| Use case | Webhook hoặc API Shopify | Quyền (scope) dự kiến |
| --- | --- | --- |
| UC-01 | orders/paid; đọc đơn qua API | read\_orders |
| UC-02 | orders/cancelled; đọc đơn qua API | read\_orders |
| UC-03 | refunds/create; đọc đơn qua API | read\_orders |
| UC-04 | customers/create, customers/update; đọc khách qua API | read\_customers |
| UC-07 | Mọi webhook | Không cần thêm |
| UC-08 | API danh sách đơn, hoàn tiền, mã giảm giá | read\_orders, read\_customers, read\_discounts |
| UC-09 | App Proxy | Không cần thêm |
| UC-10 | API tạo mã giảm giá; orders/paid (mã được dùng) | write\_discounts, read\_discounts, read\_orders |
| UC-17 | app/uninstalled | Không cần thêm |

Danh sách scope là dự kiến, cần xác nhận lại khi thiết kế API và kiểm tra tài liệu Shopify tại thời điểm triển khai.

### D. Câu hỏi mở bổ sung

- Q-07: Khách có được hủy mã đã đổi không, trong bao lâu?
- Q-08: Giao diện chỉ tiếng Việt hay thêm tiếng Anh?
- Q-09: Nếu cửa hàng bán nhiều thị trường, điểm tính theo tiền tệ nào?
- Q-10: Cho phép đổi điểm ngay tại bước thanh toán (cần Checkout extension) hay chỉ qua mã giảm giá?
- Q-11: Có tích điểm cho đơn POS hoặc bán trực tiếp không?
- Q-12: Khi khách yêu cầu xóa dữ liệu mà còn điểm chưa dùng thì xử lý thế nào?

### E. Bước tiếp theo đề xuất

1. Chủ shop trả lời Q-01 đến Q-12 (ưu tiên Q-01 đến Q-06).
2. Chốt phạm vi MVP và cập nhật tài liệu lên bản 1.0.
3. Từ các use case MVP: thiết kế dữ liệu (ERD), đặc tả API, backlog và kịch bản kiểm thử lấy trực tiếp từ các tiêu chí chấp nhận.
