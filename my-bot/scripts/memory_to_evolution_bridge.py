#!/usr/bin/env python3
"""
Memory → Evolution Bridge

Scans memory/*.md files for entries that qualify as evolution signals
but have no corresponding entry in the Evolution Backlog.

Usage:
  python memory_to_evolution_bridge.py [--auto-capture] [--days 30]

The bridge looks at:
  - type: project   → internal evolution signal
  - type: feedback  → process improvement signal
  - type: reference → external signal (papers, tools, etc.)

Cross-references with backlog.json on:
  - Exact title match
  - Memory name slug appearing in backlog title or description
  - Similar creation dates (±7 days)

Output: JSON report of gaps + optional auto-capture via evolution_capture.py
"""

import json
import os
import re
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ──
MEMORY_DIR = Path(os.environ.get(
    "FM_MEMORY_DIR",
    r"C:\Users\USERNAME\.claude\projects\d--FM-trade\memory"
))
BACKLOG_PATH = Path(os.environ.get(
    "FM_BACKLOG_PATH",
    r"D:\AgentAlpha\my-bot\data\evolution\backlog.json"
))
CAPTURE_SCRIPT = Path(os.environ.get(
    "FM_CAPTURE_SCRIPT",
    r"D:\AgentAlpha\my-bot\scripts\evolution_capture.py"
))


def parse_frontmatter(filepath: Path) -> dict | None:
    """Parse YAML frontmatter from a markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8")
        # Match --- ... --- frontmatter block
        m = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
        if not m:
            return None
        fm = yaml.safe_load(m.group(1))
        if not isinstance(fm, dict):
            return None
        # type is nested under metadata in Claude memory frontmatter
        meta = fm.get("metadata", {})
        if isinstance(meta, dict) and "type" in meta:
            fm["type"] = meta["type"]
        return fm
    except Exception:
        return None


MEM_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')


def slugify(text: str) -> str:
    """Normalize a string for fuzzy matching."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def extract_date_from_text(text: str) -> str | None:
    """Extract first YYYY-MM-DD date from a string."""
    m = MEM_DATE_RE.search(text)
    return m.group(1) if m else None


def read_memory_body(filepath: Path, max_chars: int = 300) -> str:
    """Read memory body (after frontmatter) for content matching."""
    try:
        text = filepath.read_text(encoding="utf-8")
        # Strip frontmatter
        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', text, flags=re.DOTALL)
        return body[:max_chars]
    except Exception:
        return ""


def find_matching_backlog_entry(memory: dict, backlog_entries: list[dict]) -> dict | None:
    """
    Check if a memory file already has a corresponding evolution entry.
    Primary: exact memory_ref match (deterministic).
    Fallback: date proximity + keyword overlap (heuristic).
    """
    mem_name = memory.get("name", "")

    # === Primary: memory_ref exact match ===
    for entry in backlog_entries:
        if entry.get("memory_ref") == mem_name:
            return entry

    # === Fallback: heuristic matching ===
    mem_desc = memory.get("description", "")
    mem_body = memory.get("body", "")
    mem_date = memory.get("date_in_name")
    mem_keywords = set(mem_name.replace('-', ' ').split())

    # Combine all memory text for keyword matching
    mem_all_text = f"{mem_name} {mem_desc} {mem_body}".lower()

    best_score = 0
    best_entry = None

    for entry in backlog_entries:
        score = 0
        entry_title = entry.get("title", "")
        entry_desc = entry.get("description", "")
        entry_created = entry.get("created", "")

        # Level 1: Direct slug match in title/source (high confidence)
        if mem_name and mem_name in slugify(entry_title + entry.get("source", "")):
            return entry  # immediate match

        # Level 2: Date proximity (strongest heuristic)
        if mem_date and entry_created:
            days_apart = abs(
                (datetime.strptime(mem_date, "%Y-%m-%d") -
                 datetime.strptime(entry_created, "%Y-%m-%d")).days
            )
            if days_apart <= 3:
                score += 3
            elif days_apart <= 7:
                score += 2
            elif days_apart <= 14:
                score += 1

        # Level 3: Content keyword overlap (Jaccard on significant words)
        entry_text = (entry_title + " " + entry_desc).lower()
        # Extract significant words (3+ chars, filter stopwords)
        stopwords = {"the", "and", "for", "with", "that", "this", "from", "was", "are",
                     "has", "had", "not", "but", "all", "can", "its", "one", "our",
                     "been", "have", "were", "will", "would", "could", "should"}
        mem_words = {w for w in re.findall(r'[a-z0-9]{3,}', mem_all_text) if w not in stopwords}
        entry_words = {w for w in re.findall(r'[a-z0-9]{3,}', entry_text) if w not in stopwords}
        if mem_words and entry_words:
            overlap = len(mem_words & entry_words)
            jaccard = overlap / len(mem_words | entry_words) if mem_words | entry_words else 0
            if jaccard > 0.3:
                score += 3
            elif jaccard > 0.15:
                score += 2
            elif jaccard > 0.05:
                score += 1

        # Level 4: Memory keywords appear in entry (loose match)
        entry_slug = slugify(entry_title + " " + entry_desc)
        kw_matches = sum(1 for kw in mem_keywords if kw in entry_slug)
        if kw_matches >= 3:
            score += 2
        elif kw_matches >= 1:
            score += 1

        if score > best_score:
            best_score = score
            best_entry = entry

    # Require score >= 3 for a match (date proximity alone = 1-3, needs some content overlap)
    if best_score >= 3:
        return best_entry

    return None


