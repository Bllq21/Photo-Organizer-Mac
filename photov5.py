#!/usr/bin/env python3
"""
Photo & Video Organizer v5.0
Enhanced version with:
- Improved duplicate detection (partial hash first, then full)
- Expanded DJI pattern detection
- Fixed sequence grouping algorithm (cluster-based)
- Post-transfer verification
- Filename collision handling (append _1, _2, etc.)
- Better error reporting
- Transaction log for audit trail
- RAW+JPG pairing awareness

Original functionality preserved:
- Copy/Move modes with safety confirmations
- YYYY/MM folder structure
- Dry-run workflow
- DJI sequence grouping (Panorama, Hyperlapse folders)
"""

import os
import shutil
import json
import argparse
from datetime import datetime
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS
import hashlib
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List, Dict, Set
import re

# ============================================================================
# DEFAULTS - override with command-line arguments or environment variables
# ============================================================================
SOURCE_DIR = os.environ.get("PHOTO_SOURCE", "")
DEST_DIR = os.environ.get("PHOTO_DEST", "")

# OPERATION MODE:
# "copy" - Copy files (RECOMMENDED - safer, can re-run anytime)
# "move" - Move files (USE WITH CAUTION - removes from source)
OPERATION_MODE = "copy"

# ORGANIZATION OPTIONS:
# "metadata_first" - Use EXIF/video metadata first, fallback to filesystem (RECOMMENDED)
# "oldest" - Use oldest of creation/modification dates
# "created" - Use file creation date only
# "modified" - Use file modification date only
ORGANIZATION_MODE = "metadata_first"

# DJI SEQUENCE SETTINGS:
# Time window in minutes - files within this window are considered part of same sequence
SEQUENCE_TIME_WINDOW = 25  # minutes

# VERIFICATION:
# Verify file integrity after transfer (recommended for important files)
VERIFY_AFTER_TRANSFER = True

# TRANSACTION LOG:
# Write detailed log of all operations
WRITE_TRANSACTION_LOG = True

# SKIP SUBFOLDERS:
# List of folder names to skip entirely during scanning (case-insensitive).
# Useful to exclude system folders, thumbnails, or already-processed directories.
# Examples: ["@eaDir", ".Spotlight-V100", "THUMBNAILS", "PROCESSED"]
# Leave empty [] to scan everything.
SKIP_FOLDERS = []

# DESTINATION INDEX:
# When True, the script builds a filename index of the destination drive before
# scanning the source. This detects duplicates even if they were reorganized or
# renamed manually in the destination.
# Recommended when destination has many existing files from previous runs.
# Adds startup time proportional to destination size (~1-2 min per 10,000 files).
USE_DESTINATION_INDEX = True

# ============================================================================
# Supported formats
# ============================================================================
IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
    '.heic', '.heif',  # iPhone
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2',  # RAW formats
    '.psd', '.webp'
}

VIDEO_EXTENSIONS = {
    '.mov', '.mp4', '.m4v',  # Apple
    '.avi', '.mkv', '.wmv', '.flv',
    '.mpg', '.mpeg', '.3gp', '.mts', '.m2ts',  # DJI and others
    '.dji'  # DJI specific
}

# DJI sidecar files - transferred alongside their paired main file
# LRF = low-resolution preview video, SRT = telemetry/GPS subtitle file
SIDECAR_EXTENSIONS = {'.lrf', '.srt'}

# RAW extensions that may have JPG pairs
RAW_IMAGE_EXTENSIONS = {'.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2'}

# RAW video extensions (ProRes, cinema formats)
RAW_VIDEO_EXTENSIONS = {'.mov', '.r3d', '.braw', '.ari', '.dng'}  # MOV can be ProRes

# Paired formats - these should stay together
PAIRABLE_IMAGE_FORMATS = {'.jpg', '.jpeg', '.heic', '.heif'} | RAW_IMAGE_EXTENSIONS
PAIRABLE_VIDEO_FORMATS = {'.mp4', '.mov', '.m4v'} | RAW_VIDEO_EXTENSIONS

# ============================================================================
# DJI Pattern Detection (Expanded)
# ============================================================================
DJI_SEQUENCE_PATTERNS = {
    # Pattern: (regex, sequence_type, description)
    'PANO': (re.compile(r'^PANO[_-]', re.IGNORECASE), 'PANO', 'DJI Panorama'),
    'HYPERLAPSE': (re.compile(r'^HYPERLAPSE[_-]', re.IGNORECASE), 'HYPERLAPSE', 'DJI Hyperlapse'),
    'TIMELAPSE': (re.compile(r'^TIMELAPSE[_-]', re.IGNORECASE), 'TIMELAPSE', 'DJI Timelapse'),
    'BURST': (re.compile(r'^BURST[_-]', re.IGNORECASE), 'BURST', 'DJI Burst'),
    'RAW_BURST': (re.compile(r'^RAW_BURST[_-]', re.IGNORECASE), 'RAW_BURST', 'DJI RAW Burst'),
}

# DJI standard file patterns (not sequences, but good to recognize)
DJI_FILE_PATTERNS = [
    re.compile(r'^DJI_\d{4}', re.IGNORECASE),  # DJI_0001, DJI_0002
    re.compile(r'^DJI_\d{8}_\d{6}', re.IGNORECASE),  # DJI_20240101_120000
]


# ============================================================================
# Data Classes for Better Structure
# ============================================================================
@dataclass
class FileInfo:
    """Information about a media file"""
    filepath: Path
    file_date: Optional[datetime]
    date_source: str
    is_video: bool
    is_sequence: bool = False
    sequence_type: Optional[str] = None
    file_size: int = 0
    partial_hash: Optional[str] = None
    full_hash: Optional[str] = None
    raw_pair: Optional[Path] = None  # For JPG files that have a RAW pair


@dataclass
class TransferResult:
    """Result of a file transfer operation"""
    source: Path
    destination: Path
    operation: str  # 'copy' or 'move'
    success: bool
    skipped: bool = False
    skip_reason: Optional[str] = None
    error: Optional[str] = None
    verified: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    # Extended fields for better auditability
    date_source: Optional[str] = None      # "EXIF", "Video Metadata", "Filesystem (oldest)", etc.
    metadata_error: Optional[str] = None   # Why metadata extraction failed, if it did
    file_size: int = 0                     # Source file size in bytes
    sequence_group: Optional[str] = None  # e.g. "HYPERLAPSE/003_0011 -> Hyperlapse1"


@dataclass
class TransactionLog:
    """Complete log of an organization run"""
    start_time: datetime
    end_time: Optional[datetime] = None
    source_dir: str = ""
    dest_dir: str = ""
    operation: str = ""
    organization_mode: str = ""
    total_files: int = 0
    transferred: int = 0
    skipped: int = 0
    errors: int = 0
    verified: int = 0
    verification_failures: int = 0
    transfers: List[dict] = field(default_factory=list)
    
    def add_transfer(self, result: TransferResult):
        self.transfers.append({
            'source': str(result.source),
            'destination': str(result.destination),
            'operation': result.operation,
            'success': result.success,
            'skipped': result.skipped,
            'skip_reason': result.skip_reason,
            'error': result.error,
            'verified': result.verified,
            'timestamp': result.timestamp.isoformat(),
            'date_source': result.date_source,
            'metadata_error': result.metadata_error,
            'file_size_bytes': result.file_size,
            'sequence_group': result.sequence_group,
        })
    
    def save(self, dest_path: Path):
        """Save transaction log to JSON file"""
        self.end_time = datetime.now()
        log_file = dest_path / f"TRANSFER_LOG_{self.start_time.strftime('%Y%m%d_%H%M%S')}.json"
        
        log_data = {
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'duration_seconds': (self.end_time - self.start_time).total_seconds(),
            'source_dir': self.source_dir,
            'dest_dir': self.dest_dir,
            'operation': self.operation,
            'organization_mode': self.organization_mode,
            'summary': {
                'total_files': self.total_files,
                'transferred': self.transferred,
                'skipped': self.skipped,
                'errors': self.errors,
                'verified': self.verified,
                'verification_failures': self.verification_failures
            },
            'transfers': self.transfers
        }
        
        try:
            with open(log_file, 'w') as f:
                json.dump(log_data, f, indent=2)
            return log_file
        except Exception as e:
            print(f"Warning: Could not save transaction log: {e}")
            return None


