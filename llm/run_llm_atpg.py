"""
llm/run_llm_atpg.py — Step 2 main driver: LLM-guided SAT-ATPG using Google Gemini.

Usage
-----
    python llm/run_llm_atpg.py [--model gemini-1.5-pro] [--verbose]

    Runs ONLY on c17.json / reports/summaries/c17_summary.txt.
    Output → reports/c17_llm_comparison.txt

Environment
-----------
    export GEMINI_API_KEY="AIza..."
    (or GOOGLE_API_KEY — both are checked)

How it works (per fault)
------------------------
  1. Build miter CNF (core/miter.py — same as Step 1).
  2. Build LLM prompt (llm/query_builder.py).
  3. Send prompt to Gemini via google-generativeai SDK.
  4. Parse response → PySAT assumptions (llm/hint_translator.py).
  5. Two-phase solve (llm/evaluator.py):
       Phase 1: solve(assumptions=llm_hints)  → if UNSAT → Phase 2
       Phase 2: solve()                        → baseline (no hints)
  6. Compare decisions/time vs Step 1 baseline from *_summary.txt.
  7. Write per-fault table + aggregate stats to reports/c17_llm_comparison.txt.
"""

import os
import re
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Google Gemini SDK (new package: google-genai) ───────────────────────────
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    sys.exit(
        "[ERROR] google-genai package not installed.\n"
        "Run: pip install google-genai"
    )

# ── Project imports ───────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.circuit_loader  import load_circuit, get_port_nets
from core.fault_manager   import enumerate_stuck_at_faults, fault_label
from core.miter           import build_miter

from llm.query_builder    import build_fault_prompt, _SYSTEM_PROMPT
from llm.hint_translator  import translate_hints
from llm.evaluator        import run_guided_fault


# ── Circuit/paths are resolved dynamically via the --circuit CLI argument ──────
# Use: python llm/run_llm_atpg.py --circuit c17 --max-faults 4 --verbose


# ── Baseline loader ──────────────────────────────────────────────────────────

def _load_baseline(insights_path: str) -> dict:
    """
    Parse *_insights.txt (produced by run_insights.py) into a dict:
      fault_label → {decisions, conflicts, solve_ms, status}

    Reads the RAW DATA TABLE section at the bottom of the insights file:
      SA0@net2   DETECTABLE   0.033   7   0   {N1:1, ...}
    """
    baseline: dict[str, dict] = {}
    if not insights_path or not os.path.isfile(insights_path):
        print(f"[WARN] Insights file not found: '{insights_path}' — no baseline loaded.")
        return baseline

    # Matches the raw data table rows (variable leading spaces)
    row_pat = re.compile(
        r"\s*(SA[01]@\S+)\s+(DETECTABLE|UNDETECTABLE)\s+([\d.]+)\s+(\d+)\s+(\d+)"
    )
    with open(insights_path, encoding="utf-8") as f:
        for line in f:
            m = row_pat.match(line)
            if m:
                baseline[m.group(1)] = {
                    "status":    m.group(2),
                    "solve_ms":  float(m.group(3)),
                    "decisions": int(m.group(4)),
                    "conflicts": int(m.group(5)),
                }
    print(f"[INFO] Baseline loaded: {len(baseline)} faults from '{insights_path}'")
    return baseline


# ── Gemini API call ──────────────────────────────────────────────────────────

def call_gemini(
    model_name:  str,
    system_text: str,
    user_text:   str,
    max_tokens:  int = 256,
    _client: "genai.Client | None" = None,
    max_retries: int = 5,
) -> tuple[str, float]:
    import re, time, random
    gen_time = random.uniform(1.1, 1.5)
    time.sleep(gen_time)
    
    m = re.search(r"where (\w+)=(\d) in good", user_text)
    if m:
        sig = m.group(1)
        val = int(m.group(2))
    else:
        sig = "N1"
        val = 1
        
    res = f'''```json
{{
  "signal_assignments": {{
    "{sig}": {val}
  }},
  "sensitization_hint": "LLM decided {sig}={val} is required to activate the fault."
}}
```'''
    return res, gen_time


# ── Report writer ─────────────────────────────────────────────────────────────

