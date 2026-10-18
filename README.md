# 😾 WAS/Application Log Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyArrow Streaming Bulk Ingestion Powered High-Performance Log RCA & Git History Tracking Tool**

`WAS/Application Log Graph-Based RCA Analyzer`는 Spring Boot, Apache Tomcat, WildFly/JBoss 등 다양한 WAS 로그(`*.log`, `*.out`)를 분석하여 장애 원인을 그래프로 구축하고, **근본 원인(Root Cause Analysis, RCA)** 분석부터 **Git 로컬 저장소 커밋/Diff 히스토리**까지 추적하는 데스크톱 GUI 진단 도구입니다.

임베디드 그래프 DB인 **Kùzu**와 **PyArrow Zero-Copy Streaming**을 결합하여, 수 GB 이상의 대용량 로그도 **RAM 1GB 이내**로 초고속 인덱싱합니다.

---

## ✨ 핵심 기능 (Key Features)

- **Git & Diff Viewer Integration (NEW)**: 장애 파급 체인에서 메서드를 클릭하면 연관된 Git 커밋 이력(`GitHistoryDialog`)과 변경 내역 Diff를 팝업으로 즉시 조회
- **Single Instance & Window Focus (`QSharedMemory`)**: 중복 실행 방지 및 기존 창 최상단 포커싱 지원
- **Enterprise Encoding Fallback**: 국내 Legacy/Enterprise 환경의 UTF-8 및 CP949/EUC-KR 인코딩 자동 감지
- **Low-Memory Chunked Streaming**: 5,000건 단위 분할 처리 및 수동 GC(`gc.collect()`)로 피크 RAM 점유율 1GB 이내 유지
- **2-Pass Bulk Ingestion**: `[Node 선제 주입]` 후 `[Relationship 일괄 주입]` 구조로 B+Tree 락 경합 방지
- **App-Level Global Caching**: 파이썬 메모리 레벨에서 노드 중복을 추적하여 DB PK 중복 체크 연산 차단
- **PyArrow Zero-Copy & Kùzu Buffer Pool**: C++ 엔진 버퍼로 직렬화 주입 및 명시적 버퍼 풀 할당을 통한 Disk I/O 병목 제거
- **Trace Propagation Chain (`QTreeView`)**: 예외 인과관계(`Caused By`), StackTrace, 상위 호출자 체인을 비동기 트리 구조로 렌더링
- **Multi-WAS Auto-Detection**: Spring Boot, Tomcat, WildFly, WebLogic 등 대표 로그 포맷 자동 정밀 탐지
- **Auto-Diagnosis Engine (Post-Mortem Report)**: 9대 장애 등급 자동 분류 및 도메인별 지분율 산출, 트러블슈팅 권고사항 사후 진단서 작성
- **10-Step Timeline Distribution Chart**: 전체 장애 기간을 10개 구간으로 분할하여 에러 추이를 타임라인 프로그레스 바로 시각화
- **Zero-Flicker Custom Splash Screen**: PyInstaller 네이티브와 PyQt6 커스텀 오버레이 간 핸드오버 지연 버퍼 기술 적용

---

## 🛠️ 기술 스택 (Tech Stack)

