# Problem 4: LLM-Guided SAT-Based ATPG
*Tracking for team syncs.*

## 1. Setup & Baseline
- [x] 1.1 Analyze papers & guidelines
- [x] 1.2 Yosys setup & synth test (`circuit.v` → `circuit_netlist.v`)
- [x] 1.3 Git & `.gitignore` setup
- [x] 1.4 Python Virtual Env setup
- [x] 1.5 Install PySAT library (`python-sat`)
- [x] 1.6 Update `synth.ys` to also output `circuit.json`
- [x] 1.7 Create Python `netlist_parser.py` to extract gates and wires
- [x] 1.8 Map JSON wires to unique IDs and apply Tseitin Transformations to output `circuit_logic.cnf`