# ============================================================================
# Hash Functions (Optimized)
# ============================================================================
PARTIAL_HASH_SIZE = 65536  # 64KB for partial hash


def get_partial_hash(filepath: Path) -> Optional[str]:
    """
    Calculate partial hash: first 64KB + last 64KB + file size
    This is fast and catches most differences without reading entire file
    """
    try:
        file_size = filepath.stat().st_size
        hash_md5 = hashlib.md5()
        
        with open(filepath, "rb") as f:
            # Read first chunk
            first_chunk = f.read(PARTIAL_HASH_SIZE)
            hash_md5.update(first_chunk)
            
            # Read last chunk if file is large enough
            if file_size > PARTIAL_HASH_SIZE * 2:
                f.seek(-PARTIAL_HASH_SIZE, 2)  # Seek from end
                last_chunk = f.read(PARTIAL_HASH_SIZE)
                hash_md5.update(last_chunk)
            
            # Include file size in hash
            hash_md5.update(str(file_size).encode())
        
        return hash_md5.hexdigest()
    except Exception:
        return None


def get_full_hash(filepath: Path) -> Optional[str]:
    """Calculate full MD5 hash of entire file"""
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return None


def files_are_identical(file1: Path, file2: Path) -> Tuple[bool, str]:
    """
    Check if two files are identical using tiered approach:
    1. Size check (instant)
    2. Partial hash (fast)
    3. Full hash (thorough, only if partial matches)
    
    Returns: (is_identical, reason)
    """
    if not file2.exists():
        return False, "destination_missing"
    
    # Size check first (instant)
    size1 = file1.stat().st_size
    size2 = file2.stat().st_size
    
    if size1 != size2:
        return False, "size_mismatch"
    
    # Partial hash check (fast)
    partial1 = get_partial_hash(file1)
    partial2 = get_partial_hash(file2)
    
    if partial1 is None or partial2 is None:
        # Fall back to full hash if partial fails
        pass
    elif partial1 != partial2:
        return False, "partial_hash_mismatch"
    
    # Full hash check (thorough) - only if sizes and partial hashes match
    full1 = get_full_hash(file1)
    full2 = get_full_hash(file2)
    
    if full1 and full2 and full1 == full2:
        return True, "identical"
    
    return False, "hash_mismatch"


