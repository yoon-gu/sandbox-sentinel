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

### 폐쇄망 환경이란?
- 인터넷이 차단된 개발 환경 (금융사/공공기관 전형)
- 외부 `pip install` 불가 → 사내 미러 저장소를 통해서만 패키지 반입
- 파일 반입도 **보안 심사 절차**를 거침 → 파일이 적고 단순할수록 유리
- 이 때문에 **single-file**이 강력한 가치를 가짐 (리뷰도 쉽고, 반입도 쉬움)

---

## 2. 이 문서의 범위 (Agent 역할 분리)

이 CLAUDE.md 는 **"어떤 환경에서든 적용되는 변환 Agent 의 공통 원칙"** 만 다룹니다:
- 오픈소스 탐색·비교·핵심 아이디어 추출
- single-file 구현·코드 스타일 원칙
- 폴더 구조·필수 산출물
- 데이터 영속화는 HTML 로, 바이너리 없음 등의 철학적 원칙

**환경별 구체 정책은 Skill 에 위임합니다.** 허용/금지 패키지 목록, 라이선스 카테고리, 영속화 금지 포맷 목록, 버전 핀, 타겟 Python 버전 등 **"어느 폐쇄망이냐"에 따라 달라지는 구체 리스트는 `.claude/skills/environment-adapter/` 가 전담**합니다. 변환 작업 시작 시 Skill 이 자동 트리거되어 현재 타겟 환경을 결정·검증하므로, Agent 는 이 문서의 원칙 + Skill 이 주는 환경 스펙을 조합해 행동합니다.

자세한 역할 분담은 섹션 8 (Skills) 참고.

---

## 3. Agent 워크플로우

Agent 는 다음 하이브리드 플로우로 동작합니다:

```
[1단계] 요구사항 분석
  └─ 사용자 요청에서 "어떤 기능이 필요한가" 를 구체화

[2단계] 오픈소스 탐색 & 후보 비교
  └─ 여러 오픈소스 라이브러리를 탐색하여 후보군 추출
  └─ 기능/라이선스/의존성 관점에서 비교

[3단계] 핵심 아이디어 추출 (Core Extraction)
  └─ 선택된 라이브러리에서 "정말 필요한 기능" 만 식별
  └─ 불필요한 추상화, 플러그인, 레거시 호환 코드 제거

[4단계] 환경 대응 스펙 확정 ← environment-adapter Skill 트리거
  └─ 현재 작업의 타겟 환경(Python 버전, 허용 패키지, 라이선스 정책 등)을 Skill 이 결정

[5단계] 단일 파일 구현
  └─ Skill 이 확정한 제약 안에서만 의존성 사용
  └─ 한글 주석 + 예제 usage 포함

[6단계] 검증
  └─ 원본 동작과의 근사 일치 여부 확인
  └─ Skill 이 정의한 금지 규칙 (라이선스/패키지/영속화 포맷) 준수 확인
```

CLAUDE.md 는 [1~3, 5~6] 의 공통 원칙을 담고, [4] 는 Skill 이 주관합니다.

---

## 4. 리포지토리 구조 & 폴더 규칙

### 전체 리포지토리 구조

```
<repo-root>/
├── README.md                    # 리포 전체 개요, 변환물 인덱스
├── CLAUDE.md                    # (이 문서)
├── .claude/
│   └── skills/
│       └── environment-adapter/     # 환경 대응 Skill 일체
│           ├── SKILL.md
│           └── stacks/
│               └── default.yaml     # 기본 스택 정의 (허용/금지 패키지·라이선스·포맷)
├── 001-image-segmentation/      # 변환물 #1
├── 002-text-classification/     # 변환물 #2
└── ...
```

환경별 정책 파일 (`stacks/*.yaml`) 은 Skill 이 자체 관리하므로 리포 루트에는 없습니다.

