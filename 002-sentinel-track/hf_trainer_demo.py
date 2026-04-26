"""
hf_trainer_demo.py — HuggingFace Trainer 와 결합한 drop-in 예제

핵심 포인트
-----------
1. 학습 스크립트의 **최상단** 에서 `import sentinel_track as wandb` 를 한다.
   → 이 import 부작용으로 `sys.modules["wandb"]` 가 우리 모듈로 치환되므로,
     이후 `from transformers import ...` 를 하면 HF 의 내부 `import wandb` 가
     모두 이 모듈을 가져가게 된다.
2. 기존 학습 코드는 전혀 건드리지 않는다. `TrainingArguments(report_to="wandb")`
   그대로 두면 HF 의 `WandbCallback` 이 자동으로 우리 shim 을 호출한다.
3. 학습이 끝난 뒤 `./sentinel_runs/` 에 run 이 쌓인다.
   사내 반출용 대시보드는 다음 명령으로 생성:
       python -m sentinel_track dashboard -d ./sentinel_runs -o dashboard.html

주의
----
- 본 예제는 transformers 가 설치된 환경에서만 실제로 학습이 돌아간다.
  미설치 환경에서도 `import sentinel_track` 동작과 안내 메시지까지만 확인 가능.
- 실제 사내에서는 이 파일의 `fake_model`/`fake_dataset` 부분만 진짜 모델/데이터셋으로
  바꿔주면 된다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ⬇️ 이 한 줄이 핵심 — transformers import 보다 먼저 실행되어야 함
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sentinel_track as wandb  # noqa: E402, F401

print("[hf_trainer_demo] sentinel_track 이 sys.modules['wandb'] 로 등록되었습니다.")

try:
    import torch
    from torch import nn
    from torch.utils.data import Dataset
    from transformers import Trainer, TrainingArguments
except ImportError as e:
    print(f"[hf_trainer_demo] transformers/torch 미설치 ({e}). ")
    print("    사내 미러에서 transformers 를 반입한 뒤 다시 실행하세요.")
    print("    현재 환경에서도 `import sentinel_track as wandb` 자체는 문제없이 동작했습니다.")
    sys.exit(0)


# ---------- 더미 모델 / 더미 데이터셋 ----------
# 실제 사내에서는 이 자리를 진짜 모델(예: AutoModelForSequenceClassification) 로 교체

class TinyRegressor(nn.Module):
    """손실 계산까지 포함한 아주 작은 회귀 모델 — Trainer 시그니처 만족용."""

    def __init__(self, in_dim: int = 8):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(in_dim, 16), nn.ReLU(), nn.Linear(16, 1))
        self.loss_fn = nn.MSELoss()

    def forward(self, x, labels=None):
        pred = self.net(x).squeeze(-1)
        out = {"logits": pred}
        if labels is not None:
            out["loss"] = self.loss_fn(pred, labels.float())
        return out


class NoiseDataset(Dataset):
    def __init__(self, n: int = 512, in_dim: int = 8, seed: int = 0):
        g = torch.Generator().manual_seed(seed)
        self.x = torch.randn(n, in_dim, generator=g)
        w = torch.randn(in_dim, generator=g)
        self.y = self.x @ w + 0.1 * torch.randn(n, generator=g)

    def __len__(self):
        return len(self.x)

    def __getitem__(self, idx):
        return {"x": self.x[idx], "labels": self.y[idx]}


def collate(batch):
    return {
        "x": torch.stack([b["x"] for b in batch]),
        "labels": torch.stack([b["labels"] for b in batch]),
    }


# ---------- 학습 ----------

def main() -> None:
    ds = NoiseDataset(n=512)
    model = TinyRegressor(in_dim=ds.x.shape[1])

    args = TrainingArguments(
        output_dir="./hf_out",
        per_device_train_batch_size=32,
        num_train_epochs=2,
        logging_steps=5,
        save_strategy="no",
        report_to="wandb",   # ⬅️ 여기를 바꾸지 않는다. 우리 shim 이 받는다.
        run_name="hf-trainer-tiny-regressor",
        remove_unused_columns=False,
        disable_tqdm=False,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=ds,
        data_collator=collate,
    )
    trainer.train()

    # 학습이 끝난 뒤 한 번에 대시보드 생성
    wandb.build_dashboard(
        run_dir="./sentinel_runs",
        output="./dashboard.html",
        title="HF Trainer × sentinel-track",
    )


if __name__ == "__main__":
    main()
