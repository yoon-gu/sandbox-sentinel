---
name: environment-adapter
description: Adapt converted single-file code to a specific closed-network Python environment (특정 폐쇄망 Docker 이미지의 Python/라이브러리 버전에 맞춰 변환물 코드를 조정). Use whenever the user provides a Dockerfile, requirements.txt, `docker inspect` output, or any environment specification and wants code to be compatible with it — even if they don't explicitly say "adapt". Also use when API deprecation warnings or ImportError surface on a target environment, when a converted artifact needs to be downgraded to older Python (3.8/3.9), when a required package is missing from the allowed stack and a substitute is needed, or when the user asks for a migration guide between two environments. Do not use for fresh conversions from open-source libraries — that's the main conversion workflow.
---

# Environment Adapter

폐쇄망에 이미 반입된 Docker 이미지의 환경(Python 버전, 라이브러리 버전)에 변환물 코드를 맞추는 Skill입니다.

금융사마다 반입된 환경이 다르고, 버전이 고정되어 있어 업그레이드가 쉽지 않습니다. 이 Skill은 **코드를 환경에 맞추는 방향**으로 동작합니다 (환경을 코드에 맞추는 것이 아니라).

---

## 언제 사용하는가

- 사용자가 환경 명세(Dockerfile, requirements.txt, `docker inspect` JSON, 사용자 정의 YAML/JSON)를 제공하며 코드 조정을 요청할 때
- 기존 변환물을 **다른 환경**에 재배포해야 할 때
- `DeprecationWarning`, `ImportError`, `AttributeError`가 타겟 환경에서 발생한 것을 본 사용자가 해결을 요청할 때
- Python 3.10+ 문법(예: `match`, `X | Y` union 타입)을 3.8/3.9로 다운그레이드해야 할 때

---

## 입력 (Input)

이 Skill은 다양한 형태의 환경 명세를 입력으로 받습니다. 사용자가 제공한 것에 맞춰 파싱하세요:

| 입력 형태 | 추출해야 할 정보 |
|---|---|
| `Dockerfile` | `FROM python:X.Y`, `pip install` 라인, `ENV` 변수 |
| `requirements.txt` / `requirements.lock` | 패키지명·버전 핀 |
| `docker inspect <image>` JSON | `Config.Env`의 `PYTHON_VERSION`, 실행 환경 변수 |
| `pip freeze` 출력 | 설치된 패키지의 정확한 버전 |
| 사용자 정의 YAML/JSON | `python_version`, `packages`, `platform` 등의 키 |
| 자유 서술 ("Python 3.9에 pandas 1.3 쓰고 있어요") | 대화에서 구조화된 스펙으로 변환 후 사용자에게 확인 |

**우선순위**: 명시적 파일 > 대화 서술. 충돌이 있으면 반드시 사용자에게 확인.

---

## 기능 (What this skill does)

### 1. API 호환성 조정 (주 기능)
라이브러리 버전 간 API 변경(deprecation, 이름 변경, 시그니처 변경)을 감지하고 자동 조정합니다.

참조 자료는 `references/api-mappings/` 하위 YAML에 라이브러리별로 관리됩니다:
- `scikit-learn.yaml` — 예: `sklearn.externals.joblib` → `joblib`, `normalize='l2'` 기본값 변경 등
- `pandas.yaml` — 예: `df.append()` → `pd.concat()`, `DataFrame.ix` 제거 등
- `numpy.yaml` — 예: `np.int` → `int`, `np.float` 제거 등
- `torch.yaml` — 예: `torch.tensor` 동작 변경, autograd API 변경 등
- `python-syntax.yaml` — 문법 레벨 매핑 (3.10+ → 3.9/3.8)
- `package-substitutions.yaml` — 한 패키지가 없을 때의 대체안

**작업 순서**:
1. 타겟 버전 파악 (예: pandas 1.3.5)
2. 해당 YAML을 읽어 소스 코드에서 해당 패턴 검색
3. 매핑에 따라 치환하되, **의미가 바뀌는 변경은 사용자 확인**
4. 변경 사항을 diff로 요약

