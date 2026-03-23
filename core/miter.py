"""
miter.py — Build the complete miter CNF for SAT-based ATPG.

The miter is the core of the SAT-ATPG approach:

  1. Encode the GOOD  circuit  into CNF (no fault).
  2. Encode the FAULTY circuit into CNF (driving gate excluded, fault forced).
  3. Tie every primary input of both copies to the same variable so the solver
     must use ONE input vector for both copies simultaneously.
     Exception: if the fault is ON a primary input, skip that tie — otherwise
     the tie would propagate the stuck value into the good copy as well
     (Bug Fix #2).
  4. For each primary output pair (good_Y, faulty_Y) introduce a fresh D
     variable and encode  D = good_Y ⊕ faulty_Y  via Tseitin.
  5. Add a single clause  OR(all D vars)  to force the solver to find an input
     that causes at least one output to differ between the two copies.

If the formula is SAT an input vector that exposes the fault has been found
(the fault is detectable).  UNSAT means the fault is redundant.
"""

from core.cnf_builder import build_circuit_cnf
from core.circuit_loader import find_driving_gate, get_port_nets


def build_miter(module_data: dict, fault_net: str, fault_value: int):
    """Build the full miter CNF for a single stuck-at fault.

    This function is self-contained: it accepts the raw Yosys module dict and
    internally calls ``build_circuit_cnf`` and ``find_driving_gate`` so callers
    do not need to manage intermediate circuit copies.

    Args:
        module_data (dict):
            The module dict returned by ``circuit_loader.load_circuit``.
        fault_net (str):
            Yosys net ID string for the wire to be faulted (e.g. ``"6"``).
        fault_value (int):
            ``0`` for stuck-at-0, ``1`` for stuck-at-1.

    Returns:
        ``(all_clauses, good_map, faulty_map, next_free_var, meta)`` where:

        * **all_clauses** (``list[list[int]] | None``) — combined CNF for the
          solver.  ``None`` if *fault_net* was not found in the faulty circuit's
          variable map (net does not exist in the circuit).
        * **good_map**  (``dict[str, int]``) — net-id → SAT variable for the
          good  circuit.
        * **faulty_map** (``dict[str, int]``) — net-id → SAT variable for the
          faulty circuit.
        * **next_free_var** (``int``) — first variable ID not yet allocated.
        * **meta** (``dict``) — diagnostics; see keys below.

    ``meta`` keys
    -------------
    driving_gate        Cell name driving *fault_net* (``None`` for PIs).
    driving_gate_type   Yosys gate type string, or ``"PRIMARY_INPUT"``.
    fault_var           SAT variable forced by the fault unit clause.
    total_variables     ``next_free_var - 1``.
    total_clauses       Total clause count across all three parts.
    good_clauses        Number of clauses from the good  circuit encoding.
    faulty_clauses      Number of clauses from the faulty circuit encoding
                        (including the fault unit clause).
    miter_clauses       Number of tie + XOR + OR clauses.
    d_vars              List of output-difference variables D₀ … Dₙ.
    """
    cells       = module_data.get("cells", {})
    input_nets  = get_port_nets(module_data, "input")
    output_nets = get_port_nets(module_data, "output")

    # ── Step 1 ──────────────────────────────────────────────────────────────
    # Build GOOD circuit CNF (var_offset=0 → variables start at 1)
    good_clauses, good_map, good_next = build_circuit_cnf(cells, var_offset=0)

    # ── Step 2 ──────────────────────────────────────────────────────────────
    # Find which gate drives the fault net (None → fault is on a primary input)
    driving_gate = find_driving_gate(module_data, fault_net)
    if driving_gate is not None:
        driving_gate_type = cells[driving_gate]["type"]
    else:
        driving_gate_type = "PRIMARY_INPUT"

    # ── Step 3 ──────────────────────────────────────────────────────────────
    # Build FAULTY circuit CNF.
    #   var_offset = good_next - 1  so faulty variables start right after good.
    #   skip_gate  = driving_gate   so that gate's Tseitin clauses are omitted.
    faulty_clauses, faulty_map, faulty_next = build_circuit_cnf(
        cells,
        var_offset=good_next - 1,
        skip_gate=driving_gate,
    )

    # ── Step 4 ──────────────────────────────────────────────────────────────
    # Inject the fault: force the fault net to *fault_value* in the faulty copy.
    if fault_net not in faulty_map:
        # Net does not exist in this circuit — cannot test this fault.
        return None, good_map, faulty_map, faulty_next, {
            "driving_gate":      driving_gate,
            "driving_gate_type": driving_gate_type,
            "fault_var":         None,
            "total_variables":   faulty_next - 1,
            "total_clauses":     0,
            "good_clauses":      len(good_clauses),
            "faulty_clauses":    len(faulty_clauses),
            "miter_clauses":     0,
            "d_vars":            [],
        }

    fault_var = faulty_map[fault_net]
    if fault_value == 0:
        faulty_clauses.append([-fault_var])   # unit clause: fault net = 0
    else:
        faulty_clauses.append([ fault_var])   # unit clause: fault net = 1

    # ── Step 5 ──────────────────────────────────────────────────────────────
    # Tie primary inputs.
    #   good_A == faulty_A  encoded as  (A ∨ ¬A') ∧ (¬A ∨ A')
    #   SKIP if net_id == fault_net: tying a stuck PI to itself would force
    #   the good circuit's input to the fault value too (Bug Fix #2).
    miter_clauses = []
    for net_id in input_nets:
        if net_id == fault_net:
            continue                          # Bug Fix #2
        if net_id in good_map and net_id in faulty_map:
            g = good_map[net_id]
            f = faulty_map[net_id]
            miter_clauses.append([ g, -f])   # g=0 → f=0
            miter_clauses.append([-g,  f])   # g=1 → f=1

    # ── Step 6 ──────────────────────────────────────────────────────────────
    # XOR primary outputs and force at least one difference.
    #   For each output pair introduce D = good_Y ⊕ faulty_Y (Tseitin, 4 clauses).
    #   Then add OR(all D) to require at least one output to diverge.
    next_free_var = faulty_next
    d_vars = []
    for net_id in output_nets:
        if net_id in good_map and net_id in faulty_map:
            gy = good_map[net_id]
            fy = faulty_map[net_id]
            d  = next_free_var
            next_free_var += 1
            d_vars.append(d)

            # D = gy ⊕ fy
            # (¬gy ∨ ¬fy ∨ ¬D)  both 1 → D=0
            # ( gy ∨  fy ∨ ¬D)  both 0 → D=0
            # ( gy ∨ ¬fy ∨  D)  gy=0, fy=1 → D=1
            # (¬gy ∨  fy ∨  D)  gy=1, fy=0 → D=1
            miter_clauses.append([-gy, -fy, -d])
            miter_clauses.append([ gy,  fy, -d])
            miter_clauses.append([ gy, -fy,  d])
            miter_clauses.append([-gy,  fy,  d])

    # Force at least one output to differ (OR over all D variables)
    if d_vars:
        miter_clauses.append(d_vars[:])       # shallow copy for safety

    # ── Assemble & return ───────────────────────────────────────────────────
    all_clauses = good_clauses + faulty_clauses + miter_clauses

    meta = {
        "driving_gate":      driving_gate,
        "driving_gate_type": driving_gate_type,
        "fault_var":         fault_var,
        "total_variables":   next_free_var - 1,
        "total_clauses":     len(all_clauses),
        "good_clauses":      len(good_clauses),
        "faulty_clauses":    len(faulty_clauses),
        "miter_clauses":     len(miter_clauses),
        "d_vars":            d_vars,
    }

    return all_clauses, good_map, faulty_map, next_free_var, meta
