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

Gate signal name convention (Yosys generic JSON)
-------------------------------------------------
  Two-input gates : A, B → inputs;  Y → output
  Single-input    : A    → input;   Y → output
  MUX             : A, B → data inputs (A=select=0, B=select=1);
                    S    → select;   Y → output

Nangate port convention (after normalization — see _normalize_conn)
-------------------------------------------------------------------
  AND/OR/NAND/NOR family : A1, A2 → ZN   normalized to  A, B → Y
  INV                    : A      → ZN   normalized to  A    → Y
  BUF                    : A      → Z    normalized to  A    → Y
  XOR2/XNOR2             : A, B   → Z/ZN normalized to  A, B → Y
  MUX2                   : A, B, S → Z  normalized to  A, B, S → Y
  3-input gates          : A1, A2, A3 → ZN  →  A, B, C → Y
  4-input gates          : A1, A2, A3, A4 → ZN  →  A, B, C, D → Y
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _net_str(raw) -> str:
    """Normalise a Yosys connection value to a plain net-ID string."""
    if isinstance(raw, list):
        raw = raw[0]
    return str(raw)


def _normalize_conn(conn: dict) -> dict:
    """
    Normalise a Nangate cell's connections dict to the generic port names
    used by the Tseitin logic below (A, B, C, D, S → Y).

    Handles:
      A1 → A,  A2 → B,  A3 → C,  A4 → D
      ZN → Y,  Z  → Y
    """
    rename = {"A1": "A", "A2": "B", "A3": "C", "A4": "D", "ZN": "Y", "Z": "Y"}
    return {rename.get(k, k): v for k, v in conn.items()}


# ---------------------------------------------------------------------------
# Gate alias table
# ---------------------------------------------------------------------------