### 폴더명 컨벤션
- 형식: `{3자리 번호}-{kebab-case 설명}/`
- 번호는 **생성 순서**대로 001부터 증가 (중복·건너뜀 없음)
- 설명은 **영문 kebab-case** (공백·대문자·언더스코어 사용 금지)
- 예시:
  - ✅ `001-image-segmentation/`
  - ✅ `012-gradient-boosting-lite/`
  - ❌ `001_image_segmentation/` (언더스코어)
  - ❌ `1-imageseg/` (번호 자릿수 부족)
  - ❌ `image-segmentation/` (번호 누락)

### 폴더 내부 기본 구조

각 변환물 폴더는 다음 구조를 **기본**으로 합니다:

```
001-image-segmentation/
├── README.md               # 이 변환물의 설명 (한글)
├── <main>.py               # 핵심 single-file 소스코드
├── metadata.json           # 변환 메타데이터
├── LICENSE                 # 원본 라이선스 파일 복제본
├── examples/               # 학습/사용 예제
│   ├── basic_usage.py      # CLI/스크립트형 변환물에 한정 (TUI · pure Python lib 등)
│   └── demo.ipynb          # 노트북 변환물에 한정 (ipywidgets/HTML 등 Jupyter 안에서 동작)
└── data/                   # 샘플 데이터 (선택, 작은 파일만)
```

### 필수 파일 명세

#### `README.md` (한글 작성)
최소 포함 항목:
- 변환물의 **한 줄 요약**
- **원본 출처**: 라이브러리명, 버전, GitHub URL, 라이선스
- **기능 요약**: 이 변환물이 제공하는 핵심 기능 (원본 대비 축약된 부분 명시)
- **의존성**: 실제 사용한 것 (허용 여부는 Skill 이 검증)
- **사용 예시**: 예제 파일(`examples/basic_usage.py` 또는 `examples/demo.ipynb`) 참조 안내
- **알려진 제약/한계점**

#### `<main>.py`
- 파일명은 **의미 있는 이름** 사용 (예: `segmenter.py`, `detector.py`)
- `main.py` 처럼 일반적인 이름은 여러 변환물을 `sys.path` 에 추가했을 때 충돌하므로 **지양**
- Single-file 원칙 준수 (섹션 5의 파일 구조 템플릿 참고)

#### `metadata.json`
변환 추적 메타데이터. 예시:
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
  "dependencies_used": ["numpy", "scipy"],
  "extracted_features": ["Felzenszwalb segmentation", "SLIC superpixels"],
  "limitations": ["Cython 기반 내부 최적화 코드는 순수 Python 으로 재구현되어 대용량 이미지에서 성능 저하 가능"]
}
```

#### `LICENSE`
- **원본 라이브러리의 LICENSE 파일을 그대로 복제**
- 파일 상단에 "이 라이선스는 원본 `<라이브러리명> v<버전>` 에서 가져왔습니다" 주석 추가 가능
- 원본 라이선스가 **타겟 스택의 허용 카테고리에 속하지 않으면 변환 거부** (Skill 이 판단)

#### `examples/` 디렉토리
- **변환물 형태에 맞는 1개 이상의 예제 필수**:
  · CLI / TUI / 라이브러리형 → `basic_usage.py` (독립 실행 가능 한 .py)
  · Jupyter 노트북에서 동작하는 위젯형 (ipywidgets, HTML/JS 임베드 등)
    → `demo.ipynb` 만으로 충분. 노트북 자체가 사용 예제이므로 별도
    `basic_usage.py` 를 추가할 필요 없음.
- `.py` 예제는 **독립 실행 가능** 해야 함 (Jupyter 의존 금지)
- 예제는 자체 포함 데이터 또는 `../data/` 참조만 사용

#### `data/` 디렉토리 (선택)
- **작은 샘플 데이터만** 포함 (수 MB 이내 권장)
- 개인정보·사내 데이터·라이선스 불명확 데이터 **절대 금지**

### 일관성 원칙
- 기본 구조는 모든 폴더에 적용. **필수**: `README.md`, `<main>.py`, `metadata.json`, `LICENSE`, 그리고 변환물 형태에 맞는 예제 (`examples/basic_usage.py` 또는 `examples/demo.ipynb`) 1개 이상.
- 필요 시 유연하게 확장 가능 (configs/, notebooks/, benchmarks/ 등)
- 확장 디렉토리/파일은 README.md 에 간단히 설명 추가

---

## 5. 코딩 컨벤션 & 스타일 규칙

### 기본 원칙
**가독성 우선.** 성능/간결함보다 "다음 분기에 들어온 신입 분석가도 읽을 수 있는 코드" 를 목표로 합니다.

### 주석과 문서화
- **한글 주석 선호**. 한국 금융사 사용자가 주 독자이므로 영어 주석보다 한글이 이해가 빠름
- 각 함수/클래스 상단에 **무엇을 왜 하는지** 한글 docstring 작성
- 복잡한 로직 블록 위에는 **의도 주석** (`# 이 부분은 ~을 위해 필요합니다`)
- 원본 오픈소스의 어느 부분에서 영감을 받았는지 주석으로 명시

