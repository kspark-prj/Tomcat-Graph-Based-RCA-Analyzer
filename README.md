# 😾 WAS/Application Log Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyArrow Streaming Bulk Ingestion Powered High-Performance Log RCA Tool**

`WAS/Application Log Graph-Based RCA Analyzer`는 Spring Boot, Apache Tomcat, WildFly/JBoss 등 다양한 WAS 및 애플리케이션의 대용량 로그 파일(`*.log`, `*.out`)을 분석하여 장애 원인을 그래프 데이터 모델로 구축하고, 구조화된 인메모리 마이닝 기법을 통해 **근본 원인(Root Cause Analysis, RCA)** 및 장애 전파 체인을 추적하는 데스크톱 GUI 진단 도구입니다.

임베디드 그래프 DB인 **Kùzu**와 **PyArrow Zero-Copy Streaming Ingestion** 기법을 결합하여 수 GB 이상의 대용량 로그도 **RAM 1GB 이내의 플랫한 메모리 점유율**로 초고속 인덱싱하며, 단순한 텍스트 매칭을 넘어 에러의 상위 호출 지점, 스레드 영향도, `Caused by` 인과 관계, StackTrace 체인을 유기적으로 시각화 및 분석합니다.

---

## ✨ 핵심 기능 (Key Features)

- **Chunked Streaming & Low-Memory Ingestion**: 전체 로그를 한 번에 메모리에 올리지 않고 청크(Chunk, 5,000건 단위)별 분할 배치 스트리밍 처리를 수행합니다. 배치 완료 시 수동 Garabage Collection(`gc.collect()`)을 실행하여 수 GB 대용량 파일 분석 시에도 피크 RAM 점유를 1GB 이내로 안정적으로 제어합니다.

- **2-Pass Node/Relationship Separation Ingestion**: 인덱스 탐색 병목 및 B+Tree 락 경합을 방지하기 위해 **[Pass 1: Node 선제 주입]** 후 **[Pass 2: Relationship 일괄 주입]** 구조로 데이터베이스 주입 파이프라인을 완전 분리했습니다.
- **App-Level Global Caching**: 프로그램 동작 중 중복 생성되는 노드(`Thread`, `Class`, `Method`) 존재 여부를 파이썬 메모리 레벨에서 추적합니다. DB 단의 PK 중복 체크 연산을 전면 차단하여 벌크 주입 속도를 극대화했습니다.
- **PyArrow Zero-Copy & Kùzu Buffer Pool Optimization**: Apache Arrow 메모리 구조에서 C++ 엔진인 Kùzu DB 버퍼로 Zero-Copy 직렬화 주입을 수행하며, DB 오픈 시 명시적 버퍼 풀 메모리 할당(예: 4GB)을 적용해 Disk I/O 병목을 근본적으로 제거했습니다.
- **Trace Propagation Chain & QTreeView Event Binding**: 최다 발생 근본 원인(Root Cause) 및 최근 발생 랭킹 표의 항목을 클릭하면, 선택한 메서드의 상세 Exception 발생 이력, `Caused By` 뿌리 원인, 상세 `Stack Trace` 샘플, 상위 호출자(`CALLS`) 체인을 트리 구조(`QTreeView`)로 즉시 계층 렌더링합니다.

- **Multi-WAS & Application Log Auto-Detection**: Spring Boot, Apache Tomcat/Log4j, WildFly/JBoss(server.log) 등 대표적인 3가지 로그 포맷을 정규표현식으로 자동 정밀 탐지하여 통합 분석합니다.

- **Auto-Diagnosis Engine (Post-Mortem Report)**: 수집된 예외 데이터를 분석하여 4대 장애 등급(🔴 DB 병목, ⚡ 외부 망/SFTP 유실, 🔑 인증/보안 결함, 💻 애플리케이션 로직 에러)을 분류하고 도메인별 지분율 산출 및 트러블슈팅 권고사항을 담은 사후 진단서를 자동 작성합니다.

- **Zero-Flicker Custom Splash Screen**: PyInstaller 네이티브 스플래시와 PyQt6 커스텀 오버레이 스플래시 간의 Seamless 핸드오버 지연 버퍼 기술을 적용하여 잔상/깜빡임 없는 깔끔한 로딩 UI 환경을 제공합니다.

---

## 🛠️ 사용 기술 (Tech Stack)

- **Language**: Python 3.x

- **Graph Database Engine**: [Kùzu](https://www.google.com/search?q=https://kuzudb.com/) (Embedded Graph Database)

- **In-Memory Data Framework**: Apache Arrow (`pyarrow`)

- **GUI Framework**: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)

- **Pattern Matching**: Regular Expressions (Regex) & Dynamic Line Parsing

---

## 📐 그래프 데이터베이스 모델 스키마

