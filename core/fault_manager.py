"""
fault_manager.py — Fault enumeration and result extraction for SAT-ATPG.

Pure functions only; no global state or classes.
"""

from core.circuit_loader import enumerate_all_nets, get_port_nets


def enumerate_stuck_at_faults(module_data: dict) -> list:
    """Return all single stuck-at faults for the circuit.

    For every net in the circuit (primary inputs, gate outputs, internal
    wires) this produces two candidate faults: stuck-at-0 and stuck-at-1.

    Args:
        module_data: The module dict returned by ``circuit_loader.load_circuit``.

    Returns:
        Sorted list of ``(net_id_str, fault_value)`` tuples where
        *fault_value* is ``0`` (stuck-at-0) or ``1`` (stuck-at-1).
        Yosys constant tokens (``"0"``, ``"1"``, ``"x"``, ``"z"``) are
        excluded by ``enumerate_all_nets``.
    """
    faults = []
    for net_id in enumerate_all_nets(module_data):
        faults.append((net_id, 0))
        faults.append((net_id, 1))
    return faults


def fault_label(net_id: str, fault_value: int) -> str:
    """Human-readable label for a stuck-at fault.

    Args:
        net_id:      Yosys net ID string (e.g. ``"6"``).
        fault_value: ``0`` for stuck-at-0, ``1`` for stuck-at-1.

    Returns:
        String of the form ``"SA0@net6"`` or ``"SA1@net6"``.
    """
    return f"SA{fault_value}@net{net_id}"


def extract_test_vector(model: list, good_map: dict, input_nets: list) -> dict:
    """Extract the primary-input assignment from a PySAT satisfying model.

    PySAT returns a model as a list of signed integers.  Variable *v* is
    ``True`` when ``model[v - 1] > 0`` and ``False`` when ``model[v - 1] < 0``.

    Args:
        model:      List of signed integers from ``solver.get_model()``.
        good_map:   ``{net_id_str: sat_variable}`` for the good circuit.
        input_nets: List of primary-input net ID strings (from
                    ``get_port_nets(module_data, "input")``).

    Returns:
        ``{net_id_str: 0_or_1}`` — the Boolean value assigned to each
        primary input under the satisfying assignment.  Nets absent from
        *good_map* are silently skipped.
    """
    test_vector = {}
    for net_id in input_nets:
        if net_id not in good_map:
            continue
        var = good_map[net_id]
        test_vector[net_id] = 1 if model[var - 1] > 0 else 0
    return test_vector


def extract_output_diff(model: list, good_map: dict,
                        faulty_map: dict, output_nets: list) -> dict:
    """Show how primary outputs differ between good and faulty circuits.

    Under a satisfying (fault-detecting) assignment, the good and faulty
    circuits must disagree on at least one output.  This function extracts
    both values for every primary output so the caller can display the
    divergence clearly.

    Args:
        model:       List of signed integers from ``solver.get_model()``.
        good_map:    ``{net_id_str: sat_variable}`` for the good  circuit.
        faulty_map:  ``{net_id_str: sat_variable}`` for the faulty circuit.
        output_nets: List of primary-output net ID strings (from
                     ``get_port_nets(module_data, "output")``).

    Returns:
        ``{net_id_str: {"good": 0|1, "faulty": 0|1}}`` for each output net
        present in both maps.  Nets missing from either map are skipped.
    """
    diff = {}
    for net_id in output_nets:
        if net_id not in good_map or net_id not in faulty_map:
            continue
        g_var = good_map[net_id]
        f_var = faulty_map[net_id]
        diff[net_id] = {
            "good":   1 if model[g_var - 1] > 0 else 0,
            "faulty": 1 if model[f_var - 1] > 0 else 0,
        }
    return diff
