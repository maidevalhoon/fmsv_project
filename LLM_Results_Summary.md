# LLM-Guided SAT-ATPG Experimental Results Summary
**Target Circuit:** `c17`
**Evaluated Faults:** 4 (Extracted sequentially)

## 1. Execution Commands
To reproduce these specific metrics, run the following sequence from the root directory:

CIRCUIT=c3540 yosys -c synth/synth.tcl


```bash
# 1. Ensure core dependencies and the virtual context are active
source venv/bin/activate

# 2. Run the baseline evaluation to populate the S1 reference file
python extract_reports.py

# 3. Run the LLM-guided optimization layer for the first 4 faults natively
python llm/run_llm_atpg.py --max-faults 4 --verbose
```

## 2. Experimental Data & Comparison

The prompt generation loop successfully instructed the LLM on 4 boundary faults, forcing strict boolean consistency. The solver executed and accepted the guidance without conflict (Phase 1 SAT).

### Resolution Metrics: Baseline vs LLM-Guided
| Metric | Baseline (Without LLM) | LLM-Guided (S2) |
| :--- | :---: | :---: |
| **Acceptance Rate** | - | **100.0%** (4/4 Accepted)|
| **Average Decision Count** | 6.75 | 7.00 |
| **Solver Conflicts** | 0.0 | 0.0 |

*Note: For a circuit of `c17`'s scale, the solver natively searches the variable tree without encountering structural conflicts, resulting in 0 internal conflicts for both the baseline PySAT search and the seeded PySAT search.*

### Computational Overhead
The critical metric of ATPG scaling is evaluating whether the reduction in back-tracking outweighs the heuristic overhead. The time costs break down cleanly into two segments:

| Timing Phase | Time per Fault |
| :--- | :---: |
| **SAT Solver Calculation (Without LLM)** | ~0.05 ms |
| **SAT Solver Calculation (With LLM)** | ~0.08 ms |
| **LLM Hint Generation Pipeline Latency** | ~1,214.25 ms |

### Summary Conclusion
The LLM guidance pipeline is functionally sound and successfully forces the exact mathematical variable allocations requested by the framework (100% validation rate, zero fallbacks). However, the overhead API transmission time strictly dwarfs the intrinsic performance of the Glucose3 boolean solver engine by several orders of magnitude. Measurable decision-tree time reduction will strictly rely on deployment against large-depth ISCAS benchmarks (e.g. `c6288`) where conflict resolution is mathematically heavy.
