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
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

DB_PATH = "./kuzu_springboot_db"


def escape_cypher(text: str) -> str:
    """Cypher 쿼리 내 문자열 리터럴에서 발생할 수 있는 이스케이프 에러 방지"""
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace("'", "\\'").replace("\r", " ").replace("\n", " ")


class LogParseWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, file_path, db_path):
        super().__init__()
        self.file_path = file_path
        self.db_path = db_path

    def run(self):
        # Worker 전용 연결 생성 (메인 스레드의 연결은 잠시 해제된 상태여야 함)
        try:
            db = kuzu.Database(self.db_path)
            conn = kuzu.Connection(db)
        except Exception as e:
            print(f"워커 DB 연결 실패: {e}")
            self.finished.emit()
            return

        # Spring Boot 로그 정규식 패턴 (ISO 8601 타임스탬프, PID, 스레드, 로거, 메시지)
        log_pattern = re.compile(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(?:[+-]\d{2}:\d{2})?)\s+(ERROR|WARN|INFO|DEBUG|TRACE)\s+(\d+)\s+---\s+\[([^\]]+)\]\s+([\w\.\$]+)\s+:\s+(.*)"
        )
        # Exception StackTrace 패턴 (at 구문)
        stack_pattern = re.compile(r"^\s+at\s+([\w\.\$]+)\.([\w\<]+)\(([^:]+):?(\d+)?\)")
        caused_by_pattern = re.compile(r"^\s*Caused by:\s+([\w\.\$]+):\s*(.*)")

        if not os.path.exists(self.file_path):
            self.finished.emit()
            return

        with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        current_ex_id = None

        for i, line in enumerate(lines):
            match = log_pattern.match(line)
            if match:
                raw_timestamp, log_level, pid, thread_name, logger, raw_msg = match.groups()

                # ERROR 등 예외 발생 구간만 분석 대상 처리
                if log_level != "ERROR":
                    continue

                # 타임스탬프 포맷 정제 ('T' 문자를 공백으로 변환 및 시차 표기 정제)
                clean_timestamp = raw_timestamp.replace("T", " ").split("+")[0].split(".")[0]

                if " : " in raw_msg:
                    parts = raw_msg.split(" : ", 1)
                    ex_type, ex_msg = parts[0], parts[1]
                elif ":" in raw_msg:
                    parts = raw_msg.split(":", 1)
                    ex_type, ex_msg = parts[0].strip(), parts[1].strip()
                else:
                    ex_type = logger.split(".")[-1]
                    ex_msg = raw_msg

                # 라인 번호 기반 고유 ID 생성
                current_ex_id = f"err_{i}_{int(time.time())}"
                ex_type_esc = escape_cypher(ex_type)
                ex_msg_esc = escape_cypher(ex_msg)
                thread_name_esc = escape_cypher(thread_name.strip())

                conn.execute(f"MERGE (t:Thread {{name: '{thread_name_esc}'}})")
                conn.execute(
                    f"MERGE (ex:Exception {{id: '{current_ex_id}', type: '{ex_type_esc}', message: '{ex_msg_esc}', timestamp: timestamp('{clean_timestamp}')}})"
                )
                conn.execute(
                    f"MATCH (t:Thread {{name: '{thread_name_esc}'}}), (ex:Exception {{id: '{current_ex_id}'}}) MERGE (t)-[:RAISED]->(ex)"
                )

                # 스택트레이스 파싱
                call_chain = []
                j = i + 1
                while j < len(lines):
                    s_line = lines[j]
                    stack_match = stack_pattern.match(s_line)
                    caused_match = caused_by_pattern.match(s_line)

                    # Caused by 탐지 시 Root Cause Exception 노드 생성 및 관계 설정
                    if caused_match:
                        c_type, c_msg = caused_match.groups()
                        caused_ex_id = f"caused_{j}_{int(time.time())}"
                        c_type_esc = escape_cypher(c_type)
                        c_msg_esc = escape_cypher(c_msg)

                        conn.execute(
                            f"MERGE (c_ex:Exception {{id: '{caused_ex_id}', type: '{c_type_esc}', message: '{c_msg_esc}', timestamp: timestamp('{clean_timestamp}')}})"
                        )
                        conn.execute(
                            f"MATCH (parent:Exception {{id: '{current_ex_id}'}}), (child:Exception {{id: '{caused_ex_id}'}}) MERGE (parent)-[:CAUSED_BY]->(child)"
                        )
                        # Caused by 예외를 이후 스택 추적의 기준점으로 전환
                        current_ex_id = caused_ex_id

                    elif stack_match:
                        class_name, method_name, _, _ = stack_match.groups()
                        full_method = f"{class_name}.{method_name}"
                        call_chain.append((class_name, method_name, full_method))

                    elif (
                        not s_line.startswith("\t")
                        and not s_line.startswith("   ")
                        and log_pattern.match(s_line)
                    ):
                        break

                    j += 1
                    if len(call_chain) >= 10:  # 깊이 제한 확장
                        break

                if call_chain and current_ex_id:
                    # call_chain[0]은 예외가 직접 발생한 최하단 메서드
                    occ_class, occ_method, occ_full = call_chain[0]
                    occ_class_esc = escape_cypher(occ_class)
                    occ_method_esc = escape_cypher(occ_method)
                    occ_full_esc = escape_cypher(occ_full)

                    conn.execute(f"MERGE (c:Class {{name: '{occ_class_esc}'}})")
                    conn.execute(
                        f"MERGE (m:Method {{fullName: '{occ_full_esc}', name: '{occ_method_esc}'}})"
                    )
                    conn.execute(
                        f"MATCH (m:Method {{fullName: '{occ_full_esc}'}}), (c:Class {{name: '{occ_class_esc}'}}) MERGE (m)-[:BELONGS_TO]->(c)"
                    )
                    conn.execute(
                        f"MATCH (ex:Exception {{id: '{current_ex_id}'}}), (m:Method {{fullName: '{occ_full_esc}'}}) MERGE (ex)-[:OCCURRED_IN]->(m)"
                    )

                    # 상위 호출자 방향 정정 (call_chain[k+1]이 call_chain[k]를 호출함)
                    for k in range(len(call_chain) - 1):
                        callee_class, callee_method, callee_full = call_chain[k]
                        caller_class, caller_method, caller_full = call_chain[k + 1]

                        caller_c_esc = escape_cypher(caller_class)
                        caller_m_esc = escape_cypher(caller_method)
                        caller_f_esc = escape_cypher(caller_full)

                        callee_c_esc = escape_cypher(callee_class)
                        callee_m_esc = escape_cypher(callee_method)
                        callee_f_esc = escape_cypher(callee_full)

                        conn.execute(f"MERGE (p_c:Class {{name: '{caller_c_esc}'}})")
                        conn.execute(
                            f"MERGE (p_m:Method {{fullName: '{caller_f_esc}', name: '{caller_m_esc}'}})"
                        )
                        conn.execute(
                            f"MATCH (p_m:Method {{fullName: '{caller_f_esc}'}}), (p_c:Class {{name: '{caller_c_esc}'}}) MERGE (p_m)-[:BELONGS_TO]->(p_c)"
                        )

                        conn.execute(
                            f"MATCH (m_caller:Method {{fullName: '{caller_f_esc}'}}), (m_callee:Method {{fullName: '{callee_f_esc}'}}) MERGE (m_caller)-[:CALLS]->(m_callee)"
                        )

        # Worker 자원 해제
        del conn
        del db
        gc.collect()
        self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spring Boot 로그 자동 분석기 (Kùzu 그래프 인공지능형)")
        self.setGeometry(100, 100, 1450, 950)

        self.db = None
        self.conn = None

        self.setup_ui()
        self.init_database_safely()

    def close_db_connection(self):
        """Worker 스레드와의 Lock 충돌 방지를 위한 DB 연결 해제 함수"""
        self.conn = None
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
                "CREATE NODE TABLE IF NOT EXISTS Exception(id STRING, type STRING, message STRING, timestamp TIMESTAMP, PRIMARY KEY (id))"
            )
            self.conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Method(fullName STRING, name STRING, PRIMARY KEY (fullName))"
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

    def reset_database(self):
        reply = QMessageBox.question(
            self,
            "DB 초기화",
            "정말 데이터베이스를 초기화하시겠습니까?",
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
                    print(f"초기화 과정 중 물리 삭제 실패: {e}")

            self.init_database_safely()

            self.txt_summary.clear()
            self.table_root.setRowCount(0)
            self.tree_model.clear()
            self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])
            self.btn_upload.setText("📁 Spring Boot 로그 파일 선택 및 자동 분석 시작")
            self.lbl_status.setText("데이터베이스가 성공적으로 초기화되었습니다.")

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(8)

        top_bar = QHBoxLayout()
        self.btn_upload = QPushButton("📁 Spring Boot 로그 파일 선택 및 자동 분석 시작")
        self.btn_upload.clicked.connect(self.upload_log)
        self.btn_upload.setStyleSheet(
            "background-color: #1e3d59; color: white; font-weight: bold; padding: 12px; font-size: 13px; border-radius: 4px;"
        )

        self.btn_reset = QPushButton("🧹 DB 초기화")
        self.btn_reset.clicked.connect(self.reset_database)
        self.btn_reset.setStyleSheet(
            "background-color: #ff6e40; color: white; font-weight: bold; padding: 12px; font-size: 13px; border-radius: 4px;"
        )

        top_bar.addWidget(self.btn_upload, 4)
        top_bar.addWidget(self.btn_reset, 1)
        main_layout.addLayout(top_bar)

        self.lbl_status = QLabel(
            "로그 파일을 선택하면 시스템이 자동으로 장애 성격과 위치를 파악합니다."
        )
        self.lbl_status.setStyleSheet("color: #7f8c8d; font-style: italic; margin-bottom: 2px;")
        main_layout.addWidget(self.lbl_status)

        top_report_box = QVBoxLayout()
        top_report_box.setSpacing(2)

        title_lbl = QLabel(
            "<b>📝 인메모리 마이닝 기반 장애 정밀 요약 보고서 (Post-Mortem Report)</b>"
        )
        title_lbl.setFixedHeight(title_lbl.fontMetrics().height() + 4)
        top_report_box.addWidget(title_lbl)

        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setFixedHeight(400)
        self.txt_summary.setStyleSheet(
            "background-color: #2f3640; color: #f5f6fa; font-family: Consolas, 'Courier New'; font-size: 12px; border: 1px solid #1e222b; padding: 12px; line-height: 1.5;"
        )
        top_report_box.addWidget(self.txt_summary)
        main_layout.addLayout(top_report_box)

        bottom_layout = QHBoxLayout()

        bottom_left_box = QVBoxLayout()
        bottom_left_box.addWidget(QLabel("<b>근본 원인(Root Cause) 에러 코드 랭킹</b>"))
        self.table_root = QTableWidget(0, 3)
        self.table_root.setHorizontalHeaderLabels(
            ["발생건수", "근본 원인 메서드 (Root Method)", "주요 예외 클래스"]
        )
        self.table_root.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_root.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_root.itemClicked.connect(self.root_item_clicked)
        bottom_left_box.addWidget(self.table_root)
        bottom_layout.addLayout(bottom_left_box, 1)

        bottom_right_box = QVBoxLayout()
        bottom_right_box.addWidget(
            QLabel("<b>장애 파급 효과 및 전파 체인 (상세 에러 내용 포함)</b>")
        )
        self.tree_view = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        bottom_right_box.addWidget(self.tree_view)
        bottom_layout.addLayout(bottom_right_box, 1)

        main_layout.addLayout(bottom_layout)

    def upload_log(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Spring Boot Log File", "", "Log Files (*.log *.out);;All Files (*)"
        )
        if file_path:
            self.btn_upload.setEnabled(False)
            self.btn_upload.setText("⏳ 자동 분석 중... 데이터 파싱 및 그래프 DB 모델링 처리 중")

            # DB 파일 Lock 충돌을 방지하기 위해 UI 스레드의 DB 연결 해제
            self.close_db_connection()

            self.worker = LogParseWorker(file_path, DB_PATH)
            self.worker.finished.connect(self.on_parse_finished)
            self.worker.start()

    def on_parse_finished(self):
        # Worker 파싱 완료 후 메인 스레드에서 DB 연결 재개
        self.init_database_safely()

        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("✅ 자동 분석 완료 (클릭하여 다시 분석)")
        self.lbl_status.setText("분석 프로세스가 정상 완료되었습니다.")
        self.run_auto_diagnosis()

    def run_auto_diagnosis(self):
        if not self.conn:
            return

        time_query = "MATCH (ex:Exception) RETURN Min(ex.timestamp) as start_time, Max(ex.timestamp) as end_time, Count(ex) as total_cnt"
        res = self.conn.execute(time_query)
        if not res.has_next():
            self.txt_summary.setText("장애 데이터를 찾을 수 없습니다.")
            return

        start_t, end_t, total_cnt = res.get_next()
        if total_cnt == 0:
            self.txt_summary.setText("분석된 Exception 로그가 존재하지 않습니다.")
            return

        thread_query = "MATCH (t:Thread) RETURN Count(t)"
        res_thread = self.conn.execute(thread_query)
        total_threads = res_thread.get_next()[0] if res_thread.has_next() else 0

        db_cnt, net_cnt, auth_cnt, app_cnt = 0, 0, 0, 0

        type_query = "MATCH (ex:Exception) RETURN ex.type, ex.message, Count(ex) as cnt"
        res_type = self.conn.execute(type_query)
        type_summary = ""

        while res_type.has_next():
            ex_type, ex_msg, cnt = res_type.get_next()
            type_summary += f"     > {ex_type} ({cnt}건)\n"

            if any(
                k in ex_type or k in ex_msg
                for k in [
                    "SQL",
                    "Timeout",
                    "Hikari",
                    "Connection",
                    "Deadlock",
                    "Constraint",
                ]
            ):
                db_cnt += cnt
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
                net_cnt += cnt
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
                auth_cnt += cnt
            else:
                app_cnt += cnt

        db_pct = int((db_cnt / total_cnt) * 100)
        net_pct = int((net_cnt / total_cnt) * 100)
        auth_pct = int((auth_cnt / total_cnt) * 100)
        app_pct = max(0, 100 - (db_pct + net_pct + auth_pct))

        max_pct = max(db_pct, net_pct, auth_pct, app_pct)
        diagnosis_tag, recommendation = "", ""

        if max_pct == db_pct and db_pct > 0:
            diagnosis_tag = "🔴 DATABASE BOTTLE_NECK (데이터베이스 장애)"
            recommendation = (
                "   1. [커넥션 풀 고갈]: HikariCP가 전부 사용 중(Active) 상태에서 반환되지 않아 Timeout이 유발되었습니다.\n"
                "   2. [슬로우 쿼리 저격]: 특정 대형 조인 또는 인덱스 누락 쿼리가 테이블 락(Lock)을 쥐고 전파되었습니다.\n"
                "   3. [트랜잭션 장기 점유]: Spring @Transactional 어노테이션 범위를 재검토하십시오."
            )
        elif max_pct == net_pct and net_pct > 0:
            diagnosis_tag = (
                "⚡ EXTERNAL NETWORK OUTAGE / REMOTE FILE ERROR (외부 연동망 및 SFTP/연동 장애)"
            )
            recommendation = (
                "   1. [연동 경로 확인]: Remote SFTP/외부 경로 미존재(SSH_FX_NO_SUCH_PATH) 및 연동 대상 서버 상태를 점검하십시오.\n"
                "   2. [타임아웃 타이트닝]: 스레드 동반 결빙 방지를 위해 Read Timeout 및 Polling 주기 설정을 최적화하십시오.\n"
                "   3. [서킷 브레이커 도입]: Resilience4j 같은 시스템 우회 및 폴백(Fallback) 장치 도입을 검토하십시오."
            )
        elif max_pct == auth_pct and auth_pct > 0:
            diagnosis_tag = "🔑 AUTHENTICATION & SECURITY FAILURE (인증 및 보안 장애)"
            recommendation = (
                "   1. [OAuth Secret 만료]: 서드파티 인증 서버의 연동 Secret Key 유효기간 및 IP 화이트리스트를 검토하십시오.\n"
                "   2. [JWT 서명 오류]: 서버 측 Secret Key 변경으로 토큰 검증 실패 예외가 폭발했을 수 있습니다.\n"
                "   3. [무차별 대입 공격]: 특정 IP의 비정상 대량 인증 요청 트래픽 유입인지 확인하십시오."
            )
        else:
            diagnosis_tag = "💻 APPLICATION LOGIC ERROR (소스코드 내부 결함)"
            recommendation = (
                "   1. [런타임 Exception 예외]: 특정 메시지/객체의 처리 누락 구간을 보완하십시오.\n"
                "   2. [배포 이력 크로스 체크]: 최근 배포된 형상관리(Git) 커밋 소스코드 라인을 추적하십시오.\n"
                "   3. [데이터 정밀성 검증]: 파싱 데이터 포맷 규격 에러 유입 여부를 점검하십시오."
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

            while all_errors_res.has_next():
                e_ts_str = all_errors_res.get_next()[0].split(".")[0]
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

            chart_section += f"   └─ ※ 이 차트는 총 지속시간 {int(total_duration.total_seconds() // 60)}분 데이터를 10등분한 동적 추이입니다.\n"

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

        root_query = "MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method) RETURN Count(ex) as cnt, m.fullName, ex.type ORDER BY cnt DESC LIMIT 10"
        res_root = self.conn.execute(root_query)
        self.table_root.setRowCount(0)
        row = 0
        while res_root.has_next():
            cnt, method_name, ex_type = res_root.get_next()
            self.table_root.insertRow(row)
            self.table_root.setItem(row, 0, QTableWidgetItem(str(cnt)))
            self.table_root.setItem(row, 1, QTableWidgetItem(method_name))
            self.table_root.setItem(row, 2, QTableWidgetItem(ex_type))
            row += 1

    def root_item_clicked(self, item):
        if not self.conn:
            return
        row = item.row()
        target_method = escape_cypher(self.table_root.item(row, 1).text())
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["에러 전파 타임라인 및 상세 분석 체인"])

        tree_query = f"MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method {{fullName: '{target_method}'}}) RETURN ex.timestamp, m.fullName, ex.type, ex.message ORDER BY ex.timestamp ASC LIMIT 5"
        res_tree = self.conn.execute(tree_query)

        root_node = QStandardItem(f"🔥 근본 원인 메서드 (Root Method): {target_method}")
        self.tree_model.appendRow(root_node)

        while res_tree.has_next():
            timestamp, full_name, ex_type, ex_msg = res_tree.get_next()
            time_str = (
                str(timestamp).split(" ")[1].split(".")[0]
                if " " in str(timestamp)
                else str(timestamp)
            )

            error_detail_node = QStandardItem(f" ⏱ [{time_str}] 예외종류: {ex_type}")
            error_msg_sub_node = QStandardItem(f"    💬 상세 메시지: {ex_msg}")
            error_detail_node.appendRow(error_msg_sub_node)

            caller_query = f"MATCH (caller:Method)-[:CALLS]->(m:Method {{fullName: '{target_method}'}}) RETURN caller.fullName LIMIT 1"
            res_caller = self.conn.execute(caller_query)
            if res_caller.has_next():
                caller_name = res_caller.get_next()[0]
                caller_item = QStandardItem(f"    🔗 [상위 호출지점] {caller_name}")
                error_detail_node.appendRow(caller_item)

            root_node.appendRow(error_detail_node)

        self.tree_view.expandAll()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
