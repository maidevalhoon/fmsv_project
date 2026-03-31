"""
circuit_loader.py — Load Yosys JSON netlists and expose utility helpers.

This module is the single shared entry-point for all other ATPG modules that
need to read a circuit.  It contains only pure functions; no global state or
classes are used.
"""

import json
import sys

# ---------------------------------------------------------------------------
# Constants that Yosys uses for hard-wired logic values.  We skip these when
# building the list of "real" circuit nets.
# ---------------------------------------------------------------------------
_YOSYS_CONSTANTS = {"0", "1", "x", "z"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_circuit(json_file: str):
    """Load a Yosys JSON netlist and return the top-level module.

    The file must have been produced by Yosys' ``write_json`` command and
    contain at least one module entry.

    Args:
        json_file: Path to the ``.json`` netlist file.

    Returns:
        A ``(module_name, module_data)`` tuple where *module_name* is the
        Verilog module name (str) and *module_data* is the raw dict for that
        module as parsed from JSON.

    Raises:
        SystemExit: Prints a clear error message and exits if the file cannot
            be found or is not valid JSON.
    """
    try:
        with open(json_file, "r") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"[ERROR] Netlist file not found: {json_file!r}")
    except json.JSONDecodeError as exc:
        sys.exit(f"[ERROR] Could not parse JSON from {json_file!r}: {exc}")

    modules = data.get("modules", {})
    if not modules:
        sys.exit(f"[ERROR] No modules found in {json_file!r}")

    module_name = list(modules.keys())[0]
    module_data = modules[module_name]

    warnings = validate_netlist(module_data)
    for w in warnings:
        print(f"[WARN] {w}")

    return module_name, module_data


def get_port_nets(module_data: dict, direction: str) -> list:
    """Return all net IDs that belong to ports of the given direction.

    Args:
        module_data: The module dict returned by :func:`load_circuit`.
        direction:   ``"input"`` or ``"output"`` (matches Yosys JSON field).

    Returns:
        List of net ID strings (e.g. ``["2", "3", "4"]``) for every bit of
        every port whose direction matches *direction*.
    """
    nets = []
    for port_data in module_data.get("ports", {}).values():
        if port_data.get("direction") == direction:
            for bit in port_data.get("bits", []):
                nets.append(str(bit))
    return nets


def get_net_name_map(module_data: dict) -> dict:
    """Build a human-readable name mapping from Yosys ``netnames`` section.

    Yosys assigns numeric IDs to every wire; ``netnames`` records the
    original Verilog signal names alongside those IDs.

    Args:
        module_data: The module dict returned by :func:`load_circuit`.

    Returns:
        ``{net_id_str: signal_name}`` dict.  If a signal drives multiple
        bits, each bit gets its own entry (e.g. ``"7"`` → ``"w_and"``).
        Yosys constant tokens (``"0"``, ``"1"``, ``"x"``, ``"z"``) are
        silently skipped.
    """
    name_map = {}
    for signal_name, net_info in module_data.get("netnames", {}).items():
        for bit in net_info.get("bits", []):
            bit_str = str(bit)
            if bit_str not in _YOSYS_CONSTANTS:
                name_map[bit_str] = signal_name
    return name_map


def enumerate_all_nets(module_data: dict) -> list:
    """Return a sorted list of every unique net ID in the circuit.

    Nets are collected from:

    * all port bits (inputs and outputs), and
    * all cell connection bits (gate inputs and outputs).

    Yosys constant tokens (``"0"``, ``"1"``, ``"x"``, ``"z"``) are excluded
    because they do not correspond to real circuit wires.

    Args:
        module_data: The module dict returned by :func:`load_circuit`.

    Returns:
        Sorted list of unique net ID strings.
    """
    nets = set()

    # Collect from ports
    for port_data in module_data.get("ports", {}).values():
        for bit in port_data.get("bits", []):
            s = str(bit)
            if s not in ("0", "1", "x", "z"):
                nets.add(s)

    # Collect from cell connections
    for cell_data in module_data.get("cells", {}).values():
        for bits in cell_data.get("connections", {}).values():
            for bit in bits:
                nets.add(str(bit))

    # Strip Yosys constants
    nets -= _YOSYS_CONSTANTS

    return sorted(nets, key=lambda x: int(x) if x.isdigit() else x)


def find_driving_gate(module_data: dict, fault_net: str):
    """Find the cell whose output drives *fault_net*.

    A net is "driven" by a cell when it appears in that cell's ``Y``
    connection (the standard Yosys output port name for combinational gates).

    Args:
        module_data: The module dict returned by :func:`load_circuit`.
        fault_net:   Net ID string to look up (e.g. ``"6"``).

    Returns:
        The cell name (str) if a driving gate is found, or ``None`` if the
        net is a primary input (i.e. not driven by any gate).
    """
    for cell_name, cell_data in module_data.get("cells", {}).items():
        conn = cell_data.get("connections", {})
        # Check both 'Y' (generic/RTL gates) and 'ZN' (Nangate inverting cells)
        output_bits = conn.get("Y", []) or conn.get("ZN", [])
        if fault_net in [str(b) for b in output_bits]:
            return cell_name
    return None


