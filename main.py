import gc
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta

import kuzu
import pyarrow as pa
from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplashScreen,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

DB_PATH = "./kuzu_unified_log_db"


# ==============================================================================
# 1. 커스텀 스플래시 윈도우 (이미지 + 하단 프로그레스 바)
# ==============================================================================
class CustomSplashScreen(QWidget):
    def __init__(self, image_path="splash.png"):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 이미지와 하단 프로그레스바를 감쌀 프레임
        container = QFrame()
        container.setStyleSheet(
            """
            QFrame {
                background-color: #0d131d;
                border: 1px solid #1a2638;
                border-radius: 12px;
            }
        """
        )
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(10, 10, 10, 15)
        container_layout.setSpacing(10)

        # 이미지 표시 라벨
        self.lbl_image = QLabel()
        if os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            # 스플래시 해상도에 맞게 스케일링
            self.lbl_image.setPixmap(
                pixmap.scaled(
                    720,
                    405,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.lbl_image.setText("DATA INSIGHT ANALYTICS\n데이터 인사이트 분석")
            self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_image.setStyleSheet(
                "color: #00d2d3; font-size: 24px; font-weight: bold; min-height: 300px;"
            )

        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.lbl_image)

        # 상태 메시지 라벨
        self.lbl_status = QLabel("시스템 초기화 진행 중...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #8c9ba5; font-size: 12px; font-weight: bold; background: transparent; border: none;"
        )
        container_layout.addWidget(self.lbl_status)

        # 하단 프로그레스 바
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                background-color: #1a2433;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0052d4, stop:0.5 #4364f7, stop:1 #6fb1fc);
                border-radius: 4px;
            }
        """
        )
        container_layout.addWidget(self.progress_bar)

        main_layout.addWidget(container)
        self.adjustSize()

        # 화면 중앙 배치
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) // 2, (screen.height() - size.height()) // 2)

    def update_progress(self, message, value):
        self.lbl_status.setText(message)
        self.progress_bar.setValue(value)


# ==============================================================================
# 2. 메인 초기화 작업 백그라운드 워커 Thread
# ==============================================================================
class InitWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

    def run(self):
        self.progress.emit("데이터베이스 연결 준비 중...", 20)
        time.sleep(0.3)

        self.progress.emit("데이터베이스 스키마 검증 및 연결...", 50)
        self.main_window.init_database_safely()
        time.sleep(0.3)

        self.progress.emit("사용자 인터페이스 구성 요소 로딩 중...", 80)
        time.sleep(0.2)

        self.progress.emit("초기화 완료!", 100)
        time.sleep(0.2)

        self.finished.emit()


# ==============================================================================
# 3. 데이터베이스 및 헬퍼 함수
# ==============================================================================
def parse_clean_timestamp(ts_str: str) -> str:
    if not ts_str:
        return "1970-01-01 00:00:00"
    ts = ts_str.replace("T", " ").replace(",", ".")
    ts = ts.split("+")[0].split(".")[0].strip()
    return ts


def create_schema(conn):
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Thread(name STRING, PRIMARY KEY (name))")
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Exception(id STRING, type STRING, message STRING, stackTrace STRING, timestamp TIMESTAMP, PRIMARY KEY (id))"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Method(fullName STRING, name STRING, isFramework BOOLEAN, PRIMARY KEY (fullName))"
    )
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Class(name STRING, PRIMARY KEY (name))")

    conn.execute("CREATE REL TABLE IF NOT EXISTS RAISED(FROM Thread TO Exception)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS OCCURRED_IN(FROM Exception TO Method)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS BELONGS_TO(FROM Method TO Class)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS CALLS(FROM Method TO Method)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS CAUSED_BY(FROM Exception TO Exception)")


# ==============================================================================
# 4. 로그 파싱 및 진단 워커 Thread
# ==============================================================================
class LogParseWorker(QThread):
    progress = pyqtSignal(str, int)
    pattern_detected = pyqtSignal(str)
    finished = pyqtSignal(bool, int)

    def __init__(self, file_path, db_path):
        super().__init__()
        self.file_path = file_path
        self.db_path = db_path

    def run(self):
        self.progress.emit("기존 DB 데이터 자동 초기화 중...", 3)
        if os.path.exists(self.db_path):
            try:
                if os.path.isdir(self.db_path):
                    shutil.rmtree(self.db_path)
                else:
                    os.remove(self.db_path)
            except Exception as e:
                print(f"자동 DB 초기화 중 파일 삭제 실패: {e}")

        self.progress.emit("신규 데이터베이스 스키마 구성 중...", 7)
        try:
            db = kuzu.Database(self.db_path)
            conn = kuzu.Connection(db)
            create_schema(conn)
        except Exception as e:
            print(f"워커 DB 초기화 및 연결 실패: {e}")
            self.finished.emit(False, 0)
            return

        PATTERNS = [
            {
                "name": "Spring Boot 로그 포맷",
                "re": re.compile(
                    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(?:[+-]\d{2}:\d{2})?)\s+(ERROR|WARN|INFO|DEBUG|TRACE)\s+(\d+)\s+---\s+\[([^\]]+)\]\s+([\w\.\$]+)\s+:\s+(.*)"
                ),
                "parse": lambda m: (
                    parse_clean_timestamp(m.group(1)),
                    m.group(4).strip(),
                    m.group(5),
                    m.group(6),
                    m.group(2),
                ),
            },
            {
                "name": "Tomcat / Standard Log4j 로그 포맷",
                "re": re.compile(
                    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{3})?)\s+\[([^\]]+)\]\s+(ERROR|WARN|INFO|DEBUG|TRACE)\s+([\w\.]+)\s+-\s+(.*)"
                ),
                "parse": lambda m: (
                    parse_clean_timestamp(m.group(1)),
                    m.group(2).strip(),
                    m.group(4),
                    m.group(5),
                    m.group(3),
                ),
            },
            {
                "name": "WildFly / JBoss server.log 포맷",
                "re": re.compile(
                    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d{3})?)\s+(ERROR|WARN|INFO|DEBUG|TRACE)\s+\[([^\]]+)\]\s+\(([^\)]+)\)\s+(.*)"
                ),
                "parse": lambda m: (
                    parse_clean_timestamp(m.group(1)),
                    m.group(4).strip(),
                    m.group(3),
                    m.group(5),
                    m.group(2),
                ),
            },
        ]

        FRAMEWORK_PACKAGES = (
            "java.",
            "javax.",
            "org.springframework.",
            "org.apache.",
            "com.zaxxer.",
            "org.hibernate.",
            "sun.",
            "jdk.",
        )

        if not os.path.exists(self.file_path):
            conn.close()
            self.finished.emit(False, 0)
            return

        file_size = os.path.getsize(self.file_path)
        if file_size == 0:
            conn.close()
            self.finished.emit(False, 0)
            return

        self.progress.emit("로그 패턴 탐색 중...", 10)
        detected_pattern = None

        with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
            for idx, line in enumerate(f):
                if idx > 500:
                    break
                for p in PATTERNS:
                    if p["re"].match(line):
                        detected_pattern = p
                        break
                if detected_pattern:
                    break

        if not detected_pattern:
            conn.close()
            del conn, db
            gc.collect()
            self.finished.emit(False, 0)
            return

        self.pattern_detected.emit(detected_pattern["name"])

        threads_set = set()
        classes_set = set()
        methods_dict = {}
        exceptions_dict = {}

        raised_set = set()
        occurred_in_set = set()
        belongs_to_set = set()
        calls_set = set()
        caused_by_set = set()

        parsed_error_count = 0
        bytes_read = 0

        def process_context(ctx):
            nonlocal parsed_error_count
            if not ctx:
                return

            parsed_error_count += 1
            root_ex_id = ctx["root_ex_id"]
            clean_ts = ctx["clean_timestamp"]

            try:
                dt_obj = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt_obj = datetime(1970, 1, 1, 0, 0, 0)

            full_stack_trace = "\n".join(ctx["raw_stack_trace_lines"][:100])

            t_name = ctx["thread_name"]
            threads_set.add(t_name)
            exceptions_dict[root_ex_id] = (
                root_ex_id,
                ctx["ex_type"],
                ctx["ex_msg"][:1000],
                full_stack_trace,
                dt_obj,
            )
            raised_set.add((t_name, root_ex_id))

            parent_id = root_ex_id
            for c_id, c_type, c_msg in ctx["caused_list"]:
                exceptions_dict[c_id] = (c_id, c_type, c_msg[:1000], "", dt_obj)
                caused_by_set.add((parent_id, c_id))
                parent_id = c_id

            target_occ_id = parent_id
            call_chain = ctx["call_chain"]

            if call_chain and target_occ_id:
                target_occ = next((item for item in call_chain if not item[3]), call_chain[0])
                occ_class, occ_method, occ_full, is_fw = target_occ

                classes_set.add(occ_class)
                methods_dict[occ_full] = (occ_full, occ_method, is_fw)
                belongs_to_set.add((occ_full, occ_class))
                occurred_in_set.add((target_occ_id, occ_full))

                for k in range(min(len(call_chain) - 1, 15)):
                    callee_class, callee_method, callee_full, callee_fw = call_chain[k]
                    caller_class, caller_method, caller_full, caller_fw = call_chain[k + 1]

                    classes_set.add(callee_class)
                    methods_dict[callee_full] = (callee_full, callee_method, callee_fw)
                    belongs_to_set.add((callee_full, callee_class))

                    classes_set.add(caller_class)
                    methods_dict[caller_full] = (caller_full, caller_method, caller_fw)
                    belongs_to_set.add((caller_full, caller_class))

                    calls_set.add((caller_full, callee_full))

        current_ctx = None
        caused_seq = 0

        with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_idx, line in enumerate(f):
                bytes_read += len(line.encode("utf-8"))

                if line_idx % 5000 == 0:
                    percent = int(12 + (bytes_read / file_size) * 73)
                    self.progress.emit(f"로그 파싱 중... ({line_idx:,}줄 읽음)", percent)

                match = detected_pattern["re"].match(line)
                if match:
                    clean_timestamp, thread_name, logger, raw_msg, log_level = detected_pattern[
                        "parse"
                    ](match)

                    if log_level == "ERROR":
                        if current_ctx:
                            process_context(current_ctx)

                        if " : " in raw_msg:
                            parts = raw_msg.split(" : ", 1)
                            ex_type, ex_msg = parts[0], parts[1]
                        elif " - " in raw_msg:
                            parts = raw_msg.split(" - ", 1)
                            ex_type, ex_msg = parts[0], parts[1]
                        elif ":" in raw_msg:
                            parts = raw_msg.split(":", 1)
                            ex_type, ex_msg = parts[0].strip(), parts[1].strip()
                        else:
                            ex_type = logger.split(".")[-1]
                            ex_msg = raw_msg

                        root_ex_id = f"err_line_{line_idx}"
                        caused_seq = 0
                        current_ctx = {
                            "thread_name": thread_name,
                            "root_ex_id": root_ex_id,
                            "ex_type": ex_type,
                            "ex_msg": ex_msg,
                            "clean_timestamp": clean_timestamp,
                            "raw_stack_trace_lines": [line.strip()],
                            "call_chain": [],
                            "caused_list": [],
                        }
                    else:
                        if current_ctx:
                            process_context(current_ctx)
                            current_ctx = None
                else:
                    if current_ctx:
                        line_stripped = line.strip()

                        if line_stripped.startswith("Caused by:"):
                            if len(current_ctx["raw_stack_trace_lines"]) < 100:
                                current_ctx["raw_stack_trace_lines"].append(line.rstrip())

                            caused_seq += 1
                            caused_content = line_stripped[10:].strip()
                            if ":" in caused_content:
                                c_type, c_msg = caused_content.split(":", 1)
                                c_type, c_msg = c_type.strip(), c_msg.strip()
                            else:
                                c_type, c_msg = caused_content, ""

                            caused_ex_id = f"caused_line_{line_idx}_seq_{caused_seq}"
                            current_ctx["caused_list"].append((caused_ex_id, c_type, c_msg))

                        elif line_stripped.startswith("at "):
                            if len(current_ctx["raw_stack_trace_lines"]) < 100:
                                current_ctx["raw_stack_trace_lines"].append(line.rstrip())

                            method_raw = line_stripped[3:].split("(")[0].strip()
                            if "." in method_raw:
                                class_name, method_name = method_raw.rsplit(".", 1)
                                full_method = f"{class_name}.{method_name}"
                                is_fw = class_name.startswith(FRAMEWORK_PACKAGES)

                                if len(current_ctx["call_chain"]) < 30:
                                    current_ctx["call_chain"].append(
                                        (class_name, method_name, full_method, is_fw)
                                    )

                        elif line.startswith("\t") or line.startswith("   "):
                            if len(current_ctx["raw_stack_trace_lines"]) < 100:
                                current_ctx["raw_stack_trace_lines"].append(line.rstrip())

            if current_ctx:
                process_context(current_ctx)

        self.progress.emit("DB 고속 벌크 인덱싱(COPY FROM) 진행 중...", 88)

        if threads_set:
            t_table = pa.Table.from_arrays(
                [pa.array(list(threads_set), type=pa.string())], names=["name"]
            )
            conn.execute("COPY Thread FROM t_table")

        if classes_set:
            c_table = pa.Table.from_arrays(
                [pa.array(list(classes_set), type=pa.string())], names=["name"]
            )
            conn.execute("COPY Class FROM c_table")

        if methods_dict:
            m_full = [v[0] for v in methods_dict.values()]
            m_name = [v[1] for v in methods_dict.values()]
            m_fw = [v[2] for v in methods_dict.values()]
            m_table = pa.Table.from_arrays(
                [
                    pa.array(m_full, type=pa.string()),
                    pa.array(m_name, type=pa.string()),
                    pa.array(m_fw, type=pa.bool_()),
                ],
                names=["fullName", "name", "isFramework"],
            )
            conn.execute("COPY Method FROM m_table")

        if exceptions_dict:
            e_ids = [v[0] for v in exceptions_dict.values()]
            e_types = [v[1] for v in exceptions_dict.values()]
            e_msgs = [v[2] for v in exceptions_dict.values()]
            e_sts = [v[3] for v in exceptions_dict.values()]
            e_tss = [v[4] for v in exceptions_dict.values()]
            e_table = pa.Table.from_arrays(
                [
                    pa.array(e_ids, type=pa.string()),
                    pa.array(e_types, type=pa.string()),
                    pa.array(e_msgs, type=pa.string()),
                    pa.array(e_sts, type=pa.string()),
                    pa.array(e_tss, type=pa.timestamp("us")),
                ],
                names=["id", "type", "message", "stackTrace", "timestamp"],
            )
            conn.execute("COPY Exception FROM e_table")

        if raised_set:
            r_table = pa.Table.from_arrays(
                [
                    pa.array([v[0] for v in raised_set], type=pa.string()),
                    pa.array([v[1] for v in raised_set], type=pa.string()),
                ],
                names=["from", "to"],
            )
            conn.execute("COPY RAISED FROM r_table")

        if occurred_in_set:
            o_table = pa.Table.from_arrays(
                [
                    pa.array([v[0] for v in occurred_in_set], type=pa.string()),
                    pa.array([v[1] for v in occurred_in_set], type=pa.string()),
                ],
                names=["from", "to"],
            )
            conn.execute("COPY OCCURRED_IN FROM o_table")

        if belongs_to_set:
            b_table = pa.Table.from_arrays(
                [
                    pa.array([v[0] for v in belongs_to_set], type=pa.string()),
                    pa.array([v[1] for v in belongs_to_set], type=pa.string()),
                ],
                names=["from", "to"],
            )
            conn.execute("COPY BELONGS_TO FROM b_table")

        if calls_set:
            c_table = pa.Table.from_arrays(
                [
                    pa.array([v[0] for v in calls_set], type=pa.string()),
                    pa.array([v[1] for v in calls_set], type=pa.string()),
                ],
                names=["from", "to"],
            )
            conn.execute("COPY CALLS FROM c_table")

        if caused_by_set:
            cb_table = pa.Table.from_arrays(
                [
                    pa.array([v[0] for v in caused_by_set], type=pa.string()),
                    pa.array([v[1] for v in caused_by_set], type=pa.string()),
                ],
                names=["from", "to"],
            )
            conn.execute("COPY CAUSED_BY FROM cb_table")

        self.progress.emit("DB 자원 정리 중...", 98)
        conn.close()
        del conn, db
        gc.collect()

        self.progress.emit("분석 완료!", 100)
        is_success = parsed_error_count > 0
        self.finished.emit(is_success, parsed_error_count)


class DiagnosisWorker(QThread):
    finished = pyqtSignal(str, list, list, list)

    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            db = kuzu.Database(self.db_path)
            conn = kuzu.Connection(db)

            time_query = "MATCH (ex:Exception) RETURN Min(ex.timestamp) as start_time, Max(ex.timestamp) as end_time, Count(ex) as total_cnt"
            res = conn.execute(time_query)
            if not res.has_next():  # type:ignore
                conn.close()
                self.finished.emit("장애 데이터를 찾을 수 없습니다.", [], [], [])
                return

            start_t, end_t, total_cnt = res.get_next()  # type:ignore
            if total_cnt == 0:
                conn.close()
                self.finished.emit("분석된 Exception 로그가 존재하지 않습니다.", [], [], [])
                return

            chart_10step_data = []
            try:
                dt_start = (
                    datetime.strptime(str(start_t).split(".")[0], "%Y-%m-%d %H:%M:%S")
                    if isinstance(start_t, str)
                    else start_t
                )
                dt_end = (
                    datetime.strptime(str(end_t).split(".")[0], "%Y-%m-%d %H:%M:%S")
                    if isinstance(end_t, str)
                    else end_t
                )
                total_duration = (dt_end - dt_start).total_seconds()

                if total_duration <= 0:
                    total_duration = 1.0

                step_sec = total_duration / 10.0

                temp_data = []
                for step in range(10):
                    step_s = dt_start + timedelta(seconds=step_sec * step)
                    step_e = dt_start + timedelta(seconds=step_sec * (step + 1))

                    chart_q = """
                    MATCH (ex:Exception)
                    WHERE ex.timestamp >= timestamp($s_time) AND ex.timestamp <= timestamp($e_time)
                    RETURN Count(ex)
                    """
                    res_chart = conn.execute(
                        chart_q,
                        {
                            "s_time": step_s.strftime("%Y-%m-%d %H:%M:%S"),
                            "e_time": step_e.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                    c_cnt = res_chart.get_next()[0] if res_chart.has_next() else 0  # type:ignore

                    time_lbl = f"{step_s.strftime('%H:%M')}~{step_e.strftime('%H:%M')}"

                    pct = int((c_cnt / total_cnt) * 100) if total_cnt > 0 else 0  # type:ignore
                    temp_data.append((step + 1, time_lbl, c_cnt, pct))

                chart_10step_data = list(reversed(temp_data))

            except Exception as chart_err:
                print(f"10단계 차트 산출 오류: {chart_err}")

            thread_query = "MATCH (t:Thread) RETURN Count(t)"
            res_thread = conn.execute(thread_query)
            total_threads = res_thread.get_next()[0] if res_thread.has_next() else 0  # type:ignore

            db_cnt, net_cnt, auth_cnt, app_cnt = 0, 0, 0, 0
            type_query = "MATCH (ex:Exception) RETURN ex.type, ex.message, Count(ex) as cnt"
            res_type = conn.execute(type_query)
            type_summary = ""

            while res_type.has_next():  # type:ignore
                ex_type, ex_msg, cnt = res_type.get_next()  # type:ignore
                type_summary += f"     > {ex_type} ({cnt}건)\n"

                if any(
                    k in ex_type or k in ex_msg
                    for k in ["SQL", "Timeout", "Hikari", "Connection", "Deadlock", "Constraint"]
                ):
                    db_cnt += cnt  # type:ignore
                elif any(
                    k in ex_type or k in ex_msg
                    for k in [
                        "ConnectException",
                        "SocketTimeout",
                        "HttpClient",
                        "UnknownHost",
                        "HttpServerError",
                        "SFTP",
                    ]
                ):
                    net_cnt += cnt  # type:ignore
                elif any(
                    k in ex_type or k in ex_msg
                    for k in [
                        "Unauthorized",
                        "OAuth2",
                        "JWT",
                        "ExpiredToken",
                        "SignatureException",
                        "AccessDenied",
                    ]
                ):
                    auth_cnt += cnt  # type:ignore
                else:
                    app_cnt += cnt  # type:ignore

            db_pct = int((db_cnt / total_cnt) * 100)  # type:ignore
            net_pct = int((net_cnt / total_cnt) * 100)  # type:ignore
            auth_pct = int((auth_cnt / total_cnt) * 100)  # type:ignore
            app_pct = max(0, 100 - (db_pct + net_pct + auth_pct))

            max_pct = max(db_pct, net_pct, auth_pct, app_pct)
            diagnosis_tag, recommendation = "", ""

            if max_pct == db_pct and db_pct > 0:
                diagnosis_tag = "🔴 DATABASE BOTTLE_NECK (데이터베이스 장애)"
                recommendation = "   1. [커넥션 풀 고갈]: HikariCP/DataSource 커넥션 점유 점검.\n   2. [슬로우 쿼리]: 대형 조인 및 인덱스 누락 점검."
            elif max_pct == net_pct and net_pct > 0:
                diagnosis_tag = "⚡ EXTERNAL NETWORK OUTAGE (외부 연동망 및 SFTP/네트워크 장애)"
                recommendation = "   1. [연동 경로 확인]: 타겟 서버/SFTP 경로 점검.\n   2. [타임아웃 설정]: Timeout 시간 단축 제어."
            elif max_pct == auth_pct and auth_pct > 0:
                diagnosis_tag = "🔑 AUTHENTICATION & SECURITY FAILURE (인증 및 보안 장애)"
                recommendation = "   1. [OAuth Secret 만료]: 인증 토큰 유효기간 점검.\n   2. [JWT 서명 오류]: Key 값 변경 여부 확인."
            else:
                diagnosis_tag = "💻 APPLICATION LOGIC ERROR (소스코드 내부 결함)"
                recommendation = "   1. [런타임 Exception]: NullPointer 예외 처리 보완.\n   2. [배포 이력]: 최근 Git 커밋 내역 체크."

            detailed_report = (
                f"=========================================================================================================\n"
                f" [장애 사후 진단서]  발생 시간대: {str(start_t).split('.')[0]} ~ {str(end_t).split('.')[0]}\n"
                f"=========================================================================================================\n"
                f" ■ 인프라 및 애플리케이션 영향도 검사 지표\n"
                f"   - 총 누적 예외 발생수 : {total_cnt}건\n"
                f"   - 영향받은 워커 스레드 수 : {total_threads}개\n"
                f"   - 자동 진단 분류 등급 : {diagnosis_tag}\n\n"
                f" ■ 도메인별 장애 유발 지분율 (RCA 지표)\n"
                f"   ├─ [데이터베이스 영역] : {db_pct}%\n"
                f"   ├─ [외부 연동 네트워크 및 SFTP] : {net_pct}%\n"
                f"   ├─ [인증 및 OAuth 보안] : {auth_pct}%\n"
                f"   └─ [순수 애플리케이션] : {app_pct}%\n\n"
                f" ■ 검출된 최다 빈도 예외 클래스 명세\n"
                f"{type_summary}\n"
                f" ■ 엔지니어 트러블슈팅 권고사항:\n"
                f"{recommendation}"
            )

            root_data = []
            root_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN Count(ex) as cnt, m.fullName, ex.type ORDER BY cnt DESC LIMIT 10"
            res_root = conn.execute(root_query)
            while res_root.has_next():  # type:ignore
                cnt, method_name, ex_type = res_root.get_next()  # type:ignore
                root_data.append((str(cnt), method_name, ex_type))

            recent_data = []
            recent_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN ex.timestamp, m.fullName, ex.type ORDER BY ex.timestamp DESC LIMIT 10"
            res_recent = conn.execute(recent_query)
            while res_recent.has_next():  # type:ignore
                ts, method_name, ex_type = res_recent.get_next()  # type:ignore
                time_str = str(ts).split(".")[0]
                recent_data.append((time_str, method_name, ex_type))

            conn.close()
            del conn, db
            gc.collect()

            self.finished.emit(detailed_report, root_data, recent_data, chart_10step_data)
        except Exception as e:
            print(f"진단 분석 중 오류 발생: {e}")
            self.finished.emit(f"진단 중 오류 발생: {e}", [], [], [])


# ==============================================================================
# 5. 메인 윈도우 클래스
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            "통합 WAS/애플리케이션 로그 자동 분석기 (고속 Bulk Insert 엔진 적용) v1.1.0"
        )
        self.setGeometry(100, 100, 1450, 950)

        self.db = None
        self.conn = None

        self.setup_ui()

    def close_db_connection(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
            self.db = None
        gc.collect()
        time.sleep(0.3)

    def init_database_safely(self):
        try:
            self.db = kuzu.Database(DB_PATH)
            time.sleep(0.2)
            self.conn = kuzu.Connection(self.db)
            create_schema(self.conn)
        except Exception as e:
            print(f"DB 초기 연결 실패, 재시도: {e}")
            self.close_db_connection()
            self.db = kuzu.Database(DB_PATH)
            self.conn = kuzu.Connection(self.db)
            create_schema(self.conn)

    def reset_ui_components(self):
        self.txt_summary.clear()
        self.table_root.setRowCount(0)
        self.table_recent.setRowCount(0)
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])

        for bar, lbl in self.chart_bars:
            bar.setValue(0)
            lbl.setText("0건 (0%)")

    def manual_reset_database(self):
        reply = QMessageBox.question(
            self,
            "DB 초기화",
            "정말 데이터베이스를 수동 초기화하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.close_db_connection()

            if os.path.exists(DB_PATH):
                try:
                    if os.path.isdir(DB_PATH):
                        shutil.rmtree(DB_PATH)
                    else:
                        os.remove(DB_PATH)
                except Exception as e:
                    print(f"수동 초기화 중 삭제 실패: {e}")

            self.init_database_safely()
            self.reset_ui_components()

            self.btn_upload.setText(
                "📁 통합 로그 파일 선택 및 자동 분석 시작 (Spring / Tomcat / WildFly)"
            )
            self.lbl_detected_pattern.setText("🔍 감지된 로그 포맷: [대기 중]")
            self.lbl_status.setText("데이터베이스가 성공적으로 수동 초기화되었습니다.")
            self.progress_bar.setValue(0)

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(6)

        top_bar = QHBoxLayout()
        self.btn_upload = QPushButton(
            "📁 통합 로그 파일 선택 및 자동 분석 시작 (Spring / Tomcat / WildFly)"
        )
        self.btn_upload.clicked.connect(self.upload_log)
        self.btn_upload.setStyleSheet(
            "background-color: #1e3d59; color: white; font-weight: bold; padding: 12px; font-size: 13px; border-radius: 4px;"
        )

        self.btn_reset = QPushButton("🧹 수동 DB 초기화")
        self.btn_reset.clicked.connect(self.manual_reset_database)
        self.btn_reset.setStyleSheet(
            "background-color: #ff6e40; color: white; font-weight: bold; padding: 12px; font-size: 13px; border-radius: 4px;"
        )

        top_bar.addWidget(self.btn_upload, 4)
        top_bar.addWidget(self.btn_reset, 1)
        main_layout.addLayout(top_bar)

        status_box = QHBoxLayout()
        self.lbl_detected_pattern = QLabel("🔍 감지된 로그 포맷: [대기 중]")
        self.lbl_detected_pattern.setStyleSheet(
            "color: #27ae60; font-weight: bold; font-size: 12px;"
        )

        self.lbl_status = QLabel("로그 파일을 선택하면 기존 DB를 자동 비우고 분석을 시작합니다.")
        self.lbl_status.setStyleSheet("color: #7f8c8d; font-style: italic;")

        status_box.addWidget(self.lbl_detected_pattern, 1)
        status_box.addWidget(self.lbl_status, 2)
        main_layout.addLayout(status_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        top_report_box = QVBoxLayout()
        top_report_box.setSpacing(2)
        top_report_box.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(
            "<b>📝 인메모리 마이닝 기반 장애 정밀 요약 보고서 (Post-Mortem Report)</b>"
        )
        title_lbl.setStyleSheet("margin: 0px; padding: 0px;")
        top_report_box.addWidget(title_lbl)

        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setFixedHeight(200)
        self.txt_summary.setStyleSheet(
            "background-color: #2f3640; color: #f5f6fa; font-family: Consolas, 'Courier New'; font-size: 12px; border: 1px solid #1e222b; padding: 8px; margin-top: 0px;"
        )
        top_report_box.addWidget(self.txt_summary)
        main_layout.addLayout(top_report_box)

        chart_group = QVBoxLayout()
        chart_group.setSpacing(2)

        chart_title = QLabel(
            "<b>📊 10단계 시간대별 예외 발생 분포 (Timeline Distribution - 역순 정렬)</b>"
        )
        chart_group.addWidget(chart_title)

        chart_frame = QFrame()
        chart_frame.setStyleSheet(
            "background-color: #23272e; border: 1px solid #1e222b; border-radius: 4px;"
        )
        chart_grid = QGridLayout(chart_frame)

        chart_grid.setContentsMargins(4, 4, 4, 4)
        chart_grid.setHorizontalSpacing(4)
        chart_grid.setVerticalSpacing(2)

        self.chart_bars = []
        self.chart_time_lbls = []

        for i in range(10):
            row = 0 if i < 5 else 1
            col = i if i < 5 else i - 5

            cell_box = QHBoxLayout()
            cell_box.setContentsMargins(0, 0, 0, 0)
            cell_box.setSpacing(2)

            time_lbl = QLabel(f"T{10 - i}: --:--~--:--")
            time_lbl.setStyleSheet("color: #00d2d3; font-weight: bold; font-size: 10px;")
            time_lbl.setFixedWidth(92)

            p_bar = QProgressBar()
            p_bar.setFixedHeight(10)
            p_bar.setMinimumWidth(20)
            p_bar.setTextVisible(False)
            p_bar.setStyleSheet(
                """
                QProgressBar { border: None; background-color: #353b48; border-radius: 2px; }
                QProgressBar::chunk { background-color: #ff4757; border-radius: 2px; }
            """
            )

            val_lbl = QLabel("0건 (0%)")
            val_lbl.setStyleSheet("color: #dcdde1; font-size: 10px;")
            val_lbl.setFixedWidth(52)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            cell_box.addWidget(time_lbl)
            cell_box.addWidget(p_bar, 1)
            cell_box.addWidget(val_lbl)

            chart_grid.addLayout(cell_box, row, col)

            self.chart_bars.append((p_bar, val_lbl))
            self.chart_time_lbls.append(time_lbl)

        chart_group.addWidget(chart_frame)
        main_layout.addLayout(chart_group)

        bottom_layout = QHBoxLayout()

        bottom_left_box = QVBoxLayout()
        bottom_left_box.addWidget(
            QLabel("<b>🔥 근본 원인(Root Cause) 에러 코드 랭킹 (누적 다빈도)</b>")
        )
        self.table_root = QTableWidget(0, 3)
        self.table_root.setHorizontalHeaderLabels(
            ["발생건수", "근본 원인 메서드 (Root Method)", "주요 예외 클래스"]
        )
        self.table_root.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # type:ignore
        bottom_left_box.addWidget(self.table_root, 1)

        bottom_left_box.addWidget(QLabel("<b>🚨 최근 시간대별 에러 코드 랭킹 (최근 발생 순)</b>"))
        self.table_recent = QTableWidget(0, 3)
        self.table_recent.setHorizontalHeaderLabels(
            ["최근 발생 시각", "발생 메서드 (Recent Method)", "예외 클래스"]
        )
        self.table_recent.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # type:ignore
        bottom_left_box.addWidget(self.table_recent, 1)

        bottom_layout.addLayout(bottom_left_box, 1)

        bottom_right_box = QVBoxLayout()
        bottom_right_box.addWidget(
            QLabel("<b>장애 파급 효과 및 전파 체인 (상세 스택트레이스 포함)</b>")
        )
        self.tree_view = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # type:ignore
        bottom_right_box.addWidget(self.tree_view)
        bottom_layout.addLayout(bottom_right_box, 1)

        main_layout.addLayout(bottom_layout, 1)

        self.table_root.cellClicked.connect(self.on_root_table_clicked)
        self.table_recent.cellClicked.connect(self.on_recent_table_clicked)

    def on_root_table_clicked(self, row, column):
        item = self.table_root.item(row, 1)
        if item:
            self.load_error_propagation_chain(item.text())

    def on_recent_table_clicked(self, row, column):
        item = self.table_recent.item(row, 1)
        if item:
            self.load_error_propagation_chain(item.text())

    def load_error_propagation_chain(self, method_name):
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])

        if not self.conn:
            self.init_database_safely()

        root_node = QStandardItem(f"🎯 Target Method: {method_name}")

        try:
            ex_query = """
            MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method {fullName: $method_name})
            RETURN ex.id, ex.type, ex.message, ex.stackTrace, ex.timestamp
            ORDER BY ex.timestamp DESC LIMIT 5
            """
            res_ex = self.conn.execute(ex_query, {"method_name": method_name})  # type:ignore

            has_data = False
            while res_ex.has_next():  # type:ignore
                has_data = True
                ex_id, ex_type, ex_msg, stack_trace, ts = res_ex.get_next()  # type:ignore

                ex_item = QStandardItem(f"🚨 [{str(ts).split('.')[0]}] {ex_type}: {ex_msg}")

                cb_query = """
                MATCH (ex:Exception {id: $ex_id})-[:CAUSED_BY]->(child:Exception)
                RETURN child.type, child.message
                """
                res_cb = self.conn.execute(cb_query, {"ex_id": ex_id})  # type:ignore
                while res_cb.has_next():  # type:ignore
                    c_type, c_msg = res_cb.get_next()  # type:ignore
                    ex_item.appendRow(QStandardItem(f"  └─ 💥 Caused by: {c_type}: {c_msg}"))

                if stack_trace:
                    st_item = QStandardItem("  📜 Stack Trace Sample")
                    for line in stack_trace.split("\n")[:15]:
                        st_item.appendRow(QStandardItem(f"      {line.strip()}"))
                    ex_item.appendRow(st_item)

                root_node.appendRow(ex_item)

            if not has_data:
                root_node.appendRow(
                    QStandardItem("  ℹ️ 연결된 상세 스택트레이스 데이터가 없습니다.")
                )

            calls_query = """
            MATCH (caller:Method)-[:CALLS]->(m:Method {fullName: $method_name})
            RETURN DISTINCT caller.fullName
            LIMIT 5
            """
            res_calls = self.conn.execute(calls_query, {"method_name": method_name})  # type:ignore
            while res_calls.has_next():  # type:ignore
                caller_full = res_calls.get_next()[0]  # type:ignore
                root_node.appendRow(QStandardItem(f"  ⬆️ Called By: {caller_full}"))

            self.tree_model.appendRow(root_node)
            self.tree_view.expandAll()

        except Exception as e:
            print(f"전파 체인 로딩 오류: {e}")

    def upload_log(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Log File Selection", "", "Log Files (*.log *.out);;All Files (*)"
        )
        if file_path:
            self.btn_upload.setEnabled(False)
            self.btn_upload.setText("⏳ 이전 DB 초기화 및 새로운 로그 분석 진행 중...")
            self.lbl_detected_pattern.setText("🔍 감지된 로그 포맷: [패턴 탐색 중...]")
            self.progress_bar.setValue(0)

            self.reset_ui_components()
            self.close_db_connection()

            self.worker = LogParseWorker(file_path, DB_PATH)
            self.worker.progress.connect(self.on_progress_update)
            self.worker.pattern_detected.connect(self.on_pattern_detected)
            self.worker.finished.connect(self.on_parse_finished)
            self.worker.start()

    def on_progress_update(self, msg, value):
        self.lbl_status.setText(msg)
        self.progress_bar.setValue(value)

    def on_pattern_detected(self, pattern_name):
        self.lbl_detected_pattern.setText(f"🔍 감지된 로그 포맷: [{pattern_name}]")

    def on_parse_finished(self, is_success, parsed_count):
        self.init_database_safely()

        if not is_success or parsed_count == 0:
            self.btn_upload.setEnabled(True)
            self.btn_upload.setText(
                "📁 통합 로그 파일 선택 및 자동 분석 시작 (Spring / Tomcat / WildFly)"
            )
            self.lbl_detected_pattern.setText("🔍 감지된 로그 포맷: [인식 실패]")
            self.lbl_status.setText("⚠️ 분석 중단: 일치하는 패턴이 없거나 에러 로그가 없습니다.")
            self.progress_bar.setValue(0)

            QMessageBox.warning(
                self,
                "분석 불가 안내",
                "지정된 로그 파일에서 인식 가능한 에러 패턴을 찾지 못했거나 분석 대상(ERROR) 로그가 존재하지 않습니다.",
                QMessageBox.StandardButton.Ok,
            )
            return

        self.lbl_status.setText("파싱 완료! 정밀 사후 진단 보고서 작성 중...")
        self.close_db_connection()

        self.diag_worker = DiagnosisWorker(DB_PATH)
        self.diag_worker.finished.connect(
            lambda report, r_data, rec_data, c_data: self.on_diagnosis_finished(
                report, r_data, rec_data, c_data, parsed_count
            )
        )
        self.diag_worker.start()

    def on_diagnosis_finished(self, report, root_data, recent_data, chart_data, parsed_count):
        self.init_database_safely()

        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("✅ 분석 완료 (클릭하여 다른 파일 분석)")
        self.lbl_status.setText(f"분석 완벽 종료! (총 {parsed_count}건의 예외 처리 완료)")

        self.txt_summary.setText(report)

        if chart_data:
            for idx, (step_num, time_lbl, cnt, pct) in enumerate(chart_data):
                if idx < len(self.chart_bars):
                    p_bar, val_lbl = self.chart_bars[idx]
                    p_bar.setValue(pct)
                    val_lbl.setText(f"{cnt:,}건 ({pct}%)")
                    self.chart_time_lbls[idx].setText(f"T{step_num}: {time_lbl}")

        self.table_root.setRowCount(0)
        for row, (cnt, method_name, ex_type) in enumerate(root_data):
            self.table_root.insertRow(row)
            self.table_root.setItem(row, 0, QTableWidgetItem(cnt))
            self.table_root.setItem(row, 1, QTableWidgetItem(method_name))
            self.table_root.setItem(row, 2, QTableWidgetItem(ex_type))

        self.table_recent.setRowCount(0)
        for row, (time_str, method_name, ex_type) in enumerate(recent_data):
            self.table_recent.insertRow(row)
            self.table_recent.setItem(row, 0, QTableWidgetItem(time_str))
            self.table_recent.setItem(row, 1, QTableWidgetItem(method_name))
            self.table_recent.setItem(row, 2, QTableWidgetItem(ex_type))


# ==============================================================================
# 6. 애플리케이션 실행 진입점 (Main)
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 1) 커스텀 스플래시 화면 생성 및 표출
    splash = CustomSplashScreen("splash.png")
    splash.show()

    # 2) 메인 윈도우 인스턴스 생성
    main_window = MainWindow()

    # 3) 비동기로 초기화 작업을 진행할 백그라운드 스레드 생성
    init_worker = InitWorker(main_window)
    init_worker.progress.connect(splash.update_progress)

    def on_init_finished():
        # 1. 메인 윈도우를 먼저 화면에 출력
        main_window.show()

        # 2. 메인 윈도우가 완전히 그려지고 렌더링 이벤트를 처리할 수 있도록 동기화
        QApplication.processEvents()

        # 3. 메인 윈도우가 뜬 후 스플래시 창을 완전히 닫음
        splash.close()

    init_worker.finished.connect(on_init_finished)
    init_worker.start()

    sys.exit(app.exec())
