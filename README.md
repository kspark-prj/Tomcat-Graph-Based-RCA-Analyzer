# 😾 WAS/Application Log Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyArrow Streaming Bulk Ingestion Powered High-Performance Log RCA & Git History Tracking Tool**

`WAS/Application Log Graph-Based RCA Analyzer`는 Spring Boot, Apache Tomcat, WildFly/JBoss 등 다양한 WAS 및 애플리케이션의 대용량 로그 파일(`*.log`, `*.out`)을 분석하여 장애 원인을 그래프 데이터 모델로 구축하고, 구조화된 인메모리 마이닝 기법을 통해 **근본 원인(Root Cause Analysis, RCA)**, 장애 전파 체인, 그리고 **Git 로컬 저장소 커밋/Diff 히스토리**까지 종합 추적하는 데스크톱 GUI 진단 도구입니다.

임베디드 그래프 DB인 **Kùzu**와 **PyArrow Zero-Copy Streaming Ingestion** 기법을 결합하여 수 GB 이상의 대용량 로그도 **RAM 1GB 이내의 플랫한 메모리 점유율**로 초고속 인덱싱하며, 단순한 텍스트 매칭을 넘어 에러의 상위 호출 지점, 스레드 영향도, `Caused by` 인과 관계, StackTrace 체인을 유기적으로 시각화 및 분석합니다.

---

## ✨ 핵심 기능 (Key Features)

- **Git Local Repository & Diff Viewer Integration (NEW)**: 로컬 Git 저장소 경로를 등록하면, 장애 파급 체인(`QTreeView`)의 메서드/클래스를 클릭했을 때 관련된 Git 커밋 이력(`GitHistoryDialog`)과 변경 내역 Diff(`GitDiffDialog`)을 GUI 팝업으로 즉시 조회하여 코드 변경점과의 연관성을 분석할 수 있습니다.

- **Single Instance & Window Focus Activation (NEW)**: `QSharedMemory` 기반 중복 실행 방지 메커니즘을 적용하여 이미 프로그램이 구동 중인 경우 기존 창을 최상단으로 복원 및 포커싱(`SetForegroundWindow`)하며 중복 프로세스 실행을 차단합니다.

- **Enterprise Domestic Encoding Fallback (NEW)**: 국내 엔터프라이즈 및 legacy 환경(Windows/Tomcat/WildFly)에서 발생하는 UTF-8 및 CP949/EUC-KR 인코딩 혼용 로그를 자동 감지하여 깨짐 없이 안정적으로 오픈합니다.

- **Chunked Streaming & Low-Memory Ingestion**: 전체 로그를 한 번에 메모리에 올리지 않고 청크(Chunk, 5,000건 단위)별 분할 배치 스트리밍 처리를 수행합니다. 배치 완료 시 수동 Garbage Collection(`gc.collect()`)을 실행하여 수 GB 대용량 파일 분석 시에도 피크 RAM 점유를 1GB 이내로 안정적으로 제어합니다.

- **2-Pass Node/Relationship Separation Ingestion**: 인덱스 탐색 병목 및 B+Tree 락 경합을 방지하기 위해 **[Pass 1: Node 선제 주입]** 후 **[Pass 2: Relationship 일괄 주입]** 구조로 데이터베이스 주입 파이프라인을 완전 분리했습니다.

- **App-Level Global Caching**: 프로그램 동작 중 중복 생성되는 노드(`Thread`, `Class`, `Method`) 존재 여부를 파이썬 메모리 레벨에서 추적합니다. DB 단의 PK 중복 체크 연산을 전면 차단하여 벌크 주입 속도를 극대화했습니다.

- **PyArrow Zero-Copy & Kùzu Buffer Pool Optimization**: Apache Arrow 메모리 구조에서 C++ 엔진인 Kùzu DB 버퍼로 Zero-Copy 직렬화 주입을 수행하며, DB 오픈 시 명시적 버퍼 풀 메모리 할당(예: 4GB)을 적용해 Disk I/O 병목을 근본적으로 제거했습니다.

