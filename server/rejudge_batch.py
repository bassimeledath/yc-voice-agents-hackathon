"""Re-run the current judge over an existing batch directory."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from dotenv import load_dotenv

from batch_eval import render_markdown, summarize
from core.judge import judge_transcript

load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rejudge an existing batch output directory.")
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("--judge-stack", choices=["gemini", "gpt", "nemotron"], default="gemini")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--copy-to-downloads", action="store_true")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> None:
    source_dir = args.batch_dir
    output_dir = args.output_dir or source_dir.with_name(f"{source_dir.name}-strict")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    profiles = json.loads((source_dir / "profiles.json").read_text())
    (output_dir / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")

    results = []
    for index, profile in enumerate(profiles, start=1):
        source_run_dir = source_dir / f"run-{index:03d}"
        run = json.loads((source_run_dir / "transcript.json").read_text())
        print(f"Rejudging run {index}: {profile['company']}")
        judgment = await judge_transcript(
            startup_profile=profile,
            run=run,
            judge_stack=args.judge_stack,
        )
        run_dir = output_dir / f"run-{index:03d}"
        run_dir.mkdir()
        (run_dir / "transcript.json").write_text(json.dumps(run, indent=2) + "\n")
        (run_dir / "judge.json").write_text(json.dumps(judgment, indent=2) + "\n")
        results.append({"run": run, "judgment": judgment})

    summary = summarize(results)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    render_args = argparse.Namespace(
        candidate_stack="existing",
        candidate_variant="existing",
        interviewer_stack="existing",
        judge_stack=args.judge_stack,
    )
    (output_dir / "summary.md").write_text(render_markdown(render_args, summary, profiles, results))

    if args.copy_to_downloads:
        downloads_dir = Path.home() / "Downloads" / output_dir.name
        if downloads_dir.exists():
            shutil.rmtree(downloads_dir)
        shutil.copytree(output_dir, downloads_dir)
        print(f"Copied strict report to {downloads_dir}")

    print(f"Wrote strict rejudge outputs to {output_dir}")


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
