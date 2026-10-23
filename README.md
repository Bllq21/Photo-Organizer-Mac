# Photo Organizer (macOS)

**English** · [Español](README.es.md)

A command-line tool that takes a messy folder of photos and videos and files it
into a clean `YYYY/MM` library. Built for large local and external-drive photo
collections where duplicates, RAW+JPG pairs, DJI sequences, and inconsistent
metadata are common.

## Features

- **Metadata-first dating** — EXIF for images, `exiftool`/`mdls` for videos,
  with filesystem dates as a fallback.
- **Duplicate detection** — file size plus a partial hash, then a full hash
  only when needed.
- **Destination index** — catches files that already exist in the destination
  even if they were renamed or reorganized.
- **DJI sequence grouping** — keeps panoramas, hyperlapses, timelapses, and
  bursts in the same folder.
- **RAW+JPG pairing** — keeps sidecar files together.
- **Post-transfer verification** — compares hashes after copying.
- **Collision handling** — appends `_1`, `_2`, and so on instead of overwriting.
- **Dry-run first** — scans and reports without changing anything.
- **JSON transaction log** — `TRANSFER_LOG_<timestamp>.json` in the destination.
- **Copy or move** — copy is the default; move removes files from the source.

## Requirements

- Python 3.10+
- [Pillow](https://python-pillow.org/)
- [ExifTool](https://exiftool.org/) (recommended for reliable video metadata)
- macOS for the `mdls` fallback and `caffeinate` sleep prevention

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install exiftool   # optional, but recommended
```

## Usage

Always run a dry run first:

```bash
python3 photov5.py /Volumes/Photos /Volumes/PhotoBackup --dry-run
```

Then run the copy:

```bash
python3 photov5.py /Volumes/Photos /Volumes/PhotoBackup --mode copy
```

Or set the paths once with environment variables:

```bash
export PHOTO_SOURCE="/Volumes/Photos"
export PHOTO_DEST="/Volumes/PhotoBackup"
python3 photov5.py --dry-run
```

### Options

| Option | Description |
| --- | --- |
| `--mode {copy,move}` | Copy files (default) or move them out of the source. |
| `--org-mode {metadata_first,oldest,created,modified}` | How the file date is chosen. |
| `--sequence-window MINUTES` | Time window for grouping DJI sequence files. |
| `--skip-folder NAME` | Skip a folder name; repeat for multiple folders. |
| `--no-verify` | Skip the post-transfer hash check. |
| `--no-log` | Do not write the JSON transaction log. |
| `--no-dest-index` | Skip indexing the destination before scanning. |
| `--dry-run` | Scan and report only; never transfer files. |
| `--yes` | Skip confirmation prompts. Use only after reviewing the dry run. |

`--yes` also skips the move confirmation, so review the dry run before using it
with `--mode move`.

## Output

Files are placed under the destination using their resolved date:

```text
PhotoBackup/
├── 2023/
│   ├── 05/
│   └── 11/
├── 2024/
│   └── 02/
└── TRANSFER_LOG_20260530_142501.json
```

## WhatsApp date repair

WhatsApp filenames contain the original timestamp, but the file metadata often
does not. `whatsappRename.py` copies that timestamp into EXIF/video tags:

```bash
python3 whatsappRename.py ~/Desktop/fotos --dry-run
python3 whatsappRename.py ~/Desktop/fotos
```

It scans recursively and only touches recognized WhatsApp media names.

## How it works

1. Scans the source and classifies images, videos, sidecars, and DJI sequences.
2. Resolves the best available date for each file.
3. Groups DJI sequences and RAW+JPG pairs.
4. Builds a destination index and skips duplicates.
5. Copies or moves files into `YYYY/MM`.
6. Verifies hashes and writes a transaction log.

## Safety notes

- **Copy is the default.** Keep it that way until you have verified the result.
- Use a powered connection for external drives and keep the Mac awake.
- Do not disconnect drives while a transfer is running.
- The script never overwrites an existing destination file; it picks a new name.

## License

MIT — see [LICENSE](LICENSE).