- **Trace Propagation Chain & QTreeView Event Binding**: 최다 발생 근본 원인(Root Cause) 및 최근 발생 랭킹 표의 항목을 클릭하면, 선택한 메서드의 상세 Exception 발생 이력, `Caused By` 뿌리 원인, 상세 `Stack Trace` 샘플, 상위 호출자(`CALLS`) 체인을 트리 구조(`QTreeView`)로 비동기 QThread 환경에서 정밀 계층 렌더링합니다.

- **Multi-WAS & Application Log Auto-Detection**: Spring Boot(2.x/3.x), Apache Tomcat/Log4j2/Logback, WildFly/JBoss(server.log), WebLogic/Generic WAS 등 대표적인 로그 포맷을 정규표현식으로 자동 정밀 탐지하여 통합 분석합니다.

- **Auto-Diagnosis Engine (Post-Mortem Report)**: 수집된 예외 데이터를 정밀 분석하여 **9대 장애 등급**(🧠 JVM 메모리/자원 고갈, 🔴 DB 병목, ⚡ 스레드 풀/동시성 병목, 🌐 외부 연동망/SFTP, 📨 메시지 큐/캐시, 🔑 인증/보안 결함, ⚙️ 설정/배포 환경 예외, 📦 데이터 파싱/바인딩, 💻 애플리케이션 로직 에러)을 분류하고, 도메인별 지분율 산출(RCA 지표) 및 엔지니어 트러블슈팅 권고사항을 담은 사후 진단서를 자동 작성합니다.

- **10-Step Timeline Distribution Chart**: 전체 장애 발생 기간을 10개 구간으로 자동 분할하고 구간별 에러 발생 건수 및 비율을 역순 정렬 타임라인 프로그레스 바로 한눈에 파악할 수 있도록 시각화합니다.

- **Zero-Flicker Custom Splash Screen**: PyInstaller 네이티브 스플래시와 PyQt6 커스텀 오버레이 스플래시 간의 Seamless 핸드오버 지연 버퍼 기술을 적용하여 잔상/깜빡임 없는 깔끔한 로딩 UI 환경을 제공합니다.

---

## 🛠️ 사용 기술 (Tech Stack)

- **Language**: Python 3.x

