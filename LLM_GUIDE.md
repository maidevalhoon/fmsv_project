# LLM-Guided SAT-ATPG — Architecture & Flow Guide

## Overview

This project implements a **two-step** SAT-based Automatic Test Pattern Generation (ATPG) engine for combinational circuits:

- **Step 1 (Baseline):** Pure SAT-based ATPG using Glucose3. No ML involved.
- **Step 2 (LLM-Guided):** Uses Google Gemini to generate input-assignment hints that are fed as **PySAT assumptions** to accelerate SAT solving.

The key insight: if the LLM can correctly guess even a partial input assignment, the SAT solver has fewer decisions to make, reducing solve time.

---

## Architecture Diagram

```
                     ┌──────────────────────┐
                     │   benchmarks/*.v      │   (Verilog RTL)
                     └──────────┬───────────┘
                                │  Yosys synthesis (synth/synth.ys)
                                ▼
                     ┌──────────────────────┐
                     │  benchmarks/json/*.json│  (Yosys JSON netlist)
                     └──────────┬───────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
            ▼                   ▼                   ▼
   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
   │circuit_loader.py│  │ cnf_builder.py │  │fault_manager.py│
   │  Load & parse   │  │ Tseitin encode │  │ Enumerate faults│
   │  Yosys JSON     │  │ gate → CNF     │  │ SA0/SA1 per net │
   └────────┬───────┘  └────────┬───────┘  └────────┬───────┘
            │                   │                   │
            └───────────────────┼───────────────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │     miter.py         │
                     │  Build GOOD + FAULTY │
                     │  circuit copies with │
                     │  XOR miter on outputs│
                     └──────────┬───────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                                   │
              ▼                                   ▼
     ┌─────────────────┐               ┌──────────────────┐
     │  Step 1: run_atpg│               │  Step 2: LLM-ATPG│
     │  Pure SAT solve  │               │                  │
     │  (Glucose3)      │               │  query_builder.py│ ── builds compact prompt
     │                  │               │       │          │
     │  ► reports/      │               │       ▼          │
     │    *_insights.txt│               │  Gemini API call │
     │                  │               │       │          │
     └─────────────────┘               │       ▼          │
                                       │ hint_translator.py│ ── parse JSON → assumptions
                                       │       │          │
                                       │       ▼          │
                                       │  evaluator.py    │ ── Phase 1: guided solve
                                       │                  │    Phase 2: fallback
                                       │  ► reports/      │
                                       │  *_llm_comparison│
                                       └──────────────────┘
```

---

## File-by-File Breakdown

### `core/circuit_loader.py`
**Purpose:** Parse Yosys JSON netlists into Python dicts.

Key functions:
| Function | What it does |
|---|---|
| `load_circuit(json_file)` | Reads JSON, returns `(module_name, module_data)` |
| `get_port_nets(module_data, "input")` | Returns list of primary input net IDs |
| `get_port_nets(module_data, "output")` | Returns list of primary output net IDs |
| `enumerate_all_nets(module_data)` | ALL nets including Yosys intermediates |
| `enumerate_verilog_nets(module_data)` | Only original Verilog wires (`hide_name: 0`) |
| `get_signal_name_map(module_data)` | net_id → signal name (Verilog names only) |
| `find_driving_gate(module_data, net_id)` | Which cell drives this net? (checks Y/ZN/Z) |

**Important:** `enumerate_verilog_nets` filters out Yosys-generated intermediate wires.
For c17: original Verilog has 11 wires; Yosys creates 17 (6 extra from NAND→AND+NOT decomposition). We only test faults on the original 11.

### `core/cnf_builder.py`
**Purpose:** Tseitin-encode every gate into CNF clauses.

How it works:
1. For each gate, allocate SAT variable IDs for its input/output nets
2. Emit Tseitin clauses that constrain the output to match the Boolean function
3. `skip_gate` parameter: allocate variables but emit NO clauses (used for the driving gate of the fault net in the faulty circuit copy)

Supported gate families:
- Generic Yosys: `$_AND_`, `$_OR_`, `$_NOT_`, `$_BUF_`, `$_NAND_`, `$_NOR_`, `$_XOR_`, `$_XNOR_`, `$_MUX_`
- 3/4-input variants: `$_AND3_`, `$_NAND3_`, `$_OR3_`, `$_NOR3_`, `$_AND4_`, etc.
- Nangate compound: `$_AOI21_`, `$_OAI21_`, `$_AOI22_`, `$_OAI22_`, `$_AOI211_`, `$_OAI211_`, `$_AOI221_`, `$_OAI221_`, `$_AOI222_`, `$_OAI222_`, `$_OAI33_`

Port normalization: `_normalize_conn` maps Nangate ports (A1,A2→ZN) to generic names (A,B→Y).

### `core/miter.py`
**Purpose:** Build the complete miter formula for one stuck-at fault.

