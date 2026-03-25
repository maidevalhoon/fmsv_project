"""
extract_reports.py
------------------
Extracts the most important information from each *_insights.txt report
and writes compact summary files to reports/summaries/.

What is kept:
  - Section 1: Circuit Overview (full)
  - Section 2: Fault Coverage Summary (full)
  - Section 3: Per-Fault Results — only TOP 10 HARDEST faults by decisions
  - Section 4: Key Insights for LLM Guidance Layer (full)
  - Section 5: Raw Data Table — top 10 hardest + top 10 slowest rows + stats row

What is dropped:
  - All the per-fault entries in Section 3 except the hardest 10
  - The full Section 5 raw-data table (only top rows kept)
"""

import os
import re
import sys

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
OUT_DIR     = os.path.join(REPORTS_DIR, "summaries")
os.makedirs(OUT_DIR, exist_ok=True)


# ── helpers ──────────────────────────────────────────────────────────────────

def split_sections(text: str) -> dict:
    """
    Split the report into named sections based on the numeric headers.
    Keys: '1', '2', '3', '4', '5', 'header'
    """
    sections = {}

    # Extract the banner header
    m = re.match(r"(=+.*?=+\n)", text, re.S)
    sections["header"] = m.group(0) if m else ""

    patterns = [
        ("1", r"1\. CIRCUIT OVERVIEW"),
        ("2", r"2\. FAULT COVERAGE SUMMARY"),
        ("3", r"3\. PER-FAULT DETAILED RESULTS"),
        ("4", r"4\. KEY INSIGHTS FOR LLM GUIDANCE LAYER"),
        ("5", r"5\. RAW DATA TABLE"),
    ]

    for i, (key, pat) in enumerate(patterns):
        next_pat = patterns[i + 1][1] if i + 1 < len(patterns) else r"=+\s*END OF REPORT"
        m = re.search(
            rf"({pat}.*?)(?={next_pat})",
            text, re.S
        )
        sections[key] = m.group(0).strip() if m else ""

    # Section 5 goes to end of file
    m = re.search(r"(5\. RAW DATA TABLE.*)", text, re.S)
    sections["5"] = m.group(0).strip() if m else ""

    return sections


def parse_fault_blocks(section3_text: str) -> list:
    """
    Parse individual fault blocks from Section 3.
    Returns list of dicts with: label, wire, gate, status, solve_ms, decisions, conflicts, propagations, vectors
    """
    block_pattern = re.compile(
        r"Fault #\d+: (\S+)\s+\(wire '([^']+)'\)\n"
        r".*?Driving gate\s+: (.+?)\n"
        r".*?Status\s+: (\S+)\n"
        r".*?Solve time\s+: ([\d.]+) ms\n"
        r".*?CNF size\s+: (\d+) vars, (\d+) clauses\n"
        r".*?Solver stats\s+: decisions=(\d+), conflicts=(\d+), propagations=(\d+)\n"
        r"(.*?)(?=\n  Fault #|\Z)",
        re.S
    )

    faults = []
    for m in block_pattern.finditer(section3_text):
        tv_text = m.group(11)
        # Extract test vector line if present
        tv_match = re.search(r"Test vector\s+: (.+)", tv_text)
        tv = tv_match.group(1).strip() if tv_match else "(none)"

        # Extract output diffs
        out_matches = re.findall(r"Output '([^']+)': good=(\d) +faulty=(\d)", tv_text)
        out_str = ", ".join(f"{net}: good={g} faulty={f}" for net, g, f in out_matches)

        faults.append({
            "label":        m.group(1),
            "wire":         m.group(2),
            "gate":         m.group(3).strip(),
            "status":       m.group(4),
            "solve_ms":     float(m.group(5)),
            "vars":         int(m.group(6)),
            "clauses":      int(m.group(7)),
            "decisions":    int(m.group(8)),
            "conflicts":    int(m.group(9)),
            "propagations": int(m.group(10)),
            "test_vector":  tv,
            "output_diff":  out_str or "(none)",
        })
    return faults


def parse_raw_table(section5_text: str) -> list:
    """
    Parse the Raw Data Table rows from Section 5.
    Returns list of dicts: fault, status, time_ms, decisions, conflicts, test_vector
    """
    rows = []
    for m in re.finditer(
        r"  (\S+SA[01]@\S+)\s+(\S+)\s+([\d.]+)\s+(\d+|-)\s+(\d+|-)\s+(\{.*?\})",
        section5_text
    ):
        rows.append({
            "fault":       m.group(1),
            "status":      m.group(2),
            "time_ms":     float(m.group(3)),
            "decisions":   int(m.group(4)) if m.group(4).isdigit() else 0,
            "conflicts":   int(m.group(5)) if m.group(5).isdigit() else 0,
            "test_vector": m.group(6),
        })
    return rows


def format_fault_block(f: dict, rank: int = None, reason: str = "") -> str:
    tag = f"  [{rank}] " if rank else "  "
    reason_tag = f"  ← {reason}" if reason else ""
    return (
        f"{tag}{f['label']}  (wire '{f['wire']}'){reason_tag}\n"
        f"    Gate      : {f['gate']}\n"
        f"    Status    : {f['status']}\n"
        f"    Solve time: {f['solve_ms']:.3f} ms\n"
        f"    CNF size  : {f['vars']} vars, {f['clauses']} clauses\n"
        f"    Decisions : {f['decisions']}  Conflicts: {f['conflicts']}  Props: {f['propagations']}\n"
        f"    Test vector: {f['test_vector']}\n"
        f"    Output diff: {f['output_diff']}\n"
    )


