# 😾 WAS/Application Log Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyArrow Powered High-Performance Log RCA Tool**

`WAS/Application Log Graph-Based RCA Analyzer`는 Spring Boot, Apache Tomcat, WildFly/JBoss 등 다양한 WAS 및 애플리케이션의 대용량 로그 파일(`*.log`, `*.out`)을 파싱하여 장애 원인을 그래프 데이터 모델로 구축하고, 구조화된 인메모리 마이닝 기법을 통해 **근본 원인(Root Cause Analysis, RCA)** 및 장애 전파 체인을 추적하는 데스크톱 GUI 진단 도구입니다.

초고속 임베디드 그래프 DB인 **Kùzu**와 **PyArrow In-Memory Bulk Insertion** 기법을 결합하여 대용량 로그도 수 초 내에 고속 인덱싱하며, 단순한 텍스트 매칭을 넘어 에러의 상위 호출 지점, 스레드 영향도, `Caused by` 인과 관계, StackTrace 체인을 유기적으로 시각화 및 분석합니다.

---

## ✨ 핵심 기능 (Key Features)

- **PyArrow In-Memory Bulk Insertion Engine**: 파싱 데이터를 메모리 버퍼(`set`/`dict`)에 집계한 후 Apache Arrow 테이블로 변환하여 Kùzu의 `COPY FROM` 명령어로 **초고속 벌크 인덱싱**을 수행합니다. 디스크 I/O 병목을 제거하여 대용량 로그 처리 성능이 비약적으로 향상되었습니다.
- **Single-Pass High-Efficiency Log Parsing**: 파일 크기 바이트 기반 진행률 계산 알고리즘을 도입하여 2차 파일 스캔(Double Pass)을 철저히 제거했습니다. 정규식 오버헤드를 줄인 문자열 패턴 슬라이싱 기법을 적용해 CPU 연산 효율을 극대화했습니다.
- **Trace Propagation Chain & QTreeView Event Binding**: 최다 발생 근본 원인(Root Cause) 및 최근 발생 랭킹 표의 항목을 클릭하면, 선택한 메서드의 상세 Exception 발생 이력, `Caused By` 뿌리 원인, 상세 `Stack Trace` 샘플, 상위 호출자(`CALLS`) 체인을 트리 구조(`QTreeView`)로 즉시 계층 렌더링합니다.
- **Multi-WAS & Application Log Auto-Detection**: Spring Boot, Apache Tomcat/Log4j, WildFly/JBoss(server.log) 등 대표적인 3가지 로그 포맷을 정규표현식으로 자동 정밀 탐지하여 통합 분석합니다.
- **Graph DB Architecture**: `Thread -> Exception -> Method -> Class`로 이어지는 스택 트레이스 및 `Exception -[:CAUSED_BY]-> Exception`의 뿌리 원인 체인을 그래프 모델 스키마로 설계하여 Cypher 쿼리로 정밀 추적합니다.
- **Auto-Diagnosis Engine (Post-Mortem Report)**: 수집된 예외 데이터를 분석하여 4대 장애 등급(🔴 DB 병목, ⚡ 외부 망/SFTP 유실, 🔑 인증/보안 결함, 💻 애플리케이션 로직 에러)을 분류하고 도메인별 지분율 산출 및 트러블슈팅 권고사항을 담은 사후 진단서를 자동 작성합니다.
- **Safe Multi-Threading & Resource Management**: 백그라운드 `QThread` 워커 시스템을 탑재하고, 파일 분석 전후 및 수동 초기화 시 Connection/Database 자원 해제와 Python Garbage Collection(`gc.collect()`)을 명시적으로 수행하여 File Lock 충돌 및 메모리 누수를 완전히 차단합니다.

---

## 🛠️ 사용 기술 (Tech Stack)

- **Language**: Python 3.x
- **Graph Database Engine**: [Kùzu](https://www.google.com/search?q=https://kuzudb.com/) (Embedded Graph Database)
- **In-Memory Data Framework**: Apache Arrow (`pyarrow`)
- **GUI Framework**: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)
- **Pattern Matching**: Regular Expressions (Regex) & Fast String Slicing

---

## 📐 그래프 데이터베이스 모델 스키마

에러가 발생한 지점의 연쇄 관계를 규명하기 위해 아래와 같은 그래프 토폴로지 구조를 구축합니다.

