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

from pysat.solvers import Glucose3

from core.circuit_loader import load_circuit, get_port_nets, get_net_name_map
from core.miter import build_miter
from core.fault_manager import (
    enumerate_stuck_at_faults,
    fault_label,
    extract_test_vector,
    extract_output_diff,
)


# ── Single-fault runner ──────────────────────────────────────────────────────

def run_single_fault(module_data: dict, fault_net: str,
                     fault_value: int, verbose: bool = True) -> dict:
    """Run SAT-based ATPG for one stuck-at fault.

    Args:
        module_data:  Module dict from ``load_circuit``.
        fault_net:    Yosys net ID string (e.g. ``"6"``).
        fault_value:  ``0`` for SA0, ``1`` for SA1.
        verbose:      If ``True``, print a summary line per fault.

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

def run_full_sweep(json_file: str) -> list:
    """Run SAT-ATPG over every stuck-at fault in the circuit.

    Args:
        json_file: Path to Yosys JSON netlist.

    Returns:
        List of result dicts (one per fault).
    """
    module_name, module_data = load_circuit(json_file)
    net_name_map = get_net_name_map(module_data)
    faults       = enumerate_stuck_at_faults(module_data)

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
        "--json", default="benchmarks/json/c17.json",
        metavar="PATH",
        help="Path to Yosys JSON netlist (default: benchmarks/json/c17.json)",
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
    args = _parse_args()

    if args.net is not None:
        # ── Single-fault mode ────────────────────────────────────────────
        _, module_data = load_circuit(args.json)
        result = run_single_fault(module_data, str(args.net), args.val,
                                  verbose=False)
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
        run_full_sweep(args.json)


if __name__ == "__main__":
    main()
