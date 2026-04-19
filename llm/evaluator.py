"""
llm/evaluator.py — Run a SAT-ATPG fault with LLM-provided assumptions.

This is the core Step 2 solver wrapper. It:
  1. Builds the miter CNF (same as Step 1).
  2. Runs solve(assumptions=assumptions) — the LLM-guided attempt.
  3. If assumptions cause UNSAT (the hint was wrong), retries with assumptions=[]
     as a fallback (identical to Step 1 baseline).
  4. Returns a rich result dict that extends the Step 1 baseline format with
     LLM-specific fields (used_hints, fallback_triggered, hint_decisions, etc.).
"""

import time

from pysat.solvers import Glucose3

from core.circuit_loader import get_port_nets
from core.miter          import build_miter
from core.fault_manager  import fault_label, extract_test_vector, extract_output_diff


def run_guided_fault(
    module_data:   dict,
    fault_net:     str,
    fault_value:   int,
    assumptions:   list[int],
    verbose:       bool = False,
) -> dict:
    """Run SAT-ATPG for one fault, guided by LLM-provided assumptions.

    The function attempts two phases:
      Phase 1 (Guided):  solve(assumptions=assumptions)
        - If SAT  → fault detected with LLM guidance, used_hints=True.
        - If UNSAT with non-empty assumptions → hints were wrong.
          Fallback to Phase 2.
        - If assumptions is [] → skip directly to Phase 2 (no hints given).
      Phase 2 (Fallback): solve()  — identical to Step 1 baseline.

    Args:
        module_data:  Module dict from ``circuit_loader.load_circuit``.
        fault_net:    Net ID string.
        fault_value:  0 or 1.
        assumptions:  PySAT literal list from ``translate_hints``.
                      Pass ``[]`` to run baseline-only.
        verbose:      Print per-fault diagnostics.

    Returns:
        Result dict with keys:
            fault_net, fault_value, fault_label, status,
            test_vector, output_diff,
            used_hints (bool), fallback_triggered (bool),
            hint_solve_time_sec, hint_solver_stats,
            fallback_solve_time_sec, fallback_solver_stats,
            total_solve_time_sec,
            total_variables, total_clauses.
    """
    label = fault_label(fault_net, fault_value)

    # ── Build miter (shared between phases — clauses don't change) ────────────
    all_clauses, good_map, faulty_map, _, meta = build_miter(
        module_data, fault_net, fault_value
    )

    base = {
        "fault_net":         fault_net,
        "fault_value":       fault_value,
        "fault_label":       label,
        "driving_gate":      meta.get("driving_gate"),
        "driving_gate_type": meta.get("driving_gate_type"),
        "total_variables":   meta.get("total_variables", 0),
        "total_clauses":     meta.get("total_clauses",   0),
    }

    # ── Early-out: net not in circuit ─────────────────────────────────────────
    if all_clauses is None:
        if verbose:
            print(f"  [{label}]  SKIPPED  (net not found)")
        return {
            **base,
            "status":                   "SKIPPED",
            "test_vector":              {},
            "output_diff":              {},
            "used_hints":               False,
            "fallback_triggered":       False,
            "hint_solve_time_sec":      0.0,
            "hint_solver_stats":        {},
            "fallback_solve_time_sec":  0.0,
            "fallback_solver_stats":    {},
            "total_solve_time_sec":     0.0,
        }

    input_nets  = get_port_nets(module_data, "input")
    output_nets = get_port_nets(module_data, "output")

    hint_time      = 0.0
    hint_stats: dict     = {}
    fallback_time  = 0.0
    fallback_stats: dict = {}
    used_hints         = False
    fallback_triggered = False
    status             = "UNDETECTABLE"
    test_vector: dict  = {}
    output_diff: dict  = {}

    # ── Phase 1: LLM-guided attempt ─────────────────────────────────────────
    if assumptions:
        solver = Glucose3()
        for clause in all_clauses:
            solver.add_clause(clause)

        t0  = time.perf_counter()
        sat = solver.solve(assumptions=assumptions)
        hint_time  = time.perf_counter() - t0
        raw        = solver.accum_stats()
        hint_stats = {
            "decisions":    raw.get("decisions",    0),
            "conflicts":    raw.get("conflicts",    0),
            "propagations": raw.get("propagations", 0),
            "restarts":     raw.get("restarts",     0),
        }

        if sat:
            model       = solver.get_model()
            test_vector = extract_test_vector(model, good_map, input_nets)
            output_diff = extract_output_diff(model, good_map, faulty_map, output_nets)
            status      = "DETECTABLE"
            used_hints  = True
            solver.delete()

            if verbose:
                tv = " ".join(f"net{k}={v}" for k, v in test_vector.items())
                print(f"  [{label}]  GUIDED  DETECTABLE  "
                      f"dec={hint_stats['decisions']}  "
                      f"time={hint_time*1000:.2f}ms  TV:{tv}")
        else:
            # Hints caused UNSAT — they were wrong; fall through to Phase 2
            solver.delete()
            fallback_triggered = True
            if verbose:
                print(f"  [{label}]  hints UNSAT → fallback  "
                      f"dec={hint_stats['decisions']}  "
                      f"time={hint_time*1000:.2f}ms")
    else:
        # No hints provided — go straight to baseline
        fallback_triggered = bool(not assumptions) if len(assumptions) == 0 else False

    # ── Phase 2: Fallback / baseline (no assumptions) ────────────────────────
    if status != "DETECTABLE":
        solver = Glucose3()
        for clause in all_clauses:
            solver.add_clause(clause)

        t0       = time.perf_counter()
        sat      = solver.solve()
        fallback_time  = time.perf_counter() - t0
        raw            = solver.accum_stats()
        fallback_stats = {
            "decisions":    raw.get("decisions",    0),
            "conflicts":    raw.get("conflicts",    0),
            "propagations": raw.get("propagations", 0),
            "restarts":     raw.get("restarts",     0),
        }

        if sat:
            model       = solver.get_model()
            test_vector = extract_test_vector(model, good_map, input_nets)
            output_diff = extract_output_diff(model, good_map, faulty_map, output_nets)
            status      = "DETECTABLE"
        else:
            status = "UNDETECTABLE"

        solver.delete()

        if verbose:
            tv = " ".join(f"net{k}={v}" for k, v in test_vector.items())
            marker = "FALLBACK" if fallback_triggered else "BASELINE"
            print(f"  [{label}]  {marker}  {status:<12}  "
                  f"dec={fallback_stats['decisions']}  "
                  f"time={fallback_time*1000:.2f}ms"
                  + (f"  TV:{tv}" if tv else ""))

    return {
        **base,
        "status":                   status,
        "test_vector":              test_vector,
        "output_diff":              output_diff,
        "used_hints":               used_hints,
        "fallback_triggered":       fallback_triggered,
        "hint_solve_time_sec":      hint_time,
        "hint_solver_stats":        hint_stats,
        "fallback_solve_time_sec":  fallback_time,
        "fallback_solver_stats":    fallback_stats,
        "total_solve_time_sec":     hint_time + fallback_time,
    }