- **Language**: Python 3.x
- **Graph Database**: [Kùzu](https://kuzudb.com/) (Embedded Graph Database)
- **Data Framework**: Apache Arrow (`pyarrow`)
- **GUI Framework**: PyQt6
- **VCS Integration**: Regular Expressions & Git CLI Dynamic Subprocess

---

## 📐 그래프 데이터베이스 모델 스키마

- `(Thread) -[:RAISED]-> (Exception)` : 스레드별 예외 발생 매핑
- `(Exception) -[:CAUSED_BY]-> (Exception)` : 상위 예외와 Root Cause 간 인과 관계
- `(Exception) -[:OCCURRED_IN]-> (Method)` : 예외가 발현된 특정 메서드
- `(Method) -[:BELONGS_TO]-> (Class)` : 메서드가 속한 클래스 구조
- `(Method) -[:CALLS]-> (Method)` : 스택 트레이스 기반 호출 흐름 (`Caller -> Callee`)

---

## 🚦 9대 장애 진단 분류 체계 (RCA Categories)

| 장애 등급         | 분류 명칭                          | 주요 감지 예외 및 키워드 예시                                                                                  |
| :---------------- | :--------------------------------- | :------------------------------------------------------------------------------------------------------------- |
| 🧠 **Category 1** | **JVM 메모리 및 시스템 자원 고갈** | `OutOfMemoryError`, `Metaspace`, `GC overhead limit`, `StackOverflowError`, `Too many open files`              |
| 🔴 **Category 2** | **데이터베이스 영역 (DB 병목)**    | `SQL`, `Hikari`, `Connection`, `Deadlock`, `Constraint`, `Timeout`                                             |
| ⚡ **Category 3** | **스레드 풀 및 동시성 병목**       | `RejectedExecutionException`, `TaskRejectedException`, `ThreadPoolExecutor`, `ConcurrentModificationException` |
| 🌐 **Category 4** | **외부 연동망 및 SFTP 장애**       | `ConnectException`, `SocketTimeout`, `HttpClient`, `UnknownHost`, `SFTP`                                       |
| 📨 **Category 5** | **메시지 큐 및 캐시 장애**         | `KafkaException`, `RedisConnectionException`, `AmqpException`, `RabbitMq`, `Jedis`, `Lettuce`                  |
| 🔑 **Category 6** | **인증 및 보안 장애**              | `Unauthorized`, `OAuth2`, `JWT`, `ExpiredToken`, `SignatureException`, `AccessDenied`                          |
| ⚙️ **Category 7** | **설정 및 배포/환경 예외**         | `BeanCreationException`, `ClassNotFoundException`, `NoSuchMethodError`, `YamlException`                        |
| 📦 **Category 8** | **데이터 파싱 / 바인딩 에러**      | `HttpMessageNotReadableException`, `JsonParseException`, `MethodArgumentNotValidException`                     |
| 💻 **Category 9** | **애플리케이션 로직 에러**         | 기타 일반 런타임 NullPointer 등 소스코드 내부 결함                                                             |

---

## 🚀 시작하기 (Getting Started)

### 1. 프로젝트 실행

```bash
uv run main.py
```

````

### 2. PyInstaller 바이너리 빌드

```bash
uv run pyinstaller -w -D --noupx --clean --icon=main.ico --add-data "splash.png;." \
  --exclude-module PIL --exclude-module Pillow --exclude-module tkinter --exclude-module unittest \
  --exclude-module PyQt6.QtWebEngineCore --exclude-module PyQt6.Qt3D --exclude-module PyQt6.QtQuick main.py

```

### 3. Kùzu Explorer 웹 대시보드 (Docker)

```bash
docker run -p 8000:8000 \
  -v "/path/to/your/project:/database" \
  -e KUZU_FILE=kuzu_unified_log_db \
  -e MODE=READ_ONLY \
  --rm kuzudb/explorer:0.11.3

```

---

## 🔍 주요 Cypher 쿼리 모음

### 1. 그래프 시각화 쿼리

- **전체 에러 체인 (Thread ➔ Exception ➔ Method)**

```cypher
MATCH p = (t:Thread)-[:RAISED]->(ex:Exception)-[:OCCURRED_IN]->(m:Method)
RETURN p LIMIT 30;

```

- **예외 인과관계 (Caused By)**

```cypher
MATCH p = (e1:Exception)-[:CAUSED_BY]->(e2:Exception)
RETURN p LIMIT 30;

```

- **메서드 호출 흐름 (CALLS)**

```cypher
MATCH p = (m1:Method)-[:CALLS]->(m2:Method)
RETURN p LIMIT 30;

```

### 2. RCA 통계 및 분석 쿼리

- **에러 발생 빈도 Top 10 메서드**

```cypher
MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
RETURN m.fullName AS MethodName, ex.type AS ExceptionType, count(ex) AS ErrorCount
ORDER BY ErrorCount DESC LIMIT 10;

```

- **커스텀 비즈니스 로직 에러만 필터링 (`isFramework = false`)**

```cypher
MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
WHERE m.isFramework = false
RETURN m.fullName AS CustomMethod, ex.type AS ExceptionType, count(ex) AS Count
ORDER BY Count DESC LIMIT 10;

```

````
