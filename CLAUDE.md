# CLAUDE.md

> 한국 금융사 폐쇄망 환경에서 일하는 개발자를 위한 **코드 변환 Agent** 프로젝트
> 오픈소스 패키지의 핵심 기능을 추출·재구현하여 폐쇄망 반입이 용이한 **single-file Python 코드**로 변환합니다.

---

## 1. 프로젝트 개요 (What & Why)

### 목적
폐쇄망 환경에서 외부 패키지 설치가 자유롭지 않은 금융사 개발자들이, 유명 오픈소스 라이브러리의 핵심 기능을 **단일 파일(single-file)** 형태로 쉽게 반입하여 사용할 수 있도록 돕는 AI Agent입니다.

### 핵심 제약
- **외부 API 호출 불가**: 로컬 LLM(예: Qwen3, GPT-OSS 계열)만 사용
- **외부 패키지 설치 제한**: 사내 미러에 등록된 패키지만 사용 가능
- **반입 용이성**: 최종 산출물은 **단일 `.py` 파일**이어야 함

### 사용자
사내 다양한 수준의 개발자 혼재 (초급 분석가 ~ 시니어 DS/DE)
→ 코드는 **누구나 읽을 수 있을 만큼 친절해야 함**

### 기술 스택
구체적인 허용 패키지/Python 버전은 `references/allowed-stacks/<active>.yaml`로 외부화되어 있습니다. (활성 스택은 `active_stack.txt`로 지정) 자세한 내용은 섹션 4 참고.

---

## 2. Agent 워크플로우

Agent는 다음 하이브리드 플로우로 동작합니다:

```
[1단계] 요구사항 분석
  └─ 사용자 요청에서 "어떤 기능이 필요한가"를 구체화

[2단계] 오픈소스 탐색 & 후보 패키지 비교
  └─ 여러 오픈소스 라이브러리를 탐색하여 후보군 추출
  └─ 기능/라이선스/의존성 관점에서 비교

[3단계] 핵심 추출 (Core Extraction)
  └─ 선택된 라이브러리에서 "정말 필요한 기능"만 식별
  └─ 불필요한 추상화, 플러그인, 레거시 호환 코드 제거

[4단계] 단일 파일 구현
  └─ 허용된 의존성만 사용하여 single-file로 재구현
  └─ 한글 주석 + 예제 usage 포함

[5단계] 검증
  └─ 원본 동작과의 일치 여부 확인
  └─ 라이선스/의존성 금지 규칙 준수 여부 확인
```

---

## 3. 리포지토리 구조 & 폴더 규칙

이 프로젝트는 **여러 변환물을 하나의 GitHub 리포지토리**에서 폴더 단위로 독립 관리합니다. 각 폴더는 하나의 완결된 변환물입니다.

### 전체 리포지토리 구조

```
<repo-root>/
├── README.md                    # 리포 전체 개요, 변환물 인덱스
├── CLAUDE.md                    # (이 문서)
├── active_stack.txt             # 현재 활성 스택 이름 (references/allowed-stacks/ 내 파일명)
├── references/                  # 환경·스택·API 매핑 등 참조 자료
│   ├── README.md
│   ├── allowed-stacks/          # 환경 스택 정의 (금융사별로 교체 가능)
│   │   └── datascience-baseline.yaml
│   └── api-mappings/            # 버전별 API 변경 매핑
│       ├── scikit-learn.yaml
│       ├── pandas.yaml
│       ├── numpy.yaml
│       ├── torch.yaml
│       ├── python-syntax.yaml
│       └── package-substitutions.yaml
├── skills/                      # Claude Code가 사용하는 Skill 문서
│   └── environment-adapter/
│       └── SKILL.md             # 환경 호환성 조정 Skill
├── 001-image-segmentation/      # 변환물 #1
├── 002-text-classification/     # 변환물 #2
├── 003-anomaly-detection/       # 변환물 #3
└── ...
```

