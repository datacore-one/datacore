#!/usr/bin/env python3
"""
Nightshift Report Archival System

Archives nightshift execution reports and summaries from inbox to
structured archive directories by month.

Usage:
    python nightshift_archival.py --space 0-personal
    python nightshift_archival.py --all-spaces
    python nightshift_archival.py --dry-run

Safety features:
- Deletion guards: abort if >N files would be archived
- Silent failure detection: loud errors on first occurrence
- Atomic move operations
"""

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
import yaml

# Constants
DEFAULT_RETENTION_DAYS = 30
MAX_FILES_PER_RUN = 1000  # Safety guard: abort if archiving more than this


def load_settings(data_dir):
    """Load archival settings from .datacore/settings.yaml and local overrides."""
    settings_path = Path(data_dir) / ".datacore" / "settings.yaml"
    local_settings_path = Path(data_dir) / ".datacore" / "settings.local.yaml"

    settings = {
        "nightshift": {
            "archival": {
                "enabled": True,
                "retention_days": DEFAULT_RETENTION_DAYS,
                "summary_retention_days": 90,
                "archive_base": "4-archive/nightshift",
                "log_operations": True
            }
        }
    }

    # Load base settings
    if settings_path.exists():
        with open(settings_path, 'r') as f:
            base_settings = yaml.safe_load(f) or {}
            # Merge nightshift.archival if it exists
            if "nightshift" in base_settings:
                if "archival" in base_settings["nightshift"]:
                    settings["nightshift"]["archival"].update(base_settings["nightshift"]["archival"])

    # Load local overrides
    if local_settings_path.exists():
        with open(local_settings_path, 'r') as f:
            local_settings = yaml.safe_load(f) or {}
            # Merge nightshift.archival if it exists
            if "nightshift" in local_settings:
                if "archival" in local_settings["nightshift"]:
                    settings["nightshift"]["archival"].update(local_settings["nightshift"]["archival"])

    return settings["nightshift"]["archival"]


def extract_date_from_filename(filename):
    """
    Extract date from nightshift filename or return None.

    Expected patterns:
    - nightshift-exec-YYYY-MM-DD-HHMMSS-task.md
    - nightshift-summary-YYYY-MM-DD.md
    - nightshift-LXXXX-description.md (fallback to mtime)
    """
    # Try standard patterns first
    match = re.match(r'nightshift-(?:exec|summary)-(\d{4})-(\d{2})-(\d{2})', filename)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            return None

    # No valid date in filename
    return None


def determine_file_age(filepath):
    """
    Determine file age in days.

    Returns tuple: (age_in_days, date_used, source)
    source = "filename" or "mtime"
    """
    file_date = extract_date_from_filename(filepath.name)

    if file_date:
        age_days = (datetime.now() - file_date).days
        return age_days, file_date, "filename"
    else:
        # Fallback to file modification time
        mtime = filepath.stat().st_mtime
        file_date = datetime.fromtimestamp(mtime)
        age_days = (datetime.now() - file_date).days
        return age_days, file_date, "mtime"


def get_archive_path(space_path, file_date, archive_base):
    """
    Determine archive path for a file based on its date.

    Returns Path: [space]/4-archive/nightshift/YYYY-MM/
    """
    year_month = file_date.strftime("%Y-%m")
    archive_dir = Path(space_path) / archive_base / year_month
    return archive_dir


def is_summary_file(filename):
    """Check if filename is a summary report (to apply different retention)."""
    return "summary" in filename.lower()


