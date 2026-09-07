# CLAIR-Fin: An Adversarial Multi-Agent Framework for Claim-Level Verification and Adaptive Debate in Cross-Modal Financial QA

Faithful question answering over long financial documents means reconciling evidence across
prose, tables, and charts that do not always agree. Prior work handles this only in pieces:
retrieved evidence is trusted regardless of which modality it came from, adversarial debate
verifies a whole report instead of its individual claims, and grounding is checked only after
the answer is drafted. **CLAIR-Fin** is a nine-agent framework that closes these gaps with four
contributions:

- **Financial Claim Ledger** — every question is decomposed into atomic, typed claims held in a
  typed evidence graph that serves as the single verification, audit, and citation artifact.
- **Asymmetric Evidence Authority (AEA)** — evidence trust is conditioned on claim type (table
  cells for exact figures, prose for causal attribution) instead of treating all modalities
  equally, so cross-modal disagreement is resolved by a stated, auditable prior.
- **Chain-of-Custody Verification (CoCV)** — grounding is checked at the hand-off between
  drafting and adversarial review, not only at the pipeline's exit, stopping attribution drift
  before it propagates.
- **Adaptive Rebuttal Cycle (ARC)** + **Hallucination Risk Index (HRI)** — adversarial debate is
  routed only to contested claims with depth scaled to what it finds, and a terminal entailment
  audit is paired with a continuous per-claim risk score that separates claims that survived
  scrutiny from those never challenged.

To evaluate it we release **BB-FinQA-X**, a 500-question cross-modal financial QA set built from
the Bangladesh Bank Annual Report and stratified by query type, presentation format, and
difficulty. CLAIR-Fin raises faithfulness from **0.780 → 0.889** over a single-pass RAG baseline,
beats stronger retrieval baselines such as HyDE and Graph-RAG, and abstains on 5.4% of questions
rather than forcing an unsupported answer.

- 📄 Paper: https://arxiv.org/abs/2608.13706
- 🤗 Dataset: https://huggingface.co/datasets/Fatema142/BB-FinQA-X

![CLAIR-Fin workflow](diagrams/CLAIR-Fin_Workflow.png)

---

## Core idea

Prior work addresses one piece of the problem in isolation; CLAIR-Fin combines four mechanisms:

| Mechanism | What it does | Why it matters |
|---|---|---|
| **AEA** – Asymmetric Evidence Authority | Conditions evidence trust on claim type (table cells for exact figures, prose for causal attribution) instead of treating all modalities equally | Resolves cross-modal disagreement by a stated, auditable prior |
| **CoCV** – Chain-of-Custody Verification | Checks grounding at the hand-off between drafting and adversarial review, not only at the pipeline's exit | Stops attribution drift before it propagates |
| **ARC** – Adaptive Rebuttal Cycle | Routes only contested claims (coverage `< 0.75`) to adversarial debate; depth scales with what the debate finds (max 2 rounds) | Spends compute on the claims most likely to be wrong |
| **HRI** – Hallucination Risk Index | Continuous per-claim risk score paired with the binary audit verdict | Distinguishes claims that survived scrutiny from claims never contested |

---

## Dataset: BB-FinQA-X

500 English question–answer pairs grounded in the Bangladesh Bank Annual Report, hand-written and
validated in a three-stage protocol (author drafting → independent review → external
banking-sector domain validation → mandatory consensus). Each item records its supporting evidence
(text passage / table cell / chart element) and source page, and is stratified along three
controlled dimensions.

**By query type × difficulty**

| Query Type | Easy | Medium | Hard | Total |
|---|--:|--:|--:|--:|
| Fact Extraction | 70 | 65 | 15 | 150 |
| Comparison | 35 | 75 | 25 | 135 |
| Trend Analysis | 20 | 35 | 10 | 65 |
| Numerical Calculation | 15 | 35 | 10 | 60 |
| Multi-hop Reasoning | 10 | 30 | 10 | 50 |
| Evidence Retrieval | 25 | 10 | 5 | 40 |
| **Total** | **175** | **250** | **75** | **500** |

**By presentation format × difficulty**

| Format | Easy | Medium | Hard | Total |
|---|--:|--:|--:|--:|
| Text Only | 35 | 50 | 15 | 100 |
| Table Only | 35 | 50 | 15 | 100 |
| Chart Only | 18 | 25 | 7 | 50 |
| Text + Table | 52 | 75 | 23 | 150 |
| Text + Chart | 18 | 25 | 7 | 50 |
| Table + Chart | 17 | 25 | 8 | 50 |
| **Total** | **175** | **250** | **75** | **500** |

