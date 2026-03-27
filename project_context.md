# Comprehensive SAT-Based ATPG Project Context

This document is an exhaustive, self-explanatory reference for the "LLM-Guided SAT-Based ATPG" project. It details the entire architecture, data flow, and every function implementation across the repository, serving as the ultimate guide for an LLM to understand and work on this codebase.

---

## 1. High-Level System Architecture and Flow

The project implements an Automatic Test Pattern Generation (ATPG) pipeline built on Boolean Satisfiability (SAT). It converts digital circuit gate-level netlists into Conjunctive Normal Form (CNF) and uses the PySAT library (specifically `Glucose3`) to detect single stuck-at faults (0 or 1). 

**The Pipeline Flow:**
1. **Verilog to JSON Synthesis:** A standard Verilog RTL file (e.g., `benchmarks/circuit.v`) is passed through Yosys using the script `synth/synth.ys`. Yosys maps the logic to standard cells (`$_AND_`, `$_NOT_`, etc.) and outputs a structured JSON netlist.
2. **Circuit Loading:** Python parses the JSON file, extracting input/output ports, wires (nets), and gates (cells).
3. **Miter Construction:** For a target fault on a specific net:
   - A **Good** circuit CNF is generated.
   - A **Faulty** circuit CNF is generated, injecting the stick-at fault as a forced unit clause ($net = 0$ or $net = 1$). To avoid contradictions, the gate driving the faulted net has its internal CNF clauses scrubbed (`skip_gate` mechanism).
   - The primary inputs of both circuits are tied together (equivalent).
   - The primary outputs are XORed and combined in an `OR` clause to demand at least one divergence between the Good and Faulty circuits.
4. **SAT Solving:** PySAT processes the combined CNF.
   - If **SAT**, a test vector is recovered from the satisfying assignment.
   - If **UNSAT**, the fault is logically redundant/undetectable.
5. **Insights Collection:** The solver's internal health metrics (decisions, conflicts, propagations, execution time) are recorded for LLM guidance in the subsequent phase.

---

## 2. Detailed Module & Function Implementations

### A. `core/circuit_loader.py`
This module acts as the single entry point for parsing the Yosys JSON netlist and provides utilities to query the network structure.

- **`def load_circuit(json_file: str) -> tuple`**
  - **Use:** Opens and parses the JSON netlist.
  - **Implementation:** Handles `FileNotFoundError` and `JSONDecodeError`, aborting gracefully with `sys.exit()`. Extracts the ﬁrst module block from `data["modules"]` and calls `validate_netlist()`. Returns `(module_name, module_data)`.
  
- **`def get_port_nets(module_data: dict, direction: str) -> list`**
  - **Use:** Acquires all net IDs designated as either `"input"` or `"output"`.
  - **Implementation:** Iterates through `module_data["ports"]`. If a port's `"direction"` matches the argument, it appends all bit identifiers (as strings) to a list.

- **`def get_net_name_map(module_data: dict) -> dict`**
  - **Use:** Establishes a mapping from arbitrary Yosys numeric net IDs to original Verilog human-readable signal names.
  - **Implementation:** Traverses `module_data["netnames"]`. Discards Yosys constants (`"0"`, `"1"`, `"x"`, `"z"`). Returns a dictionary: `{"net_id" (str): "signal_name" (str)}`.

- **`def enumerate_all_nets(module_data: dict) -> list`**
  - **Use:** Compiles a master list of every unique physical wire in the circuit.
  - **Implementation:** Uses a `set()` to aggregate all bits found in `module_data["ports"]` and connection arrays in `module_data["cells"]`. Constants are stripped out, and the resulting set is strictly sorted numerically.

- **`def find_driving_gate(module_data: dict, fault_net: str) -> str | None`**
  - **Use:** Locates the specific logic gate that controls a given net. Critical for the faulty circuit creation to "skip" this gate.
  - **Implementation:** Iterates over `module_data["cells"]`. Identifies the cell that has `fault_net` in its `"Y"` (output) connection list. Returns the `cell_name` if identified, otherwise `None` (implying the net is a primary input).

- **`def validate_netlist(module_data: dict) -> list[str]`**
  - **Use:** Sanity-checks the parsed netlist to ensure it has inputs, outputs, cells, and recognized gate types. Returns a list of string warnings.

### B. `core/cnf_builder.py`
The bedrock of the structural SAT-ATPG, converting gates into PySAT-compatible integer variable lists via the **Tseitin Transformation**.

- **`def build_circuit_cnf(cells, var_offset=0, skip_gate=None) -> tuple`**
  - **Use:** Transforms the network into a list of clauses.
  - **Implementation:** Creates an internal mapping `net_to_var` ensuring every Yosys net maps to a unique integer $\ge 1 + \text{var\_offset}$. Loops over all cells and standardizes their gate type (merging Yosys identifiers like `$and` and `$_AND_`).
  - Implements the Tseitin logical equivalences for: `$_AND_`, `$_OR_`, `$_NOT_`, `$_BUF_`, `$_NAND_`, `$_NOR_`, `$_XOR_`, `$_XNOR_`, and `$_MUX_`.
  - **The `skip_gate` hook:** If the `cell_name` equals `skip_gate`, internal variable integers are still structurally allocated for upstream/downstream tracking, but the equivalent Tseitin constraint clauses (e.g., `(A ∨ ¬Y)`) are strategically omitted.
  - Returns: `(clauses (list), net_to_var (dict), next_free_var (int))`.