### 폴더명 컨벤션
- 형식: `{3자리 번호}-{kebab-case 설명}/`
- 번호는 **생성 순서**대로 001부터 증가 (중복·건너뜀 없음)
- 설명은 **영문 kebab-case** (공백·대문자·언더스코어 사용 금지)
- 예시:
  - ✅ `001-image-segmentation/`
  - ✅ `012-gradient-boosting-lite/`
  - ❌ `001_image_segmentation/` (언더스코어)
  - ❌ `1-imageseg/` (번호 자릿수 부족, 설명 불명확)
  - ❌ `image-segmentation/` (번호 누락)

### 폴더 내부 기본 구조

각 변환물 폴더는 다음 구조를 **기본**으로 합니다:

```
001-image-segmentation/
├── README.md               # 이 변환물의 설명 (한글)
├── <main>.py               # 핵심 single-file 소스코드
├── metadata.json           # 변환 메타데이터
├── LICENSE                 # 원본 라이선스 파일 복제본
├── examples/               # 학습/사용 예제 스크립트
│   ├── basic_usage.py
│   └── train.py            # 필요 시
└── data/                   # 샘플 데이터 (작은 파일만)
    └── sample.csv
```

### 필수 파일 명세

#### `README.md` (한글 작성)
최소 포함 항목:
- 변환물의 **한 줄 요약**
- **원본 출처**: 라이브러리명, 버전, GitHub URL, 라이선스
- **기능 요약**: 이 변환물이 제공하는 핵심 기능 (원본 대비 축약된 부분 명시)
- **의존성**: 허용 목록 중 실제 사용한 것
- **사용 예시**: `examples/basic_usage.py` 참조 안내
- **알려진 제약/한계점**

#### `<main>.py`
- 파일명은 **의미 있는 이름** 사용 (예: `segmenter.py`, `detector.py`)
- `main.py`처럼 일반적인 이름은 여러 변환물을 `sys.path`에 추가했을 때 충돌하므로 **지양**
- Single-file 원칙 준수 (섹션 5의 파일 구조 템플릿 참고)

#### `metadata.json`
변환 추적을 위한 메타데이터. 예시:
```json
{
  "id": "001",
  "name": "image-segmentation",
  "source": {
    "library": "scikit-image",
    "version": "0.22.0",
    "url": "https://github.com/scikit-image/scikit-image",
    "license": "BSD-3-Clause",
    "commit_sha": "abc1234"
  },
  "converted_at": "2026-04-17",
  "agent_version": "0.1.0",
  "dependencies_used": ["numpy", "scipy"],
  "extracted_features": [
    "Felzenszwalb segmentation",
    "SLIC superpixels"
  ],
  "limitations": [
    "Cython 기반 내부 최적화 코드는 순수 Python으로 재구현되어 대용량 이미지에서 성능 저하 가능"
  ]
}
```

#### `LICENSE`
- **원본 라이브러리의 LICENSE 파일을 그대로 복제**
- 파일 상단에 "이 라이선스는 원본 `<라이브러리명> v<버전>`에서 가져왔습니다"라는 주석 추가 가능
- 원본 라이선스가 **활성 스택의 허용 카테고리에 속하지 않으면 변환을 거부**해야 함 (섹션 6 참고)

#### `examples/` 디렉토리
- 최소 1개의 `basic_usage.py` 필수
- 학습이 필요한 변환물은 `train.py`도 함께 제공
- 각 예제 스크립트는 **독립 실행 가능**해야 함 (Jupyter 의존 금지)
- 예제는 자체 포함 데이터 또는 `../data/` 참조만 사용

#### `data/` 디렉토리 (선택)
- **작은 샘플 데이터만** 포함 (수 MB 이내 권장)
- 개인정보·사내 데이터·라이선스 불명확 데이터 **절대 금지**
- 공개 표준 데이터셋의 소규모 샘플 또는 합성 데이터 사용

### 일관성 원칙
- **기본 구조는 모든 폴더에 적용** (`README.md`, `<main>.py`, `metadata.json`, `LICENSE`, `examples/basic_usage.py`는 필수)
- **필요 시 유연하게 확장 가능**: 예를 들어 configs/, notebooks/, benchmarks/ 등을 추가해도 무방
- 단, 기본 구조에 있는 파일을 **생략하지는 않음**
- 확장 디렉토리/파일은 README.md에 간단히 설명 추가

