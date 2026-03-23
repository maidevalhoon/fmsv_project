# How to Run — SAT-Based ATPG

## Setup

```bash
cd "project faltu"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 0 — Re-synthesize a Circuit (optional)

Only needed if you change a `.v` file.

```bash
yosys synth/synth.ys
# Outputs: benchmarks/c17_netlist.v  and  benchmarks/c17.json
```

---

## Step 1 — Run ATPG

### Single fault mode

Test one specific stuck-at fault:

```bash
# SA0 on net 6  (AND gate output in circuit.v)
python run_atpg.py --json benchmarks/json/circuit.json --net 6 --val 0

# SA1 on net 6
python run_atpg.py --json benchmarks/json/circuit.json --net 6 --val 1

# Use c17 benchmark instead
python run_atpg.py --json benchmarks/json/c17.json --net 10 --val 0
```

### Full fault sweep

Test **every** net × {SA0, SA1} and print coverage:

```bash
python run_atpg.py --json benchmarks/json/c17.json
python run_atpg.py --json benchmarks/json/circuit.json
```

---

## Step 2 — Collect Solver Metrics (LLM guidance report)

Runs the full sweep and writes a structured report to `reports/`:

```bash
python run_insights.py --json benchmarks/json/circuit.json --out reports/solver_insights_report.txt

# c17 benchmark
python run_insights.py --json benchmarks/json/c17.json --out reports/c17_insights_report.txt
```

The report contains:
1. Circuit overview (net ID → name table)
2. Fault coverage summary
3. Per-fault results: test vectors, output diffs, solver stats
4. LLM guidance analysis (difficulty, redundant faults, PySAT hint examples)
5. Raw data table for baseline comparison

---

## Default argument values

| Script | `--json` default | `--out` default |
|---|---|---|
| `run_atpg.py` | `benchmarks/json/c17.json` | — |
| `run_insights.py` | `benchmarks/json/c17.json` | `reports/solver_insights_report.txt` |
