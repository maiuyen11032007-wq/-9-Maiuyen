# AI Agent – Tổng hợp & phân phối cơ hội kinh doanh tuần (Công ty Bình Minh)

Pipeline Python (pandas) **tất định, chạy offline**, bám đúng sơ đồ quy trình 6 bước + 2 điểm con người can thiệp.

```
Input ─► 1.data_cleaner ─► 2.data_reconciler ─► 3.kpi_calculator ─► 4.anomaly_detector
                                                                        │  ▲
                                                     (Human-in-the-loop: decisions.csv)
                                                                        ▼  │
              Output ◄─ Phê duyệt (approve) ◄─ 6.report_generator ◄─ 5.priority_ranker
```

| Bước | Module | Xử lý chính |
|---|---|---|
| Input | `agent/io_utils.py` | Đọc nhiều tệp CSV/XLSX, tự ánh xạ tên cột (Việt/Anh) theo `config/schema.yaml` |
| 1 | `agent/data_cleaner.py` | Kiểm tra schema, chuẩn hóa ngày/số/giai đoạn; **ngày đối chiếu thống nhất** (bỏ bản ghi phát sinh sau ngày đó); loại trùng mã giữa các tệp (giữ bản cập nhật mới nhất); phát hiện nghi trùng khác mã |
| 2 | `agent/data_reconciler.py` | Trạng thái tại ngày đối chiếu theo thứ tự **Đơn hàng > Lịch sử trạng thái > Hồ sơ**; gắn đơn hàng; **tách doanh số đã ghi nhận khỏi pipeline mở**; đơn một phần chỉ để phần còn lại |
| 3 | `agent/kpi_calculator.py` | EV = Giá trị còn mở × **trọng số chuẩn** (`rules.yaml`); doanh số chỉ lấy từ đơn hàng; % hoàn thành & dự báo theo NV |
| 4 | `agent/anomaly_detector.py` | Gắn cờ: quá hạn không hoạt động, ghi chú mất khách chưa đổi trạng thái, lâu không liên hệ, giá trị lớn thiếu xác nhận… → `can_xac_minh.csv`; áp dụng quyết định của con người |
| 5 | `agent/priority_ranker.py` | Chỉ xếp hạng CH **không còn vấn đề chưa xác minh**; điểm = Giá trị/Độ tin cậy/Tiến độ/Khẩn cấp; Top 5 kèm **Căn cứ**, **Hành động tuần tới**, **Người hỗ trợ** |
| 6 | `agent/report_generator.py` | Báo cáo Markdown, Dashboard HTML, CSV, kế hoạch hành động – trạng thái **DỰ THẢO** |
| Duyệt | `main.py approve` | Trưởng phòng phê duyệt → bản `FINAL` có dấu phê duyệt |

## Chạy Giao diện Web Trực quan (Localhost)

Hệ thống có sẵn máy chủ Web Localhost tích hợp đầy đủ tính năng:
- **Dashboard điều hành**: 6 thẻ KPI, biểu đồ tiến độ 10 NV, tỷ lệ dự báo hoàn thành kỳ.
- **Top 5 ưu tiên**: Chi tiết căn cứ, hành động tuần tới và phân công hỗ trợ.
- **Human-in-the-loop trực quan**: Xem danh sách cơ hội "Cần xác minh", chọn quyết định xử lý (`CONFIRM_LOST`, `KEEP_OPEN`, `UPDATE_STAGE`, `CONFIRMED`, `MERGE_INTO`) trực tiếp trên dropdown và bấm nút *"Lưu quyết định & Chạy lại đối soát"*.
- **Phê duyệt chính thức**: Trưởng phòng nhập tên và nhận xét để phê duyệt báo cáo sang `FINAL`.
- **Tải về**: Tải ngay các tệp CSV, Markdown, Dashboard HTML offline.