- **Graph Database Engine**: [Kùzu](https://www.google.com/search?q=https://kuzudb.com/) (Embedded Graph Database)

- **In-Memory Data Framework**: Apache Arrow (`pyarrow`)

- **GUI Framework**: PyQt6

- **Pattern Matching & VCS Integration**: Regular Expressions (Regex) & Git CLI Dynamic Subprocess Integration

---

## 📐 그래프 데이터베이스 모델 스키마

에러가 발생한 지점의 연쇄 관계를 규명하기 위해 아래와 같은 그래프 토폴로지 구조를 구축합니다.

- `(Thread) -[:RAISED]-> (Exception)` : 특정 스레드에서 예외 발생

- `(Exception) -[:CAUSED_BY]-> (Exception)` : 상위 예외와 Root Cause 예외 간 인과 관계 추적

- `(Exception) -[:OCCURRED_IN]-> (Method)` : 해당 예외가 특정 메서드 내에서 발현

- `(Method) -[:BELONGS_TO]-> (Class)` : 메서드가 속한 클래스 구조 정의

- `(Method) -[:CALLS]-> (Method)` : 스택 트레이스 기반 상위/하위 호출 흐름 연결 (`Caller -> Callee`)

---

## 🚦 9대 장애 진단 분류 체계 (RCA Categories)

시스템에서 분류하는 9가지 장애 등급과 주요 감지 예외 항목은 다음과 같습니다.

| 장애 등급         | 분류 명칭                          | 주요 감지 예외 및 키워드 예시                                                                                      |
| ----------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 🧠 **Category 1** | **JVM 메모리 및 시스템 자원 고갈** | `OutOfMemoryError`, `Metaspace`, `GC overhead limit`, `StackOverflowError`, `Too many open files`<br>              |
| 🔴 **Category 2** | **데이터베이스 영역 (DB 병목)**    | `SQL`, `Hikari`, `Connection`, `Deadlock`, `Constraint`, `Timeout`<br>                                             |
| ⚡ **Category 3** | **스레드 풀 및 동시성 병목**       | `RejectedExecutionException`, `TaskRejectedException`, `ThreadPoolExecutor`, `ConcurrentModificationException`<br> |
| 🌐 **Category 4** | **외부 연동망 및 SFTP 장애**       | `ConnectException`, `SocketTimeout`, `HttpClient`, `UnknownHost`, `SFTP`<br>                                       |
| 📨 **Category 5** | **메시지 큐 및 캐시 장애**         | `KafkaException`, `RedisConnectionException`, `AmqpException`, `RabbitMq`, `Jedis`, `Lettuce`<br>                  |
| 🔑 **Category 6** | **인증 및 보안 장애**              | `Unauthorized`, `OAuth2`, `JWT`, `ExpiredToken`, `SignatureException`, `AccessDenied`<br>                          |
| ⚙️ **Category 7** | **설정 및 배포/환경 예외**         | `BeanCreationException`, `ClassNotFoundException`, `NoSuchMethodError`, `YamlException`<br>                        |
| 📦 **Category 8** | **데이터 파싱 / 바인딩 에러**      | `HttpMessageNotReadableException`, `JsonParseException`, `MethodArgumentNotValidException`<br>                     |
| 💻 **Category 9** | **애플리케이션 로직 에러**         | 기타 일반 런타임 NullPointer 등 소스코드 내부 결함                                                                 |

|

---

## 🔄 파이프라인 및 아키텍처 (Architecture & Flow)

로그 파일의 텍스트 스트림이 메모리 상에서 정제된 뒤 PyArrow를 거쳐 Kùzu DB 노드와 관계(Edge)로 고속 변환되는 고성능 파이프라인 프로세스입니다.

### 1. 지원하는 로그 포맷 예시

1. **Spring Boot 포맷**: `2026-07-23T14:30:15.123+09:00 ERROR 12345 --- [http-nio-8080-exec-5] com.example.Controller : Error msg`
2. **Tomcat / Standard Log4j 포맷**: `2026-07-23 14:30:15.123 [http-nio-8080-exec-5] ERROR com.example.Controller - Error msg`
3. **WildFly / JBoss server.log 포맷**: `2026-07-23 14:30:15,123 ERROR [com.example.Controller] (default task-1) Error msg`
4. **WebLogic / Generic WAS 포맷**: `<2026-07-23T14:30:15.123> <ERROR> <AdminServer> <http-nio-8080-exec-5> Error msg`

---

## 🚀 시작하기 (Getting Started)

### 1. 프로젝트 실행

구동 환경이 준비되면 메인 스크립트를 실행합니다.

```bash
uv run main.py

```

### 3. PyInstaller 단일 실행 파일 패키징 (바이너리 빌드)

PyInstaller 네이티브 스플래시 화면 및 아이콘 리소스가 포함된 원클릭 패키징 명령어입니다.

```bash
uv run pyinstaller -w -D --noupx --clean --icon=main.ico --add-data "splash.png;." --exclude-module PIL --exclude-module Pillow --exclude-module tkinter --exclude-module unittest --exclude-module PyQt6.QtWebEngineCore --exclude-module PyQt6.Qt3D --exclude-module PyQt6.QtQuick main.py
```

### kuzu explorer docker 웹 대시보드

docker run -p 8000:8000 \
 -v "/path/to/your/project:/database" \
 -e KUZU_FILE=kuzu_unified_log_db \
 -e MODE=READ_ONLY \
 --rm kuzudb/explorer:0.11.3

### 1. Kùzu Explorer 그래프 시각화 쿼리

Explorer의 강점인 노드와 관계선 시각화 그래프를 바로 확인할 수 있는 쿼리입니다.

#### ① 스레드 ➔ 예외 ➔ 발생 메서드 전체 체인 시각화

    MATCH (t:Thread)-[r1:RAISED]->(ex:Exception)-[r2:OCCURRED_IN]->(m:Method)
    RETURN *
    LIMIT 50;

#### ② 예외 전파 체인 (Caused By) 시각화

상위 예외와 이를 유발한 하위 근본 예외 간의 인과관계를 그래프로 확인합니다.
MATCH (parent:Exception)-[r:CAUSED_BY]->(child:Exception)
RETURN \*
LIMIT 30;

#### ③ 메서드 간 호출 관계 (CALLS) 시각화

에러 발생 지점을 호출한 메서드들의 호출 흐름을 확인합니다.

    MATCH (caller:Method)-[r:CALLS]->(callee:Method)
    RETURN *
    LIMIT 30;

### 2. 근본 원인 분석 (RCA) 통계 쿼리

#### ① 에러 발생 빈도 1위 메서드 Top 10 (장애 유발 핵심 메서드)

어떤 메서드에서 가장 많은 에러가 터졌는지 집계합니다.

    MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
    RETURN m.fullName AS MethodName, ex.type AS ExceptionType, count(ex) AS ErrorCount
    ORDER BY ErrorCount DESC
    LIMIT 10;

#### ② 프레임워크가 아닌 "사용자 애플리케이션 코드" 에러만 필터링

Spring, Tomcat, JDK 내부 코드를 제외하고 실제 비즈니스 로직(Custom Code)에서 발생한 에러만 추출합니다.

    MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
    WHERE m.isFramework = false
    RETURN m.fullName AS CustomMethod, ex.type AS ExceptionType, count(ex) AS Count
    ORDER BY Count DESC
    LIMIT 10;

#### ③ 가장 빈번하게 발생한 예외 유형 및 메시지 Top 10

    MATCH (ex:Exception)
    RETURN ex.type AS ExceptionType, ex.message AS Message, count(ex) AS Frequency
    ORDER BY Frequency DESC
    LIMIT 10;

### 3. 데이터 및 테이블 상태 확인 쿼리

데이터가 정상적으로 저장되어 있는지 빠르게 확인할 때 유용합니다.

#### ① 노드 테이블별 데이터 건수 확인

    MATCH (t:Thread) RETURN 'Thread' AS TableName, count(t) AS Count
    UNION ALL
    MATCH (ex:Exception) RETURN 'Exception' AS TableName, count(ex) AS Count
    UNION ALL
    MATCH (m:Method) RETURN 'Method' AS TableName, count(m) AS Count
    UNION ALL
    MATCH (c:Class) RETURN 'Class' AS TableName, count(c) AS Count;

#### ② 최근 발생한 예외 및 스택트레이스 상세 확인

    MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
    RETURN ex.timestamp AS Time, ex.type AS Type, m.fullName AS Method, ex.stackTrace AS StackTrace
    ORDER BY ex.timestamp DESC
    LIMIT 5;

### 그래프(노드-엣지 시각화)로 출력되는 쿼리 모음

#### 1. 스레드 ➔ 예외 ➔ 메서드 전체 연결 그래프

    MATCH p = (t:Thread)-[:RAISED]->(ex:Exception)-[:OCCURRED_IN]->(m:Method)
    RETURN p
    LIMIT 30

#### 2. 예외 인과관계 체인 그래프 (Caused By)

    MATCH p = (e1:Exception)-[:CAUSED_BY]->(e2:Exception)
    RETURN p
    LIMIT 30

#### 3. 메서드 간 호출 흐름 그래프 (CALLS)

    MATCH p = (m1:Method)-[:CALLS]->(m2:Method)
    RETURN p
    LIMIT 30

#### 4. DB 내에 연결되어 있는 모든 관계 샘플 보기

노드와 릴레이션 종류에 상관없이 연결된 그래프를 한눈에 볼 때 사용합니다.

    MATCH p = (a)-[r]->(b)
    RETURN p
    LIMIT 50

#### 5. 특정 예외 노드들만 점(Circle) 형태로 띄워보기

    MATCH (ex:Exception)
    RETURN ex
    LIMIT 40