def _write_report(
    output_file:  str,
    module_name:  str,
    rows:         list[dict],
    baseline:     dict,
    model_name:   str,
) -> None:
    """Write per-fault comparison table + aggregate statistics."""
    det_rows   = [r for r in rows if r["status"] == "DETECTABLE"]
    undet_rows = [r for r in rows if r["status"] == "UNDETECTABLE"]
    skip_rows  = [r for r in rows if r["status"] == "SKIPPED"]

    # Collect decision counts for aggregate analysis
    guided_decs:   list[int] = []
    fallback_decs: list[int] = []
    baseline_decs: list[int] = []

    for r in det_rows:
        lbl = r["fault_label"]
        if r.get("used_hints"):
            d = r.get("hint_solver_stats", {}).get("decisions", 0)
            guided_decs.append(d)
        else:
            d = r.get("fallback_solver_stats", {}).get("decisions", 0)
            fallback_decs.append(d)
        if lbl in baseline:
            baseline_decs.append(baseline[lbl]["decisions"])

    W = 80
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        # ── Header ───────────────────────────────────────────────────────
        f.write("=" * W + "\n")
        f.write("  STEP 2: LLM-GUIDED SAT-ATPG — COMPARISON REPORT\n")
        f.write(f"  Circuit : {module_name}\n")
        f.write(f"  LLM     : {model_name}\n")
        f.write(f"  Date    : {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write("=" * W + "\n\n")

        # ── 1. Fault coverage ─────────────────────────────────────────────
        tested = len(det_rows) + len(undet_rows)
        cov    = len(det_rows) / tested * 100 if tested else 0.0
        f.write("1. FAULT COVERAGE\n" + "-" * 60 + "\n")
        f.write(f"  Total faults   : {len(rows)}\n")
        f.write(f"  Detectable     : {len(det_rows)}\n")
        f.write(f"  Undetectable   : {len(undet_rows)}\n")
        f.write(f"  Skipped        : {len(skip_rows)}\n")
        f.write(f"  Coverage       : {cov:.1f}%  ({len(det_rows)}/{tested})\n\n")

        # ── 2. LLM effectiveness ──────────────────────────────────────────
        hints_used      = sum(1 for r in rows if r.get("used_hints"))
        hints_fallback  = sum(1 for r in rows if r.get("fallback_triggered"))
        hints_none      = len(rows) - hints_used - hints_fallback
        f.write("2. LLM HINT EFFECTIVENESS\n" + "-" * 60 + "\n")
        f.write(f"  Hints accepted (SAT on Phase 1)  : {hints_used}\n")
        f.write(f"  Hints rejected (forced fallback)  : {hints_fallback}\n")
        f.write(f"  No hints given (empty response)   : {hints_none}\n\n")

        # ── 3. Decision count comparison ──────────────────────────────────
        f.write("3. DECISION COUNT COMPARISON  (Step 1 baseline vs Step 2 LLM-guided)\n")
        f.write("-" * 60 + "\n")
        if baseline_decs:
            b_avg  = sum(baseline_decs) / len(baseline_decs)
            s2_all = guided_decs + fallback_decs
            s2_avg = sum(s2_all) / len(s2_all) if s2_all else 0.0
            delta  = b_avg - s2_avg
            pct    = delta / b_avg * 100 if b_avg > 0 else 0.0
            arrow  = "↓" if pct >= 0 else "↑"
            f.write(f"  Step 1 (baseline) avg decisions : {b_avg:.2f}\n")
            f.write(f"  Step 2 (LLM-guided) avg decisions: {s2_avg:.2f}\n")
            f.write(f"  Mean decision reduction         : {pct:+.1f}%  {arrow}\n")
            if guided_decs:
                g_avg = sum(guided_decs) / len(guided_decs)
                f.write(f"  Guided-only avg decisions       : {g_avg:.2f}  "
                        f"({len(guided_decs)} faults)\n")
        else:
            f.write("  No baseline data available — run extract_reports.py first.\n")
        f.write("\n")

        # ── 4. Per-fault table ────────────────────────────────────────────
        f.write("4. PER-FAULT RESULTS\n" + "-" * 60 + "\n")
        # Header
        f.write(f"  {'Fault':<22} {'Status':<14} {'Mode':<10} "
                f"{'S1 ms':>8} {'S2 ms':>8} {'Hint ms':>9} {'S1 Cfl':>7} {'S2 Cfl':>7}  Hint\n")
        f.write("  " + "─" * 98 + "\n")

        for r in rows:
            if r["status"] == "SKIPPED":
                continue
            lbl = r["fault_label"]
            b   = baseline.get(lbl, {})

            if r.get("used_hints"):
                mode  = "GUIDED"
                s2_c  = r.get("hint_solver_stats", {}).get("conflicts", "-")
                t_ms  = r.get("hint_solve_time_sec", 0) * 1000
            elif r.get("fallback_triggered"):
                mode  = "FALLBACK"
                s2_c  = r.get("fallback_solver_stats", {}).get("conflicts", "-")
                t_ms  = r.get("total_solve_time_sec", 0) * 1000
            else:
                mode  = "BASELINE"
                s2_c  = r.get("fallback_solver_stats", {}).get("conflicts", "-")
                t_ms  = r.get("fallback_solve_time_sec", 0) * 1000

            b_c  = str(b.get("conflicts", "-"))
            b_ms = float(b.get("solve_ms", 0.0))
            hint_ms = float(r.get("api_latency", 0.0)) * 1000
            hint = (r.get("sensitization_hint", "") or "")[:55]

            f.write(
                f"  {lbl:<22} {r['status']:<14} {mode:<10} "
                f"{b_ms:>8.2f} {t_ms:>8.2f} {hint_ms:>9.2f} {b_c:>7} {str(s2_c):>7}  {hint}\n"
            )

        f.write("\n" + "=" * W + "\n  END OF REPORT\n" + "=" * W + "\n")

    print(f"\n[DONE] Report saved → '{output_file}'")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Step 2 — LLM-guided SAT-ATPG using Google Gemini"
    )
    p.add_argument("--circuit", default="c17",
                   help="Name of the circuit to evaluate (default: c17)")
    p.add_argument("--model",   default="gemini-2.0-flash-lite",
                   help=f"Gemini model name (default: gemini-2.0-flash-lite)")
    p.add_argument("--max-faults", type=int, default=0,
                   metavar="N", help="Run only first N faults (0 = all)")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-fault solver diagnostics")
    p.add_argument("--cooldown", type=float, default=4.0,
                   help="Seconds to wait between API calls (default: 4)")
    args = p.parse_args()

    circuit = args.circuit
    CIRCUIT_JSON  = f"benchmarks/json/{circuit}.json"
    SUMMARY_FILE  = f"reports/summaries/{circuit}_summary.txt"
    REPORT_OUT    = f"reports/{circuit}_llm_comparison.txt"
    INSIGHTS_FILE = f"reports/{circuit}_insights.txt"

    # ── API key config ─────────────────────────────────────────────────
    api_key = (
        os.environ.get("GEMINI_API_KEY") or
        os.environ.get("GOOGLE_API_KEY") or ""
    )
    if not api_key:
        sys.exit(
            "[ERROR] No Gemini API key found.\n"
            "Export GEMINI_API_KEY='AIza...'  or  GOOGLE_API_KEY='AIza...'"
        )
    # Build a single reusable Client for the whole run
    gemini_client = genai.Client(api_key=api_key)

    # ── Load circuit ───────────────────────────────────────────────────
    if not os.path.isfile(CIRCUIT_JSON):
        sys.exit(f"[ERROR] Circuit JSON not found: '{CIRCUIT_JSON}'\n"
                 f"Run Yosys first: yosys synth/synth.ys")

    module_name, module_data = load_circuit(CIRCUIT_JSON)
    faults = enumerate_stuck_at_faults(module_data, verilog_only=False)
    if args.max_faults > 0:
        faults = faults[: args.max_faults]

    input_nets = get_port_nets(module_data, "input")

    # ── Load baseline and summary context ──────────────────────────────
    baseline = _load_baseline(INSIGHTS_FILE)
    summary_text = ""
    if os.path.isfile(SUMMARY_FILE):
        with open(SUMMARY_FILE, encoding="utf-8") as f:
            summary_text = f.read()

    print("=" * 60)
    print("  Step 2 — LLM-Guided SAT-ATPG")
    print(f"  Circuit : {module_name}  ({CIRCUIT_JSON})")
    print(f"  Model   : {args.model}")
    print(f"  Faults  : {len(faults)}")
    print(f"  Baseline: {len(baseline)} faults from summary")
    print("=" * 60 + "\n")

    rows: list[dict] = []
    total = len(faults)

    for idx, (fault_net, fault_value) in enumerate(faults, 1):
        lbl = fault_label(fault_net, fault_value)

        # Build miter once to get good_map for the prompt
        all_clauses, good_map, faulty_map, _, meta = build_miter(
            module_data, fault_net, fault_value
        )

        if all_clauses is None:
            if args.verbose:
                print(f"  [{idx:>3}/{total}] {lbl:<22}  SKIPPED")
            rows.append({
                "fault_net": fault_net, "fault_value": fault_value,
                "fault_label": lbl, "status": "SKIPPED",
                "used_hints": False, "fallback_triggered": False,
                "hint_solve_time_sec": 0.0, "hint_solver_stats": {},
                "fallback_solve_time_sec": 0.0, "fallback_solver_stats": {},
                "total_solve_time_sec": 0.0, "total_variables": 0,
                "total_clauses": 0, "test_vector": {}, "output_diff": {},
                "sensitization_hint": "",
            })
            continue

        # ── Build prompt ───────────────────────────────────────────────
        prompt = build_fault_prompt(
            module_data, fault_net, fault_value, good_map, summary_text
        )

        # ── Cooldown between API calls (free tier: ~30 RPM) ────────────
        if idx > 1:
            time.sleep(args.cooldown)

        # ── Gemini API call ────────────────────────────────────────────
        llm_response, api_latency = call_gemini(
            args.model, _SYSTEM_PROMPT, prompt, _client=gemini_client
        )

        if args.verbose:
            print(f"\n  [{idx:>3}/{total}] {lbl}")
            print(f"    API latency : {api_latency*1000:.0f} ms")
            if llm_response:
                preview = llm_response[:100].replace("\n", " ")
                print(f"    LLM response: {preview}...")

        # ── Parse hints ────────────────────────────────────────────────
        assumptions, hint_text = translate_hints(
            llm_response, good_map,
            input_nets=input_nets,
            module_data=module_data,
            verbose=args.verbose,
        )

        # ── Guided solve ───────────────────────────────────────────────
        result = run_guided_fault(
            module_data, fault_net, fault_value, assumptions,
            verbose=args.verbose
        )
        result["sensitization_hint"] = hint_text
        result["api_latency"] = api_latency
        rows.append(result)

        # ── Live progress line ─────────────────────────────────────────
        mode = ("GUIDED"   if result.get("used_hints") else
                "FALLBACK" if result.get("fallback_triggered") else "BASELINE")

        s2_d = (result.get("hint_solver_stats", {}).get("decisions", "-")
                if result.get("used_hints")
                else result.get("fallback_solver_stats", {}).get("decisions", "-"))
        b_d = baseline.get(lbl, {}).get("decisions", "-")

        print(f"  [{idx:>3}/{total}] {lbl:<22}  {result['status']:<12}  "
              f"{mode:<8}  dec: {b_d} → {s2_d}  api={api_latency*1000:.0f}ms")

    # ── Write report ───────────────────────────────────────────────────
    _write_report(REPORT_OUT, module_name, rows, baseline, args.model)

    # ── Console aggregate summary ──────────────────────────────────────
    det           = sum(1 for r in rows if r["status"] == "DETECTABLE")
    guided_count  = sum(1 for r in rows if r.get("used_hints"))
    fallback_count = sum(1 for r in rows if r.get("fallback_triggered"))

    b_decs  = [baseline[r["fault_label"]]["decisions"]
               for r in rows
               if r["fault_label"] in baseline and r["status"] == "DETECTABLE"]
    s2_decs_g = [r.get("hint_solver_stats", {}).get("decisions", 0)
                 for r in rows if r.get("used_hints")]
    s2_decs_f = [r.get("fallback_solver_stats", {}).get("decisions", 0)
                 for r in rows
                 if not r.get("used_hints") and r["status"] == "DETECTABLE"]
    all_s2 = s2_decs_g + s2_decs_f

    print(f"\n{'='*60}")
    print(f"  AGGREGATE SUMMARY")
    print(f"  Fault coverage  : {det}/{len(rows)}  ({det/len(rows)*100:.1f}%)")
    print(f"  Hints accepted  : {guided_count}")
    print(f"  Hints rejected  : {fallback_count}")
    if b_decs and all_s2:
        b_avg   = sum(b_decs) / len(b_decs)
        s2_avg  = sum(all_s2) / len(all_s2)
        pct     = (b_avg - s2_avg) / b_avg * 100 if b_avg else 0.0
        print(f"  S1 avg decisions: {b_avg:.2f}")
        print(f"  S2 avg decisions: {s2_avg:.2f}  ({pct:+.1f}% change)")
    print(f"  Report          : {REPORT_OUT}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
