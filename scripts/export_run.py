"""Export compact, reviewable evidence, omitting credentials and bulky depth arrays."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.run_dir.resolve(); dest = args.destination.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    names = ["report.json", "decisions.jsonl", "decision_summary.md", "episode_4x.mp4", "agent_completion_proposal.json"]
    for name in names:
        if (source / name).exists(): shutil.copyfile(source / name, dest / name)
    for png in source.glob("*.png"): shutil.copyfile(png, dest / png.name)
    for f in (source / "observations").rglob("*"):
        if f.suffix in (".png", ".json"):
            target = dest / f.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, target)
    if (source / "events.jsonl").exists():
        # Portable paths; private local run location is not needed for review.
        (dest / "events.jsonl").write_text((source / "events.jsonl").read_text().replace(str(source)+"/", ""))
    versions = {name: importlib.metadata.version(name) for name in ("mujoco", "numpy", "scipy", "pillow", "httpx", "imageio", "imageio-ffmpeg", "mcp", "pytest")}
    (dest / "environment.json").write_text(json.dumps({"python": platform.python_version(), "packages": versions}, indent=2))
    decisions = [json.loads(line) for line in (source / "decisions.jsonl").read_text().splitlines()] if (source / "decisions.jsonl").exists() else []
    if decisions:
        ds = [r["decision"] for r in decisions]
        (dest / "api_statistics.json").write_text(json.dumps({"calls": len(ds), "models": sorted({d["model"] for d in ds}), "confidence_min": min(d["confidence"] for d in ds), "confidence_max": max(d["confidence"] for d in ds), "latency_ms_mean": sum(d["latency_ms"] for d in ds)/len(ds), "input_tokens": sum(d["usage"].get("input_tokens",0) for d in ds), "output_tokens": sum(d["usage"].get("output_tokens",0) for d in ds)}, indent=2))
    print(dest)


if __name__ == "__main__": main()
