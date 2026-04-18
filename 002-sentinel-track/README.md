# 002 - sentinel-track

> **한 줄 요약**: 폐쇄망에서도 돌아가는 `wandb` 호환 실험 트래커 + self-contained HTML 대시보드. `import sentinel_track as wandb` 한 줄로 기존 HuggingFace `Trainer(report_to="wandb")` 학습 코드를 그대로 후킹합니다.

## 원본 출처

| 항목 | 값 |
|---|---|
| 참조 라이브러리 | [wandb (Weights & Biases)](https://github.com/wandb/wandb) — 공개 API 표면만 참조 |
| 호환 대상 버전 | `wandb >= 0.16` 의 주요 API (`init / log / finish / config / run / Table / define_metric / watch / Settings`) |
| 라이선스 | MIT (wandb 원본도 MIT, 코드 복제는 아님) |
| 아이디어 참고 | [trackio](https://github.com/gradio-app/trackio) 의 "wandb drop-in" 컨셉 — 코드 복제 아님 |

## 기능 요약

- **wandb drop-in**: 학습 스크립트 최상단에 `import sentinel_track as wandb` 한 줄만 추가하면 끝. 내부적으로 `sys.modules["wandb"]` 를 이 모듈로 치환해 HuggingFace `transformers.integrations.WandbCallback` 이 자동으로 우리 shim 을 사용합니다. **기존 학습 코드 수정 없음** (`report_to="wandb"` 그대로).
- **시스템 메트릭 자동 수집**: 백그라운드 스레드가 CPU (per-core + 프로세스), RAM, GPU (torch.cuda 기반 memory/utilization) 를 2초 간격으로 샘플링.
- **Run 당 JSONL 적재**: `./sentinel_runs/<run_id>/` 밑에 `meta.json / events.jsonl / system.jsonl / tables.jsonl`. **바이너리 포맷 일절 없음** — SQLite/pickle/parquet 쓰지 않음 (폐쇄망 반출 정책 준수).
- **self-contained HTML 대시보드**: `python -m sentinel_track dashboard` 로 모든 run 을 임베드한 HTML 한 장을 생성. 외부 `<script src>` / `<link href>` / `fetch()` 없음, Chart.js 같은 외부 라이브러리도 쓰지 않음 — 순수 인라인 SVG + vanilla JS. **업무망에서 file:// 로 바로 열림.**
- **탭 3종**:
  1. **Runs** — 전체 run 목록, 상태/Duration/Summary top-3, 이름·태그·config 검색, 컬럼별 정렬
  2. **Run 상세** — 선택한 run 의 학습 메트릭 / 시스템 메트릭 / `wandb.Table` 을 한 페이지에
  3. **Sweep** — 여러 run 을 체크박스로 선택 → config × 최종 summary 를 parallel coordinates + 정렬 가능 테이블로 비교
- **`wandb.Table` 호환**: `columns` + `data` 또는 `dataframe=` 전달. 대시보드에서 테이블로 렌더.
- **`WANDB_DISABLED=true` 존중**: 이 환경변수가 켜져 있으면 로깅은 전부 no-op.

## 의존성

| 사용 여부 | 패키지 | 용도 |
|---|---|---|
| 필수 | Python 표준 라이브러리 | JSON, threading, HTML 템플릿 |
| 필수(권장) | `psutil` | CPU / RAM 메트릭. 미설치 시 관련 필드가 빈 값으로 기록됨 |
| 선택 | `torch` | GPU (CUDA) 메트릭. CPU-only 환경이면 자동 skip |
| 선택 | `transformers` | `hf_trainer_demo.py` 를 실제로 돌릴 때만. 본체 import 와는 무관 |

> `numpy`, `pandas` 는 본체 기능에 필요하지 않습니다. `wandb.Table(dataframe=df)` 를 쓸 때만 pandas 가 필요합니다.

## 사용 예시

### (A) drop-in 방식 — HuggingFace Trainer 와 결합

```python
import sentinel_track as wandb                    # ⬅️ transformers import 보다 먼저
from transformers import Trainer, TrainingArguments

args = TrainingArguments(
    output_dir="./out",
    num_train_epochs=3,
    report_to="wandb",                            # ⬅️ 기존 값 그대로
    run_name="my-experiment",
)
Trainer(model, args, train_dataset=ds).train()     # HF WandbCallback 이 우리 shim 을 호출
```

학습이 끝나면 `./sentinel_runs/<run_id>/` 가 쌓이고, 다음 명령으로 대시보드를 뽑습니다:

```bash
python -m sentinel_track dashboard -d ./sentinel_runs -o dashboard.html
# 또는 파이썬 안에서
python -c "import sentinel_track as w; w.build_dashboard('./sentinel_runs', 'dashboard.html')"
```

### (B) 수동 로깅 (torch/HF 불필요)

`examples/basic_usage.py` 가 torch 없이 바로 돌아가는 최소 예제입니다.

```bash
cd 002-sentinel-track
./.venv/bin/python examples/basic_usage.py
open dashboard.html   # macOS
```

핵심 API:

```python
import sentinel_track as wandb

run = wandb.init(project="demo", name="exp-1",
                 config={"lr": 1e-3, "batch_size": 32},
                 tags=["baseline"])
for step in range(100):
    wandb.log({"loss": 0.1, "acc": 0.9}, step=step)

tbl = wandb.Table(columns=["pred", "label"], data=[["A", "A"], ["B", "C"]])
wandb.log({"predictions": tbl}, step=100)

wandb.finish()

wandb.build_dashboard("./sentinel_runs", "dashboard.html")
```

### (C) 내장 데모 (가장 빠른 체험 경로)

```bash
./.venv/bin/python sentinel_track.py demo
open dashboard.html
```

`demo-classifier` 프로젝트로 3개 run 을 모의 학습한 뒤 대시보드를 만들어줍니다. Sweep 탭에서 `lr` / `optimizer` × `final_loss` / `final_acc` 를 한 번에 비교할 수 있습니다.

## 반출 워크플로 (개발 공간 → 업무 공간)

1. 개발 공간에서 학습 수행 → `./sentinel_runs/` 적재
2. `python -m sentinel_track dashboard -d ./sentinel_runs -o trainlog_YYYYMMDD.html`
3. **HTML 파일 한 장만** 업무 공간으로 반입 신청
4. 업무 공간에서 더블클릭 → 브라우저가 file:// 로 열고, 내장 JSON + 인라인 JS 로 전체 대시보드가 바로 렌더

(HTML 안에 JS fetch 가 없으므로 업무망의 파일시스템 접근 차단 정책과 충돌하지 않습니다.)

## 저장 포맷

```
./sentinel_runs/<run_id>/
├── meta.json       # run 메타데이터 (config, 상태, 시각, summary 최종값)
├── events.jsonl    # wandb.log 스칼라 이벤트 — 라인당 {"step","t","data":{...}}
├── system.jsonl    # 시스템 샘플 — 라인당 {"t", "cpu_percent", "gpu":[...], ...}
└── tables.jsonl    # wandb.Table 로그 — 라인당 {"key","step","t","columns","data"}
```

모두 **append-only JSONL**. 쉽게 `git diff` / `jq` / `pandas.read_json(lines=True)` 로 사후 분석 가능.

## 파일 구조

```
002-sentinel-track/
├── README.md                    # 이 문서
├── sentinel_track.py            # single-file 본체 (shim + 모니터 + 대시보드 빌더)
├── metadata.json
├── LICENSE
└── examples/
    ├── basic_usage.py           # torch/HF 없이 바로 돌아가는 최소 예제
    └── hf_trainer_demo.py       # HuggingFace Trainer 와 결합 (transformers 필요)
```

## 알려진 제약 / 한계

- **리치 미디어 미지원**: `wandb.Image`, `wandb.Video`, `wandb.Artifact`, `wandb.Histogram` 은 구현하지 않았습니다. 이미지가 필요하면 별도 PNG 로 저장 후 링크만 Table 에 넣으세요. 단, 폐쇄망 반출 정책상 가급적 이미지는 피하는 편이 좋습니다.
- **분산 학습**: 여러 프로세스(rank) 에서 동시에 `wandb.init` 을 호출하면 각각 별도 run 으로 기록됩니다. rank 0 에서만 로깅하도록 HF `args.report_to` 를 조건부로 세팅하는 일반적 패턴을 그대로 따르세요.
- **Sweep 오케스트레이션 없음**: wandb 의 sweep agent 기능은 제공하지 않습니다. Sweep 탭은 "이미 끝난 여러 run 의 config × summary 비교 뷰" 만 제공합니다. 탐색 실행 자체는 사용자가 루프로 돌려 주세요.
- **GPU 메트릭 범위**: `torch.cuda.memory_*` / `torch.cuda.utilization()` 만 사용합니다 (torch>=2.1 권장). nvidia-smi 파싱 / 전력 / 온도는 미구현.
- **라이브 모니터링 아님**: 대시보드 HTML 은 **스냅샷**입니다. 학습 진행 중 실시간 갱신이 필요하면 학습 끝난 뒤 다시 `build_dashboard` 를 호출하세요 (JSONL 은 append-only 라 중간에 호출해도 안전).
- **wandb API 호환 범위는 "HF Trainer 가 쓰는 것" 중심**: wandb 의 모든 구석까지 채우지 않았습니다. 쓰지 않는 메서드는 대부분 no-op 이며, 프로젝트 요구에 따라 `sentinel_track.py` 에 직접 추가하세요 (single-file 이라 편집이 간단합니다).
- **transformers import 순서 주의**: `import sentinel_track` 을 `from transformers import ...` 보다 **먼저** 하세요. 순서가 뒤바뀌면 transformers 가 기동 시 `find_spec("wandb")` 결과를 캐시해 버려 우리 shim 이 탐지되지 않을 수 있습니다.
