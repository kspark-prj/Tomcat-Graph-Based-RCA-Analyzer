import base64
import json
import os
import re
import sys
from datetime import datetime

from neo4j import Driver, GraphDatabase
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

# ==========================================
# 1. 설정 및 정규식 정의
# ==========================================
CONFIG_FILE = "neo4j_config.json"
LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\[([^\]]+)\]\s+(\s+|ERROR|SEVERE|WARN)\s+([\w\.]+)\s*-\s*(.*)$"
)
STACK_TRACE_PATTERN = re.compile(r"^\s+at\s+([\w\.]+)\.([\w\<]+)\(([^:]+):?(\d+)?\)")


class TomcatRCAAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tomcat 자동 장애 사후 분석기 (RCA Analyzer)")
        self.resize(1400, 820)

        self.driver: Driver | None = None
        self.init_ui()
        self.load_config()  # 시작 시 저장된 설정 로드

    # ==========================================
    # 2. UI 레이아웃 구성
    # ==========================================
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(15, 12, 15, 15)
        main_layout.setSpacing(12)

        # [최상단] 가로배치 DB 접속 정보 입력 바
        db_bar_layout = QHBoxLayout()
        db_bar_layout.setSpacing(8)

        db_bar_layout.addWidget(QLabel("🌐 Neo4j URI:"))
        self.txt_uri = QLineEdit("bolt://localhost:7687")
        self.txt_uri.setFixedWidth(200)
        db_bar_layout.addWidget(self.txt_uri)

        db_bar_layout.addWidget(QLabel("👤 User:"))
        self.txt_user = QLineEdit("neo4j")
        self.txt_user.setFixedWidth(100)
        db_bar_layout.addWidget(self.txt_user)

        db_bar_layout.addWidget(QLabel("🔒 Password:"))
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setFixedWidth(130)
        db_bar_layout.addWidget(self.txt_password)

        self.btn_save_config = QPushButton("💾 접속정보 저장")
        self.btn_save_config.setStyleSheet(
            "padding: 3px 10px; font-weight: bold; background-color: #27AE60; color: white; border-radius: 3px;"
        )
        self.btn_save_config.clicked.connect(self.save_config)
        db_bar_layout.addWidget(self.btn_save_config)

        db_bar_layout.addStretch()
        main_layout.addLayout(db_bar_layout)

        # [중단] 요약 대시보드 구조화 (타이틀 + 상태 레이블 + 분석 버튼)
        timeline_wrapper = QWidget()
        timeline_layout = QVBoxLayout(timeline_wrapper)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(6)

        header_line_layout = QHBoxLayout()
        title_timeline = QLabel("📅 장애 타임라인 & 구간 요약 분석")
        title_timeline.setStyleSheet("font-weight: bold; color: #2C3E50; font-size: 14px;")
        header_line_layout.addWidget(title_timeline)
        header_line_layout.addStretch()

        self.lbl_status = QLabel("로그 파일을 업로드해주세요.")
        self.lbl_status.setStyleSheet(
            "color: #7F8C8D; font-weight: bold; font-size: 12px; margin-right: 10px;"
        )
        header_line_layout.addWidget(self.lbl_status)

        self.btn_open = QPushButton("📂 파일 선택 및 자동 분석")
        self.btn_open.setStyleSheet("""
            QPushButton {
                padding: 5px 14px;
                font-weight: bold;
                background-color: #34495E;
                color: white;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #2C3E50;
            }
        """)
        self.btn_open.clicked.connect(self.process_log_file)
        header_line_layout.addWidget(self.btn_open)
        timeline_layout.addLayout(header_line_layout)

        self.lbl_timeline = QLabel(
            "분석 전입니다. 우측 상단의 버튼을 통해 로그 파일을 선택하시면 탐지된 장애 피크 정보가 이곳에 표출됩니다."
        )
        self.lbl_timeline.setWordWrap(True)
        self.lbl_timeline.setStyleSheet("""
            background-color: #1E272C;
            color: #FFFFFF;
            padding: 15px;
            border-radius: 6px;
            font-size: 13px;
            line-height: 1.6;
        """)
        timeline_layout.addWidget(self.lbl_timeline)
        main_layout.addWidget(timeline_wrapper)

        # [하단 영역] 좌우 두 개의 그리드 배치 (Splitter 이용)
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)

        # [하단 왼쪽 그리드] Root Cause 랭킹
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)

        title_center = QLabel("🚨 만악의 근원 (Root Cause) 랭킹")
        title_center.setStyleSheet("font-weight: bold; color: #2C3E50; font-size: 13px;")
        center_layout.addWidget(title_center)

        self.table_root_cause = QTableWidget(0, 3)
        self.table_root_cause.setHorizontalHeaderLabels(
            ["우선순위", "메서드 (Class.Method)", "파생 에러 수"]
        )

        header = self.table_root_cause.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.table_root_cause.itemClicked.connect(self.on_root_cause_clicked)
        center_layout.addWidget(self.table_root_cause)
        bottom_splitter.addWidget(center_widget)

        # [하단 오른쪽 그리드] 장애 전파 체인 트리
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        title_right = QLabel("🌿 장애 전파 체인 트리 (CALLS)")
        title_right.setStyleSheet("font-weight: bold; color: #2C3E50; font-size: 13px;")
        right_layout.addWidget(title_right)

        self.tree_propagation = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["장애 전파 경로 (하위 -> 상위 호출단)"])
        self.tree_propagation.setModel(self.tree_model)
        right_layout.addWidget(self.tree_propagation)
        bottom_splitter.addWidget(right_widget)

        bottom_splitter.setSizes([700, 700])
        main_layout.addWidget(bottom_splitter)

    # ==========================================
    # 3. 설정 저장/로드 및 Base64 처리 로직
    # ==========================================
    def save_config(self):
        uri = self.txt_uri.text().strip()
        user = self.txt_user.text().strip()
        pwd = self.txt_password.text()

        encoded_pwd = base64.b64encode(pwd.encode("utf-8")).decode("utf-8")

        config_data = {"uri": uri, "user": user, "password": encoded_pwd}

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
            QMessageBox.information(
                self, "완료", "Neo4j 접속 정보가 로컬 파일에 안전하게 저장되었습니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "오류", f"설정 저장 실패: {str(e)}")

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            self.txt_uri.setText(config_data.get("uri", "bolt://localhost:7687"))
            self.txt_user.setText(config_data.get("user", "neo4j"))

            encoded_pwd = config_data.get("password", "")
            if encoded_pwd:
                decoded_pwd = base64.b64decode(encoded_pwd.encode("utf-8")).decode("utf-8")
                self.txt_password.setText(decoded_pwd)
        except Exception:
            pass

    def init_db_connection(self) -> bool:
        uri = self.txt_uri.text().strip()
        user = self.txt_user.text().strip()
        pwd = self.txt_password.text()

        try:
            if self.driver:
                self.driver.close()
            self.driver = GraphDatabase.driver(uri, auth=(user, pwd))

            with self.driver.session() as session:
                session.run(
                    "CREATE CONSTRAINT log_id_unique IF NOT EXISTS FOR (l:LogEntry) REQUIRE l.id IS UNIQUE;"
                )
                session.run(
                    "CREATE INDEX log_timestamp_idx IF NOT EXISTS FOR (l:LogEntry) ON (l.timestamp);"
                )
                session.run(
                    "CREATE INDEX method_name_idx IF NOT EXISTS FOR (m:Method) ON (m.name);"
                )
            return True
        except Exception as e:
            QMessageBox.critical(
                self, "DB 연결 실패", f"입력된 정보로 Neo4j에 접속할 수 없습니다:\n{str(e)}"
            )
            return False

    def closeEvent(self, event):
        if self.driver:
            self.driver.close()
        super().closeEvent(event)

    # ==========================================
    # 4. 비즈니스 로직 및 파싱
    # ==========================================
    def process_log_file(self):
        if not self.init_db_connection() or not self.driver:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Tomcat 로그 선택", "", "Log Files (*.out *.log);;All Files (*)"
        )
        if not file_path:
            return

        self.lbl_status.setText("Phase 1: 로그 파싱 및 DB 적재 중...")
        QApplication.processEvents()

        try:
            with self.driver.session() as session:
                session.run("MATCH (n) DETACH DELETE n")

            log_entries = []
            current_entry = None
            log_id_counter = 0

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    log_match = LOG_PATTERN.match(line)
                    if log_match:
                        timestamp_str, thread, level, clazz, message = log_match.groups()
                        if level.strip() in ["ERROR", "SEVERE", "WARN"] or "Exception" in message:
                            log_id_counter += 1
                            current_entry = {
                                "id": log_id_counter,
                                "timestamp": timestamp_str,
                                "level": level.strip(),
                                "message": message.strip(),
                                "stack_trace": [],
                            }
                            log_entries.append(current_entry)
                        else:
                            current_entry = None
                    else:
                        if current_entry:
                            stack_match = STACK_TRACE_PATTERN.match(line)
                            if stack_match:
                                pkg_class, method, file_name, line_num = stack_match.groups()
                                current_entry["stack_trace"].append(
                                    {
                                        "class_method": f"{pkg_class}.{method}",
                                        "line": line_num if line_num else "0",
                                    }
                                )

            if not log_entries:
                QMessageBox.warning(self, "안내", "분석할 에러/예외 로그가 발견되지 않았습니다.")
                self.lbl_status.setText("분석 대상 에러 없음.")
                return

            self.bulk_insert_to_neo4j(log_entries)

            self.lbl_status.setText("Phase 2: 원인 분석 진행 중...")
            QApplication.processEvents()

            self.analyze_and_render()
            self.lbl_status.setText("💡 분석 완료")

        except Exception as e:
            QMessageBox.critical(self, "오류", f"처리 중 오류 발생: {str(e)}")
            self.lbl_status.setText("오류로 중단됨.")

    def bulk_insert_to_neo4j(self, entries):
        if not self.driver:
            return
        query = """
        UNWIND $batch AS entry
        CREATE (l:LogEntry {id: entry.id, timestamp: datetime(replace(entry.timestamp, ' ', 'T')), level: entry.level, message: entry.message})
        WITH l, entry.stack_trace AS traces
        WHERE size(traces) > 0

        UNWIND range(0, size(traces)-1) AS idx
        WITH l, traces, idx, traces[idx] AS current_trace
        MERGE (m:Method {name: current_trace.class_method})

        FOREACH (_ IN CASE WHEN idx = 0 THEN [1] ELSE [] END |
            CREATE (l)-[:DETECTED_IN]->(m)
        )

        WITH l, traces, idx, m
        WHERE idx < size(traces) - 1
        WITH l, m AS cause_method, traces[idx+1] AS parent_trace
        MERGE (target_method:Method {name: parent_trace.class_method})
        MERGE (cause_method)-[:CALLS]->(target_method)
        """
        batch_size = 5000
        for i in range(0, len(entries), batch_size):
            batch = entries[i : i + batch_size]
            with self.driver.session() as session:
                session.run(query, batch=batch)

    # ==========================================
    # 5. 분석 결과 렌더링 (장애 유형 비율 가로 정렬 반영)
    # ==========================================
    def analyze_and_render(self):
        if not self.driver:
            return
        with self.driver.session() as session:
            query_timeline = """
            MATCH (l:LogEntry)
            WITH l.timestamp AS ts, l
            ORDER BY ts
            WITH datetime({year: ts.year, month: ts.month, day: ts.day, hour: ts.hour, minute: ts.minute - (ts.minute % 5)}) AS window_start, l
            RETURN
                window_start,
                window_start + duration({minutes: 5}) AS window_end,
                count(l) AS error_count,
                sum(case when l.message CONTAINS 'Hikari' or l.message CONTAINS 'Timeout' or l.message CONTAINS 'Connection' then 1 else 0 end) AS db_errors
            ORDER BY error_count DESC
            LIMIT 1
            """
            result_time = session.run(query_timeline).single()
            if not result_time:
                self.lbl_timeline.setText("장애 피크 타임을 계산할 수 없습니다.")
                return

            w_start = result_time["window_start"].isoformat()
            w_end = result_time["window_end"].isoformat()
            total_err = result_time["error_count"]
            db_err = result_time["db_errors"]

            db_ratio = (db_err / total_err * 100) if total_err > 0 else 0
            code_ratio = 100 - db_ratio

            # [수정 반영] 우측 테이블 셀 내의 <br>을 제거하고 가로 한 줄 구조로 최적화
            summary_text = (
                f"<table width='100%' style='color: #FFFFFF; font-size: 13px; border-collapse: collapse;'>"
                f"  <tr>"
                f"    <td width='50%' style='vertical-align: top; padding-right: 20px; border-right: 1px solid #3A4B53;'>"
                f"      <b style='font-size:14px; color:#2ECC71;'>🔍 탐지된 장애 핵심 시간대 (Time Window)</b><br>"
                f"      • <b>시작 일시:</b> {w_start}<br>"
                f"      • <b>종료 일시:</b> {w_end}"
                f"    </td>"
                f"    <td width='50%' style='vertical-align: top; padding-left: 20px;'>"
                f"      <b style='font-size:14px; color:#E67E22;'>📊 해당 피크 구간 통계 요약</b><br>"
                f"      • <b>총 에러 발생량:</b> <span style='font-size:14px; color:#FF5733;'><b>{total_err}</b></span> 건<br>"
                f"      • <b>장애 유형 비율:</b> DB 병목 <b>{db_ratio:.1f}%</b> | 소스코드 버그 및 기타 <b>{code_ratio:.1f}%</b>"
                f"    </td>"
                f"  </tr>"
                f"</table>"
            )
            self.lbl_timeline.setText(summary_text)

            query_root_cause = """
            MATCH (l:LogEntry)-[:DETECTED_IN]->(m:Method)
            WHERE l.timestamp >= datetime($start) AND l.timestamp <= datetime($end)
            OPTIONAL MATCH (m)-[:CALLS]->(parent)
            WITH m.name AS method_name, count(distinct l) AS direct_errors, count(distinct parent) AS spread_count,
                 case when m.name CONTAINS 'Hikari' or m.name CONTAINS 'Connection' or m.name CONTAINS 'Timeout' then 1 else 0 end AS is_db
            RETURN method_name, (direct_errors + spread_count) AS impact_score
            ORDER BY is_db DESC, impact_score DESC
            LIMIT 30
            """
            result_root = session.run(query_root_cause, start=w_start, end=w_end)

            self.table_root_cause.setRowCount(0)
            for idx, record in enumerate(result_root):
                row = self.table_root_cause.rowCount()
                self.table_root_cause.insertRow(row)
                self.table_root_cause.setItem(row, 0, QTableWidgetItem(str(idx + 1)))
                self.table_root_cause.setItem(row, 1, QTableWidgetItem(record["method_name"]))
                self.table_root_cause.setItem(row, 2, QTableWidgetItem(str(record["impact_score"])))

    def on_root_cause_clicked(self, item: QTableWidgetItem):
        if not self.driver:
            return
        row = item.row()
        cell_item = self.table_root_cause.item(row, 1)
        if cell_item is None:
            return

        method_name = cell_item.text()

        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels([f"'{method_name}' 파생 전파 체인"])

        query_path = """
        MATCH path = (m:Method {name: $name})-[:CALLS*1..5]->(parent:Method)
        RETURN [n in nodes(path) | n.name] AS call_chain
        LIMIT 20
        """
        with self.driver.session() as session:
            results = session.run(query_path, name=method_name)

            root_node = QStandardItem(f"Root: {method_name}")
            self.tree_model.appendRow(root_node)

            for record in results:
                chain = record["call_chain"]
                current_item = root_node
                for step in chain[1:]:
                    found_item = None
                    for r in range(current_item.rowCount()):
                        child = current_item.child(r)
                        if child and child.text() == step:
                            found_item = child
                            break
                    if found_item:
                        current_item = found_item
                    else:
                        new_item = QStandardItem(f"└─ CALLS ➔ {step}")
                        current_item.appendRow(new_item)
                        current_item = new_item

        self.tree_propagation.expandAll()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    analyzer = TomcatRCAAnalyzer()
    analyzer.show()
    sys.exit(app.exec())
