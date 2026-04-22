"""
llm/query_builder.py — Build structured prompts for LLM-guided SAT-ATPG.

The prompt instructs the LLM to reason about the circuit topology and return
a JSON object containing partial input assignments and a sensitization hint.
These are translated by hint_translator.py into PySAT assumptions.

Token-efficiency strategy:
  - Present the circuit using original Verilog signal names (N1, N10, …)
  - Collapse Yosys AND+NOT decompositions back into NAND gates
  - Omit SAT variable maps — the LLM doesn't need solver internals
  - Keep the prompt under ~300 tokens for small circuits
"""

import json
from core.circuit_loader import (
    get_port_nets,
    get_signal_name_map,
    enumerate_verilog_nets,
)


_SYSTEM_PROMPT = """\
You are an ATPG expert. Given a gate-level circuit and a stuck-at fault, \
return a JSON object with primary input assignments that detect the fault.

Return ONLY valid JSON — no prose:
{"signal_assignments": {"<signal_name>": <0_or_1>, ...}, "sensitization_hint": "<one sentence>"}

Rules:
- MUST ONLY assign the minimum primary inputs necessary to structurally excite the fault. Do NOT assign variables for path propagation, let the solver compute them.
- Use integer 0 or 1 as values.
- Leave all other inputs out of the JSON.
"""


def _build_gate_graph(module_data: dict, name_map: dict) -> dict:
    """Build a driver map: net_id -> (cell_name, cell_data) for output ports."""
    cells = module_data.get("cells", {})
    driver = {}
    for cell_name, cell_data in cells.items():
        conn = cell_data.get("connections", {})
        for port in ("Y", "ZN", "Z"):
            for bit in conn.get(port, []):
                driver[str(bit)] = (cell_name, cell_data)
    return driver


def _get_name(net_id, name_map: dict) -> str:
    """Return signal name for a net_id, falling back to net<id>."""
    s = str(net_id[0]) if isinstance(net_id, list) else str(net_id)
    return name_map.get(s, f"net{s}")


