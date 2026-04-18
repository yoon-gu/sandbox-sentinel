"""
sentinel_track — 폐쇄망 환경을 위한 wandb 호환 실험 트래커 (single-file)

원본 출처(API 표면 참조): Weights & Biases (https://github.com/wandb/wandb, MIT)
※ 코드 복제 아님. init/log/finish 같은 공개 API 이름과 시그니처만 맞추어 자체 구현.
생성: Code Conversion Agent

핵심 사용법
-----------
    import sentinel_track as wandb   # <- 이 한 줄이 sys.modules["wandb"] 를 치환함
    from transformers import Trainer, TrainingArguments

    args = TrainingArguments(..., report_to="wandb")  # 기존 코드 그대로
    Trainer(model, args, ...).train()                 # HF WandbCallback 이 우리 shim 을 사용

    # 학습이 끝난 뒤 대시보드 HTML 로 묶기 (반출용 self-contained)
    #   python -m sentinel_track dashboard -d ./sentinel_runs -o dashboard.html

수동 로깅
---------
    import sentinel_track as wandb
    run = wandb.init(project="demo", name="exp-1", config={"lr": 1e-3})
    for step in range(100):
        wandb.log({"loss": 0.1, "acc": 0.9}, step=step)
    wandb.finish()

데이터는 `./sentinel_runs/<run_id>/` 밑에 JSONL 로 적재됩니다 (바이너리 포맷 없음).
"""

# ===== 1. Imports =====
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import html as _html
import io
import json
import math
import os
import pathlib
import random
import socket
import string
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# psutil 은 CLAUDE.md 허용 목록에 포함 — import 실패는 사내 미러 누락을 의미
try:
    import psutil as _psutil
except ImportError:  # pragma: no cover
    _psutil = None  # 없으면 CPU/RAM 메트릭이 0으로 기록됨

# torch 는 GPU 메트릭용. CPU-only 환경에서도 동작해야 하므로 optional.
try:
    import torch as _torch
except ImportError:  # pragma: no cover
    _torch = None


# ===== 2. 상수 · 경로 · 유틸 =====

DEFAULT_RUN_DIR = "./sentinel_runs"
DEFAULT_SYSTEM_INTERVAL_SEC = 2.0  # 시스템 메트릭 샘플링 주기
SCHEMA_VERSION = 1


def _now_iso() -> str:
    """현재 시각을 ISO8601 (UTC 제거, 로컬) 로 반환."""
    return _dt.datetime.now().isoformat(timespec="seconds")


def _now_unix() -> float:
    return time.time()


def _gen_run_id() -> str:
    """wandb 느낌의 짧은 run id (8자 base36-ish)."""
    alphabet = string.ascii_lowercase + string.digits
    rnd = uuid.uuid4().int
    chars = []
    while rnd > 0 and len(chars) < 8:
        rnd, i = divmod(rnd, len(alphabet))
        chars.append(alphabet[i])
    return "".join(chars) or "run"


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """쓰기 도중 interrupt 되더라도 부분 파일이 남지 않도록 임시파일 경유."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _json_safe(obj: Any) -> Any:
    """JSON 직렬화 불가 타입(numpy 스칼라, torch 텐서 등)을 파이썬 기본 타입으로 변환."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    # numpy / torch 스칼라는 대부분 .item() 을 가짐
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    if hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except Exception:
            pass
    return repr(obj)


# ===== 3. 시스템 메트릭 수집기 =====

class _SystemMonitor:
    """
    백그라운드 스레드로 CPU·RAM·GPU 메트릭을 주기 샘플링해 system.jsonl 에 적재.

    - CPU: psutil 프로세스 CPU %, per-core 시스템 CPU %, RAM 사용량/%
    - GPU: torch.cuda 가 있으면 device 별 memory_allocated / utilization
    - 아무것도 없으면 조용히 빈 샘플만 남김 (예외로 학습 중단 금지)
    """

    def __init__(self, events_path: pathlib.Path, interval_sec: float):
        self._events_path = events_path
        self._interval = max(0.2, float(interval_sec))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process = _psutil.Process(os.getpid()) if _psutil is not None else None
        # 첫 호출은 0을 반환하므로 초기 warm-up
        if self._process is not None:
            try:
                self._process.cpu_percent(interval=None)
                _psutil.cpu_percent(interval=None, percpu=True)
            except Exception:
                pass

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="sentinel-track-sysmon", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _collect(self) -> Dict[str, Any]:
        sample: Dict[str, Any] = {"t": _now_unix()}
        # --- CPU / RAM ---
        if self._process is not None:
            try:
                sample["proc_cpu_percent"] = float(self._process.cpu_percent(interval=None))
                mem = self._process.memory_info()
                sample["proc_ram_mb"] = round(mem.rss / (1024 * 1024), 2)
            except Exception:
                pass
            try:
                per_core = _psutil.cpu_percent(interval=None, percpu=True)
                sample["cpu_per_core"] = [round(float(x), 1) for x in per_core]
                sample["cpu_percent"] = round(sum(per_core) / max(len(per_core), 1), 1)
            except Exception:
                pass
            try:
                vmem = _psutil.virtual_memory()
                sample["ram_percent"] = float(vmem.percent)
                sample["ram_used_mb"] = round(vmem.used / (1024 * 1024), 2)
                sample["ram_total_mb"] = round(vmem.total / (1024 * 1024), 2)
            except Exception:
                pass
        # --- GPU ---
        gpu_list: List[Dict[str, Any]] = []
        if _torch is not None and hasattr(_torch, "cuda") and _torch.cuda.is_available():
            try:
                for i in range(_torch.cuda.device_count()):
                    g: Dict[str, Any] = {"idx": i}
                    try:
                        g["name"] = _torch.cuda.get_device_name(i)
                    except Exception:
                        g["name"] = f"cuda:{i}"
                    try:
                        g["mem_alloc_mb"] = round(
                            _torch.cuda.memory_allocated(i) / (1024 * 1024), 2
                        )
                        g["mem_reserved_mb"] = round(
                            _torch.cuda.memory_reserved(i) / (1024 * 1024), 2
                        )
                    except Exception:
                        pass
                    # utilization 은 최신 torch(>=2.1)에서만 제공
                    util_fn = getattr(_torch.cuda, "utilization", None)
                    if callable(util_fn):
                        try:
                            g["util_percent"] = float(util_fn(i))
                        except Exception:
                            pass
                    gpu_list.append(g)
            except Exception:
                pass
        if gpu_list:
            sample["gpu"] = gpu_list
        return sample

    def _run(self) -> None:
        with self._events_path.open("a", encoding="utf-8") as f:
            while not self._stop.is_set():
                try:
                    sample = self._collect()
                    f.write(json.dumps(_json_safe(sample), ensure_ascii=False) + "\n")
                    f.flush()
                except Exception:
                    # 학습을 방해하지 않기 위해 조용히 무시
                    pass
                # 정지 신호를 빠르게 반영하기 위해 interval 을 작게 쪼갬
                self._stop.wait(self._interval)


# ===== 4. Config · Table · Run =====