---

## 4. 도메인 지식 & 비즈니스 컨텍스트

### 폐쇄망 환경이란?
- 인터넷이 차단된 개발 환경 (금융사/공공기관 전형)
- 외부 `pip install` 불가 → 사내 미러 저장소를 통해서만 패키지 반입
- 파일 반입도 **보안 심사 절차**를 거침 → 파일이 적고 단순할수록 유리
- 이 때문에 **single-file**이 강력한 가치를 가짐 (리뷰도 쉽고, 반입도 쉬움)

### 사내 미러에 기대할 수 있는 패키지
금융사/팀마다 반입 가능한 패키지 목록이 다릅니다. 이 프로젝트는 **환경 스택 정의를 `references/allowed-stacks/` 하위 YAML 파일로 외부화**합니다.

- 현재 활성 스택은 프로젝트 루트의 `active_stack.txt` (또는 명시적 지정)로 결정
- 각 스택 YAML에는 허용 패키지·금지 패키지·라이선스 정책·Python 버전이 정의됨
- Agent는 변환 작업 전 **반드시 활성 스택을 읽고** 그 제약 안에서 동작
- 새 환경에 이식 시 `references/allowed-stacks/` 안에 새 YAML을 추가하면 됨

자세한 구조는 `references/README.md` 참고.

### 활성 스택이 지정되지 않았을 때의 처리 (중요)
사용자가 매번 환경 정보를 명시하지는 않습니다. 이때 Agent의 행동 규칙:

1. **매 변환 작업 시작 시 활성 스택을 확인**
   - `active_stack.txt` 존재 여부, 사용자 메시지 내 환경 스펙 여부, 최근 대화의 명시적 지정 여부를 점검
   - 세션 내에서 이전 작업에 사용한 스택을 **자동 재사용하지 않음** (변환 대상마다 타겟 환경이 다를 수 있음)

2. **지정되지 않았으면 기본 스택을 가정하고 시작**
   - `references/allowed-stacks/default.yaml`을 기본값으로 사용
   - 변환 작업을 **중단하지 않음** — 가정을 명시하며 진행

3. **사용자에게 반드시 확인을 요청**
   - 기본 스택의 **핵심 내용을 구체적으로 제시**: Python 버전, 주요 허용 패키지, 주요 금지 항목
   - "이대로 진행할까요? 수정이 필요하면 알려주세요"라는 형태로 유도
   - 목록을 그대로 나열하기보다 **요약 + 필요 시 상세 제공** 방식 권장

4. **사용자 수정사항은 현재 작업에만 반영**
   - 사용자가 "pandas 버전은 1.3으로 맞춰주세요"라고 하면 **이 변환 작업에만** 적용
   - 다음 작업에서는 다시 기본 스택 + 확인 절차 반복
   - 지속적 변경이 필요하면 사용자가 `active_stack.txt`를 수정하도록 안내

**예시 응답 패턴**:
```
이 변환 작업의 타겟 환경을 확인하겠습니다. 기본 스택(default.yaml)을 가정하면:
- Python: 3.9
- 허용 패키지: numpy, pandas, scipy, scikit-learn, torch, langgraph
- 라이선스 정책: MIT/Apache-2.0/BSD 계열만 허용

이대로 진행할까요? 타겟 환경이 다르면 (예: Python 3.8, pandas 1.3 고정 등) 알려주세요.
```

### 데이터 영속화 원칙 (요약)
폐쇄망 환경에서는 **개발 공간 → 업무 공간 파일 이동 시 보안 심사**를 받습니다. 이 과정에서 바이너리 파일(SQLite, pickle, parquet 등)은 반출이 까다롭거나 불가능하지만, HTML 파일은 self-contained인 경우 반출 가능합니다. 따라서 **기록·상태·결과물은 모두 self-contained HTML로 통합**합니다.