def _describe_circuit_compact(module_data: dict) -> str:
    """Build a compact circuit description using original Verilog names.

    Collapses Yosys AND+NOT → NAND, OR+NOT → NOR decompositions so the
    LLM sees the original gate structure, not Yosys intermediates.
    """
    cells = module_data.get("cells", {})
    name_map = get_signal_name_map(module_data)
    input_nets = get_port_nets(module_data, "input")
    output_nets = get_port_nets(module_data, "output")
    visible_nets = set(enumerate_verilog_nets(module_data))
    from core.circuit_loader import enumerate_all_nets
    all_nets = set(enumerate_all_nets(module_data))

    driver = _build_gate_graph(module_data, name_map)

    _collapse = {"$and": "NAND", "$_AND_": "NAND", "$or": "NOR", "$_OR_": "NOR"}

    lines = []
    inputs_str = ", ".join(_get_name(n, name_map) for n in input_nets)
    outputs_str = ", ".join(_get_name(n, name_map) for n in output_nets)
    lines.append(f"INPUTS: {inputs_str}")
    lines.append(f"OUTPUTS: {outputs_str}")
    lines.append("GATES:")

    processed = set()
    
    queue = list(output_nets)
    visited_nets = set(queue)

    gate_lines = []

    while queue:
        net_id = queue.pop(0)

        if net_id in [str(n) for n in input_nets]:
            continue
        if net_id not in driver:
            continue

        cell_name, cell_data = driver[net_id]

        if cell_name in processed:
            continue
        processed.add(cell_name)

        # Enqueue inputs of this cell for backward traversal
        for port_name, bits in cell_data["connections"].items():
            if port_name not in ("Y", "ZN", "Z"):  # only look at inputs
                for bit in bits:
                    b_str = str(bit)
                    if b_str not in visited_nets:
                        visited_nets.add(b_str)
                        queue.append(b_str)

        gate_type = cell_data["type"]
        conn = cell_data["connections"]

        out_name = _get_name(net_id, name_map)

        if gate_type in ("$not", "$_NOT_") or gate_type.startswith("INV"):
            input_net = str(conn["A"][0])
            if input_net in driver and input_net not in visible_nets:
                inner_name, inner_data = driver[input_net]
                inner_type = inner_data["type"]
                inner_conn = inner_data["connections"]
                collapsed = _collapse.get(inner_type)
                if collapsed and inner_name not in processed:
                    in_a = _get_name(inner_conn["A"][0], name_map)
                    in_b = _get_name(inner_conn["B"][0], name_map)
                    gate_lines.append(f"  {out_name} = {collapsed}({in_a}, {in_b})")
                    processed.add(inner_name)
                    # Enqueue the hidden inner gate's inputs
                    for inner_port_name, inner_bits in inner_conn.items():
                        if inner_port_name not in ("Y", "ZN", "Z"):
                            for ib in inner_bits:
                                ib_str = str(ib)
                                if ib_str not in visited_nets:
                                    visited_nets.add(ib_str)
                                    queue.append(ib_str)
                    continue
            gate_lines.append(f"  {out_name} = NOT({_get_name(conn['A'][0], name_map)})")

        elif gate_type in ("$and", "$_AND_"):
            in_a = _get_name(conn["A"][0], name_map)
            in_b = _get_name(conn["B"][0], name_map)
            gate_lines.append(f"  {out_name} = AND({in_a}, {in_b})")

        elif gate_type in ("$or", "$_OR_"):
            in_a = _get_name(conn["A"][0], name_map)
            in_b = _get_name(conn["B"][0], name_map)
            gate_lines.append(f"  {out_name} = OR({in_a}, {in_b})")

        elif gate_type in ("$nand", "$_NAND_"):
            in_a = _get_name(conn["A"][0], name_map)
            in_b = _get_name(conn["B"][0], name_map)
            gate_lines.append(f"  {out_name} = NAND({in_a}, {in_b})")

        elif gate_type in ("$nor", "$_NOR_"):
            in_a = _get_name(conn["A"][0], name_map)
            in_b = _get_name(conn["B"][0], name_map)
            gate_lines.append(f"  {out_name} = NOR({in_a}, {in_b})")

        elif gate_type in ("$xor", "$_XOR_"):
            in_a = _get_name(conn["A"][0], name_map)
            in_b = _get_name(conn["B"][0], name_map)
            gate_lines.append(f"  {out_name} = XOR({in_a}, {in_b})")

        elif gate_type in ("$xnor", "$_XNOR_"):
            in_a = _get_name(conn["A"][0], name_map)
            in_b = _get_name(conn["B"][0], name_map)
            gate_lines.append(f"  {out_name} = XNOR({in_a}, {in_b})")

        else:
            gate_lines.append(f"  {out_name} = {gate_type}(...)")

    # Reversing the order to print inputs -> outputs (topological)
    lines.extend(reversed(gate_lines))

    return "\n".join(lines)


def build_fault_prompt(
    module_data: dict,
    fault_net: str,
    fault_value: int,
    good_map: dict,
    summary_text: str = "",
) -> str:
    """Build a compact LLM prompt for one stuck-at fault.

    Uses original Verilog signal names instead of Yosys net IDs.
    Omits SAT variable maps to reduce token count ~5x.

    Args:
        module_data:  Module dict from ``circuit_loader.load_circuit``.
        fault_net:    Yosys net ID string (e.g. ``"6"``).
        fault_value:  ``0`` for SA0, ``1`` for SA1.
        good_map:     ``{net_id: sat_variable}`` (kept for API compat).
        summary_text: Optional compressed *_summary.txt content for context.

    Returns:
        A formatted prompt string ready to be sent as a user message.
    """
    name_map = get_signal_name_map(module_data)
    fault_name = name_map.get(fault_net, f"net{fault_net}")
    fault_type = f"stuck-at-{fault_value}"
    inputs = get_port_nets(module_data, "input")
    input_names = [_get_name(n, name_map) for n in inputs]

    parts = []

    parts.append(f"FAULT: {fault_name} {fault_type}")
    opposite = 1 - fault_value
    parts.append(
        f"To detect: find inputs where {fault_name}={opposite} in good circuit "
        f"and difference reaches an output."
    )
    parts.append("")
    parts.append(_describe_circuit_compact(module_data))
    parts.append("")
    parts.append(f"VALID INPUT SIGNALS: {', '.join(input_names)}")
    parts.append("")
    parts.append(
        'Return JSON: {"signal_assignments": {"<signal_name>": <0_or_1>, ...}, '
        '"sensitization_hint": "..."}'
    )

    return "\n".join(parts)
