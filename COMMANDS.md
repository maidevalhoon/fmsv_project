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

**Output:** `benchmarks/json/<circuit>.json` and `benchmarks/netlists/<circuit>_netlist.v`

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
# Tech-mapped circuit (Nangate cells: AOI21, OAI21, NAND2, INV, AND2)
yosys -p 'read_json benchmarks/json/eval_techmap.json; show -format svg -prefix eval_techmap_circuit'

# Non-tech-mapped circuit (generic gates: $_AND_, $_NOT_, $_NAND_)
yosys -p 'read_json benchmarks/json/eval_non_tech.json; show -format svg -prefix eval_non_tech_circuit'

# Original RTL NAND-level circuit (no synthesis, matches Verilog source)
yosys -p 'read_verilog benchmarks/c17.v; proc; opt; show -format svg -prefix c17_nand'

# Json to svg
yosys -p 'read_json benchmarks/json/c17.json; show -format svg -prefix c17_circuit'
```

**Note:** The `show` command requires `xdot` (install with `brew install xdot`) or use `-format pdf`/`-format svg` to save to file.

---

## 2. Step 1 — SAT-Based ATPG (Baseline)

### Full fault sweep (all Verilog wires, SA0 + SA1)

```bash
# c17 (default)
python run_atpg.py --json benchmarks/json/c17.json

# Other benchmarks
python run_atpg.py --json benchmarks/json/c432.json
python run_atpg.py --json benchmarks/json/c880.json
python run_atpg.py --json benchmarks/json/c1355.json
```

### Include Yosys intermediate nets (all nets, not just Verilog wires)

```bash
python run_atpg.py --json benchmarks/json/c17.json --all-nets
```

### Single fault mode

```bash
# Test SA0 on net 6 (N7 in c17)
python run_atpg.py --json benchmarks/json/c17.json --net 6 --val 0

# Test SA1 on net 7 (N22 in c17)
python run_atpg.py --json benchmarks/json/c17.json --net 7 --val 1
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

### Run LLM-guided ATPG on c17

```bash
# Default model (gemini-2.0-flash-lite)
python -m llm.run_llm_atpg

# With verbose output (shows LLM responses and hint parsing)
python -m llm.run_llm_atpg --verbose

# Limit to first N faults (useful for testing/debugging)
python -m llm.run_llm_atpg --max-faults 5 --verbose

# Adjust cooldown between API calls (default 4s; increase if hitting rate limits)
python -m llm.run_llm_atpg --verbose --cooldown 10

# Use a different Gemini model
python -m llm.run_llm_atpg --model gemini-2.0-flash
python -m llm.run_llm_atpg --model gemini-1.5-flash
python -m llm.run_llm_atpg --model gemini-2.5-pro-preview-03-25
```

**Output:** `reports/c17_llm_comparison.txt`

### Alternative invocation (if module run doesn't work)

```bash
python llm/run_llm_atpg.py --verbose
```

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

# 3. Run baseline ATPG
python run_atpg.py --json benchmarks/json/c17.json

# 4. Generate insights
python run_insights.py

# 5. Run LLM-guided ATPG (requires GEMINI_API_KEY)
python -m llm.run_llm_atpg --verbose
```

---

## 7. Useful One-Liners

```bash
# Count faults for a circuit (Verilog wires only)
python -c "
from core.circuit_loader import load_circuit, enumerate_verilog_nets
_, m = load_circuit('benchmarks/json/c17.json')
nets = enumerate_verilog_nets(m)
print(f'{len(nets)} wires → {len(nets)*2} faults')
"

# List all Verilog wire names for a circuit
python -c "
from core.circuit_loader import load_circuit, get_signal_name_map
_, m = load_circuit('benchmarks/json/c17.json')
for nid, name in sorted(get_signal_name_map(m).items(), key=lambda x: int(x[0])):
    print(f'  net{nid} → {name}')
"

# Show the compact LLM prompt for a specific fault
python -c "
from core.circuit_loader import load_circuit
from core.miter import build_miter
from llm.query_builder import build_fault_prompt
_, m = load_circuit('benchmarks/json/c17.json')
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