### 파일 구조 (single-file 내부)
```python
"""
<모듈 제목 및 한 줄 설명>

원본 출처: <원본 라이브러리 이름 + 버전 + GitHub URL>
라이선스: <예: MIT, Apache-2.0>
생성: Code Conversion Agent
"""

# ===== 1. Imports =====
import numpy as np
# (Skill 이 허용한 패키지만)

# ===== 2. 유틸리티 함수 =====
# ===== 3. 핵심 클래스/함수 =====
# ===== 4. Example Usage =====
if __name__ == "__main__":
    ...
```

### 필수 포함 항목
1. **Example Usage** (`if __name__ == "__main__":` 블록): 복사·실행 가능한 예제
2. **원본 출처와 라이선스**: 파일 최상단 docstring 에 명시
3. **한글 주석**: 핵심 로직에 빠짐없이

### 네이밍 규칙
- **함수/변수**: `snake_case` (예: `fit_model`, `input_tensor`)
- **클래스**: `PascalCase` (예: `Segmenter`, `TextClassifier`)
- **상수**: `UPPER_SNAKE_CASE` (예: `DEFAULT_BATCH_SIZE`)
- **비공개(internal)**: 앞에 `_` 접두어
- **약어 사용 지양**: `img_segmenter` ✅ vs `imseg` ❌
- **원본 라이브러리의 주요 클래스명은 가급적 유지** 하여 사용자가 원본 문서를 찾기 쉽도록 함

### 에러 처리
- **명시적 예외 타입 사용**: `except Exception:` 이 아니라 `except ValueError:`
- **무시해도 되는 예외는 주석으로 이유 명시**
- **사용자에게 의미 있는 에러 메시지**
- **원본 라이브러리의 예외 계층은 단순화**: 표준 예외 (`ValueError`, `RuntimeError`, `TypeError`) 로 매핑

### 로깅 vs print
- **라이브러리성 코드(변환물 본체)**: `logging` 모듈 사용
- **예제 스크립트(`examples/`)**: `print()` 사용 가능
- **진행률**: tqdm 등이 허용 스택에 없으면 `print()` + `\r`

### 타입 힌트
- **공개 API 에는 타입 힌트 권장** (사용자가 읽기 편함)
- **내부 헬퍼 함수는 선택**
- **Python 버전 호환성**: 타겟 Python 버전은 Skill 이 결정. 낮은 버전 대응이 필요하면 `from __future__ import annotations` 또는 `typing.List` 등 Skill 지시에 따름

### 사용자 페르소나 고려
사용자 수준이 다양하므로:
- 고급 기능보다 **명확한 동작** 이 우선
- API 는 scikit-learn 스타일 (`fit`/`predict`) 처럼 **이미 익숙한 패턴** 차용 권장
- 마법 같은 추상화 (metaclass, 동적 import) 는 지양

---

## 6. 공통 금지사항 (원칙)

