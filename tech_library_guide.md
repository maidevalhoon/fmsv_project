# Technology Library Guide

## What Are These Libraries?

The **Nangate Open Cell Library** is a real, open-source 45nm standard-cell library used in EDA research. It ships in two file formats for two totally different purposes:

---

## 1. `NangateOpenCellLibrary.blackbox.v` — Verilog Blackbox Declarations

### What it is
A flat Verilog file with **~100 blackbox module stubs** — every cell in the library declared as a Verilog module but with **no logic body**. The `(* blackbox *)` attribute tells Yosys: *"I know this cell exists. Don't try to synthesize it — trust its name and interface."*

```verilog
(* blackbox *) module AND2_X1 (A1, A2, ZN);
  input A1; input A2; output ZN;
endmodule

(* blackbox *) module NAND3_X1 (A1, A2, A3, ZN);
  input A1; input A2; input A3; output ZN;
endmodule
```

### Port naming convention (critical for `cnf_builder.py`)
| Cell family | Input ports | Output port |
|---|---|---|
| AND/OR/NAND/NOR/XOR/XNOR | `A1, A2 [, A3, A4]` | `ZN` |
| INV (inverter) | `A` | `ZN` |
| BUF | `A` | `Z` |
| MUX2 | `A, B, S` | `Z` |
| XOR2 | `A, B` | `Z` |
| DFF (flip-flop) | `D, CK` | `Q, QN` |
| FA (full adder) | `A, B, CI` | `CO, S` |

> **Key difference from generic synthesis:** Yosys currently maps RTL to **generic cells** (`$_AND_`, `$_NOT_`, etc.) because `synth.ys` has no `techmap` step. When we add this library, Yosys maps to **real** cells like `AND2_X1`, `NAND3_X1`, etc. These have **different port names**.

---

### Before (current — without library)
```
synth.ys:
  read_verilog benchmarks/c17.v
  synth              ← maps to generic $-cells
  write_json benchmarks/json/c17.json

→ JSON cells have type "$_AND_", "$_NOT_", etc.
→ cnf_builder.py handles these fine.
```

### After (with library)
```
synth.ys:
  read_verilog benchmarks/c17.v
  read_verilog -lib "Technology Library/NangateOpenCellLibrary.blackbox.v"
  synth -top <module>
  dfflibmap -liberty "Technology Library/NangateOpenCellLibrary_typical.lib"
  abc -liberty "Technology Library/NangateOpenCellLibrary_typical.lib"
  write_json benchmarks/json/c17.json

→ JSON cells have type "AND2_X1", "NAND3_X1", "INV_X1", etc.
→ cnf_builder.py needs new aliases for these cell types.
```

---

## 2. `NangateOpenCellLibrary_typical.lib` — Liberty Timing File

### What it is
A 6 MB **Liberty format** (`.lib`) file describing every cell's:
- **Boolean function** → `function : "(A1 & A2)"` on pin `ZN`
- **Timing tables** → 7×7 lookup tables (input slew vs output capacitance) for `cell_rise`, `cell_fall`, `rise_transition`, `fall_transition`
- **Power tables** → `internal_power` per pin per transition direction
- **Area** → silicon area in arbitrary units

### Where it is used
| Tool | Flag | Purpose |
|---|---|---|
| Yosys `abc` | `-liberty` | Technology mapping: maps logic cones to real cells minimizing area/delay |
| Yosys `dfflibmap` | `-liberty` | Maps flip-flop types to real DFF cells |
| STA tools (OpenSTA etc.) | input | Timing analysis after P&R |

### What this changes for ATPG
The `.lib` file gives ABC the **Boolean function** of each cell. ABC uses this to decide what gate implementation minimizes area. After mapping:
- An AND gate with 3 inputs might become `AND3_X1` (not two cascaded `AND2_X1`).
- An AOI (AND-OR-INVERT) cell like `AOI21_X1` condenses a whole logic cone into one cell.
- Complex cells like `OAI211_X1` appear — these are NOT in `cnf_builder.py` currently.