def archive_nightshift_reports(space_path, retention_days=30, summary_retention_days=None,
                               archive_base="4-archive/nightshift", dry_run=False,
                               verbose=False, log_operations=True):
    """
    Archive nightshift reports older than retention_days.

    Args:
        space_path: Path to space (e.g., "0-personal")
        retention_days: Days to keep execution logs in inbox
        summary_retention_days: Days to keep summaries (None = same as retention_days)
        archive_base: Base path for archives within space
        dry_run: If True, only preview operations without moving files
        verbose: Detailed logging
        log_operations: Log each operation

    Returns:
        dict: Statistics about archival operation
    """
    space_path = Path(space_path)
    inbox_path = space_path / "0-inbox"

    if not inbox_path.exists():
        print(f"Warning: Inbox not found at {inbox_path}", file=sys.stderr)
        return {"archived": 0, "kept": 0, "errors": 0}

    # If summary_retention_days not specified, use same as regular retention
    if summary_retention_days is None:
        summary_retention_days = retention_days

    # Find all nightshift files in inbox and subdirectories
    nightshift_files = []
    for pattern in ["nightshift-*.md", "*/nightshift-*.md", "*/*/nightshift-*.md"]:
        nightshift_files.extend(inbox_path.glob(pattern))

    # Remove duplicates and sort
    nightshift_files = sorted(set(nightshift_files))

    if verbose:
        print(f"\nScanning {space_path.name}/0-inbox/")
        print(f"Found {len(nightshift_files)} nightshift files")

    # Safety guard: check if we're about to archive too many files
    to_archive = []
    to_keep = []
    errors = []

    for filepath in nightshift_files:
        try:
            age_days, file_date, source = determine_file_age(filepath)

            # Apply different retention for summaries
            is_summary = is_summary_file(filepath.name)
            applicable_retention = summary_retention_days if is_summary else retention_days

            if age_days > applicable_retention:
                to_archive.append((filepath, file_date, age_days, source))
            else:
                to_keep.append((filepath, age_days))

        except Exception as e:
            errors.append((filepath, str(e)))
            print(f"Error processing {filepath.name}: {e}", file=sys.stderr)

    # Safety guard: abort if archiving more than MAX_FILES_PER_RUN
    if len(to_archive) > MAX_FILES_PER_RUN:
        print(f"\n❌ SAFETY ABORT: Would archive {len(to_archive)} files (max: {MAX_FILES_PER_RUN})",
              file=sys.stderr)
        print(f"This seems abnormal. Please investigate before proceeding.", file=sys.stderr)
        print(f"Use --retention-days to adjust retention policy if intentional.", file=sys.stderr)
        return {"archived": 0, "kept": len(to_keep), "errors": len(errors), "aborted": True}

    # Archive files
    archived_count = 0

    for filepath, file_date, age_days, source in to_archive:
        try:
            archive_dir = get_archive_path(space_path, file_date, archive_base)
            dest_path = archive_dir / filepath.name

            if dry_run:
                rel_path = filepath.relative_to(space_path)
                archive_rel = dest_path.relative_to(space_path)
                print(f"[DRY RUN] Would archive: {rel_path} → {archive_rel} (age: {age_days}d, from {source})")
            else:
                # Create archive directory if it doesn't exist
                archive_dir.mkdir(parents=True, exist_ok=True)

                # Move file (atomic operation)
                shutil.move(str(filepath), str(dest_path))
                archived_count += 1

                if log_operations or verbose:
                    rel_path = filepath.relative_to(space_path)
                    archive_rel = dest_path.relative_to(space_path)
                    print(f"Archived: {rel_path} → {archive_rel} (age: {age_days}d)")

        except Exception as e:
            errors.append((filepath, str(e)))
            print(f"❌ Error archiving {filepath.name}: {e}", file=sys.stderr)

    # Summary
    stats = {
        "archived": archived_count,
        "kept": len(to_keep),
        "errors": len(errors),
        "aborted": False
    }

    if not dry_run and (log_operations or verbose):
        print(f"\n{'='*60}")
        print(f"Archival Summary for {space_path.name}:")
        print(f"  Archived: {archived_count} files (>{retention_days}d)")
        print(f"  Kept in inbox: {len(to_keep)} files (<={retention_days}d)")
        if errors:
            print(f"  Errors: {len(errors)}")
        print(f"{'='*60}\n")

    return stats