아래는 **모든 폐쇄망 환경에 공통으로 적용되는 원칙 레벨 금지사항** 입니다. **구체 리스트 (어떤 패키지·어떤 라이선스·어떤 포맷이 정확히 금지되는지) 는 `.claude/skills/environment-adapter/stacks/<stack>.yaml` 에 정의되고 Skill 이 적용합니다.**

### 🚫 외부 의존성
- **타겟 스택에 정의되지 않은 패키지 import 금지** — 판단은 Skill 이 수행
- **`pip install`, `conda install` 등 런타임 설치 명령 포함 금지**

### 🚫 네트워크 호출
- **외부 인터넷 호출 코드 포함 금지**
  - HTTP 클라이언트 라이브러리 사용 금지 (구체 목록은 Skill 이 관리)
  - 외부 URL 에서 모델/데이터 다운로드 (`from_pretrained("http://...")`) 금지
  - 원격 telemetry·로깅 서버 전송 코드 금지
- **소켓/포트 오픈 코드 금지** (Agent 가 만든 single-file 한정)

### 🚫 데이터 영속화
**배경**: 폐쇄망 환경에서는 **개발 공간 → 업무 공간 파일 이동 시 보안 심사** 를 받습니다. 바이너리 파일 (SQLite, pickle, parquet 등) 은 반출이 까다롭거나 불가능하지만, HTML 파일은 self-contained 인 경우 반출 가능합니다. 따라서 **기록·상태·결과물은 모두 self-contained HTML 로 통합** 합니다.

**원칙**:
1. **바이너리 포맷 저장 금지** — 구체 확장자 목록은 Skill 이 관리 (`blocked_persistence_formats`)
2. **외부 파일 참조 HTML 금지** — `fetch()`, `XMLHttpRequest`, `<link href=>`, `<script src=>`, `<img src="file.png">` 금지. 이미지도 base64 data URI 로 인라인
3. **HTML 은 반드시 self-contained** — 데이터/CSS/JS/이미지 모두 단일 HTML 안에 임베드
4. **기록/로그/결과물은 HTML 하나로 통합** — 시각화·테이블·리포트를 별도 파일로 분리하지 않음 (텍스트 JSON/CSV 는 예외적 허용)
5. **HTML 생성은 표준 라이브러리 또는 Skill 허용 패키지로** — 외부 템플릿 엔진은 Skill 허용 여부 확인 후

### 🚫 라이선스
- **Skill 이 정의한 차단 카테고리 라이선스의 코드 차용 금지** — 발견 시 변환 중단하고 사용자에게 알림
- 라이선스 불명확 시 Skill 의 `on_unknown` 정책 (`ask` / `block` / `allow`) 따름

### 🚫 프롬프트 복잡도
로컬 LLM (상대적으로 제한된 reasoning 능력) 을 쓰는 환경 전제:
- Chain-of-Thought 을 여러 단계로 중첩하지 않기
- 한 프롬프트에 너무 많은 제약/예시 투입 금지
- 복잡한 작업은 여러 step 으로 분해
- Agentic workflow 구현 시 노드는 단일 책임 원칙, state 스키마 최소화

### 🚫 기타
- 개인정보/금융정보가 예제에 포함되지 않도록 주의
- 사내 데이터 스키마·테이블명을 하드코딩하여 예제에 넣지 않기

---

## 7. 알려진 이슈 & 한계점

### 로컬 LLM 의 한계
- **컨텍스트 길이 제약**: 대형 라이브러리 전체를 한 번에 읽히기 어려움 → 파일 단위 선택적 샘플링 필요
- **코드 생성 품질 편차**: 복잡한 알고리즘(특히 C++/Cython 기반) 의 재구현은 실패율이 높음 → Agent 가 "불가능" 신호를 사용자에게 명확히 전달
- **최신 라이브러리 미학습**: 로컬 LLM cutoff 이후 릴리즈된 API 는 알지 못함