### 2. Python 문법 호환성 다운그레이드
타겟 Python이 3.10 미만이면 다음을 다운그레이드:
- `match/case` → `if/elif` 체인
- `X | Y` (PEP 604) → `Union[X, Y]` (`from typing import Union`)
- `list[int]` 등 네이티브 제네릭 → `List[int]` (`from typing import List`)
- `Self` 타입 → 문자열 forward reference
- 제네릭 슬롯, `ParamSpec` 등 3.10+ 전용 기능 사용 시 경고

### 3. 누락 패키지의 대체안 제시
타겟 환경에 필요한 패키지가 없으면 `package-substitutions.yaml` 참고:
- `scipy` 없음 → `numpy`로 대체 가능 여부 평가
- `seaborn` 없음 → `matplotlib`만으로 재작성 가능 여부 평가
- `requests` 없음 → 표준 `urllib.request` 사용 (단, 폐쇄망에서는 네트워크 자체가 금지이므로 드문 케이스)

대체가 어려우면 **명확히 "불가능"을 사용자에게 전달**.

### 4. 활성 스택과의 일치성 검증
프로젝트 루트의 `active_stack.txt`가 지정한 스택 YAML(`references/allowed-stacks/<name>.yaml`)과 타겟 환경 사이의 gap 분석:
- 타겟 환경에 `allowed_packages` 외 패키지가 있는가? → 괜찮음 (타겟에만 있는 건 기회)
- 코드가 사용 중인 패키지가 타겟 환경에 없는가? → 대체안 찾거나 경고
- `blocked_packages`가 타겟에 설치되어 있어도 코드에서는 **사용하지 않음**

### 5. 마이그레이션 가이드 생성
조정을 마친 뒤 변환물 폴더에 `MIGRATION.md`를 생성합니다:
- 소스 환경 → 타겟 환경 요약
- 변경된 파일과 라인 번호
- 수동 검토가 필요한 부분 (의미 변경 가능성이 있는 치환)
- 테스트 권장 항목

---

## 워크플로우 (Step-by-step)

```
1. 입력 파싱
   └─ 사용자가 준 환경 명세에서 {python_version, packages} 추출

2. 타겟 환경 스펙 확정
   └─ 불명확하면 사용자에게 확인 (핀된 버전 vs 범위 등)

3. 활성 스택 로드
   └─ active_stack.txt 또는 명시된 스택 YAML 읽기

4. 소스 코드 분석
   └─ 대상 변환물 폴더(예: 001-image-segmentation/)의 <main>.py 파싱
   └─ import 문, 함수 호출 패턴 추출

5. API 매핑 적용
   └─ references/api-mappings/ 하위 YAML을 라이브러리별로 로드
   └─ 매칭되는 패턴을 치환 (의미 변경이 있는 건 주석으로 TODO 남기기)

6. 문법 다운그레이드
   └─ 타겟 Python 버전에 필요한 만큼만

7. 검증
   └─ 수정된 코드가 여전히 single-file 원칙을 지키는지
   └─ 활성 스택의 금지 규칙을 위반하지 않는지

8. MIGRATION.md 생성
   └─ 대상 변환물 폴더에 저장

9. 사용자에게 diff 요약 제출
   └─ 의미 변경 가능성이 있는 부분은 명시적으로 플래그
```

---

## 제약 (Constraints)

- **코드 의미를 바꾸는 치환은 자동 적용하지 않음**. 예: pandas `df.append()` → `pd.concat()`은 인덱스 리셋 동작이 미묘하게 다를 수 있음. 반드시 사용자 확인을 거칠 것.
- **Cython/C extension 기반 동작은 조정 범위 밖**. 이 경우 "재변환이 필요함"을 사용자에게 알림.
- **테스트는 이 Skill의 책임이 아님**. 조정 후 동작 검증은 사용자 몫이며, 예제 스크립트가 여전히 실행되는지 정도만 확인.
- **활성 스택 정책 우선**. 타겟 환경에 있어도 스택이 금지한 패키지는 사용하지 않음.

