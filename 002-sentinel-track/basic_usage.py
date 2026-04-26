"""
basic_usage.py — sentinel_track 를 "직접" 사용하는 최소 예제 (torch/HF 불필요)

실행:
    cd 002-sentinel-track
    ./.venv/bin/python basic_usage.py

이 스크립트는 가상의 학습 루프 2개를 돌려 두 개의 run 을 남긴 뒤
dashboard.html 을 생성합니다. 외부 네트워크/바이너리 파일 없이 전부 JSONL + HTML 만 씁니다.
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

# 002-sentinel-track 폴더를 import 경로에 넣기 (polished install 없이 바로 실행)
THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent.parent))

import sentinel_track as wandb  # noqa: E402  — 이 줄이 sys.modules["wandb"] 도 함께 등록


def fake_train(run_name: str, lr: float, optimizer: str, seed: int) -> None:
    """간단한 수렴 모사 루프. loss 는 lr · optimizer 에 따라 다른 속도로 떨어진다."""
    wandb.init(
        project="basic-demo",
        name=run_name,
        config={"lr": lr, "batch_size": 32, "optimizer": optimizer},
        tags=["example", optimizer],
        notes="sentinel-track basic_usage 예제",
        reinit=True,
    )

    rng = random.Random(seed)
    loss = 2.5
    for step in range(50):
        # lr 이 클수록 빨리 줄지만 노이즈도 큼
        loss = loss * (0.9 - lr * 2.0) + rng.random() * 0.05
        loss = max(loss, 0.02)
        acc = max(0.0, 1.0 - loss / 2.5)
        # SGD 는 살짝 덜 정확
        if optimizer == "sgd":
            acc *= 0.92
        wandb.log(
            {"loss": loss, "acc": acc, "lr_effective": lr * math.exp(-step / 80)},
            step=step,
        )
        time.sleep(0.02)  # 시스템 모니터가 한두 샘플이라도 찍게 시간 여유

    # wandb.Table 예시 — 정렬 가능한 HTML 테이블로 대시보드에 렌더됨
    tbl = wandb.Table(
        columns=["idx", "input", "pred", "label", "correct"],
        data=[
            [i,
             f"sample-{i:02d}",
             "positive" if i % 2 == 0 else "negative",
             "positive" if i % 3 != 0 else "negative",
             (i % 2 == 0) == (i % 3 != 0)]
            for i in range(12)
        ],
    )
    wandb.log({"predictions": tbl}, step=50)

    # summary 최종값
    wandb.log({"final_loss": loss, "final_acc": acc}, step=50)
    wandb.finish()


def main() -> None:
    fake_train("adam-lr1e-3", lr=1e-3, optimizer="adam", seed=0)
    fake_train("adam-lr5e-3", lr=5e-3, optimizer="adam", seed=1)
    fake_train("sgd-lr1e-2",  lr=1e-2, optimizer="sgd",  seed=2)

    # 모든 run 을 묶어 self-contained 대시보드 생성
    out = wandb.build_dashboard(
        run_dir="./sentinel_runs",
        output="./dashboard.html",
        title="basic_usage demo",
    )
    print(f"\n대시보드 열기: file://{out.resolve()}")


if __name__ == "__main__":
    main()
