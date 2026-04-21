"""
run_atpg.py — Main entry point for SAT-based ATPG (Step 1).

Usage
-----
# Full fault sweep (all nets, both SA0 and SA1):
    python run_atpg.py --json benchmarks/c17.json

# Single fault:
    python run_atpg.py --json benchmarks/c17.json --net 6 --val 0
"""

import argparse
import time
import os

from pysat.solvers import Glucose3

from core.export_cnf import dump_good_circuit_cnf, dump_miter_cnf

from core.circuit_loader import load_circuit, get_port_nets, get_net_name_map, enumerate_verilog_nets
from core.miter import build_miter
from core.fault_manager import (
    enumerate_stuck_at_faults,
    fault_label,
    extract_test_vector,
    extract_output_diff,
)


# ── Single-fault runner ──────────────────────────────────────────────────────

def run_single_fault(module_data: dict, fault_net: str,
                     fault_value: int, verbose: bool = True,
                     module_name: str = "unknown",
                     export_cnf_dir: str = None) -> dict:
    """Run SAT-based ATPG for one stuck-at fault.

    Args:
        module_data:  Module dict from ``load_circuit``.
        fault_net:    Yosys net ID string (e.g. ``"6"``).
        fault_value:  ``0`` for SA0, ``1`` for SA1.
        verbose:      If ``True``, print a summary line per fault.
        module_name:  Circuit name for the export label.
        export_cnf_dir:  Path to directory for dropping the CNF if requested.

    Returns:
        Result dict with keys:
            fault_net, fault_value, fault_label, driving_gate,
            driving_gate_type, status, solve_time_sec,
            total_variables, total_clauses, solver_stats,
            test_vector, output_diff.
    """
    label = fault_label(fault_net, fault_value)

    # ── Build miter ──────────────────────────────────────────────────────
    all_clauses, good_map, faulty_map, next_free_var, meta = build_miter(
        module_data, fault_net, fault_value
    )

    if export_cnf_dir is not None and all_clauses is not None:
        dump_miter_cnf(module_name, fault_net, fault_value, all_clauses, export_cnf_dir)

    base_result = {
        "fault_net":         fault_net,
        "fault_value":       fault_value,
        "fault_label":       label,
        "driving_gate":      meta["driving_gate"],
        "driving_gate_type": meta["driving_gate_type"],
        "total_variables":   meta["total_variables"],
        "total_clauses":     meta["total_clauses"],
    }

    # ── Early-out: net not in circuit ────────────────────────────────────
    if all_clauses is None:
        if verbose:
            print(f"  [{label}]  SKIPPED  (net not found in circuit)")
        return {**base_result,
                "status": "SKIPPED", "solve_time_sec": 0.0,
                "solver_stats": {}, "test_vector": {}, "output_diff": {}}

    input_nets  = get_port_nets(module_data, "input")
    output_nets = get_port_nets(module_data, "output")

    # ── Solve ────────────────────────────────────────────────────────────
    solver = Glucose3()
    for clause in all_clauses:
        solver.add_clause(clause)

    t0 = time.perf_counter()
    sat = solver.solve()
    solve_time = time.perf_counter() - t0

    raw_stats    = solver.accum_stats()
    solver_stats = {
        "decisions":    raw_stats.get("decisions",    0),
        "conflicts":    raw_stats.get("conflicts",    0),
        "propagations": raw_stats.get("propagations", 0),
        "restarts":     raw_stats.get("restarts",     0),
    }

    test_vector = {}
    output_diff = {}

    if sat:
        model       = solver.get_model()
        test_vector = extract_test_vector(model, good_map, input_nets)
        output_diff = extract_output_diff(model, good_map, faulty_map, output_nets)
        status      = "DETECTABLE"
    else:
        status = "UNDETECTABLE"

    solver.delete()

    if verbose:
        tv_str = " ".join(f"net{k}={v}" for k, v in test_vector.items())
        print(f"  [{label}]  {status:<12}  "
              f"vars={meta['total_variables']}  "
              f"clauses={meta['total_clauses']}  "
              f"time={solve_time:.4f}s"
              + (f"  TV: {tv_str}" if test_vector else ""))

    return {
        **base_result,
        "status":         status,
        "solve_time_sec": solve_time,
        "solver_stats":   solver_stats,
        "test_vector":    test_vector,
        "output_diff":    output_diff,
    }


# ── Full sweep ───────────────────────────────────────────────────────────────

