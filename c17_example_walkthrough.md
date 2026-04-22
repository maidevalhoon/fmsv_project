# SAT-Based ATPG: Execution Walkthrough for a Small Circuit

This document explains step-by-step how the SAT-based Automatic Test Pattern Generation (ATPG) algorithm processes a small example circuit (like the `c17` benchmark) to detect a specific stuck-at fault. 

We will walk through the logic file by file, mapping out exactly what happens when you attempt to find a test vector for a single fault.

## 1. `run_atpg.py`: The Main Entry Point

Testing starts by invoking the main script for a target fault. Consider the CLI execution to check if net `6` has a stuck-at-0 (SA0) fault in the `c17` circuit:

```bash
python run_atpg.py --circuit c17 --tech --net 6 --val 0
```

- **Initialization:** Upon starting in single-fault mode, `run_atpg.py` parses the arguments and invokes `load_circuit("benchmarks/json/c17_tech.json")` from the `core.circuit_loader` module.
- **Execution Hand-off:** It extracts the Yosys module data and delegates the problem to `run_single_fault(module_data, fault_net="6", fault_value=0)`. This function is responsible for building the miter circuit CNF, invoking the PySAT solver, and returning the test vector if the fault is detectable.

## 2. `core/circuit_loader.py`: Understanding the Netlist

This file bridges Yosys-synthesized JSON terminology to Python structures.

- **`load_circuit`**: Reads the `c17_tech.json` file. It returns the raw dictionary (`module_data`) representing the circuit's gates (cells), ports (inputs/outputs), and internal wires (nets).
- **`get_port_nets`**: Called next by `run_single_fault`, it collects all primary input and output net IDs. This is required because inputs must be tied together later, and outputs must be checked for discrepancy.
- **`find_driving_gate`**: Identifies which specific logic gate physically outputs to `net 6`. For example, it might identify a NAND cell named `gate4`. This information is critical for the fault injection process.

## 3. `core/miter.py`: Constructing the Miter Copy

The ATPG system works by creating a "miter" (a comparison circuit). `run_single_fault()` inside `run_atpg.py` calls `build_miter()` which performs 6 precise steps to create the Boolean SAT formula:

- **Step 1: The Good Circuit:** It calls `build_circuit_cnf()` to generate a pure, unfaulted Conjunctive Normal Form (CNF) logic for `c17`. Memory integer IDs for its variables start from 1. 
- **Step 2: Gate Location:** It records the driving gate of the fault using `find_driving_gate()` (e.g., `gate4` driving net `6`).
- **Step 3: The Faulty Circuit:** It calls `build_circuit_cnf()` *a second time*, appending to the variable IDs. Crucially, it passes `skip_gate=gate4`. This creates a second copy of `c17`, but the logic constraints for `gate4` are intentionally omitted so that they don't logically contradict the artificial fault unit clause we are about to add.
- **Step 4: Fault Injection:** A unit clause `[-faulty_map["6"]]` is added to explicitly force the variable corresponding to net `6` in the faulty copy to realistically represent an electrical `0` trace lock.
- **Step 5: Tie Primary Inputs:** It generates rules saying every primary input in the good circuit must equal the corresponding primary input in the faulty circuit `(A == A')`. (Exception: if the fault itself is on a primary input, it skips tying that specific net so the stuck value doesn't falsely reflect on the good circuit).
- **Step 6: Output Discrepancy (D-Vars):** For each primary output pair (Good Output vs. Faulty Output), an XOR clause is built representing a 'Discrepancy' variable `$D_i$`. A final `OR($D_0$, $D_1$, ...)` clause is appended—demanding that for the formula to be true, at least one output bit must diverge.

## 4. `core/cnf_builder.py`: Tseitin Logic Transformation

Called twice by `miter.py`, `build_circuit_cnf()` converts gates to basic boolean logic clauses using the Tseitin transformation templates.

- **Gate Aliases:** It maps standard cells (e.g., Nangate `NAND2_X1`) to base behavioral gates (e.g., `$_NAND_`) handled by Yosys.
- **Variable Allocation:** Gives each wire in `c17` its own unique SAT integer parameter. So `net 10` might become variable ID `42`.
- **Applying Clauses:** If traversing an `AND` gate `Y = A & B`, it transforms it into three CNF clauses: `(A ∨ ¬Y) ∧ (B ∨ ¬Y) ∧ (¬A ∨ ¬B ∨ Y)`.
- **Handling `skip_gate`:** While constructing the faulty copy, when the loop reaches `gate4`, it registers its wire connection mappings *but emits 0 structural log clauses*. This effectively "disconnects" where the value comes from so the forced stuck-at condition in Step 4 doesn't result in an immediate contradiction.

## 5. Returning to `run_atpg.py`: The Sat Solver Run

`build_miter()` returns the complete assembled rulebook (`all_clauses`) to `run_atpg.py`.

- **Initialization:** An instance of the `Glucose3` PySAT solver is created. Every clause (lists of negative/positive integers) is loaded into it.
- **Solving:** `solver.solve()` is invoked. 
  - If the solver outputs **SAT** (`True`): It means there is some combination of primary inputs where the good circuit outputs the expected logical result, but the faulty circuit behaves differently due to the net `6` problem. The input parameters are extracted back out using `extract_test_vector`, yielding the ATPG Test Vector (e.g. `n1=0, n2=1, ...`).
  - If the solver outputs **UNSAT** (`False`): No test vector could force a discrepancy at any primary output, meaning the net `6` stuck-at-0 fault is undetectable and logically redundant in `c17`. 
- **CNF Exporter Trigger:** Following evaluation, the generated constraint instances are automatically dumped into raw files for reference:
  - `benchmarks/cnf/c17/good_circuit.cnf` (The baseline formulas without any targeted fault clauses applied)
  - `benchmarks/cnf/c17/SA0_net6.cnf` (The full active formulated miter explicitly evaluated for this single run)
- **Reporting:** Metrics about constraint variables, solver time, decision rules, and the final Test Vector are cleanly printed mapping directly to the script output.
