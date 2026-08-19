import gc
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta

import kuzu
import pyarrow as pa
import subprocess
from PyQt6.QtCore import QModelIndex, QSharedMemory, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


# Windows 전용 포커스 이동을 위한 ctypes 임포트
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

# PyInstaller 네이티브 스플래시 라이브러리 모듈 임포트 시도
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

DB_PATH = "./kuzu_unified_log_db"
# 8GB / 16GB 등 사양에 맞게 조정 가능한 Kùzu 버퍼 풀 크기 (예: 4GB)
KUZU_BUFFER_POOL_SIZE = 4 * 1024 * 1024 * 1024


# ==============================================================================
# 0. PyInstaller 동적 경로 및 인코딩/안전 삭제/중복 실행 포커스 헬퍼 함수
# ==============================================================================
def get_resource_path(relative_path: str) -> str:
    """PyInstaller 동결(frozen) 환경과 일반 개발 환경 경로를 통합 처리하는 함수"""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)

        # 1. sys._MEIPASS 임시 디렉터리 확인 (PyInstaller 내부 경로)
        if hasattr(sys, "_MEIPASS"):
            p = os.path.join(sys._MEIPASS, relative_path)
            if os.path.exists(p):
                return p

        # 2. PyInstaller v6+ onedir 빌드 시 _internal 폴더 확인
        p_internal = os.path.join(exe_dir, "_internal", relative_path)
        if os.path.exists(p_internal):
            return p_internal

        # 3. main.exe 실행 파일과 같은 위치의 루트 폴더 확인
        p_exe = os.path.join(exe_dir, relative_path)
        if os.path.exists(p_exe):
            return p_exe

    # 일반 파이썬 script 실행 환경
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def safe_remove_db_path(path: str, retries: int = 5, delay: float = 0.3):
    """DB 폴더/파일 안전 삭제 헬퍼 함수 (파일 잠금 해제 지연 대응)"""
    if not os.path.exists(path):
        return

    for i in range(retries):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            break
        except Exception as e:
            if i == retries - 1:
                print(f"[Warning] DB 경로 삭제 실패 ({path}): {e}")
            time.sleep(delay)


def open_log_file(file_path: str):
    """국내 엔터프라이즈(Windows/Tomcat/WildFly) CP949/EUC-KR Fallback 인코딩 핸들러"""
    try:
        f = open(file_path, "r", encoding="utf-8")
        f.readline()
        f.seek(0)
        return f
    except (UnicodeDecodeError, Exception):
        return open(file_path, "r", encoding="cp949", errors="ignore")


def activate_existing_window(window_title_keyword: str):
    """기존에 실행 중인 프로세스의 윈도우 창을 찾아 최상단으로 끌어오고 포커스 이동"""
    if sys.platform == "win32":
        user32 = ctypes.windll.user32

        def enum_windows_callback(hwnd, extra):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                if window_title_keyword in buff.value:
                    # 최소화 해제 및 최상단 포커스 이동
                    SW_RESTORE = 9
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                    return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)