def run_full_sweep(json_file: str, verilog_only: bool = True) -> list:
    """Run SAT-ATPG over every stuck-at fault in the circuit.

    Args:
        json_file:    Path to Yosys JSON netlist.
        verilog_only: If True, only test faults on original Verilog wires.

    Returns:
        List of result dicts (one per fault).
    """
    module_name, module_data = load_circuit(json_file)
    
    export_cnf_dir = os.path.join("benchmarks", "cnf", module_name)
    os.makedirs(export_cnf_dir, exist_ok=True)
    dump_good_circuit_cnf(module_name, module_data, export_cnf_dir)
    
    net_name_map = get_net_name_map(module_data)
    faults       = enumerate_stuck_at_faults(module_data, verilog_only=verilog_only)

    print(f"\n{'='*60}")
    print(f"  SAT-ATPG Full Sweep")
    print(f"  Circuit : {module_name}  ({json_file})")
    print(f"  Faults  : {len(faults)} ({len(faults)//2} nets × SA0/SA1)")
    print(f"{'='*60}")

    results = []
    t_sweep_start = time.perf_counter()
    for fault_net, fault_value in faults:
        result = run_single_fault(module_data, fault_net, fault_value,
                                  verbose=True)
        results.append(result)
    total_elapsed = time.perf_counter() - t_sweep_start

    # ── Summary ──────────────────────────────────────────────────────────
    detectable   = sum(1 for r in results if r["status"] == "DETECTABLE")
    undetectable = sum(1 for r in results if r["status"] == "UNDETECTABLE")
    skipped      = sum(1 for r in results if r["status"] == "SKIPPED")
    tested       = detectable + undetectable
    coverage     = (detectable / tested * 100) if tested else 0.0

    print(f"\n{'='*60}")
    print(f"  Results")
    print(f"  Detectable   : {detectable}")
    print(f"  Undetectable : {undetectable}")
    print(f"  Skipped      : {skipped}")
    print(f"  Fault coverage: {coverage:.1f}%  ({detectable}/{tested} tested faults)")
    print(f"  Total time   : {total_elapsed*1000:.2f} ms  ({total_elapsed:.4f}s)")
    print(f"{'='*60}\n")

    return results


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(
        description="SAT-based ATPG — detects stuck-at faults via miter + Glucose3"
    )
    parser.add_argument(
        "--circuit", default="c17",
        metavar="CIRCUIT",
        help="Name of the circuit (default: c17). Looks in benchmarks/json/.",
    )
    parser.add_argument(
        "--tech", action="store_true",
        help="Use the tech-mapped version of the circuit (benchmarks/json/<circuit>_tech.json)",
    )
    parser.add_argument(
        "--notech", action="store_true",
        help="Use the generic synthesized version of the circuit (benchmarks/json/<circuit>_notech.json)",
    )
    parser.add_argument(
        "--net", default=None,
        metavar="NET_ID",
        help="Net ID to test in single-fault mode (e.g. --net 6)",
    )
    parser.add_argument(
        "--val", type=int, default=0, choices=[0, 1],
        metavar="0|1",
        help="Stuck-at value: 0 = SA0, 1 = SA1 (default: 0)",
    )
    return parser.parse_args()


def main():
    import sys
    args = _parse_args()

    if args.tech and args.notech:
        sys.exit("[ERROR] Specify either --tech or --notech, not both.")
    if not args.tech and not args.notech:
        # Default to tech mapping if neither is provided just so it doesn't crash on standard runs
        args.tech = True

    suffix = "tech" if args.tech else "notech"
    json_path = os.path.join("benchmarks", "json", f"{args.circuit}_{suffix}.json")

    if not os.path.isfile(json_path):
        sys.exit(f"[ERROR] Found no JSON at '{json_path}'. Run yosys synth first.")

    if args.net is not None:
        # ── Single-fault mode ────────────────────────────────────────────
        module_name, module_data = load_circuit(json_path)
        
        export_cnf_dir = os.path.join("benchmarks", "cnf", module_name)
        os.makedirs(export_cnf_dir, exist_ok=True)
        dump_good_circuit_cnf(module_name, module_data, export_cnf_dir)
        
        result = run_single_fault(module_data, str(args.net), args.val,
                                  verbose=False, module_name=module_name, 
                                  export_cnf_dir=export_cnf_dir)
        label = result["fault_label"]
        print(f"\n{'='*60}")
        print(f"  Single fault: {label}")
        print(f"  Status      : {result['status']}")
        print(f"  Driving gate: {result['driving_gate']} "
              f"({result['driving_gate_type']})")
        print(f"  Variables   : {result['total_variables']}")
        print(f"  Clauses     : {result['total_clauses']}")
        print(f"  Solve time  : {result['solve_time_sec']:.4f}s")
        if result["test_vector"]:
            print(f"\n  Test Vector (primary inputs):")
            for net_id, val in result["test_vector"].items():
                print(f"    net{net_id} = {val}")
        if result["output_diff"]:
            print(f"\n  Output Divergence:")
            for net_id, vals in result["output_diff"].items():
                print(f"    net{net_id}: good={vals['good']}  "
                      f"faulty={vals['faulty']}")
        print(f"{'='*60}\n")
    else:
        # ── Full-sweep mode ──────────────────────────────────────────────
        run_full_sweep(json_path, verilog_only=False)


if __name__ == "__main__":
    main()