# ============================================================================
# DJI Detection (Expanded)
# ============================================================================
def detect_dji_sequence(filename: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Check if file is part of a DJI sequence
    
    Returns: (is_sequence, sequence_type, description)
    """
    for seq_type, (pattern, type_name, description) in DJI_SEQUENCE_PATTERNS.items():
        if pattern.match(filename):
            return True, type_name, description
    
    return False, None, None


def is_dji_file(filename: str) -> bool:
    """Check if file follows DJI naming convention (non-sequence)"""
    for pattern in DJI_FILE_PATTERNS:
        if pattern.match(filename):
            return True
    return False


# ============================================================================
# Sequence Grouping (Multiple Strategies)
# ============================================================================
def extract_sequence_base_name(filename: str) -> Optional[str]:
    """
    Extract the base name of a sequence file for grouping.
    
    DJI naming patterns:
    - HYPERLAPSE_0001.JPG -> HYPERLAPSE
    - PANO_20240615_140000_001.JPG -> PANO_20240615_140000
    - BURST_0001_001.JPG -> BURST_0001
    
    Returns the base name that should be shared by all files in the sequence.
    """
    name = filename.upper()
    stem = Path(filename).stem.upper()
    
    # Pattern: PREFIX_NNNN (simple numbered sequence)
    # HYPERLAPSE_0001, PANO_0001, etc.
    match = re.match(r'^(HYPERLAPSE|PANO|TIMELAPSE|BURST|RAW_BURST)_(\d+)$', stem)
    if match:
        return match.group(1)  # Just the prefix for simple sequences
    
    # Pattern: PREFIX_TIMESTAMP_NNN (timestamped sequence)
    # PANO_20240615_140000_001
    match = re.match(r'^(HYPERLAPSE|PANO|TIMELAPSE|BURST|RAW_BURST)_(\d{8}_\d{6})_\d+$', stem)
    if match:
        return f"{match.group(1)}_{match.group(2)}"  # PREFIX_TIMESTAMP
    
    # Pattern: PREFIX_TIMESTAMP (no sequence number, group by timestamp)
    match = re.match(r'^(HYPERLAPSE|PANO|TIMELAPSE|BURST|RAW_BURST)_(\d{8}_\d{6})$', stem)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    
    # Fallback: just use the prefix
    for prefix in ['HYPERLAPSE', 'PANO', 'TIMELAPSE', 'BURST', 'RAW_BURST']:
        if stem.startswith(prefix):
            return prefix
    
    return None


def group_sequence_files_by_cluster(files_with_dates: List[Tuple[Path, datetime, str]], 
                                     time_window_minutes: int = 10) -> List[List[Tuple[Path, datetime, str]]]:
    """
    Group sequence files using a hybrid approach:
    1. FIRST: Group by source directory (DJI already separates sequences into folders!)
    2. Within each directory, group by base filename pattern
    3. Then, if timestamps have large gaps, split into sub-groups
    
    This ensures files that DJI put in separate folders stay separate,
    while files in the same folder stay together.
    """
    if not files_with_dates:
        return []
    
    # Step 1: Group by SOURCE DIRECTORY first - this is critical!
    # DJI puts each hyperlapse/pano in its own subfolder like 001_0128, 003_0011
    by_directory = defaultdict(list)
    for filepath, dt, seq_type in files_with_dates:
        by_directory[filepath.parent].append((filepath, dt, seq_type))
    
    all_clusters = []
    
    # Step 2: Process each directory separately
    for directory, dir_files in by_directory.items():
        # Within this directory, group by base name pattern
        by_base_name = defaultdict(list)
        for filepath, dt, seq_type in dir_files:
            base = extract_sequence_base_name(filepath.name)
            if base is None:
                base = seq_type
            by_base_name[base].append((filepath, dt, seq_type))
        
        # Step 3: For each base name group in this directory
        for base_name, group_files in by_base_name.items():
            if len(group_files) <= 1:
                all_clusters.append(group_files)
                continue
            
            # Sort by timestamp
            sorted_files = sorted(group_files, key=lambda x: x[1])
            
            # Check total time span
            total_span_minutes = (sorted_files[-1][1] - sorted_files[0][1]).total_seconds() / 60
            
            # If total span is within reasonable window, keep as single cluster
            # Using 2x window as buffer since hyperlapses can span long real-time periods
            if total_span_minutes <= time_window_minutes * 2:
                all_clusters.append(sorted_files)
                continue
            
            # Otherwise, split using gap detection
            # Look for gaps larger than time_window between consecutive files
            current_cluster = [sorted_files[0]]
            
            for i in range(1, len(sorted_files)):
                current_file = sorted_files[i]
                prev_file = sorted_files[i-1]
                
                gap_minutes = (current_file[1] - prev_file[1]).total_seconds() / 60
                
                # If gap is larger than window, start new cluster
                if gap_minutes > time_window_minutes:
                    all_clusters.append(current_cluster)
                    current_cluster = [current_file]
                else:
                    current_cluster.append(current_file)
            
            if current_cluster:
                all_clusters.append(current_cluster)
    
    return all_clusters


def group_sequence_files_by_directory(files: List[Tuple[Path, str]]) -> List[List[Tuple[Path, str]]]:
    """
    Group sequence files without dates by their source directory and base name.
    Files in the same directory with the same sequence prefix stay together.
    """
    # Group by (directory, base_name)
    by_dir_and_base = defaultdict(list)
    
    for filepath, seq_type in files:
        base = extract_sequence_base_name(filepath.name) or seq_type
        key = (filepath.parent, base)
        by_dir_and_base[key].append((filepath, seq_type))
    
    return list(by_dir_and_base.values())


# ============================================================================
# Metadata Extraction (Improved Error Reporting)
# ============================================================================
@dataclass
class MetadataResult:
    """Result of metadata extraction attempt"""
    date: Optional[datetime]
    source: str
    success: bool
    error: Optional[str] = None


def get_exif_date(filepath: Path) -> MetadataResult:
    """Extract EXIF DateTimeOriginal from image with detailed error reporting"""
    try:
        image = Image.open(filepath)
        exif = image._getexif()
        
        if exif is None:
            return MetadataResult(None, "EXIF", False, "No EXIF data found")
        
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "DateTimeOriginal":
                try:
                    date = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                    return MetadataResult(date, "EXIF", True)
                except ValueError as e:
                    return MetadataResult(None, "EXIF", False, f"Invalid date format: {value}")
        
        return MetadataResult(None, "EXIF", False, "DateTimeOriginal tag not found")
    
    except Image.UnidentifiedImageError:
        return MetadataResult(None, "EXIF", False, "Unidentified image format (possibly HEIC)")
    except Exception as e:
        return MetadataResult(None, "EXIF", False, str(e))


def _is_epoch_placeholder(date: datetime) -> bool:
    """
    DJI (and other cameras) frequently leave the container's standard
    creation-time atom unset (zero). Tools reading that atom report it
    back as 1970-01-01 (Unix epoch) or occasionally 1904-01-01 (QuickTime
    epoch, unconverted). This is a valid-looking date, NOT a null/error,
    so it silently passes strptime and poisons the YYYY/MM sort unless
    explicitly rejected.
    """
    return date.year <= 1971


def _get_date_via_exiftool(filepath: Path) -> MetadataResult:
    """
    Extract creation date using exiftool, trying tags in priority order.
    exiftool reads DJI's actual embedded metadata (not just the generic
    QuickTime mvhd atom that mdls relies on), so it's far more reliable
    for drone footage. Falls through tags if one is missing or epoch.
    """
    tags_in_priority = ["-DateTimeOriginal", "-CreateDate", "-MediaCreateDate", "-TrackCreateDate"]
    try:
        for tag in tags_in_priority:
            result = subprocess.run(
                ["exiftool", "-s3", tag, str(filepath)],
                capture_output=True, text=True, timeout=10
            )
            value = result.stdout.strip()
            if not value:
                continue
            try:
                date = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
            if _is_epoch_placeholder(date):
                continue  # this tag is also a placeholder — try the next one
            return MetadataResult(date, f"exiftool ({tag.lstrip('-')})", True)

        return MetadataResult(None, "exiftool", False, "No valid (non-epoch) date tag found")

    except FileNotFoundError:
        return MetadataResult(None, "exiftool", False, "exiftool not installed (brew install exiftool)")
    except subprocess.TimeoutExpired:
        return MetadataResult(None, "exiftool", False, "exiftool timeout")
    except Exception as e:
        return MetadataResult(None, "exiftool", False, str(e))


def _get_date_via_mdls(filepath: Path) -> MetadataResult:
    """Fallback: macOS Spotlight index. Kept for when exiftool is unavailable."""
    try:
        result = subprocess.run(
            ['mdls', '-name', 'kMDItemContentCreationDate', '-raw', str(filepath)],
            capture_output=True,
            text=True,
            timeout=5
        )

        date_str = result.stdout.strip()
        if not date_str or date_str == '(null)':
            return MetadataResult(None, "Video Metadata (mdls)", False, "No creation date in metadata")

        # Parse the date string
        # Format: 2024-01-15 10:30:45 +0000
        date_str = date_str.split('+')[0].split('-')
        if len(date_str) >= 3:
            date_str = '-'.join(date_str[:3])
            try:
                date = datetime.strptime(date_str.strip(), '%Y-%m-%d %H:%M:%S')
            except ValueError as e:
                return MetadataResult(None, "Video Metadata (mdls)", False, f"Date parse error: {e}")

            if _is_epoch_placeholder(date):
                return MetadataResult(None, "Video Metadata (mdls)", False,
                                       f"Rejected epoch placeholder date: {date}")

            return MetadataResult(date, "Video Metadata (mdls)", True)

        return MetadataResult(None, "Video Metadata (mdls)", False, "Unexpected date format")

    except subprocess.TimeoutExpired:
        return MetadataResult(None, "Video Metadata (mdls)", False, "mdls timeout")
    except FileNotFoundError:
        return MetadataResult(None, "Video Metadata (mdls)", False, "mdls not found (not macOS?)")
    except Exception as e:
        return MetadataResult(None, "Video Metadata (mdls)", False, str(e))


def get_video_metadata_date(filepath: Path) -> MetadataResult:
    """
    Extract creation date from video metadata.
    Tries exiftool first (reads DJI's embedded tags, not just the generic
    QuickTime atom), falls back to mdls. Both reject epoch placeholder
    dates rather than trusting them — see _is_epoch_placeholder().
    """
    result = _get_date_via_exiftool(filepath)
    if result.success:
        return result

    return _get_date_via_mdls(filepath)


def get_filesystem_dates(filepath: Path) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Get creation and modification dates from filesystem"""
    try:
        stat = os.stat(filepath)
        creation = stat.st_birthtime if hasattr(stat, 'st_birthtime') else stat.st_ctime
        modification = stat.st_mtime
        return (
            datetime.fromtimestamp(creation),
            datetime.fromtimestamp(modification)
        )
    except Exception:
        return None, None


def get_file_date(filepath: Path, is_video: bool = False, 
                  mode: str = "metadata_first") -> Tuple[Optional[datetime], str]:
    """
    Get date for file based on organization mode.
    
    Returns: (date, source_description)
    """
    created_date, modified_date = get_filesystem_dates(filepath)
    
    if mode == "metadata_first":
        if is_video:
            result = get_video_metadata_date(filepath)
            if result.success and result.date:
                return result.date, "Video Metadata"
        else:
            result = get_exif_date(filepath)
            if result.success and result.date:
                return result.date, "EXIF"
        
        # Fallback to filesystem
        if created_date and modified_date:
            oldest = min(created_date, modified_date)
            return oldest, "Filesystem (oldest)"
        elif created_date:
            return created_date, "Filesystem (created)"
        elif modified_date:
            return modified_date, "Filesystem (modified)"
    
    elif mode == "oldest":
        if created_date and modified_date:
            return min(created_date, modified_date), "Oldest"
        elif created_date:
            return created_date, "Created"
        elif modified_date:
            return modified_date, "Modified"
    
    elif mode == "created":
        if created_date:
            return created_date, "Created"
    
    elif mode == "modified":
        if modified_date:
            return modified_date, "Modified"
    
    return None, "NONE"


# ============================================================================
# Filename Handling
# ============================================================================
def sanitize_filename(filename: str) -> str:
    """Remove problematic characters from filename"""
    filename = unicodedata.normalize('NFKD', filename)
    filename = filename.encode('ascii', 'ignore').decode('ascii')
    filename = filename.replace('\x00', '')
    if not filename or filename.isspace():
        filename = f"unnamed_{int(time.time())}"
    return filename


def get_unique_destination(dest_file: Path) -> Path:
    """
    Get a unique destination path by appending _1, _2, etc. if file exists.
    
    This prevents overwriting files with same name but different content.
    """
    if not dest_file.exists():
        return dest_file
    
    stem = dest_file.stem
    suffix = dest_file.suffix
    parent = dest_file.parent
    
    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        new_path = parent / new_name
        if not new_path.exists():
            return new_path
        counter += 1
        
        # Safety limit
        if counter > 10000:
            raise ValueError(f"Too many files with same name: {stem}")