### C. `core/miter.py`
Orchestrates the creation of the miter logic structure to simultaneously simulate good and faulty conditions.

- **`def build_miter(module_data: dict, fault_net: str, fault_value: int) -> tuple`**
  - **Use:** Builds the unified CNF that a PySAT solver operates on.
  - **Implementation:**
    1. Generates the Good circuit CNF via `build_circuit_cnf(cells, var_offset=0)`.
    2. Runs `find_driving_gate()` to resolve what gate operates the `fault_net`.
    3. Generates the Faulty circuit CNF via `build_circuit_cnf(cells, var_offset=good_next, skip_gate=driving_gate)`.
    4. Explicitly forces the `fault_net` in the faulty circuit to either 0 or 1.
    5. Appends tie-clauses: For every primary input $A$, forces $Good\_A \iff Faulty\_A$. Exception: If the fault is directly ON an input, the tie is skipped so the fault does not accidentally propagate backward into the good copy.
    6. Appends Difference-clauses (D-vars): For every output $Y$, introduces $D_N = Good\_Y_N \oplus Faulty\_Y_N$. Appends a massive `OR` clause demanding at least one $D_N$ evaluates to True.
  - Returns `(all_clauses, good_map, faulty_map, next_free_var, meta)`.

### D. `core/fault_manager.py`
A lightweight suite of pure functions dedicated to orchestrating fault setups and extracting diagnostic telemetry from SAT models.

- **`def enumerate_stuck_at_faults(module_data: dict) -> list`**
  - **Use:** Yields all possible single stuck-at faults for a circuit.
  - **Implementation:** Calls `enumerate_all_nets()`, pushing tuple pairs `(net_id, 0)` and `(net_id, 1)` to a list.
- **`def fault_label(net_id: str, fault_value: int) -> str`**
  - **Use:** Constructs a human-readable identifier (e.g., `SA0@net6`).
- **`def extract_test_vector(model: list, good_map: dict, input_nets: list) -> dict`**
  - **Use:** Reconstructs the 0/1 binary values mapped to the primary input ports using a satisfied PySAT assignment.
  - **Implementation:** Filters the PySAT `model` array via the `good_map` to check mathematical signs.
- **`def extract_output_diff(model: list, good_map: dict, faulty_map: dict, output_nets: list) -> dict`**
  - **Use:** Reports how the raw outputs diverged between the uncorrupted and corrupted paths.

### E. Execution Scripts

1. **`run_atpg.py`** (The testing workhorse)
   - **`run_single_fault(module_data, fault_net, fault_value, verbose)`**: Wraps `build_miter()` with a `Glucose3()` PySAT instance. If `sat`, it recovers the test vector and output divergence. Records decisions, conflicts, props, and timer. Returns a robust statistics dict.
   - **`run_full_sweep(json_file)`**: Iterates over every generated fault mapped by `enumerate_stuck_at_faults()`. Prints diagnostic breakdowns of Fault Coverage.
   - **`main()`**: Houses `argparse` routing to let users test single nets or sweep full geometries.

2. **`run_insights.py`** (The bridge to the LLM)
   - Operates a specialized sweep invoking `run_single_fault()` iteratively.
   - **`generate_report(...)`**: Constructs `reports/*_insights.txt`, a beautifully formatted file dividing data into 5 segments:
     1. Circuit Overview.
     2. Fault Coverage Summary.
     3. Per-Fault Detailed Results.
     4. Key Insights for LLM Guidance Layer (where it aggregates max-decision metrics, identifies redundant faults, and explicitly maps out how an LLM can use `assumptions=` in PySAT).
     5. Raw Data Table.

3. **`extract_reports.py`** (The context optimizer)
   - Because standard circuit sweeps generate massive insight reports (unfriendly to LLM token limits), this script shrinks those logs using RegEx parsing.
   - **`parse_fault_blocks()`** & **`parse_raw_table()`**: Digitizes the text block formats back into structured Python arrays.
   - Retains only the exact top 10 hardest faults (decisions) and top 10 slowest faults (time), stripping hundreds of "easy" faults away. Generates `*_summary.txt`.

---

## 3. The Future Strategy: LLM Guidance (Step 2)

The ultimate vision of the problem statement dictates that the LLM serves as a **Semantic Heuristic Oracle**. Standard SAT-solving uses completely blind syntactic structures (VSIDS algorithms inside Glucose3). The intent is for an LLM to utilize structural semantic details (functional sensitization paths) to guide the SAT solver out of high-conflict traps.

### How this codebase prepares for that:
- For complex faults, testing requires significant backtracking (`run_insights.py` lists exactly which faults are complex).
- The future code will query the LLM: *"For this circuit and fault SA0@netX, predicting partial conditions?"*
- **Tactic A (Assumptions):** The LLM yields a predicted state (e.g., set `netA=1` to allow propagation). The code executes `solver.solve(assumptions=[good_map["netA"]])`. This softly limits search space, vastly reducing solver time, but can backtrack safely.
- **Tactic B (Hard clauses):** For absolute certainties, the code injects raw unit clauses `solver.add_clause([good_map["netA"]])`.

By leveraging the pure functions across `/core/`, future scripts inside `/llm/` have total structural control to manipulate standard solving workflows.