class Config(dict):
    """
    wandb.config 호환 dict. 점 접근도 허용 (`config.lr`).
    `update(d, allow_val_change=True)` 시그니처까지 받아줌.
    """

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def update(self, other: Any = None, allow_val_change: bool = False, **kwargs: Any) -> None:  # type: ignore[override]
        if other is not None:
            if hasattr(other, "items"):
                for k, v in other.items():
                    self[k] = v
            else:
                for k, v in other:
                    self[k] = v
        for k, v in kwargs.items():
            self[k] = v


class Table:
    """
    wandb.Table 호환. 컬럼명 + 2D 데이터. 대시보드에서 HTML 테이블로 렌더됨.

    사용 예:
        tbl = wandb.Table(columns=["pred", "label"], data=[["a", "a"], ["b", "c"]])
        wandb.log({"predictions": tbl}, step=10)
    """

    def __init__(
        self,
        columns: Optional[Sequence[str]] = None,
        data: Optional[Sequence[Sequence[Any]]] = None,
        dataframe: Any = None,
    ):
        if dataframe is not None:
            # pandas DataFrame 지원 (optional)
            self.columns = [str(c) for c in list(dataframe.columns)]
            self.data = [list(row) for row in dataframe.itertuples(index=False, name=None)]
        else:
            self.columns = [str(c) for c in (columns or [])]
            self.data = [list(row) for row in (data or [])]

    def add_data(self, *row: Any) -> None:
        self.data.append(list(row))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "columns": list(self.columns),
            "data": [_json_safe(list(row)) for row in self.data],
        }


class Summary(dict):
    """wandb.run.summary 호환 — 단순 dict 로 충분."""
    pass


class Run:
    """
    하나의 학습 실행 단위. init() 이 이 객체를 반환하고, 모듈 레벨 `run` 에 바인딩.

    디스크 레이아웃:
        <dir>/<run_id>/
            meta.json       — run 메타데이터 (config, 상태, 시각 등)
            events.jsonl    — wandb.log 이벤트. 라인당 {"step","t","data"}
            system.jsonl    — 시스템 메트릭 샘플
            tables.jsonl    — Table 로그 (Table 값은 크므로 별도 스트림)
    """

    def __init__(
        self,
        *,
        project: str,
        name: str,
        run_id: str,
        root_dir: pathlib.Path,
        config: Dict[str, Any],
        tags: Sequence[str],
        notes: str,
        group: Optional[str],
        job_type: Optional[str],
        system_interval_sec: float,
    ):
        self.project = project
        self.name = name
        self.id = run_id
        self.tags = list(tags)
        self.notes = notes
        self.group = group
        self.job_type = job_type
        self.config = Config(config or {})
        self.summary = Summary()

        self._dir = root_dir / run_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self._dir / "events.jsonl"
        self._system_path = self._dir / "system.jsonl"
        self._tables_path = self._dir / "tables.jsonl"
        self._meta_path = self._dir / "meta.json"

        self._events_file = self._events_path.open("a", encoding="utf-8")
        self._tables_file = self._tables_path.open("a", encoding="utf-8")
        self._step = 0
        self._finished = False
        self._created_at_unix = _now_unix()
        self._lock = threading.Lock()

        self._monitor = _SystemMonitor(self._system_path, system_interval_sec)
        self._monitor.start()

        self._flush_meta(status="running", finished_at=None)

    # ---- 공개 API ----

    def log(self, data: Dict[str, Any], step: Optional[int] = None, commit: bool = True) -> None:
        if self._finished:
            return
        if not isinstance(data, dict):
            raise TypeError("wandb.log 에는 dict 를 전달해야 합니다")
        if step is None:
            step = self._step
        else:
            self._step = max(self._step, int(step))
        ts = _now_unix()
        scalar: Dict[str, Any] = {}
        with self._lock:
            for key, value in data.items():
                if isinstance(value, Table):
                    # Table 은 별도 스트림으로 (용량이 커질 수 있음)
                    payload = {
                        "key": str(key),
                        "step": int(step),
                        "t": ts,
                        **value.to_dict(),
                    }
                    self._tables_file.write(
                        json.dumps(_json_safe(payload), ensure_ascii=False) + "\n"
                    )
                else:
                    scalar[str(key)] = _json_safe(value)
                    # summary 갱신 — wandb 와 동일하게 "최신 값" 을 유지
                    self.summary[str(key)] = scalar[str(key)]
            if scalar:
                line = {"step": int(step), "t": ts, "data": scalar}
                self._events_file.write(json.dumps(line, ensure_ascii=False) + "\n")
            if commit:
                self._events_file.flush()
                self._tables_file.flush()
                self._step += 1

    def finish(self, exit_code: int = 0, quiet: bool = False) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._monitor.stop()
        except Exception:
            pass
        try:
            self._events_file.flush()
            self._events_file.close()
            self._tables_file.flush()
            self._tables_file.close()
        except Exception:
            pass
        status = "finished" if exit_code == 0 else "crashed"
        self._flush_meta(status=status, finished_at=_now_iso(), exit_code=exit_code)
        if not quiet:
            duration = _now_unix() - self._created_at_unix
            print(
                f"[sentinel-track] run {self.id} ({self.name}) "
                f"종료 · status={status} · {duration:,.1f}s · 디렉토리={self._dir}"
            )

    # ---- 내부 ----

    def _flush_meta(
        self,
        *,
        status: str,
        finished_at: Optional[str],
        exit_code: Optional[int] = None,
    ) -> None:
        meta = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.id,
            "name": self.name,
            "project": self.project,
            "tags": list(self.tags),
            "notes": self.notes,
            "group": self.group,
            "job_type": self.job_type,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "created_at": _dt.datetime.fromtimestamp(self._created_at_unix).isoformat(
                timespec="seconds"
            ),
            "finished_at": finished_at,
            "status": status,
            "exit_code": exit_code,
            "config": _json_safe(dict(self.config)),
            "summary": _json_safe(dict(self.summary)),
        }
        _atomic_write_text(self._meta_path, json.dumps(meta, ensure_ascii=False, indent=2))


# ===== 5. wandb 호환 모듈 레벨 API =====

# 현재 활성 run (HF WandbCallback 이 wandb.run 으로 참조)
run: Optional[Run] = None
# 모듈 레벨 config — run 이 시작되기 전이라도 참조할 수 있도록 임시 버퍼
config: Config = Config()