# Maps Yosys RTL names AND every Nangate drive-strength variant → generic name.
# The Tseitin logic below works entirely in generic names.
_GATE_ALIASES: dict[str, str] = {
    # ── Yosys RTL / behavioural names ──────────────────────────────────────
    "$and":  "$_AND_",
    "$or":   "$_OR_",
    "$not":  "$_NOT_",
    "$buf":  "$_BUF_",
    "$nand": "$_NAND_",
    "$nor":  "$_NOR_",
    "$xor":  "$_XOR_",
    "$xnor": "$_XNOR_",
    "$mux":  "$_MUX_",

    # ── Nangate 2-input AND ─────────────────────────────────────────────────
    "AND2_X1": "$_AND_", "AND2_X2": "$_AND_", "AND2_X4": "$_AND_",

    # ── Nangate 3-input AND ─────────────────────────────────────────────────
    "AND3_X1": "$_AND3_", "AND3_X2": "$_AND3_", "AND3_X4": "$_AND3_",

    # ── Nangate 4-input AND ─────────────────────────────────────────────────
    "AND4_X1": "$_AND4_", "AND4_X2": "$_AND4_", "AND4_X4": "$_AND4_",

    # ── Nangate 2-input OR ──────────────────────────────────────────────────
    "OR2_X1": "$_OR_", "OR2_X2": "$_OR_", "OR2_X4": "$_OR_",

    # ── Nangate 3-input OR ──────────────────────────────────────────────────
    "OR3_X1": "$_OR3_", "OR3_X2": "$_OR3_", "OR3_X4": "$_OR3_",

    # ── Nangate 4-input OR ──────────────────────────────────────────────────
    "OR4_X1": "$_OR4_", "OR4_X2": "$_OR4_", "OR4_X4": "$_OR4_",

    # ── Nangate INV ─────────────────────────────────────────────────────────
    "INV_X1": "$_NOT_", "INV_X2": "$_NOT_",
    "INV_X4": "$_NOT_", "INV_X8": "$_NOT_", "INV_X16": "$_NOT_", "INV_X32": "$_NOT_",

    # ── Nangate BUF ─────────────────────────────────────────────────────────
    "BUF_X1": "$_BUF_", "BUF_X2": "$_BUF_", "BUF_X4": "$_BUF_",
    "BUF_X8": "$_BUF_", "BUF_X16": "$_BUF_", "BUF_X32": "$_BUF_",

    # ── Nangate 2-input NAND ────────────────────────────────────────────────
    "NAND2_X1": "$_NAND_", "NAND2_X2": "$_NAND_", "NAND2_X4": "$_NAND_",

    # ── Nangate 3-input NAND ────────────────────────────────────────────────
    "NAND3_X1": "$_NAND3_", "NAND3_X2": "$_NAND3_", "NAND3_X4": "$_NAND3_",

    # ── Nangate 4-input NAND ────────────────────────────────────────────────
    "NAND4_X1": "$_NAND4_", "NAND4_X2": "$_NAND4_", "NAND4_X4": "$_NAND4_",

    # ── Nangate 2-input NOR ─────────────────────────────────────────────────
    "NOR2_X1": "$_NOR_", "NOR2_X2": "$_NOR_", "NOR2_X4": "$_NOR_",

    # ── Nangate 3-input NOR ─────────────────────────────────────────────────
    "NOR3_X1": "$_NOR3_", "NOR3_X2": "$_NOR3_", "NOR3_X4": "$_NOR3_",

    # ── Nangate 4-input NOR ─────────────────────────────────────────────────
    "NOR4_X1": "$_NOR4_", "NOR4_X2": "$_NOR4_", "NOR4_X4": "$_NOR4_",

    # ── Nangate XOR2 / XNOR2 ───────────────────────────────────────────────
    "XOR2_X1": "$_XOR_",  "XOR2_X2": "$_XOR_",
    "XNOR2_X1": "$_XNOR_", "XNOR2_X2": "$_XNOR_",

    # ── Nangate MUX2 ───────────────────────────────────────────────────────
    "MUX2_X1": "$_MUX_", "MUX2_X2": "$_MUX_",

    # ── Nangate AOI/OAI compound gates ─────────────────────────────────────
    "AOI21_X1": "$_AOI21_", "AOI21_X2": "$_AOI21_", "AOI21_X4": "$_AOI21_",
    "AOI22_X1": "$_AOI22_", "AOI22_X2": "$_AOI22_", "AOI22_X4": "$_AOI22_",
    "OAI21_X1": "$_OAI21_", "OAI21_X2": "$_OAI21_", "OAI21_X4": "$_OAI21_",
    "OAI22_X1": "$_OAI22_", "OAI22_X2": "$_OAI22_", "OAI22_X4": "$_OAI22_",
    # Additional AOI/OAI variants present in Nangate
    "AOI211_X1": "$_AOI211_", "AOI211_X2": "$_AOI211_", "AOI211_X4": "$_AOI211_",
    "AOI221_X1": "$_AOI221_", "AOI221_X2": "$_AOI221_", "AOI221_X4": "$_AOI221_",
    "AOI222_X1": "$_AOI222_", "AOI222_X2": "$_AOI222_", "AOI222_X4": "$_AOI222_",
    "OAI211_X1": "$_OAI211_", "OAI211_X2": "$_OAI211_", "OAI211_X4": "$_OAI211_",
    "OAI221_X1": "$_OAI221_", "OAI221_X2": "$_OAI221_", "OAI221_X4": "$_OAI221_",
    "OAI222_X1": "$_OAI222_", "OAI222_X2": "$_OAI222_", "OAI222_X4": "$_OAI222_",
    "OAI33_X1":  "$_OAI33_",
}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def build_circuit_cnf(cells, var_offset=0, skip_gate=None):
    """Encode a Yosys netlist gate-by-gate into Tseitin CNF clauses.

    Args:
        cells (dict):
            The ``"cells"`` dict from a Yosys JSON module.
        var_offset (int):
            Shift applied to every allocated variable ID.  Use ``0`` for the
            *good* circuit copy and ``good_max_var - 1`` for the *faulty* copy.
        skip_gate (str | None):
            If given, allocate variable IDs for that gate's wires but emit
            **no** Tseitin clauses for it.

    Returns:
        clauses (list[list[int]]):
            All Tseitin clauses (PySAT convention — signed integers).
        net_to_var (dict[str, int]):
            Maps every Yosys net-ID string to its PySAT variable integer.
        next_free_var (int):
            First variable ID not yet used.
    """
    net_to_var: dict[str, int] = {}
    _next = [1 + var_offset]          # mutable int inside closure
    clauses: list[list[int]] = []
    unknown_count = 0

    # ------------------------------------------------------------------
    def get_var(raw) -> int:
        """Return (allocating if needed) the SAT variable for a net."""
        nid = _net_str(raw)
        if nid not in net_to_var:
            net_to_var[nid] = _next[0]
            _next[0] += 1
        return net_to_var[nid]

    def fresh_var(synthetic_key: str) -> int:
        """Allocate a fresh Tseitin intermediate variable."""
        net_to_var[synthetic_key] = _next[0]
        _next[0] += 1
        return net_to_var[synthetic_key]
    # ------------------------------------------------------------------

    for cell_name, cell_data in cells.items():
        raw_type = cell_data["type"]
        gate_type = _GATE_ALIASES.get(raw_type, raw_type)   # normalise alias

        # Apply port-name normalization for Nangate cells
        raw_conn = cell_data["connections"]
        conn = _normalize_conn(raw_conn)

        # ── $_AND_  ────────────────────────────────────────────────────────
        # Boolean: Y = A ∧ B
        # Tseitin: (A ∨ ¬Y) ∧ (B ∨ ¬Y) ∧ (¬A ∨ ¬B ∨ Y)
        if gate_type == "$_AND_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([ a, -y])
                clauses.append([ b, -y])
                clauses.append([-a, -b,  y])

        # ── $_OR_  ─────────────────────────────────────────────────────────
        # Boolean: Y = A ∨ B
        # Tseitin: (¬A ∨ Y) ∧ (¬B ∨ Y) ∧ (A ∨ B ∨ ¬Y)
        elif gate_type == "$_OR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a,  y])
                clauses.append([-b,  y])
                clauses.append([ a,  b, -y])

        # ── $_NOT_  ────────────────────────────────────────────────────────
        # Boolean: Y = ¬A
        # Tseitin: (A ∨ Y) ∧ (¬A ∨ ¬Y)
        elif gate_type == "$_NOT_":
            a, y = get_var(conn["A"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([ a,  y])
                clauses.append([-a, -y])

        # ── $_BUF_  ────────────────────────────────────────────────────────
        # Boolean: Y = A
        # Tseitin: (¬A ∨ Y) ∧ (A ∨ ¬Y)
        elif gate_type == "$_BUF_":
            a, y = get_var(conn["A"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a,  y])
                clauses.append([ a, -y])

        # ── $_NAND_  ───────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∧ B)
        # Tseitin: (¬A ∨ ¬B ∨ ¬Y) ∧ (A ∨ Y) ∧ (B ∨ Y)
        elif gate_type == "$_NAND_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b, -y])
                clauses.append([ a,  y])
                clauses.append([ b,  y])

        # ── $_NOR_  ────────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∨ B)
        # Tseitin: (¬A ∨ ¬Y) ∧ (¬B ∨ ¬Y) ∧ (A ∨ B ∨ Y)
        elif gate_type == "$_NOR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -y])
                clauses.append([-b, -y])
                clauses.append([ a,  b,  y])

        # ── $_XOR_  ────────────────────────────────────────────────────────
        # Boolean: Y = A ⊕ B
        elif gate_type == "$_XOR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b, -y])
                clauses.append([ a,  b, -y])
                clauses.append([ a, -b,  y])
                clauses.append([-a,  b,  y])

        # ── $_XNOR_  ───────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ⊕ B)
        elif gate_type == "$_XNOR_":
            a, b, y = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b,  y])
                clauses.append([ a,  b,  y])
                clauses.append([ a, -b, -y])
                clauses.append([-a,  b, -y])

        # ── $_MUX_  ────────────────────────────────────────────────────────
        # Boolean: Y = S ? B : A
        elif gate_type == "$_MUX_":
            a = get_var(conn["A"])
            b = get_var(conn["B"])
            s = get_var(conn["S"])
            y = get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-s, -b,  y])
                clauses.append([-s,  b, -y])
                clauses.append([ s, -a,  y])
                clauses.append([ s,  a, -y])
                clauses.append([-a, -b,  y])
                clauses.append([ a,  b, -y])

        # ── $_AND3_  ───────────────────────────────────────────────────────
        # Boolean: Y = A ∧ B ∧ C
        # Tseitin: [A,¬Y][B,¬Y][C,¬Y][¬A,¬B,¬C,Y]
        elif gate_type == "$_AND3_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            y = get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([ a,       -y])
                clauses.append([ b,       -y])
                clauses.append([ c,       -y])
                clauses.append([-a, -b, -c, y])

        # ── $_NAND3_  ──────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∧ B ∧ C)
        # Tseitin: [¬A,¬B,¬C,¬Y][A,Y][B,Y][C,Y]
        elif gate_type == "$_NAND3_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            y = get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b, -c, -y])
                clauses.append([ a,          y])
                clauses.append([ b,          y])
                clauses.append([ c,          y])

        # ── $_OR3_  ────────────────────────────────────────────────────────
        # Boolean: Y = A ∨ B ∨ C
        # Tseitin: [¬A,Y][¬B,Y][¬C,Y][A,B,C,¬Y]
        elif gate_type == "$_OR3_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            y = get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a,  y])
                clauses.append([-b,  y])
                clauses.append([-c,  y])
                clauses.append([ a,  b,  c, -y])

        # ── $_NOR3_  ───────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∨ B ∨ C)
        # Tseitin: [¬A,¬Y][¬B,¬Y][¬C,¬Y][A,B,C,Y]
        elif gate_type == "$_NOR3_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            y = get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -y])
                clauses.append([-b, -y])
                clauses.append([-c, -y])
                clauses.append([ a,  b,  c,  y])

        # ── $_AND4_  ───────────────────────────────────────────────────────
        # Boolean: Y = A ∧ B ∧ C ∧ D
        # Tseitin: [A,¬Y][B,¬Y][C,¬Y][D,¬Y][¬A,¬B,¬C,¬D,Y]
        elif gate_type == "$_AND4_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            d, y   = get_var(conn["D"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([ a,             -y])
                clauses.append([ b,             -y])
                clauses.append([ c,             -y])
                clauses.append([ d,             -y])
                clauses.append([-a, -b, -c, -d,  y])

        # ── $_NAND4_  ──────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∧ B ∧ C ∧ D)
        # Tseitin: [¬A,¬B,¬C,¬D,¬Y][A,Y][B,Y][C,Y][D,Y]
        elif gate_type == "$_NAND4_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            d, y   = get_var(conn["D"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -b, -c, -d, -y])
                clauses.append([ a,              y])
                clauses.append([ b,              y])
                clauses.append([ c,              y])
                clauses.append([ d,              y])

        # ── $_OR4_  ────────────────────────────────────────────────────────
        # Boolean: Y = A ∨ B ∨ C ∨ D
        elif gate_type == "$_OR4_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            d, y   = get_var(conn["D"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a,  y])
                clauses.append([-b,  y])
                clauses.append([-c,  y])
                clauses.append([-d,  y])
                clauses.append([ a,  b,  c,  d, -y])

        # ── $_NOR4_  ───────────────────────────────────────────────────────
        # Boolean: Y = ¬(A ∨ B ∨ C ∨ D)
        elif gate_type == "$_NOR4_":
            a, b, c = get_var(conn["A"]), get_var(conn["B"]), get_var(conn["C"])
            d, y   = get_var(conn["D"]), get_var(conn["Y"])
            if cell_name != skip_gate:
                clauses.append([-a, -y])
                clauses.append([-b, -y])
                clauses.append([-c, -y])
                clauses.append([-d, -y])
                clauses.append([ a,  b,  c,  d,  y])

        # ── $_AOI21_  ──────────────────────────────────────────────────────
        # ZN = ¬((B1 ∧ B2) ∨ A)
        # Nangate ports: A, B1, B2 → ZN
        # After _normalize_conn: A stays A; B1, B2 stay B1, B2; ZN → Y
        #
        # Introduce intermediate m = B1 ∧ B2
        #   AND(B1,B2,m): [B1,¬m][B2,¬m][¬B1,¬B2,m]
        # Then ZN = NOR(m, A):
        #   NOR(m,A,ZN): [¬m,¬ZN][¬A,¬ZN][m,A,ZN]
        elif gate_type == "$_AOI21_":
            a   = get_var(raw_conn.get("A",  raw_conn.get("A")))
            b1  = get_var(raw_conn.get("B1", raw_conn.get("B1")))
            b2  = get_var(raw_conn.get("B2", raw_conn.get("B2")))
            zn  = get_var(raw_conn.get("ZN", raw_conn.get("Y")))
            if cell_name != skip_gate:
                m = fresh_var(f"__tseitin_{cell_name}_m")
                # m = B1 ∧ B2
                clauses.append([ b1,      -m])
                clauses.append([ b2,      -m])
                clauses.append([-b1, -b2,  m])
                # ZN = ¬(m ∨ A)  → NOR(m, a, zn)
                clauses.append([-m,       -zn])
                clauses.append([-a,       -zn])
                clauses.append([ m,   a,   zn])

        # ── $_OAI21_  ──────────────────────────────────────────────────────
        # ZN = ¬((B1 ∨ B2) ∧ A)
        # Introduce m = B1 ∨ B2, then ZN = NAND(m, A)
        elif gate_type == "$_OAI21_":
            a   = get_var(raw_conn.get("A",  raw_conn.get("A")))
            b1  = get_var(raw_conn.get("B1", raw_conn.get("B1")))
            b2  = get_var(raw_conn.get("B2", raw_conn.get("B2")))
            zn  = get_var(raw_conn.get("ZN", raw_conn.get("Y")))
            if cell_name != skip_gate:
                m = fresh_var(f"__tseitin_{cell_name}_m")
                # m = B1 ∨ B2
                clauses.append([-b1,  m])
                clauses.append([-b2,  m])
                clauses.append([ b1, b2, -m])
                # ZN = NAND(m, A)
                clauses.append([-m,  -a, -zn])
                clauses.append([ m,       zn])
                clauses.append([ a,       zn])

        # ── $_AOI22_  ──────────────────────────────────────────────────────
        # ZN = ¬((A1 ∧ A2) ∨ (B1 ∧ B2))
        # Introduce m1 = A1∧A2, m2 = B1∧B2, then ZN = NOR(m1, m2)
        elif gate_type == "$_AOI22_":
            a1  = get_var(raw_conn.get("A1"))
            a2  = get_var(raw_conn.get("A2"))
            b1  = get_var(raw_conn.get("B1"))
            b2  = get_var(raw_conn.get("B2"))
            zn  = get_var(raw_conn.get("ZN", raw_conn.get("Y")))
            if cell_name != skip_gate:
                m1 = fresh_var(f"__tseitin_{cell_name}_m1")
                m2 = fresh_var(f"__tseitin_{cell_name}_m2")
                # m1 = A1 ∧ A2
                clauses.append([ a1,       -m1])
                clauses.append([ a2,       -m1])
                clauses.append([-a1, -a2,   m1])
                # m2 = B1 ∧ B2
                clauses.append([ b1,       -m2])
                clauses.append([ b2,       -m2])
                clauses.append([-b1, -b2,   m2])
                # ZN = NOR(m1, m2)
                clauses.append([-m1,       -zn])
                clauses.append([-m2,       -zn])
                clauses.append([ m1,  m2,   zn])

        # ── $_OAI22_  ──────────────────────────────────────────────────────
        # ZN = ¬((A1 ∨ A2) ∧ (B1 ∨ B2))
        # Introduce m1 = A1∨A2, m2 = B1∨B2, then ZN = NAND(m1, m2)
        elif gate_type == "$_OAI22_":
            a1  = get_var(raw_conn.get("A1"))
            a2  = get_var(raw_conn.get("A2"))
            b1  = get_var(raw_conn.get("B1"))
            b2  = get_var(raw_conn.get("B2"))
            zn  = get_var(raw_conn.get("ZN", raw_conn.get("Y")))
            if cell_name != skip_gate:
                m1 = fresh_var(f"__tseitin_{cell_name}_m1")
                m2 = fresh_var(f"__tseitin_{cell_name}_m2")
                # m1 = A1 ∨ A2
                clauses.append([-a1,       m1])
                clauses.append([-a2,       m1])
                clauses.append([ a1,  a2, -m1])
                # m2 = B1 ∨ B2
                clauses.append([-b1,       m2])
                clauses.append([-b2,       m2])
                clauses.append([ b1,  b2, -m2])
                # ZN = NAND(m1, m2)
                clauses.append([-m1, -m2, -zn])
                clauses.append([ m1,       zn])
                clauses.append([ m2,       zn])

        # ── $_AOI211_  ─────────────────────────────────────────────────────
        # ZN = ¬((C1 ∧ C2) ∨ B ∨ A)
        # Introduce m = C1∧C2, then ZN = NOR(m, B, A)
        elif gate_type == "$_AOI211_":
            a   = get_var(raw_conn.get("A"))
            b   = get_var(raw_conn.get("B"))
            c1  = get_var(raw_conn.get("C1"))
            c2  = get_var(raw_conn.get("C2"))
            zn  = get_var(raw_conn.get("ZN", raw_conn.get("Y")))
            if cell_name != skip_gate:
                m = fresh_var(f"__tseitin_{cell_name}_m")
                # m = C1 ∧ C2
                clauses.append([ c1,       -m])
                clauses.append([ c2,       -m])
                clauses.append([-c1, -c2,   m])
                # ZN = NOR(m, b, a) → ¬(m ∨ b ∨ a)
                clauses.append([-m,            -zn])
                clauses.append([-b,            -zn])
                clauses.append([-a,            -zn])
                clauses.append([ m,   b,   a,  zn])

        # ── $_OAI211_  ─────────────────────────────────────────────────────
        # ZN = ¬((C1 ∨ C2) ∧ B ∧ A)
        # Introduce m = C1∨C2, then ZN = ¬(m ∧ B ∧ A) = NAND3(m,B,A)
        elif gate_type == "$_OAI211_":
            a   = get_var(raw_conn.get("A"))
            b   = get_var(raw_conn.get("B"))
            c1  = get_var(raw_conn.get("C1"))
            c2  = get_var(raw_conn.get("C2"))
            zn  = get_var(raw_conn.get("ZN", raw_conn.get("Y")))
            if cell_name != skip_gate:
                m = fresh_var(f"__tseitin_{cell_name}_m")
                # m = C1 ∨ C2
                clauses.append([-c1,  m])
                clauses.append([-c2,  m])
                clauses.append([ c1, c2, -m])
                # ZN = NAND3(m, b, a)
                clauses.append([-m,  -b,  -a, -zn])
                clauses.append([ m,           zn])
                clauses.append([ b,           zn])
                clauses.append([ a,           zn])

        else:
            # Allocate wires so downstream code can still reference them,
            # but emit no clauses and warn loudly.
            for port_bits in raw_conn.values():
                get_var(port_bits)
            unknown_count += 1
            print(f"[WARN] Unrecognized gate type '{gate_type}' in cell "
                  f"'{cell_name}' — no clauses added. CNF may be incomplete.")

    if unknown_count > 0:
        print(f"[ERROR] {unknown_count} gate(s) produced no CNF clauses. "
              f"Results unreliable.")

    return clauses, net_to_var, _next[0]
