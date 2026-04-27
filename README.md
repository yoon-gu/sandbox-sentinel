# sandbox-sentinel

> **한국 금융사 폐쇄망 환경**용 **오픈소스 → single-file Python 변환물 모음** + 변환을 돕는 **Claude Code Agent 설정·Skill**.

폐쇄망에서는 `pip install` 이 자유롭지 않고 파일 반입마다 보안 심사를 거치므로, **`.py` 한 파일로 완결된 도구**가 강력합니다. 사용자는 원하는 변환물 폴더의 `.py` 만 반입하면 되고, 리포 전체는 필요 없습니다.

## 리포 구조

```
sandbox-sentinel/
├── CLAUDE.md                # 변환 Agent 의 환경 무관 공통 원칙 (워크플로 · 코드 스타일 · 금지사항)
├── .claude/skills/          # Claude Code 가 자동 로드하는 프로젝트 Skill
│   └── environment-adapter/ # 환경별 정책 (허용 패키지 · 라이선스 · 영속화 포맷 · Python 버전)
└── 0NN-<name>/              # 변환물 폴더들 (아래 인덱스 참조)
```

> **역할 분담**: CLAUDE.md = 환경 무관 공통 원칙. `.claude/skills/environment-adapter/` = 타겟 폐쇄망마다 달라지는 구체 리스트(허용 패키지·라이선스·포맷 등). 새 환경은 `stacks/<env-name>.yaml` 하나로 추가.

## 변환물 인덱스

| # | 이름 | 원본 | 한 줄 요약 |
|---|---|---|---|
| 001 | [langgraph-notebook-chatbot](001-langgraph-notebook-chatbot/) | langgraph (MIT) | Jupyter 멀티턴 챗봇 + self-contained HTML 트레이서 |
| 002 | [sentinel-track](002-sentinel-track/) | wandb 개념 참고 | 폐쇄망용 wandb 호환 실험 트래커 (HTML 대시보드 반출) |
| 003 | [langgraph-chat-repl](003-langgraph-chat-repl/) | langgraph + textual (MIT) | 터미널 풀스크린 LangGraph 챗봇 REPL (인라인 HITL · 슬래시 팔레트) |
| 004 | [langgraph-prompt-toolkit-repl](004-langgraph-prompt-toolkit-repl/) | langgraph + prompt_toolkit (MIT · BSD) | 003 의 prompt_toolkit 판 — textual 불필요 |

각 폴더의 `README.md` 에 설치·사용법·제약이 한글로 정리되어 있습니다.

## Skills

Claude Code 는 `<repo-root>/.claude/skills/<name>/SKILL.md` (프로젝트 Skill) 와 `~/.claude/skills/...` (개인 Skill) 만 자동 인식합니다. 이 리포는 **프로젝트 Skill** 방식이므로 clone 후 Claude Code 를 이 디렉토리에서 실행하면 별도 설정 없이 `/skills` 로 확인됩니다.

| Skill | 트리거 · 책임 |
|---|---|
| [**environment-adapter**](.claude/skills/environment-adapter/SKILL.md) | 변환 작업 시작 / 환경 스펙(Dockerfile, requirements.txt, `pip freeze` 등) 제공 시 자동 트리거. 허용·금지 패키지, 라이선스 카테고리, 영속화 포맷, Python 버전 정책을 소유. API 호환 치환 · 문법 다운그레이드 · 누락 패키지 대체안 · `MIGRATION.md` 생성도 담당. 의미가 바뀌는 치환은 사용자 확인 후 적용. |

새 Skill 추가 규칙은 [`CLAUDE.md`](CLAUDE.md) 의 "Skills" 섹션 참고 (요약: `.claude/skills/<kebab-case>/SKILL.md` + YAML frontmatter `name`/`description` 필수, 본문 500줄 미만).

## 라이선스

- 각 변환물 폴더의 `LICENSE` 는 **원본 오픈소스 라이선스 복제본** — 변환물마다 다를 수 있으니 반입 전 확인.
- 이 리포의 Agent 설정·Skill·README 등은 사내에서 자유롭게 활용 가능합니다.
