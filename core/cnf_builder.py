"""
cnf_builder.py — Tseitin CNF encoding for Yosys-parsed gate netlists.

CRITICAL DESIGN NOTE
--------------------
When injecting a stuck-at fault, the gate that *drives* the fault net must be
excluded from the faulty circuit's Tseitin encoding.  If we kept its clauses,
the gate's own encoding (e.g. "if A=1 ∧ B=1 → Y=1") would directly contradict
the fault clause ("Y=0"), making the formula trivially UNSAT.

``skip_gate`` handles this: variable IDs are still allocated for every wire of
the skipped gate (so the miter can reference them), but no Tseitin clauses are
emitted for it.

Gate signal name convention (Yosys JSON)
-----------------------------------------
  Two-input gates : A, B → inputs;  Y → output
  Single-input    : A    → input;   Y → output
  MUX             : A, B → data inputs (A=select=0, B=select=1);
                    S    → select;   Y → output
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _net_str(raw) -> str:
    """Normalise a Yosys connection value to a plain net-ID string.

    Yosys stores connections either as a bare integer or as a single-element
    list.  Both are valid; we always want the string of the integer.
    """
    if isinstance(raw, list):
        raw = raw[0]
    return str(raw)


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def build_circuit_cnf(cells, var_offset=0, skip_gate=None):
    """Encode a Yosys netlist gate-by-gate into Tseitin CNF clauses.

    Args:
        cells (dict):
            The ``"cells"`` dict from a Yosys JSON module — maps
            ``cell_name → {type, connections, …}``.
        var_offset (int):
            Shift applied to every allocated variable ID.  Use ``0`` for the
            *good* circuit copy and ``good_max_var - 1`` for the *faulty* copy
            so the two variable spaces never overlap.
        skip_gate (str | None):
            If given, allocate variable IDs for that gate's wires as usual but
            emit **no** Tseitin clauses for it.  This is the gate that drives
            the fault net; its behavior is replaced by the explicit fault unit
            clause added later by ``fault_manager.inject_fault``.

    Returns:
        clauses (list[list[int]]):
            All Tseitin clauses for the circuit (each clause is a list of
            signed integers — PySAT convention).
        net_to_var (dict[str, int]):
            Maps every Yosys net-ID string seen in *cells* to its PySAT
            variable integer (>= 1).
        next_free_var (int):
            The first variable ID **not** yet used; pass this to subsequent
            modules (miter, fault injection) so they allocate fresh variables.
    """
    net_to_var = {}
    _next = [1 + var_offset]          # mutable int inside closure
    clauses = []

    # ------------------------------------------------------------------
    def get_var(raw) -> int:
        """Return (allocating if needed) the SAT variable for a net."""
        nid = _net_str(raw)
        if nid not in net_to_var:
            net_to_var[nid] = _next[0]
            _next[0] += 1
        return net_to_var[nid]
    # ------------------------------------------------------------------

    # Map Yosys RTL/behavioral cell names → structural names used below.
    # Yosys emits "$and", "$or", "$not", "$xor" etc. (no underscores) when
    # reading RTL Verilog, but "$_AND_", "$_OR_" etc. (with underscores) when
    # using its standard-cell tech-mapping pass.  Both must be handled.
    _GATE_ALIASES: dict = {
        "$and":  "$_AND_",
        "$or":   "$_OR_",
        "$not":  "$_NOT_",
        "$buf":  "$_BUF_",
        "$nand": "$_NAND_",
        "$nor":  "$_NOR_",
        "$xor":  "$_XOR_",
        "$xnor": "$_XNOR_",
        "$mux":  "$_MUX_",
    }

    for cell_name, cell_data in cells.items():
        gate_type = cell_data["type"]
        gate_type = _GATE_ALIASES.get(gate_type, gate_type)   # normalise
        conn      = cell_data["connections"]

        # ── $_AND_  ────────────────────────────────────────────────────
        # Boolean: Y = A ∧ B
        # Tseitin: (¬A ∨ ¬B ∨ Y) ∧ (A ∨ ¬Y) ∧ (B ∨ ¬Y)
        if gate_type == "$_AND_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([ a, -y])        # A=0 → Y=0
                clauses.append([ b, -y])        # B=0 → Y=0
                clauses.append([-a, -b,  y])    # A=1 ∧ B=1 → Y=1

        # ── $_OR_  ─────────────────────────────────────────────────────
        # Boolean: Y = A ∨ B
        # Tseitin: (¬A ∨ Y) ∧ (¬B ∨ Y) ∧ (A ∨ B ∨ ¬Y)
        elif gate_type == "$_OR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a,  y])        # A=1 → Y=1
                clauses.append([-b,  y])        # B=1 → Y=1
                clauses.append([ a,  b, -y])    # A=0 ∧ B=0 → Y=0

        # ── $_NOT_  ────────────────────────────────────────────────────
        # Boolean: Y = ¬A
        # Tseitin: (A ∨ Y) ∧ (¬A ∨ ¬Y)
        elif gate_type == "$_NOT_":
            a, y = get_var(conn["A"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([ a,  y])        # A=0 → Y=1
                clauses.append([-a, -y])        # A=1 → Y=0

        # ── $_BUF_  ────────────────────────────────────────────────────
        # Boolean: Y = A  (buffer / wire)
        # Tseitin: (¬A ∨ Y) ∧ (A ∨ ¬Y)
        elif gate_type == "$_BUF_":
            a, y = get_var(conn["A"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a,  y])        # A=1 → Y=1
                clauses.append([ a, -y])        # A=0 → Y=0

        # ── $_NAND_  ───────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∧ B)
        # Tseitin: (Y ∨ ¬A ∨ ¬B)  ← wait, Y=NAND(A,B)
        #   Equivalently: ¬AND(A,B) = Y
        #   (Y ∨  A) — not right; use derived form:
        #   (¬A ∨ ¬B ∨ ¬Y) ∧ (A ∨ Y) ∧ (B ∨ Y)
        elif gate_type == "$_NAND_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b, -y])   # A=1 ∧ B=1 → Y=0
                clauses.append([ a,  y])        # A=0 → Y=1
                clauses.append([ b,  y])        # B=0 → Y=1

        # ── $_NOR_  ────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∨ B)
        # Tseitin: (¬A ∨ ¬Y) ∧ (¬B ∨ ¬Y) ∧ (A ∨ B ∨ Y)
        elif gate_type == "$_NOR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -y])        # A=1 → Y=0
                clauses.append([-b, -y])        # B=1 → Y=0
                clauses.append([ a,  b,  y])    # A=0 ∧ B=0 → Y=1

        # ── $_XOR_  ────────────────────────────────────────────────────
        # Boolean: Y = A ⊕ B
        # Tseitin: (¬A ∨ ¬B ∨ ¬Y) ∧ (A ∨ B ∨ ¬Y)
        #        ∧ (A ∨ ¬B ∨  Y) ∧ (¬A ∨ B ∨  Y)
        elif gate_type == "$_XOR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b, -y])   # A=B=1 → Y=0
                clauses.append([ a,  b, -y])   # A=B=0 → Y=0
                clauses.append([ a, -b,  y])   # A=0, B=1 → Y=1
                clauses.append([-a,  b,  y])   # A=1, B=0 → Y=1

        # ── $_XNOR_  ───────────────────────────────────────────────────
        # Boolean: Y = ¬(A ⊕ B)   (XNOR / equality)
        # Tseitin: (¬A ∨ ¬B ∨  Y) ∧ (A ∨ B ∨  Y)
        #        ∧ (A ∨ ¬B ∨ ¬Y) ∧ (¬A ∨ B ∨ ¬Y)
        elif gate_type == "$_XNOR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b,  y])   # A=B=1 → Y=1
                clauses.append([ a,  b,  y])   # A=B=0 → Y=1
                clauses.append([ a, -b, -y])   # A=0, B=1 → Y=0
                clauses.append([-a,  b, -y])   # A=1, B=0 → Y=0

        # ── $_MUX_  ────────────────────────────────────────────────────
        # Boolean: Y = S ? B : A      (A when S=0, B when S=1)
        # Tseitin has 6 clauses to capture all implications:
        #   (¬S ∨ ¬B ∨  Y)  B=1,S=1 → Y=1
        #   (¬S ∨  B ∨ ¬Y)  B=0,S=1 → Y=0
        #   ( S ∨ ¬A ∨  Y)  A=1,S=0 → Y=1
        #   ( S ∨  A ∨ ¬Y)  A=0,S=0 → Y=0
        #   (¬A ∨ ¬B ∨  Y)  A=B=1   → Y=1  (regardless of S)
        #   ( A ∨  B ∨ ¬Y)  A=B=0   → Y=0  (regardless of S)
        elif gate_type == "$_MUX_":
            a = get_var(conn["A"])
            b = get_var(conn["B"])
            s = get_var(conn["S"])
            y = get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-s, -b,  y])   # S=1 ∧ B=1 → Y=1
                clauses.append([-s,  b, -y])   # S=1 ∧ B=0 → Y=0
                clauses.append([ s, -a,  y])   # S=0 ∧ A=1 → Y=1
                clauses.append([ s,  a, -y])   # S=0 ∧ A=0 → Y=0
                clauses.append([-a, -b,  y])   # A=B=1 → Y=1
                clauses.append([ a,  b, -y])   # A=B=0 → Y=0

        else:
            # Allocate wires so downstream code can still reference them,
            # but do not add any clauses (over-constrained unknown gate).
            for port_bits in conn.values():
                get_var(port_bits)
            if cell_name != skip_gate:
                print(f"[WARNING] Unhandled gate type '{gate_type}' "
                      f"(cell '{cell_name}') — no clauses emitted.")

    return clauses, net_to_var, _next[0]