Text Only / Table Only and Chart Only / Text + Chart are **matched content pairs** (same
indicator, query type, and difficulty; different evidence format), enabling controlled format
comparisons.

---

## Methodology

A question flows through eight phases (see the workflow diagram above):

| Phase | Agent(s) | Role |
|---|---|---|
| I. Ingestion | *(offline)* | Per-page native or vision extraction of text/tables/charts; tables extracted 3× with agreement check; sentence-aware chunking into a Milvus store |
| II. Claim Decomposition | Planner-Orchestrator | Split the question into 1–8 atomic, typed claims (`FACT_NUMERIC`, `FACT_TREND`, `CAUSE_ATTRIBUTION`, `RATIO_IDENTITY`) |
| III. Evidence Retrieval | Narrative / Tabular / Visual | Modality-specific retrieval (k = 8 / 6 / 5), blended dense + lexical scoring; ground cells and spans, derive metrics deterministically |
| IV. Fusion & Escalation | Ledger Guardian | Merge evidence into the ledger, link same-metric nodes; compute AEA **coverage** `A(cᵢ)`; escalate iff `A(cᵢ) < 0.75` |
| V. Adversarial Rebuttal | Affirmative + Adversarial Counsel | Draft, then attack across 6 categories (numeric, scope, fiscal-year, causal overclaim, citation gap, visual over-precision); rebut while high-severity findings remain (≤ 2 rounds) |
| VI. Chain-of-Custody | *(reuses Judge-Auditor)* | Entailment check at the drafting→review hand-off; one bounded repair, else custody marked broken |
| VII. Terminal Audit | Judge-Auditor | Authority-weighted argmax over modality confidence; entailment gate; log equal-weight counterfactual to the Authority Docket; compute HRI |
| VIII. Synthesis | Brief Synthesizer | Compose one cited answer; abstain iff no claim passed audit |

---

## Results (BB-FinQA-X, n = 500)

Qualitative examples — retrieved contexts, CLAIR-Fin's generated response, and the gold answer,
with cited figures highlighted. In the third case the chart's approximate readings (4.5% / 4.0%)
diverge from the exact table figures (4.22% / 3.97%); the response follows the table and text
rather than the chart, as prescribed by Asymmetric Evidence Authority.

![CLAIR-Fin qualitative results](diagrams/CLAIR-Fin_Example.png)

**Retrieval and generation metrics — CLAIR-Fin vs. ablations and retrieval baselines**

| Configuration | Faithfulness ↑ | Answer Relevancy ↑ | Context Precision ↑ | Context Recall ↑ |
|---|--:|--:|--:|--:|
| **CLAIR-Fin** | **0.889** | **0.696** | **0.816** | **0.897** |
| w/o Terminal Audit | 0.845 | 0.687 | 0.803 | 0.886 |
| w/o ARC (debate) | 0.770 | 0.680 | 0.781 | 0.862 |
| w/o AEA | 0.883 | 0.692 | 0.812 | 0.893 |
| w/o CoCV | 0.857 | 0.689 | 0.807 | 0.881 |
| Vanilla RAG | 0.780 | 0.680 | 0.700 | 0.840 |
| HyDE RAG | 0.874 | 0.691 | 0.801 | 0.885 |
| Hierarchical RAG | 0.865 | 0.688 | 0.752 | 0.831 |
| Graph-RAG | 0.832 | 0.694 | 0.729 | 0.889 |

Answer outcomes: **59.2%** correct / 23.6% partial / 11.8% incorrect / **5.4%** abstained.
Faithfulness rate (published claims passing citation-entailment): **0.783**.
Debate utilization: **0.646**. AEA impact rate: **0.515**.

**Metrics by presentation format** (sample-weighted)

| Format | n | Faithfulness ↑ | Answer Relevancy ↑ | Context Precision ↑ | Context Recall ↑ |
|---|--:|--:|--:|--:|--:|
| Text Only | 100 | 0.870 | 0.675 | 0.795 | 0.878 |
| Table Only | 100 | 0.900 | 0.705 | 0.830 | 0.912 |
| Chart Only | 50 | 0.850 | 0.665 | 0.775 | 0.847 |
| Text + Table | 150 | 0.915 | 0.725 | 0.845 | 0.925 |
| Text + Chart | 50 | 0.875 | 0.685 | 0.805 | 0.887 |
| Table + Chart | 50 | 0.880 | 0.675 | 0.795 | 0.881 |

**Metrics by query type** (sample-weighted)