---

## Impact on `cnf_builder.py` — New Gate Aliases Needed

When synthesis uses the Nangate library, the JSON `"type"` field in cells will have **real library cell names** not Yosys generic names. The CNF builder must map them.

### Simple mappings (direct equivalents)
```
AND2_X1 / AND2_X2 / AND2_X4   →  $_AND_   (A1, A2 → ZN)
OR2_X1  / OR2_X2  / OR2_X4    →  $_OR_    (A1, A2 → ZN)
INV_X1  / INV_X2  ...         →  $_NOT_   (A → ZN)
BUF_X1  / BUF_X2  ...         →  $_BUF_   (A → Z)
NAND2_X1 / NAND2_X2 ...       →  $_NAND_  (A1, A2 → ZN)
NOR2_X1  / NOR2_X2 ...        →  $_NOR_   (A1, A2 → ZN)
XNOR2_X1 / XNOR2_X2           →  $_XNOR_  (A, B → ZN)
XOR2_X1  / XOR2_X2            →  $_XOR_   (A, B → Z)
MUX2_X1  / MUX2_X2            →  $_MUX_   (A, B, S → Z)
```

### 3/4-input gates — need decomposition in CNF builder
`AND3_X1`, `NAND3_X1`, `OR3_X1`, `NOR3_X1`, etc. are **not decomposed by Yosys** when using the Nangate library — they appear directly. `cnf_builder.py` must implement Tseitin clauses for these.

### Complex compound gates — new Tseitin encodings required
| Cell | Logic | Notes |
|---|---|---|
| `AOI21_X1` | `ZN = !((B1 & B2) | A)` | AND-OR-INVERT |
| `OAI21_X1` | `ZN = !((B1 | B2) & A)` | OR-AND-INVERT |
| `AOI22_X1` | `ZN = !((A1&A2) | (B1&B2))` | requires intermediate var |
| `OAI22_X1` | `ZN = !((A1|A2) & (B1|B2))` | requires intermediate var |

### What does NOT change
- `circuit_loader.py` — works on any cell type, just reads connections
- `miter.py` — purely structural, unaffected
- `fault_manager.py` — purely structural, unaffected

---

## Why Only ONE CNF File (`circuit_logic.cnf`)?

`benchmarks/cnf/circuit_logic.cnf` is a **leftover from a very early prototype** (a static, single gate-level CNF for `circuit.v` written out once). It has:
```
p cnf 5 6   ← 5 variables, 6 clauses
1 -3 0
2 -3 0
...
```
This represents a simple 2-AND → AND circuit. **It is not used by any current code.** The actual project generates CNF dynamically in memory via `cnf_builder.py` for every fault run:
1. **No CNF is ever written to disk** — clauses live as Python `list[list[int]]` objects.
2. **Why:** For ATPG, every fault needs a different CNF (the miter changes per fault — the faulty copy skips a different gate). Saving 200+ CNF files to disk is wasteful. 
3. **The single CNF file was an early sanity-check** from the prototype phase. It should be considered archived/irrelevant.

---

## Updated `synth.ys` — With Nangate Library

The synthesis script should be updated to use the library for all benchmarks. With library-mapped synthesis, Yosys generates **structurally optimal** gate netlists (using compound gates like AOI/OAI) instead of flat 2-input generic gates.

```
# For each benchmark, create a synth_<name>.ys or parameterize:
read_verilog benchmarks/c17.v
read_verilog -lib "Technology Library/NangateOpenCellLibrary.blackbox.v"
hierarchy -check -top c17
synth -top c17
dfflibmap -liberty "Technology Library/NangateOpenCellLibrary_typical.lib"
abc -liberty "Technology Library/NangateOpenCellLibrary_typical.lib"
clean
write_json benchmarks/json/c17.json
```

> **Readiness check:** Before enabling library synthesis, `cnf_builder.py` must be augmented with aliases and Tseitin clauses for 3-input gates and compound gates (AOI/OAI families). Otherwise, those cells will silently produce no clauses and generate wrong results.