def init(
    project: Optional[str] = None,
    name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,  # noqa: A002  (wandb 호환을 위해 이름 유지)
    dir: Optional[str] = None,  # noqa: A002
    id: Optional[str] = None,  # noqa: A002
    tags: Optional[Sequence[str]] = None,
    notes: Optional[str] = None,
    group: Optional[str] = None,
    job_type: Optional[str] = None,
    resume: Any = None,
    reinit: bool = False,
    mode: Optional[str] = None,
    settings: Any = None,
    entity: Optional[str] = None,  # 사내에서는 의미 없음 — 무시
    anonymous: Any = None,
    **_ignored: Any,
) -> Run:
    """
    새 run 을 시작한다. wandb.init 시그니처의 핵심만 호환.

    환경변수 WANDB_PROJECT / WANDB_NAME / WANDB_RUN_ID / WANDB_DIR / WANDB_DISABLED
    를 wandb 와 동일하게 존중한다.
    """
    global run  # noqa: PLW0603

    # WANDB_DISABLED=true 이면 no-op run 반환 (로깅은 silently drop)
    if (mode and mode.lower() == "disabled") or os.environ.get("WANDB_DISABLED", "").lower() in (
        "true",
        "1",
    ):
        return _DisabledRun()

    if run is not None and not reinit:
        # 기존 run 이 살아있으면 그대로 반환 (wandb 동작과 동일)
        return run
    if run is not None and reinit:
        try:
            run.finish()
        except Exception:
            pass

    project = project or os.environ.get("WANDB_PROJECT") or "uncategorized"
    name = name or os.environ.get("WANDB_NAME") or _gen_default_name()
    run_id = id or os.environ.get("WANDB_RUN_ID") or _gen_run_id()
    root_dir = pathlib.Path(dir or os.environ.get("WANDB_DIR") or DEFAULT_RUN_DIR).resolve()
    root_dir.mkdir(parents=True, exist_ok=True)

    interval = float(os.environ.get("SENTINEL_SYSTEM_INTERVAL", DEFAULT_SYSTEM_INTERVAL_SEC))

    new_run = Run(
        project=project,
        name=name,
        run_id=run_id,
        root_dir=root_dir,
        config=dict(config or {}),
        tags=list(tags or []),
        notes=notes or "",
        group=group,
        job_type=job_type,
        system_interval_sec=interval,
    )
    run = new_run
    # 모듈 레벨 config 는 run.config 를 "바라보도록" 참조 공유
    globals()["config"] = new_run.config
    print(
        f"[sentinel-track] run 시작 · id={new_run.id} name={new_run.name} "
        f"project={project} dir={new_run._dir}"
    )
    return new_run


def _gen_default_name() -> str:
    adjectives = ["silent", "bright", "calm", "brave", "swift", "gentle", "bold", "quiet"]
    nouns = ["otter", "falcon", "river", "mountain", "willow", "harbor", "lynx", "pine"]
    return f"{random.choice(adjectives)}-{random.choice(nouns)}-{random.randint(1, 999)}"


def log(data: Dict[str, Any], step: Optional[int] = None, commit: bool = True) -> None:
    """현재 run 에 메트릭을 기록. run 이 없으면 암묵 init."""
    global run
    if run is None:
        init()
    assert run is not None
    run.log(data, step=step, commit=commit)


def finish(exit_code: int = 0, quiet: bool = False) -> None:
    global run
    if run is None:
        return
    run.finish(exit_code=exit_code, quiet=quiet)
    run = None


def watch(*_args: Any, **_kwargs: Any) -> None:
    """torch 모델 gradient 추적. 본 구현에서는 no-op (HF 호출 호환용)."""
    return None


def define_metric(*_args: Any, **_kwargs: Any) -> None:
    return None


def termlog(msg: str, *_a: Any, **_k: Any) -> None:
    print(f"[sentinel-track] {msg}")


def termwarn(msg: str, *_a: Any, **_k: Any) -> None:
    print(f"[sentinel-track] WARN: {msg}")


def termerror(msg: str, *_a: Any, **_k: Any) -> None:
    print(f"[sentinel-track] ERROR: {msg}", file=sys.stderr)


def save(*_args: Any, **_kwargs: Any) -> None:
    return None


def login(*_args: Any, **_kwargs: Any) -> bool:
    # 사내 환경에서는 로그인 개념 없음 — 호환용 true 반환
    return True


class Settings:
    """wandb.Settings(...) 더미. 생성자 인자만 받고 아무것도 하지 않음."""

    def __init__(self, **kwargs: Any):
        self._kwargs = kwargs

    def __getattr__(self, item: str) -> Any:
        return self._kwargs.get(item)


class _DisabledRun:
    """WANDB_DISABLED 모드에서 반환되는 no-op run. 모든 메서드 / 속성 흡수."""

    id = "disabled"
    name = "disabled"
    config = Config()
    summary = Summary()

    def log(self, *_a: Any, **_k: Any) -> None:
        return None

    def finish(self, *_a: Any, **_k: Any) -> None:
        return None

    def __getattr__(self, _item: str) -> Any:
        return lambda *a, **k: None


# wandb.errors.UsageError 같은 예외 접근을 받아줌
class _ErrorsModule:
    class Error(Exception):
        pass

    class UsageError(Exception):
        pass

    class CommError(Exception):
        pass


errors = _ErrorsModule()

# wandb.sdk 를 참조하는 코드 대비 (극히 제한적 호환)
class _SDKModule:
    class lib:  # noqa: N801
        class disabled:  # noqa: N801
            class RunDisabled:
                pass


sdk = _SDKModule()


# ===== 6. sys.modules 치환 — 이 모듈이 "wandb" 로도 import 되도록 =====

def _install_as_wandb() -> None:
    """
    import sentinel_track 만으로 transformers 의 `import wandb` / `find_spec("wandb")` 가
    이 모듈을 찾도록 sys.modules 에 등록한다. 이미 "wandb" 가 로드돼 있어도 덮어쓴다
    (사용자가 명시적으로 import 한 의도이므로).
    """
    import importlib.machinery  # 지연 import

    this_mod = sys.modules[__name__]
    # find_spec("wandb") 가 None 을 반환하지 않도록 합성 spec 을 부여
    try:
        spec = importlib.machinery.ModuleSpec(
            name="wandb", loader=None, origin=getattr(this_mod, "__file__", None)
        )
        # submodule_search_locations 를 설정해두면 "wandb.errors" 같은 하위 경로 접근 시도에
        # 대비하여 최소한 spec 탐색이 가능함 (실 import 는 여전히 속성 경유가 먼저).
        this_mod.__spec__ = spec  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.modules["wandb"] = this_mod
    # 하위 속성 접근 (wandb.errors 등) 은 이미 모듈 속성으로 존재


_install_as_wandb()


# ===== 7. 대시보드 HTML 빌더 =====