# ============================================================================
# RAW+JPG and Video Pairing
# ============================================================================
@dataclass
class FilePair:
    """A pair of related files (e.g., RAW+JPG)"""
    primary: Path  # The "main" file (usually JPG or MP4)
    secondary: Path  # The paired file (usually RAW or ProRes)
    pair_type: str  # 'image' or 'video'


def find_file_pairs(files: List[FileInfo]) -> Tuple[Dict[Path, Path], Dict[Path, Path]]:
    """
    Find paired files that should stay together:
    - RAW+JPG image pairs (same stem, different extensions)
    - ProRes+MP4 video pairs
    
    Returns: 
        - primary_to_secondary: Dict mapping primary file to its pair
        - secondary_to_primary: Dict mapping secondary file to its primary
    """
    primary_to_secondary = {}
    secondary_to_primary = {}
    
    # Group by directory and stem (filename without extension)
    by_dir_and_stem = defaultdict(list)
    for f in files:
        key = (f.filepath.parent, f.filepath.stem.lower())
        by_dir_and_stem[key].append(f)
    
    for (directory, stem), file_list in by_dir_and_stem.items():
        if len(file_list) < 2:
            continue
        
        # === IMAGE PAIRS ===
        raw_images = [f for f in file_list if f.filepath.suffix.lower() in RAW_IMAGE_EXTENSIONS]
        jpg_images = [f for f in file_list if f.filepath.suffix.lower() in {'.jpg', '.jpeg', '.heic', '.heif'}]
        
        if raw_images and jpg_images:
            # JPG is primary, RAW is secondary
            for jpg in jpg_images:
                for raw in raw_images:
                    primary_to_secondary[jpg.filepath] = raw.filepath
                    secondary_to_primary[raw.filepath] = jpg.filepath
        
        # === VIDEO PAIRS ===
        # Common pattern: ProRes MOV + H.264 MP4 with same name
        mov_files = [f for f in file_list if f.filepath.suffix.lower() == '.mov' and f.is_video]
        mp4_files = [f for f in file_list if f.filepath.suffix.lower() == '.mp4' and f.is_video]
        
        if mov_files and mp4_files:
            # MP4 is typically the "proxy" or delivery format, MOV is often ProRes master
            # But file size can help determine which is the "master"
            for mp4 in mp4_files:
                for mov in mov_files:
                    # Larger file is likely the master/RAW
                    if mov.file_size > mp4.file_size:
                        # MOV is the "RAW", MP4 is primary (smaller proxy)
                        primary_to_secondary[mp4.filepath] = mov.filepath
                        secondary_to_primary[mov.filepath] = mp4.filepath
                    else:
                        # MP4 is larger, MOV might be proxy
                        primary_to_secondary[mov.filepath] = mp4.filepath
                        secondary_to_primary[mp4.filepath] = mov.filepath
    
    return primary_to_secondary, secondary_to_primary


def get_pair_destination(primary_dest: Path, secondary_path: Path) -> Path:
    """
    Given where the primary file will go, determine where its pair should go.
    Keeps the pair in the same folder with the same stem but original extension.
    """
    return primary_dest.parent / (primary_dest.stem + secondary_path.suffix)


def transfer_sidecars(primary_src: Path, primary_dest: Path,
                      sidecar_map: Dict[tuple, List[Path]],
                      operation: str, verify: bool,
                      stats: dict, failed_files: list,
                      tx_log, sequence_group: Optional[str] = None):
    """
    Transfer any sidecar files (LRF, SRT) that share the stem of primary_src.
    Called immediately after the primary file is successfully transferred.
    Sidecars go to the same destination folder as the primary.
    """
    key = (primary_src.parent, primary_src.stem.lower())
    sidecars = sidecar_map.get(key, [])
    
    for sidecar_path in sidecars:
        sidecar_dest = primary_dest.parent / sidecar_path.name
        
        # Skip if already there and identical
        if sidecar_dest.exists():
            is_identical, _ = files_are_identical(sidecar_path, sidecar_dest)
            if is_identical:
                stats['sidecars_skipped'] = stats.get('sidecars_skipped', 0) + 1
                continue
            else:
                sidecar_dest = get_unique_destination(sidecar_dest)
        
        sidecar_result = transfer_file_with_retry(
            sidecar_path, sidecar_dest, operation, verify=verify
        )
        sidecar_result.sequence_group = sequence_group

        if sidecar_result.success:
            stats['sidecars_transferred'] = stats.get('sidecars_transferred', 0) + 1
        else:
            stats['errors'] += 1
            failed_files.append((sidecar_path.name, sidecar_result.error))
            print(f"\nERROR (sidecar): {sidecar_path.name}: {sidecar_result.error}")
        
        tx_log.add_transfer(sidecar_result)


# ============================================================================
# File Transfer (With Verification)
# ============================================================================
def transfer_file_with_retry(src: Path, dst: Path, operation: str = "copy", 
                              max_retries: int = 3, verify: bool = True) -> TransferResult:
    """
    Copy or move file with retry logic, unicode handling, and verification.

    COPY verification: hash src vs dst after copy (full pipeline).
    MOVE verification: hash src BEFORE move, hash dst AFTER, compare.
                       This is the only safe approach since src is gone post-move.
    """
    dst_dir = dst.parent
    dst_name = sanitize_filename(dst.name)
    dst = dst_dir / dst_name
    
    result = TransferResult(
        source=src,
        destination=dst,
        operation=operation,
        success=False
    )

    # For MOVE: capture source hash BEFORE the file disappears
    pre_move_hash = None
    if verify and operation == "move":
        pre_move_hash = get_full_hash(src)
        if pre_move_hash is None:
            result.error = "Could not hash source before move — aborting for safety"
            return result
    
    for attempt in range(max_retries):
        try:
            if operation == "copy":
                shutil.copy2(str(src), str(dst))
            else:
                shutil.move(str(src), str(dst))
            
            result.success = True
            
            # Verification
            if verify and operation == "copy":
                # Full pipeline: size + partial + full MD5
                is_identical, reason = files_are_identical(src, dst)
                result.verified = is_identical
                if not is_identical:
                    result.error = f"Verification failed: {reason}"
                    result.success = False
                    try:
                        dst.unlink()
                    except:
                        pass
                    continue  # Retry

            elif verify and operation == "move":
                # Compare pre-move source hash to post-move destination hash
                post_move_hash = get_full_hash(dst)
                if post_move_hash is None:
                    result.error = "Could not hash destination after move"
                    result.verified = False
                elif pre_move_hash == post_move_hash:
                    result.verified = True
                else:
                    result.verified = False
                    result.error = f"Move verification failed: hash mismatch (src={pre_move_hash[:8]}... dst={post_move_hash[:8]}...)"
                    # File is already moved — log the error but don't delete dst
                    # since the source is gone. The error log will flag this for manual review.
            
            return result
            
        except Exception as e:
            if attempt == max_retries - 1:
                result.error = str(e)
                return result
            time.sleep(1)
    
    result.error = "Unknown error after retries"
    return result


# ============================================================================
# Progress Tracking
# ============================================================================
class ProgressTracker:
    """Track progress and estimate completion time"""
    
    def __init__(self, total: int):
        self.total = total
        self.current = 0
        self.start_time = time.time()
        self.last_update = self.start_time
        self.current_file = ""
        
    def update(self, increment: int = 1, filename: Optional[str] = None):
        self.current += increment
        if filename:
            self.current_file = filename
        current_time = time.time()
        
        # Update every 0.1s for smoother display
        if current_time - self.last_update >= 0.1 or self.current == self.total:
            self.last_update = current_time
            self._print_progress()
    
    def _print_progress(self):
        elapsed = time.time() - self.start_time
        if self.current == 0:
            return
        
        percent = (self.current / self.total) * 100
        rate = self.current / elapsed if elapsed > 0 else 0
        remaining = (self.total - self.current) / rate if rate > 0 else 0
        
        bar_length = 40
        filled = int(bar_length * self.current / self.total)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        # Clear and print
        print('\r\033[K', end='')
        print(f'[{bar}] {percent:.1f}% | {self.current}/{self.total} | ETA: {format_time(remaining)}')
        
        if self.current_file:
            max_length = 65
            display_name = self.current_file if len(self.current_file) <= max_length else '...' + self.current_file[-(max_length-3):]
            print(f'Processing: {display_name}', end='')
        
        if self.current == self.total:
            print()
        else:
            print('\r\033[1A', end='', flush=True)


