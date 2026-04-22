# Commands Reference

All commands should be run from the **project root** directory.

---

## Prerequisites

```bash
# Install Python dependencies
pip install -r requirements.txt

# For LLM-guided mode, set your Gemini API key
export GEMINI_API_KEY="AIza..."
# (or GOOGLE_API_KEY — both are checked)
```

---

## 1. Yosys Synthesis (Nangate Technology Library)

Synthesis maps the design to real Nangate Open Cell Library standard cells (AND2_X1, NAND2_X1, INV_X1, AOI21_X1, etc.).

```bash
# Synthesize c17 (default — uses synth.ys, hardcoded to c17)
yosys synth/synth.ys

# Synthesize a specific benchmark (uses synth.tcl — requires -c flag)
CIRCUIT=c432 yosys -c synth/synth.tcl
CIRCUIT=c880 yosys -c synth/synth.tcl
CIRCUIT=c1355 yosys -c synth/synth.tcl

# Synthesize ALL benchmarks
for circ in c17 c432 c499 c880 c1355 c3540 c6288; do
  CIRCUIT=$circ yosys -c synth/synth.tcl
done
```

**Outputs Generated:** 
- `benchmarks/json/<circuit>_tech.json` (JSON representation using Nangate gates)
- `benchmarks/json/<circuit>_notech.json` (JSON representation using generic boolean basic gates)
- `benchmarks/netlists/<circuit>_tech_netlist.v` (Synthesized Verilog matching the tech JSON)
- `benchmarks/netlists/<circuit>_notech_netlist.v` (Synthesized Verilog matching the notech JSON)
- `benchmarks/json/eval_techmap.json` (Technology mapping evaluation statistics)
- `benchmarks/json/eval_non_tech.json` (Generic baseline evaluation statistics)
### Visualize the Circuit (Yosys `show`)

```bash
# View c17 mapped to Nangate cells (opens xdot viewer)
yosys -p 'read_verilog benchmarks/netlists/c17_netlist.v; read_liberty -lib "Technology Library/NangateOpenCellLibrary_typical.lib"; show'

# Save as PDF instead of opening viewer
yosys -p 'read_verilog benchmarks/netlists/c17_netlist.v; read_liberty -lib "Technology Library/NangateOpenCellLibrary_typical.lib"; show -format pdf -prefix c17_circuit'

# Save as SVG
yosys -p 'read_verilog benchmarks/netlists/c17_netlist.v; read_liberty -lib "Technology Library/NangateOpenCellLibrary_typical.lib"; show -format svg -prefix c17_circuit'

# View other circuits
yosys -p 'read_verilog benchmarks/netlists/c432_netlist.v; read_liberty -lib "Technology Library/NangateOpenCellLibrary_typical.lib"; show'
```

### Generate Circuit SVG from JSON Netlists

```bash
# Export the deep generic Notech circuit (13 nets -> 26 faults)
yosys -p "read_json benchmarks/json/c17_notech.json; show -format svg -prefix c17_notech"

# Export the Nangate mapped circuit (11 nets -> 22 faults)
yosys -p "read_json benchmarks/json/c17_tech.json; read_liberty -lib \"Technology Library/NangateOpenCellLibrary_typical.lib\"; show -format svg -prefix c17_tech"
```

**Note:** The generated images will drop in your project root as `c17_notech.svg`, `c17_notech.dot`, `c17_tech.svg`, and `c17_tech.dot`. Reading the `.svg` generated files requires an SVG viewer (like your web browser).

---

## 2. Step 1 — SAT-Based ATPG (Baseline)

### Full fault sweep

```bash
# c17 (tech mapped - 22 Faults)
python run_atpg.py --circuit c17 --tech

# c17 (generic basic gates mapped - 26 Faults)
python run_atpg.py --circuit c17 --notech

# Other benchmarks
python run_atpg.py --circuit c432 --tech
python run_atpg.py --circuit c880 --tech
python run_atpg.py --circuit c1355 --tech
```

### Single fault mode

```bash
# Test SA0 on net 6 (N7 in c17, tech mapped)
python run_atpg.py --circuit c17 --tech --net 6 --val 0

# Test SA1 on net 7 (N22 in c17, notech mapped)
python run_atpg.py --circuit c17 --notech --net 7 --val 1
```

---

## 3. Step 1 — Generate Insights Reports

```bash
# Generate insights for all benchmarks
python run_insights.py

# This produces:
#   reports/<circuit>_insights.txt    (detailed per-fault results)
#   reports/summaries/<circuit>_summary.txt (compact summary for LLM)
```

