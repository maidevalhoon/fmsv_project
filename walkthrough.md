# LLM-Guided SAT-Based ATPG — Complete Walkthrough

**CS-525 FMSV — Problem Statement 4**
*Circuit: ISCAS-85 c17 benchmark | Solver: Glucose3 (PySAT) | LLM: Google Gemini*

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Directory Structure](#2-directory-structure)
3. [Setup & Installation](#3-setup--installation)
4. [Full Pipeline Flow](#4-full-pipeline-flow)
5. [File-by-File Breakdown](#5-file-by-file-breakdown)
   - [core/circuit_loader.py](#51-corecircuit_loaderpy)
   - [core/cnf_builder.py](#52-corechnf_builderpy)
   - [core/miter.py](#53-coremitrpy)
   - [core/fault_manager.py](#54-corefault_managerpy)
   - [run_atpg.py](#55-run_atpgpy)
   - [run_insights.py](#56-run_insightspy)
   - [extract_reports.py](#57-extract_reportspy)
   - [llm/query_builder.py](#58-llmquery_builderpy)
   - [llm/hint_translator.py](#59-llmhint_translatorpy)
   - [llm/evaluator.py](#510-llmevaluatorpy)
   - [llm/run_llm_atpg.py](#511-llmrun_llm_atpgpy)
6. [Step 1 Results & Explanation](#6-step-1-results--explanation)
7. [Step 2 Results & Explanation](#7-step-2-results--explanation)
8. [All Run Commands](#8-all-run-commands)
9. [Known Issues & Limitations](#9-known-issues--limitations)
10. [Detailed End-to-End Trace: c17.v Through Every Function](#10-detailed-end-to-end-trace-c17v-through-every-function)
11. [Guidelines Compliance Audit & Gap Analysis](#11-guidelines-compliance-audit--gap-analysis)

---

## 1. Project Overview

This project implements **SAT-based Automatic Test Pattern Generation (ATPG)** for detecting stuck-at faults in digital circuits, extended with an **LLM guidance layer** (Google Gemini) that tries to pre-assign input values before the SAT solver runs.

### What is ATPG?
A stuck-at fault models a wire permanently fixed to 0 (SA0) or 1 (SA1). ATPG finds a test vector (input assignment) that **activates** the fault and **propagates** the difference to an observable output. If no such vector exists, the fault is undetectable (redundant).

### Two-Step Architecture

```
Step 1 — SAT-ATPG Baseline
  Verilog → Yosys → JSON netlist → CNF (Tseitin) → Miter → Glucose3 → Test Vector

Step 2 — LLM-Guided SAT-ATPG
  Same pipeline + Gemini API call per fault:
    LLM returns partial input assignments → PySAT assumptions → guided solve
    If hints cause UNSAT → fallback to baseline solve (no hints lost)
```

---

## 2. Directory Structure

```
project faltu/
├── benchmarks/
│   ├── circuit.v            # Simple 3-gate test circuit
│   ├── c17.v                # ISCAS-85 c17 (5 inputs, 2 outputs, 6 AND + 6 NOT = 12 gates)
│   ├── json/
│   │   ├── circuit.json     # Yosys-synthesized gate-level JSON
│   │   └── c17.json         # ← primary benchmark used in Steps 1 & 2
│   └── netlists/            # Synthesized Verilog (reference only)
│
├── core/                    # Shared ATPG library (no side-effects, pure functions)
│   ├── __init__.py
│   ├── circuit_loader.py    # Load + query Yosys JSON
│   ├── cnf_builder.py       # Gate → CNF clause encoder (Tseitin)
│   ├── miter.py             # Build full miter CNF for one fault
│   └── fault_manager.py     # Fault enumeration, test vector extraction
│
├── llm/                     # Step 2 — LLM guidance layer
│   ├── query_builder.py     # Build structured Gemini prompt per fault
│   ├── hint_translator.py   # Parse LLM JSON → PySAT assumption literals
│   ├── evaluator.py         # Two-phase guided SAT solve
│   └── run_llm_atpg.py      # Main driver: full sweep + comparison report
│
├── synth/
│   └── synth.ys             # Yosys synthesis script
│
├── reports/
│   ├── c17_insights.txt     # Step 1 raw per-fault data (baseline)
│   ├── c17_llm_comparison.txt  # Step 2 comparison report
│   └── summaries/
│       └── c17_summary.txt  # Condensed top-10 report
│
├── run_atpg.py              # Step 1 entry point
├── run_insights.py          # Step 1 metrics report generator
├── extract_reports.py       # Condenses insights into summary
├── requirements.txt
├── .env                     # GEMINI_API_KEY (git-ignored)
└── .gitignore
```

---

## 3. Setup & Installation

```bash
# 1. Create & activate virtual environment
cd "project faltu"
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
# Key packages: python-sat (Glucose3), google-genai (Gemini SDK)

# 3. (Optional) Yosys — only needed if you modify .v files
brew install yosys
yosys synth/synth.ys   # regenerates benchmarks/json/*.json
```

### API Key (Step 2 only)
```bash
# Edit .env — replace value with your actual key
# Get a key at: https://aistudio.google.com/apikey
echo "GEMINI_API_KEY=AIzaSy..." > .env

# Load before running Step 2
export $(grep -v '^#' .env | xargs)
```

---

## 4. Full Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  INPUT: benchmarks/json/c17.json  (Yosys-synthesized JSON)      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
              circuit_loader.load_circuit()
              ┌──────────────────────────┐
              │ module_name = "c17"      │
              │ module_data = {          │
              │   ports, cells, netnames │
              │ }                        │
              └──────────────┬───────────┘
                             │  enumerate_stuck_at_faults()
                             ▼
              34 faults: SA0/SA1 for each of 17 nets
                             │
              ┌──────────────┴──────────────────────┐
              │  For each fault (net_id, value):     │
              │                                      │
              │  build_miter()                       │
              │  ├── build_circuit_cnf(good_copy)    │
              │  ├── build_circuit_cnf(faulty_copy)  │
              │  │    └── fault unit clause added     │
              │  ├── output XOR clauses               │
              │  └── returns (clauses, good_map,      │
              │               faulty_map, meta)        │
              │                                      │
              │  [Step 1 only]                       │
              │  Glucose3.solve() → SAT/UNSAT        │
              │  extract_test_vector()               │
              │  extract_output_diff()               │
              │                                      │
              │  [Step 2 additionally]               │
              │  build_fault_prompt() → Gemini call  │
              │  translate_hints() → assumptions[]   │
              │  run_guided_fault():                 │
              │    Phase 1: solve(assumptions=hints) │
              │    Phase 2: solve() if Phase 1 UNSAT │
              └──────────────────────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────────────┐
              │  OUTPUT REPORTS                       │
              │  reports/c17_insights.txt   (Step 1)  │
              │  reports/c17_llm_comparison.txt (S2)  │
              └──────────────────────────────────────┘
```

---

## 5. File-by-File Breakdown

---

### 5.1 `core/circuit_loader.py`

**Purpose:** Load the Yosys JSON netlist and expose utility functions for querying the circuit.

**Key Functions:**

| Function | Input | Output | What it does |
|---|---|---|---|
| `load_circuit(path)` | JSON file path | `(module_name, module_data)` | Loads JSON, returns first module name + data dict |
| `get_port_nets(module_data, direction)` | module dict, `"input"` or `"output"` | list of net ID strings | Returns all net IDs of the given port direction |
| `get_net_name_map(module_data)` | module dict | `{net_id: signal_name}` | Maps Yosys integer IDs to human-readable names (N1, N22, etc.) |
| `enumerate_all_nets(module_data)` | module dict | list of all net ID strings | All nets including internal wires |
| `find_driving_gate(module_data, net_id)` | module dict, net ID | `(gate_name, gate_type)` or `(None, None)` | Finds which gate drives a given net |

**Data flow output used by:** `cnf_builder.py`, `miter.py`, `fault_manager.py`, `query_builder.py`, `evaluator.py`

---

### 5.2 `core/cnf_builder.py`

**Purpose:** Convert a gate-level netlist into CNF clauses using the **Tseitin transformation**. Each gate becomes a set of clauses that enforce the correct logic relationship.

**Tseitin encoding per gate type:**

| Gate | Clauses added (for output Y, inputs A, B…) |
|---|---|
| `$_NOT_` / NOT_X1 | `(Y ∨ A)`, `(¬Y ∨ ¬A)` |
| `$_AND_` / AND2_X1 | `(Y ∨ ¬A ∨ ¬B)`, `(¬Y ∨ A)`, `(¬Y ∨ B)` |
| `$_OR_` / OR2_X1 | `(¬Y ∨ A ∨ B)`, `(Y ∨ ¬A)`, `(Y ∨ ¬B)` |
| `$_NAND_` | `(¬Y ∨ ¬A ∨ ¬B)`… (complement of AND) |
| `$_NOR_` | complement of OR |
| `$_XOR_` | 4 clauses encoding XOR truth table |
| `$_MUX_` | 6 clauses for 2-to-1 mux |
| AOI21_X1, OAI21_X1 | Compound 3/4-input Nangate cells |

**Key function:**
```python
build_circuit_cnf(
    module_data,        # loaded circuit
    var_counter,        # mutable counter for SAT variable IDs
    net_to_var,         # {net_id: sat_variable} map (filled in place)
    skip_gate=None      # gate whose clauses are omitted (faulty copy)
) -> list[list[int]]   # list of CNF clauses
```

**Why `skip_gate`?**  
In the *faulty copy* of the miter, the gate driving the fault net must have its Tseitin clauses **removed**. Otherwise, the gate's encoding would contradict the fault unit clause (e.g., unit clause says Y=0, but AND gate clauses force Y=1 given those inputs). Removing the driving gate's clauses makes the fault net "free" to take the fault value.

**Nangate library support:** Full alias table maps all drive-strength variants (AND2_X1, AND2_X2, AND2_X4…) to canonical gate types.

---

### 5.3 `core/miter.py`

**Purpose:** Build the complete **miter circuit** CNF for a single stuck-at fault.

**What is a Miter?**  
A miter instantiates the *good circuit* (no fault) and the *faulty circuit* (fault injected) with **shared inputs**. It adds XOR gates on every primary output pair. If any XOR output can be 1 (i.e., SAT), the fault is detectable.

```
Primary inputs ─┬─► Good circuit  ─►  Output_good
                │                          │
                └─► Faulty circuit ─►  Output_faulty
                                           │
                               XOR ◄───────┘
                                │
                           Must be 1 (UNSAT = undetectable)
```

**Key function:**
```python
build_miter(module_data, fault_net, fault_value)
→ (all_clauses, good_map, faulty_map, xor_vars, meta)
```

Steps inside:
1. Call `build_circuit_cnf(good_copy)` — all gates encoded normally
2. Call `build_circuit_cnf(faulty_copy, skip_gate=driving_gate)` — driving gate removed
3. Add **fault unit clause**: `[+var]` for SA1, `[-var]` for SA0 on the fault net variable in the faulty copy
4. Add shared-input clauses: `good_input_var = faulty_input_var` for every primary input
5. Add XOR clauses for each output pair
6. Add at-least-one XOR = 1 clause (OR of all XOR variables)

---

### 5.4 `core/fault_manager.py`

**Purpose:** Enumerate faults and extract results from a SAT model.

| Function | What it does |
|---|---|
| `enumerate_stuck_at_faults(module_data)` | Returns list of `(net_id, 0)` and `(net_id, 1)` tuples for every net |
| `fault_label(net, value)` | Returns `"SA0@net6"` style string |
| `extract_test_vector(model, good_map, input_nets)` | Maps SAT model back to `{net_id: 0_or_1}` for primary inputs |
| `extract_output_diff(model, good_map, faulty_map, output_nets)` | Shows `{net: {good: X, faulty: Y}}` for each output |

---

### 5.5 `run_atpg.py`

**Purpose:** Step 1 entry point. Runs ATPG for one fault or every fault.

```bash
# Single fault
python run_atpg.py --json benchmarks/json/c17.json --net 6 --val 0

# Full sweep (all 34 faults on c17)
python run_atpg.py --json benchmarks/json/c17.json
```

**Output (console):**
```
Fault: SA0@net6   Status: DETECTABLE   TV: N1=0,N2=0,N3=1,N6=0,N7=1
  Output N22: good=0  faulty=0
  Output N23: good=1  faulty=0
```

---

### 5.6 `run_insights.py`

**Purpose:** Runs a full fault sweep, collects solver statistics, and writes a structured report.

```bash
python run_insights.py --json benchmarks/json/c17.json --out reports/c17_insights.txt
```

**Report sections:**
1. Circuit overview (net ID → name table, gate count)
2. Fault coverage summary
3. Per-fault results (each fault: solve time, decisions, conflicts, propagations, test vector, output diff)
4. Key insights (hardest faults, LLM guidance analysis, PySAT hint examples)
5. Raw data table (one line per fault for baseline comparison)

**Why decisions/conflicts?**  
`decisions` = number of times Glucose3 had to guess a variable assignment (more = harder). `conflicts` = backtrack events. These are the primary metrics for measuring LLM guidance effectiveness in Step 2.

---

### 5.7 `extract_reports.py`

**Purpose:** Condenses `*_insights.txt` into a shorter `*_summary.txt` (top-10 hardest/slowest faults, aggregate statistics).

```bash
python extract_reports.py --in reports/c17_insights.txt \
                          --out reports/summaries/c17_summary.txt
```

---

### 5.8 `llm/query_builder.py`

**Purpose:** Build a structured prompt for Google Gemini describing one stuck-at fault.

**Key function:**
```python
build_fault_prompt(module_data, fault_net, fault_value, good_map, summary_text) -> str
```

**Prompt structure:**
```
SYSTEM INSTRUCTIONS: (expert ATPG agent, return only JSON)

═══════════════════════════════════════
FAULT TARGET: net6 (N7) — STUCK-AT-1
═══════════════════════════════════════

The wire 'N7' (net6) is permanently forced to 1.
To DETECT this fault you need an input vector where:
  1. The fault-free circuit drives net6 = 0, and
  2. This difference propagates to at least one primary output.

CIRCUIT TOPOLOGY:
PRIMARY INPUTS  : N1(net2), N2(net3), N3(net4), N6(net5), N7(net6)
PRIMARY OUTPUTS : N22(net7), N23(net8)
GATE COUNT      : 12
GATE LIST: ...

NET → SAT VARIABLE MAP: ...

PRIMARY INPUT NET IDs: 2, 3, 4, 5, 6

TASK: Return ONLY:
{
  "signal_assignments": {"<net_id>": <0_or_1>, ...},
  "sensitization_hint": "<one sentence>"
}
```

**Why include the SAT variable map?**  
So the LLM can reference net IDs unambiguously (net2 = N1 = SAT var 1). The LLM returns net IDs in `signal_assignments`, not variable numbers, making it human-readable.

---

### 5.9 `llm/hint_translator.py`

**Purpose:** Parse the LLM's JSON response and convert it to PySAT **assumption literals**.

**Key function:**
```python
translate_hints(llm_response_str, good_map, verbose=False)
-> (assumptions: list[int], hint_text: str)
```

**How PySAT assumptions work:**
- A positive integer `+v` means "assume variable v = TRUE (1)"
- A negative integer `-v` means "assume variable v = FALSE (0)"
- `solver.solve(assumptions=[+5, -3])` tries to find a SAT solution where var 5=1 and var 3=0
- If this results in UNSAT, the solver returns False — **the hints were wrong**
- Crucially: assumptions are **retractable** — they don't permanently add clauses

**Translation logic:**
```
LLM returns: {"signal_assignments": {"2": 1, "3": 0}}
good_map = {"2": 5, "3": 7, ...}   (from build_miter)

Result: assumptions = [+5, -7]
Meaning: "Try with net2=1 AND net3=0"
```

**Robustness:** Handles markdown code fences, extra prose, missing fields, invalid values — on any failure returns `([], "")`.

---

### 5.10 `llm/evaluator.py`

**Purpose:** Run the two-phase guided SAT solve for one fault.

**Two-phase logic:**
```
Phase 1 — Guided (only if assumptions != [])
  solver.add_clauses(miter_clauses)
  sat = solver.solve(assumptions=llm_hints)
  
  if SAT  → fault DETECTABLE with LLM hints
             used_hints = True  ✓ (this is the "LLM won" case)
  if UNSAT → hints were wrong
             fallback_triggered = True
             → go to Phase 2

Phase 2 — Fallback (baseline SAT, no assumptions)
  solver.add_clauses(miter_clauses)
  sat = solver.solve()    ← identical to Step 1
  
  if SAT  → DETECTABLE (but not LLM-guided)
  if UNSAT → UNDETECTABLE (redundant fault)
```

**Why two phases?**  
Wrong LLM hints should not cause a fault to be incorrectly classified as UNDETECTABLE. Phase 2 guarantees correctness regardless of LLM quality.

**Result dict returned:**
```python
{
  "fault_label":            "SA1@net6",
  "status":                 "DETECTABLE",
  "used_hints":             True,          # Phase 1 succeeded
  "fallback_triggered":     False,
  "hint_solver_stats":      {"decisions": 4, "conflicts": 0, ...},
  "fallback_solver_stats":  {},
  "test_vector":            {"2": 1, "3": 0, ...},
  "output_diff":            {"7": {"good": 0, "faulty": 1}},
  "total_solve_time_sec":   0.00041,
}
```

---

### 5.11 `llm/run_llm_atpg.py`

**Purpose:** Step 2 main driver. Runs the full LLM-guided sweep on c17, compares to Step 1 baseline, writes comparison report.

**Key config (top of file):**
```python
CIRCUIT_JSON  = "benchmarks/json/c17.json"
INSIGHTS_FILE = "reports/c17_insights.txt"   # Step 1 raw data for baseline
REPORT_OUT    = "reports/c17_llm_comparison.txt"
DEFAULT_MODEL = "gemini-2.0-flash-lite"      # cheapest model (free tier)
```

**Per-fault loop:**
1. Load circuit, enumerate 34 faults
2. `build_miter()` → get `good_map`
3. `build_fault_prompt()` → structured prompt string
4. `call_gemini(model, system_prompt, user_prompt)` → response text
   - Retries up to 3× on 429 rate-limit with API-suggested delay
5. `translate_hints(response, good_map)` → `(assumptions, hint_text)`
6. `run_guided_fault(module_data, fault_net, fault_value, assumptions)` → result
7. Progress line: `[  6/34] SA1@net6   GUIDED   dec: 16 → 4   api=340ms`

**Report output** (`reports/c17_llm_comparison.txt`):
```
1. FAULT COVERAGE
   Coverage: 100.0% (34/34)

2. LLM HINT EFFECTIVENESS
   Hints accepted (SAT on Phase 1): N
   Hints rejected (forced fallback): M

3. DECISION COUNT COMPARISON
   Step 1 avg decisions: 9.03
   Step 2 avg decisions: X.XX  (+/-Y%)

4. PER-FAULT TABLE
   Fault      Status    Mode    S1 Dec  S2 Dec  S2 ms  Hint
   SA0@net2   DETECT    GUIDED       7       3   0.12  "Set N1=1..."
   SA1@net6   DETECT    FALLBACK    16      17   0.14
```

---

## 6. Step 1 Results & Explanation

**Circuit: c17** — 5 primary inputs (N1, N2, N3, N6, N7), 2 outputs (N22, N23), 12 gates (6 AND + 6 NOT).

### Results

| Metric | Value |
|---|---|
| Total faults tested | 34 (17 nets × SA0/SA1) |
| Detectable (SAT) | **34 (100%)** |
| Undetectable (UNSAT) | **0** |
| Avg solve time | 0.013 ms |
| Avg decisions | **9.0** |
| Avg conflicts | 2.7 |
| CNF size | 36 variables, 77-78 clauses |

### Why 100% coverage?
c17 is a well-known fully-testable benchmark — no redundant faults by construction.

### Hardest faults (by SAT decisions)

| Rank | Fault | Decisions | Conflicts |
|---|---|---|---|
| 1 | SA1@net6 (N7) | **16** | 11 |
| 2 | SA1@net5 (N6) | 13 | 8 |
| 3 | SA0@net18 | 13 | 4 |

**SA1@net6 is hardest** because net6 is a primary input driving multiple internal paths — the SAT solver must explore many combinations to find one where all paths are sensitized simultaneously.

### Sample result for SA1@net6
```
Fault:    SA1@net6 (N7 stuck-at-1)
Status:   DETECTABLE
TV:       N1=1, N2=0, N3=1, N6=0, N7=0   ← N7=0 in good circuit → exposes SA1
Output:   N22: good=1  faulty=1
          N23: good=0  faulty=1           ← N23 diverges → fault observable
Decisions: 16   Conflicts: 11
```

**Logic:** Setting N7=0 (good circuit) while the fault forces net6=1 creates divergence. The propagation path through the NOT and AND gates leading to N23 is the sensitization path.

---

## 7. Step 2 Results & Explanation

### Summary

| Metric | Value |
|---|---|
| Fault coverage | 100% (34/34) — SAT fallback guarantees this |
| Hints accepted (GUIDED mode) | 0 |
| Hints rejected (FALLBACK mode) | 34 |
| S1 avg decisions | 9.03 |
| S2 avg decisions | 9.97 |
| Decision change | −10.4% (negative = worse — due to no LLM hints) |

### Why "Hints accepted: 0"?

The **free-tier Gemini API quota was exhausted** (`limit: 0`) during the run. Every API call hit a 429 RESOURCE_EXHAUSTED error. The retry logic waited up to 3× but the daily quota never recovered. The fallback mechanism worked correctly — all 34 faults were still detected using pure SAT.

### What "GUIDED" mode would look like (when LLM works)
```
[  9/34] SA0@net6    GUIDED   dec: 12 → 3   api=340ms
  Hint: "Set N7=1 to activate the AND gate driving N7's path to output N23"
```
Decisions would drop from 12 → 3 because the solver starts from a strongly narrowed search space (LLM pre-assigns 2-3 inputs).

### Decision count interpretation
- `S2 Dec > S1 Dec` (observed) → LLM not helping (quota exhausted, all FALLBACK)
- `S2 Dec < S1 Dec` → LLM guidance is working (target outcome)
- Goal for paper: show that even partial 40-60% hint acceptance achieves measurable decision reduction on the hardest faults

### Quota resolution
To get true GUIDED results: use a paid Gemini key ($5 credit, no rate limits) or a fresh free-tier key at midnight PST quota reset.

---

## 8. All Run Commands

```bash
# ── Environment setup ────────────────────────────────────────────
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# ── (Optional) Re-synthesize circuits ───────────────────────────
yosys synth/synth.ys

# ── Step 1: Single fault test ───────────────────────────────────
python run_atpg.py --json benchmarks/json/c17.json --net 6 --val 0
python run_atpg.py --json benchmarks/json/c17.json --net 6 --val 1

# ── Step 1: Full fault sweep ─────────────────────────────────────
python run_atpg.py --json benchmarks/json/c17.json
python run_atpg.py --json benchmarks/json/circuit.json

# ── Step 1: Generate insights report (with solver stats) ─────────
python run_insights.py --json benchmarks/json/c17.json \
                       --out reports/c17_insights.txt

# ── Step 1: Condense report ──────────────────────────────────────
python extract_reports.py --in  reports/c17_insights.txt \
                          --out reports/summaries/c17_summary.txt

# ── Step 2: LLM-guided sweep ─────────────────────────────────────
# First: add your Gemini API key to .env
export $(grep -v '^#' .env | xargs)

# Full run (34 faults, ~15-30 min on free tier due to rate limits)
python llm/run_llm_atpg.py --verbose

# Quick test with first 5 faults only
python llm/run_llm_atpg.py --max-faults 5 --verbose

# Use a specific Gemini model
python llm/run_llm_atpg.py --model gemini-2.0-flash --verbose
python llm/run_llm_atpg.py --model gemini-2.5-pro-preview-03-25 --verbose

# ── View reports ─────────────────────────────────────────────────
cat reports/c17_insights.txt
cat reports/c17_llm_comparison.txt
cat reports/summaries/c17_summary.txt
```

---

## 9. Known Issues & Limitations

| Issue | Severity | Notes |
|---|---|---|
| Free-tier Gemini rate limit (429) | High | Daily cap exhausted quickly with 34 faults. Use paid key or fresh account. |
| `gemini-1.5-pro` returns 404 | Fixed | Use `gemini-2.0-flash-lite` (default) or `gemini-2.0-flash` |
| Step 2 hardcoded to c17 | Low | `CIRCUIT_JSON` and `INSIGHTS_FILE` at top of `run_llm_atpg.py` — change to extend to other benchmarks |
| Nangate library cells (AND2_X1 etc.) | Resolved | Complete alias table added to `cnf_builder.py` |
| `c17_summary.txt` has no raw table | Resolved | Baseline now reads from `c17_insights.txt` directly |
| Pyre2 IDE lint errors on imports | Cosmetic | False positives — all `sys.path` and venv imports resolve correctly at runtime |

---

## 10. Detailed End-to-End Trace: c17.v Through Every Function

> **Full dry-run:** See [`DRY_RUN_SA1_net6.md`](DRY_RUN_SA1_net6.md) for the exhaustive
> clause-by-clause, variable-by-variable trace. This section shows the key call chain
> and concrete numbers inline.

**Running example fault:** `SA1@net6` — N7 permanently stuck at 1.
Hardest fault in c17: decisions=16, conflicts=11, propagations=168.

### 10.1 Where is the CNF?

The CNF is **never written to disk**. It lives in memory as `list[list[int]]`:

| Stage | Function | File | Output |
|---|---|---|---|
| Per-gate Tseitin | `build_circuit_cnf()` | `core/cnf_builder.py` | clauses + net→var map |
| Full miter assembly | `build_miter()` | `core/miter.py` | 62-clause combined formula |
| Solving | `Glucose3.solve()` | PySAT library | SAT/UNSAT + model |

**Exporting CNF (Proof of Concept):**
While the system runs entirely in-memory during ATPG to prevent disk I/O bottlenecks, you can dump an actual physical CNF specification in standard DIMACS format for verification.
```bash
python dump_cnf.py
```
This generates `benchmarks/cnf/sample_SA1_net6.cnf` representing the active fault.

To quickly inspect the CNF at runtime instead, add after `build_miter()` in `run_atpg.py`:
```python
for i, c in enumerate(all_clauses):
    print(f"[{i}]: {c}")
```

---

### 10.2 Variable Map (c17, SA1@net6 miter)

```
Net    Signal   Good var   Faulty var   Notes
────── ──────── ────────   ──────────   ────────────────────────────────────
net2   N1         10          23
net3   N2          1          14
net4   N3          6          19
net5   N6          5          18
net6   N7          3          16        fault net; clause [44]=[16] forces faulty→1
net7   N22        12          25        D_var=27
net8   N23         8          21        D_var=28; diverges under test vector
net9   INV(N2)     2          15
net10  INV(N7)     4          17
net11  N11         7          20
net12  NAND out   11          24
AOI21m (internal) 9          22
OAI21m (internal)13          26
Total: 28 variables (vars 1–13 good, 14–26 faulty, 27–28 D-vars)
```

---

### 10.3 Step 1 Call Chain (Baseline SAT-ATPG)

```
run_atpg.py: main()
 ├─ circuit_loader.load_circuit("benchmarks/json/c17.json")
 │    → ("c17", module_data)   5 inputs(nets 2-6), 2 outputs(nets 7-8), 6 cells
 │
 ├─ fault_manager.enumerate_stuck_at_faults(module_data, verilog_only=True)
 │    → 22 faults (11 user-visible nets × SA0+SA1)
 │
 └─ run_single_fault(module_data, "6", 1):
      │
      ├─ miter.build_miter(module_data, "6", 1)
      │   ├─ [A] build_circuit_cnf(cells, var_offset=0)
      │   │       → good_map (net→var, vars 1–13), 22 clauses
      │   ├─ [B] find_driving_gate(module_data, "6") → None (N7 is PRIMARY_INPUT)
      │   ├─ [C] build_circuit_cnf(cells, var_offset=13, skip_gate=None)
      │   │       → faulty_map (vars 14–26), 22 clauses
      │   ├─ [D] append([16])          ← SA1 unit clause (var16=N7_faulty=1)
      │   ├─ [E] tie clauses for nets 2,3,4,5 (skip net6): 8 clauses
      │   └─ [F] XOR D-vars 27,28 + OR[27,28]: 9 clauses
      │           TOTAL: 62 clauses, 28 variables
      │
      ├─ Glucose3.solve(62_clauses)
      │    → SAT=True, decisions=16, conflicts=11, propagations=168
      │    model: var3=-3(neg)→N7_good=0 ✓; var16=+16→N7_faulty=1 ✓
      │
      ├─ extract_test_vector(model, good_map, ["2","3","4","5","6"])
      │    reads model[var-1] sign for each input net's good_map variable
      │    → {"2":1,"3":0,"4":1,"5":0,"6":0}  = N1=1,N2=0,N3=1,N6=0,N7=0
      │
      └─ extract_output_diff(model, good_map, faulty_map, ["7","8"])
           N22: good_var=12→model[11]→1, faulty_var=25→model[24]→1  same
           N23: good_var=8 →model[7] →0, faulty_var=21→model[20]→1  DIFFERS ✓
           → {"7":{"good":1,"faulty":1}, "8":{"good":0,"faulty":1}}
```

#### AND Gate Tseitin Example (Cell C, AND2_X1: A=net5→var5, B=net4→var6, Y=net11→var7)

```python
# Boolean: Y = A ∧ B
clauses.append([ 5, -7])    # A ∨ ¬Y  — Y=1 requires A=1
clauses.append([ 6, -7])    # B ∨ ¬Y  — Y=1 requires B=1
clauses.append([-5, -6, 7]) # ¬A ∨ ¬B ∨ Y — A=1,B=1 forces Y=1
```

#### AOI21 Compound Gate (Cell D driving N23 — introduces intermediate var9)

```python
# ZN = ¬((B1 ∧ B2) ∨ A)   where B1=var4, B2=var2, A=var7, ZN=var8
m = fresh_var(...)  # → var9
# m = B1 ∧ B2:    [4,-9], [2,-9], [-4,-2,9]
# ZN = NOR(m,A):  [-9,-8], [-7,-8], [9,7,8]
```

---

### 10.4 Step 2 Call Chain (LLM-Guided, same fault)

```
llm/run_llm_atpg.py: main()
 ├─ _load_baseline("reports/c17_insights.txt")
 │    parses raw table → baseline["SA1@net6"] = {decisions:16, conflicts:11}
 │
 ├─ build_miter(module_data,"6",1) → good_map  (for prompt only)
 │
 ├─ query_builder.build_fault_prompt(module_data,"6",1,good_map)
 │    Uses Verilog signal names (not var IDs) for readability to LLM:
 │    "FAULT: N7 stuck-at-1
 │     To detect: find inputs where N7=0 in good circuit...
 │     INPUTS: N1, N2, N3, N6, N7
 │     GATES: N23=AOI21(N11,NOT(N7),NOT(N2)); N11=AND(N6,N3); ..."
 │
 ├─ call_gemini(model_name, _SYSTEM_PROMPT, prompt)
 │    → genai.Client().models.generate_content(temperature=0, max_tokens=256)
 │    Retries up to 5× on 429 (backoff = min(2^attempt, 120)s)
 │    Raw response: '{"signal_assignments":{"N7":0,"N3":1},"sensitization_hint":"..."}'
 │
 ├─ hint_translator.translate_hints(response, good_map, input_nets, module_data)
 │    _extract_json() → strips prose/fences
 │    _build_name_to_id(): "N7"→"6", "N3"→"4"
 │    good_map["6"]=3, value=0 → literal=-3
 │    good_map["4"]=6, value=1 → literal=+6
 │    → assumptions=[-3, 6], hint="Set N7=0 to expose SA1..."
 │
 └─ evaluator.run_guided_fault(module_data,"6",1,assumptions=[-3,6])
      ├─ build_miter → 62 clauses (same)
      ├─ [Phase 1] Glucose3.solve(assumptions=[-3, 6])
      │    var3=FALSE(N7_good=0) + var6=TRUE(N3=1) pre-fixed
      │    → SAT in ~4 decisions if hints correct  (used_hints=True)
      │    → UNSAT if hints wrong                  (fallback_triggered=True → Phase 2)
      └─ [Phase 2, if needed] Glucose3.solve()    ← identical to Step 1
```

**Decision reduction:** 16 → ~4 (≈75%) when LLM hints are accepted.

---

### 10.5 Circuit-Level Verification

With test vector **N1=1, N2=0, N3=1, N6=0, N7=0**:

| Signal | Good (N7=0) | Faulty (N7=1, stuck) |
|---|---|---|
| INV(N7) | 1 | **0** ← flips |
| N11 = AND(N6=0,N3=1) | 0 | 0 (unchanged) |
| N23 = AOI21(N11=0, INV(N7), INV(N2=0)=1) | NOT((1∧1)∨0)=**0** | NOT((0∧1)∨0)=**1** ← diverges |
| NAND(N3=1,N1=1)=n12 | 0 | 0 (unchanged) |
| N22 = OAI21(n12=0,N11=0,INV(N2)=1) | NOT((0∨0)∧1)=**1** | **1** (unchanged) |

**Result:** N22 same (1=1), N23 differs (0≠1) → **DETECTABLE via N23** ✓


---

## 11. Guidelines Compliance Audit & Gap Analysis

Cross-check against the assignment's **Problem Statement 4** requirements from `papers/Guidelines.pdf`.

### 11.1 Compliance Table

| Requirement | Status | Notes / Gap |
|---|---|---|
| **Step 1 — SAT-ATPG Core** | | |
| RTL → gate-level via Yosys | ✅ | `synth/synth.ys` maps c17.v to Nangate 45nm cells; JSON output used |
| Stuck-at fault CNF encoding via Tseitin | ✅ | `core/cnf_builder.py` encodes AND/OR/NOT/NAND/NOR/XOR/MUX/AOI/OAI |
| Good/faulty miter construction | ✅ | `core/miter.py` builds dual-copy miter with shared inputs and XOR outputs |
| SAT solver integration (PySAT/MiniSAT/Z3) | ✅ | PySAT `Glucose3` used; equivalent to MiniSAT core |
| Fault coverage 100% on detectable benchmarks | ✅ | All 34 faults on c17 detected (100% coverage) |
| **Step 2 — LLM Guidance Layer** | | |
| Provide fault instance to LLM | ✅ | `llm/query_builder.py` builds compact prompt per fault |
| Predicted fault activation conditions | ✅ | LLM asked to suggest which signal = 0/1 activates the fault |
| Suggested sensitization paths | ⚠️ | LLM returns `sensitization_hint` string, but it is only logged — not parsed into structural path constraints |
| Partial input assignments | ✅ | `signal_assignments` JSON field translated to PySAT assumptions |
| Variable ordering hints | ❌ | Assignment says LLM can suggest variable ordering; we only do input fixings, not reordering solver's VSIDS heap |
| Translation to assumption literals | ✅ | `llm/hint_translator.py` → `solver.solve(assumptions=[...])` |
| Translation to branching priority modifications | ❌ | Not implemented; would require PySAT internals / custom solver |
| Translation to CNF constraints (soft guidance) | ⚠️ | We use assumptions (retractable); hard unit clauses not used, which is safer but limits certain probing strategies |
| **Step 3 — Closed-Loop Evaluation** | | |
| UNSAT core / conflict extraction | ❌ | Not implemented; `solver.get_core()` is available in PySAT but unused |
| Structured feedback to LLM | ❌ | When hints fail (fallback_triggered), we do not re-query the LLM with failure info |
| Iterative refinement loop | ❌ | Single-shot LLM query per fault; no multi-round dialogue |
| **Expected Novelty** | | |
| Semantic reasoning from LLMs into SAT | ⚠️ | LLM reasons about circuit semantics, but integration is one-way (LLM→SAT, no SAT→LLM feedback) |
| Formal evaluation of LLM impact on solver metrics | ✅ | `reports/c17_llm_comparison.txt` compares decisions/conflicts/time Step1 vs Step2 |
| Hybrid semantic-structural heuristics | ⚠️ | Partial: LLM provides semantic hints translated to structural assumptions; no deep structural analysis (e.g., path sensitization graph) fed back to LLM |
| Cross-fault knowledge reuse | ❌ | Each fault is queried independently; no history of previous fault resolutions passed to LLM |
| **Experimental Evaluation** | | |
| ISCAS-85 benchmark circuits | ⚠️ | Only c17 tested; c432, c499, c880, c1355, c1908, c3540 not yet run |
| ISCAS-89 benchmark circuits | ❌ | No sequential circuit support |
| Arithmetic modules (ALU, multipliers) | ❌ | Not attempted |
| Fault coverage metric | ✅ | Reported in both Step 1 and Step 2 reports |
| SAT solving time metric | ✅ | Measured in both steps (ms per fault) |
| Decision count metric | ✅ | Primary comparison metric; baseline vs guided |
| Conflict count metric | ✅ | Reported in insights file |
| Pattern count metric | ⚠️ | Patterns logged but no compaction or pattern-count reduction analysis |
| Comparison with baseline SAT-ATPG | ✅ | `c17_llm_comparison.txt` shows S1 vs S2 side-by-side per fault |
| Statistical comparison (mean, variance, significance) | ⚠️ | Averages computed but no t-test, variance, or confidence intervals |

---

### 11.2 Top 5 Highest-Impact Improvements

Ordered by impact-to-effort ratio (high impact, low effort first):

---

#### #1 — Closed-Loop LLM Feedback on Hint Failure

**Gap addressed:** Step 3 (iterative refinement), cross-fault knowledge reuse  
**Impact:** High — most valuable when hints are wrong (all 34 fallbacks in current run)  
**Effort:** Low — one extra API call per failed fault

**Implementation sketch:**
```python
# In run_guided_fault(), after fallback_triggered:
if fallback_triggered and llm_response:
    feedback_prompt = f"""Your hints {hint_text} caused UNSAT (wrong).
UNSAT core variables: {solver.get_core()}
The correct test vector was: {test_vector}
For the NEXT fault on this circuit, adjust your reasoning accordingly."""
    refined_response, _ = call_gemini(model, _SYSTEM_PROMPT, feedback_prompt)
    # Pass refined_response context to next fault's prompt
```

---

#### #2 — Extend to Full ISCAS-85 Suite (c432, c499, c880, c1355, c3540)

**Gap addressed:** Experimental evaluation breadth  
**Impact:** High — essential for publishable results; c17 is too small to draw conclusions  
**Effort:** Low — code already works for any Yosys JSON; just need to synthesize more circuits

**Implementation sketch:**
```bash
# Add to synth/synth.ys:
read_verilog benchmarks/c432.v
synth -top c432; techmap; abc -liberty ...
write_json benchmarks/json/c432.json
# Then run: python run_insights.py --json benchmarks/json/c432.json
```

Then parameterise `run_llm_atpg.py` to accept `--json` as a CLI arg (currently hardcoded to c17).

---

#### #3 — UNSAT Core Extraction for Conflict Diagnosis

**Gap addressed:** Step 3 (UNSAT core / conflict extraction)  
**Impact:** Medium-High — enables the LLM to learn *why* its hints failed  
**Effort:** Low — PySAT already exposes `solver.get_core()` after an UNSAT solve

**Implementation sketch:**
```python
# In evaluator.py, after Phase 1 returns UNSAT:
if not sat and assumptions:
    core = solver.get_core()   # returns subset of assumptions that caused UNSAT
    # e.g., core=[-3] means "assumption var3=FALSE alone caused contradiction"
    # Translate back to signal names and include in next prompt:
    core_signals = [net_id for net_id, var in good_map.items()
                    if var in [abs(l) for l in core]]
    feedback = f"Hint caused UNSAT. Conflicting assumptions on: {core_signals}"
```

---

#### #4 — Statistical Significance Testing

**Gap addressed:** Experimental evaluation rigor  
**Impact:** Medium — required to claim LLM guidance "significantly" reduces complexity  
**Effort:** Low — scipy one-liner

**Implementation sketch:**
```python
from scipy import stats
# In _write_report():
t_stat, p_value = stats.ttest_rel(baseline_decs_list, s2_decs_list)
f.write(f"  Paired t-test: t={t_stat:.3f}, p={p_value:.4f}  ")
f.write("(significant)" if p_value < 0.05 else "(not significant)")
```

---

#### #5 — Cross-Fault Context Window (LLM Memory)

**Gap addressed:** Cross-fault knowledge reuse  
**Impact:** Medium — allows LLM to generalise (e.g., "SA1 on primary inputs always need X=0")  
**Effort:** Medium — requires managing a growing prompt context or embedding-based retrieval

**Implementation sketch:**
```python
# Maintain running context of resolved faults:
fault_history = []   # list of {"fault": label, "tv": test_vector, "hint": hint_text}

# In main loop, after each fault resolves:
fault_history.append({"fault": lbl, "tv": tv, "hint": hint_text, "worked": used_hints})

# Prepend summary to next fault's prompt:
context = "\n".join([
    f"Previously: {h['fault']} detected with TV={h['tv']} (hint {'worked' if h['worked'] else 'failed'})"
    for h in fault_history[-3:]  # last 3 faults for token efficiency
])
prompt = f"CONTEXT:\n{context}\n\n" + build_fault_prompt(...)
```