- `(Thread) -[:RAISED]-> (Exception)` : 특정 스레드에서 예외 발생
- `(Exception) -[:CAUSED_BY]-> (Exception)` : 상위 예외와 Root Cause 예외 간 인과 관계 추적
- `(Exception) -[:OCCURRED_IN]-> (Method)` : 해당 예외가 특정 메서드 내에서 발현
- `(Method) -[:BELONGS_TO]-> (Class)` : 메서드가 속한 클래스 구조 정의
- `(Method) -[:CALLS]-> (Method)` : 스택 트레이스 기반 상위/하위 호출 흐름 연결 (`Caller -> Callee`)

---

## 🔄 로그 기반 데이터 변환 및 저장 프로세스 (Parsing & Indexing Flow)

로그 파일의 텍스트가 메모리 상에서 유일(Unique) 엔티티로 정제된 뒤 PyArrow를 거쳐 Kùzu DB 노드와 관계(Edge)로 고속 변환되는 전체 프로세스입니다.

### 1. 처리 알고리즘 흐름

```text
[ 1. 기존 DB 안전 해제 및 메모리 초기화 ]
                   ↓
[ 2. 로그 패턴 자동 감지 (Spring Boot / Tomcat / WildFly) ]
                   ↓
[ 3. Single-Pass 로그 스캔 & 에러/Caused by/StackTrace 패턴 정제 ]
                   ↓
[ 4. Python set/dict 메모리 버퍼에 유일 엔티티 및 관계 집계 ]
                   ↓
[ 5. PyArrow Table 변환 및 Kùzu 'COPY FROM' 초고속 벌크 인덱싱 ]
                   ↓
[ 6. 인메모리 마이닝 기반 사후 진단 보고서 작성 ]
                   ↓
[ 7. QTableWidget 랭킹 생성 & 셀 클릭 시 QTreeView 전파 체인 동적 로딩 ]

```

### 2. 지원하는 로그 포맷 예시

1. **Spring Boot 포맷**: `2026-07-23T14:30:15.123+09:00 ERROR 12345 --- [http-nio-8080-exec-5] com.example.Controller : Error msg`
2. **Tomcat / Standard Log4j 포맷**: `2026-07-23 14:30:15.123 [http-nio-8080-exec-5] ERROR com.example.Controller - Error msg`
3. **WildFly / JBoss server.log 포맷**: `2026-07-23 14:30:15,123 ERROR [com.example.Controller] (default task-1) Error msg`

---

## 🚀 시작하기 (Getting Started)

### 1. 필수 패키지 설치

프로젝트 실행을 위해 아래 라이브러리들을 설치해야 합니다.

```bash
pip install PyQt6 kuzu pyarrow

```

```bash
# 단일 바이너리 빌드
pyinstaller -w -D --noupx --clean --icon=main.ico --exclude-module PIL --exclude-module Pillow --exclude-module tkinter --exclude-module unittest --exclude-module PyQt6.QtWebEngineCore --exclude-module PyQt6.Qt3D --exclude-module PyQt6.QtQuick unified_rca_analyzer.py
```

### 2. 프로젝트 실행

구동 환경이 준비되면 메인 스크립트를 실행합니다.

```bash
python unified_log_analyzer.py

```

---

## 💡 주요 코드 하이라이트

### 1. PyArrow 기반 `COPY FROM` 벌크 인덱싱

단일 Insert 쿼리 대신 PyArrow 메모리 테이블을 구축하여 Kùzu에 벌크 인덱싱을 수행합니다.

```python
# Exception 데이터 PyArrow 변환 및 초고속 COPY FROM 실행
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

```

### 2. 셀 클릭을 통한 에러 전파 체인(`QTreeView`) 실시간 복원

테이블 클릭 시 선택된 메서드의 `Exception`, `Caused By`, `StackTrace`, `CALLS` 관계를 Cypher 쿼리로 동적 조회하여 트리에 시각화합니다.

```python
# Caused By 원인 분석 체인 및 호출 경로 조회
cb_query = """
MATCH (ex:Exception {id: $ex_id})-[:CAUSED_BY]->(child:Exception)
RETURN child.type, child.message
"""
res_cb = self.conn.execute(cb_query, {"ex_id": ex_id})
while res_cb.has_next():
    c_type, c_msg = res_cb.get_next()
    ex_item.appendRow(QStandardItem(f"  └─ 💥 Caused by: {c_type}: {c_msg}"))

```

---

## 📄 라이선스 (License)

이 프로젝트는 MIT 라이선스 하에 자유롭게 수정 및 배포가 가능합니다.
