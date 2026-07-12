#!/usr/bin/env python3
"""Export transcripts from coursera_transcripts_checkpoint.json to structured files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _clean_title(raw: str) -> str:
    """Strip newline-suffix garbage left in titles: 'Title\nVideo•\n. Duration: X min'"""
    return raw.split("\n")[0].strip()


def _safe_filename(title: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", title)[:60].strip("_")
    return safe or "untitled"


def export(checkpoint_path: str, output_dir: str) -> None:
    data = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))

    if not isinstance(data.get("lectures"), list) or not data["lectures"]:
        raise ValueError("checkpoint missing 'lectures' list or it is empty")

    course_slug = data.get("course_slug", "course")
    course_title = data.get("course_title", course_slug)
    lectures = data.get("lectures", [])

    course_out = Path(output_dir) / course_slug
    transcripts_dir = course_out / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    total = len(lectures)
    pad = len(str(total))

    merged_txt: list[str] = []
    merged_md: list[str] = [f"# {course_title}"]
    metadata_lectures: list[dict] = []
    current_week: str | None = None

    print("  creating individual lecture files")
    for idx, lec in enumerate(lectures, 1):
        clean = _clean_title(lec.get("title", "Untitled"))
        week = lec.get("week", "")
        url = lec.get("url", "")
        transcript = lec.get("transcript", "(No transcript found.)")
        stem = f"{idx:0{pad}d}-{_safe_filename(clean)}"

        print(f"    [{idx}/{total}] {clean[:60]}")

        (transcripts_dir / f"{stem}.txt").write_text(transcript, encoding="utf-8")
        (transcripts_dir / f"{stem}.md").write_text(
            f"# {clean}\n\n**Module:** {week}\n\n{transcript}\n",
            encoding="utf-8",
        )

        if week != current_week:
            merged_txt.append(f"=== Module: {week} ===\n")
            merged_md.append(f"\n## {week}")
            current_week = week

        t = transcript.rstrip()
        merged_txt.extend([f"[{idx}] {clean}", *([t, ""] if t else []), "---", ""])
        merged_md.extend([f"\n### [{idx}] {clean}", *([t, ""] if t else []), "---"])

        metadata_lectures.append({
            "index": idx,
            "week": week,
            "title": clean,
            "url": url,
            "filename": stem,
        })

    print("  creating transcript.txt")
    (course_out / "transcript.txt").write_text("\n".join(merged_txt), encoding="utf-8")

    print("  creating transcript.md")
    (course_out / "transcript.md").write_text("\n".join(merged_md), encoding="utf-8")

    print("  creating metadata.json")
    (course_out / "metadata.json").write_text(
        json.dumps(
            {
                "course_slug": course_slug,
                "course_title": course_title,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "total_lectures": total,
                "lectures": metadata_lectures,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Export Coursera transcripts from checkpoint JSON to structured files."
    )
    p.add_argument("--input", required=True, help="Path to checkpoint JSON")
    p.add_argument("--output", required=True, help="Output base directory")
    args = p.parse_args()

    try:
        export(args.input, args.output)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    except PermissionError as exc:
        print(f"error: permission denied: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
