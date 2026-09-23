"""Export the completed pilot without credentials or bulky depth arrays."""
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runs/v2_pilot_01"
DEST = ROOT / "experiments/results/v2_pilot_01"


def copy_file(path, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in (".json", ".jsonl", ".md"):
        target.write_text(path.read_text().replace(str(SOURCE)+"/", ""))
    else: shutil.copyfile(path, target)


def main():
    for name in ("preregistered_plan.md", "manifest.json", "frozen.json", "request_plan.json", "api_results.jsonl", "physical_scores.json", "summary.json"):
        copy_file(SOURCE/name, DEST/name)
    # Reveal the setup only after all choices and primary scores have been saved.
    copy_file(SOURCE/"setup_private.json", DEST/"setup_revealed_after_decisions.json")
    for p in (SOURCE/"annotations").glob("*.json"): copy_file(p, DEST/"annotations"/p.name)
    for case in (SOURCE/"cases").iterdir():
        for name in ("scene.xml", "state.npz", "pending_observation.json"):
            copy_file(case/name, DEST/"cases"/case.name/name)
        for receipt_path in (case/"observation_receipts").glob("*.json"):
            r = json.loads(receipt_path.read_text())
            copy_file(receipt_path, DEST/receipt_path.relative_to(SOURCE))
            for image in r["images"].values():
                image = Path(image)
                copy_file(image, DEST/image.relative_to(SOURCE))
                calibration = image.parent/"calibration.json"
                copy_file(calibration, DEST/calibration.relative_to(SOURCE))
    for directory in (SOURCE/"scoring", SOURCE/"diagnostics"):
        for p in directory.rglob("*"):
            if p.is_file() and p.suffix in (".png", ".json", ".jsonl"):
                copy_file(p, DEST/p.relative_to(SOURCE))
    summary = json.loads((SOURCE/"summary.json").read_text())
    with (DEST/"per_state.csv").open("w") as f:
        columns = ["case", "cluster", "objective", "A", "B", "C", "C_choice", "C_accepted", "C_gate"]
        writer = csv.DictWriter(f, fieldnames=columns); writer.writeheader()
        for row in summary["table"]: writer.writerow({k: row[k] for k in columns})
    versions = {p: importlib.metadata.version(p) for p in ("mujoco", "numpy", "scipy", "pydantic", "httpx", "matplotlib")}
    (DEST/"environment.json").write_text(json.dumps(versions, indent=2))
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.6))
    labels = ["A: linear text", "B: structured JSON"]
    colors = ["#55758c", "#37837a"]
    progress = [summary["progress_rate"][g]*100 for g in ("A", "B")]
    tokens = [summary["groups"][g]["mean_input_tokens"] for g in ("A", "B")]
    for ax, values, title, unit in zip(axes, (progress, tokens), ("Single-action physical progress", "Mean input tokens per request"), ("Percent", "Tokens")):
        bars = ax.bar(labels, values, color=colors, width=.6)
        ax.bar_label(bars, labels=[f"{v:.1f}" for v in values], padding=4)
        ax.set_title(title, fontsize=11); ax.set_ylabel(unit)
        ax.set_ylim(0, 100 if unit == "Percent" else max(values)*1.18)
        ax.spines[["top", "right"]].set_visible(False)
        ax.yaxis.grid(True, alpha=.2); ax.set_axisbelow(True)
    fig.suptitle("V2 pilot: no measured progress gain from JSON formatting", fontsize=13)
    fig.text(.5, .045, "12 fixed states / 7 layout sources; 3 candidate permutations per state.\nLocal one-action objectives, not end-to-end grasp success. Text baseline is deterministic field linearization.", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, .12, 1, .94))
    fig.savefig(DEST/"format_comparison.png", dpi=180)
    fig.savefig(DEST/"format_comparison.pdf")
    plt.close(fig)
    hashes = {str(p.relative_to(DEST)): hashlib.sha256(p.read_bytes()).hexdigest() for p in DEST.rglob("*") if p.is_file() and p.name != "artifact_integrity.json"}
    (DEST/"artifact_integrity.json").write_text(json.dumps({"exported_wall_time": time.time(), "files": hashes}, indent=2))
    print(DEST)


if __name__ == "__main__": main()