def scan_memory_files(days: int = 60) -> list[dict]:
    """Scan memory directory for qualifying .md files."""
    memories = []
    cutoff = datetime.now() - timedelta(days=days)

    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue

        fm = parse_frontmatter(f)
        if not fm:
            continue

        mem_type = fm.get("type", "")
        if mem_type not in ("project", "feedback", "reference"):
            continue

        # Check file modification time
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime < cutoff:
            continue

        memories.append({
            "file": str(f),
            "filename": f.name,
            "name": fm.get("name", ""),
            "description": fm.get("description", ""),
            "type": mem_type,
            "mtime": mtime.isoformat(),
            "date_in_name": extract_date_from_text(fm.get("name", "")),
            "body": read_memory_body(f),
        })

    return memories


def map_type_to_track(mem_type: str) -> str:
    """Map memory type to evolution track."""
    if mem_type == "project":
        return "internal"
    elif mem_type == "reference":
        return "external"
    elif mem_type == "feedback":
        return "internal"  # feedback is process improvement
    return "internal"


def map_type_to_severity(mem_type: str, description: str) -> str:
    """Heuristic severity mapping."""
    desc_lower = description.lower()
    # P0 keywords
    if any(kw in desc_lower for kw in ["p0", "critical", "blocking", "data error", "failed", "broken"]):
        return "P0"
    # P1 keywords
    if any(kw in desc_lower for kw in ["p1", "major", "significant", "degraded", "missing"]):
        return "P1"
    # P2 keywords
    if any(kw in desc_lower for kw in ["p2", "moderate", "improvement", "upgrade"]):
        return "P2"
    # Default
    if mem_type == "feedback":
        return "P2"
    return "P3"


def main():
    # Fix Windows GBK encoding for emoji output
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="Bridge memory files to evolution backlog")
    parser.add_argument("--auto-capture", action="store_true",
                        help="Auto-capture gaps via evolution_capture.py")
    parser.add_argument("--days", type=int, default=60,
                        help="Look back N days for memory files (default: 60)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON (for pipeline consumption)")
    args = parser.parse_args()

    # Load backlog
    if not BACKLOG_PATH.exists():
        print(f"ERROR: backlog.json not found at {BACKLOG_PATH}", file=sys.stderr)
        sys.exit(1)

    backlog = json.loads(BACKLOG_PATH.read_text(encoding="utf-8"))
    entries = backlog.get("entries", [])

    # Scan memories
    memories = scan_memory_files(args.days)

    # Find gaps
    gaps = []
    matched = []

    for mem in memories:
        match = find_matching_backlog_entry(mem, entries)
        if match:
            matched.append({
                "memory": mem["name"],
                "evolution_id": match["id"],
                "evolution_title": match["title"],
            })
        else:
            severity = map_type_to_severity(mem["type"], mem["description"])
            track = map_type_to_track(mem["type"])
            gaps.append({
                **mem,
                "track": track,
                "severity": severity,
            })

    # Output
    if args.json:
        result = {
            "scan_date": datetime.now().isoformat(),
            "total_memories": len(memories),
            "matched": len(matched),
            "gaps": len(gaps),
            "gap_details": gaps,
            "matched_details": matched,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"=== Memory → Evolution Bridge ===\n")
        print(f"Scanned: {len(memories)} memory files (last {args.days} days)")
        print(f"Matched: {len(matched)} already in backlog")
        print(f"Gaps:    {len(gaps)} without evolution entries\n")

        if matched:
            print("--- Already in Backlog ---")
            for m in matched:
                print(f"  [OK] {m['memory']} -> {m['evolution_id']}")

        if gaps:
            print("\n--- GAPS: Memory files without Evolution entries ---")
            for i, g in enumerate(gaps, 1):
                print(f"\n  [{i}] {g['name']}")
                print(f"      Type: {g['type']} | Track: {g['track']} | Severity: {g['severity']}")
                print(f"      File: {g['filename']}")
                if g['description']:
                    desc = g['description'][:100]
                    print(f"      Desc: {desc}...")
        else:
            print("\n[OK] No gaps found — all memory files have evolution entries.")

    # Auto-capture
    if args.auto_capture and gaps:
        import subprocess
        captured = 0
        for g in gaps:
            title = g['description'][:100] if g['description'] else g['name']
            cmd = [
                sys.executable, str(CAPTURE_SCRIPT), "capture",
                "--track", g['track'],
                "--source", f"Memory bridge: {g['filename']}",
                "--title", title,
                "--severity", g['severity'],
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                      encoding="utf-8", errors="replace")
                if result.returncode == 0:
                    captured += 1
                    print(f"  [OK] Captured: {g['name']}")
                else:
                    print(f"  [FAIL] Failed: {g['name']} — {result.stderr.strip()}")
            except Exception as e:
                print(f"  [FAIL] Error: {g['name']} — {e}")

        print(f"\nAuto-captured: {captured}/{len(gaps)} gaps")

    sys.exit(0 if not gaps else 1)


if __name__ == "__main__":
    main()