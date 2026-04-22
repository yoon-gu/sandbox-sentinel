---
name: environment-adapter
description: Adapt converted single-file code to a specific closed-network Python environment (특정 폐쇄망 Python 환경에 변환물 코드를 조정). Use **at the start of every conversion task** to resolve the target stack, and whenever the user provides a Dockerfile, requirements.txt, `docker inspect` output, `pip freeze`, or any other environment specification. Also use when API deprecation warnings or ImportError surface on a target environment, when a converted artifact needs to be downgraded to older Python (3.8/3.9), when a required package is missing from the allowed stack and a substitute is needed, or when the user asks for a migration guide between two environments. This skill **owns the concrete environment policy** — allowed/blocked packages, license categories, blocked persistence formats, Python/CUDA versions — delegated from CLAUDE.md. Do not use for fresh conversions from open-source libraries — that's the main conversion workflow described in CLAUDE.md.
---

# Environment Adapter

폐쇄망에 이미 반입된 Docker 이미지의 환경(Python 버전, 라이브러리 버전, 라이선스 정책)에 변환물 코드를 맞추는 Skill 입니다.

CLAUDE.md 는 "**환경 무관 공통 원칙**" (변환 워크플로, 코드 스타일, 원칙 레벨 금지사항)을 담고, 이 Skill 은 "**환경별 구체 정책**" (허용/금지 패키지, 라이선스 카테고리, 영속화 포맷, 버전 핀 등) 을 전담합니다. **구체 리스트는 이 Skill 이 단독으로 소유·관리** 합니다.

---

## 이 Skill 이 소유하는 정책

아래 모든 항목의 **구체 리스트는 `stacks/*.yaml` 에 정의**되고, Skill 이 각 변환 작업에 적용합니다:

| 정책 | 설명 | YAML 키 |
|---|---|---|
| **허용 패키지** | 타겟 환경에서 import 해도 되는 패키지 | `allowed_packages` |
| **버전 핀** | 정확한 설치 버전이 명시된 패키지 | `package_versions` |
| **금지 패키지** | import 금지 (HTTP 클라이언트, 바이너리 영속화 등) | `blocked_packages` |
| **영속화 금지 포맷** | 출력 금지 파일 확장자 | `blocked_persistence_formats` |
| **라이선스 허용/금지 카테고리** | 차용 가능/불가능 라이선스 분류 | `license_policy` |
| **타겟 Python 버전** | 문법 호환성 타겟 | `python` |
| **CUDA 버전** | GPU 관련 패키지의 호환성 기준 | `cuda` |

---

## 언제 자동 트리거되는가

1. **변환 작업 시작 시** — CLAUDE.md 의 워크플로 [4단계 환경 대응] 에서 이 Skill 이 호출되어 타겟 스택을 결정·확인
2. **사용자가 환경 명세를 제공할 때**
   - Dockerfile, requirements.txt, `docker inspect` JSON, `pip freeze` 출력, 자유 서술
3. **에러 기반 요청**
   - `DeprecationWarning`, `ImportError`, `AttributeError` 가 타겟에서 발생했다고 보고받을 때
4. **Python 문법 다운그레이드 요청**
   - Python 3.10+ 문법 (match, `X | Y`, `list[int]` 등) 을 3.8/3.9 로 내리기
5. **패키지 대체 요청**
   - 타겟 환경에 없는 패키지를 대체할 방법 질문

**하지 말 것**: 이 Skill 은 **오픈소스에서 새 변환물을 만드는 작업** 에는 사용하지 않음. 그 작업은 CLAUDE.md 의 메인 변환 워크플로가 담당.

---

## 스택 결정 로직 (타겟 환경 확정)

변환 작업 시작 또는 사용자가 스펙을 줬을 때 아래 순서로 결정:

1. **사용자가 이번 메시지/대화에서 환경 스펙을 명시적으로 제공** → 그 스펙 우선
   - Dockerfile, requirements.txt, pip freeze, `docker inspect`, 자유 서술 모두 지원
   - 형식별 파싱은 [입력 파싱](#입력-파싱-input-parsing) 참고
2. **없으면 기본 스택 사용** → `stacks/default.yaml` 로드
3. **사용자에게 반드시 확인 요청** — 가정한 스택의 핵심 내용을 요약해 보여주고 수정 의사 묻기:
   ```
   이 변환 작업의 타겟 환경을 다음으로 가정하고 진행하겠습니다 (stacks/default.yaml 기준):
   - Python: 3.11 / CUDA: 12.4
   - 주요 허용 패키지: numpy, pandas, scikit-learn 1.6.0, torch 2.4.0+cu124, transformers 4.49.0, langchain 1.2.10, langgraph 1.0.10 …
   - 라이선스: MIT / Apache-2.0 / BSD 계열만 허용
   - 영속화 금지: .pkl, .sqlite, .parquet, .h5 등
   이대로 진행할까요? 타겟이 다르면 (예: Python 3.8 + pandas 1.3 고정) 스펙 공유 부탁드립니다.
   ```
4. **사용자 수정사항은 현재 작업에만 반영** — 세션 내 다른 작업에 자동 전파하지 않음. 지속적 변경이 필요하면 `stacks/<이름>.yaml` 추가를 권장.
5. **세션 내 이전 작업의 가정을 재사용하지 않음** — 변환 대상마다 타겟 환경이 다를 수 있으므로 매번 [1]부터 반복.

---

## 입력 파싱 (Input Parsing)

사용자가 제공할 수 있는 형태별 추출 방법:

| 입력 형태 | 추출 정보 |
|---|---|
| `Dockerfile` | `FROM python:X.Y`, `pip install` 라인, `ENV` 변수 |
| `requirements.txt` / `*.lock` | 패키지명·버전 핀 |
| `docker inspect <image>` JSON | `Config.Env` 의 `PYTHON_VERSION`, 실행 환경 변수 |
| `pip freeze` 출력 | 설치된 패키지의 정확한 버전 |
| 사용자 정의 YAML/JSON | `python_version`, `packages`, `platform` 등 |
| 자유 서술 (`"Python 3.9에 pandas 1.3 쓰고 있어요"`) | 대화에서 구조화된 스펙으로 변환 후 사용자 확인 |

**우선순위**: 명시적 파일 > 대화 서술. 충돌이 있으면 사용자에게 확인.

---

## Skill 이 수행하는 작업

### 1. 사전 정책 적용 (변환 시작 시)

타겟 스택의 허용/금지 목록을 Agent 에 전달해 **변환 본체 코드가 금지 패키지를 import 하지 않도록** 가드.

- `allowed_packages` 에 없는 import 가 발견되면 변환 중단 → 대체안 탐색 또는 사용자에게 질문
- 원본 라이선스가 `license_policy.blocked_categories` 에 해당하면 변환 거부
- `blocked_persistence_formats` 에 해당하는 확장자 출력 코드 발견 시 **HTML 대안으로 재설계 요청**

### 2. 기존 변환물 환경 조정

사용자가 기존 변환물을 다른 환경에 재배포하려 할 때:

#### API 호환성 조정
라이브러리 버전 간 API 변경을 감지하고 조정. 예시 패턴 (구체 매핑은 Claude 의 일반 지식 + 필요시 조사로 해결):
- `np.float` → `float` (numpy 1.20+ 에서 제거)
- `pd.DataFrame.append()` → `pd.concat([df1, df2])` (pandas 2.0+ 에서 제거)
- `sklearn.externals.joblib` → `joblib` (scikit-learn 0.23+ 에서 이동)
- `torch.tensor(...)` vs `torch.Tensor(...)` 동작 차이
- langgraph `MemorySaver` ↔ `InMemorySaver` 별칭 관계

**의미가 바뀌는 치환은 자동 적용하지 않음** — 반드시 사용자 확인. 예: `df.append()` → `pd.concat()` 은 인덱스 리셋 동작이 미묘하게 다름.

#### Python 문법 호환성 다운그레이드
타겟 Python 이 3.10 미만이면 다음을 다운그레이드:
- `match/case` → `if/elif` 체인
- `X | Y` (PEP 604) → `Union[X, Y]` (`from typing import Union`)
- `list[int]` 등 네이티브 제네릭 → `List[int]` (`from typing import List`)
- `Self` 타입 → 문자열 forward reference
- 필요 시 `from __future__ import annotations` 삽입

#### 누락 패키지 대체안 제시
타겟 환경에 필요한 패키지가 없으면 일반적 대체 경로 제안:
- `scipy` 없음 → `numpy` 만으로 구현 가능 여부 평가
- `seaborn` 없음 → `matplotlib` 만으로 재작성
- `requests` 없음 → 표준 `urllib.request` (단, 폐쇄망에서는 네트워크 자체가 금지이므로 드문 케이스)
- `pyarrow`/`fastparquet` 등 바이너리 영속화 패키지 없음 → HTML / JSONL 로 대체

대체가 어려우면 **명확히 "불가능"** 을 사용자에게 전달.

### 3. 타겟 환경과 코드 gap 분석

- 타겟 환경에 `allowed_packages` 외 패키지가 있는가? → 괜찮음 (타겟에만 있는 건 기회)
- 코드가 사용 중인 패키지가 타겟 환경에 없는가? → 대체안 찾거나 경고
- `blocked_packages` 가 타겟에 설치되어 있어도 코드에서는 **사용하지 않음**

### 4. MIGRATION.md 생성 (선택)

환경 조정이 끝난 뒤 대상 변환물 폴더에 `MIGRATION.md` 를 생성:
- 소스 환경 → 타겟 환경 요약
- 변경된 파일과 라인 번호
- 수동 검토가 필요한 부분 (의미 변경 가능성이 있는 치환)
- 테스트 권장 항목

---

## 워크플로우 (Step-by-step)

```
1. 입력 파싱
   └─ 사용자가 준 환경 명세에서 {python, packages, platform} 추출
      (없으면 stacks/default.yaml 로드)

2. 타겟 스택 확정
   └─ 가정 내용을 사용자에게 확인 (요약 + 필요 시 상세 제공)

3. 정책 리스트 로드
   └─ allowed_packages, blocked_packages, package_versions,
      blocked_persistence_formats, license_policy 를 메모리로

4. (신규 변환) 이 정책을 Agent 에 가드로 전달
   OR (기존 변환물 조정) 대상 폴더의 <main>.py 파싱

5. 조정 수행
   └─ API 치환, 문법 다운그레이드, 대체안 제시
   └─ 의미 변경 가능성 있는 부분은 TODO 주석 + 사용자 확인

6. 검증
   └─ 수정된 코드가 여전히 single-file 원칙 준수
   └─ blocked_packages / blocked_persistence_formats 위반 없음

7. (선택) MIGRATION.md 생성 및 metadata.json 업데이트

8. 사용자에게 diff 요약 제출
   └─ 의미 변경 가능성이 있는 부분은 명시적으로 플래그
```

---

## 제약 (Constraints)

- **코드 의미를 바꾸는 치환은 자동 적용하지 않음.** 반드시 사용자 확인.
- **Cython/C extension 기반 동작은 조정 범위 밖.** "재변환 필요" 를 사용자에게 알림.
- **테스트는 이 Skill 의 책임이 아님.** 조정 후 동작 검증은 사용자 몫.
- **스택 정책 우선.** 타겟 환경에 있어도 스택이 금지한 패키지는 사용하지 않음.
- **세션 간 스택 가정 재사용 금지.** 매 작업마다 입력 파싱 → 확인 반복.

---

## 출력 (Output)

조정이 끝난 변환물 폴더 상태:

```
001-image-segmentation/
├── README.md                 # 변경 없음 (또는 호환 환경 섹션 추가)
├── segmenter.py              # 수정됨
├── metadata.json             # adapted_for 필드 추가
├── LICENSE                   # 변경 없음
├── MIGRATION.md              # 신규 생성 (선택)
├── examples/
│   └── basic_usage.py        # 필요 시 수정됨
└── data/
```

`metadata.json` 에 추가 필드:
```json
{
  "adapted_for": {
    "target_env": "finance-corp-a-2026-q2",
    "python_version": "3.11",
    "cuda_version": "12.4",
    "key_packages": {"torch": "2.4.0+cu124", "transformers": "4.49.0"},
    "adapted_at": "2026-04-23"
  }
}
```

---

## 사용 예시

### 예시 1: Dockerfile 기반 조정
```
사용자: "이 Dockerfile 환경에 001-image-segmentation 을 맞춰줘"
[Dockerfile 첨부]

Skill:
1. Dockerfile 파싱 → python:3.9-slim, scikit-learn==1.0.2 추출
2. segmenter.py 분석 → np.float 사용 발견 (numpy 1.20+ 제거됨)
3. np.float → float 치환 (의미 변경 없음, 자동 적용 가능)
4. metadata.json 에 adapted_for 추가, MIGRATION.md 작성
5. diff 요약 제출
```

### 예시 2: 에러 메시지 기반 조정
```
사용자: "폐쇄망에서 돌렸더니 'AttributeError: module numpy has no attribute int' 떠"

Skill:
1. 에러 메시지에서 numpy 버전 문제 추정
2. 사용자에게 타겟 numpy 버전 확인 요청
3. np.int → int 치환
4. 관련 파일 전체 스캔하여 유사 패턴 함께 수정
```

### 예시 3: 스펙 없이 새 변환 작업 시작
```
사용자: "scikit-image 기반으로 이미지 세그멘테이션 변환물 만들어줘"

Skill (CLAUDE.md 워크플로 [4단계] 에서 호출됨):
1. 이번 메시지에 환경 스펙 없음 → stacks/default.yaml 로드
2. 사용자에게 요약 제시 + 확인 요청
3. 사용자가 OK → 그 정책을 Agent 에 가드로 전달
4. Agent 가 CLAUDE.md 워크플로 [5단계] 로 진행 (single-file 구현)
```

---

## 참조 파일

Skill 디렉토리 내부에서 관리:

```
.claude/skills/environment-adapter/
├── SKILL.md                        # 이 문서
└── stacks/
    └── default.yaml                # 기본 스택 (추가 환경은 finance-*.yaml 등으로 추가)
```

**`stacks/*.yaml` 스키마** (default.yaml 의 주석 참고):
- `name`, `description`: 스택 이름·설명
- `python`, `cuda`: 버전
- `allowed_packages`: 리스트
- `package_versions`: 버전 핀 맵 (선택)
- `blocked_packages`: 금지 리스트
- `blocked_persistence_formats`: 확장자 리스트
- `license_policy.allowed_categories` / `blocked_categories` / `on_unknown`

새 환경이 필요하면 `stacks/<env-name>.yaml` 을 추가하고 사용자가 해당 스펙을 대화에서 언급하거나 파일로 제공.
