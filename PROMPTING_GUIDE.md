# PROMPTING_GUIDE.md — Claude Code 로 폐쇄망 변환물 만들기

> "누군가 어떻게 Claude Code 에게 물어보면 이런 앱을 만들어주나요?" 에 대한 답.

이 리포의 7개 변환물은 Claude Code 와의 대화로 만들어졌습니다. 그 대화에서 **어떤 프롬프트가 통했는지**, **어떤 워크플로를 따라갔는지**, **어떤 함정이 있는지** 를 정리합니다. 같은 패턴으로 새 변환물을 시작하거나 기존 변환물을 보강하고 싶은 분께.

---

## 0. TL;DR — 가장 짧은 한 줄

```
"<오픈소스 X> 의 <기능 Y> 를 폐쇄망에 반입할 수 있게 single-file Python 으로 만들어주세요"
```

Claude 는 [`CLAUDE.md`](CLAUDE.md) (변환 원칙) + [`environment-adapter` Skill](.claude/skills/environment-adapter/) (환경 정책) 을 자동으로 읽고 그에 맞춰 변환물을 만듭니다. 위 한 줄이면 시작할 수 있습니다.

---

## 1. 핵심 컨셉 3가지

이 리포의 변환물이 어떻게 만들어지는지 이해하면 프롬프트를 잘 쓸 수 있습니다.

### (1) Single-file `.py` 가 반입 단위
폐쇄망에서는 파일 하나당 보안 심사를 거칩니다. 그래서 외부 자산(JS/CSS/WASM 등) 도 raw-string 으로 인라인해서 `.py` 한 파일로 봉인합니다 — 005-sql-codemirror-runner 가 좋은 예 (CodeMirror 244KB 가 `sql_codemirror.py` 안에 통째로).

### (2) 환경 정책은 Skill 이 담당, 코드 원칙은 CLAUDE.md
- [`CLAUDE.md`](CLAUDE.md) → "어느 환경이든 적용되는 공통 원칙" (워크플로, 코드 스타일, single-file 원칙, 영속화는 HTML 로)
- [`environment-adapter` Skill](.claude/skills/environment-adapter/) → "이 폐쇄망의 구체 정책" (허용/금지 패키지, 라이선스 카테고리, Python 버전, 영속화 금지 포맷). `stacks/default.yaml` 에 정의.

→ 새 폐쇄망에 맞추려면 Skill 의 `stacks/<env>.yaml` 만 추가하면 됨. CLAUDE.md 는 안 건드림.

### (3) 반복 대화로 다듬는다
처음부터 완벽 X. 첫 버전 → 사용자가 써봄 → 피드백 → 수정. 이 리포의 005/006 는 둘 다 십수 번의 작은 PR 같은 수정을 거쳤습니다.

---

## 2. 시작 프롬프트 5가지 (실제 통한 것)

### A. 새 변환물 만들기 (가장 일반적)

```
"<오픈소스 X> 의 <기능 Y> 를 폐쇄망 Jupyter 노트북에서 쓸 수 있는 single-file
Python 변환물로 만들어주세요. 좌측에 entity 트리, 우측에 SQL 입력창,
컨텍스트 자동완성 기능이 있었으면 좋겠어요."
```

→ Claude 가 environment-adapter Skill 을 트리거 → 스택 가정 (Python 3.11, 허용 패키지) 확인 → CLAUDE.md 워크플로 [1~3] (오픈소스 탐색·핵심 추출) → [4] (환경 대응) → [5] (single-file 구현) → [6] (검증) 순으로 진행.

**팁**: "노트북에서" / "터미널에서" / "HTML 한 파일로" 처럼 **실행 환경을 명시** 하면 Claude 가 ipywidgets / textual / HTML/JS 중 적절한 도구를 고릅니다.

### B. 환경에 맞추기 (Dockerfile / requirements 가 있을 때)

```
"이 Dockerfile 환경에 [기존 변환물] 를 맞춰주세요"
[Dockerfile 첨부 또는 paste]
```

또는

```
"폐쇄망에서 'AttributeError: module numpy has no attribute int' 떠요"
```

→ environment-adapter Skill 이 자동 트리거되어 API 호환성 조정 (`np.int → int`), Python 문법 다운그레이드, 누락 패키지 대체안 제시.

### C. 기능 추가 (점진적 개선)

```
"006 의 자동완성 popup 에 컬럼 타입을 같이 보여주세요. 너무 길면 이모지로
단축해도 좋아요."
```

```
"화살표로 커서를 옮긴 다음 다시 select 로 돌아가서 e. o. 이런식으로
alias 를 이용하는 경우도 자동완성이 됐으면 좋겠습니다."
```

→ 변환물 별 README 의 기능 요약 + 한계를 보고 무엇이 가능한지 파악한 뒤 "어떤 사용자 시나리오인가" 를 한 줄로. 작은 단위가 빨리 끝납니다.

### D. 버그 신고 (재현 가능한 예시 첨부)

```
"이 쿼리에서 컨텍스트가 컬럼이 아니라 'any' 로 잡힙니다:

  SELECT col AS d, SUM(x) AS revenue, status, |커서|

왜 그럴까요?"
```

