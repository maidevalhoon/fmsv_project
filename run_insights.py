"""
run_insights.py — Collect SAT solver metrics and write a structured report
                  for the LLM guidance layer (Step 2 design aid).

Usage
-----
    python run_insights.py --json benchmarks/c17.json --out reports/solver_insights_report.txt
"""

import argparse
import os

from core.circuit_loader import load_circuit, get_port_nets, get_net_name_map
from core.fault_manager import enumerate_stuck_at_faults
from run_atpg import run_single_fault


# ── Report generator ─────────────────────────────────────────────────────────

def generate_report(module_name: str, module_data: dict,
                    insights: list, output_file: str) -> None:
    """Write a human-readable + LLM-ready insights report to *output_file*.

    Sections
    --------
    1. Circuit Overview
    2. Fault Coverage Summary
    3. Per-Fault Detailed Results
    4. Key Insights for LLM Guidance Layer
    5. Raw Data Table

    Args:
        module_name:  Verilog module name string.
        module_data:  Module dict from ``load_circuit``.
        insights:     List of result dicts from ``run_single_fault``.
        output_file:  Destination path for the report file.
    """
    input_nets   = get_port_nets(module_data, "input")
    output_nets  = get_port_nets(module_data, "output")
    net_name_map = get_net_name_map(module_data)
    cells        = module_data.get("cells", {})

    def net_name(nid):
        return net_name_map.get(nid, f"net{nid}")

    def net_tag(nid):
        if nid in input_nets:  return "IN"
        if nid in output_nets: return "OUT"
        return "INT"

    detectable   = [r for r in insights if r["status"] == "DETECTABLE"]
    undetectable = [r for r in insights if r["status"] == "UNDETECTABLE"]
    skipped      = [r for r in insights if r["status"] == "SKIPPED"]
    total_tested = len(detectable) + len(undetectable)
    coverage_pct = (len(detectable) / total_tested * 100) if total_tested else 0.0

    W = 80   # report width

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    with open(output_file, "w") as f:

        # ── Header ──────────────────────────────────────────────────────
        f.write("=" * W + "\n")
        f.write("  SOLVER INSIGHTS REPORT — Step 1 Analysis for LLM Guidance Layer\n")
        f.write("=" * W + "\n\n")

        # ── Section 1: Circuit Overview ──────────────────────────────────
        f.write("1. CIRCUIT OVERVIEW\n" + "-" * 60 + "\n")
        f.write(f"  Module       : {module_name}\n")
        f.write(f"  Input nets   : {input_nets}\n")
        f.write(f"  Output nets  : {output_nets}\n")
        f.write(f"  Gate count   : {len(cells)}\n")

        gate_types = sorted({cd['type'] for cd in cells.values()})
        f.write(f"  Gate types   : {gate_types}\n\n")

        f.write("  Net ID → Name Mapping:\n")
        sorted_nets = sorted(net_name_map.items(),
                             key=lambda kv: int(kv[0]) if kv[0].isdigit() else kv[0])
        for nid, name in sorted_nets:
            f.write(f"    Net {nid:>4} → {name:<20} [{net_tag(nid)}]\n")
        f.write("\n")

        # ── Section 2: Fault Coverage Summary ───────────────────────────
        f.write("2. FAULT COVERAGE SUMMARY\n" + "-" * 60 + "\n")
        f.write(f"  Total faults         : {len(insights)}\n")
        f.write(f"  Detectable   (SAT)   : {len(detectable)}\n")
        f.write(f"  Undetectable (UNSAT) : {len(undetectable)}\n")
        f.write(f"  Skipped              : {len(skipped)}\n")
        f.write(f"  Fault coverage       : {coverage_pct:.1f}%"
                f"  ({len(detectable)}/{total_tested} tested)\n\n")

        # ── Section 3: Per-Fault Detailed Results ───────────────────────
        f.write("3. PER-FAULT DETAILED RESULTS\n" + "-" * 60 + "\n")
        idx = 0
        for r in insights:
            if r["status"] == "SKIPPED":
                continue
            idx += 1
            wire = net_name(r["fault_net"])
            f.write(f"\n  Fault #{idx}: {r['fault_label']}  (wire '{wire}')\n")
            dg = r["driving_gate"] or "—"
            dgt = r["driving_gate_type"]
            skipped_note = "  ← clauses skipped in faulty copy" if r["driving_gate"] else ""
            f.write(f"    Driving gate   : {dg}  [{dgt}]{skipped_note}\n")
            f.write(f"    Status         : {r['status']}\n")
            f.write(f"    Solve time     : {r['solve_time_sec']*1000:.3f} ms\n")
            f.write(f"    CNF size       : {r['total_variables']} vars,"
                    f" {r['total_clauses']} clauses\n")

            ss = r.get("solver_stats", {})
            f.write(f"    Solver stats   : decisions={ss.get('decisions','-')},"
                    f" conflicts={ss.get('conflicts','-')},"
                    f" propagations={ss.get('propagations','-')}\n")

            tv = r.get("test_vector") or {}
            if tv:
                tv_str = ", ".join(f"{net_name(k)}={v}" for k, v in tv.items())
                f.write(f"    Test vector    : {tv_str}\n")

            od = r.get("output_diff") or {}
            for out_net, vals in od.items():
                f.write(f"    Output '{net_name(out_net)}'"
                        f": good={vals['good']}  faulty={vals['faulty']}\n")

        f.write("\n")

        # ── Section 4: Key Insights for LLM Guidance Layer ──────────────
        f.write("\n" + "=" * W + "\n")
        f.write("4. KEY INSIGHTS FOR LLM GUIDANCE LAYER\n")
        f.write("=" * W + "\n")

        # 4.1 Solver difficulty
        f.write("\n  4.1  SOLVER DIFFICULTY (detectable faults)\n")
        f.write("  " + "─" * 56 + "\n")
        if detectable:
            times      = [r["solve_time_sec"] for r in detectable]
            decisions  = [r.get("solver_stats", {}).get("decisions", 0) for r in detectable]
            conflicts  = [r.get("solver_stats", {}).get("conflicts", 0) for r in detectable]
            f.write(f"    Avg solve time : {sum(times)/len(times)*1000:.3f} ms\n")
            f.write(f"    Avg decisions  : {sum(decisions)/len(decisions):.1f}\n")
            f.write(f"    Avg conflicts  : {sum(conflicts)/len(conflicts):.1f}\n")
            hardest = max(detectable,
                          key=lambda r: r.get("solver_stats", {}).get("decisions", 0))
            f.write(f"\n    Hardest fault  : {hardest['fault_label']}"
                    f"  (decisions={hardest.get('solver_stats',{}).get('decisions','?')})\n")
            f.write( "    → LLM hints would help most on high-decision faults like this.\n")
        else:
            f.write("    No detectable faults found.\n")

        # 4.2 Redundant faults
        f.write("\n  4.2  REDUNDANT FAULTS\n")
        f.write("  " + "─" * 56 + "\n")
        if undetectable:
            for r in undetectable:
                f.write(f"    {r['fault_label']:<20}  wire '{net_name(r['fault_net'])}'\n")
            f.write("    → LLM could predict redundancy upfront, "
                    "allowing the solver to be skipped entirely.\n")
        else:
            f.write("    No redundant faults found.\n")

        # 4.3 How LLM hints map to PySAT
        f.write("""
  4.3  HOW LLM HINTS MAP TO PYSAT MECHANISMS
  ────────────────────────────────────────────────────────

    Option A — ASSUMPTIONS  (soft, per-query, retractable)
      Best for partial input assignments the LLM is unsure about.
      The solver can still backtrack past them.

        from pysat.solvers import Glucose3
        solver = Glucose3()
        for clause in all_clauses:
            solver.add_clause(clause)
        # LLM predicts net "2" is likely 1:
        guess_var = good_map["2"]
        sat = solver.solve(assumptions=[guess_var])

    Option B — HARD CLAUSES  (permanent, strongest bias)
      Only for high-confidence LLM predictions.
      Incorrect unit clauses will cause UNSAT immediately.

        solver.add_clause([good_map["2"]])   # force net "2" = 1

    Recommended hybrid: use assumptions first; escalate to hard
    clauses only if the LLM confidence score exceeds a threshold.

""")

        # 4.4 Baseline metrics
        f.write("  4.4  BASELINE METRICS FOR STEP 2 COMPARISON\n")
        f.write("  " + "─" * 56 + "\n")
        f.write("    Record per fault: decisions, conflicts, propagations,\n"
                "    solve_time_sec, status.  Step 2 will compare these\n"
                "    against LLM-guided runs to measure improvement.\n\n")

        # ── Section 5: Raw Data Table ────────────────────────────────────
        f.write("5. RAW DATA TABLE\n" + "-" * 60 + "\n")
        col = {"fault": 20, "status": 14, "time": 10, "dec": 8, "conf": 8}
        hdr = (f"  {'Fault':<{col['fault']}} {'Status':<{col['status']}}"
               f" {'Time(ms)':<{col['time']}} {'Dec':<{col['dec']}}"
               f" {'Conf':<{col['conf']}} Test Vector")
        f.write(hdr + "\n")
        f.write("  " + "─" * (len(hdr) - 2) + "\n")

        for r in insights:
            if r["status"] == "SKIPPED":
                continue
            ss = r.get("solver_stats", {})
            tv = r.get("test_vector") or {}
            tv_str = "{" + ", ".join(f"{net_name(k)}:{v}" for k, v in tv.items()) + "}"
            f.write(
                f"  {r['fault_label']:<{col['fault']}}"
                f" {r['status']:<{col['status']}}"
                f" {r['solve_time_sec']*1000:<{col['time']}.3f}"
                f" {str(ss.get('decisions','-')):<{col['dec']}}"
                f" {str(ss.get('conflicts','-')):<{col['conf']}}"
                f" {tv_str}\n"
            )

        f.write("\n" + "=" * W + "\n  END OF REPORT\n" + "=" * W + "\n")

    print(f"[DONE] Report saved → '{output_file}'")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAT-ATPG solver-insight collector — generates LLM guidance report"
    )
    parser.add_argument(
        "--json", default="benchmarks/json/c17.json",
        metavar="PATH",
        help="Path to Yosys JSON netlist (default: benchmarks/json/c17.json)",
    )
    parser.add_argument(
        "--out", default="reports/solver_insights_report.txt",
        metavar="PATH",
        help="Output report path (default: reports/solver_insights_report.txt)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Step 1 — Solver Insights Collector")
    print("=" * 60)

    module_name, module_data = load_circuit(args.json)
    print(f"\n[INFO] Loaded module : {module_name}")

    faults = enumerate_stuck_at_faults(module_data)
    print(f"[INFO] Faults to test: {len(faults)}  ({len(faults)//2} nets × SA0/SA1)\n")

    insights = []
    total = len(faults)
    for idx, (fault_net, fault_value) in enumerate(faults, start=1):
        result = run_single_fault(module_data, fault_net, fault_value, verbose=False)
        insights.append(result)
        tv_str = ""
        if result.get("test_vector"):
            tv_str = "  TV: " + str(result["test_vector"])
        print(f"  [{idx:>3}/{total}] {result['fault_label']:<20}"
              f"  {result['status']:<12}"
              f"  {result['solve_time_sec']*1000:6.2f} ms{tv_str}")

    generate_report(module_name, module_data, insights, args.out)

    det   = len([r for r in insights if r["status"] == "DETECTABLE"])
    undet = len([r for r in insights if r["status"] == "UNDETECTABLE"])
    total_tested = det + undet
    cov   = det / total_tested * 100 if total_tested else 0.0

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"  Detectable   : {det}")
    print(f"  Undetectable : {undet}")
    print(f"  Coverage     : {cov:.1f}%  ({det}/{total_tested} tested)")
    print(f"  Report       : {args.out}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
