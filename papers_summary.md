# Exhaustive Project Papers & Assignment Background

This document encapsulates every nuance, requirement, detail, and theoretical background derived from the course assignment documents and referenced research papers. It serves as the authoritative source for the context behind "Problem Statement 4" and the associated literature.

---

## 1. Defining The Baseline: ATPG and SAT 

**Automatic Test Pattern Generation (ATPG)** is the cornerstone of structural digital circuit testing. Its goal is to find binary input vectors ("test patterns") that reveal physical manufacturing defects (modeled logically as stuck-at-0 or stuck-at-1 faults) by causing structural outputs to misbehave. 

While classical algorithms (PODEM, FAN) traversed the circuit tree using topological structures, modern implementations convert the testing problem into a logic formula using **Boolean Satisfiability (SAT)**.
- **The SAT formulation:** A "Good" circuit CNF and a "Faulty" circuit CNF are built. Inputs are tied, and outputs are XORed (setting a divergence constraint). If a SAT solver finds a satisfiable configuration, the resulting input vector is a valid test. If UNSAT, the fault cannot be excited/propagated (it is logically redundant).

---

## 2. Exhaustive Assignment Details: Problem Statement 4

**Title:** LLM-Guided Decision Heuristics for SAT-Based Automatic Test Pattern Generation.

### The Problem Gap:
Currently, SAT solvers rely on syntax-based heuristic learning (like VSIDS — Variable State Independent Decaying Sum). VSIDS branches on variables that appear most often in recent conflict clauses. 
*The flaw:* It treats digital logic as abstract math, utterly oblivious to semantic constraints (like datapath sensitization or gate-level structure). Current SAT-ATPG doesn't "learn" semantic properties across multiple faults; every run is a blank slate.
*The LLM Opportunity:* Large Language Models possess semantic reasoning capabilities. They understand the difference between an ALU datapath and an FSM. The hypothesis of this assignment is that LLMs can act as a **semantic heuristic oracle**, providing hints to the SAT solver to preemptively skip massive computational black holes.

### Primary Research Questions to be Addressed:
1. Can an LLM predict useful partial assignments to guarantee fault activation and propagation?
2. Can LLM-guided variable selection decisively slash the SAT solver's decision counts, conflict counts, and aggregate solving time?
3. Does LLM guidance specifically assist in the detection of conventionally "hard-to-detect" faults?
4. Is "cross-fault knowledge transfer" viable? (Can an LLM's discoveries from resolving Fault A immediately accelerate resolving Fault B?)

### Project Implementation Architecture

**Step 1: SAT-Based ATPG Core (Currently Implemented)**
- Consume RTL to gate-level structural netlists using Yosys.
- Implement the comprehensive logic to encode stuck-at faults into CNF.
- Construct the miter architecture.
- Utilize a bare-metal solver (`PySAT`).

**Step 2: LLM Guidance Layer (To be Implemented)**
- Systematically provide the fault instance to an LLM.
- LLM outputs: Predicted fault activation conditions, suggested sensitization paths, partial input assignments, or broad variable ordering.
- The Python bridging software must translate LLM output into concrete PySAT `assumptions` or soft constraints (soft guidance), or branching logic overrides.

**Step 3: Closed-Loop Evaluation (To be Implemented)**
- If the SAT heavily conflicts or returns UNSAT, an evaluation must parse the UNSAT core / conflict trace.
- A systematic prompt feeds this technical feedback *back* into the LLM logic context, forcing it to iteratively refine its guidance logic.

### Expected Deliverables and Evaluation Layout:
Students must execute this on `ISCAS-85/89` benchmarks and complex Arithmetic Modules.
*Metrics to rigorously analyze:* Fault coverage percentages, raw SAT solving times, internal Decision counts, internal Conflict counts. 
*End result documentation:* SAT-ATPG code implementation, LLM integration module, full complexity evaluation, discussions on why the model succeeded or failed.
*(Bonus):* Extending to transition faults, LLM-guided test point insertion strings, cross-circuit multi-learning.

<br/>

*(Note: The other problem statements focused on LLM-assisted SAT equivalence checking for structurally divergent circuits, contextual limits of cloud-agent memory systems, and LLM-based autonomous assertion checking/formalization).*

---

## 3. Theoretical Literature & Research Summaries

To ground the SAT implementation, the project demands understanding two pre-eminent research papers.

### A. "Combinational Test Generation Using Satisfiability" (Stephan, Brayton, Sangiovanni-Vincentelli, 1996)
*Context:* This established the "TEGUS" algorithm, arguably the benchmark text proving that SAT was structurally competitive with state-of-the-art D-algorithm / PODEM branches without requiring randomly injected vectors.

**Key Findings:**
1. **Simplified Characteristic Equations:** Prior methodologies literally translated the D-algorithm primitives. TEGUS shrinks the generated Product-of-Sums structures rapidly. For instance, rather than mapping massive redundant backward `!D-chain` logic traces, TEGUS logically discards traces knowing the fault path inherently cascades forward.
2. **Greedy SAT Branching (DFS):** Standard SAT solving is broad. TEGUS operates a swift Depth-First-Search variable ordering prioritizing structural hierarchy. It immediately pulls an unassigned variable from the very next unsatisfied clause and branches, maximizing computational velocity.
3. **Global Implications:** Instead of solely relying on the base equation, TEGUS computationally iterated global implications recursively on historically difficult faults. This explicitly subsumed ancient algorithmic bottlenecks like "unique sensitization paths".
*Result:* TEGUS cleanly computed on 100% of ISCAS networks, beating structural competitors by extreme magnitudes in runtime scaling.

### B. "A New SAT-based ATPG for Generating Highly Compacted Test Sets" (Eggersglüß et al., 2012)
*Context:* In industrial realms, test time is hyper-expensive. Creating a unique test vector for every single fault produces bloated test sets. This paper pioneers methodologies to produce "Compacted" (dense) test vectors.

**Key Findings:**
1. **Dynamic vs. Static Compaction:** Static compaction happens post-generation (merging unknown `X` vectors after the fact). Dynamic Compaction involves restricting ATPG generation incrementally on the fly to fulfill multiple faults.
2. **The Multiple Target Test Generation (MTTG) Approach:** The core problem with treating dynamic compaction via structural ATPG (like PODEM) is that arbitrarily restricting paths routinely forces the topological trees into unsolvable voids.
3. **The SAT Advantage:** Formulating MTTG as a SAT problem allowed developers to lean on SAT's defining trait: robustness under extreme constraints. Since SAT deals globally in clauses rather than routing topological wires, if an assignment combination exists that exposes multiple faults at once without collision, SAT will systematically discover it via non-chronological backtracking and conflict clause learning.
*Result:* Pushing test pattern compaction to SAT configurations led to up to a 63% scale reduction in required vector pattern counts over the dynamic-compaction methods reigning at the time for stuck-at and transition faults.
