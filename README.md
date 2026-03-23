# LLM-Guided SAT-Based ATPG

**CS-525 FMSV — Problem Statement 4**

A SAT-based Automatic Test Pattern Generation framework where an LLM acts as a semantic heuristic oracle to guide a SAT solver for detecting stuck-at faults in digital circuits.

---

## Project Layout

```
project/
├── core/                    # Shared ATPG library
│   ├── __init__.py
│   ├── circuit_loader.py    # Load Yosys JSON, net utilities
│   ├── cnf_builder.py       # Tseitin CNF encoding (all gate types)
│   ├── miter.py             # Miter circuit construction
│   └── fault_manager.py     # Fault enumeration & result extraction
│
├── benchmarks/              # Circuit source files
│   ├── circuit.v            #   Simple test circuit
│   ├── c17.v               #   ISCAS-85 c17 benchmark
│   ├── json/               #   Yosys JSON netlists
│   │   ├── circuit.json
│   │   └── c17.json
│   ├── netlists/           #   Synthesized Verilog netlists
│   │   ├── circuit_netlist.v
│   │   └── c17_netlist.v
│   └── cnf/                #   DIMACS CNF files
│       └── circuit_logic.cnf
│
├── synth/
│   └── synth.ys             # Yosys synthesis script
│
├── reports/                 # Auto-generated reports (git-ignored *.txt)
│   └── .gitkeep
│
├── llm/                     # Step 2 — LLM guidance layer (empty)
│   └── .gitkeep
│
├── _archive/                # Old monolithic scripts (reference only)
│   ├── atpg.py
│   ├── solver_insights.py
│   └── netlist_parser.py
│
├── run_atpg.py              # Entry point: single fault or full sweep
├── run_insights.py          # Entry point: solver metrics + LLM report
├── requirements.txt
├── .gitignore
├── walkthrough.md           # How to run the project
└── README.md                # This file
```

---

## Core Module Summary

| Module | Exported | Purpose |
|---|---|---|
| `core/circuit_loader.py` | `load_circuit`, `get_port_nets`, `get_net_name_map`, `enumerate_all_nets`, `find_driving_gate` | Parse Yosys JSON, enumerate nets |
| `core/cnf_builder.py` | `build_circuit_cnf` | Tseitin encoding for AND/OR/NOT/BUF/NAND/NOR/XOR/XNOR/MUX |
| `core/miter.py` | `build_miter` | Build complete miter CNF for one fault |
| `core/fault_manager.py` | `enumerate_stuck_at_faults`, `fault_label`, `extract_test_vector`, `extract_output_diff` | Fault list & result parsing |

---

## How It Works

```
  Verilog RTL          Yosys             JSON Netlist         Python (PySAT)
 ┌──────────┐      ┌──────────┐      ┌──────────────┐     ┌────────────────┐
 │  c17.v   │─────▶│ synth.ys │─────▶│  c17.json    │────▶│  run_atpg.py   │
 │ (circuit)│      │ (Yosys)  │      │ (gates+wires)│     │ (miter + SAT)  │
 └──────────┘      └──────────┘      └──────────────┘     └───────┬────────┘
                                                                  │
                                                          ┌───────▼────────┐
                                                          │  Test Vector   │
                                                          │  or UNSAT      │
                                                          └────────────────┘
```

**Key concepts:**
- **Tseitin Transformation** — each gate becomes an equivalent set of CNF clauses
- **Miter Circuit** — good + faulty circuits share inputs; outputs are XORed to detect divergence
- **skip_gate** — the gate driving the fault net has its Tseitin clauses removed in the faulty copy, preventing an encoding contradiction with the fault unit clause
- **Stuck-At Fault Model** — a wire is permanently stuck to 0 (SA0) or 1 (SA1)

---

## Quick Start

See [`walkthrough.md`](walkthrough.md) for full run commands.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Single fault
python run_atpg.py --json benchmarks/json/circuit.json --net 6 --val 0

# Full sweep
python run_atpg.py --json benchmarks/json/c17.json

# Solver metrics report
python run_insights.py --json benchmarks/json/c17.json --out reports/c17_insights.txt
```

---

## Roadmap

| Step | Status |
|---|---|
| Step 1 — SAT-based ATPG baseline | ✅ Complete |
| Step 2 — LLM guidance layer | 🔲 Not started |

**Step 2 plan:** Design LLM prompt templates, translate LLM predictions into PySAT assumptions, implement UNSAT-core feedback loop, compare baseline vs LLM-guided metrics.