구체적 규칙과 금지 패턴은 섹션 6 "🚫 데이터 영속화 관련" 참고.

### 사용자 페르소나 고려
사용자 수준이 다양하므로:
- 고급 기능보다 **명확한 동작**이 우선
- API는 scikit-learn 스타일(`fit`/`predict`)처럼 **이미 익숙한 패턴** 차용 권장
- 마법 같은 추상화(metaclass, 동적 import)는 지양

---

## 5. 코딩 컨벤션 & 스타일 규칙

### 기본 원칙
**가독성 우선.** 성능/간결함보다 "다음 분기에 들어온 신입 분석가도 읽을 수 있는 코드"를 목표로 합니다.

### 구체적 규칙

#### 주석과 문서화
- **한글 주석 선호**. 한국 금융사 사용자가 주 독자이므로 영어 주석보다 한글이 이해가 빠름
- 각 함수/클래스 상단에 **무엇을 왜 하는지** 한글 docstring 작성
- 복잡한 로직 블록 위에는 **의도 주석** (`# 이 부분은 ~을 위해 필요합니다`)
- 원본 오픈소스의 어느 부분에서 영감을 받았는지 주석으로 명시

#### 파일 구조 (single-file 내부)
```python
"""
<모듈 제목 및 한 줄 설명>

원본 출처: <원본 라이브러리 이름 + 버전 + GitHub URL>
라이선스: <예: MIT, Apache-2.0>
생성: Code Conversion Agent
"""

# ===== 1. Imports =====
import numpy as np
# (허용된 것만)

# ===== 2. 유틸리티 함수 =====

# ===== 3. 핵심 클래스/함수 =====

# ===== 4. Example Usage =====
if __name__ == "__main__":
    # 예제 입출력
    ...
```

#### 필수 포함 항목
1. **Example Usage** (`if __name__ == "__main__":` 블록): 복사·실행 가능한 예제
2. **원본 출처와 라이선스**: 파일 최상단 docstring에 명시
3. **한글 주석**: 핵심 로직에 빠짐없이

#### 네이밍 규칙
- **함수/변수**: `snake_case` (예: `fit_model`, `input_tensor`)
- **클래스**: `PascalCase` (예: `Segmenter`, `TextClassifier`)
- **상수**: `UPPER_SNAKE_CASE` (예: `DEFAULT_BATCH_SIZE`)
- **비공개(internal)**: 앞에 `_` 접두어 (예: `_compute_weights`)
- **약어 사용 지양**: `img_segmenter` ✅ vs `imseg` ❌
- **원본 라이브러리의 주요 클래스명은 가급적 유지**하여 사용자가 문서를 찾기 쉽도록 함

#### 에러 처리
- **명시적 예외 타입 사용**: `except Exception:`이 아니라 `except ValueError:`
- **무시해도 되는 예외는 주석으로 이유 명시**
  ```python
  try:
      result = compute()
  except StopIteration:
      # 이터레이터가 비어있는 경우 기본값 사용 (설계상 정상 경로)
      result = default_value
  ```
- **사용자에게 의미 있는 에러 메시지**: `raise ValueError("input_dim must be > 0, got %d" % dim)`
- **원본 라이브러리의 예외 계층은 단순화**: 커스텀 예외를 무리하게 이식하지 말고, 표준 예외(`ValueError`, `RuntimeError`, `TypeError`)로 매핑

#### 로깅 vs print
- **라이브러리성 코드(변환물 본체)**: `logging` 모듈 사용 (`logger = logging.getLogger(__name__)`)
- **예제 스크립트(`examples/`)**: `print()` 사용 가능 (사용자 눈으로 바로 확인하는 용도이므로)
- **진행률 표시**: tqdm 등 외부 패키지가 활성 스택에 없으면 `print()` + `\r`로 간단히 구현
- **디버그 로그 남발 금지**: `logger.debug`는 변환물 사용자가 켜고 끌 수 있도록 남기되, `logger.info`는 정말 사용자가 알아야 할 것만