# ==============================================================================
# 1. 커스텀 스플래시 윈도우 (단일 배경 이미지 및 하단 위젯 오버레이 적용)
# ==============================================================================
class CustomSplashScreen(QWidget):
    def __init__(self, image_path: str = "splash.png"):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        target_width = 600
        target_height = 410
        self.setFixedSize(target_width, target_height)

        self.lbl_bg = QLabel(self)
        self.lbl_bg.setGeometry(0, 0, target_width, target_height)

        real_image_path = get_resource_path(image_path)
        pixmap = QPixmap(real_image_path)

        if os.path.exists(real_image_path) and not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                target_width,
                target_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_bg.setPixmap(scaled_pixmap)
        else:
            self.lbl_bg.setText("DATA INSIGHT ANALYTICS\n데이터 인사이트 분석")
            self.lbl_bg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_bg.setStyleSheet(
                "color: #00d2d3; font-size: 22px; font-weight: bold; background-color: #0d131d; border: 1px solid #2a2b2d; border-radius: 8px;"
            )

        bottom_height = 60
        bottom_container = QWidget(self)
        bottom_container.setGeometry(0, target_height - bottom_height, target_width, bottom_height)
        bottom_container.setStyleSheet("background: transparent;")

        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(20, 6, 20, 10)
        bottom_layout.setSpacing(4)

        self.lbl_status = QLabel("시스템 초기화 진행 중...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_status.setStyleSheet("color: #a0a6b2; font-size: 11px; font-weight: 600; background: transparent; border: none;")
        bottom_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                background-color: #2a2d32;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0052d4, stop:0.5 #4364f7, stop:1 #6fb1fc);
                border-radius: 3px;
            }
        """
        )
        bottom_layout.addWidget(self.progress_bar)

        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - target_width) // 2, (screen.height() - target_height) // 2)

    def update_progress(self, message: str, value: int):
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
        time.sleep(0.1)

        self.progress.emit("데이터베이스 스키마 검증 및 연결...", 50)
        db, conn = None, None
        try:
            db = kuzu.Database(DB_PATH, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
            conn = kuzu.Connection(db)
            create_schema(conn)
        except Exception as e:
            print(f"초기화 DB 스키마 생성 중 예외 발생: {e}")
        finally:
            if conn:
                conn.close()
            if db and hasattr(db, "close"):
                db.close()
            gc.collect()

        self.progress.emit("사용자 인터페이스 구성 요소 로딩 중...", 80)
        time.sleep(0.1)

        self.progress.emit("초기화 완료!", 100)
        time.sleep(0.1)

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


def create_schema(conn: kuzu.Connection):
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Thread(name STRING, PRIMARY KEY (name))")
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Exception(id STRING, type STRING, message STRING, stackTrace STRING, timestamp TIMESTAMP, PRIMARY KEY (id))"
    )
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Method(fullName STRING, name STRING, isFramework BOOLEAN, PRIMARY KEY (fullName))")
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Class(name STRING, PRIMARY KEY (name))")

    conn.execute("CREATE REL TABLE IF NOT EXISTS RAISED(FROM Thread TO Exception)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS OCCURRED_IN(FROM Exception TO Method)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS BELONGS_TO(FROM Method TO Class)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS CALLS(FROM Method TO Method)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS CAUSED_BY(FROM Exception TO Exception)")


# ==============================================================================
# 4. 고속 스트리밍 로그 파싱 및 진단 워커 Thread
# ==============================================================================
class LogParseWorker(QThread):
    progress = pyqtSignal(str, int)
    pattern_detected = pyqtSignal(str)
    finished = pyqtSignal(bool, int)

    def __init__(self, file_path: str, db_path: str, chunk_error_limit: int = 5000):
        super().__init__()
        self.file_path = file_path
        self.db_path = db_path
        self.chunk_error_limit = chunk_error_limit

    def run(self):
        self.progress.emit("기존 DB 데이터 자동 초기화 중...", 3)
        safe_remove_db_path(self.db_path)

        self.progress.emit("신규 데이터베이스 스키마 구성 중...", 7)
        db, conn = None, None
        try:
            db = kuzu.Database(self.db_path, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
            conn = kuzu.Connection(db)
            create_schema(conn)

            PATTERNS = [
                {
                    "name": "Spring Boot 2.x / 3.x 통합 로그 포맷",
                    "re": re.compile(
                        r"^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[\.,]\d{3})?(?:[+-]\d{2}:?\d{2}|Z)?)\s+"
                        r"(ERROR|WARN|INFO|DEBUG|TRACE|FATAL|CRITICAL|EMERGENCY)\s+"
                        r"(\d+|-)\s+---\s+\[([^\]]+)\]\s+"
                        r"([^\s:]+)\s+:\s+(.*)"
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
                    "name": "Tomcat / Standard Log4j2 / Logback 포맷",
                    "re": re.compile(
                        r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[\.,]\d{3})?)\s+"
                        r"\[([^\]]+)\]\s+"
                        r"(ERROR|WARN|INFO|DEBUG|TRACE|FATAL|CRITICAL|EMERGENCY)\s+"
                        r"([^\s\-]+)\s+-\s+(.*)"
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
                        r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:[\.,]\d{3})?)\s+"
                        r"(ERROR|WARN|INFO|DEBUG|TRACE|FATAL|CRITICAL|EMERGENCY)\s+"
                        r"\[([^\]]+)\]\s+\(([^\)]+)\)\s+(.*)"
                    ),
                    "parse": lambda m: (
                        parse_clean_timestamp(m.group(1)),
                        m.group(4).strip(),
                        m.group(3),
                        m.group(5),
                        m.group(2),
                    ),
                },
                {
                    "name": "WebLogic / Generic WAS Standard Out 포맷",
                    "re": re.compile(
                        r"^<(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[\.,]\d{3})?)>\s+"
                        r"<(ERROR|WARN|INFO|DEBUG|TRACE|FATAL|CRITICAL|EMERGENCY|Error|Warning)>\s+"
                        r"<([^>]+)>\s+<([^>]+)>\s+(.*)"
                    ),
                    "parse": lambda m: (
                        parse_clean_timestamp(m.group(1)),
                        m.group(3).strip(),
                        m.group(4),
                        m.group(5),
                        m.group(2).upper(),
                    ),
                },
            ]

            FRAMEWORK_PACKAGES = (
                "java.",
                "javax.",
                "jakarta.",
                "sun.",
                "jdk.",
                "org.springframework.",
                "egovframework.",
                "org.apache.",
                "io.netty.",
                "io.undertow.",
                "org.hibernate.",
                "org.mybatis.",
                "com.zaxxer.",
                "io.lettuce.",
                "org.redisson.",
                "com.fasterxml.jackson.",
                "com.google.gson.",
                "com.netflix.",
                "lombok.",
                "org.slf4j.",
                "ch.qos.logback.",
                "org.apache.logging.",
            )

            if not os.path.exists(self.file_path):
                self.finished.emit(False, 0)
                return

            file_size = os.path.getsize(self.file_path)
            if file_size == 0:
                self.finished.emit(False, 0)
                return

            self.progress.emit("로그 패턴 탐색 중...", 10)
            detected_pattern = None

            with open_log_file(self.file_path) as f:
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
                self.finished.emit(False, 0)
                return

            self.pattern_detected.emit(detected_pattern["name"])

            global_threads_set = set()
            global_classes_set = set()
            global_methods_set = set()

            chunk_threads_set = set()
            chunk_classes_set = set()
            chunk_methods_dict = {}
            chunk_exceptions_dict = {}

            chunk_raised_set = set()
            chunk_occurred_in_set = set()
            chunk_belongs_to_set = set()
            chunk_calls_set = set()
            chunk_caused_by_set = set()

            parsed_error_count = 0
            chunk_error_count = 0
            bytes_read = 0

            def commit_chunk():
                nonlocal chunk_error_count

                new_threads = chunk_threads_set - global_threads_set
                if new_threads:
                    t_table = pa.Table.from_arrays([pa.array(list(new_threads), type=pa.string())], names=["name"])
                    conn.execute("COPY Thread FROM t_table")
                    global_threads_set.update(new_threads)

                new_classes = chunk_classes_set - global_classes_set
                if new_classes:
                    c_table = pa.Table.from_arrays([pa.array(list(new_classes), type=pa.string())], names=["name"])
                    conn.execute("COPY Class FROM c_table")
                    global_classes_set.update(new_classes)

                new_methods_dict = {k: v for k, v in chunk_methods_dict.items() if k not in global_methods_set}
                if new_methods_dict:
                    m_full = [v[0] for v in new_methods_dict.values()]
                    m_name = [v[1] for v in new_methods_dict.values()]
                    m_fw = [v[2] for v in new_methods_dict.values()]
                    m_table = pa.Table.from_arrays(
                        [
                            pa.array(m_full, type=pa.string()),
                            pa.array(m_name, type=pa.string()),
                            pa.array(m_fw, type=pa.bool_()),
                        ],
                        names=["fullName", "name", "isFramework"],
                    )
                    conn.execute("COPY Method FROM m_table")
                    global_methods_set.update(new_methods_dict.keys())

                if chunk_exceptions_dict:
                    e_ids = [v[0] for v in chunk_exceptions_dict.values()]
                    e_types = [v[1] for v in chunk_exceptions_dict.values()]
                    e_msgs = [v[2] for v in chunk_exceptions_dict.values()]
                    e_sts = [v[3] for v in chunk_exceptions_dict.values()]
                    e_tss = [v[4] for v in chunk_exceptions_dict.values()]
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

                if chunk_raised_set:
                    r_table = pa.Table.from_arrays(
                        [
                            pa.array([v[0] for v in chunk_raised_set], type=pa.string()),
                            pa.array([v[1] for v in chunk_raised_set], type=pa.string()),
                        ],
                        names=["from", "to"],
                    )
                    conn.execute("COPY RAISED FROM r_table")

                if chunk_occurred_in_set:
                    o_table = pa.Table.from_arrays(
                        [
                            pa.array([v[0] for v in chunk_occurred_in_set], type=pa.string()),
                            pa.array([v[1] for v in chunk_occurred_in_set], type=pa.string()),
                        ],
                        names=["from", "to"],
                    )
                    conn.execute("COPY OCCURRED_IN FROM o_table")

                if chunk_belongs_to_set:
                    b_table = pa.Table.from_arrays(
                        [
                            pa.array([v[0] for v in chunk_belongs_to_set], type=pa.string()),
                            pa.array([v[1] for v in chunk_belongs_to_set], type=pa.string()),
                        ],
                        names=["from", "to"],
                    )
                    conn.execute("COPY BELONGS_TO FROM b_table")

                if chunk_calls_set:
                    c_table = pa.Table.from_arrays(
                        [
                            pa.array([v[0] for v in chunk_calls_set], type=pa.string()),
                            pa.array([v[1] for v in chunk_calls_set], type=pa.string()),
                        ],
                        names=["from", "to"],
                    )
                    conn.execute("COPY CALLS FROM c_table")

                if chunk_caused_by_set:
                    cb_table = pa.Table.from_arrays(
                        [
                            pa.array([v[0] for v in chunk_caused_by_set], type=pa.string()),
                            pa.array([v[1] for v in chunk_caused_by_set], type=pa.string()),
                        ],
                        names=["from", "to"],
                    )
                    conn.execute("COPY CAUSED_BY FROM cb_table")

                chunk_threads_set.clear()
                chunk_classes_set.clear()
                chunk_methods_dict.clear()
                chunk_exceptions_dict.clear()

                chunk_raised_set.clear()
                chunk_occurred_in_set.clear()
                chunk_belongs_to_set.clear()
                chunk_calls_set.clear()
                chunk_caused_by_set.clear()

                chunk_error_count = 0
                gc.collect()

            def process_context(ctx):
                nonlocal parsed_error_count, chunk_error_count
                if not ctx:
                    return

                parsed_error_count += 1
                chunk_error_count += 1

                root_ex_id = ctx["root_ex_id"]
                clean_ts = ctx["clean_timestamp"]

                try:
                    dt_obj = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt_obj = datetime(1970, 1, 1, 0, 0, 0)

                full_stack_trace = "\n".join(ctx["raw_stack_trace_lines"][:25])

                t_name = ctx["thread_name"]
                chunk_threads_set.add(t_name)
                chunk_exceptions_dict[root_ex_id] = (
                    root_ex_id,
                    ctx["ex_type"],
                    ctx["ex_msg"][:1000],
                    full_stack_trace,
                    dt_obj,
                )
                chunk_raised_set.add((t_name, root_ex_id))

                parent_id = root_ex_id
                for c_id, c_type, c_msg in ctx["caused_list"]:
                    chunk_exceptions_dict[c_id] = (c_id, c_type, c_msg[:1000], "", dt_obj)
                    chunk_caused_by_set.add((parent_id, c_id))
                    parent_id = c_id

                target_occ_id = parent_id
                call_chain = ctx["call_chain"]

                if call_chain and target_occ_id:
                    target_occ = next((item for item in call_chain if not item[3]), call_chain[0])
                    occ_class, occ_method, occ_full, is_fw = target_occ

                    chunk_classes_set.add(occ_class)
                    chunk_methods_dict[occ_full] = (occ_full, occ_method, is_fw)
                    chunk_belongs_to_set.add((occ_full, occ_class))
                    chunk_occurred_in_set.add((target_occ_id, occ_full))

                    for k in range(min(len(call_chain) - 1, 15)):
                        callee_class, callee_method, callee_full, callee_fw = call_chain[k]
                        caller_class, caller_method, caller_full, caller_fw = call_chain[k + 1]

                        chunk_classes_set.add(callee_class)
                        chunk_methods_dict[callee_full] = (callee_full, callee_method, callee_fw)
                        chunk_belongs_to_set.add((callee_full, callee_class))

                        chunk_classes_set.add(caller_class)
                        chunk_methods_dict[caller_full] = (caller_full, caller_method, caller_fw)
                        chunk_belongs_to_set.add((caller_full, caller_class))

                        chunk_calls_set.add((caller_full, callee_full))

                if chunk_error_count >= self.chunk_error_limit:
                    commit_chunk()

            current_ctx = None
            caused_seq = 0

            with open_log_file(self.file_path) as f:
                for line_idx, line in enumerate(f):
                    bytes_read += len(line.encode("utf-8", errors="ignore"))

                    if line_idx % 10000 == 0:
                        percent = int(12 + (bytes_read / file_size) * 73)
                        self.progress.emit(
                            f"대용량 스트리밍 분석 중... ({line_idx:,}줄 / 파싱된 에러: {parsed_error_count:,}건)",
                            min(percent, 85),
                        )

                    match = detected_pattern["re"].match(line)
                    if match:
                        clean_timestamp, thread_name, logger, raw_msg, log_level = detected_pattern["parse"](match)

                        if log_level in ["ERROR", "FATAL", "CRITICAL", "EMERGENCY"]:
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
                                if len(current_ctx["raw_stack_trace_lines"]) < 25:
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
                                if len(current_ctx["raw_stack_trace_lines"]) < 25:
                                    current_ctx["raw_stack_trace_lines"].append(line.rstrip())

                                method_raw = line_stripped[3:].split("(")[0].strip()
                                if "." in method_raw:
                                    class_name, method_name = method_raw.rsplit(".", 1)
                                    full_method = f"{class_name}.{method_name}"
                                    is_fw = class_name.startswith(FRAMEWORK_PACKAGES)

                                    if len(current_ctx["call_chain"]) < 30:
                                        current_ctx["call_chain"].append((class_name, method_name, full_method, is_fw))

                            elif line.startswith("\t") or line.startswith("   "):
                                if len(current_ctx["raw_stack_trace_lines"]) < 25:
                                    current_ctx["raw_stack_trace_lines"].append(line.rstrip())

            if current_ctx:
                process_context(current_ctx)

            self.progress.emit("잔여 파싱 데이터 DB 플러시 중...", 88)
            commit_chunk()

            global_threads_set.clear()
            global_classes_set.clear()
            global_methods_set.clear()

            self.progress.emit("분석 완료!", 100)
            is_success = parsed_error_count > 0
            self.finished.emit(is_success, parsed_error_count)

        except Exception as e:
            print(f"로그 파싱 중 심각한 예외 발생: {e}")
            self.finished.emit(False, 0)
        finally:
            if conn:
                conn.close()
            if db and hasattr(db, "close"):
                db.close()
            gc.collect()


# ==============================================================================
# 5. 사후 진단 워커 Thread (Cypher GROUP BY 최적화 및 DB Lock 해제 적용)
# ==============================================================================
class DiagnosisWorker(QThread):
    finished = pyqtSignal(str, list, list, list)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def run(self):
        db, conn = None, None
        try:
            db = kuzu.Database(self.db_path, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
            conn = kuzu.Connection(db)

            time_query = "MATCH (ex:Exception) RETURN min(ex.timestamp) as start_time, max(ex.timestamp) as end_time, count(ex) as total_cnt"
            res = conn.execute(time_query)
            if not res.has_next():
                self.finished.emit("장애 데이터를 찾을 수 없습니다.", [], [], [])
                return

            start_t, end_t, total_cnt = res.get_next()
            if not total_cnt or total_cnt == 0 or start_t is None:
                self.finished.emit("분석된 Exception 로그가 존재하지 않습니다.", [], [], [])
                return

            chart_10step_data = []
            try:
                dt_start = datetime.strptime(str(start_t).split(".")[0], "%Y-%m-%d %H:%M:%S") if isinstance(start_t, str) else start_t
                dt_end = datetime.strptime(str(end_t).split(".")[0], "%Y-%m-%d %H:%M:%S") if isinstance(end_t, str) else end_t
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
                    WHERE ex.timestamp >= $s_time AND ex.timestamp <= $e_time
                    RETURN count(ex)
                    """
                    res_chart = conn.execute(
                        chart_q,
                        {
                            "s_time": step_s,
                            "e_time": step_e,
                        },
                    )
                    c_cnt = res_chart.get_next()[0] if res_chart.has_next() else 0

                    time_lbl = f"{step_s.strftime('%H:%M')}~{step_e.strftime('%H:%M')}"
                    pct = int((c_cnt / total_cnt) * 100) if total_cnt > 0 else 0
                    temp_data.append((step + 1, time_lbl, c_cnt, pct))

                chart_10step_data = list(reversed(temp_data))

            except Exception as chart_err:
                print(f"10단계 차트 산출 오류: {chart_err}")

            thread_query = "MATCH (t:Thread) RETURN count(t)"
            res_thread = conn.execute(thread_query)
            total_threads = res_thread.get_next()[0] if res_thread.has_next() else 0

            # 9대 카테고리 카운터
            oom_cnt = 0
            db_cnt = 0
            thread_cnt = 0
            net_cnt = 0
            mq_cnt = 0
            auth_cnt = 0
            cfg_cnt = 0
            parse_cnt = 0
            app_cnt = 0

            type_query = "MATCH (ex:Exception) RETURN ex.type, ex.message, count(ex) as cnt ORDER BY cnt DESC"
            res_type = conn.execute(type_query)
            type_summary = ""

            while res_type.has_next():
                ex_type, ex_msg, cnt = res_type.get_next()
                str_type = str(ex_type or "")
                str_msg = str(ex_msg or "")

                type_summary += f"     > {str_type} ({cnt:,}건)\n"

                # 우선순위 기반 (1~9단계 if-elif 체인)
                if any(
                    k in str_type or k in str_msg
                    for k in [
                        "OutOfMemoryError",
                        "Metaspace",
                        "GC overhead limit exceeded",
                        "StackOverflowError",
                        "No space left on device",
                        "Too many open files",
                    ]
                ):
                    oom_cnt += cnt
                elif any(k in str_type or k in str_msg for k in ["SQL", "Timeout", "Hikari", "Connection", "Deadlock", "Constraint"]):
                    db_cnt += cnt
                elif any(
                    k in str_type or k in str_msg
                    for k in [
                        "RejectedExecutionException",
                        "TaskRejectedException",
                        "ThreadPoolExecutor",
                        "ConcurrentModificationException",
                    ]
                ):
                    thread_cnt += cnt
                elif any(
                    k in str_type or k in str_msg
                    for k in [
                        "ConnectException",
                        "SocketTimeout",
                        "HttpClient",
                        "UnknownHost",
                        "HttpServerError",
                        "SFTP",
                    ]
                ):
                    net_cnt += cnt
                elif any(
                    k in str_type or k in str_msg
                    for k in [
                        "KafkaException",
                        "RedisConnectionException",
                        "AmqpException",
                        "RabbitMq",
                        "Jedis",
                        "Lettuce",
                        "Redisson",
                    ]
                ):
                    mq_cnt += cnt
                elif any(
                    k in str_type or k in str_msg
                    for k in [
                        "Unauthorized",
                        "OAuth2",
                        "JWT",
                        "ExpiredToken",
                        "SignatureException",
                        "AccessDenied",
                    ]
                ):
                    auth_cnt += cnt
                elif any(
                    k in str_type or k in str_msg
                    for k in [
                        "BeanCreationException",
                        "NoSuchBeanDefinitionException",
                        "ClassNotFoundException",
                        "NoSuchMethodError",
                        "PropertyNotFoundException",
                        "YamlException",
                    ]
                ):
                    cfg_cnt += cnt
                elif any(
                    k in str_type or k in str_msg
                    for k in [
                        "HttpMessageNotReadableException",
                        "JsonParseException",
                        "InvalidFormatException",
                        "UnrecognizedPropertyException",
                        "MethodArgumentNotValidException",
                    ]
                ):
                    parse_cnt += cnt
                else:
                    app_cnt += cnt

            oom_pct = int((oom_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            db_pct = int((db_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            thread_pct = int((thread_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            net_pct = int((net_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            mq_pct = int((mq_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            auth_pct = int((auth_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            cfg_pct = int((cfg_cnt / total_cnt) * 100) if total_cnt > 0 else 0
            parse_pct = int((parse_cnt / total_cnt) * 100) if total_cnt > 0 else 0

            used_sum = oom_pct + db_pct + thread_pct + net_pct + mq_pct + auth_pct + cfg_pct + parse_pct
            app_pct = max(0, 100 - used_sum)

            max_pct = max(oom_pct, db_pct, thread_pct, net_pct, mq_pct, auth_pct, cfg_pct, parse_pct, app_pct)
            diagnosis_tag, recommendation = "", ""

            if max_pct == oom_pct and oom_pct > 0:
                diagnosis_tag = "🧠 JVM & MEMORY EXHAUSTION (메모리 및 시스템 자원 고갈)"
                recommendation = "   1. [Heap Memory]: JVM -Xmx 메모리 증설 및 Heap Dump 분석 (Memory Leak 탐색).\n   2. [OS File Descriptor]: 파일/소켓 Close 누락 여부 확인 (ulimit 점검)."
            elif max_pct == db_pct and db_pct > 0:
                diagnosis_tag = "🔴 DATABASE BOTTLE_NECK (데이터베이스 장애)"
                recommendation = "   1. [커넥션 풀 고갈]: HikariCP/DataSource 커넥션 점유 점검.\n   2. [슬로우 쿼리]: 대형 조인 및 인덱스 누락 점검."
            elif max_pct == thread_pct and thread_pct > 0:
                diagnosis_tag = "⚡ THREAD POOL & CONCURRENCY BOTTLE_NECK (스레드 풀 고갈 및 동시성 병목)"
                recommendation = "   1. [Async Thread Pool]: @Async 및 TaskExecutor의 corePoolSize / queueCapacity 재설정.\n   2. [Backpressure]: 순간 유입 트래픽 제어를 위한 Rate Limiter 도입 검토."
            elif max_pct == net_pct and net_pct > 0:
                diagnosis_tag = "⚡ EXTERNAL NETWORK OUTAGE (외부 연동망 및 SFTP/네트워크 장애)"
                recommendation = "   1. [연동 경로 확인]: 타겟 서버/SFTP 경로 점검.\n   2. [타임아웃 설정]: Timeout 시간 단축 제어."
            elif max_pct == mq_pct and mq_pct > 0:
                diagnosis_tag = "📨 MESSAGE QUEUE & CACHE OUTAGE (메시지 큐 및 캐시 장애)"
                recommendation = "   1. [Redis/Kafka]: 미들웨어 노드 상태 및 컨슈머(Consumer) Lag/Offset 점검.\n   2. [Fallback]: 캐시/MQ 장애 시 DB 직접 조회 및 Failover 로직 보완."
            elif max_pct == auth_pct and auth_pct > 0:
                diagnosis_tag = "🔑 AUTHENTICATION & SECURITY FAILURE (인증 및 보안 장애)"
                recommendation = "   1. [OAuth Secret 만료]: 인증 토큰 유효기간 점검.\n   2. [JWT 서명 오류]: Key 값 변경 여부 확인."
            elif max_pct == cfg_pct and cfg_pct > 0:
                diagnosis_tag = "⚙️ CONFIG & DEPLOYMENT ERROR (설정 및 배포/환경 예외)"
                recommendation = "   1. [배포 검증]: 최근 신규 배포 패키지의 application.yml / 환경변수 설정값 확인.\n   2. [라이브러리 충돌]: 의존성(Gradle/Maven) 버전 단절 및 중복 Jar 파악."
            elif max_pct == parse_pct and parse_pct > 0:
                diagnosis_tag = "📦 DATA PARSING & VALIDATION FAILURE (데이터 직렬화 / 바인딩 에러)"
                recommendation = "   1. [Payload Validation]: 연동 시스템 간 DTO/API 스펙 계약(Contract) 재확인.\n   2. [Jackson Parser]: FAIL_ON_UNKNOWN_PROPERTIES 등 파싱 옵션 유연화 검토."
            else:
                diagnosis_tag = "💻 APPLICATION LOGIC ERROR (소스코드 내부 결함)"
                recommendation = "   1. [런타임 Exception]: NullPointer 예외 처리 보완.\n   2. [배포 이력]: 최근 Git 커밋 내역 체크."

            detailed_report = (
                f"=========================================================================================================\n"
                f" [장애 사후 진단서]  발생 시간대: {str(start_t).split('.')[0]} ~ {str(end_t).split('.')[0]}\n"
                f"=========================================================================================================\n"
                f" ■ 인프라 및 애플리케이션 영향도 검사 지표\n"
                f"   - 총 누적 예외 발생수 : {total_cnt:,}건\n"
                f"   - 영향받은 워커 스레드 수 : {total_threads:,}개\n"
                f"   - 자동 진단 분류 등급 : {diagnosis_tag}\n\n"
                f" ■ 도메인별 장애 유발 지분율 (RCA 지표)\n"
                f"   ├─ [JVM 메모리/자원 고갈] : {oom_pct}%\n"
                f"   ├─ [데이터베이스 영역] : {db_pct}%\n"
                f"   ├─ [스레드풀 및 동시성] : {thread_pct}%\n"
                f"   ├─ [외부 연동망 및 SFTP] : {net_pct}%\n"
                f"   ├─ [캐시 및 메시지 큐] : {mq_pct}%\n"
                f"   ├─ [인증 및 OAuth 보안] : {auth_pct}%\n"
                f"   ├─ [설정 및 배포/환경] : {cfg_pct}%\n"
                f"   ├─ [데이터 파싱/바인딩] : {parse_pct}%\n"
                f"   └─ [순수 애플리케이션] : {app_pct}%\n\n"
                f" ■ 검출된 최다 빈도 예외 클래스 명세\n"
                f"{type_summary}\n"
                f" ■ 엔지니어 트러블슈팅 권고사항:\n"
                f"{recommendation}"
            )

            root_data = []
            root_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN count(ex) as cnt, m.fullName, ex.type ORDER BY cnt DESC LIMIT 10"
            res_root = conn.execute(root_query)
            while res_root.has_next():
                cnt, method_name, ex_type = res_root.get_next()
                root_data.append((f"{cnt:,}", str(method_name or ""), str(ex_type or "")))

            recent_data = []
            recent_query = (
                "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN ex.timestamp, m.fullName, ex.type ORDER BY ex.timestamp DESC LIMIT 10"
            )
            res_recent = conn.execute(recent_query)
            while res_recent.has_next():
                ts, method_name, ex_type = res_recent.get_next()
                time_str = str(ts).split(".")[0]
                recent_data.append((time_str, str(method_name or ""), str(ex_type or "")))

            self.finished.emit(detailed_report, root_data, recent_data, chart_10step_data)
        except Exception as e:
            print(f"진단 분석 중 오류 발생: {e}")
            self.finished.emit(f"진단 중 오류 발생: {e}", [], [], [])
        finally:
            if conn:
                conn.close()
            if db and hasattr(db, "close"):
                db.close()
            gc.collect()


# ==============================================================================
# 5-1. Git 로컬 히스토리 팝업 레이어 (GitHistoryDialog)
# ==============================================================================
class GitHistoryDialog(QDialog):
    def __init__(self, git_path: str, class_full_name: str, method_name: str, parent=None):
        super().__init__(parent)
        self.git_path = git_path
        self.class_full_name = class_full_name
        self.method_name = method_name
        
        self.setWindowTitle(f"Git History - {class_full_name.split('.')[-1]}")
        self.resize(850, 500)
        self.setup_ui()
        self.load_git_history()

    def setup_ui(self):
        self.setStyleSheet(
            """
            QDialog { background-color: #23272e; color: #dcdde1; }
            QLabel { color: #dcdde1; font-size: 12px; }
            QTableWidget {
                background-color: #2f3640;
                color: #f5f6fa;
                gridline-color: #1e222b;
                border: 1px solid #1e222b;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #1e222b;
                color: #f5f6fa;
                padding: 6px;
                border: 1px solid #2f3640;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #34495e;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #415b76;
            }
            """
        )
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        
        # 상단 정보 레이블
        info_layout = QGridLayout()
        info_layout.setSpacing(6)
        
        lbl_class_title = QLabel("<b>클래스 풀네임:</b>")
        self.lbl_class_val = QLabel(self.class_full_name)
        self.lbl_class_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        lbl_method_title = QLabel("<b>메소드명:</b>")
        self.lbl_method_val = QLabel(self.method_name)
        self.lbl_method_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        lbl_file_title = QLabel("<b>로컬 파일 경로:</b>")
        self.lbl_file_val = QLabel("검색 중...")
        self.lbl_file_val.setStyleSheet("color: #00d2d3;")
        self.lbl_file_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        info_layout.addWidget(lbl_class_title, 0, 0)
        info_layout.addWidget(self.lbl_class_val, 0, 1)
        info_layout.addWidget(lbl_method_title, 1, 0)
        info_layout.addWidget(self.lbl_method_val, 1, 1)
        info_layout.addWidget(lbl_file_title, 2, 0)
        info_layout.addWidget(self.lbl_file_val, 2, 1)
        
        layout.addLayout(info_layout)
        
        # Git 기록 테이블
        self.table = QTableWidget(0, 5)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setHorizontalHeaderLabels(["SHA", "작성자 (Author)", "이메일 (Email)", "날짜 (Date)", "커밋 메시지 (Commit Message)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        
        layout.addWidget(self.table)
        
        # 하단 닫기 버튼
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(self.accept)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

    def load_git_history(self):
        relative_file_path = self.find_file_in_git(self.git_path, self.class_full_name)
        if not relative_file_path:
            self.lbl_file_val.setText("로컬 Git 저장소 내에서 해당 소스 파일을 찾을 수 없습니다.")
            self.lbl_file_val.setStyleSheet("color: #ff6e40;")
            return
            
        full_file_path = os.path.join(self.git_path, relative_file_path).replace("\\", "/")
        self.lbl_file_val.setText(full_file_path)
        self.lbl_file_val.setStyleSheet("color: #27ae60;")
        
        try:
            cmd = [
                "git", "log",
                "--follow",
                "-n", "30",
                "--pretty=format:%h|%an|%ae|%ad|%s",
                "--date=short",
                "--",
                relative_file_path
            ]
            
            result = subprocess.run(
                cmd,
                cwd=self.git_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            if result.returncode != 0:
                self.lbl_file_val.setText(f"Git 로그 조회 실패: {result.stderr.strip()}")
                self.lbl_file_val.setStyleSheet("color: #ff6e40;")
                return
                
            lines = result.stdout.splitlines()
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            for row_idx, line in enumerate(lines):
                parts = line.split("|", 4)
                if len(parts) < 5:
                    parts = parts + [""] * (5 - len(parts))
                
                sha, author, email, date, msg = parts
                
                self.table.insertRow(row_idx)
                self.table.setItem(row_idx, 0, QTableWidgetItem(sha))
                self.table.setItem(row_idx, 1, QTableWidgetItem(author))
                self.table.setItem(row_idx, 2, QTableWidgetItem(email))
                self.table.setItem(row_idx, 3, QTableWidgetItem(date))
                self.table.setItem(row_idx, 4, QTableWidgetItem(msg))
            self.table.setSortingEnabled(True)
                
        except Exception as e:
            self.lbl_file_val.setText(f"Git 실행 오류: {e}")
            self.lbl_file_val.setStyleSheet("color: #ff6e40;")

    def find_file_in_git(self, git_path: str, class_full_name: str) -> str | None:
        path_suffix = class_full_name.replace(".", "/")
        class_simple_name = class_full_name.split(".")[-1]
        
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=git_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if result.returncode != 0:
                return None
                
            files = result.stdout.splitlines()
            
            # 1차 매칭: 패키지 경로 일치
            for f in files:
                f_no_ext, _ = os.path.splitext(f)
                if f_no_ext.replace("\\", "/").endswith(path_suffix):
                    return f
                    
            # 2차 매칭: 단순 클래스명 매칭
            for f in files:
                f_no_ext, _ = os.path.splitext(f)
                if os.path.basename(f_no_ext) == class_simple_name:
                    return f
                    
        except Exception as e:
            print(f"Git 파일 검색 오류: {e}")
            
        return None


# ==============================================================================
# 6. 비동기 트리 뷰 데이터 로더 Thread
# ==============================================================================
class TreeLoadWorker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, db_path: str, method_name: str):
        super().__init__()
        self.db_path = db_path
        self.method_name = method_name

    def run(self):
        root_node = QStandardItem(f"🎯 Target Method: {self.method_name}")
        db, conn = None, None
        try:
            db = kuzu.Database(self.db_path, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
            conn = kuzu.Connection(db)

            ex_query = """
            MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method {fullName: $method_name})
            RETURN ex.id, ex.type, ex.message, ex.stackTrace, ex.timestamp
            ORDER BY ex.timestamp DESC LIMIT 5
            """
            res_ex = conn.execute(ex_query, {"method_name": self.method_name})

            has_data = False
            while res_ex.has_next():
                has_data = True
                ex_id, ex_type, ex_msg, stack_trace, ts = res_ex.get_next()

                ex_item = QStandardItem(f"🚨 [{str(ts).split('.')[0]}] {ex_type}: {ex_msg}")

                cb_query = """
                MATCH (ex:Exception {id: $ex_id})-[:CAUSED_BY]->(child:Exception)
                RETURN child.type, child.message
                """
                res_cb = conn.execute(cb_query, {"ex_id": ex_id})
                while res_cb.has_next():
                    c_type, c_msg = res_cb.get_next()
                    ex_item.appendRow(QStandardItem(f"  └─ 💥 Caused by: {c_type}: {c_msg}"))

                if stack_trace:
                    st_item = QStandardItem("  📜 Stack Trace Sample")
                    for line in str(stack_trace).split("\n")[:25]:
                        st_item.appendRow(QStandardItem(f"      {line.strip()}"))
                    ex_item.appendRow(st_item)

                root_node.appendRow(ex_item)

            if not has_data:
                root_node.appendRow(QStandardItem("  ℹ️ 연결된 상세 스택트레이스 데이터가 없습니다."))

            calls_query = """
            MATCH (caller:Method)-[:CALLS]->(m:Method {fullName: $method_name})
            RETURN DISTINCT caller.fullName
            LIMIT 5
            """
            res_calls = conn.execute(calls_query, {"method_name": self.method_name})
            while res_calls.has_next():
                caller_full = res_calls.get_next()[0]
                root_node.appendRow(QStandardItem(f"  ⬆️ Called By: {caller_full}"))

        except Exception as e:
            print(f"전파 체인 로딩 오류: {e}")
            root_node.appendRow(QStandardItem(f"  ⚠️ 조회 실패: {e}"))
        finally:
            if conn:
                conn.close()
            if db and hasattr(db, "close"):
                db.close()
            gc.collect()

        self.finished.emit(root_node)


# ==============================================================================
# 7. 메인 윈도우 클래스
# ==============================================================================
class MainWindow(QMainWindow):
    WINDOW_TITLE = "통합 WAS/애플리케이션 로그 자동 분석기 (고속 Bulk Insert 엔진 적용) v1.3.0"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setGeometry(100, 100, 1450, 950)

        self.db = None
        self.conn = None
        self.tree_worker = None

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
                if hasattr(self.db, "close"):
                    self.db.close()
            except Exception:
                pass
            self.db = None
        gc.collect()
        time.sleep(0.2)

    def init_database_safely(self):
        self.close_db_connection()
        try:
            self.db = kuzu.Database(DB_PATH, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
            self.conn = kuzu.Connection(self.db)
            create_schema(self.conn)
        except Exception as e:
            print(f"DB 연결 중 초기화 오류 재시도: {e}")
            time.sleep(0.3)
            self.close_db_connection()
            self.db = kuzu.Database(DB_PATH, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
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
            safe_remove_db_path(DB_PATH)
            self.init_database_safely()
            self.reset_ui_components()

            self.btn_upload.setText("📁 통합 로그 파일 선택 및 자동 분석 시작 (Spring / Tomcat / WildFly)")
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
        self.btn_upload = QPushButton("📁 통합 로그 파일 선택 및 자동 분석 시작 (Spring / Tomcat / WildFly)")
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

        # Git 로컬 경로 입력 창 추가
        git_bar = QHBoxLayout()
        git_bar.setSpacing(6)
        
        lbl_git_path = QLabel("<b>📁 Git 로컬 경로:</b>")
        lbl_git_path.setStyleSheet("font-size: 12px; color: #dcdde1;")
        
        self.txt_git_path = QLineEdit()
        self.txt_git_path.setPlaceholderText("예: C:/Users/name/workspace/tomcat (로컬 Git 저장소 경로 등록 시 상세 분석 체인 클릭하여 Git 히스토리 조회 가능)")
        self.txt_git_path.setStyleSheet(
            "background-color: #2f3640; color: #f5f6fa; border: 1px solid #1e222b; padding: 6px; border-radius: 4px; font-size: 12px;"
        )
        
        btn_git_browse = QPushButton("📁 경로 선택")
        btn_git_browse.clicked.connect(self.browse_git_path)
        btn_git_browse.setStyleSheet(
            "background-color: #34495e; color: white; padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 12px;"
        )
        
        git_bar.addWidget(lbl_git_path)
        git_bar.addWidget(self.txt_git_path, 1)
        git_bar.addWidget(btn_git_browse)
        
        main_layout.addLayout(git_bar)

        status_box = QHBoxLayout()
        self.lbl_detected_pattern = QLabel("🔍 감지된 로그 포맷: [대기 중]")
        self.lbl_detected_pattern.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 12px;")

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

        title_lbl = QLabel("<b>📝 인메모리 마이닝 기반 장애 정밀 요약 보고서 (Post-Mortem Report)</b>")
        title_lbl.setStyleSheet("margin: 0px; padding: 0px;")
        top_report_box.addWidget(title_lbl)

        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setFixedHeight(230)
        self.txt_summary.setStyleSheet(
            "background-color: #2f3640; color: #f5f6fa; font-family: Consolas, 'Courier New'; font-size: 12px; border: 1px solid #1e222b; padding: 8px; margin-top: 0px;"
        )
        top_report_box.addWidget(self.txt_summary)
        main_layout.addLayout(top_report_box)

        chart_group = QVBoxLayout()
        chart_group.setSpacing(2)

        chart_title = QLabel("<b>📊 10단계 시간대별 예외 발생 분포 (Timeline Distribution - 역순 정렬)</b>")
        chart_group.addWidget(chart_title)

        chart_frame = QFrame()
        chart_frame.setStyleSheet("background-color: #23272e; border: 1px solid #1e222b; border-radius: 4px;")
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
        bottom_left_box.addWidget(QLabel("<b>🔥 근본 원인(Root Cause) 에러 코드 랭킹 (누적 다빈도)</b>"))
        self.table_root = QTableWidget(0, 3)
        self.table_root.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_root.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_root.setHorizontalHeaderLabels(["발생건수", "근본 원인 메서드 (Root Method)", "주요 예외 클래스"])
        self.table_root.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        bottom_left_box.addWidget(self.table_root, 1)

        bottom_left_box.addWidget(QLabel("<b>🚨 최근 시간대별 에러 코드 랭킹 (최근 발생 순)</b>"))
        self.table_recent = QTableWidget(0, 3)
        self.table_recent.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_recent.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_recent.setHorizontalHeaderLabels(["최근 발생 시각", "발생 메서드 (Recent Method)", "예외 클래스"])
        self.table_recent.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        bottom_left_box.addWidget(self.table_recent, 1)

        bottom_layout.addLayout(bottom_left_box, 1)

        bottom_right_box = QVBoxLayout()
        bottom_right_box.addWidget(QLabel("<b>장애 파급 효과 및 전파 체인 (상세 스택트레이스 포함)</b>"))
        self.tree_view = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        bottom_right_box.addWidget(self.tree_view)
        bottom_layout.addLayout(bottom_right_box, 1)

        main_layout.addLayout(bottom_layout, 1)

        self.table_root.cellClicked.connect(self.on_root_table_clicked)
        self.table_recent.cellClicked.connect(self.on_recent_table_clicked)
        self.tree_view.clicked.connect(self.on_tree_view_clicked)

    def on_root_table_clicked(self, row: int, column: int):
        item = self.table_root.item(row, 1)
        if item:
            self.load_error_propagation_chain(item.text())

    def on_recent_table_clicked(self, row: int, column: int):
        item = self.table_recent.item(row, 1)
        if item:
            self.load_error_propagation_chain(item.text())

    def browse_git_path(self):
        path = QFileDialog.getExistingDirectory(self, "Git 로컬 저장소 디렉토리 선택")
        if path:
            self.txt_git_path.setText(path)

    def on_tree_view_clicked(self, index: QModelIndex):
        git_path = self.txt_git_path.text().strip()
        if not git_path:
            return
            
        item = self.tree_model.itemFromIndex(index)
        if not item:
            return
            
        text = item.text()
        method_name = None
        
        if text.startswith("🎯 Target Method:"):
            method_name = text.replace("🎯 Target Method:", "").strip()
        elif "⬆️ Called By:" in text:
            method_name = text.split("⬆️ Called By:")[-1].strip()
            
        if method_name:
            self.show_git_history_popup(git_path, method_name)

    def show_git_history_popup(self, git_path: str, method_name: str):
        parts = method_name.split(".")
        if len(parts) >= 2:
            class_full_name = ".".join(parts[:-1])
            method_only = parts[-1]
        else:
            class_full_name = method_name
            method_only = ""
            
        dialog = GitHistoryDialog(git_path, class_full_name, method_only, self)
        dialog.exec()

    def load_error_propagation_chain(self, method_name: str):
        """UI Freeze 방지를 위한 비동기 QThread 스택트레이스 로딩 처리"""
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])

        loading_node = QStandardItem(f"⏳ Target Method ({method_name}) 상세 전파 체인 불러오는 중...")
        self.tree_model.appendRow(loading_node)

        self.close_db_connection()

        self.tree_worker = TreeLoadWorker(DB_PATH, method_name)
        self.tree_worker.finished.connect(self.on_tree_loaded)
        self.tree_worker.start()

    def on_tree_loaded(self, root_node: QStandardItem):
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])
        self.tree_model.appendRow(root_node)
        self.tree_view.expandAll()

    def upload_log(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Log File Selection", "", "Log Files (*.log *.out);;All Files (*)")
        if file_path:
            self.btn_upload.setEnabled(False)
            self.btn_upload.setText("⏳ 이전 DB 초기화 및 대용량 스트리밍 분석 진행 중...")
            self.lbl_detected_pattern.setText("🔍 감지된 로그 포맷: [패턴 탐색 중...]")
            self.progress_bar.setValue(0)

            self.reset_ui_components()
            self.close_db_connection()

            self.worker = LogParseWorker(file_path, DB_PATH)
            self.worker.progress.connect(self.on_progress_update)
            self.worker.pattern_detected.connect(self.on_pattern_detected)
            self.worker.finished.connect(self.on_parse_finished)
            self.worker.start()

    def on_progress_update(self, msg: str, value: int):
        self.lbl_status.setText(msg)
        self.progress_bar.setValue(value)

    def on_pattern_detected(self, pattern_name: str):
        self.lbl_detected_pattern.setText(f"🔍 감지된 로그 포맷: [{pattern_name}]")

    def on_parse_finished(self, is_success: bool, parsed_count: int):
        if not is_success or parsed_count == 0:
            self.init_database_safely()
            self.btn_upload.setEnabled(True)
            self.btn_upload.setText("📁 통합 로그 파일 선택 및 자동 분석 시작 (Spring / Tomcat / WildFly)")
            self.lbl_detected_pattern.setText("🔍 감지된 로그 포맷: [인식 실패]")
            self.lbl_status.setText("⚠️ 분석 중단: 일치하는 패턴이 없거나 에러 로그가 없습니다.")
            self.progress_bar.setValue(0)

            QMessageBox.warning(
                self,
                "분석 불가 안내",
                "지정된 로그 파일에서 인식 가능한 에러 패턴을 찾지 못했거나 분석 대상 로그가 존재하지 않습니다.",
                QMessageBox.StandardButton.Ok,
            )
            return

        self.lbl_status.setText("파싱 완료! 정밀 사후 진단 보고서 작성 중...")

        self.diag_worker = DiagnosisWorker(DB_PATH)
        self.diag_worker.finished.connect(
            lambda report, r_data, rec_data, c_data: self.on_diagnosis_finished(report, r_data, rec_data, c_data, parsed_count)
        )
        self.diag_worker.start()

    def on_diagnosis_finished(self, report: str, root_data: list, recent_data: list, chart_data: list, parsed_count: int):
        self.init_database_safely()

        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("✅ 분석 완료 (클릭하여 다른 파일 분석)")
        self.lbl_status.setText(f"분석 완벽 종료! (총 {parsed_count:,}건의 예외 처리 완료)")

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
# 8. 애플리케이션 실행 진입점 (중복 실행 방지 및 포커스 전환 처리)
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 1. 고유 키 기반 QSharedMemory 단일 인스턴스 검증
    shared_memory_key = "Unique_Log_Analyzer_Single_Instance_Key_v130"
    shared_memory = QSharedMemory(shared_memory_key)

    # 메모리에 이미 연결할 수 있다면 -> 프로세스가 이미 실행 중임
    if not shared_memory.create(1):
        # PyInstaller C-native 스플래시가 노출되어 있다면 즉시 종료
        if pyi_splash and pyi_splash.is_alive():
            pyi_splash.close()

        # 기존 윈도우 창을 탐색하여 화면 전면으로 끌어오고 포커스 부여
        activate_existing_window("통합 WAS/애플리케이션 로그 자동 분석기")

        # CustomSplashScreen 객체 생성 조차 수행하지 않고 프로세스 종료
        sys.exit(0)

    # 2. 첫 실행인 경우: 스플래시 윈도우 표시 및 앱 정상 초기화
    splash = CustomSplashScreen("splash.png")
    splash.show()
    QApplication.processEvents()

    def close_native_splash():
        if pyi_splash and pyi_splash.is_alive():
            pyi_splash.close()

    QTimer.singleShot(80, close_native_splash)

    main_window = MainWindow()

    init_worker = InitWorker(main_window)
    init_worker.progress.connect(splash.update_progress)

    def _finish_loading():
        main_window.show()
        main_window.update()
        QApplication.processEvents()

        splash.close()
        splash.deleteLater()

        main_window.raise_()
        main_window.activateWindow()

    init_worker.finished.connect(_finish_loading)
    init_worker.start()

    sys.exit(app.exec())