→ Claude 가 `detect_context` 동작을 추적 → 원인 (`AS` 가 가장 가까운 anchor) → 수정 → 회귀 테스트. 실제 이 리포의 한 커밋이 정확히 이 흐름.

**팁**: 실제 입력값 + 기대값 + 현재 동작 — 이 셋을 같이 적으면 디버깅 시간 단축.

### E. 정리 / 리팩터

```
"전반적으로 examples/ 폴더 다 없애주세요"
"005 폴더 삭제하고 넘버링 다시 부탁합니다"
"asset/_build/_template 의 역할이 뭐예요?"
```

→ 구조 정리, 명명 규칙 (NNN-kebab-case), 메타데이터 일관성은 Claude 가 일괄 처리. 단순한 한 줄 명령으로 충분.

---

## 3. Claude 가 따라가는 워크플로 (CLAUDE.md)

요청을 받으면 Claude 는 [`CLAUDE.md`](CLAUDE.md) 의 6단계를 자동으로 따라갑니다:

```
[1] 요구사항 분석     ← 사용자 메시지에서 "정말 필요한 기능" 구체화
[2] 오픈소스 탐색·비교 ← 후보 라이브러리 추출, 라이선스/의존성 비교
[3] 핵심 아이디어 추출 ← 불필요한 추상화 제거, 정말 필요한 부분만
[4] 환경 대응 스펙 확정 ← environment-adapter Skill 트리거
[5] 단일 파일 구현      ← single-file 원칙, 한글 주석
[6] 검증               ← 헤드리스 스모크 / CLI --check
```

이 중 [4]는 Skill 이 자동으로 처리하므로 사용자가 "Python 3.11 / pandas 2.0" 같은 스펙을 매번 안 말해도 됩니다. 다른 환경이면 `stacks/default.yaml` 외 새 yaml 을 추가하거나 직접 명세를 주면 됩니다.

---

## 4. 실제로 통한 협업 패턴 (이 리포 사례)

### 패턴 1: 작은 단위로, 빠르게 피드백
```
사용자: "에디터 길이 좀 늘려주세요. 30줄 정도 보였으면"
Claude: cm.setSize(220→600), CSS min-height 갱신, 빌드, 푸시
사용자: "결과창은 좁게 줄여도 OK"
Claude: Output min_height 500→300, 푸시
```
→ 한 메시지에 한 가지만. 다음 메시지에 후속 요청. 가장 안정적.

### 패턴 2: 한계를 정직하게 묻고 결정
```
사용자: "tree-sitter 의존성 없앨 수 있어?"
Claude: 가능하지만 Textual TextArea 의 색깔이 사라집니다.
        다른 기능은 그대로 동작. 어떻게 하시겠어요?
사용자: "그냥 그대로 두시지요"
```
→ Claude 가 "할 수 있긴 한데 trade-off 가 X" 라고 답할 때 사용자가 결정.

### 패턴 3: 우회 가능한 한계는 TODO 로 보내기
```
사용자: "CTE 도 처리할 수 있지 않아?"
Claude: 가능. 단 SELECT body 파싱 + 컬럼 추론이 복잡. 부작용 N가지...
사용자: "너무 복잡해지네요. 나중에 할 TODO 로"
Claude: README 의 한계 섹션에 TODO 로 기록 + 콤마/schema 한계는 보강 완료
```
→ 모든 기능을 다 넣을 필요 없음. 무게/가치 비교 후 보류.

### 패턴 4: Trust 정도에 따라 자율도 부여
```
사용자: "GitHub 에 푸시해주세요"
Claude: (전 25개 커밋이 있으니 검토 후 진행) ... 푸시 완료.
```
→ destructive 가 아닌 동작은 사용자가 명시 허락하면 진행. push, rm, rename 등은 매번 확인이 원칙이지만 사용자가 "그대로 진행" 이라 하면 빠름.

### 패턴 5: 검증 요청을 작은 케이스로
```
사용자: 자기 쿼리 일부를 첨부 → "이 위치에서 컬럼이 안 떠요"
Claude: 실제 입력값으로 detect_context / get_suggestions 호출 → 7/7 케이스 통과 확인 → 사용자에게 결과 보고
```
→ 사용자가 "검증해줘" 라고 안 해도 Claude 가 변경 후 자동으로 작은 헤드리스 테스트로 확인.

---

## 5. 잘 안 됐던 패턴 / 함정

| 함정 | 대안 |
|---|---|
| **요청이 너무 큼** ("뭐든 좋은 SQL editor 만들어줘") | 환경(노트북/TUI), 핵심 기능 1-2개 만 명시 |
| **여러 기능 동시 요청** | 한 메시지 한 기능. 누적되면 추적 어려움 |
| **외부 LLM API 사용 가정** | 폐쇄망이라 외부 호출 0. MockLLM 같은 시뮬레이터 우선 |
| **바이너리 영속화 (`.parquet`, `.pkl`)** | HTML 자기-완결 (Sentinel-Track 의 dashboard.html 처럼) |
| **CodeMirror v6 같은 ESM 번들 라이브러리** | v5 같은 IIFE / UMD 번들이 인라인 적합 |
| **마음에 안 든다고 silently 다시 하라기** | 무엇이 어떻게 안 맞는지 1-2 문장으로. "예전처럼" 도 OK |
| **Browser/Trust 의존 고려 안 함** | 005/006 의 trade-off 표 참고 |