### Single-file 의 본질적 한계
- **재사용성 ≠ 유지보수성**: single-file 은 반입은 쉽지만 장기 유지보수에는 불리 → 대규모/장기 운영 코드는 타겟이 아님을 명시
- **Cython/C extension 의존 기능 변환 불가** → 재변환 대상이 아님을 사용자에게 전달
- **대용량 사전학습 weight 는 변환 범위 밖** → 구조만 재구현, weight 는 별도 반입 필요

### 검증의 한계
- 변환된 코드와 원본의 **완전한 동치성은 보장 불가**
- Agent 는 대표 테스트 케이스로 "행동 근사 동일성(behavioral approximation)" 만 확인
- 사용자는 **자체 검증** 필요 (운영 투입 전 반드시)

### 라이선스 판단의 한계
- Agent 의 라이선스 판단은 참고용이며 **법적 검토 대체 불가**
- 모호한 경우 보수적으로 차단하되 최종 책임은 사용자에게 있음

---

## 8. 사용 가능한 Skills

이 프로젝트에서는 재사용 가능한 기능 단위를 `.claude/skills/` 폴더에 **프로젝트 Skill** 로 등록하여 관리합니다. Claude Code 가 자동으로 로드하는 공식 경로이며, 리포를 clone 한 모든 사용자가 `/skills` 명령으로 즉시 사용할 수 있습니다.

### 등록된 Skills

| Skill | 경로 | 언제 쓰는가 |
|---|---|---|
| `environment-adapter` | `.claude/skills/environment-adapter/SKILL.md` | **변환 작업 시작 시 및 타겟 환경 스펙 (Dockerfile/requirements.txt/`docker inspect`/`pip freeze` 등) 제공 시 자동 트리거.** 허용/금지 패키지·라이선스 정책·영속화 포맷·Python 버전 등 환경별 구체 리스트를 Skill 이 소유한 `stacks/*.yaml` 에서 읽어 적용. API 호환성 조정·Python 문법 다운그레이드·누락 패키지 대체안도 이 Skill 이 담당. |

### Skill 책임 경계
- **CLAUDE.md**: 환경 무관 공통 원칙 (이 문서)
- **Skill**: 환경별 구체 리스트 (허용/금지 패키지, 라이선스 카테고리, 영속화 포맷 등) + 타겟 환경 대응 행위 (API 치환, 문법 다운그레이드 등)

### Skill 추가 규칙
- 새 Skill 은 `.claude/skills/<skill-name>/SKILL.md` 형태로 추가 (`<skill-name>` 은 kebab-case 영문)
- SKILL.md 는 **YAML frontmatter (`name`, `description`) 필수**
- `description` 은 "무엇을 하는지" 와 "언제 트리거할지" 를 모두 담을 것 (Skill 트리거의 기반)
- 본문은 500줄 미만을 목표로. 큰 참조 자료는 Skill 디렉토리 하위 (`stacks/`, `data/` 등) 로 분리
- Claude Code 세션에서 `/skills` 로 Skill 이 목록에 뜨는지 확인

---

## 9. Agent 가 지켜야 할 행동 원칙 (요약)

1. **모르면 구현하지 말고 묻는다.** 추측으로 코드를 만들지 않는다.
2. **매 변환 작업마다 환경 대응은 Skill 에 위임한다.** 타겟 환경 스펙은 Skill 이 결정·검증하므로 이 문서의 원칙만 붙잡고 가정하지 않는다.
3. **한 파일, 한 목적.** 여러 기능을 한 파일에 욱여넣지 않는다.
4. **한 폴더, 한 변환물.** 번호 기반 폴더로 독립 관리하며 기본 구조를 유지한다.
5. **의존성은 적을수록 선(善).** 허용 목록을 넘으면 대안을 찾는다.
6. **사용자는 다양하다.** 가장 초급 사용자를 기준으로 설명한다.
7. **불확실성은 명시한다.** "아마도" 는 코드가 아니라 주석에 쓴다.
8. **영속화는 HTML 로.** 바이너리 파일은 반출되지 않는다. 기록은 self-contained HTML 에 담는다.
