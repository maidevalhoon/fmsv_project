# How the LLM Improves SAT-ATPG Performance

## The Core Idea

A SAT solver finds test patterns by **exploring a search tree** of variable assignments. Each branch point is called a **decision**. More decisions = more work = slower solving.

The LLM acts as a **heuristic oracle**: it analyzes the circuit and fault, then suggests which primary input values are likely to detect the fault. These suggestions are injected into the SAT solver as **assumptions** — pre-set variable assignments that the solver treats as given facts.

If the LLM guesses correctly, the solver skips the decisions it would have needed to figure out those assignments on its own.

```
WITHOUT LLM (Step 1 Baseline):
  SAT solver starts from scratch
  → explores full search tree
  → makes N decisions to find test vector

WITH LLM (Step 2 Guided):
  LLM pre-assigns K input variables (assumptions)
  → SAT solver starts with K variables already fixed
  → explores a smaller search tree
  → makes (N - K) or fewer decisions
```

---

## The Two-Phase Solving Strategy

For each stuck-at fault, Step 2 runs two phases:

```
                ┌─────────────────────────┐
                │  LLM generates hints    │
                │  (input assignments)    │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │  Phase 1: GUIDED SOLVE  │
                │  solver.solve(          │
                │    assumptions=hints)   │
                └──────┬──────────┬───────┘
                       │          │
                   SAT ✓      UNSAT ✗
                       │     (hints wrong)
                       │          │
                       ▼          ▼
                   DONE     ┌─────────────────────────┐
                  (fast!)   │  Phase 2: FALLBACK      │
                            │  solver.solve()         │
                            │  (no assumptions,       │
                            │   same as Step 1)       │
                            └─────────────────────────┘
```

- **Phase 1 succeeds (GUIDED):** The LLM hints were correct. The solver found a test vector with fewer decisions than baseline. This is a performance win.
- **Phase 1 fails (FALLBACK):** The LLM gave bad hints (they led to a contradiction). The solver retries without any assumptions, performing identically to Step 1. No performance loss — just wasted API time.

This design guarantees that **the LLM can never reduce fault coverage**. It can only help or be neutral.

---

## What Data the LLM Receives

The LLM gets a compact prompt (~150 tokens for c17) containing:

| Component | Example (c17, SA0 on N10) | Purpose |
|---|---|---|
| **Fault description** | `N10 stuck-at-0` | What to detect |
| **Detection goal** | `find inputs where N10=1 in good circuit` | What the solver needs |
| **Primary inputs** | `N1, N2, N3, N6, N7` | What the LLM can assign |
| **Primary outputs** | `N22, N23` | Where to observe the fault |
| **Gate equations** | `N10 = NAND(N1, N3)` | Circuit structure for reasoning |

The prompt does NOT include SAT variable IDs, Tseitin clauses, or Yosys internal wire names — only human-readable circuit information.

---

## What the LLM Returns

A JSON object with two fields:

```json
{
  "signal_assignments": {"N1": 1, "N3": 1, "N6": 0},
  "sensitization_hint": "Set N1=N3=1 to force N10=0 in good circuit, 
                          sensitize through N22"
}
```

- **`signal_assignments`**: Partial or full primary input values. Each is converted to a PySAT literal (e.g., `N1=1` becomes `+var3`, `N3=0` becomes `-var5`).
- **`sensitization_hint`**: A reasoning explanation (logged for analysis, not used by the solver).

---

## How Performance is Measured

The key metric is **SAT solver decisions** — a hardware-independent measure of how much work the solver did.

| Metric | What it measures |
|---|---|
| **S1 decisions** | Decisions made by the baseline solver (Step 1, no hints) |
| **S2 decisions** | Decisions made by the LLM-guided solver (Step 2) |
| **Decision reduction %** | `(S1 - S2) / S1 × 100` — higher is better |
| **Hints accepted** | Faults where Phase 1 (guided) succeeded |
| **Hints rejected** | Faults where Phase 1 failed and Phase 2 (fallback) was used |
| **Fault coverage** | Must remain the same as Step 1 (guaranteed by design) |

### Example comparison (ideal scenario):

```
Fault          S1 Dec    S2 Dec    Mode      Reduction
SA0@N10           12         3    GUIDED       75.0%
SA1@N10            8         8    FALLBACK      0.0%
SA0@N11           15         5    GUIDED       66.7%
SA1@N11            9         2    GUIDED       77.8%
─────────────────────────────────────────────────────
Average          11.0       4.5               59.1%
```

When the LLM provides correct hints:
- The guided solve uses **fewer decisions** because the solver's search space is pre-narrowed
- Fewer decisions also means **less wall-clock time** (microseconds for small circuits)

When hints are wrong or empty:
- The fallback solve matches the baseline exactly
- No performance loss, just the overhead of the API call

---

## Why LLM Guidance Helps More on Larger Circuits

For a tiny circuit like c17 (5 inputs, 6 NAND gates), the baseline solver already needs very few decisions (~7-16). The LLM overhead (API latency) far exceeds any solver speedup.

The real benefit appears on larger ISCAS benchmarks:

| Circuit | Gates | Inputs | Baseline avg decisions | Expected LLM benefit |
|---|---|---|---|---|
| c17 | 6 | 5 | ~9 | Minimal (solver is already fast) |
| c432 | 160 | 36 | ~hundreds | Moderate (partial input fix helps) |
| c1355 | 546 | 41 | ~thousands | Significant |
| c6288 | 2416 | 32 | ~tens of thousands | Large (deep cone, many conflicts) |

For larger circuits, a correct partial assignment from the LLM can eliminate entire subtrees of the search space, saving thousands of decisions.

---

## The Feedback Loop (Token Efficiency)

A key design goal is keeping the LLM prompt small to minimize API cost:

```
Original prompt design:    ~800 tokens  (included SAT var maps, Yosys internals)
Optimized prompt design:   ~150 tokens  (circuit equations + fault only)
                           ─────────
                           ~5× reduction
```

Optimizations applied:
1. **Signal names over net IDs**: `N10 = NAND(N1, N3)` instead of `net9 = AND(net2, net5)`
2. **Gate collapsing**: Yosys AND+NOT pairs collapsed back to NAND
3. **No solver internals**: SAT variable maps removed entirely
4. **No verbose context**: Only the circuit structure and fault target

---

## Summary

| Aspect | Without LLM (Step 1) | With LLM (Step 2) |
|---|---|---|
| **Solver input** | Raw CNF only | CNF + assumption literals from LLM |
| **Search space** | Full | Reduced (inputs pre-assigned) |
| **Decisions** | Baseline | Equal or fewer |
| **Fault coverage** | 100% of detectable | Same (guaranteed) |
| **Overhead** | None | API latency (~0.5-2s per fault) |
| **Best for** | Small circuits | Large circuits with many inputs |

The LLM does not replace the SAT solver — it **guides** it. The solver remains the source of truth for correctness. The LLM is a performance accelerator that reduces search effort when its hints are accurate, and gracefully falls back to baseline when they are not.
