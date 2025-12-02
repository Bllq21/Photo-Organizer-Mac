#!/usr/bin/env python3
"""Rewrite WhatsApp media timestamps from the filename.

WhatsApp names files like ``IMG-2023-05-14-18-32-07.jpg``. This script parses
that timestamp and writes it to the EXIF/video metadata tags so the files sort
correctly in Photos, Lightroom, and other library tools.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERN = re.compile(
    r"(?:PHOTO|VID|VIDEO|IMG|AUD|PTT|AUDIO|DOC|STK)-"
    r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})"
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".3gp"}


def process_image(file: Path, datetime_str: str) -> None:
    subprocess.run(
        [
            "exiftool",
            f"-DateTimeOriginal={datetime_str}",
            f"-CreateDate={datetime_str}",
            f"-ModifyDate={datetime_str}",
            "-overwrite_original",
            str(file),
        ],
        check=True,
    )


def process_video(file: Path, datetime_str: str) -> None:
    subprocess.run(
        [
            "exiftool",
            f"-DateTimeOriginal={datetime_str}",
            f"-CreateDate={datetime_str}",
            f"-ModifyDate={datetime_str}",
            f"-TrackCreateDate={datetime_str}",
            f"-TrackModifyDate={datetime_str}",
            f"-MediaCreateDate={datetime_str}",
            f"-MediaModifyDate={datetime_str}",
            "-overwrite_original",
            str(file),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set WhatsApp photo/video metadata dates from filenames."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Folder to scan recursively (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching files without changing metadata",
    )
    args = parser.parse_args()

    photo_dir = args.directory
    if not photo_dir.is_dir():
        parser.error(f"not a directory: {photo_dir}")

    skipped = []
    processed = []

    for file in photo_dir.rglob("*"):
        if not file.is_file():
            continue

        ext = file.suffix.lower()
        if ext not in IMAGE_EXTS | VIDEO_EXTS:
            continue

        match = PATTERN.search(file.stem)
        if not match:
            skipped.append(file.name)
            continue

        year, month, day, hour, minute, second = match.groups()
        datetime_str = f"{year}:{month}:{day} {hour}:{minute}:{second}"

        if args.dry_run:
            processed.append(f"{file.name} -> {datetime_str}")
            continue

        try:
            if ext in IMAGE_EXTS:
                process_image(file, datetime_str)
            else:
                process_video(file, datetime_str)
            processed.append(f"{file.name} -> {datetime_str}")
        except FileNotFoundError:
            sys.exit("ERROR: exiftool is not installed. Install it with: brew install exiftool")
        except subprocess.CalledProcessError as error:
            skipped.append(f"{file.name} (exiftool error: {error})")

    action = "Would process" if args.dry_run else "Processed"
    print(f"\n{action}: {len(processed)}")
    for item in processed:
        print(f"  {item}")

    print(f"\nSkipped: {len(skipped)}")
    for item in skipped:
        print(f"  {item}")


if __name__ == "__main__":
    main()
