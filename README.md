# 😾 Tomcat Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyQt6 Powered Dynamic Tomcat Log RCA POC**

`Tomcat Graph-Based RCA Analyzer`는 톰캣 대용량 로그 파일(`catalina.out`)을 파싱하여 장애 원인을 그래프 데이터 모델로 구축하고, 구조화된 인메모리 마이닝 기법을 통해 **근본 원인(Root Cause Analysis, RCA)** 및 장애 전파 체인을 추적하는 데스크톱 GUI 진단 도구입니다.

초고속 임베디드 그래프 DB인 **Kùzu**를 결합하여, 단순한 텍스트 매칭을 넘어 에러의 상위 호출 지점, 스레드 결빙 현상, `Caused by` 인과 관계를 유기적으로 시각화 및 분석합니다.

---

## ✨ 핵심 기능 (Key Features)

- **Graph DB Architecture**: `Thread -> Exception -> Method -> Class`로 이어지는 스택 트레이스 및 `Exception -[:CAUSED_BY]-> Exception`의 뿌리 원인 체인을 그래프 모델 스키마로 설계하여 Cypher 쿼리로 정밀 추적합니다.

- **Auto-Diagnosis Engine**: 수집된 예외 데이터를 분석하여 4대 장애 등급(🔴 DB 병목, ⚡ 외부 망 유실, 🔑 인증 결함, 💻 로직 에러)을 분류하고 대응 가이드를 담은 사후 진단서(Post-Mortem Report)를 자동 작성합니다.

- **File-driven Dynamic Timeline**: 로그의 타임스탬프를 10개의 구간으로 실시간 수치화하여 장애 집중 발생 시간대를 텍스트 차트로 동적 렌더링합니다.

- **Trace Propagation Chain & Root Cause Tracking**: 특정 Root Cause 에러 메서드를 선택하면 상위 호출 지점(`CALLS`), 에러 메시지 상세 및 발생 타임라인을 트리 구조(`QTreeView`)로 계층 분석합니다.

- **Safe Multi-Threading & Thread Lock Prevention**: 백그라운드 `QThread` 워커 시스템을 탑재하고 Worker와 Main UI 스레드 간 Kùzu DB File Lock 충돌을 완벽히 차단하여 안정적인 데이터 모델링 환경을 제공합니다.

