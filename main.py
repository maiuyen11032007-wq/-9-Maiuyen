"""AI Agent - Tổng hợp & phân phối cơ hội kinh doanh tuần (Công ty Bình Minh).

Cách dùng:
  uv run python main.py run --input data/sample --as-of 2026-11-13
  uv run python main.py run --input data/input  --as-of 2026-11-13 --decisions output/2026-11-13/decisions.csv
  uv run python main.py approve --run output/2026-11-13 --approver "Nguyễn Văn A" --comment "Đồng ý"
"""
import argparse
import os
import sys
from datetime import date

from agent.io_utils import SchemaError
from agent.pipeline import approve, run

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="AI Agent kinh doanh tuần - Bình Minh")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Chạy pipeline tạo dự thảo báo cáo")
    r.add_argument("--input", default=os.path.join(BASE, "data", "input"), help="Thư mục dữ liệu đầu vào")
    r.add_argument("--output", default=os.path.join(BASE, "output"), help="Thư mục kết quả")
    r.add_argument("--as-of", default=str(date.today()), help="Ngày đối chiếu (YYYY-MM-DD)")
    r.add_argument("--config", default=os.path.join(BASE, "config"))
    r.add_argument("--decisions", default=None, help="Tệp quyết định xác minh (Human-in-the-loop)")
    a = sub.add_parser("approve", help="Trưởng phòng phê duyệt báo cáo")
    a.add_argument("--run", required=True, help="Thư mục kết quả của lần chạy")
    a.add_argument("--approver", required=True)
    a.add_argument("--comment", default="")
    args = p.parse_args()
    try:
        if args.cmd == "run":
            res = run(args.input, args.output, args.as_of, args.config, args.decisions)
            print(f"\nHoàn tất. Kết quả: {res['out_dir']}")
        else:
            approve(args.run, args.approver, args.comment)
    except SchemaError as e:
        print(f"\n[LỖI DỮ LIỆU] {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