def _read_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _load_run(run_dir: pathlib.Path) -> Optional[Dict[str, Any]]:
    """한 run 디렉토리를 읽어 대시보드 JSON 구조로 변환."""
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    events = _read_jsonl(run_dir / "events.jsonl")
    system = _read_jsonl(run_dir / "system.jsonl")
    tables = _read_jsonl(run_dir / "tables.jsonl")

    # 메트릭별로 (step, value) 로 정리
    metric_series: Dict[str, List[Tuple[int, float, float]]] = {}
    for ev in events:
        step = int(ev.get("step", 0))
        t = float(ev.get("t", 0.0))
        for k, v in (ev.get("data") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                metric_series.setdefault(k, []).append((step, t, float(v)))
    # step 기준 정렬
    for k in metric_series:
        metric_series[k].sort(key=lambda x: x[0])

    # system series 는 시간 기준
    sys_series: Dict[str, List[Tuple[float, float]]] = {}
    for s in system:
        t = float(s.get("t", 0.0))
        if "cpu_percent" in s:
            sys_series.setdefault("cpu_percent", []).append((t, float(s["cpu_percent"])))
        if "ram_percent" in s:
            sys_series.setdefault("ram_percent", []).append((t, float(s["ram_percent"])))
        if "proc_ram_mb" in s:
            sys_series.setdefault("proc_ram_mb", []).append((t, float(s["proc_ram_mb"])))
        for g in s.get("gpu") or []:
            idx = g.get("idx", 0)
            if "mem_alloc_mb" in g:
                sys_series.setdefault(f"gpu{idx}_mem_alloc_mb", []).append(
                    (t, float(g["mem_alloc_mb"]))
                )
            if "util_percent" in g:
                sys_series.setdefault(f"gpu{idx}_util_percent", []).append(
                    (t, float(g["util_percent"]))
                )

    return {
        "meta": meta,
        "metrics": {k: [[s, v] for (s, _t, v) in vs] for k, vs in metric_series.items()},
        "system": {
            k: [[round(t - (vs[0][0] if vs else 0), 2), v] for (t, v) in vs]
            for k, vs in sys_series.items()
        },
        "tables": tables,
    }


def _scan_runs(run_dir: pathlib.Path) -> List[Dict[str, Any]]:
    if not run_dir.exists():
        return []
    runs = []
    for child in sorted(run_dir.iterdir()):
        if not child.is_dir():
            continue
        data = _load_run(child)
        if data is not None:
            runs.append(data)
    # 최신순 (created_at 문자열 비교 — ISO 포맷이라 OK)
    runs.sort(key=lambda r: r["meta"].get("created_at", ""), reverse=True)
    return runs


def build_dashboard(
    run_dir: Union[str, pathlib.Path] = DEFAULT_RUN_DIR,
    output: Union[str, pathlib.Path] = "dashboard.html",
    title: str = "sentinel-track dashboard",
) -> pathlib.Path:
    """
    `run_dir` 밑의 모든 run 을 self-contained HTML 하나로 묶는다.
    외부 `<script src>` / `<link href>` / `fetch` 없음 — 업무망 반출 후에도 바로 열림.
    """
    run_dir = pathlib.Path(run_dir)
    output = pathlib.Path(output)
    runs = _scan_runs(run_dir)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "run_root": str(run_dir.resolve()),
        "runs": runs,
    }
    html = _render_dashboard_html(title=title, payload=payload)
    output.write_text(html, encoding="utf-8")
    print(
        f"[sentinel-track] 대시보드 생성 · {len(runs)}개 run · "
        f"{output.resolve()} ({len(html) / 1024:,.1f} KB)"
    )
    return output


def _render_dashboard_html(title: str, payload: Dict[str, Any]) -> str:
    """
    데이터 + 인라인 CSS + 자체 작성 JS(순수 JS, 외부 라이브러리 없음) + SVG 렌더러.

    JS 에서는 다음 탭을 지원:
      1) Runs  — run 목록 · 상태 · 지표 요약 · 검색/필터
      2) Run 상세 — 선택한 run 의 메트릭/시스템/Table
      3) Sweep — config × 최종 메트릭 parallel coordinates + 정렬 가능 테이블
    """
    data_json = json.dumps(payload, ensure_ascii=False)
    # HTML 내부에 안전하게 넣기 위해 </script> 를 escape
    data_json_safe = data_json.replace("</", "<\\/")
    title_safe = _html.escape(title)
    # CSS / JS 는 아래 상수에서 가져옴
    return _HTML_TEMPLATE.format(
        title=title_safe,
        data_json=data_json_safe,
        css=_DASHBOARD_CSS,
        js=_DASHBOARD_JS,
    )


_DASHBOARD_CSS = r"""
:root {
  --bg: #0f172a;
  --panel: #111827;
  --panel-2: #1f2937;
  --text: #e5e7eb;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --accent-2: #f472b6;
  --ok: #34d399;
  --warn: #fbbf24;
  --err: #f87171;
  --grid: #334155;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue",
               "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
}
header {
  padding: 14px 20px;
  border-bottom: 1px solid var(--grid);
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
header h1 { font-size: 16px; margin: 0; letter-spacing: .2px; }
header .meta { color: var(--muted); font-size: 12px; }
nav.tabs { display: flex; gap: 4px; margin-left: auto; }
nav.tabs button {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--grid);
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font: inherit;
}
nav.tabs button.active { color: var(--text); border-color: var(--accent); background: rgba(56,189,248,.08); }
main { padding: 16px 20px; }

.card {
  background: var(--panel);
  border: 1px solid var(--grid);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 16px;
}
.card h2 { margin: 0 0 10px 0; font-size: 14px; color: var(--muted); font-weight: 600; }

table.runs { width: 100%; border-collapse: collapse; font-size: 13px; }
table.runs th, table.runs td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--grid);
}
table.runs th { color: var(--muted); font-weight: 500; cursor: pointer; user-select: none; }
table.runs th.sort-asc::after { content: " ▲"; color: var(--accent); }
table.runs th.sort-desc::after { content: " ▼"; color: var(--accent); }
table.runs tr:hover { background: rgba(148, 163, 184, .08); cursor: pointer; }
table.runs tr.selected { background: rgba(56,189,248,.12); }
td.status-finished { color: var(--ok); }
td.status-running  { color: var(--warn); }
td.status-crashed  { color: var(--err); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }

input.search {
  background: var(--panel-2);
  border: 1px solid var(--grid);
  color: var(--text);
  border-radius: 6px;
  padding: 6px 10px;
  width: 240px;
  font: inherit;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 12px;
}
.chart {
  background: var(--panel-2);
  border-radius: 6px;
  padding: 10px;
}
.chart h3 {
  font-size: 12px;
  color: var(--muted);
  margin: 0 0 6px 0;
  font-weight: 500;
  letter-spacing: .3px;
}
svg { display: block; width: 100%; height: auto; }
.legend { font-size: 11px; color: var(--muted); margin-top: 4px; display:flex; flex-wrap:wrap; gap:8px; }
.legend .dot { display:inline-block; width: 8px; height: 8px; border-radius: 50%; vertical-align: middle; margin-right: 4px; }

.kv { display: grid; grid-template-columns: 140px 1fr; gap: 4px 12px; font-size: 13px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; font-variant-numeric: tabular-nums; word-break: break-word; }

.runs-checkboxes { display:flex; flex-wrap: wrap; gap:6px; margin-top: 8px; }
.runs-checkboxes label {
  background: var(--panel-2);
  border: 1px solid var(--grid);
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}
.runs-checkboxes label input { margin-right: 6px; }

table.sweep { width: 100%; border-collapse: collapse; font-size: 12px; }
table.sweep th, table.sweep td { padding: 6px 8px; border-bottom: 1px solid var(--grid); }
table.sweep th { color: var(--muted); font-weight: 500; cursor: pointer; }

.tag {
  display: inline-block;
  background: var(--panel-2);
  color: var(--muted);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  margin-right: 4px;
}
.empty { color: var(--muted); font-style: italic; padding: 24px; text-align: center; }
"""


