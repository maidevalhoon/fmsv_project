# ── Yosys Synthesis Script (Tcl — Nangate Technology Library) ──────────────────
#
# Usage (run from project root):
#   yosys -c synth/synth.tcl                   ← synthesizes c17 (default)
#   CIRCUIT=c432 yosys -c synth/synth.tcl      ← synthesizes c432
#   CIRCUIT=c880 yosys -c synth/synth.tcl      ← synthesizes c880
#
# NOTE: Must use -c flag (Tcl mode). Plain `yosys synth/synth.tcl` will fail.
#       For a non-Tcl version, use synth.ys instead (hardcoded to c17).
#
# Maps the design to Nangate Open Cell Library standard cells.
# After running, use `yosys -p "read_verilog benchmarks/netlists/<circuit>_netlist.v; show"`
# to view the circuit with real Nangate cell names.
# ──────────────────────────────────────────────────────────────────────────────

# ── Set circuit name (override via env: CIRCUIT=c432) ─────────────────────────
if { [catch {set CIRCUIT $::env(CIRCUIT)}] } { set CIRCUIT c17 }

# ── Step 1: Read RTL ──────────────────────────────────────────────────────────
yosys read_verilog benchmarks/${CIRCUIT}.v

# ── Step 2: Read Nangate technology library ──────────────────────────────────
yosys read_liberty -lib "Technology Library/NangateOpenCellLibrary_typical.lib"

# ── Step 3: Synthesise & map to Nangate cells ────────────────────────────────
yosys synth -top ${CIRCUIT}
yosys abc -liberty "Technology Library/NangateOpenCellLibrary_typical.lib"
yosys clean

# ── Step 4: Output ───────────────────────────────────────────────────────────
yosys write_verilog benchmarks/netlists/${CIRCUIT}_netlist.v
yosys write_json    benchmarks/json/${CIRCUIT}.json