# ============================================================================
# Utility Functions
# ============================================================================
def format_size(bytes: int) -> str:
    """Format bytes to human readable"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} PB"


def format_time(seconds: float) -> str:
    """Format seconds to human readable"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    else:
        return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"


# ============================================================================
# System Checks
# ============================================================================
def check_system_requirements(source: str, dest: str) -> Tuple[List[str], List[str]]:
    """Perform pre-flight checks"""
    issues = []
    warnings = []
    
    source_path = Path(source)
    dest_path = Path(dest)
    
    if not source_path.exists():
        issues.append(f"Source doesn't exist: {source}")
    
    if not dest_path.parent.exists():
        issues.append(f"Destination parent doesn't exist: {dest_path.parent}")
    
    source_str = str(source_path)
    dest_str = str(dest_path)
    
    if source_str.startswith('/Volumes/'):
        print(f"✓ Source is on external drive: {source_path.parts[2]}")
    else:
        warnings.append("Source is on internal drive")
    
    if dest_str.startswith('/Volumes/'):
        print(f"✓ Destination is on external drive: {dest_path.parts[2]}")
    else:
        warnings.append("Destination is on internal drive")
    
    try:
        source_stat = shutil.disk_usage(source_path)
        dest_stat = shutil.disk_usage(dest_path.parent if dest_path.exists() else dest_path.parent)
        
        needed_space = source_stat.used
        available_space = dest_stat.free
        
        print(f"\nSpace check:")
        print(f"  Source used: {format_size(source_stat.used)}")
        print(f"  Destination available: {format_size(available_space)}")
        
        if available_space < needed_space * 0.5:
            warnings.append(f"Low disk space on destination ({format_size(available_space)} available)")
    except Exception as e:
        warnings.append(f"Could not check disk space: {e}")
    
    # Check filesystem type (macOS specific)
    try:
        result = subprocess.run(['diskutil', 'info', str(dest_path)], 
                              capture_output=True, text=True, timeout=5)
        if 'File System Personality:' in result.stdout:
            for line in result.stdout.split('\n'):
                if 'File System Personality:' in line:
                    fs_type = line.split(':')[1].strip()
                    if fs_type not in ['APFS', 'ExFAT', 'HFS+']:
                        warnings.append(f"Destination filesystem is {fs_type} (APFS or ExFAT recommended)")
                    else:
                        print(f"✓ Destination filesystem: {fs_type}")
    except Exception:
        pass
    
    return issues, warnings


# ============================================================================
# Destination Index (for large drives)
# ============================================================================
def build_destination_index(dest_path: Path) -> Dict[str, List[Path]]:
    """
    Walk the destination drive and build an index of:
        filename (lowercase, no extension) -> [list of full paths]

    This allows duplicate detection even when a file has been manually
    moved or renamed in the destination (e.g. added _1 suffix).

    The index is keyed by stem only (not full path) so it catches
    files regardless of which subfolder they ended up in.

    Returns: dict of {stem_lower: [Path, ...]}
    """
    print("\nBuilding destination index...")
    index: Dict[str, List[Path]] = defaultdict(list)
    count = 0

    all_dest_extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS

    for root, dirs, files in os.walk(dest_path):
        # Skip log files we wrote ourselves
        dirs[:] = [d for d in dirs]  # no pruning needed on dest
        for filename in files:
            if Path(filename).suffix.lower() not in all_dest_extensions:
                continue
            filepath = Path(root) / filename
            index[filepath.stem.lower()].append(filepath)
            count += 1
            if count % 1000 == 0:
                print(f"  Indexed {count} destination files...", end='\r')

    print(f"  Indexed {count} destination files total")
    return index


def find_in_destination_index(src: Path, index: Dict[str, List[Path]]) -> Optional[Path]:
    """
    Look up a source file in the destination index.
    Returns the first destination path where the file is identical, or None.

    Matching strategy:
    1. Find all destination candidates with the same stem
    2. For each candidate, run files_are_identical (size + hash)
    3. Return the first match, or None
    """
    candidates = index.get(src.stem.lower(), [])
    for candidate in candidates:
        is_identical, _ = files_are_identical(src, candidate)
        if is_identical:
            return candidate
    return None