# JS 는 순수 vanilla — 외부 라이브러리 없음.
# 레이아웃: data 를 #data 스크립트 태그에서 파싱 → 탭 3종을 렌더.
_DASHBOARD_JS = r"""
(function () {
  const el = (t, a, c) => {
    const e = document.createElement(t);
    if (a) for (const k in a) {
      if (k === "class") e.className = a[k];
      else if (k === "html") e.innerHTML = a[k];
      else if (k === "text") e.textContent = a[k];
      else e.setAttribute(k, a[k]);
    }
    if (c) (Array.isArray(c) ? c : [c]).forEach(x => x != null && e.appendChild(typeof x === "string" ? document.createTextNode(x) : x));
    return e;
  };
  const fmt = (v) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
      if (!isFinite(v)) return String(v);
      if (Math.abs(v) >= 1000 || (v !== 0 && Math.abs(v) < 0.01)) return v.toExponential(3);
      return v.toFixed(4).replace(/\.?0+$/, "");
    }
    return String(v);
  };
  const fmtTime = (iso) => iso ? iso.replace("T", " ") : "—";

  // ---------- 데이터 로드 ----------
  const raw = document.getElementById("data").textContent;
  const payload = JSON.parse(raw);
  const runs = payload.runs || [];

  // run_id -> 색상 (HSL 균등분포)
  const RUN_COLORS = {};
  runs.forEach((r, i) => {
    const h = Math.round((i * 360) / Math.max(runs.length, 1));
    RUN_COLORS[r.meta.run_id] = `hsl(${h}, 70%, 60%)`;
  });

  // ---------- 상태 ----------
  const state = {
    tab: "runs",
    search: "",
    selectedRunId: runs[0]?.meta?.run_id || null,
    compareSet: new Set(runs.slice(0, Math.min(4, runs.length)).map(r => r.meta.run_id)),
    sortBy: "created_at",
    sortDir: -1
  };

  // ---------- 공통: 라인차트 (SVG) ----------
  // series: [{name, color, points: [[x, y], ...]}]
  function lineChart(container, series, opts) {
    opts = opts || {};
    const W = opts.width || 360, H = opts.height || 180;
    const PAD_L = 40, PAD_R = 10, PAD_T = 8, PAD_B = 24;
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");

    let allX = [], allY = [];
    series.forEach(s => s.points.forEach(p => { allX.push(p[0]); allY.push(p[1]); }));
    if (allX.length === 0) {
      container.appendChild(el("div", {class: "empty", text: "데이터 없음"}));
      return;
    }
    const xMin = Math.min(...allX), xMax = Math.max(...allX);
    let yMin = Math.min(...allY), yMax = Math.max(...allY);
    if (yMin === yMax) { yMin -= 1; yMax += 1; }
    const xRange = xMax - xMin || 1;
    const yRange = yMax - yMin || 1;
    const sx = x => PAD_L + ((x - xMin) / xRange) * (W - PAD_L - PAD_R);
    const sy = y => H - PAD_B - ((y - yMin) / yRange) * (H - PAD_T - PAD_B);

    // y 축 그리드 5 라인
    for (let i = 0; i <= 4; i++) {
      const yv = yMin + (yRange * i) / 4;
      const yp = sy(yv);
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", PAD_L); line.setAttribute("x2", W - PAD_R);
      line.setAttribute("y1", yp); line.setAttribute("y2", yp);
      line.setAttribute("stroke", "#334155");
      line.setAttribute("stroke-width", "0.5");
      svg.appendChild(line);
      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", PAD_L - 4); label.setAttribute("y", yp + 3);
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "10"); label.setAttribute("fill", "#94a3b8");
      label.textContent = fmt(yv);
      svg.appendChild(label);
    }
    // x 축 시작/끝 라벨
    const xlabL = document.createElementNS(svgNS, "text");
    xlabL.setAttribute("x", PAD_L); xlabL.setAttribute("y", H - 6);
    xlabL.setAttribute("font-size", "10"); xlabL.setAttribute("fill", "#94a3b8");
    xlabL.textContent = fmt(xMin);
    svg.appendChild(xlabL);
    const xlabR = document.createElementNS(svgNS, "text");
    xlabR.setAttribute("x", W - PAD_R); xlabR.setAttribute("y", H - 6);
    xlabR.setAttribute("text-anchor", "end");
    xlabR.setAttribute("font-size", "10"); xlabR.setAttribute("fill", "#94a3b8");
    xlabR.textContent = fmt(xMax);
    svg.appendChild(xlabR);

    // series polyline
    series.forEach(s => {
      if (s.points.length === 0) return;
      const pts = s.points.map(p => `${sx(p[0])},${sy(p[1])}`).join(" ");
      const poly = document.createElementNS(svgNS, "polyline");
      poly.setAttribute("points", pts);
      poly.setAttribute("fill", "none");
      poly.setAttribute("stroke", s.color);
      poly.setAttribute("stroke-width", "1.5");
      poly.setAttribute("stroke-linejoin", "round");
      svg.appendChild(poly);
    });
    container.appendChild(svg);
    if (series.length > 1) {
      const lg = el("div", {class: "legend"});
      series.forEach(s => {
        const item = el("span", {}, [
          el("span", {class: "dot", style: `background:${s.color}`}),
          s.name
        ]);
        item.style.marginRight = "10px";
        lg.appendChild(item);
      });
      container.appendChild(lg);
    }
  }

  // ---------- Runs 탭 ----------
  function renderRuns(root) {
    const header = el("div", {class: "card"});
    header.appendChild(el("h2", {text: `Runs (${runs.length})`}));
    const searchBox = el("input", {class: "search", placeholder: "검색: 이름 / 태그 / config 값"});
    searchBox.value = state.search;
    searchBox.oninput = () => { state.search = searchBox.value; renderTable(); };
    header.appendChild(searchBox);
    header.appendChild(el("span", {
      class: "meta",
      text: `  · 생성: ${fmtTime(payload.generated_at)} · 경로: ${payload.run_root}`
    }));
    root.appendChild(header);

    const tableCard = el("div", {class: "card"});
    root.appendChild(tableCard);

    function matches(r) {
      const q = state.search.trim().toLowerCase();
      if (!q) return true;
      const m = r.meta;
      const hay = [m.name, m.run_id, m.project, (m.tags||[]).join(","), JSON.stringify(m.config||{})]
        .join(" ").toLowerCase();
      return hay.indexOf(q) >= 0;
    }

    function sortedRows() {
      const rows = runs.filter(matches).map(r => {
        const m = r.meta;
        const summary = m.summary || {};
        const start = m.created_at;
        const end = m.finished_at;
        let duration = "—";
        if (start && end) {
          const s = Date.parse(start), e = Date.parse(end);
          if (!isNaN(s) && !isNaN(e)) duration = Math.round((e - s) / 1000) + "s";
        }
        return {
          run_id: m.run_id, name: m.name, project: m.project, status: m.status,
          created_at: m.created_at, finished_at: m.finished_at || "",
          duration, tags: (m.tags || []).join(","),
          summary_str: Object.entries(summary).slice(0,3)
             .map(([k,v]) => `${k}=${fmt(v)}`).join(", "),
          _run: r
        };
      });
      const key = state.sortBy, dir = state.sortDir;
      rows.sort((a, b) => {
        const va = a[key], vb = b[key];
        if (va === vb) return 0;
        return (va > vb ? 1 : -1) * dir;
      });
      return rows;
    }

    function renderTable() {
      tableCard.innerHTML = "";
      const rows = sortedRows();
      if (rows.length === 0) {
        tableCard.appendChild(el("div", {class: "empty", text: "표시할 run 없음"}));
        return;
      }
      const cols = [
        {k: "name", label: "Name"},
        {k: "run_id", label: "ID"},
        {k: "project", label: "Project"},
        {k: "status", label: "Status"},
        {k: "created_at", label: "Started"},
        {k: "duration", label: "Duration"},
        {k: "summary_str", label: "Summary (top 3)"},
        {k: "tags", label: "Tags"}
      ];
      const table = el("table", {class: "runs"});
      const thead = el("thead");
      const trh = el("tr");
      cols.forEach(c => {
        const th = el("th", {text: c.label});
        if (state.sortBy === c.k) th.className = state.sortDir > 0 ? "sort-asc" : "sort-desc";
        th.onclick = () => {
          if (state.sortBy === c.k) state.sortDir *= -1;
          else { state.sortBy = c.k; state.sortDir = 1; }
          renderTable();
        };
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      table.appendChild(thead);
      const tbody = el("tbody");
      rows.forEach(row => {
        const tr = el("tr");
        if (row.run_id === state.selectedRunId) tr.className = "selected";
        tr.onclick = () => {
          state.selectedRunId = row.run_id;
          state.tab = "detail";
          render();
        };
        cols.forEach(c => {
          const td = el("td", {text: row[c.k] == null ? "" : String(row[c.k])});
          if (c.k === "status") td.className = "status-" + row.status;
          if (c.k === "duration") td.className = "num";
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      tableCard.appendChild(table);
    }

    renderTable();
  }

  // ---------- Run 상세 탭 ----------
  function renderDetail(root) {
    if (!state.selectedRunId) {
      root.appendChild(el("div", {class: "empty", text: "Runs 탭에서 run 을 선택하세요."}));
      return;
    }
    const r = runs.find(x => x.meta.run_id === state.selectedRunId);
    if (!r) {
      root.appendChild(el("div", {class: "empty", text: "선택된 run 을 찾을 수 없습니다."}));
      return;
    }
    const m = r.meta;

    // --- 헤더 ---
    const head = el("div", {class: "card"});
    head.appendChild(el("h2", {text: `${m.name}  ·  ${m.run_id}`}));
    const kv = el("dl", {class: "kv"});
    const kvRows = [
      ["Project", m.project],
      ["Status", m.status],
      ["Created", fmtTime(m.created_at)],
      ["Finished", fmtTime(m.finished_at)],
      ["Host", m.host],
      ["PID", m.pid],
      ["Python", m.python],
      ["Tags", (m.tags || []).map(t => `<span class='tag'>${t}</span>`).join(" ") || "—"],
      ["Notes", m.notes || "—"],
    ];
    kvRows.forEach(([k, v]) => {
      kv.appendChild(el("dt", {text: k}));
      const dd = el("dd", {html: String(v)});
      kv.appendChild(dd);
    });
    head.appendChild(kv);
    root.appendChild(head);

    // --- Config / Summary ---
    const cs = el("div", {class: "card"});
    cs.appendChild(el("h2", {text: "Config / Summary"}));
    const grid = el("div", {style: "display:grid; grid-template-columns: 1fr 1fr; gap: 16px;"});
    function kvDl(title, obj) {
      const wrap = el("div");
      wrap.appendChild(el("div", {text: title, style: "color:#94a3b8;font-size:12px;margin-bottom:4px;"}));
      const dl = el("dl", {class: "kv"});
      const entries = Object.entries(obj || {});
      if (entries.length === 0) dl.appendChild(el("div", {class: "empty", text: "비어있음"}));
      entries.forEach(([k, v]) => {
        dl.appendChild(el("dt", {text: k}));
        dl.appendChild(el("dd", {text: fmt(v)}));
      });
      wrap.appendChild(dl);
      return wrap;
    }
    grid.appendChild(kvDl("Config", m.config));
    grid.appendChild(kvDl("Summary (최종값)", m.summary));
    cs.appendChild(grid);
    root.appendChild(cs);

    // --- 학습 메트릭 ---
    const metricsCard = el("div", {class: "card"});
    metricsCard.appendChild(el("h2", {text: "학습 메트릭 (wandb.log)"}));
    const metricKeys = Object.keys(r.metrics || {}).sort();
    if (metricKeys.length === 0) {
      metricsCard.appendChild(el("div", {class: "empty", text: "log() 로 기록된 스칼라 메트릭이 없습니다."}));
    } else {
      const grid2 = el("div", {class: "chart-grid"});
      metricKeys.forEach(k => {
        const chart = el("div", {class: "chart"});
        chart.appendChild(el("h3", {text: k}));
        lineChart(chart, [{
          name: k,
          color: RUN_COLORS[m.run_id] || "#38bdf8",
          points: r.metrics[k]
        }]);
        grid2.appendChild(chart);
      });
      metricsCard.appendChild(grid2);
    }
    root.appendChild(metricsCard);

    // --- 시스템 메트릭 ---
    const sysCard = el("div", {class: "card"});
    sysCard.appendChild(el("h2", {text: "시스템 메트릭 (CPU / RAM / GPU)"}));
    const sysKeys = Object.keys(r.system || {}).sort();
    if (sysKeys.length === 0) {
      sysCard.appendChild(el("div", {class: "empty", text: "수집된 시스템 샘플이 없습니다."}));
    } else {
      const grid3 = el("div", {class: "chart-grid"});
      sysKeys.forEach(k => {
        const chart = el("div", {class: "chart"});
        chart.appendChild(el("h3", {text: k + "  (x: 경과 초)"}));
        lineChart(chart, [{
          name: k,
          color: k.startsWith("gpu") ? "#f472b6" : (k.startsWith("cpu") ? "#38bdf8" : "#34d399"),
          points: r.system[k]
        }]);
        grid3.appendChild(chart);
      });
      sysCard.appendChild(grid3);
    }
    root.appendChild(sysCard);

    // --- Tables ---
    const tblCard = el("div", {class: "card"});
    tblCard.appendChild(el("h2", {text: "Tables (wandb.Table)"}));
    if (!r.tables || r.tables.length === 0) {
      tblCard.appendChild(el("div", {class: "empty", text: "Table 로그 없음"}));
    } else {
      r.tables.forEach(tbl => {
        tblCard.appendChild(el("h3", {text: `${tbl.key}  ·  step=${tbl.step}`, style: "color:#cbd5e1;font-size:13px;margin-top:10px;"}));
        const t = el("table", {class: "runs"});
        const th = el("thead");
        const trh = el("tr");
        (tbl.columns || []).forEach(c => trh.appendChild(el("th", {text: c})));
        th.appendChild(trh); t.appendChild(th);
        const tb = el("tbody");
        (tbl.data || []).forEach(row => {
          const tr = el("tr");
          row.forEach(v => tr.appendChild(el("td", {text: fmt(v)})));
          tb.appendChild(tr);
        });
        t.appendChild(tb);
        tblCard.appendChild(t);
      });
    }
    root.appendChild(tblCard);
  }

  // ---------- Sweep 탭 ----------
  function renderSweep(root) {
    // 모든 run 의 config key 와 summary key 를 수집
    const configKeys = new Set(), summaryKeys = new Set();
    runs.forEach(r => {
      Object.keys(r.meta.config || {}).forEach(k => configKeys.add(k));
      Object.keys(r.meta.summary || {}).forEach(k => summaryKeys.add(k));
    });
    const cKeys = [...configKeys].sort();
    const sKeys = [...summaryKeys].sort();

    const controlCard = el("div", {class: "card"});
    controlCard.appendChild(el("h2", {text: "Sweep view — config × 최종 메트릭"}));
    controlCard.appendChild(el("div", {
      class: "meta",
      text: "parallel coordinates 의 각 축은 config 파라미터 또는 summary 메트릭. 선 하나 = run 하나. 색상은 runs 탭의 run 색상을 그대로 따른다."
    }));
    controlCard.appendChild(el("div", {text: "비교할 run 선택:", style: "margin-top:8px;color:#94a3b8;font-size:12px;"}));
    const chkWrap = el("div", {class: "runs-checkboxes"});
    runs.forEach(r => {
      const id = r.meta.run_id;
      const label = el("label");
      label.style.borderLeft = `3px solid ${RUN_COLORS[id]}`;
      const chk = el("input", {type: "checkbox"});
      chk.checked = state.compareSet.has(id);
      chk.onchange = () => {
        if (chk.checked) state.compareSet.add(id); else state.compareSet.delete(id);
        renderPC(); renderTable();
      };
      label.appendChild(chk);
      label.appendChild(document.createTextNode(` ${r.meta.name} (${id})`));
      chkWrap.appendChild(label);
    });
    controlCard.appendChild(chkWrap);
    root.appendChild(controlCard);

    const pcCard = el("div", {class: "card"});
    pcCard.appendChild(el("h2", {text: "Parallel Coordinates"}));
    root.appendChild(pcCard);

    const tableCard = el("div", {class: "card"});
    tableCard.appendChild(el("h2", {text: "정렬 가능 테이블"}));
    root.appendChild(tableCard);

    function axisValues(r, key, isSummary) {
      const obj = isSummary ? (r.meta.summary || {}) : (r.meta.config || {});
      return obj[key];
    }

    function renderPC() {
      pcCard.querySelectorAll("svg,.empty,.legend").forEach(n => n.remove());
      const selected = runs.filter(r => state.compareSet.has(r.meta.run_id));
      const axes = [
        ...cKeys.map(k => ({key: k, label: `cfg: ${k}`, summary: false})),
        ...sKeys.map(k => ({key: k, label: `sum: ${k}`, summary: true}))
      ];
      if (selected.length === 0 || axes.length === 0) {
        pcCard.appendChild(el("div", {class: "empty", text: "표시할 데이터 없음"}));
        return;
      }
      // 각 축에 대해 수치 스케일을 만든다. 수치가 아니면 카테고리 인덱스로 인코딩.
      const axisScales = axes.map(ax => {
        const values = selected.map(r => axisValues(r, ax.key, ax.summary));
        const nums = values.filter(v => typeof v === "number" && isFinite(v));
        if (nums.length === values.filter(v => v !== undefined && v !== null).length && nums.length > 0) {
          let lo = Math.min(...nums), hi = Math.max(...nums);
          if (lo === hi) { lo -= 1; hi += 1; }
          return {type: "num", lo, hi, toY: v => (typeof v === "number" ? (v - lo) / (hi - lo) : null)};
        }
        const cats = [...new Set(values.filter(v => v !== undefined && v !== null).map(String))];
        return {type: "cat", cats, toY: v => {
          if (v === undefined || v === null) return null;
          const i = cats.indexOf(String(v));
          return i < 0 ? null : (cats.length <= 1 ? 0.5 : i / (cats.length - 1));
        }};
      });

      const W = Math.max(500, axes.length * 120), H = 280;
      const PAD_L = 60, PAD_R = 60, PAD_T = 20, PAD_B = 60;
      const svgNS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const axisX = i => PAD_L + (i * (W - PAD_L - PAD_R)) / Math.max(axes.length - 1, 1);
      const y = t => PAD_T + (1 - t) * (H - PAD_T - PAD_B);

      // 축 선 + 라벨 + 스케일 표시
      axes.forEach((ax, i) => {
        const x = axisX(i);
        const ln = document.createElementNS(svgNS, "line");
        ln.setAttribute("x1", x); ln.setAttribute("x2", x);
        ln.setAttribute("y1", PAD_T); ln.setAttribute("y2", H - PAD_B);
        ln.setAttribute("stroke", "#64748b"); ln.setAttribute("stroke-width", "1");
        svg.appendChild(ln);
        const lbl = document.createElementNS(svgNS, "text");
        lbl.setAttribute("x", x); lbl.setAttribute("y", H - PAD_B + 14);
        lbl.setAttribute("text-anchor", "middle");
        lbl.setAttribute("font-size", "10"); lbl.setAttribute("fill", "#e5e7eb");
        lbl.textContent = ax.label.length > 18 ? ax.label.slice(0, 16) + "…" : ax.label;
        svg.appendChild(lbl);
        const sc = axisScales[i];
        if (sc.type === "num") {
          [sc.lo, (sc.lo + sc.hi)/2, sc.hi].forEach((v, j) => {
            const t = document.createElementNS(svgNS, "text");
            t.setAttribute("x", x - 4); t.setAttribute("y", y(j/2) + 3);
            t.setAttribute("text-anchor", "end");
            t.setAttribute("font-size", "9"); t.setAttribute("fill", "#94a3b8");
            t.textContent = fmt(v);
            svg.appendChild(t);
          });
        } else {
          sc.cats.forEach((c, j) => {
            const yt = sc.cats.length <= 1 ? 0.5 : j / (sc.cats.length - 1);
            const t = document.createElementNS(svgNS, "text");
            t.setAttribute("x", x - 4); t.setAttribute("y", y(yt) + 3);
            t.setAttribute("text-anchor", "end");
            t.setAttribute("font-size", "9"); t.setAttribute("fill", "#94a3b8");
            t.textContent = c.length > 8 ? c.slice(0,7)+"…" : c;
            svg.appendChild(t);
          });
        }
      });

      // 각 run 의 라인
      selected.forEach(r => {
        const pts = [];
        axes.forEach((ax, i) => {
          const raw = axisValues(r, ax.key, ax.summary);
          const t = axisScales[i].toY(raw);
          if (t !== null && !isNaN(t)) pts.push([axisX(i), y(t)]);
        });
        if (pts.length < 2) return;
        const poly = document.createElementNS(svgNS, "polyline");
        poly.setAttribute("points", pts.map(p => p.join(",")).join(" "));
        poly.setAttribute("fill", "none");
        poly.setAttribute("stroke", RUN_COLORS[r.meta.run_id]);
        poly.setAttribute("stroke-width", "1.5");
        poly.setAttribute("opacity", "0.85");
        svg.appendChild(poly);
      });
      pcCard.appendChild(svg);
      const legend = el("div", {class: "legend"});
      selected.forEach(r => {
        const s = el("span", {}, [
          el("span", {class: "dot", style: `background:${RUN_COLORS[r.meta.run_id]}`}),
          `${r.meta.name} (${r.meta.run_id})`
        ]);
        legend.appendChild(s);
      });
      pcCard.appendChild(legend);
    }

    let sortKey = null, sortDir = 1;
    function renderTable() {
      tableCard.querySelectorAll("table,.empty").forEach(n => n.remove());
      const selected = runs.filter(r => state.compareSet.has(r.meta.run_id));
      if (selected.length === 0) {
        tableCard.appendChild(el("div", {class: "empty", text: "선택된 run 없음"}));
        return;
      }
      const cols = [
        {k: "__name", label: "name", getter: r => r.meta.name},
        {k: "__id",   label: "id",   getter: r => r.meta.run_id},
        {k: "__status", label: "status", getter: r => r.meta.status},
        ...cKeys.map(k => ({k: "c::"+k, label: "cfg."+k, getter: r => (r.meta.config||{})[k]})),
        ...sKeys.map(k => ({k: "s::"+k, label: "sum."+k, getter: r => (r.meta.summary||{})[k]}))
      ];
      const rows = selected.map(r => {
        const row = {_r: r};
        cols.forEach(c => row[c.k] = c.getter(r));
        return row;
      });
      if (sortKey) {
        rows.sort((a, b) => {
          const va = a[sortKey], vb = b[sortKey];
          if (va === vb) return 0;
          if (va === undefined || va === null) return 1;
          if (vb === undefined || vb === null) return -1;
          return (va > vb ? 1 : -1) * sortDir;
        });
      }
      const t = el("table", {class: "sweep"});
      const thead = el("thead"); const trh = el("tr");
      cols.forEach(c => {
        const th = el("th", {text: c.label});
        th.onclick = () => {
          if (sortKey === c.k) sortDir *= -1;
          else { sortKey = c.k; sortDir = 1; }
          renderTable();
        };
        trh.appendChild(th);
      });
      thead.appendChild(trh); t.appendChild(thead);
      const tb = el("tbody");
      rows.forEach(row => {
        const tr = el("tr");
        tr.style.borderLeft = `3px solid ${RUN_COLORS[row._r.meta.run_id]}`;
        cols.forEach(c => tr.appendChild(el("td", {text: fmt(row[c.k])})));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      tableCard.appendChild(t);
    }

    renderPC(); renderTable();
  }

  // ---------- 탭 전환 ----------
  function render() {
    const main = document.getElementById("main");
    main.innerHTML = "";
    document.querySelectorAll("nav.tabs button").forEach(b => {
      b.classList.toggle("active", b.dataset.tab === state.tab);
    });
    if (runs.length === 0) {
      main.appendChild(el("div", {class: "empty", text: "기록된 run 이 없습니다. 학습을 실행한 뒤 대시보드를 다시 생성하세요."}));
      return;
    }
    if (state.tab === "runs") renderRuns(main);
    else if (state.tab === "detail") renderDetail(main);
    else if (state.tab === "sweep") renderSweep(main);
  }

  document.querySelectorAll("nav.tabs button").forEach(b => {
    b.onclick = () => { state.tab = b.dataset.tab; render(); };
  });
  render();
})();
"""