Steps:
1. Encode GOOD circuit (var_offset=0, variables start at 1)
2. Encode FAULTY circuit (var_offset=good_next-1, starts right after good)
3. Skip the driving gate in the faulty copy (its clauses would contradict the fault)
4. Inject fault unit clause: `[-fault_var]` for SA0, `[+fault_var]` for SA1
5. Tie primary inputs (good_A == faulty_A), except the fault net for PI faults
6. XOR primary outputs: D = good_Y ⊕ faulty_Y (Tseitin, 4 clauses per output)
7. Add `OR(all D_vars)` — force at least one output to differ

### `core/fault_manager.py`
**Purpose:** Enumerate all faults and extract test vectors from SAT models.

- `enumerate_stuck_at_faults(module_data, verilog_only=True)`: Returns `[(net_id, 0), (net_id, 1), ...]` for every target wire
- `extract_test_vector(model, good_map, input_nets)`: Maps PySAT model → input values
- `extract_output_diff(model, good_map, faulty_map, output_nets)`: Shows good vs faulty output values

### `llm/query_builder.py`
**Purpose:** Build a compact LLM prompt for one stuck-at fault.

Token-efficiency strategy:
- Uses **original Verilog signal names** (N1, N10, …), not Yosys net IDs
- **Collapses** Yosys AND+NOT decompositions back into NAND gates
- **Omits** SAT variable maps — the LLM reasons in circuit terms, not solver internals
- Prompt for c17 is ~150 tokens (was ~800 before optimization)

Example prompt for SA0 on N1 in c17:
```
FAULT: N1 stuck-at-0
To detect: find inputs where N1=1 in good circuit and difference reaches an output.

INPUTS: N1, N2, N3, N6, N7
OUTPUTS: N22, N23
GATES:
  N10 = NAND(N1, N3)
  N11 = NAND(N3, N6)
  N16 = NAND(N2, N11)
  N19 = NAND(N11, N7)
  N22 = NAND(N10, N16)
  N23 = NAND(N16, N19)

VALID INPUT SIGNALS: N1, N2, N3, N6, N7

Return JSON: {"signal_assignments": {"<signal_name>": <0_or_1>, ...}, "sensitization_hint": "..."}
```

### `llm/hint_translator.py`
**Purpose:** Parse LLM JSON response → PySAT assumptions.

Safety features:
1. **Robust JSON extraction:** Handles code fences, extra prose, malformed JSON
2. **Name resolution:** Accepts both signal names ("N1") and net IDs ("2")
3. **PI-only validation:** Rejects assignments to internal nets (these would contradict gate Tseitin clauses and cause false UNSAT)
4. **Graceful degradation:** On any error, returns `([], "")` — SAT solves unaided

### `llm/evaluator.py`
**Purpose:** Two-phase SAT solve with LLM guidance.

- **Phase 1 (Guided):** `solver.solve(assumptions=llm_hints)`
  - SAT → fault detected with LLM help (`used_hints=True`)
  - UNSAT → hints were wrong, fall to Phase 2
- **Phase 2 (Fallback):** `solver.solve()` — identical to Step 1 baseline

### `llm/run_llm_atpg.py`
**Purpose:** Main driver that orchestrates the LLM-guided ATPG loop.

For each fault:
1. Build miter CNF (same as Step 1)
2. Build compact prompt via `query_builder.build_fault_prompt`
3. Call Gemini API (with rate-limit retry)
4. Parse response via `hint_translator.translate_hints`
5. Run two-phase solve via `evaluator.run_guided_fault`
6. Compare decisions/time against Step 1 baseline
7. Write comparison report to `reports/c17_llm_comparison.txt`

---

## How Faults Work (Miter Construction)

For a stuck-at-0 fault on wire W:

```
GOOD CIRCUIT                    FAULTY CIRCUIT
┌─────────────┐                ┌─────────────┐
│ All gates    │                │ All gates    │
│ encoded      │                │ EXCEPT the   │
│ normally     │                │ gate driving │
│              │                │ W (skipped)  │
│    W = f(…)  │                │    W = 0     │ ← forced by unit clause
└──────┬──────┘                └──────┬──────┘
       │                              │
       │    shared primary inputs     │
       │ ◄────────────────────────► │
       │                              │
       ▼                              ▼
    good_out                      faulty_out
       │                              │
       └──────── XOR ────────────────┘
                  │
                  ▼
              D (must = 1 for at least one output)
```

If SAT: the model gives a test vector that detects the fault.
If UNSAT: the fault is redundant (cannot be detected by any input).

---

## Verilog-Only Faults (The hide_name Filter)

When Yosys synthesizes `nand NAND2_1 (N10, N1, N3)`, it decomposes into:
- `$and$c17.v:16$1`: AND(N1, N3) → **hidden wire 9**
- `$not$c17.v:16$2`: NOT(wire 9) → **N10** (visible)

Wire 9 is an implementation artifact with `"hide_name": 1` in the JSON.
N10 is the actual circuit wire with `"hide_name": 0`.

We only test faults on `hide_name: 0` wires because:
1. They match the original design intent
2. Faults on hidden wires are redundant with faults on visible wires
3. c17: 11 wires × 2 = 22 faults (vs 17 × 2 = 34 with hidden wires)

---

## Environment Setup

```bash
pip install python-sat google-genai
export GEMINI_API_KEY="AIza..."
```

See `COMMANDS.md` for all runnable commands.