def organize_media(source_dir: str, dest_dir: str, org_mode: str,
                   operation: str = "copy", dry_run: bool = True,
                   verify: bool = True, write_log: bool = True,
                   skip_folders: Optional[List[str]] = None,
                   use_dest_index: bool = True) -> Optional[dict]:
    """
    Organize photos and videos into YYYY/MM structure with DJI sequence handling.
    """
    skip_folders_lower = {f.lower() for f in (skip_folders or [])}

    source_path = Path(source_dir).resolve()
    dest_path = Path(dest_dir).resolve()
    
    if not source_path.exists():
        print(f"ERROR: Source directory doesn't exist: {source_path}")
        return None
    
    # Initialize statistics
    stats = {
        'total': 0,
        'transferred': 0,
        'skipped_duplicate': 0,
        'skipped_collision': 0,
        'no_date': 0,
        'errors': 0,
        'sequences_found': 0,
        'sequences_no_date': 0,
        'verified': 0,
        'verification_failures': 0,
        'file_pairs': 0,
        'pairs_transferred': 0,
        'sidecars_transferred': 0,
        'sidecars_skipped': 0,
    }
    
    # Initialize transaction log
    tx_log = TransactionLog(
        start_time=datetime.now(),
        source_dir=str(source_path),
        dest_dir=str(dest_path),
        operation=operation,
        organization_mode=org_mode
    )
    
    all_extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
    all_media_extensions = all_extensions | SIDECAR_EXTENSIONS
    no_date_files = []
    failed_files = []
    # sidecar_map: stem -> list of sidecar paths, keyed by (parent_dir, stem)
    sidecar_map: Dict[tuple, List[Path]] = defaultdict(list)
    
    mode_text = "DRY RUN" if dry_run else f"{operation.upper()}ING FILES"
    print(f"\n{'='*70}")
    print(f"{mode_text}")
    print(f"Organization mode: {org_mode}")
    print(f"Verification: {'enabled' if verify else 'disabled'}")
    print(f"From: {source_path}")
    print(f"To:   {dest_path}")
    if skip_folders_lower:
        print(f"Skipping folders: {', '.join(sorted(skip_folders_lower))}")
    print(f"{'='*70}\n")

    # ========================================================================
    # PHASE 0: Build destination index (if enabled and dest exists)
    # ========================================================================
    dest_index: Dict[str, List[Path]] = {}
    if use_dest_index and dest_path.exists():
        dest_index = build_destination_index(dest_path)
    elif use_dest_index:
        print("Destination doesn't exist yet — skipping index build")

    # ========================================================================
    # PHASE 1: Scan and classify all files
    # ========================================================================
    print("\nPhase 1: Scanning files and detecting sequences...")
    
    all_files: List[FileInfo] = []
    sequence_files_by_type: Dict[str, List[Tuple[Path, datetime, str]]] = defaultdict(list)
    sequence_files_no_date: Dict[str, List[Tuple[Path, str]]] = defaultdict(list)
    
    scan_start = time.time()
    file_count = 0
    sidecar_count = 0
    skipped_folder_count = 0

    for root, dirs, files in os.walk(source_path):
        # Skip folders by name (modifying dirs in-place prunes the walk)
        if skip_folders_lower:
            skipped = [d for d in dirs if d.lower() in skip_folders_lower]
            skipped_folder_count += len(skipped)
            dirs[:] = [d for d in dirs if d.lower() not in skip_folders_lower]

        for filename in files:
            filepath = Path(root) / filename
            ext = filepath.suffix.lower()

            # Handle sidecar files (LRF, SRT) - store for later pairing
            if ext in SIDECAR_EXTENSIONS:
                key = (filepath.parent, filepath.stem.lower())
                sidecar_map[key].append(filepath)
                sidecar_count += 1
                continue

            if ext not in all_extensions:
                continue
            
            file_count += 1
            if file_count % 100 == 0:
                print(f"  Scanned {file_count} files...", end='\r')
            
            is_video = ext in VIDEO_EXTENSIONS
            is_sequence, seq_type, seq_desc = detect_dji_sequence(filename)
            
            # Get date
            file_date, date_source = get_file_date(filepath, is_video, org_mode)
            
            file_info = FileInfo(
                filepath=filepath,
                file_date=file_date,
                date_source=date_source,
                is_video=is_video,
                is_sequence=is_sequence,
                sequence_type=seq_type,
                file_size=filepath.stat().st_size
            )
            
            all_files.append(file_info)
            
            if is_sequence:
                if file_date:
                    month_key = f"{file_date.year}-{file_date.month:02d}-{seq_type}"
                    sequence_files_by_type[month_key].append((filepath, file_date, seq_type))
                else:
                    dir_key = f"{filepath.parent}-{seq_type}"
                    sequence_files_no_date[dir_key].append((filepath, seq_type))
    
    scan_time = time.time() - scan_start
    msg = f"  Scanned {file_count} media files + {sidecar_count} sidecar files in {format_time(scan_time)}"
    if skipped_folder_count:
        msg += f" ({skipped_folder_count} folders skipped)"
    print(msg)
    
    # ========================================================================
    # PHASE 2: Find file pairs (RAW+JPG, ProRes+MP4)
    # ========================================================================
    print("\nPhase 2: Finding file pairs (RAW+JPG, video pairs)...")
    primary_to_secondary, secondary_to_primary = find_file_pairs(all_files)
    stats['file_pairs'] = len(primary_to_secondary)
    print(f"  Found {len(primary_to_secondary)} file pairs")
    
    # ========================================================================
    # PHASE 3: Group sequences using fixed algorithm
    # ========================================================================
    print("\nPhase 3: Grouping DJI sequences...")
    
    # Key: (year, month, seq_type, group_num), Value: list of filepaths
    sequence_groups: Dict[tuple, List[Path]] = {}
    # Key for no-date sequences: ('NO_DATE', seq_type, group_num)
    
    # Process sequences WITH dates (grouped by time proximity)
    for month_key, seq_files in sequence_files_by_type.items():
        parts = month_key.split('-')
        year, month, seq_type = int(parts[0]), int(parts[1]), parts[2]
        
        # Use fixed clustering algorithm
        clusters = group_sequence_files_by_cluster(seq_files, SEQUENCE_TIME_WINDOW)
        
        for group_num, cluster in enumerate(clusters, start=1):
            key = (year, month, seq_type, group_num)
            sequence_groups[key] = [f[0] for f in cluster]
            stats['sequences_found'] += 1
    
    # NEW: Process sequences WITHOUT dates (grouped by directory and base name)
    no_date_seq_counter = defaultdict(int)  # Track group numbers per sequence type
    no_date_groups = group_sequence_files_by_directory(
        [(f, t) for dir_key, files in sequence_files_no_date.items() for f, t in files]
    )
    
    for group_files in no_date_groups:
        if not group_files:
            continue
        
        seq_type = group_files[0][1]
        no_date_seq_counter[seq_type] += 1
        group_num = no_date_seq_counter[seq_type]
        
        # Use special key for no-date sequences
        key = ('NO_DATE', 0, seq_type, group_num)
        sequence_groups[key] = [f[0] for f in group_files]
        stats['sequences_found'] += 1
        stats['sequences_no_date'] = stats.get('sequences_no_date', 0) + 1
    
    print(f"  Found {stats['sequences_found']} sequences")
    if stats.get('sequences_no_date', 0) > 0:
        print(f"  ({stats['sequences_no_date']} sequences without date metadata - will go to NO_DATE folder)")
    
    # ========================================================================
    # PHASE 4: Separate regular files from sequence files
    # ========================================================================
    sequence_filepaths = set()
    for filepaths in sequence_groups.values():
        sequence_filepaths.update(filepaths)
    
    regular_files = [f for f in all_files if f.filepath not in sequence_filepaths]
    
    stats['total'] = len(regular_files) + len(sequence_filepaths)
    tx_log.total_files = stats['total']
    
    print(f"\nFound {len(regular_files)} regular files")
    print(f"Found {len(sequence_filepaths)} sequence files in {stats['sequences_found']} sequences")
    print(f"Total: {stats['total']} files\n")
    
    if stats['total'] == 0:
        print("No media files found!")
        return stats
    
    # ========================================================================
    # PHASE 5: Process files
    # ========================================================================
    progress = ProgressTracker(stats['total'])
    
    # Process regular files
    for file_info in regular_files:
        filepath = file_info.filepath
        file_date = file_info.file_date
        
        # Skip if this is a secondary file (will be handled with its primary)
        if filepath in secondary_to_primary:
            continue
        
        if file_date is None:
            stats['no_date'] += 1
            no_date_files.append(filepath)
            dest_folder = dest_path / "NO_DATE"
        else:
            dest_folder = dest_path / str(file_date.year) / f"{file_date.month:02d}"
        
        dest_file = dest_folder / filepath.name
        
        # Check for duplicates — exact path first, then index
        skip = False
        skip_reason = None

        if dest_file.exists():
            is_identical, reason = files_are_identical(filepath, dest_file)
            if is_identical:
                stats['skipped_duplicate'] += 1
                skip = True
                skip_reason = "duplicate_exact_path"
            else:
                if not dry_run:
                    dest_file = get_unique_destination(dest_file)
                    stats['skipped_collision'] += 1
        elif dest_index:
            # File not at expected path — check if it exists elsewhere on the drive
            found_at = find_in_destination_index(filepath, dest_index)
            if found_at:
                stats['skipped_duplicate'] += 1
                skip = True
                skip_reason = f"duplicate_found_at:{found_at}"
        
        if not dry_run and not skip:
            try:
                dest_folder.mkdir(parents=True, exist_ok=True)
                result = transfer_file_with_retry(filepath, dest_file, operation, verify=verify)
                result.date_source = file_info.date_source
                result.file_size = file_info.file_size
                
                if result.success:
                    stats['transferred'] += 1
                    if result.verified:
                        stats['verified'] += 1
                    elif verify:
                        stats['verification_failures'] += 1
                    
                    # Transfer RAW/paired file alongside primary
                    if filepath in primary_to_secondary:
                        pair_path = primary_to_secondary[filepath]
                        pair_dest = get_pair_destination(dest_file, pair_path)
                        pair_skip = False
                        if pair_dest.exists():
                            is_identical, _ = files_are_identical(pair_path, pair_dest)
                            if is_identical:
                                pair_skip = True
                            else:
                                pair_dest = get_unique_destination(pair_dest)
                        if not pair_skip:
                            pair_result = transfer_file_with_retry(pair_path, pair_dest, operation, verify=verify)
                            pair_result.date_source = file_info.date_source
                            if pair_result.success:
                                stats['pairs_transferred'] += 1
                            tx_log.add_transfer(pair_result)
                    
                    # Transfer sidecar files (LRF, SRT) paired to this file
                    transfer_sidecars(
                        filepath, dest_file, sidecar_map,
                        operation, verify, stats, failed_files, tx_log
                    )
                else:
                    stats['errors'] += 1
                    failed_files.append((filepath.name, result.error))
                    print(f"\nERROR: {filepath.name}: {result.error}")
                
                tx_log.add_transfer(result)
                
            except Exception as e:
                stats['errors'] += 1
                failed_files.append((filepath.name, str(e)))
                print(f"\nERROR: {filepath.name}: {e}")
                tx_log.add_transfer(TransferResult(
                    source=filepath,
                    destination=dest_file,
                    operation=operation,
                    success=False,
                    error=str(e),
                    date_source=file_info.date_source,
                    file_size=file_info.file_size
                ))
        elif skip:
            tx_log.add_transfer(TransferResult(
                source=filepath,
                destination=dest_file,
                operation=operation,
                success=True,
                skipped=True,
                skip_reason=skip_reason
            ))
        
        progress.update(filename=filepath.name)
    
    # Process sequence files
    for (year, month, seq_type, group_num), filepaths in sorted(sequence_groups.items()):
        # Create sequence folder name
        folder_names = {
            'PANO': f"Panorama{group_num}",
            'HYPERLAPSE': f"Hyperlapse{group_num}",
            'TIMELAPSE': f"Timelapse{group_num}",
            'BURST': f"Burst{group_num}",
            'RAW_BURST': f"RAW_Burst{group_num}"
        }
        folder_name = folder_names.get(seq_type, f"{seq_type}{group_num}")
        
        # Handle NO_DATE sequences
        if year == 'NO_DATE':
            dest_folder = dest_path / "NO_DATE" / folder_name
        else:
            dest_folder = dest_path / str(year) / f"{month:02d}" / folder_name
        
        for filepath in filepaths:
            dest_file = dest_folder / filepath.name
            seq_label = f"{seq_type}/{filepath.parent.name} -> {folder_name}"
            
            skip = False
            skip_reason = None

            if dest_file.exists():
                is_identical, reason = files_are_identical(filepath, dest_file)
                if is_identical:
                    stats['skipped_duplicate'] += 1
                    skip = True
                    skip_reason = "duplicate_exact_path"
                else:
                    if not dry_run:
                        dest_file = get_unique_destination(dest_file)
                        stats['skipped_collision'] += 1
            elif dest_index:
                found_at = find_in_destination_index(filepath, dest_index)
                if found_at:
                    stats['skipped_duplicate'] += 1
                    skip = True
                    skip_reason = f"duplicate_found_at:{found_at}"
            
            if not dry_run and not skip:
                try:
                    dest_folder.mkdir(parents=True, exist_ok=True)
                    result = transfer_file_with_retry(filepath, dest_file, operation, verify=verify)
                    result.sequence_group = seq_label
                    result.file_size = filepath.stat().st_size
                    
                    if result.success:
                        stats['transferred'] += 1
                        if result.verified:
                            stats['verified'] += 1
                        elif verify:
                            stats['verification_failures'] += 1
                        
                        # Transfer RAW/paired file alongside primary
                        if filepath in primary_to_secondary:
                            pair_path = primary_to_secondary[filepath]
                            pair_dest = get_pair_destination(dest_file, pair_path)
                            pair_skip = False
                            if pair_dest.exists():
                                is_identical, _ = files_are_identical(pair_path, pair_dest)
                                if is_identical:
                                    pair_skip = True
                                else:
                                    pair_dest = get_unique_destination(pair_dest)
                            if not pair_skip:
                                pair_result = transfer_file_with_retry(pair_path, pair_dest, operation, verify=verify)
                                pair_result.sequence_group = seq_label
                                if pair_result.success:
                                    stats['pairs_transferred'] += 1
                                tx_log.add_transfer(pair_result)
                        
                        # Transfer sidecar files (LRF, SRT) paired to this file
                        transfer_sidecars(
                            filepath, dest_file, sidecar_map,
                            operation, verify, stats, failed_files, tx_log,
                            sequence_group=seq_label
                        )
                    else:
                        stats['errors'] += 1
                        failed_files.append((filepath.name, result.error))
                        print(f"\nERROR: {filepath.name}: {result.error}")
                    
                    tx_log.add_transfer(result)
                    
                except Exception as e:
                    stats['errors'] += 1
                    failed_files.append((filepath.name, str(e)))
                    print(f"\nERROR: {filepath.name}: {e}")
                    tx_log.add_transfer(TransferResult(
                        source=filepath,
                        destination=dest_file,
                        operation=operation,
                        success=False,
                        error=str(e),
                        sequence_group=seq_label,
                        file_size=filepath.stat().st_size
                    ))
            elif skip:
                tx_log.add_transfer(TransferResult(
                    source=filepath,
                    destination=dest_file,
                    operation=operation,
                    success=True,
                    skipped=True,
                    skip_reason=skip_reason,
                    sequence_group=seq_label
                ))
            
            progress.update(filename=filepath.name)
    
    # ========================================================================
    # Summary
    # ========================================================================
    print(f"\n{'='*70}")
    print(f"SUMMARY:")
    print(f"  Total media files: {stats['total']}")
    print(f"  DJI sequences found: {stats['sequences_found']}")
    if stats.get('sequences_no_date', 0) > 0:
        print(f"    (includes {stats['sequences_no_date']} sequences without dates)")
    print(f"  File pairs found: {stats['file_pairs']}")
    
    if not dry_run:
        op_text = "copied" if operation == "copy" else "moved"
        print(f"  Successfully {op_text}: {stats['transferred']}")
        if stats['pairs_transferred'] > 0:
            print(f"    + {stats['pairs_transferred']} RAW/paired files")
        if stats['sidecars_transferred'] > 0:
            print(f"    + {stats['sidecars_transferred']} sidecar files (LRF/SRT)")
        if stats['sidecars_skipped'] > 0:
            print(f"    ({stats['sidecars_skipped']} sidecars already at destination)")
        print(f"  Skipped (duplicates): {stats['skipped_duplicate']}")
        print(f"  Renamed (collisions): {stats['skipped_collision']}")
        print(f"  Errors: {stats['errors']}")
        if verify:
            print(f"  Verified: {stats['verified']}")
            if stats['verification_failures'] > 0:
                print(f"  Verification failures: {stats['verification_failures']}")
    else:
        op_text = "copy" if operation == "copy" else "move"
        print(f"  Would {op_text}: {stats['total'] - stats['skipped_duplicate']}")
        print(f"  Would skip (duplicates): {stats['skipped_duplicate']}")
    
    print(f"  Files without date: {stats['no_date']}")
    print(f"{'='*70}\n")
    
    if stats['sequences_found'] > 0:
        print(f"DJI sequences organized:")
        for (year, month, seq_type, group_num), filepaths in sorted(sequence_groups.items()):
            folder_names = {
                'PANO': f"Panorama{group_num}",
                'HYPERLAPSE': f"Hyperlapse{group_num}",
                'TIMELAPSE': f"Timelapse{group_num}",
                'BURST': f"Burst{group_num}",
                'RAW_BURST': f"RAW_Burst{group_num}"
            }
            folder_name = folder_names.get(seq_type, f"{seq_type}{group_num}")
            
            if year == 'NO_DATE':
                print(f"  NO_DATE/{folder_name}/ ({len(filepaths)} files)")
            else:
                print(f"  {year}/{month:02d}/{folder_name}/ ({len(filepaths)} files)")
    
    if stats['no_date'] > 0:
        print(f"\nNOTE: {stats['no_date']} files will go to NO_DATE folder")
    
    # Update transaction log stats
    tx_log.transferred = stats['transferred']
    tx_log.skipped = stats['skipped_duplicate']
    tx_log.errors = stats['errors']
    tx_log.verified = stats['verified']
    tx_log.verification_failures = stats['verification_failures']
    
    # Write transaction log
    if not dry_run and write_log:
        log_path = tx_log.save(dest_path)
        if log_path:
            print(f"\n📋 Transaction log saved: {log_path}")
    
    # Write error log if there were failures
    if not dry_run and failed_files:
        log_path = dest_path / f"FAILED_TRANSFERS_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        try:
            with open(log_path, 'w') as log:
                log.write(f"Failed transfers log - {datetime.now()}\n")
                log.write(f"Operation: {operation}\n")
                log.write(f"Source: {source_path}\n")
                log.write(f"Destination: {dest_path}\n")
                log.write("="*70 + "\n\n")
                for filename, error in failed_files:
                    log.write(f"{filename}\n  Error: {error}\n\n")
            print(f"\n⚠️  Error log written to: {log_path}")
        except Exception as e:
            print(f"\n⚠️  Could not write error log: {e}")
    
    return stats


