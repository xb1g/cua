"""Fine-tune Qwen2.5-1.5B on successful AEGIS trajectories.

This script converts successful `RunResult` JSON files from `trajectories/`
into prompt/completion pairs, then runs TRL SFTTrainer with LoRA/PEFT.
Install optional dependencies first:

    uv pip install -e ".[finetune]"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cua_loop.types import AttemptResult, RunResult


DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B"
DEFAULT_TRAJ_DIR = Path("trajectories")
DEFAULT_OUTPUT_DIR = Path("finetuned-aegis")
DEFAULT_LOSS_PLOT = Path("trajectories/finetune_loss.png")


def load_successful_runs(traj_dir: Path = DEFAULT_TRAJ_DIR) -> list[RunResult]:
    runs: list[RunResult] = []
    for path in sorted(traj_dir.glob("*.json")):
        try:
            run = RunResult.model_validate_json(path.read_text())
        except Exception:
            continue
        if run.success:
            runs.append(run)
    return runs


def _safe_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _attempt_to_completion(attempt: AttemptResult) -> str:
    actions = [
        {
            "action_type": step.action_type,
            "action_args": step.action_args,
            "verification_passed": step.verification_passed,
            "verification_reason": step.verification_reason,
            "blocked": step.blocked,
        }
        for step in attempt.trajectory.steps
    ]
    payload = {
        "success": attempt.verifier.success,
        "rows_extracted": attempt.verifier.rows_extracted,
        "schema_valid": attempt.verifier.schema_valid,
        "verifier_reason": attempt.verifier.reason,
        "actions": actions,
        "final_message": attempt.trajectory.final_message,
        "extracted": attempt.trajectory.extracted,
    }
    return _safe_json(payload)


def trajectory_to_examples(run: RunResult) -> list[dict[str, str]]:
    examples: list[dict[str, str]] = []
    for attempt in run.attempts:
        if not attempt.verifier.success:
            continue
        prompt = (
            "You are AEGIS, a safe and reliable computer-use agent.\n"
            "Given a user shopping/search task and starting URL, produce a safe trajectory summary, "
            "verified extracted result, and never perform irreversible actions without approval.\n\n"
            f"Task: {run.task}\n"
            f"URL: {run.url or '(none)'}\n"
            "Return JSON with success metadata, action sequence, final message, and extracted data."
        )
        examples.append({"prompt": prompt, "completion": _attempt_to_completion(attempt)})
    return examples


def build_dataset_records(traj_dir: Path = DEFAULT_TRAJ_DIR) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for run in load_successful_runs(traj_dir):
        records.extend(trajectory_to_examples(run))
    return records


def format_sft_text(example: dict[str, str]) -> str:
    return (
        "<|im_start|>user\n"
        f"{example['prompt']}\n"
        "<|im_end|>\n"
        "<|im_start|>assistant\n"
        f"{example['completion']}\n"
        "<|im_end|>"
    )


def _import_training_deps():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
        from trl import SFTTrainer
    except ImportError as exc:
        raise SystemExit(
            "Missing fine-tuning dependencies. Install them with: uv pip install -e \".[finetune]\""
        ) from exc
    return plt, torch, Dataset, LoraConfig, AutoModelForCausalLM, AutoTokenizer, TrainingArguments, SFTTrainer


def plot_loss_curve(log_history: list[dict[str, Any]], output_path: Path = DEFAULT_LOSS_PLOT) -> None:
    plt, *_ = _import_training_deps()
    losses = [(item.get("step"), item.get("loss")) for item in log_history if item.get("loss") is not None]
    if not losses:
        return
    steps, values = zip(*losses)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(steps, values, marker="o", linewidth=2)
    ax.set_title("AEGIS SFT Loss Curve")
    ax.set_xlabel("Training step")
    ax.set_ylabel("Loss")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(output_path))
    plt.close(fig)


def finetune(args: argparse.Namespace) -> None:
    plt, torch, Dataset, LoraConfig, AutoModelForCausalLM, AutoTokenizer, TrainingArguments, SFTTrainer = _import_training_deps()
    records = build_dataset_records(args.trajectories)
    if not records:
        raise SystemExit(f"No successful trajectories found in {args.trajectories}. Run AEGIS first, then retry.")

    dataset = Dataset.from_list([{"text": format_sft_text(record)} for record in records])
    device_has_cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if device_has_cuda and torch.cuda.is_bf16_supported() else torch.float16 if device_has_cuda else torch.float32

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=dtype,
        device_map="auto" if device_has_cuda else None,
        trust_remote_code=True,
    )

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        logging_steps=1,
        save_strategy="epoch",
        report_to="none",
        fp16=device_has_cuda and dtype == torch.float16,
        bf16=device_has_cuda and dtype == torch.bfloat16,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        peft_config=lora_config,
        args=training_args,
        packing=False,
    )
    trainer.train()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    plot_loss_curve(trainer.state.log_history, args.loss_plot)
    print(f"saved LoRA model -> {args.output_dir}")
    print(f"saved loss plot -> {args.loss_plot}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fine-tune Qwen2.5-1.5B on successful AEGIS trajectories with TRL + LoRA.")
    parser.add_argument("--trajectories", type=Path, default=DEFAULT_TRAJ_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--loss-plot", type=Path, default=DEFAULT_LOSS_PLOT)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--dry-run", action="store_true", help="Build prompt/completion records without training.")
    args = parser.parse_args()

    if args.dry_run:
        records = build_dataset_records(args.trajectories)
        print(f"built {len(records)} prompt/completion records")
        if records:
            print(format_sft_text(records[0])[:1200])
        return 0

    finetune(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