- **Cypher Escape & Data Sanitation**: 로그 메시지 내 백슬래시(`\`), 작은따옴표(`'`), 개행문자 등의 특수문자로 인한 Cypher 쿼리 구문 오류 발생을 방지하는 이스케이프 알고리즘을 탑재했습니다.

---

## 🛠️ 사용 기술 (Tech Stack)

- **Language**: Python 3.x[cite: 2]
- **Graph Database**: [Kùzu](https://www.google.com/search?q=https://kuzudb.com/) (Embedded Graph Database)[cite: 2]
- **GUI Framework**: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)[cite: 2]
- **Pattern Matching**: Regular Expressions (Regex)

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

`catalina.out` 로그 파일의 텍스트 한 줄 한 줄이 파싱되어 그래프 DB 노드와 관계(Edge)로 변환되는 전체 프로세스입니다.

### 1. 처리 알고리즘 흐름

````text
[ 1. 스키마 & Primary Key 인덱스 정의 ]
                   ↓
[ 2. ERROR 헤더 로그 감지 (Regex 매칭) ]
                   ↓
[ 3. Thread & Exception 노드 / RAISED 관계 생성 ]
                   ↓
[ 4. Caused by 발생 여부 탐지 & CAUSED_BY 관계 생성 ]
                   ↓
[ 5. 스택 트레이스 연속 블록 수집 (최대 Depth 10) ]
                   ↓
[ 6. Root Method 추출 / OCCURRED_IN 관계 연결 ]
                   ↓
[ 7. 스택 역순 분석으로 상위 호출자(CALLS) 연결 ]
```[cite: 1, 2]

### 2. 실제 로그 텍스트 ➔ 그래프 DB 변환 예시

#### 📄 파싱 대상 로그

```text
2026-07-23 14:30:15.123 [http-nio-8080-exec-5] ERROR com.example.controller.OrderController - org.springframework.dao.DataAccessException: Database error
	at com.example.service.OrderService.processOrder(OrderService.java:30)
	at com.example.controller.OrderController.create(OrderController.java:15)
Caused by: java.sql.SQLException: Connection timeout
	at com.example.repository.OrderRepository.findOrder(OrderRepository.java:45)
```

#### ⚙️ 단계별 그래프 DB 매핑 동작

1. **ERROR 헤더 라인 분석**:
   - `Thread` 노드 생성 (`name: 'http-nio-8080-exec-5'`)[cite: 1, 2]
   - `Exception` 노드 생성 (`id: 'err_0_...'`, `type: 'org.springframework.dao.DataAccessException'`, `message: 'Database error'`, `timestamp: ...`)
   - `(Thread) -[:RAISED]-> (Exception)` 관계 연결[cite: 1, 2]

2. **Caused by 라인 감지 (Root Cause 추적)**:
   - `Caused by` 감지 시 새로운 Root `Exception` 노드 생성 (`id: 'caused_3_...'`, `type: 'java.sql.SQLException'`, `message: 'Connection timeout'`)
   - `(Parent Exception) -[:CAUSED_BY]-> (Child Exception)` 관계 구축 후 추적 대상 전환

3. **스택 트레이스 라인 분석 (근본 원인 및 호출 체인 추적)**:
   - 실제 예외 직접 발생지점인 최하단 메서드 `OrderRepository.findOrder`를 추출하여 `(Child Exception) -[:OCCURRED_IN]-> (Method:findOrder)` 직접 연결
   - 스택 역순 분석을 진행하여 상위 호출자 방향 정정 (`call_chain[k+1]`가 `call_chain[k]`를 호출):
     - `(OrderService.processOrder) -[:CALLS]-> (OrderRepository.findOrder)`[cite: 1, 2]
     - `(OrderController.create) -[:CALLS]-> (OrderService.processOrder)`[cite: 1, 2]

#### 🌐 최종 완성된 그래프 데이터 토폴로지

```text
(Thread: http-nio-8080-exec-5)
       │
   [RAISED]
       ↓
(Exception: DataAccessException)
       │
  [CAUSED_BY]
       ↓
(Exception: SQLException) ──[OCCURRED_IN]──> (Method: OrderRepository.findOrder) ──[BELONGS_TO]──> (Class: OrderRepository)
                                                    ▲
                                                [CALLS]
                                                    │
                                     (Method: OrderService.processOrder) ──[BELONGS_TO]──> (Class: OrderService)
                                                    ▲
                                                [CALLS]
                                                    │
                                     (Method: OrderController.create)    ──[BELONGS_TO]──> (Class: OrderController)
```

- **포인트 (In-Memory Index & MERGE)**: `escape_cypher()`를 통한 안전한 텍스트 변환 및 `MERGE` 구문을 활용해 중복 노드를 방지합니다. 수만 건의 에러 로그가 유입되어도 동일 메서드 노드에 관계성이 수집되어 파급 효과를 직관적으로 분석할 수 있습니다[cite: 1, 2].

---

## 🚀 시작하기 (Getting Started)

### 1. 필수 패키지 설치

프로젝트 실행을 위해 아래 라이브러리들을 설치해야 합니다[cite: 2].

```bash
pip install PyQt6 kuzu
```[cite: 2]

### 2. 프로젝트 실행

구동 환경이 준비되면 메인 스크립트를 실행합니다[cite: 2].

```bash
python tomcat_rca_analyzer.py
```[cite: 2]

---

## 💡 주요 코드 하이라이트 (Cypher 쿼리를 통한 인프라 마이닝)

Kùzu 그래프 엔진의 Cypher 쿼리를 통해 예외 클래스와 메시지 특징을 결합, 단순 통계가 아닌 인프라 영향 지표 및 RCA 연산 알고리즘을 수행하는 핵심 로직 예시입니다[cite: 1, 2].

```python
# 가장 빈번하게 장애를 유발한 근본 원인(Root Cause) 메서드 및 상위 예외 추출
root_query = """
    MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
    RETURN Count(ex) as cnt, m.fullName, ex.type
    ORDER BY cnt DESC
    LIMIT 10
"""
res_root = self.conn.execute(root_query)
```[cite: 1, 2]

---

## 📄 라이선스 (License)

이 프로젝트는 MIT 라이선스 하에 자유롭게 수정 및 배포가 가능합니다[cite: 2].

````