# ============================================================================
# Main Entry Point
# ============================================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="Organize photos and videos into a YYYY/MM folder structure."
    )
    parser.add_argument("source", nargs="?", default=SOURCE_DIR,
                        help="Source folder (or set PHOTO_SOURCE)")
    parser.add_argument("dest", nargs="?", default=DEST_DIR,
                        help="Destination folder (or set PHOTO_DEST)")
    parser.add_argument("--mode", choices=("copy", "move"), default=OPERATION_MODE,
                        help="Copy files or move them (default: copy)")
    parser.add_argument("--org-mode",
                        choices=("metadata_first", "oldest", "created", "modified"),
                        default=ORGANIZATION_MODE,
                        help="How to determine each file's date")
    parser.add_argument("--sequence-window", type=int, default=SEQUENCE_TIME_WINDOW,
                        help="DJI sequence grouping window in minutes")
    parser.add_argument("--skip-folder", action="append", default=[], metavar="NAME",
                        help="Folder name to skip (repeatable)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip post-transfer integrity verification")
    parser.add_argument("--no-log", action="store_true",
                        help="Do not write the JSON transaction log")
    parser.add_argument("--no-dest-index", action="store_true",
                        help="Skip building the destination filename index")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run the scan and report only; never transfer files")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompts (review the dry run first)")
    return parser.parse_args()