---

## 출력 (Output)

이 Skill이 끝난 후 대상 변환물 폴더의 상태:

```
001-image-segmentation/
├── README.md                 # 변경 없음 (또는 호환 환경 섹션 추가)
├── segmenter.py              # 수정됨
├── metadata.json             # adapted_for 필드 추가
├── LICENSE                   # 변경 없음
├── MIGRATION.md              # 신규 생성
├── examples/
│   └── basic_usage.py        # 필요 시 수정됨
└── data/
```

`metadata.json`에 다음 필드 추가:
```json
{
  "adapted_for": {
    "target_env": "finance-corp-a-2024-q4",
    "python_version": "3.9.18",
    "key_packages": {"pandas": "1.3.5", "numpy": "1.21.6"},
    "adapted_at": "2026-04-18",
    "adapter_version": "0.1.0"
  }
}
```

---

## 사용 예시

### 예시 1: Dockerfile 기반 조정
```
사용자: "이 Dockerfile 환경에 001-image-segmentation을 맞춰줘"
[Dockerfile 첨부]

Skill:
1. Dockerfile에서 python:3.9-slim, scikit-learn==1.0.2 추출
2. segmenter.py 분석: np.float 사용 발견 (numpy 1.20+에서 제거됨)
3. api-mappings/numpy.yaml에서 매핑 확인: np.float → float
4. 치환 후 MIGRATION.md 작성
5. 사용자에게 diff 제출
```

### 예시 2: 에러 메시지 기반 조정
```
사용자: "폐쇄망에서 돌렸더니 'AttributeError: module numpy has no attribute int' 떠"

Skill:
1. 에러 메시지에서 numpy 버전 문제로 추정
2. 사용자에게 타겟 numpy 버전 확인 요청
3. np.int → int로 치환 (references/api-mappings/numpy.yaml)
4. 관련 파일 전체를 스캔하여 유사 패턴 함께 수정
```

### 예시 3: 환경 명세가 없는 경우
```
사용자: "이거 사내에서 안 돌아가"

Skill:
1. 활성 스택 확인 → active_stack.txt 없음
2. references/allowed-stacks/default.yaml 로드 (기본 스택 가정)
3. 사용자에게 확인:
   "환경 정보가 명시되지 않아 기본 스택(Python 3.9, pandas/numpy/
    scikit-learn/torch 등)을 가정하고 진단합니다. 타겟 환경이
    다르다면 다음 중 하나를 공유해 주세요:
    - Dockerfile 또는 `docker inspect` 결과
    - `pip freeze` 출력
    - 에러 메시지 전문 (어느 모듈 어느 라인에서 나는지)"
4. 사용자 응답 대기 후 조정 진행
```

**주의**: 기본 스택 가정만으로 코드를 수정하지 않습니다. 기본 스택은 **진단의 시작점**이며, 실제 수정은 반드시 사용자 확인 후.

---

## 참조 파일

상세 매핑 데이터는 별도 파일로 관리합니다 (SKILL.md가 커지지 않도록):

- `references/api-mappings/scikit-learn.yaml` — scikit-learn 버전별 API 매핑
- `references/api-mappings/pandas.yaml` — pandas 버전별 API 매핑
- `references/api-mappings/numpy.yaml` — numpy 버전별 API 매핑
- `references/api-mappings/torch.yaml` — PyTorch 버전별 API 매핑
- `references/api-mappings/python-syntax.yaml` — Python 문법 레벨 매핑
- `references/api-mappings/package-substitutions.yaml` — 패키지 대체 매핑
- `references/allowed-stacks/*.yaml` — 환경별 허용 스택 정의

매핑 파일이 없거나 커버하지 못하는 케이스를 만나면, 수정 대신 **사용자에게 질문**합니다.