#### 타입 힌트
- **공개 API에는 타입 힌트 권장** (사용자가 읽기 편함)
- **내부 헬퍼 함수는 선택**
- **타입 힌트 호환성은 활성 스택의 Python 버전에 맞춤** (3.9 이하면 `from __future__ import annotations` 또는 `typing.List` 사용)

---

## 6. 금지사항 (DO NOT)

### 🚫 외부 의존성 관련
- **활성 스택에 정의되지 않은 패키지 import 금지**
  - 허용 목록은 `references/allowed-stacks/<active>.yaml`의 `allowed_packages` 참고
  - 금지 목록(참고용 비망록)은 같은 파일의 `blocked_packages` 참고
  - 확신이 서지 않으면 활성 스택 YAML을 먼저 확인
- **`pip install`, `conda install` 등 런타임 설치 명령 포함 금지**

### 🚫 네트워크 관련
- **외부 인터넷 호출 코드 포함 금지**
  - HTTP 클라이언트 라이브러리 사용 금지 (구체 목록은 활성 스택 YAML의 `blocked_packages` 참고)
  - 외부 URL에서 모델/데이터를 다운로드하는 코드 금지 (`from_pretrained("http://...")` 같은 패턴)
  - 원격 telemetry, 로깅 서버 전송 코드 금지
- **소켓/포트 오픈 코드 금지** (Agent가 만든 single-file에 한함)

### 🚫 데이터 영속화 관련
(배경과 맥락은 섹션 4의 "데이터 영속화 원칙 (요약)" 참고)

**반드시 지켜야 할 규칙:**

1. **바이너리 포맷으로 저장하는 코드 금지**
   - 구체 금지 목록은 활성 스택 YAML의 `blocked_persistence_formats` 참고
   - 일반적으로 SQLite(`.db`, `.sqlite`), pickle(`.pkl`), parquet, feather, HDF5 등
2. **외부 파일을 참조하는 HTML 금지**
   - 금지 패턴: `fetch()`, `XMLHttpRequest`, `<link href="...">`, `<script src="...">`
   - 이미지도 `<img src="file.png">` 금지 → base64 data URI로 인라인
3. **HTML은 반드시 self-contained**
   - 데이터: `<script type="application/json" id="data">...</script>` 형태로 임베드
   - CSS: `<style>` 태그 안에 인라인
   - JS: `<script>` 태그 안에 인라인
   - 이미지: base64 data URI
4. **기록/로그/결과물은 HTML 하나로 통합**
   - 시각화·테이블·리포트를 별도 파일로 분리하지 않음
   - 대안이 필요하면 순수 텍스트(JSON, CSV)로만 분리
5. **HTML 생성은 표준 라이브러리 또는 활성 스택 내 도구로**
   - `string.Template`, f-string, 순수 HTML 템플릿 문자열 사용
   - 외부 템플릿 엔진(Jinja2 등)은 활성 스택에 명시되지 않은 이상 금지

### 🚫 라이선스 관련
- **활성 스택의 `license_policy.blocked_categories`에 해당하는 라이선스 코드 차용 금지**
  - 해당 라이선스 오픈소스를 발견하면 **변환 중단하고 사용자에게 알림**
  - 대안: `license_policy.allowed_categories` 중에서 우선 탐색
- 라이선스를 확인할 수 없는 경우, 활성 스택의 `license_policy.on_unknown` 정책 따름

### 🚫 프롬프트 복잡도 관련
로컬 LLM(상대적으로 제한된 reasoning 능력)을 쓰는 환경을 전제로 합니다.
- **Chain-of-Thought을 여러 단계로 중첩하지 않기**
- **한 프롬프트에 너무 많은 제약/예시 투입 금지** (토큰 낭비 + 품질 저하)
- 복잡한 작업은 **여러 step으로 분해** (한 프롬프트에 모든 걸 담지 말 것)
- 프롬프트 템플릿은 **간결하고 명시적**으로. 암시적/추상적 표현 지양
- Agentic workflow 구현 시: 노드는 **단일 책임 원칙**을 지키고, state 스키마를 최소화. 과도하게 분기된 그래프는 로컬 LLM에서 디버깅이 어려움