---

## 6. 새 변환물 시작 체크리스트

새 변환물 (`NNN-foo/`) 를 시작할 때 Claude 에게 줄 정보:

```
[1] 한 줄 요약: "<오픈소스 X> 의 <기능 Y> 를 single-file 로"
[2] 실행 환경: 노트북 / 터미널 TUI / HTML / 라이브러리
[3] 핵심 사용 시나리오: "사용자가 ..."
[4] 환경 정책: stacks/default.yaml 그대로 / 별도 명시
[5] 라이선스 검토: 원본 라이브러리 라이선스 (MIT/Apache/BSD 만 OK)
```

[1] 만 명시해도 Claude 가 [4] 를 environment-adapter Skill 로 가정·확인하므로 시작은 가능. 더 자세하게 줄수록 첫 출력의 정확도가 올라감.

작업 진행 후 폴더 구조 (CLAUDE.md 명세 기준):
```
NNN-foo/
├── README.md          # 한 줄 요약 + 원본 출처 + 기능 + 의존성 + 한계
├── <main>.py          # single-file 본체 (의미 있는 이름, "main.py" 지양)
├── metadata.json      # id / source / converted_at / dependencies_used / ...
├── LICENSE            # 원본 라이선스 복제본
├── basic_usage.py     # CLI / TUI / lib 형태일 때
└── demo.ipynb         # 노트북 형태일 때
```

---

## 7. FAQ

### Q. Claude 가 외부 LLM 못 쓰는데 어떻게 시연을?
A. 변환물 본체에는 MockLLM (오프라인 시뮬레이터) 을 동봉합니다. 사내 LLM 어댑터로 1줄 교체할 수 있도록 인터페이스를 명시 (`invoke(messages) -> dict` 같은 형태). 001/003/004 의 `MockLLM` 이 좋은 예.

### Q. 사내 미러에 패키지 추가는?
A. 두 군데 같이 갱신: `requirements.txt` (개발 환경) + `.claude/skills/environment-adapter/stacks/default.yaml` (정책). Claude 에게 "X 를 사내 미러에 추가됐다고 가정해주세요" 라고 말하면 정책 갱신은 직접.

### Q. 노트북 trust 막힌 환경은?
A. 005 (CodeMirror 노트북) 대신 **006 (Textual TUI)** 사용. 인라인 `<script>` 가 차단되는 환경에서도 터미널은 그대로 돌아감.

### Q. Python 3.8 / 3.9 환경?
A. environment-adapter Skill 이 문법 다운그레이드 (`X | Y` → `Union[X, Y]`, `list[int]` → `List[int]`, etc.) 를 담당. "Python 3.9 환경에 맞춰주세요" 라고 말하면 됨.

### Q. 결과를 후속 분석할 수 있나?
A. 005 (CodeMirror 노트북) 는 `runner.last_result` / `runner.history` 로 다음 셀에서 분석 가능. 006 (TUI) 는 DataTable 안에서만 봄 — 후속 분석 필요하면 005 사용.

### Q. CodeMirror 같은 외부 자산을 single-file 에 어떻게?
A. 빌드 단계 패턴 (005 사례):
- `_assets/` 에 원본 .js/.css 보관
- `_template.py` 에 Python wrapper + `# %%BUNDLE%%` 플레이스홀더
- `_build.py` 가 자산을 raw-string 으로 읽어 템플릿에 삽입 → `sql_codemirror.py` 출력 (~290KB)
- 폐쇄망 반입 단위는 결과 `.py` **한 파일만**

### Q. Claude 의 답변이 이상할 때?
A. 1) 더 작게 쪼개서 다시. 2) "왜 이렇게 했나요?" 라고 이유 묻기. 3) "예전처럼" / "원래대로" 로 롤백. Claude 는 git 으로 단계별 커밋하므로 어느 커밋으로든 되돌릴 수 있습니다.

---

## 8. 참고 문서

- [`README.md`](README.md) — 리포 개요 + 변환물 인덱스 + 개발 환경 셋업
- [`CLAUDE.md`](CLAUDE.md) — 변환 워크플로, 코드 스타일, 원칙
- [`DEMO_STORY.md`](DEMO_STORY.md) — 변환물별 시연 시나리오 (~17분 코스)
- [`.claude/skills/environment-adapter/SKILL.md`](.claude/skills/environment-adapter/SKILL.md) — 환경별 정책 진입점
- [`.claude/skills/environment-adapter/stacks/default.yaml`](.claude/skills/environment-adapter/stacks/default.yaml) — 기본 스택 정의 (허용/금지 패키지·라이선스·Python 버전)

각 변환물 폴더의 `README.md` 도 좋은 사례 — 무엇을 어떻게 추출하고, 무엇을 의도적으로 뺐는지가 정직하게 적혀 있습니다.