_HTML_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <span class="meta">sentinel-track · self-contained</span>
  <nav class="tabs">
    <button data-tab="runs" class="active">Runs</button>
    <button data-tab="detail">Run 상세</button>
    <button data-tab="sweep">Sweep</button>
  </nav>
</header>
<main id="main"></main>
<script type="application/json" id="data">{data_json}</script>
<script>{js}</script>
</body>
</html>
"""


# ===== 8. CLI =====

def _cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sentinel_track",
        description="sentinel-track — 폐쇄망용 wandb 호환 트래커",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_dash = sub.add_parser("dashboard", help="run 디렉토리에서 self-contained HTML 대시보드 생성")
    p_dash.add_argument("-d", "--dir", default=DEFAULT_RUN_DIR, help="run 저장 디렉토리")
    p_dash.add_argument("-o", "--output", default="dashboard.html", help="출력 HTML 경로")
    p_dash.add_argument("-t", "--title", default="sentinel-track dashboard", help="대시보드 제목")

    sub.add_parser("demo", help="내장 데모 실행 (toy 학습 3회 + 대시보드 생성)")

    args = parser.parse_args(argv)
    if args.cmd == "dashboard":
        build_dashboard(run_dir=args.dir, output=args.output, title=args.title)
        return 0
    if args.cmd == "demo":
        _demo()
        return 0
    parser.print_help()
    return 1


def _demo() -> None:
    """여러 run 을 모의 학습으로 돌리고 대시보드를 생성하는 self-contained 데모."""
    tmp_root = pathlib.Path("./sentinel_runs_demo")
    if tmp_root.exists():
        # 기존 데모 잔재 제거는 안전하게 rmtree 수준까지는 안 하고, 신규 run 만 쌓음
        pass

    os.environ["WANDB_DIR"] = str(tmp_root)

    configs = [
        {"lr": 1e-3, "batch_size": 32, "optimizer": "adam"},
        {"lr": 3e-3, "batch_size": 64, "optimizer": "adam"},
        {"lr": 1e-2, "batch_size": 32, "optimizer": "sgd"},
    ]
    for i, cfg in enumerate(configs):
        # init — 매 iteration 마다 reinit
        finish()  # 이전 run 정리
        r = init(
            project="demo-classifier",
            name=f"run-{i+1}-{cfg['optimizer']}",
            config=cfg,
            tags=["demo", cfg["optimizer"]],
            notes="sentinel-track 내장 데모",
            reinit=True,
        )
        # 모의 학습 루프: loss 는 cfg 에 따라 다른 수렴 속도
        rng = random.Random(i)
        loss = 2.5
        for step in range(40):
            loss = loss * (0.92 - (cfg["lr"] * 2.5)) + rng.random() * 0.05
            loss = max(loss, 0.02)
            acc = max(0.0, 1.0 - loss / 2.5) * (0.9 if cfg["optimizer"] == "sgd" else 1.0)
            log({"loss": loss, "acc": acc, "epoch": step // 10}, step=step)
            time.sleep(0.04)
        # 대표 Table 한 개 남기기
        tbl = Table(
            columns=["idx", "pred", "label", "correct"],
            data=[[k, "A" if k % 2 == 0 else "B", "A" if k % 3 != 0 else "B",
                   (k % 2 == 0) == (k % 3 != 0)] for k in range(8)],
        )
        log({"predictions": tbl}, step=40)
        # summary 에 최종값이 남도록 한 번 더 log
        log({"final_loss": loss, "final_acc": acc}, step=40)
    finish()
    build_dashboard(run_dir=tmp_root, output="dashboard.html", title="sentinel-track demo")


# ===== 9. Example Usage =====

if __name__ == "__main__":
    try:
        sys.exit(_cli())
    except KeyboardInterrupt:
        try:
            finish(exit_code=130)
        except Exception:
            pass
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        try:
            finish(exit_code=1)
        except Exception:
            pass
        sys.exit(1)
