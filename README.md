# sandbox-sentinel

> **한국 금융사 폐쇄망 환경**을 위한 **오픈소스 → single-file Python 변환물 모음**과,
> 변환 작업을 돕는 **Claude Code Agent 설정 & Skill**을 함께 담은 리포지토리입니다.

## 이 리포는 무엇인가요? (처음 오신 분을 위한 안내)

폐쇄망(인터넷이 차단된 업무망)에서는 `pip install`이 자유롭지 않고, 외부 파일을 들여올 때마다 보안 심사를 거쳐야 합니다. 그래서 **파일 하나(`.py`)로 완결된 도구**가 강력한 가치를 가집니다.

이 리포지토리는 다음 두 가지를 제공합니다.

1. **변환물 (Conversions)** — 유명 오픈소스의 핵심 기능만 뽑아 **single-file로 재구현**한 폴더들 (`001-`, `002-`, ...)
2. **Agent 설정 & Skills** — 변환 작업을 보조하는 Claude Code의 프로젝트 규칙(`CLAUDE.md`)과 재사용 가능한 Skill들 (`skills/`)

사용자는 원하는 변환물 폴더의 `.py` 파일만 폐쇄망에 반입하면 됩니다. 리포 전체를 반입할 필요가 없습니다.

## 리포 구조

```
sandbox-sentinel/
├── README.md                     # 이 문서
├── CLAUDE.md                     # 변환 Agent 의 환경 무관 공통 원칙 (워크플로, 코드 스타일, 원칙 레벨 금지사항)
├── .claude/
│   └── skills/                   # Claude Code가 자동 로드하는 프로젝트 Skill 모음
│       └── environment-adapter/
│           ├── SKILL.md          # 환경별 구체 정책을 전담하는 Skill
│           └── stacks/
│               └── default.yaml  # 기본 스택 정의 (허용/금지 패키지·라이선스·영속화 포맷)
├── 001-langgraph-notebook-chatbot/   # 변환물 #1: Jupyter용 LangGraph 챗봇 + HTML 트레이서
└── 002-sentinel-track/               # 변환물 #2: 폐쇄망용 wandb 호환 실험 트래커
```

> **역할 분담**: CLAUDE.md 는 환경과 무관한 공통 원칙 (OSS 탐색·핵심 추출·single-file 구현 등)만 담고, **허용/금지 패키지 · 라이선스 카테고리 · 영속화 포맷 · Python/CUDA 버전** 같이 타겟 폐쇄망마다 달라지는 구체 리스트는 `.claude/skills/environment-adapter/` 가 단독으로 소유·관리합니다. 새 환경이 필요하면 `stacks/<env-name>.yaml` 을 추가하면 됩니다.

## 사용 가능한 Skills

Claude Code는 다음 경로의 Skill만 자동 인식합니다.

- **프로젝트 Skill**: `<repo-root>/.claude/skills/<skill-name>/SKILL.md` — 이 리포를 clone한 모든 사용자가 공유
- **사용자 Skill**: `~/.claude/skills/<skill-name>/SKILL.md` — 내 모든 프로젝트에서 개인적으로 사용

**이 리포는 프로젝트 Skill 방식**을 사용합니다. 즉 `.claude/skills/` 하위의 Skill들은 저장소를 clone하고 Claude Code를 이 디렉토리에서 실행하면 별도 설정 없이 즉시 쓸 수 있으며, `/skills` 명령으로도 확인됩니다. 리포 최상단의 `skills/` 폴더에 두면 CLAUDE.md 등 문서에서 참조는 가능해도 **Claude Code가 자동으로 불러오지 않습니다.**

각 Skill은 `SKILL.md` 하나로 정의되며, YAML frontmatter의 `description`이 트리거 조건을 담습니다.

| Skill | 경로 | 언제 쓰는가 |
|---|---|---|
| **environment-adapter** | [`.claude/skills/environment-adapter/SKILL.md`](.claude/skills/environment-adapter/SKILL.md) | **모든 변환 작업 시작 시 + 타겟 환경 스펙 제공 시 자동 트리거.** 허용/금지 패키지·라이선스 정책·영속화 포맷 등 환경 정책 리스트를 소유하며 변환 코드에 강제. API 변경 치환, Python 문법 다운그레이드, 누락 패키지 대체안도 담당. |

### environment-adapter 요약

- **책임 범위**: CLAUDE.md 의 공통 원칙 (영속화는 HTML로, 네트워크 호출 없음 등) 을 **환경별 구체 리스트** (어떤 패키지/라이선스/확장자가 금지/허용인지) 로 내려받아 집행
- **소유 자산**: `stacks/default.yaml` 등 환경 스택 정의 YAML (Skill 디렉토리 내부)
- **입력**: Dockerfile / requirements.txt / `docker inspect` JSON / `pip freeze` 출력 / 자유 서술 등 다양한 환경 명세 (없으면 `stacks/default.yaml` 가정 + 사용자 확인)
- **기능**: ① 타겟 스택 결정·사용자 확인, ② 허용/금지 검증, ③ 라이브러리 버전 간 API 호환 치환, ④ Python 3.10+ 문법을 3.8·3.9 로 다운그레이드, ⑤ 누락 패키지 대체안 제시, ⑥ `MIGRATION.md` 생성
- **제약**: 의미가 바뀌는 치환은 자동 적용하지 않고 반드시 사용자 확인. Cython/C extension 기반 동작은 조정 범위 밖 (재변환 필요).
- **사용 예**: "이 Dockerfile 에 001 을 맞춰줘" · "폐쇄망에서 `AttributeError: module numpy has no attribute int` 떠" · "3.10 문법을 3.9 로 내려줘"

## 변환물 인덱스

| # | 이름 | 원본 | 한 줄 요약 |
|---|---|---|---|
| 001 | [langgraph-notebook-chatbot](001-langgraph-notebook-chatbot/) | langgraph (MIT) | Jupyter 멀티턴 챗봇 + LangSmith 스타일 self-contained HTML 트레이서 |
| 002 | [sentinel-track](002-sentinel-track/) | wandb 개념 참고 | 폐쇄망용 wandb 호환 실험 트래커 (HTML 대시보드 반출) |

각 폴더의 `README.md`에 설치·사용법·제약이 한글로 정리되어 있습니다.

## 새 Skill을 추가하려면

1. **경로**: `.claude/skills/<skill-name>/SKILL.md` (리포 최상단의 `skills/`가 아님에 주의 — 그 위치는 Claude Code가 로드하지 않습니다)
2. `<skill-name>`은 kebab-case 영문
3. 파일 상단에 **YAML frontmatter (`name`, `description`) 필수**
4. `description`은 "무엇을 하는지" + "언제 트리거할지"를 모두 담을 것 — Claude Code가 이 설명을 보고 Skill을 자동으로 불러옵니다
5. 본문은 500줄 미만을 목표. 큰 참조 데이터(매핑 YAML 등)는 `references/` 아래로 분리
6. 이 README의 "사용 가능한 Skills" 표에 한 줄 추가
7. Claude Code 세션에서 `/skills` 로 Skill이 목록에 뜨는지 확인

자세한 프로젝트 규칙은 [`CLAUDE.md`](CLAUDE.md)를 참고하세요.

## 라이선스

- 각 변환물 폴더의 `LICENSE`는 **원본 오픈소스의 라이선스 복제본**입니다. 변환물별로 다를 수 있으니 반입 전 반드시 확인해 주세요.
- 이 리포의 Agent 설정·Skill 문서·README 등은 자유롭게 사내에서 활용할 수 있습니다.