def compute_stats(faults: list) -> str:
    det = [f for f in faults if f["status"] == "DETECTABLE"]
    undet = [f for f in faults if f["status"] == "UNDETECTABLE"]
    if not det:
        return "  No detectable faults.\n"

    times = [f["solve_ms"] for f in det]
    decs  = [f["decisions"] for f in det]
    confs = [f["conflicts"] for f in det]

    return (
        f"  Detectable faults    : {len(det)}\n"
        f"  Undetectable faults  : {len(undet)}\n"
        f"  Coverage             : {len(det)/(len(det)+len(undet))*100:.1f}%\n\n"
        f"  Solve time  — avg: {sum(times)/len(times):.3f} ms  "
        f"max: {max(times):.3f} ms  min: {min(times):.3f} ms\n"
        f"  Decisions   — avg: {sum(decs)/len(decs):.1f}  "
        f"max: {max(decs)}  min: {min(decs)}\n"
        f"  Conflicts   — avg: {sum(confs)/len(confs):.1f}  "
        f"max: {max(confs)}  min: {min(confs)}\n"
    )


# ── main extractor ────────────────────────────────────────────────────────────

def extract(report_path: str, out_path: str, top_n: int = 10):
    print(f"  Processing: {os.path.basename(report_path)}")

    with open(report_path, encoding="utf-8") as f:
        text = f.read()

    sections = split_sections(text)

    faults      = parse_fault_blocks(sections.get("3", ""))
    raw_rows    = parse_raw_table(sections.get("5", ""))

    # top N by decisions (hardest for SAT solver)
    top_hard = sorted(faults, key=lambda x: x["decisions"], reverse=True)[:top_n]
    # top N by solve time (slowest)
    top_slow = sorted(faults, key=lambda x: x["solve_ms"],  reverse=True)[:top_n]
    # undetectable faults (redundant)
    redundant = [f for f in faults if f["status"] == "UNDETECTABLE"]

    W = 80
    lines = []
    lines.append("=" * W)
    lines.append("  CONDENSED SUMMARY REPORT")
    lines.append(f"  Source: {os.path.basename(report_path)}")
    lines.append("=" * W)
    lines.append("")

    # ── SECTION 1 & 2 (full) ─────────────────────────────────────────────────
    lines.append(sections.get("1", ""))
    lines.append("")
    lines.append(sections.get("2", ""))
    lines.append("")

    # ── COMPUTED STATS ────────────────────────────────────────────────────────
    lines.append("=" * W)
    lines.append("  COMPUTED STATISTICS (from per-fault data)")
    lines.append("=" * W)
    lines.append(compute_stats(faults))

    # ── TOP-N HARDEST FAULTS ─────────────────────────────────────────────────
    lines.append("-" * W)
    lines.append(f"  TOP {top_n} HARDEST FAULTS  (by SAT decisions)")
    lines.append("-" * W)
    for i, f in enumerate(top_hard, 1):
        lines.append(format_fault_block(f, rank=i, reason="most decisions"))
    lines.append("")

    # ── TOP-N SLOWEST FAULTS ─────────────────────────────────────────────────
    lines.append("-" * W)
    lines.append(f"  TOP {top_n} SLOWEST FAULTS  (by solve time)")
    lines.append("-" * W)
    for i, f in enumerate(top_slow, 1):
        lines.append(format_fault_block(f, rank=i, reason="slowest"))
    lines.append("")

    # ── REDUNDANT FAULTS ─────────────────────────────────────────────────────
    lines.append("-" * W)
    lines.append(f"  REDUNDANT (UNDETECTABLE) FAULTS  — {len(redundant)} total")
    lines.append("-" * W)
    if redundant:
        for f in redundant:
            lines.append(f"  {f['label']:<24} wire '{f['wire']}'  gate: {f['gate']}")
    else:
        lines.append("  None — all faults are detectable.")
    lines.append("")

    # ── SECTION 4: LLM GUIDANCE INSIGHTS (full) ──────────────────────────────
    lines.append("")
    lines.append(sections.get("4", ""))
    lines.append("")

    # ── END ──────────────────────────────────────────────────────────────────
    lines.append("=" * W)
    lines.append("  END OF CONDENSED SUMMARY")
    lines.append("=" * W)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    in_size  = os.path.getsize(report_path)
    out_size = os.path.getsize(out_path)
    print(f"    {in_size/1024:.0f} KB  →  {out_size/1024:.0f} KB  "
          f"({100*out_size/in_size:.1f}% of original)")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    report_files = sorted(
        f for f in os.listdir(REPORTS_DIR)
        if f.endswith("_insights.txt")
    )

    if not report_files:
        print(f"No *_insights.txt files found in {REPORTS_DIR}")
        sys.exit(1)

    print(f"\nExtracting summaries → {OUT_DIR}\n")
    for fname in report_files:
        in_path  = os.path.join(REPORTS_DIR, fname)
        out_name = fname.replace("_insights.txt", "_summary.txt")
        out_path = os.path.join(OUT_DIR, out_name)
        extract(in_path, out_path, top_n=10)

    print(f"\nDone! {len(report_files)} summaries written to reports/summaries/\n")


if __name__ == "__main__":
    main()
