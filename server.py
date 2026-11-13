"""Máy chủ Web Localhost cho AI Agent Kinh doanh tuần - Công ty Bình Minh.
Sử dụng thư viện chuẩn của Python (http.server) - chạy trực tiếp không cần cài thêm package.
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import mimetypes
import os
import pickle
import shutil
import sys
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

from agent import anomaly_detector, io_utils, llm_assistant, pipeline
from agent.io_utils import fmt_money, fmt_pct, load_yaml
from agent.issues import ISSUE_CATALOG

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE, "config")
DATA_DIR = os.path.join(BASE, "data")
INPUT_DIR = os.path.join(DATA_DIR, "input")
SAMPLE_DIR = os.path.join(DATA_DIR, "sample")
OUTPUT_DIR = os.path.join(BASE, "output")

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

PIPELINE_LOCK = threading.Lock()
CURRENT_JOB = {"status": "idle", "message": "Sẵn sàng", "progress": 0}


def classify_file(filename: str, schema: dict) -> str:
    """Xác định loại dữ liệu của tệp dựa theo schema.yaml."""
    fn_lower = filename.lower()
    for ds_name, spec in schema.get("datasets", {}).items():
        for pat in spec.get("pattern", []):
            if fnmatch.fnmatch(fn_lower, pat.lower()):
                return ds_name
    return "unknown"


class AgentWebServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"[Localhost] {self.address_string()} - {format % args}\n")

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, content_type: str = "text/plain; charset=utf-8", status: int = 200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file_response(self, file_path: str, as_download: bool = False, filename: str | None = None):
        if not os.path.exists(file_path):
            self.send_text("File không tồn tại", status=404)
            return
        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"
        size = os.path.getsize(file_path)
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(size))
        if as_download:
            fn = filename or os.path.basename(file_path)
            self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
        self.end_headers()
        with open(file_path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        params = parse_qs(url.query)

        if path in ("/", "/index.html"):
            index_path = os.path.join(BASE, "web", "index.html")
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    self.send_text(f.read(), "text/html; charset=utf-8")
            else:
                self.send_text("Đang xây dựng giao diện...", "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            file_path = os.path.join(BASE, "web", "static", rel_path)
            self.send_file_response(file_path)
            return

        if path == "/api/status":
            self.send_json({"job": CURRENT_JOB})
            return

        if path == "/api/uploaded_files":
            schema = load_yaml(os.path.join(CONFIG_DIR, "schema.yaml"))
            files_info = []
            detected_date = io_utils.detect_file_report_date(INPUT_DIR)
            if os.path.exists(INPUT_DIR):
                for fn in sorted(os.listdir(INPUT_DIR)):
                    if fn.startswith("~$"):
                        continue
                    fp = os.path.join(INPUT_DIR, fn)
                    if os.path.isfile(fp):
                        st = os.stat(fp)
                        ds_type = classify_file(fn, schema)
                        sheets = []
                        if fn.lower().endswith((".xlsx", ".xls")):
                            try:
                                import openpyxl
                                wb = openpyxl.load_workbook(fp, read_only=True)
                                sheets = wb.sheetnames
                                ds_type = f"Excel đa sheet ({len(sheets)} sheets)"
                            except Exception:
                                pass
                        file_det_date = io_utils.detect_file_report_date(INPUT_DIR, fn)
                        files_info.append({
                            "filename": fn,
                            "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "dataset_type": ds_type,
                            "sheets": sheets,
                            "detected_date": file_det_date
                        })
            dataset_status = {}
            for ds, spec in schema.get("datasets", {}).items():
                matched = [f for f in files_info if f["dataset_type"] == ds or (f["sheets"] and any(fnmatch.fnmatch(s.lower(), p.lower()) for s in f["sheets"] for p in spec.get("sheet_patterns", [])))]
                dataset_status[ds] = {
                    "count": len(matched),
                    "files": [m["filename"] for m in matched],
                    "required": ds not in ["stage_history", "activities"]
                }
            self.send_json({"files": files_info, "dataset_status": dataset_status, "detected_date": detected_date})
            return

        if path == "/api/runs":
            runs = []
            if os.path.exists(OUTPUT_DIR):
                for d in sorted(os.listdir(OUTPUT_DIR), reverse=True):
                    dp = os.path.join(OUTPUT_DIR, d)
                    if os.path.isdir(dp):
                        has_pkl = os.path.exists(os.path.join(dp, "context.pkl"))
                        has_app = os.path.exists(os.path.join(dp, "approval.json"))
                        runs.append({"date": d, "has_data": has_pkl, "approved": has_app})
            self.send_json({"runs": runs})
            return

        if path == "/api/data":
            run_date = params.get("date", [""])[0]
            if not run_date:
                if os.path.exists(OUTPUT_DIR):
                    ds = sorted([d for d in os.listdir(OUTPUT_DIR) if os.path.isdir(os.path.join(OUTPUT_DIR, d))], reverse=True)
                    if ds:
                        run_date = ds[0]
            if not run_date:
                self.send_json({"error": "Chưa có kết quả phân tích nào. Vui lòng tải file lên và bấm 'Bắt đầu chạy AI Agent'."}, status=404)
                return

            run_dir = os.path.join(OUTPUT_DIR, run_date)
            pkl_path = os.path.join(run_dir, "context.pkl")
            if not os.path.exists(pkl_path):
                # Fallback sang thư mục phân tích hợp lệ gần nhất nếu có
                latest_cands = sorted([d for d in os.listdir(OUTPUT_DIR) if os.path.isdir(os.path.join(OUTPUT_DIR, d)) and os.path.exists(os.path.join(OUTPUT_DIR, d, "context.pkl"))], reverse=True)
                if latest_cands:
                    run_dir = os.path.join(OUTPUT_DIR, latest_cands[0])
                    pkl_path = os.path.join(run_dir, "context.pkl")
                    run_date = latest_cands[0]
                else:
                    self.send_json({"error": f"Không tìm thấy dữ liệu cho ngày đối chiếu {run_date}"}, status=404)
                    return

            try:
                with open(pkl_path, "rb") as f:
                    ctx = pickle.load(f)
            except Exception as e:
                self.send_json({"error": f"Lỗi đọc dữ liệu: {e}"}, status=500)
                return

            approval_file = os.path.join(run_dir, "approval.json")
            approval = None
            if os.path.exists(approval_file):
                with open(approval_file, "r", encoding="utf-8") as f:
                    approval = json.load(f)

            log_entries = []
            run_log_file = os.path.join(run_dir, "run_log.json")
            if os.path.exists(run_log_file):
                with open(run_log_file, "r", encoding="utf-8") as f:
                    try:
                        log_entries = json.load(f).get("log", [])
                    except Exception:
                        pass

            by_rep = ctx["by_rep"].to_dict("records")
            top = ctx["top"].to_dict("records")
            quarantine = ctx["quarantine"].to_dict("records")
            action_plan = ctx["action_plan"].to_dict("records")
            rules = ctx["rules"]

            iss = ctx["issues"]
            iss_unresolved = iss[(~iss["resolved"]) & (iss["severity"] != "INFO")]
            issue_summary = []
            if not iss_unresolved.empty:
                for (code, sev), g in iss_unresolved.groupby(["issue_code", "severity"]):
                    label, _, act = ISSUE_CATALOG.get(code, (code, "", ""))
                    issue_summary.append({
                        "issue_code": code,
                        "label": label,
                        "severity": sev,
                        "count": g["opp_id"].nunique(),
                        "suggested_action": act
                    })
                issue_summary.sort(key=lambda x: -x["count"])

            resp_data = {
                "as_of": ctx["as_of"].strftime("%Y-%m-%d"),
                "total": ctx["total"],
                "excluded": ctx["excluded"],
                "stats": ctx["stats"],
                "by_rep": by_rep,
                "top": top,
                "quarantine": quarantine,
                "action_plan": action_plan,
                "issue_summary": issue_summary,
                "has_gemini": bool(llm_assistant.get_api_key()),
                "rules": {
                    "stages": rules.get("stages", {}),
                    "priority_rules": rules.get("priority_rules", {}),
                    "anomaly_rules": rules.get("anomaly_rules", {}),
                },
                "approval": approval,
                "log_entries": log_entries,
                "decisions_applied": ctx.get("decisions_applied", pd.DataFrame()).to_dict("records") if isinstance(ctx.get("decisions_applied"), pd.DataFrame) else []
            }
            self.send_json(resp_data)
            return

        if path == "/api/opportunity_detail":
            run_date = params.get("date", [""])[0]
            opp_id = params.get("id", [""])[0]
            if not run_date or not opp_id:
                self.send_json({"error": "Thiếu date hoặc id"}, status=400)
                return

            run_dir = os.path.join(OUTPUT_DIR, run_date)
            pkl_path = os.path.join(run_dir, "context.pkl")
            if not os.path.exists(pkl_path):
                self.send_json({"error": "Không tìm thấy dữ liệu"}, status=404)
                return

            with open(pkl_path, "rb") as f:
                ctx = pickle.load(f)

            opps = ctx["opportunities"]
            matching = opps[opps["opp_id"] == opp_id]
            if matching.empty:
                self.send_json({"error": f"Không tìm thấy cơ hội {opp_id}"}, status=404)
                return

            opp_row = matching.iloc[0].to_dict()
            issues_df = ctx["issues"]
            opp_issues = issues_df[issues_df["opp_id"] == opp_id].to_dict("records")

            self.send_json({"opportunity": opp_row, "issues": opp_issues})
            return

        if path == "/api/download":
            run_date = params.get("date", [""])[0]
            file_type = params.get("type", [""])[0]
            if not run_date or not file_type:
                self.send_text("Thiếu tham số date hoặc type", status=400)
                return

            run_dir = os.path.join(OUTPUT_DIR, run_date)
            mapping = {
                "report_md": os.path.join(run_dir, f"BaoCao_Tuan_{run_date}_FINAL.md" if os.path.exists(os.path.join(run_dir, f"BaoCao_Tuan_{run_date}_FINAL.md")) else f"BaoCao_Tuan_{run_date}_DRAFT.md"),
                "dashboard_html": os.path.join(run_dir, f"Dashboard_{run_date}_FINAL.html" if os.path.exists(os.path.join(run_dir, f"Dashboard_{run_date}_FINAL.html")) else f"Dashboard_{run_date}_DRAFT.html"),
                "kpi_csv": os.path.join(run_dir, "kpi_theo_nhan_vien.csv"),
                "top_csv": os.path.join(run_dir, "top_uu_tien.csv"),
                "quarantine_csv": os.path.join(run_dir, "can_xac_minh.csv"),
                "plan_csv": os.path.join(run_dir, "ke_hoach_hanh_dong.csv"),
                "opps_csv": os.path.join(run_dir, "co_hoi_da_doi_soat.csv"),
            }
            target_path = mapping.get(file_type)
            if not target_path or not os.path.exists(target_path):
                self.send_text(f"Không tìm thấy tệp {file_type} cho ngày {run_date}", status=404)
                return
            self.send_file_response(target_path, as_download=True)
            return

        self.send_text("Not Found", status=404)

    def do_POST(self):
        url = urlparse(self.path)
        path = unquote(url.path)

        length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(post_data) if post_data else {}
        except Exception:
            payload = {}

        if path == "/api/upload":
            files = payload.get("files", [])
            if not files:
                self.send_json({"error": "Không có tệp nào được gửi lên"}, status=400)
                return

            saved = []
            for item in files:
                fn = os.path.basename(item.get("filename", "")).strip()
                b64 = item.get("content_base64", "")
                if not fn or not b64:
                    continue
                try:
                    data = base64.b64decode(b64)
                    dest_path = os.path.join(INPUT_DIR, fn)
                    with open(dest_path, "wb") as f:
                        f.write(data)
                    saved.append(fn)
                except Exception as e:
                    self.send_json({"error": f"Lỗi lưu file {fn}: {e}"}, status=500)
                    return

            saved_details = []
            for fn in saved:
                fp = os.path.join(INPUT_DIR, fn)
                sz = os.path.getsize(fp) if os.path.exists(fp) else 0
                file_det_date = io_utils.detect_file_report_date(INPUT_DIR, fn)
                sheets = []
                if fn.lower().endswith((".xlsx", ".xls")):
                    try:
                        import openpyxl
                        wb = openpyxl.load_workbook(fp, read_only=True)
                        sheets = wb.sheetnames
                    except Exception:
                        pass
                saved_details.append({
                    "filename": fn,
                    "size": sz,
                    "detected_date": file_det_date,
                    "sheets": sheets
                })
            overall_date = io_utils.detect_file_report_date(INPUT_DIR, saved[0] if saved else None)
            self.send_json({
                "status": "ok",
                "saved_files": saved,
                "saved_details": saved_details,
                "count": len(saved),
                "detected_date": overall_date
            })
            return

        if path == "/api/delete_file":
            fn = payload.get("filename", "").strip()
            if not fn:
                self.send_json({"error": "Thiếu tên file"}, status=400)
                return
            fp = os.path.join(INPUT_DIR, fn)
            if os.path.exists(fp):
                os.remove(fp)
                self.send_json({"status": "ok", "message": f"Đã xóa {fn}"})
            else:
                self.send_json({"error": "File không tồn tại"}, status=404)
            return

        if path == "/api/clear_files":
            for fn in os.listdir(INPUT_DIR):
                fp = os.path.join(INPUT_DIR, fn)
                if os.path.isfile(fp):
                    os.remove(fp)
            self.send_json({"status": "ok", "message": "Đã dọn sạch thư mục tải lên data/input"})
            return

        if path == "/api/load_sample_into_input":
            if not os.path.exists(SAMPLE_DIR):
                self.send_json({"error": "Không tìm thấy thư mục mẫu"}, status=404)
                return
            copied = []
            for fn in os.listdir(SAMPLE_DIR):
                sp = os.path.join(SAMPLE_DIR, fn)
                if os.path.isfile(sp):
                    shutil.copy2(sp, os.path.join(INPUT_DIR, fn))
                    copied.append(fn)
            self.send_json({"status": "ok", "copied_files": copied, "message": f"Đã nạp {len(copied)} file mẫu vào input"})
            return

        if path == "/api/run":
            if PIPELINE_LOCK.locked():
                self.send_json({"error": "Agent đang xử lý quy trình khác, vui lòng chờ..."}, status=429)
                return

            target_file = payload.get("target_file", "").strip() or None
            as_of_str = payload.get("as_of", "").strip()
            input_dir = INPUT_DIR

            existing = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f)) and not f.startswith("~$")]
            if not existing:
                self.send_json({"error": "Chưa có file dữ liệu nào trong thư mục tải lên. Vui lòng kéo thả hoặc bấm tải lên các file Excel/CSV trước!"}, status=400)
                return

            if not target_file and len(existing) == 1:
                target_file = existing[0]

            if not as_of_str or as_of_str == "auto":
                detected = io_utils.detect_file_report_date(input_dir, target_file)
                as_of_str = detected if detected else str(date.today())

            def _worker(t_file, a_date):
                with PIPELINE_LOCK:
                    CURRENT_JOB["status"] = "running"
                    CURRENT_JOB["message"] = f"Đang chạy Agent đối chiếu ngày {a_date} trên tệp {t_file or 'tất cả'}..."
                    CURRENT_JOB["date"] = a_date
                    CURRENT_JOB["target_file"] = t_file
                    try:
                        pipeline.run(input_dir, OUTPUT_DIR, a_date, CONFIG_DIR, target_file=t_file)
                        CURRENT_JOB["status"] = "success"
                        CURRENT_JOB["message"] = f"Hoàn tất phân tích ngày {a_date} trên tệp {t_file or 'tất cả'}"
                        CURRENT_JOB["date"] = a_date
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        CURRENT_JOB["status"] = "error"
                        CURRENT_JOB["message"] = f"Lỗi Agent: {e}"

            t = threading.Thread(target=_worker, args=(target_file, as_of_str))
            t.daemon = True
            t.start()
            self.send_json({"status": "started", "as_of": as_of_str, "target_file": target_file, "message": "Đã bắt đầu chạy AI Agent"})
            return

        if path == "/api/decisions":
            run_date = payload.get("date")
            decisions = payload.get("decisions", [])
            if not run_date or not decisions:
                self.send_json({"error": "Thiếu dữ liệu ngày hoặc danh sách quyết định"}, status=400)
                return

            run_dir = os.path.join(OUTPUT_DIR, run_date)
            os.makedirs(run_dir, exist_ok=True)
            dec_csv = os.path.join(run_dir, "decisions.csv")

            df = pd.DataFrame(decisions)
            df.to_csv(dec_csv, index=False, encoding="utf-8-sig")

            try:
                pipeline.run(INPUT_DIR, OUTPUT_DIR, run_date, CONFIG_DIR, decisions=dec_csv)
                self.send_json({"status": "ok", "message": f"Đã áp dụng {len(decisions)} quyết định và cập nhật lại báo cáo!"})
            except Exception as e:
                self.send_json({"error": f"Lỗi khi chạy lại đối soát: {e}"}, status=500)
            return

        if path == "/api/generate_email":
            run_date = payload.get("date")
            if not run_date:
                self.send_json({"error": "Thiếu tham số date"}, status=400)
                return
            run_dir = os.path.join(OUTPUT_DIR, run_date)
            pkl_path = os.path.join(run_dir, "context.pkl")
            if not os.path.exists(pkl_path):
                self.send_json({"error": "Chưa có dữ liệu phân tích"}, status=404)
                return
            with open(pkl_path, "rb") as f:
                ctx = pickle.load(f)
            email_text = llm_assistant.generate_sales_briefing_email(ctx)
            if not email_text:
                email_text = "Không kết nối được Gemini API hoặc chưa cấu hình API key."
            self.send_json({"status": "ok", "email": email_text})
            return

        if path == "/api/approve":
            run_date = payload.get("date")
            approver = payload.get("approver", "").strip()
            comment = payload.get("comment", "").strip()
            if not run_date or not approver:
                self.send_json({"error": "Vui lòng nhập họ tên người phê duyệt"}, status=400)
                return

            run_dir = os.path.join(OUTPUT_DIR, run_date)
            try:
                res = pipeline.approve(run_dir, approver, comment)
                self.send_json({"status": "ok", "message": "Báo cáo đã được phê duyệt chính thức!", "approval": res["approval"]})
            except Exception as e:
                self.send_json({"error": f"Lỗi phê duyệt: {e}"}, status=500)
            return

        self.send_text("Not Found", status=404)


def main():
    parser = argparse.ArgumentParser(description="Chạy máy chủ Web Localhost cho AI Agent Kinh doanh tuần")
    parser.add_argument("--host", default="127.0.0.1", help="Host (mặc định: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port (mặc định: 8080)")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AgentWebServer)
    print(f"\n=======================================================")
    print(f"  AI AGENT KINH DOANH TUẦN - CÔNG TY BÌNH MINH")
    print(f"  Máy chủ Localhost đang chạy tại:")
    print(f"  👉 http://{args.host}:{args.port}")
    print(f"  👉 http://localhost:{args.port}")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang dừng máy chủ...")
        server.server_close()


if __name__ == "__main__":
    main()