def find_all_spaces(data_dir):
    """
    Find all spaces in Data directory.

    Returns list of space paths matching pattern: N-name/
    """
    data_path = Path(data_dir)
    spaces = []

    # Pattern: number-name (e.g., 0-personal, 1-datafund, 2-datacore)
    for item in data_path.iterdir():
        if item.is_dir() and re.match(r'^\d+-', item.name):
            spaces.append(item)

    return sorted(spaces)


def main():
    parser = argparse.ArgumentParser(
        description="Archive nightshift reports from inbox to structured archives",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run for all spaces
  python nightshift_archival.py --all-spaces --dry-run

  # Archive personal space with 60-day retention
  python nightshift_archival.py --space 0-personal --retention-days 60

  # Archive all spaces (uses settings from settings.yaml)
  python nightshift_archival.py --all-spaces
        """
    )

    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / "Data"),
        help="Data directory (default: ~/Data)"
    )
    parser.add_argument(
        "--space",
        help="Space to archive (e.g., 0-personal, 1-datafund)"
    )
    parser.add_argument(
        "--all-spaces",
        action="store_true",
        help="Archive all spaces"
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        help="Override retention period for execution logs (default from settings)"
    )
    parser.add_argument(
        "--summary-retention-days",
        type=int,
        help="Override retention period for summaries (default from settings)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview operations without moving files"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Detailed logging"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress operation logging"
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.space and not args.all_spaces:
        parser.error("Must specify either --space or --all-spaces")

    if args.space and args.all_spaces:
        parser.error("Cannot specify both --space and --all-spaces")

    # Load settings
    settings = load_settings(args.data_dir)

    # Check if archival is enabled
    if not settings.get("enabled", True) and not args.dry_run:
        print("Archival is disabled in settings. Use --dry-run to preview or enable in settings.yaml")
        sys.exit(0)

    # Override settings with CLI args
    retention_days = args.retention_days or settings.get("retention_days", DEFAULT_RETENTION_DAYS)
    summary_retention_days = args.summary_retention_days or settings.get("summary_retention_days", 90)
    archive_base = settings.get("archive_base", "4-archive/nightshift")
    log_operations = not args.quiet and settings.get("log_operations", True)

    # Determine spaces to process
    if args.all_spaces:
        spaces = find_all_spaces(args.data_dir)
        if not spaces:
            print(f"No spaces found in {args.data_dir}", file=sys.stderr)
            sys.exit(1)
    else:
        space_path = Path(args.data_dir) / args.space
        if not space_path.exists():
            print(f"Error: Space not found: {space_path}", file=sys.stderr)
            sys.exit(1)
        spaces = [space_path]

    if args.dry_run:
        print(f"\n{'='*60}")
        print("DRY RUN MODE - No files will be moved")
        print(f"{'='*60}\n")

    # Process each space
    total_stats = {"archived": 0, "kept": 0, "errors": 0}

    for space_path in spaces:
        stats = archive_nightshift_reports(
            space_path=space_path,
            retention_days=retention_days,
            summary_retention_days=summary_retention_days,
            archive_base=archive_base,
            dry_run=args.dry_run,
            verbose=args.verbose,
            log_operations=log_operations
        )

        if stats.get("aborted"):
            print(f"\nArchival aborted for {space_path.name}. Stopping.", file=sys.stderr)
            sys.exit(1)

        total_stats["archived"] += stats["archived"]
        total_stats["kept"] += stats["kept"]
        total_stats["errors"] += stats["errors"]

    # Final summary for multi-space runs
    if args.all_spaces and (log_operations or args.verbose):
        print(f"\n{'='*60}")
        print(f"Total across all spaces:")
        print(f"  Archived: {total_stats['archived']} files")
        print(f"  Kept: {total_stats['kept']} files")
        if total_stats['errors']:
            print(f"  Errors: {total_stats['errors']}")
        print(f"{'='*60}\n")

    # Exit with error code if there were errors
    sys.exit(1 if total_stats['errors'] > 0 else 0)


if __name__ == "__main__":
    main()
