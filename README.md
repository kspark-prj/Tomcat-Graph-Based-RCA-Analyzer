# 😾 Tomcat Graph-Based RCA Analyzer

> **Graph Database Engine (Kùzu) & PyQt6 Powered Dynamic Tomcat Log RCA POC**

`Tomcat Graph-Based RCA Analyzer`는 톰캣 대용량 로그 파일(`catalina.out`)을 파싱하여 장애 원인을 그래프 데이터 모델로 구축하고, 구조화된 인메모리 마이닝 기법을 통해 **근본 원인(Root Cause Analysis, RCA)** 및 장애 전파 체인을 추적하는 데스크톱 GUI 진단 도구입니다.

초고속 임베디드 그래프 DB인 **Kùzu**를 결합하여, 단순한 텍스트 매칭을 넘어 에러의 상위 호출 지점과 스레드 결빙 현상을 유기적으로 시각화 및 분석합니다.

---

## ✨ 핵심 기능 (Key Features)

- **Graph DB Architecture**: `Thread -> Exception -> Method -> Class`로 이어지는 복잡한 스택 트레이스 관계를 그래프 모델 스키마로 설계하여 `MATCH`/`MERGE` 쿼리로 정밀 추적합니다.
- **Auto-Diagnosis Engine**: 수집된 예외 데이터를 분석하여 4대 장애 등급(🔴 DB 병목, ⚡ 외부 망 유실, 🔑 인증 결함, 💻 로직 에러)을 분류하고 대응 가이드를 담은 **사후 진단서(Post-Mortem Report)**를 자동 작성합니다.
- **File-driven Dynamic Timeline**: 로그의 타임스탬프를 10개의 구간으로 실시간 수치화하여 장애 집중 발생 시간대를 텍스트 차트로 동적 렌더링합니다.
- **Trace Propagation Chain**: 특정 Root Cause 에러 메서드를 선택하면, 해당 장애의 상위 호출 지점(`CALLS`) 및 하위 전파 타임라인을 트리 구조(`QTreeView`)로 계층 분석합니다.
- **Responsive Background Parsing**: 대용량 로그 데이터 파싱 시 GUI 정지를 방지하기 위해 독립적인 `QThread` 워커 시스템을 탑재하고, 버튼 레이블을 통해 직관적인 진행 상태 피드백을 전달합니다.

---

## 🛠️ 사용 기술 (Tech Stack)

- **Language**: Python 3.x
- **Graph Database**: [Kùzu](https://kuzudb.com/) (Embedded Graph Database)
- **GUI Framework**: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)
- **Pattern Matching**: Regular Expressions (Regex)

---

## 📐 그래프 데이터베이스 모델 스키마

에러가 발생한 지점의 연쇄 관계를 규명하기 위해 아래와 같은 그래프 토폴로지 구조를 구축합니다.

- `(Thread) -[:RAISED]-> (Exception)` : 특정 스레드에서 예외 발생
- `(Exception) -[:OCCURRED_IN]-> (Method)` : 해당 예외가 특정 메서드 내에서 발현
- `(Method) -[:BELONGS_TO]-> (Class)` : 메서드가 속한 클래스 구조 정의
- `(Method) -[:CALLS]-> (Method)` : 스택 트레이스 기반 상위/하위 호출 흐름 연결

---

## 🚀 시작하기 (Getting Started)

### 1. 필수 패키지 설치

프로젝트 실행을 위해 아래 라이브러리들을 설치해야 합니다.

```bash
pip install PyQt6 kuzu
```

````

### 2. 프로젝트 실행

구동 환경이 준비되면 메인 스크립트를 실행합니다.

```bash
python tomcat_rca_analyzer.py

```

---

## 💡 주요 코드 하이라이트 (Cypher 쿼리를 통한 인프라 마이닝)

Kùzu 그래프 엔진의 Cypher 쿼리를 통해 예외 클래스와 메시지 특징을 결합, 단순 통계가 아닌 인프라 영향 지표 및 RCA 연산 알고리즘을 수행하는 핵심 로직 예시입니다.

```python
# 가장 빈번하게 장애를 유발한 근본 원인(Root Cause) 메서드 및 상위 예외 추출
root_query = """
    MATCH (ex:Exception)-[:OCCURRED_IN]->(m:Method)
    RETURN Count(ex) as cnt, m.fullName, ex.type
    ORDER BY cnt DESC
    LIMIT 10
"""
res_root = self.conn.execute(root_query)

```

---

## 📄 라이선스 (License)

이 프로젝트는 MIT 라이선스 하에 자유롭게 수정 및 배포가 가능합니다.

```

```
````