# ---------------------------------------------------------------------------
# Netlist validation
# ---------------------------------------------------------------------------

def validate_netlist(module_data: dict) -> list[str]:
    """
    Check the netlist for common problems.
    Returns a list of warning strings. Empty list means all checks passed.
    """
    warnings = []
    ports = module_data.get("ports", {})
    cells = module_data.get("cells", {})

    inputs  = [p for p, d in ports.items() if d["direction"] == "input"]
    outputs = [p for p, d in ports.items() if d["direction"] == "output"]

    if not inputs:
        warnings.append("No input ports found")
    if not outputs:
        warnings.append("No output ports found")
    if not cells:
        warnings.append("No cells (gates) found — netlist may be empty")

    known_types = {
        # ── Yosys generic / RTL names ──────────────────────────────────────
        "$_AND_", "$_OR_", "$_NOT_", "$_BUF_",
        "$_NAND_", "$_NOR_", "$_XOR_", "$_XNOR_", "$_MUX_",
        "$and", "$or", "$not", "$buf",
        "$nand", "$nor", "$xor", "$xnor", "$mux",
        # ── Nangate standard cells (all drive strengths) ────────────────────
        "AND2_X1", "AND2_X2", "AND2_X4",
        "AND3_X1", "AND3_X2", "AND3_X4",
        "AND4_X1", "AND4_X2", "AND4_X4",
        "OR2_X1",  "OR2_X2",  "OR2_X4",
        "OR3_X1",  "OR3_X2",  "OR3_X4",
        "OR4_X1",  "OR4_X2",  "OR4_X4",
        "INV_X1",  "INV_X2",  "INV_X4",  "INV_X8",  "INV_X16", "INV_X32",
        "BUF_X1",  "BUF_X2",  "BUF_X4",  "BUF_X8",  "BUF_X16", "BUF_X32",
        "NAND2_X1", "NAND2_X2", "NAND2_X4",
        "NAND3_X1", "NAND3_X2", "NAND3_X4",
        "NAND4_X1", "NAND4_X2", "NAND4_X4",
        "NOR2_X1",  "NOR2_X2",  "NOR2_X4",
        "NOR3_X1",  "NOR3_X2",  "NOR3_X4",
        "NOR4_X1",  "NOR4_X2",  "NOR4_X4",
        "XOR2_X1",  "XOR2_X2",
        "XNOR2_X1", "XNOR2_X2",
        "MUX2_X1",  "MUX2_X2",
        "AOI21_X1", "AOI21_X2", "AOI21_X4",
        "AOI22_X1", "AOI22_X2", "AOI22_X4",
        "AOI211_X1", "AOI211_X2", "AOI211_X4",
        "AOI221_X1", "AOI221_X2", "AOI221_X4",
        "AOI222_X1", "AOI222_X2", "AOI222_X4",
        "OAI21_X1", "OAI21_X2", "OAI21_X4",
        "OAI22_X1", "OAI22_X2", "OAI22_X4",
        "OAI211_X1", "OAI211_X2", "OAI211_X4",
        "OAI221_X1", "OAI221_X2", "OAI221_X4",
        "OAI222_X1", "OAI222_X2", "OAI222_X4",
        "OAI33_X1",
    }
    unknown = set()
    for cell_name, cell_data in cells.items():
        gt = cell_data.get("type", "")
        if gt not in known_types:
            unknown.add(gt)
    if unknown:
        warnings.append(f"Unrecognized gate types (will be skipped in CNF): {sorted(unknown)}")

    return warnings


# ---------------------------------------------------------------------------
# Backward-compatibility shims
# (run_atpg.py imports these by their old names; kept so existing callers
#  don't break while we migrate to get_port_nets)
# ---------------------------------------------------------------------------

def get_input_nets(ports: dict) -> list:
    """(Deprecated) Return input net IDs.  Prefer ``get_port_nets``."""
    nets = []
    for port_data in ports.values():
        if port_data.get("direction") == "input":
            for bit in port_data.get("bits", []):
                nets.append(str(bit))
    return nets


def get_output_nets(ports: dict) -> list:
    """(Deprecated) Return output net IDs.  Prefer ``get_port_nets``."""
    nets = []
    for port_data in ports.values():
        if port_data.get("direction") == "output":
            for bit in port_data.get("bits", []):
                nets.append(str(bit))
    return nets