### 🚫 기타
- 개인정보/금융정보가 예제에 포함되지 않도록 주의
- 사내 데이터 스키마, 테이블명을 하드코딩하여 예제에 넣지 않기

---

## 7. 알려진 이슈 & 한계점

### 로컬 LLM의 한계
- **컨텍스트 길이 제약**: 대형 라이브러리 전체를 한 번에 읽히기 어려움
  → 파일 단위로 선택적 샘플링 필요
- **코드 생성 품질 편차**: 복잡한 알고리즘(특히 C++/Cython 기반 코드)의 재구현은 실패율이 높음
  → 이 경우 Agent가 "불가능" 신호를 사용자에게 명확히 전달해야 함
- **최신 라이브러리 미학습**: 로컬 LLM의 cutoff 이후 릴리즈된 API는 알지 못함

### Single-file의 본질적 한계
- **재사용성 ≠ 유지보수성**: single-file은 반입은 쉽지만, 장기 유지보수에는 불리
  → 대규모/장기 운영 코드는 이 Agent의 타겟이 아님을 사용자에게 명시
- **Cython/C extension 의존 기능 변환 불가**: pandas 내부 등 성능 크리티컬 부분은 재구현 대상이 아님
- **대용량 사전학습 weight는 변환 범위 밖**: 구조만 재구현, weight는 별도 반입 필요

### 검증의 한계
- 변환된 코드와 원본의 **완전한 동치성은 보장 불가**
- Agent는 대표 테스트 케이스로 "행동 근사 동일성(behavioral approximation)"만 확인
- 사용자는 **자체 검증** 필요 (운영 투입 전 반드시)

### 라이선스 판단의 한계
- Agent의 라이선스 판단은 참고용이며, **법적 검토 대체 불가**
- 모호한 경우 보수적으로 차단하지만, 최종 책임은 사용자에게 있음

---

## 8. 사용 가능한 Skills

이 프로젝트에서는 재사용 가능한 기능 단위를 `skills/` 폴더에 Skill 문서로 관리합니다. 각 Skill의 상세 동작·입력·워크플로우는 해당 SKILL.md를 참조합니다.

### 등록된 Skills

| Skill | 경로 | 언제 쓰는가 |
|---|---|---|
| `environment-adapter` | `skills/environment-adapter/SKILL.md` | 타겟 환경(Dockerfile, requirements.txt 등)에 맞춰 변환물 코드를 조정할 때 |

### Skill 추가 규칙
- 새 Skill은 `skills/<skill-name>/SKILL.md` 형태로 추가 (`<skill-name>`은 kebab-case 영문)
- SKILL.md는 **YAML frontmatter (`name`, `description`) 필수**
- `description`은 "무엇을 하는지"와 "언제 트리거할지"를 모두 담을 것 (Skill 트리거의 기반)
- 본문은 500줄 미만을 목표로. 큰 참조 자료는 `references/` 아래로 분리

---

## 9. Agent가 지켜야 할 행동 원칙 (요약)

1. **모르면 구현하지 말고 묻는다.** 추측으로 코드를 만들지 않는다.
2. **매 작업마다 스택을 확인한다.** 이전 작업의 가정을 재사용하지 않는다. 지정되지 않으면 기본 스택을 제시하고 확인을 구한다.
3. **한 파일, 한 목적.** 여러 기능을 한 파일에 욱여넣지 않는다.
4. **한 폴더, 한 변환물.** 번호 기반 폴더로 독립 관리하며 기본 구조를 유지한다.
5. **의존성은 적을수록 선(善).** 허용 목록을 넘으면 대안을 찾는다.
6. **사용자는 다양하다.** 가장 초급 사용자를 기준으로 설명한다.
7. **불확실성은 명시한다.** "아마도"는 코드가 아니라 주석에 쓴다.
8. **영속화는 HTML로.** 바이너리 파일은 반출되지 않는다. 기록은 self-contained HTML에 담는다.