### Cách khởi động:
- **Cách 1**: Nhấp đúp vào file `run_localhost.bat`
- **Cách 2**: Chạy lệnh terminal:
  ```powershell
  .venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8080
  ```
- **Truy cập trình duyệt**: **[http://localhost:8080](http://localhost:8080)**

---

## Cài đặt & chạy dòng lệnh (CLI)

```powershell
uv sync                                   # cài pandas, openpyxl, pyyaml
# (tùy chọn) sinh dữ liệu giả lập để chạy thử
uv run python tools/generate_sample_data.py --out data/sample --as-of 2026-11-13
# Chạy agent
uv run python main.py run --input data/sample --as-of 2026-11-13
```

### Điểm con người can thiệp (Human-in-the-loop)
1. Mở `output/<ngày>/decisions_template.csv`, điền cột `decision` cho các cơ hội đã xác minh, lưu thành `output/<ngày>/decisions.csv`, rồi **chạy lại** lệnh `run`.

   | decision | Ý nghĩa | Cột kèm |
   |---|---|---|
   | `CONFIRM_LOST` | Xác nhận đã mất → LOST | |
   | `KEEP_OPEN` | Vẫn mở | `new_close_date` (bắt buộc nếu quá hạn) |
   | `UPDATE_STAGE` | Sửa giai đoạn / ngày chốt | `new_stage`, `new_close_date` |
   | `CONFIRMED` | Đã xác nhận người quyết định/ngân sách/giá trị | |
   | `MERGE_INTO` | Là bản trùng → loại | `merge_into` |
2. Phê duyệt cuối cùng:
   ```powershell
   uv run python main.py approve --run output/2026-11-13 --approver "Họ tên Trưởng phòng" --comment "..."
   ```

## Đưa dữ liệu thật vào
Đặt tệp vào `data/input/` (hoặc thư mục bất kỳ qua `--input`). Tên tệp và cột tối thiểu:

| Dataset | Mẫu tên tệp | Cột bắt buộc | Cột nên có |
|---|---|---|---|
| Hồ sơ cơ hội (nhiều tệp được) | `opportunit*`, `co_hoi*` | opp_id, customer_name, owner_id, stage, amount, expected_close_date | opp_name, created_date, last_updated, decision_maker_confirmed, budget_confirmed |
| Lịch sử trạng thái | `stage_history*`, `lich_su_trang_thai*` | opp_id, change_date, to_stage | from_stage |
| Hoạt động liên hệ | `activit*`, `hoat_dong*` | opp_id, activity_date | activity_type, notes |
| Đơn hàng | `order*`, `don_hang*` | order_id, order_date, amount | opp_id, customer_name, owner_id, order_status |
| Nhân viên | `sales_rep*`, `nhan_vien*` | rep_id, rep_name | team |
| Mục tiêu theo kỳ | `target*`, `muc_tieu*` | rep_id, period_start, period_end, target_amount | period |

Tên cột khác? Thêm bí danh trong `config/schema.yaml`. Quy tắc trạng thái, **tỷ lệ trọng số từng giai đoạn**, ngưỡng bất thường và **tiêu chí ưu tiên** nằm trong `config/rules.yaml` – hãy thay bằng quy tắc chính thức của công ty.

## Kết quả (`output/<ngày đối chiếu>/`)
- `BaoCao_Tuan_<ngày>_DRAFT.md` / `_FINAL.md` – báo cáo đầy đủ (tóm tắt điều hành, KPI, Top 5, cảnh báo, kế hoạch hành động, nhật ký đối soát, phương pháp)
- `Dashboard_<ngày>_DRAFT.html` / `_FINAL.html` – dashboard mở trực tiếp bằng trình duyệt
- `top_uu_tien.csv`, `ke_hoach_hanh_dong.csv`, `can_xac_minh.csv`, `kpi_theo_nhan_vien.csv`, `xep_hang_day_du.csv`, `co_hoi_da_doi_soat.csv`, `nhat_ky_van_de.csv`, `run_log.json`