에러가 발생한 지점의 연쇄 관계를 규명하기 위해 아래와 같은 그래프 토폴로지 구조를 구축합니다.

- `(Thread) -[:RAISED]-> (Exception)` : 특정 스레드에서 예외 발생

- `(Exception) -[:CAUSED_BY]-> (Exception)` : 상위 예외와 Root Cause 예외 간 인과 관계 추적

- `(Exception) -[:OCCURRED_IN]-> (Method)` : 해당 예외가 특정 메서드 내에서 발현

- `(Method) -[:BELONGS_TO]-> (Class)` : 메서드가 속한 클래스 구조 정의

- `(Method) -[:CALLS]-> (Method)` : 스택 트레이스 기반 상위/하위 호출 흐름 연결 (`Caller -> Callee`)

---

## 🔄 파이프라인 및 아키텍처 (Architecture & Flow)

로그 파일의 텍스트 스트림이 메모리 상에서 정제된 뒤 PyArrow를 거쳐 Kùzu DB 노드와 관계(Edge)로 고속 변환되는 고성능 파이프라인 프로세스입니다.

### 1. 처리 알고리즘 흐름

```text
[ 1. 파일 안전 해제 및 기존 Kùzu DB 안전 삭제/초기화 ]
                   ↓
[ 2. 로그 패턴 자동 감지 (Spring Boot / Tomcat / WildFly) ]
                   ↓
[ 3. 청크 단위 라인 스트리밍 파싱 (5,000 ERROR Context 단위) ]
                   ↓
[ 4. 앱 레벨 글로벌 캐싱(Global Caching) 기반 중복 노드 필터링 ]
                   ↓
[ 5. [Pass 1] Node 데이터 PyArrow Zero-Copy 직렬화 & Kùzu 'COPY FROM' 주입 ]
                   ↓
[ 6. [Pass 2] Relationship 데이터 PyArrow Zero-Copy & Kùzu 'COPY FROM' 주입 ]
                   ↓
[ 7. 청크 메모리 초기화 및 명시적 GC(gc.collect()) 호출 ]
                   ↓
[ 8. 인메모리 마이닝 기반 사후 진단 보고서 작성 (Post-Mortem Report) ]
                   ↓
[ 9. QTableWidget 랭킹 생성 & 셀 클릭 시 QTreeView 전파 체인 동적 로딩 ]

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

### 2. 프로젝트 실행

구동 환경이 준비되면 메인 스크립트를 실행합니다.

```bash
python main.py

```

### 3. PyInstaller 단일 실행 파일 패키징 (바이너리 빌드)

PyInstaller 네이티브 스플래시 화면 및 아이콘 리소스가 포함된 원클릭 패키징 명령어입니다.

```bash
pyinstaller -w -D --noupx --clean --icon=main.ico --add-data "splash.png;." --splash splash.png --exclude-module PIL --exclude-module Pillow --exclude-module tkinter --exclude-module unittest --exclude-module PyQt6.QtWebEngineCore --exclude-module PyQt6.Qt3D --exclude-module PyQt6.QtQuick main.py

```

---

## 💡 주요 코드 하이라이트

### 1. PyArrow 기반 2-Pass 노드/관계 분리 벌크 주입 (`LogParseWorker`)

노드를 먼저 주입하여 Graph DB 엔진 내부의 Primary Key 인덱스 공간을 확정한 뒤, 관계(Edge)를 일괄 주입하는 2-Pass 아키텍처입니다.

```python
# Pass 1: Exception 노드 벌크 주입
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

# Pass 2: OCCURRED_IN 관계(Edge) 벌크 주입
o_table = pa.Table.from_arrays(
    [
        pa.array([v[0] for v in chunk_occurred_in_set], type=pa.string()),
        pa.array([v[1] for v in chunk_occurred_in_set], type=pa.string()),
    ],
    names=["from", "to"],
)
conn.execute("COPY OCCURRED_IN FROM o_table")

```

### 2. Kùzu DB 버퍼 풀 할당 및 메모리 관리

대용량 I/O 병목을 없애기 위해 Kùzu 데이터베이스 세션 연결 시 메모리 버퍼 풀 크기를 제어합니다.

```python
# Kùzu 버퍼 풀 메모리 4GB 할당 예시
KUZU_BUFFER_POOL_SIZE = 4 * 1024 * 1024 * 1024
db = kuzu.Database(DB_PATH, buffer_pool_size=KUZU_BUFFER_POOL_SIZE)
conn = kuzu.Connection(db)

```

### 3. 셀 클릭을 통한 에러 전파 체인(`QTreeView`) 실시간 복원

테이블 클릭 시 선택된 메서드의 `Exception`, `Caused By`, `StackTrace`, `CALLS` 관계를 Cypher 쿼리로 동적 조회하여 트리에 시각화합니다.

```python
# Caused By 원인 분석 체인 및 호출 경로 조회[cite: 3]
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