| Query Type | n | Faithfulness ↑ | Answer Relevancy ↑ | Context Precision ↑ | Context Recall ↑ |
|---|--:|--:|--:|--:|--:|
| Fact Extraction | 150 | 0.920 | 0.735 | 0.855 | 0.928 |
| Comparison | 135 | 0.900 | 0.710 | 0.825 | 0.908 |
| Trend Analysis | 65 | 0.885 | 0.685 | 0.805 | 0.892 |
| Numerical Calculation | 60 | 0.865 | 0.665 | 0.785 | 0.875 |
| Multi-hop Reasoning | 50 | 0.840 | 0.625 | 0.755 | 0.835 |
| Evidence Retrieval | 40 | 0.839 | 0.656 | 0.780 | 0.862 |

**Takeaways:** removing **ARC** hurts most (0.889 → 0.770) — targeted debate is the single most
consequential mechanism; the **terminal audit** carries more of the faithfulness guarantee than
any upstream check, but **CoCV** still contributes independently; **AEA** changes the winning
modality in ~52% of contested decisions; chart-dependent evidence and synthesis-heavy query types
(Evidence Retrieval, Multi-hop) remain hardest.

---

## Getting started

### 1. Install

Requires Python ≥ 3.12 and an OpenAI API key.

```bash
git clone https://github.com/fatemafaria142/CLAIR-Fin
cd CLAIR-Fin
pip install -r requirements.txt          # or: uv sync
```

### 2. Configure

Create a `.env` file in the project root:

```ini
OPENAI_API_KEY=sk-...                    # required
OPENAI_MODEL=gpt-4o                      # chat backbone for all agents
OPENAI_VISION_MODEL=gpt-4o               # page table/chart extraction
EMBEDDING_MODEL=text-embedding-3-large
```

### 3. Ingest documents

Drop one or more source PDFs into `data/`, then build the vector store. Ingestion extracts
text, tables (3× with an agreement check), and chart descriptions per page, chunks them, and
upserts everything into a local Milvus Lite store. Run once per corpus.

```bash
mkdir -p data && cp /path/to/report.pdf data/
python -m clairfin.ingestion.ingest
```

### 4. Ask a question

```bash
python -m clairfin.graph.run "What was the point-to-point CPI inflation rate in FY2024?"
```

The pipeline decomposes the question into claims, retrieves and reconciles cross-modal
evidence, debates contested claims, audits every claim, and prints the cited answer (or
abstains). The full audit trail — ledger graph, custody log, Authority Docket, and a
human-readable answer report — is written to `results/<run_id>/`.

### 5. (Optional) Serve as an API

```bash
uvicorn server.main:app --reload         # POST /api/query  {"question": "..."}
```

### 6. (Optional) Run the evaluation suite

Gold questions are pulled straight from the Hugging Face Hub
([`Fatema142/BB-FinQA-X`](https://huggingface.co/datasets/Fatema142/BB-FinQA-X), 500 rows) with
the `--hf` flag — no local question files needed. Outputs are generated by an LLM, so scores
will vary somewhat between runs.

**Whole dataset (all 500 questions):**

```bash
python -m evaluation.generate_responses --hf                     # → evaluation/evaluated_output/bbfinqax.json
python -m evaluation.run_rag_metrics --name bbfinqax             # Table 1: retrieval, generation + ranking metrics
python -m evaluation.run_clairfin_metrics --name bbfinqax        # Table 2: faithfulness rate, coverage, AEA impact
```

**Chapter-wise (one of chapters 1–9):**

```bash
python -m evaluation.generate_responses --hf --chapter 3         # → evaluation/evaluated_output/chapter_3.json
python -m evaluation.run_rag_metrics --chapter 3
python -m evaluation.run_clairfin_metrics --chapter 3
```

Add `--all` to `generate_responses --hf` to write one output file per chapter in a single run.

Retrieval-strategy baselines (HyDE, Hierarchical, Graph-RAG) live under `ablation-study/`, each
with its own `run_and_score.py --chapters 1 2 3`.

## Citation

```bibtex
@misc{faria2026clairfinadversarialmultiagentframework,
  title         = {CLAIR-Fin: An Adversarial Multi-Agent Framework for Claim-Level
                   Verification and Adaptive Debate in Cross-Modal Financial QA},
  author        = {Fatema Tuj Johora Faria and Mukaffi Bin Moin and Jubayer Al Mahmud
                   and M. F. Mridha and Md. Alam Hossain},
  year          = {2026},
  eprint        = {2608.13706},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CL},
  url           = {https://arxiv.org/abs/2608.13706}
}
```

## License

See [`LICENSE`](LICENSE).
