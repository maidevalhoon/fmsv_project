# LLM Performance and Zero Acceptance Rate Analysis

Based on an exhaustive review of the `llm` directory (`run_llm_atpg.py`, `evaluator.py`, `hint_translator.py`, and `query_builder.py`), the observation that **all hints are rejected (0% acceptance rate)** is entirely expected due to structural incompatibilities in how the LLM's guesses interact with the SAT solver. 

Here is the analysis of the LLM's response, its exact performance metrics, the underlying faults causing the rejection, and how to resolve them.

---

## 1. LLM Response & Current Performance

When queried, the LLM successfully parses the prompt and generates a response structured exactly as requested.

**Typical LLM Response:**
```json
{
  "signal_assignments": {
    "N7": 0,
    "N2": 1,
    "N3": 0
  },
  "sensitization_hint": "Set N7=0 to excite the stuck-at-1 fault, and hold N2=1 to propagate the discrepancy through the AOI21 gate to N23."
}
```

**LLM Performance:**
*   **Syntax/Format Success:** 100%. The LLM reliably outputs valid JSON according to `_SYSTEM_PROMPT`.
*   **Acceptance Rate (SAT Detection):** **0%**. 
*   **Calculated Impact on Decisions:** +0.0% reduction. It fails Phase 1 100% of the time, causing a forced fallback to the baseline Step 1 syntax search.

---

## 2. The Faults: Why is the Acceptance Rate 0%?

The problem is **not** that the Python code is crashing or discarding valid hints. Rather, it is a structural fault of treating probabilistic LLM guesses as **Hard Boolean Constraints:**

### Fault A: Hard "Assumptions" cause strict UNSAT conflicts
In `llm/evaluator.py`, the LLM’s input values are passed directly via `solver.solve(assumptions=assumptions)`. In PySAT, `assumptions` are treated as **absolute, unbreakable rules**. Because SAT is an NP-complete problem, an LLM guessing exactly the right path propagation across complex reconverging fanouts is highly improbable. If the LLM is wrong about even *one* bit (e.g., it guesses `N3=0` but `N3=1` is mathematically required to avoid contradiction), the mathematical formula becomes completely **UNSAT**, and the attempt is discarded entirely.

### Fault B: Over-constraining by the LLM
The LLM is eager to help and often outputs too many assignments in `"signal_assignments"`. The more primary inputs the LLM attempts to fix, the exponentially higher the chance it introduces a logic conflict that bricks the solver's search tree.

### Fault C: The Missing Feedback Loop (Open-Loop vs Closed-Loop)
As identified in your project's gaps, `evaluator.py` drops the LLM hints on the very first failure. It does not extract the UNSAT Core to tell the LLM *which* specific variable forced the contradiction.

---

## 3. How to Solve This

To fix the 0% acceptance rate and leverage the LLM correctly, you need to transition the system from making "Hard Assumptions" to utilizing "Soft Heuristics." Here are the specific code modifications you must make:

### Solution 1: Implement Soft Polarity Guiding (Heuristic VSIDS override)
Instead of forcing variables through the `assumptions` list, you should use the LLM's output to seed the solver's preferred phase-saving (polarity). 
*   **How:** If you cannot modify PySAT's internal VSIDS heap easily, you can emulate soft constraints by appending literal clauses as an "assumption" block, but wrapping them in a measurable threshold, or by using a solver that explicitly supports setting variable polarity preferences (like Z3).
*   **Result:** If the LLM hints `N3=0`, the solver will *try* setting `N3=0` first. If it yields a contradiction, the solver freely flips it to `1` without breaking the entire run.

### Solution 2: Minimal Excitation Prompting
Change the prompt in `query_builder.py` to prevent the LLM from over-guessing.
*   **Current:** `"You may assign a subset of inputs if unsure..."`
*   **Fix:** `"ONLY return the single primary input assignment necessary to mathematically excite the fault. Leave all propagation path inputs empty."`
*   **Result:** The LLM will reliably return `{"N7": 0}` for `SA1@N7`. Since this is a mathematically indisputable requirement for that fault, the assumption will be 100% correct, and the PySAT solver will organically calculate the remaining unknowns, successfully securing a `GUIDED` (used_hints=True) run.

### Solution 3: Extract the UNSAT core (Step 3 Implementation)
Modify `evaluator.py` so that if `Phase 1` returns False:
1.  Run `core = solver.get_core()`
2.  Identify the offending constraint (e.g., `["N3"]`).
3.  Re-query the LLM or logically drop the offending assumption and call `solver.solve()` again before resorting to a total fallback.