def main():
    global SOURCE_DIR, DEST_DIR, OPERATION_MODE, ORGANIZATION_MODE
    global SEQUENCE_TIME_WINDOW, SKIP_FOLDERS, VERIFY_AFTER_TRANSFER
    global WRITE_TRANSACTION_LOG, USE_DESTINATION_INDEX

    args = parse_args()
    SOURCE_DIR = args.source
    DEST_DIR = args.dest
    OPERATION_MODE = args.mode
    ORGANIZATION_MODE = args.org_mode
    SEQUENCE_TIME_WINDOW = args.sequence_window
    SKIP_FOLDERS = list(dict.fromkeys(SKIP_FOLDERS + args.skip_folder))
    VERIFY_AFTER_TRANSFER = not args.no_verify
    WRITE_TRANSACTION_LOG = not args.no_log
    USE_DESTINATION_INDEX = not args.no_dest_index

    if not SOURCE_DIR or not DEST_DIR:
        raise SystemExit(
            "ERROR: source and destination are required.\n"
            "Pass them as arguments or set PHOTO_SOURCE and PHOTO_DEST."
        )

    print("Photo & Video Organizer v5.0")
    print("="*70)
    print("Improvements: Efficient hashing, fixed DJI grouping, verification,")
    print("              collision handling, expanded patterns, transaction logs")
    print("="*70)
    
    if OPERATION_MODE not in ["copy", "move"]:
        print(f"\nERROR: Invalid OPERATION_MODE: {OPERATION_MODE}")
        print("Must be 'copy' or 'move'")
        return
    
    print(f"\nCurrent Configuration:")
    print(f"  Source: {SOURCE_DIR}")
    print(f"  Destination: {DEST_DIR}")
    print(f"  Operation: {OPERATION_MODE.upper()}")
    print(f"  Organization: {ORGANIZATION_MODE}")
    print(f"  Sequence grouping window: {SEQUENCE_TIME_WINDOW} minutes")
    print(f"  Verification: {'enabled' if VERIFY_AFTER_TRANSFER else 'disabled'}")
    print(f"  Transaction log: {'enabled' if WRITE_TRANSACTION_LOG else 'disabled'}")
    print(f"  Skip folders: {SKIP_FOLDERS if SKIP_FOLDERS else 'none'}")
    print(f"  Destination index: {'enabled' if USE_DESTINATION_INDEX else 'disabled'}")
    
    if OPERATION_MODE == "move":
        print("\n" + "!"*70)
        print("⚠️  WARNING: MOVE OPERATION SELECTED")
        print("!"*70)
        print("  MOVE will DELETE files from source after copying!")
        print("  This operation CANNOT be undone.")
        print("  Consider using COPY instead for safety.")
        print("!"*70)
        
        if args.yes:
            confirm = "MOVE"
        else:
            confirm = input("\nAre you ABSOLUTELY SURE you want to MOVE files? (type 'MOVE' to confirm): ").strip()
        if confirm != "MOVE":
            print("\nOperation cancelled. Change OPERATION_MODE to 'copy' for safer operation.")
            return
    
    print("\n" + "="*70)
    print("PRE-FLIGHT CHECKS")
    print("="*70)
    
    issues, warnings = check_system_requirements(SOURCE_DIR, DEST_DIR)
    
    if issues:
        print("\n❌ CRITICAL ISSUES:")
        for issue in issues:
            print(f"  - {issue}")
        print("\nPlease fix these issues before proceeding.")
        return
    
    if warnings:
        print("\n⚠️  WARNINGS:")
        for warning in warnings:
            print(f"  - {warning}")
    
    print("\n✓ Basic checks passed")
    
    print("\n" + "="*70)
    print("IMPORTANT REMINDERS")
    print("="*70)
    print("  1. Ensure Mac won't sleep (System Settings → Energy)")
    print("  2. Ensure external drives won't sleep/disconnect")
    print("  3. Use powered USB connections (not hubs if possible)")
    print("  4. Don't disconnect drives during operation")
    
    if not args.yes:
        input("\nPress Enter when ready to proceed...")
    
    print("\n" + "="*70)
    print("STEP 1: DRY RUN")
    print("="*70)
    
    stats = organize_media(
        SOURCE_DIR, DEST_DIR, ORGANIZATION_MODE, OPERATION_MODE,
        dry_run=True, verify=VERIFY_AFTER_TRANSFER, write_log=False,
        skip_folders=SKIP_FOLDERS, use_dest_index=USE_DESTINATION_INDEX
    )
    
    if stats is None or stats['total'] == 0:
        return

    if args.dry_run:
        print("\nDry run complete. No files were changed.")
        return
    
    print("\n" + "="*70)
    print(f"STEP 2: ACTUAL {OPERATION_MODE.upper()}")
    print("="*70)
    op_text = "copy" if OPERATION_MODE == "copy" else "move"
    print(f"\nReady to {op_text} {stats['total'] - stats['skipped_duplicate']} files")
    print(f"(Skipping {stats['skipped_duplicate']} duplicates)")
    
    if OPERATION_MODE == "move":
        print("\n⚠️  WARNING: Files will be REMOVED from source!")
    
    if args.yes:
        response = "yes"
    else:
        response = input(f"\nProceed with {OPERATION_MODE}? (yes/no): ").strip().lower()
    
    if response == 'yes':
        print(f"\nStarting {OPERATION_MODE} operation...")
        print("Keep your Mac awake and connected to power.\n")
        
        # Prevent sleep on macOS
        caffeinate_process = None
        if sys.platform == "darwin":
            caffeinate_process = subprocess.Popen(['caffeinate', '-d'])

        try:
            organize_media(
                SOURCE_DIR, DEST_DIR, ORGANIZATION_MODE, OPERATION_MODE,
                dry_run=False, verify=VERIFY_AFTER_TRANSFER, write_log=WRITE_TRANSACTION_LOG,
                skip_folders=SKIP_FOLDERS, use_dest_index=USE_DESTINATION_INDEX
            )
            print(f"\n✓ Organization complete!")
            if OPERATION_MODE == "copy":
                print("\nYou can run this script again anytime - it will skip duplicates.")
        finally:
            if caffeinate_process:
                caffeinate_process.terminate()
    else:
        print("\nOperation cancelled.")


if __name__ == "__main__":
    main()