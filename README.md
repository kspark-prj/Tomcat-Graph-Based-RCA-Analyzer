# 😾 WAS/Application Log Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyQt6 Powered Dynamic Log RCA Tool**

`WAS/Application Log Graph-Based RCA Analyzer`는 Spring Boot, Apache Tomcat, WildFly/JBoss 등 다양한 WAS 및 애플리케이션의 대용량 로그 파일(`*.log`, `*.out`)을 파싱하여 장애 원인을 그래프 데이터 모델로 구축하고, 구조화된 인메모리 마이닝 기법을 통해 **근본 원인(Root Cause Analysis, RCA)** 및 장애 전파 체인을 추적하는 데스크톱 GUI 진단 도구입니다.

초고속 임베디드 그래프 DB인 **Kùzu**를 결합하여, 단순한 텍스트 매칭을 넘어 에러의 상위 호출 지점, 스레드 영향도, `Caused by` 인과 관계를 유기적으로 시각화 및 분석합니다.

---

## ✨ 핵심 기능 (Key Features)

- **Multi-WAS & Application Log Auto-Detection**: Spring Boot, Apache Tomcat/Log4j, WildFly/JBoss(server.log) 등 대표적인 3가지 로그 포맷을 정규표현식으로 자동 정밀 탐지하여 통합 분석합니다.

- **Graph DB Architecture**: `Thread -> Exception -> Method -> Class`로 이어지는 스택 트레이스 및 `Exception -[:CAUSED_BY]-> Exception`의 뿌리 원인 체인을 그래프 모델 스키마로 설계하여 Cypher 쿼리로 정밀 추적합니다.

- **Auto-Diagnosis Engine (Post-Mortem Report)**: 수집된 예외 데이터를 분석하여 4대 장애 등급(🔴 DB 병목, ⚡ 외부 망/SFTP 유실, 🔑 인증/보안 결함, 💻 애플리케이션 로직 에러)을 분류하고 도메인별 지분율 산출 및 트러블슈팅 권고사항을 담은 사후 진단서를 자동 작성합니다.

- **File-driven Dynamic Timeline**: 로그 타임스탬프의 시작과 끝 구간을 계산하여 10개 구간으로 실시간 수치화하고, 장애 집중 발생 시간대 및 지속시간을 텍스트 차트로 동적 렌더링합니다.

- **Trace Propagation Chain & Root Cause Ranking**: 최다 발생 근본 원인(Root Cause) 메서드 및 예외 클래스 랭킹을 표로 제공하며, 선택 시 해당 지점의 예외 종류, 상세 메시지, `Caused By` 체인, 상위 호출 지점(`CALLS`)을 트리 구조(`QTreeView`)로 계층 분석합니다.

- **Safe Multi-Threading & Thread Lock Prevention**: 백그라운드 `QThread` 워커 시스템을 탑재하고, 로그 분석 전/후 Kùzu DB Connection/Database 메모리 자원을 안전하게 해제하여 File Lock 충돌 및 메모리 누수를 완전히 차단합니다.

- **Automatic DB Reset & Manual Reset**: 파일 업로드 시 기존 DB를 자동으로 초기화하여 깔끔하게 새 데이터를 분석하며, 필요에 따라 UI에서 '수동 DB 초기화' 버튼을 통해 DB 물리 삭제 및 재연결을 수행할 수 있습니다.

- **Cypher Escape & Timestamp Normalization**: 특수문자 이스케이프 알고리즘과 ISO T/타임존/밀리초 처리 정제 함수를 내장하여 Cypher 쿼리 구문 오류를 철저히 방지합니다.

---

## 🛠️ 사용 기술 (Tech Stack)

- **Language**: Python 3.x

- **Graph Database**: [Kùzu](https://www.google.com/search?q=https://kuzudb.com/) (Embedded Graph Database)

- **GUI Framework**: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)

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

로그 파일의 텍스트 한 줄 한 줄이 파싱되어 그래프 DB 노드와 관계(Edge)로 변환되는 전체 프로세스입니다.

### 1. 처리 알고리즘 흐름

````text
[ 1. 기존 DB 자동 초기화 & 신규 스키마 정의 ]
                   ↓
[ 2. 로그 패턴 자동 감지 (Spring Boot / Tomcat / WildFly) ]
                   ↓
[ 3. ERROR 로그 헤더 감지 및 메시지 파싱 ]
                   ↓
[ 4. Thread & Exception 노드 생성 및 RAISED 관계 연결 ]
                   ↓
[ 5. Caused by 발생 감지 및 CAUSED_BY 관계 생성 ]
                   ↓
[ 6. 스택 트레이스 수집 (비즈니스 앱 코드 우선 매핑) ]
                   ↓
[ 7. Root Method 추출 및 OCCURRED_IN / BELONGS_TO 관계 연결 ]
                   ↓
[ 8. 호출 체인 분석으로 상위 호출자(CALLS) 연결 ]
```[cite: 1, 2]

### 2. 지원하는 로그 포맷 예시

1. **Spring Boot 포맷**: `2026-07-23T14:30:15.123+09:00 ERROR 12345 --- [http-nio-8080-exec-5] com.example.Controller : Error msg`[cite: 2]
2. **Tomcat / Standard Log4j 포맷**: `2026-07-23 14:30:15.123 [http-nio-8080-exec-5] ERROR com.example.Controller - Error msg`[cite: 1, 2]
3. **WildFly / JBoss server.log 포맷**: `2026-07-23 14:30:15,123 ERROR [com.example.Controller] (default task-1) Error msg`[cite: 2]

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
python unified_log_analyzer.py
```[cite: 2]

---

## 💡 주요 코드 하이라이트 (Cypher 쿼리를 통한 인프라 마이닝)

Kùzu 그래프 엔진의 Cypher 쿼리를 통해 예외 클래스와 메시지 특징을 결합하여, 근본 원인(Root Cause) 메서드 및 타임라인 기반 전파 체인을 조회하는 로직 예시입니다[cite: 1, 2].

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
