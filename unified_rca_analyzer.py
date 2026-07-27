import gc
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta

import kuzu
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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

DB_PATH = "./kuzu_unified_log_db"


def parse_clean_timestamp(ts_str: str) -> str:
    """다양한 날짜/시간 포맷 정제"""
    if not ts_str:
        return "1970-01-01 00:00:00"
    ts = ts_str.replace("T", " ").replace(",", ".")
    ts = ts.split("+")[0].split(".")[0].strip()
    return ts


class LogParseWorker(QThread):
    progress = pyqtSignal(str, int)
    pattern_detected = pyqtSignal(str)
    finished = pyqtSignal(bool, int)

    def __init__(self, file_path, db_path):
        super().__init__()
        self.file_path = file_path
        self.db_path = db_path

    def run(self):
        # 1. 자동 DB 물리적 초기화
        self.progress.emit("기존 DB 데이터 자동 초기화 중...", 3)
        if os.path.exists(self.db_path):
            try:
                if os.path.isdir(self.db_path):
                    shutil.rmtree(self.db_path)
                else:
                    os.remove(self.db_path)
            except Exception as e:
                print(f"자동 DB 초기화 중 기존 파일 삭제 실패: {e}")

        # 2. 신규 DB 생성 및 스키마 수립
        self.progress.emit("신규 데이터베이스 스키마 구성 중...", 7)
        try:
            db = kuzu.Database(self.db_path)
            conn = kuzu.Connection(db)

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

        stack_pattern = re.compile(r"^\s+at\s+([\w\.\$]+)\.([\w\<]+)\(([^:]+):?(\d+)?\)")
        caused_by_pattern = re.compile(r"^\s*Caused by:\s+([\w\.\$]+):\s*(.*)")

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

        # 1차 스캔: 패턴 감지 및 전체 라인 수 계산
        self.progress.emit("로그 분석 준비 중...", 10)
        total_lines = 0
        detected_pattern = None

        with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                total_lines += 1
                if not detected_pattern and total_lines <= 200:
                    for p in PATTERNS:
                        if p["re"].match(line):
                            detected_pattern = p
                            break

        if total_lines == 0 or not detected_pattern:
            conn.close()
            del conn, db
            gc.collect()
            self.finished.emit(False, 0)
            return

        self.pattern_detected.emit(detected_pattern["name"])

        # 2차 스캔: 라인 단위 스트리밍 파싱 (OOM 방지)
        parsed_error_count = 0

        # Parameterized Queries 미리 준비 (보안 및 성능 확보)
        q_create_caused = """
            MERGE (c_ex:Exception {id: $id})
            ON CREATE SET c_ex.type = $type, c_ex.message = $msg, c_ex.stackTrace = '', c_ex.timestamp = timestamp($ts)
        """
        q_rel_caused = """
            MATCH (parent:Exception {id: $parent_id}), (child:Exception {id: $child_id})
            MERGE (parent)-[:CAUSED_BY]->(child)
        """
        q_create_main = """
            MERGE (t:Thread {name: $thread_name})
            MERGE (ex:Exception {id: $ex_id})
            ON CREATE SET ex.type = $type, ex.message = $msg, ex.stackTrace = $st, ex.timestamp = timestamp($ts)
            MERGE (t)-[:RAISED]->(ex)
        """
        q_create_occ = """
            MERGE (c:Class {name: $class_name})
            MERGE (m:Method {fullName: $full_name})
            ON CREATE SET m.name = $method_name, m.isFramework = $is_fw
            MERGE (m)-[:BELONGS_TO]->(c)
            WITH m
            MATCH (ex:Exception {id: $ex_id})
            MERGE (ex)-[:OCCURRED_IN]->(m)
        """
        q_create_calls = """
            MERGE (p_c:Class {name: $caller_class})
            MERGE (p_m:Method {fullName: $caller_full})
            ON CREATE SET p_m.name = $caller_method, p_m.isFramework = $caller_fw
            MERGE (p_m)-[:BELONGS_TO]->(p_c)
            WITH p_m
            MERGE (c_c:Class {name: $callee_class})
            MERGE (c_m:Method {fullName: $callee_full})
            ON CREATE SET c_m.name = $callee_method, c_m.isFramework = $callee_fw
            MERGE (c_m)-[:BELONGS_TO]->(c_c)
            WITH p_m, c_m
            MERGE (p_m)-[:CALLS]->(c_m)
        """

        with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines_buffer = f.readlines()  # 버퍼 탐색용

        i = 0
        while i < len(lines_buffer):
            line = lines_buffer[i]
            if i % 100 == 0:
                percent = int(15 + (i / total_lines) * 80)
                self.progress.emit(f"로그 파싱 및 DB 구조화 중... ({i}/{total_lines} 줄)", percent)

            match = detected_pattern["re"].match(line)
            if match:
                clean_timestamp, thread_name, logger, raw_msg, log_level = detected_pattern[
                    "parse"
                ](match)

                if log_level == "ERROR":
                    parsed_error_count += 1

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

                    root_ex_id = f"err_line_{i}"
                    current_ex_id = root_ex_id
                    call_chain = []
                    raw_stack_trace_lines = [line.strip()]
                    j = i + 1
                    caused_seq = 0

                    while j < len(lines_buffer):
                        s_line = lines_buffer[j]
                        stack_match = stack_pattern.match(s_line)
                        caused_match = caused_by_pattern.match(s_line)

                        if (
                            caused_match
                            or stack_match
                            or s_line.startswith("\t")
                            or s_line.startswith("   ")
                        ):
                            raw_stack_trace_lines.append(s_line.rstrip())

                        if caused_match:
                            caused_seq += 1
                            c_type, c_msg = caused_match.groups()
                            caused_ex_id = f"caused_line_{j}_seq_{caused_seq}"

                            conn.execute(
                                q_create_caused,
                                {
                                    "id": caused_ex_id,
                                    "type": c_type,
                                    "msg": c_msg,
                                    "ts": clean_timestamp,
                                },
                            )
                            conn.execute(
                                q_rel_caused, {"parent_id": current_ex_id, "child_id": caused_ex_id}
                            )
                            current_ex_id = caused_ex_id

                        elif stack_match:
                            class_name, method_name, _, _ = stack_match.groups()
                            full_method = f"{class_name}.{method_name}"
                            is_fw = class_name.startswith(FRAMEWORK_PACKAGES)
                            call_chain.append((class_name, method_name, full_method, is_fw))

                        elif (
                            not s_line.startswith("\t")
                            and not s_line.startswith("   ")
                            and detected_pattern["re"].match(s_line)
                        ):
                            break

                        j += 1
                        if len(call_chain) >= 25:
                            break

                    full_stack_trace = "\n".join(raw_stack_trace_lines)

                    # Parameter Binding 기반 DB Insert
                    conn.execute(
                        q_create_main,
                        {
                            "thread_name": thread_name,
                            "ex_id": root_ex_id,
                            "type": ex_type,
                            "msg": ex_msg,
                            "st": full_stack_trace,
                            "ts": clean_timestamp,
                        },
                    )

                    if call_chain and current_ex_id:
                        target_occ = next(
                            (item for item in call_chain if not item[3]), call_chain[0]
                        )
                        occ_class, occ_method, occ_full, is_fw = target_occ

                        conn.execute(
                            q_create_occ,
                            {
                                "class_name": occ_class,
                                "full_name": occ_full,
                                "method_name": occ_method,
                                "is_fw": is_fw,
                                "ex_id": current_ex_id,
                            },
                        )

                        for k in range(len(call_chain) - 1):
                            callee_class, callee_method, callee_full, callee_fw = call_chain[k]
                            caller_class, caller_method, caller_full, caller_fw = call_chain[k + 1]

                            conn.execute(
                                q_create_calls,
                                {
                                    "caller_class": caller_class,
                                    "caller_full": caller_full,
                                    "caller_method": caller_method,
                                    "caller_fw": caller_fw,
                                    "callee_class": callee_class,
                                    "callee_full": callee_full,
                                    "callee_method": callee_method,
                                    "callee_fw": callee_fw,
                                },
                            )

            i += 1

        self.progress.emit("DB 자원 정리 중...", 95)
        conn.close()
        del conn, db
        gc.collect()

        self.progress.emit("분석 완료!", 100)
        is_success = parsed_error_count > 0
        self.finished.emit(is_success, parsed_error_count)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            "통합 WAS/애플리케이션 로그 자동 분석기 (Spring Boot / Tomcat / WildFly)"
        )
        self.setGeometry(100, 100, 1450, 950)

        self.db = None
        self.conn = None

        self.setup_ui()
        self.init_database_safely()

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
            self.create_schema_tables()
        except Exception as e:
            print(f"DB 초기 연결 실패, 재시도: {e}")
            self.close_db_connection()
            self.db = kuzu.Database(DB_PATH)
            self.conn = kuzu.Connection(self.db)
            self.create_schema_tables()

    def create_schema_tables(self):
        if not self.conn:
            return
        try:
            self.conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Thread(name STRING, PRIMARY KEY (name))"
            )
            self.conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Exception(id STRING, type STRING, message STRING, stackTrace STRING, timestamp TIMESTAMP, PRIMARY KEY (id))"
            )
            self.conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Method(fullName STRING, name STRING, isFramework BOOLEAN, PRIMARY KEY (fullName))"
            )
            self.conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Class(name STRING, PRIMARY KEY (name))"
            )

            self.conn.execute("CREATE REL TABLE IF NOT EXISTS RAISED(FROM Thread TO Exception)")
            self.conn.execute(
                "CREATE REL TABLE IF NOT EXISTS OCCURRED_IN(FROM Exception TO Method)"
            )
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS BELONGS_TO(FROM Method TO Class)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS CALLS(FROM Method TO Method)")
            self.conn.execute(
                "CREATE REL TABLE IF NOT EXISTS CAUSED_BY(FROM Exception TO Exception)"
            )
        except Exception as e:
            print(f"Schema 생성 정보: {e}")

    def reset_ui_components(self):
        self.txt_summary.clear()
        self.table_root.setRowCount(0)
        self.table_recent.setRowCount(0)
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])

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
        main_layout.setSpacing(8)

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
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #bbb;
                border-radius: 3px;
                background-color: #f3f3f3;
            }
            QProgressBar::chunk {
                background-color: #2980b9;
                width: 10px;
            }
        """)
        main_layout.addWidget(self.progress_bar)

        top_report_box = QVBoxLayout()
        top_report_box.setSpacing(2)

        title_lbl = QLabel(
            "<b>📝 인메모리 마이닝 기반 장애 정밀 요약 보고서 (Post-Mortem Report)</b>"
        )
        title_lbl.setFixedHeight(title_lbl.fontMetrics().height() + 4)
        top_report_box.addWidget(title_lbl)

        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setFixedHeight(340)
        self.txt_summary.setStyleSheet(
            "background-color: #2f3640; color: #f5f6fa; font-family: Consolas, 'Courier New'; font-size: 12px; border: 1px solid #1e222b; padding: 12px; line-height: 1.5;"
        )
        top_report_box.addWidget(self.txt_summary)
        main_layout.addLayout(top_report_box)

        bottom_layout = QHBoxLayout()

        # 좌측: 근본 원인 랭킹 + 최근 발생 에러 랭킹
        bottom_left_box = QVBoxLayout()
        bottom_left_box.addWidget(
            QLabel("<b>🔥 근본 원인(Root Cause) 에러 코드 랭킹 (누적 다빈도)</b>")
        )
        self.table_root = QTableWidget(0, 3)
        self.table_root.setHorizontalHeaderLabels(
            ["발생건수", "근본 원인 메서드 (Root Method)", "주요 예외 클래스"]
        )
        self.table_root.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # type:ignore
        self.table_root.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_root.itemClicked.connect(self.root_item_clicked)
        bottom_left_box.addWidget(self.table_root, 1)

        bottom_left_box.addWidget(QLabel("<b>🚨 최근 시간대별 에러 코드 랭킹 (최근 발생 순)</b>"))
        self.table_recent = QTableWidget(0, 3)
        self.table_recent.setHorizontalHeaderLabels(
            ["최근 발생 시각", "발생 메서드 (Recent Method)", "예외 클래스"]
        )
        self.table_recent.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # type:ignore
        self.table_recent.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_recent.itemClicked.connect(self.recent_item_clicked)
        bottom_left_box.addWidget(self.table_recent, 1)

        bottom_layout.addLayout(bottom_left_box, 1)

        # 우측: 장애 전파 체인 및 트리 뷰
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

        main_layout.addLayout(bottom_layout)

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

        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("✅ 분석 완료 (클릭하여 다른 파일 분석)")

        if not is_success or parsed_count == 0:
            self.lbl_detected_pattern.setText("🔍 감지된 로그 포맷: [인식 실패]")
            self.lbl_status.setText("⚠️ 분석 중단: 일치하는 패턴이 없거나 에러 로그가 없습니다.")
            self.progress_bar.setValue(0)

            QMessageBox.warning(
                self,
                "분석 불가 안내",
                "지정된 로그 파일에서 인식 가능한 에러 패턴을 찾지 못했거나 분석 대상(ERROR) 로그가 존재하지 않습니다.\n\n"
                "지원 포맷: Spring Boot, Apache Tomcat, WildFly/JBoss server.log",
                QMessageBox.StandardButton.Ok,
            )
            return

        self.lbl_status.setText(f"분석 완벽 종료! (총 {parsed_count}건의 예외 처리 완료)")
        self.run_auto_diagnosis()

    def run_auto_diagnosis(self):
        if not self.conn:
            return

        time_query = "MATCH (ex:Exception) RETURN Min(ex.timestamp) as start_time, Max(ex.timestamp) as end_time, Count(ex) as total_cnt"
        res = self.conn.execute(time_query)
        if not res.has_next():  # type:ignore
            self.txt_summary.setText("장애 데이터를 찾을 수 없습니다.")
            return

        start_t, end_t, total_cnt = res.get_next()  # type:ignore
        if total_cnt == 0:
            self.txt_summary.setText("분석된 Exception 로그가 존재하지 않습니다.")
            return

        thread_query = "MATCH (t:Thread) RETURN Count(t)"
        res_thread = self.conn.execute(thread_query)
        total_threads = res_thread.get_next()[0] if res_thread.has_next() else 0  # type:ignore

        db_cnt, net_cnt, auth_cnt, app_cnt = 0, 0, 0, 0

        type_query = "MATCH (ex:Exception) RETURN ex.type, ex.message, Count(ex) as cnt"
        res_type = self.conn.execute(type_query)
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
                    "SSH_FX_NO_SUCH_PATH",
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
            recommendation = (
                "   1. [커넥션 풀 고갈]: HikariCP/DataSource 커넥션 점유 및 미반환 상태를 점검하십시오.\n"
                "   2. [슬로우 쿼리 저격]: 특정 대형 조인 및 인덱스 누락 쿼리가 Lock을 잡고 전파되었습니다.\n"
                "   3. [트랜잭션 장기 점유]: @Transactional 어노테이션 범위를 재검토하십시오."
            )
        elif max_pct == net_pct and net_pct > 0:
            diagnosis_tag = "⚡ EXTERNAL NETWORK OUTAGE (외부 연동망 및 SFTP/네트워크 장애)"
            recommendation = (
                "   1. [연동 경로 확인]: 타겟 서버/SFTP 타겟 경로 존재 여부 및 네트워크 핑 상태를 확인하십시오.\n"
                "   2. [타임아웃 설정]: Connection/Read Timeout을 단축하여 워커 스레드 동반 결빙을 차단하십시오.\n"
                "   3. [서킷 브레이커 도입]: Resilience4j 같은 우회 조치 패턴 적용을 권장합니다."
            )
        elif max_pct == auth_pct and auth_pct > 0:
            diagnosis_tag = "🔑 AUTHENTICATION & SECURITY FAILURE (인증 및 보안 장애)"
            recommendation = (
                "   1. [OAuth Secret 만료]: 인증 서버 토큰 유효기간 및 IP WhiteList 설정을 점검하십시오.\n"
                "   2. [JWT 서명 오류]: 서버 Secret Key 변경에 따른 서명 실패 여부를 확인하십시오.\n"
                "   3. [비정상 트래픽]: 특정 IP의 어뷰징 공격 트래픽 유입 여부를 검토하십시오."
            )
        else:
            diagnosis_tag = "💻 APPLICATION LOGIC ERROR (소스코드 내부 결함)"
            recommendation = (
                "   1. [런타임 Exception 예외]: NullPointer/Format 에러 등 예외 처리 코드를 보완하십시오.\n"
                "   2. [배포 이력 확인]: 최근 배포된 Git 소스코드 커밋 내역을 크로스 체크하십시오."
            )

        chart_section = " ■ 로그 기준 동적 장애 타임라인 추이 (File-driven Dynamic Timeline)\n"
        try:
            st_str = str(start_t).split(".")[0]
            ed_str = str(end_t).split(".")[0]

            dt_start = datetime.strptime(st_str, "%Y-%m-%d %H:%M:%S")
            dt_end = datetime.strptime(ed_str, "%Y-%m-%d %H:%M:%S")

            total_duration = dt_end - dt_start

            if total_duration.total_seconds() <= 0:
                total_duration = timedelta(seconds=10)
                dt_end = dt_start + total_duration

            NUM_INTERVALS = 10
            interval_secs = total_duration.total_seconds() / NUM_INTERVALS
            intervals = []

            for idx in range(NUM_INTERVALS):
                grid_start = dt_start + timedelta(seconds=idx * interval_secs)
                grid_end = dt_start + timedelta(seconds=(idx + 1) * interval_secs)
                intervals.append({"start": grid_start, "end": grid_end, "count": 0})

            all_errors_query = "MATCH (ex:Exception) RETURN STRING(ex.timestamp) as ts"
            all_errors_res = self.conn.execute(all_errors_query)

            while all_errors_res.has_next():  # type:ignore
                e_ts_str = all_errors_res.get_next()[0].split(".")[0]  # type:ignore
                e_dt = datetime.strptime(e_ts_str, "%Y-%m-%d %H:%M:%S")

                for iv in intervals:
                    if iv["start"] <= e_dt <= iv["end"]:
                        iv["count"] += 1
                        break

            max_grid_count = max(iv["count"] for iv in intervals)

            for i, iv in enumerate(intervals):
                cnt = iv["count"]
                bar_length = int((cnt / max_grid_count) * 30) if max_grid_count > 0 else 0
                bar_str = "■" * bar_length
                lbl_time = iv["start"].strftime("%H:%M:%S")
                chart_section += (
                    f"   ├─ [{i + 1:02d}구간] {lbl_time} ~ : {bar_str.ljust(32)} ({cnt}건)\n"
                )

            chart_section += f"   └─ ※ 총 지속시간 {int(total_duration.total_seconds() // 60)}분 데이터를 10등분한 동적 추이입니다.\n"

        except Exception as chart_err:
            chart_section += f"   └─ 동적 타임라인 차트 생성 실패: {chart_err}\n"

        detailed_report = (
            f"=========================================================================================================\n"
            f" [장애 사후 진단서]  발생 시간대: {str(start_t).split('.')[0]} ~ {str(end_t).split('.')[0]}\n"
            f"=========================================================================================================\n"
            f" ■ 인프라 및 애플리케이션 영향도 검사 지표\n"
            f"   - 총 누적 예외 발생수 : {total_cnt}건\n"
            f"   - 영향받은 워커 스레드 수 : {total_threads}개\n"
            f"   - 자동 진단 분류 등급 : {diagnosis_tag}\n\n"
            f"{chart_section}\n"
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
        self.txt_summary.setText(detailed_report)

        # 1. 근본 원인(Root Cause) 다빈도 에러 랭킹 조회
        root_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN Count(ex) as cnt, m.fullName, ex.type ORDER BY cnt DESC LIMIT 10"
        res_root = self.conn.execute(root_query)
        self.table_root.setRowCount(0)
        row = 0
        while res_root.has_next():  # type:ignore
            cnt, method_name, ex_type = res_root.get_next()  # type:ignore
            self.table_root.insertRow(row)
            self.table_root.setItem(row, 0, QTableWidgetItem(str(cnt)))
            self.table_root.setItem(row, 1, QTableWidgetItem(method_name))
            self.table_root.setItem(row, 2, QTableWidgetItem(ex_type))
            row += 1

        # 2. 최근 시간대별 에러 코드 랭킹 (최근 발생 순) 조회
        recent_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN ex.timestamp, m.fullName, ex.type ORDER BY ex.timestamp DESC LIMIT 10"
        res_recent = self.conn.execute(recent_query)
        self.table_recent.setRowCount(0)
        row_r = 0
        while res_recent.has_next():  # type:ignore
            ts, method_name, ex_type = res_recent.get_next()  # type:ignore
            time_str = str(ts).split(".")[0]
            self.table_recent.insertRow(row_r)
            self.table_recent.setItem(row_r, 0, QTableWidgetItem(time_str))
            self.table_recent.setItem(row_r, 1, QTableWidgetItem(method_name))
            self.table_recent.setItem(row_r, 2, QTableWidgetItem(ex_type))
            row_r += 1

    def root_item_clicked(self, item):
        row = item.row()
        target_method = self.table_root.item(row, 1).text()  # type:ignore
        self.render_tree_chain(target_method, "🔥 근본 원인 분석 체인", show_stack_trace=False)

    def recent_item_clicked(self, item):
        row = item.row()
        target_method = self.table_recent.item(row, 1).text()  # type:ignore
        self.render_tree_chain(target_method, "🚨 최근 발생 원본 로그 분석", show_stack_trace=True)

    def render_tree_chain(self, target_method, root_title_prefix, show_stack_trace=False):
        if not self.conn:
            return

        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])

        tree_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method {fullName: $target_method}) RETURN ex.id, ex.timestamp, m.fullName, ex.type, ex.message, ex.stackTrace ORDER BY ex.timestamp DESC LIMIT 5"
        res_tree = self.conn.execute(tree_query, {"target_method": target_method})

        root_node = QStandardItem(f"{root_title_prefix}: {target_method}")
        self.tree_model.appendRow(root_node)

        while res_tree.has_next():  # type:ignore
            ex_id, timestamp, full_name, ex_type, ex_msg, stack_trace = res_tree.get_next()  # type:ignore
            time_str = (
                str(timestamp).split(" ")[1].split(".")[0]
                if " " in str(timestamp)
                else str(timestamp)
            )

            error_detail_node = QStandardItem(f" ⏱ [{time_str}] 예외종류: {ex_type}")

            if show_stack_trace and stack_trace:
                st_header_node = QStandardItem(" 📄 [Original Log Raw Data]")
                for st_line in stack_trace.split("\n"):
                    if st_line:
                        st_header_node.appendRow(QStandardItem(st_line))
                error_detail_node.appendRow(st_header_node)
            else:
                msg_node = QStandardItem(f"    💬 메시지: {ex_msg}")
                error_detail_node.appendRow(msg_node)

                caller_query = "MATCH (caller:Method)-[:CALLS]->(m:Method {fullName: $target_method}) RETURN caller.fullName LIMIT 1"
                res_caller = self.conn.execute(caller_query, {"target_method": target_method})
                if res_caller.has_next():  # type:ignore
                    caller_name = res_caller.get_next()[0]  # type:ignore
                    caller_item = QStandardItem(f"    🔗 [상위 호출지점] {caller_name}")
                    error_detail_node.appendRow(caller_item)

            root_node.appendRow(error_detail_node)

        self.tree_view.expandAll()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