---

## 4. Step 2 — LLM-Guided ATPG

### Run LLM-guided ATPG

```bash
# Default model (gemini-2.0-flash-lite) on c17 tech-mapped
python llm/run_llm_atpg.py --circuit c17 --tech

# Generic basic gates on c432 with verbose output
python llm/run_llm_atpg.py --circuit c432 --notech --verbose

# Limit to first N faults (useful for testing/debugging)
python llm/run_llm_atpg.py --max-faults 5 --verbose

# Adjust cooldown between API calls (default 4s; increase if hitting rate limits)
python llm/run_llm_atpg.py --verbose --cooldown 10

# Use a different Gemini model
python llm/run_llm_atpg.py --model gemini-2.0-flash
python llm/run_llm_atpg.py --model gemini-1.5-flash
```

**Output:** `reports/<circuit>_llm_comparison.txt`

---

## 5. Extract Summary Reports

```bash
python extract_reports.py
```

---

## 6. Quick Verification Pipeline

Run the full pipeline for c17 end-to-end:

```bash
# 1. Synthesize (maps to Nangate cells)
yosys synth/synth.ys

# 2. View the circuit
yosys -p 'read_verilog benchmarks/netlists/c17_netlist.v; read_liberty -lib "Technology Library/NangateOpenCellLibrary_typical.lib"; show'

# 3. Run baseline ATPG (tech mapped)
python run_atpg.py --circuit c17 --tech

# 4. Generate insights
python run_insights.py --circuit c17 --tech

# 5. Extract compact summaries
python extract_reports.py

# 6. Run LLM-guided ATPG (requires GEMINI_API_KEY)
python llm/run_llm_atpg.py --circuit c17 --tech --verbose
```

---

## 7. Useful One-Liners

```bash
# Count faults for a circuit
python -c "
from core.circuit_loader import load_circuit, enumerate_all_nets
_, m = load_circuit('benchmarks/json/c17_tech.json')
nets = enumerate_all_nets(m)
print(f'{len(nets)} wires → {len(nets)*2} faults')
"

# List all Verilog wire names for a circuit
python -c "
from core.circuit_loader import load_circuit, get_signal_name_map
_, m = load_circuit('benchmarks/json/c17_tech.json')
for nid, name in sorted(get_signal_name_map(m).items(), key=lambda x: int(x[0])):
    print(f'  net{nid} → {name}')
"

# Show the compact LLM prompt for a specific fault
python -c "
from core.circuit_loader import load_circuit
from core.miter import build_miter
from llm.query_builder import build_fault_prompt
_, m = load_circuit('benchmarks/json/c17_tech.json')
_, gmap, _, _, _ = build_miter(m, '2', 0)
print(build_fault_prompt(m, '2', 0, gmap))
"
```

---

## Supported Benchmarks

| Circuit | Inputs | Outputs | Verilog Wires | Faults (Verilog-only) |
|---------|--------|---------|---------------|----------------------|
| c17     | 5      | 2       | 11            | 22                   |
| c432    | 36     | 7       | varies        | varies               |
| c499    | 41     | 32      | varies        | varies               |
| c880    | 60     | 26      | varies        | varies               |
| c1355   | 41     | 32      | varies        | varies               |
| c3540   | 50     | 22      | varies        | varies               |
| c6288   | 32     | 32      | varies        | varies               |

---

## Gemini Model Options

| Model | Speed | Quality | Free Tier |
|-------|-------|---------|-----------|
| `gemini-2.0-flash-lite` | Fastest | Basic | Best quota |
| `gemini-2.0-flash` | Fast | Good | Good quota |
| `gemini-1.5-flash` | Fast | Good | Good quota |
| `gemini-2.5-pro-preview-03-25` | Slow | Best | Limited |

---

## 8. Export CNF Formula (Proof of Concept)

While the ATPG system solves CNF entirely in-memory to prevent disk I/O bottlenecks, it automatically dumps copies of the circuit and any single-faults you test specifically to help you debug or pass to external solvers.

Every time you run `python run_atpg.py --circuit c17 --tech`, it will automatically extract the exact active CNF and drop the clauses here:
`benchmarks/cnf/c17/good_circuit.cnf`

**Outputs Generated:**
- `benchmarks/cnf/<circuit>/good_circuit.cnf` (The baseline unfaulted circuit constraints)
- `benchmarks/cnf/<circuit>/SA<val>_net<X>.cnf` (The combined miter formulation forcing the given fault parameter to diverge)
