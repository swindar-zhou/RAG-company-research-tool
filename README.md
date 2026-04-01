# Hedge Fund RAG System

A learning project documenting how I studied Claude Code's production architecture to build a domain-specific RAG pipeline for fundamental equity research.

> Built by reading Claude Code's leaked source — `fileStateCache.ts`, `FileReadTool.ts`, `forkedAgent.ts` — and applying those patterns to a financial document analysis problem.

---

## What This Is

This is a **prototype-stage implementation**, not a production system. No real 10-K filings have been processed yet. The architecture is complete and runnable; the benchmarks and accuracy claims in an earlier version of this README were removed because they were theoretical, not measured.

What exists and works:
- LRU file cache with `isPartialView` flag (directly ported from Claude Code)
- PDF reading with line-number attribution using PyMuPDF
- In-memory knowledge graph with provenance tracking
- Multi-model wrapper framework (Claude + GPT functional; GLM/Gemini stubbed)
- Benchmarking harness with cost/latency tracking infrastructure

What doesn't exist yet:
- Validation on real SEC filings
- Measured accuracy or hallucination rates
- Redis or PostgreSQL integration (designed, not implemented)
- Fork agent pattern (designed, not implemented)

---

## The Problem I'm Solving

Traditional RAG systems built with LangChain chunk documents before understanding them. For financial documents, this breaks semantic relationships that span multiple pages.

**Example of semantic fragmentation:**
```
10-K, pages 23–25:
  p23: "Revenue $500M"
  p24: "Adjusted for one-time items..."
  p25: "Normalized revenue $300M"

Static RAG retrieves chunk from p23 only.
LLM sees "$500M" and treats it as the answer.
Correct answer: $300M normalized.
```

This matters for hedge funds because a misread revenue figure can cascade into a wrong valuation model. The core design principle here is: **understand first, structure second** — read the full document, then extract, rather than chunking blindly upfront.

---

## Use Case

**Who would use this:** A small fundamental equity research team (2–5 analysts) screening 30–100 companies per quarter across SEC filings (10-K, 10-Q, 8-K).

**Concrete workflow:**
1. Analyst uploads AAPL's 10-K PDF
2. System reads the full document with line-level attribution
3. LLM extracts structured financial metrics into a knowledge graph
4. Analyst queries: *"What's AAPL's normalized revenue and where does that number come from?"*
5. System returns: `$394.3B — source: 10-K page 25, line 892`

**Why this is better than asking ChatGPT directly:** Every fact is traceable to a source line. The analyst can verify. The system can't hallucinate a number that doesn't appear in the document.

---

## Architecture

### High-Level Flow

```
PDF
 │
 ▼
read_pdf_with_line_numbers()     ← PyMuPDF, tracks line→page mapping
 │
 ▼
FileStateCache (LRU, 25MB)       ← isPartialView flag prevents partial reads
 │
 ├── cache hit, full view → return immediately
 └── cache miss or partial → continue
         │
         ▼
    extract_revenue()            ← regex pre-filter + LLM structuring
    extract_margins()
    extract_risks()
         │
         ▼
    KnowledgeGraph               ← nodes + edges + provenance metadata
         │
         ▼
    query()                      ← answer + source attribution
```

### Component 1: FileStateCache — Learned from Claude Code

**Source:** `claude-code/src/utils/fileStateCache.ts`

The original TypeScript type:
```typescript
export type FileState = {
  content: string
  timestamp: number
  offset: number | undefined
  limit: number | undefined
  isPartialView?: boolean
}
```

My Python port:
```python
class FileState:
    def __init__(self, content, timestamp, offset=0,
                 limit=None, isPartialView=False):
        self.content = content
        self.timestamp = timestamp
        self.offset = offset
        self.limit = limit
        self.isPartialView = isPartialView  # The key flag
```

**Why `isPartialView` matters:**

```
Without the flag:
  1. Read pages 1–10 of a 100-page 10-K
  2. Extract revenue → cache it
  3. Analyst asks to edit the summary
  4. System allows it — but summary is based on 10% of the document
  5. Result: analysis missing pages 11–100

With the flag:
  1. Read pages 1–10, cache with isPartialView=True
  2. Analyst asks to edit
  3. System blocks: "Document not fully read — re-read complete file first"
  4. Result: data integrity preserved
```

Cache limits mirror Claude Code exactly: 100 entries, 25MB total, LRU eviction.

---

### Component 2: PDF Reading with Line Attribution

**Source inspiration:** `claude-code/src/tools/FileReadTool/FileReadTool.ts`

Claude Code adds line numbers to every file read for source attribution. I applied the same pattern to PDFs:

```python
def add_line_numbers(content: str, start_line: int = 1) -> str:
    lines = content.split('\n')
    return '\n'.join(
        f"{str(i + start_line).rjust(6)}→{line}"
        for i, line in enumerate(lines)
    )
```

Output format:
```
     1→===== PAGE 1 =====
     2→
     3→APPLE INC.
     4→ANNUAL REPORT ON FORM 10-K
    ...
   892→Revenue for fiscal year 2023 was $394.3 billion
```

The `page_map` dict maps every line number back to its source page, enabling citations like `"page 25, line 892"` rather than vague references.

---

### Component 3: Knowledge Graph

**My addition — not in Claude Code.**

Claude Code uses an LRU cache for short-term, session-scoped memory. I added a knowledge graph for cross-session persistence with explicit relationship structure.

**Graph schema:**
```
Nodes:
  company:{TICKER}         → {type: company, ticker: str}
  metric:revenue:{TICKER}  → {type: metric, value: float, source_line: str}
  doc:{path}               → {type: document, path: str, doc_type: str}

Edges:
  (company) --[has_revenue]--> (metric)   metadata: {year, source_doc}
  (company) --[filed]--------> (doc)      metadata: {year}
```

**Why graph over vector embeddings for this use case:**

| | Embeddings | Knowledge Graph |
|---|---|---|
| Relationship structure | Lost in vector space | Explicit edges |
| Source attribution | No native support | Every edge has provenance |
| Query type | Similarity search | Exact lookup + graph traversal |
| Explainability | Black box | Fully traceable |
| Failure mode | Confident hallucination | Missing node (explicit) |

The tradeoff: knowledge graph requires structured extraction to succeed. Unstructured text (risk factor prose, MD&A narrative) is harder to extract reliably than numerical metrics. I handle this with a fallback: regex extraction when LLM structuring fails, stored with lower confidence.

---

### Component 4: Multi-Model Framework

**Source inspiration:** Claude Code's model routing and fork agent patterns.

```python
class ModelRouter:
    def choose_model(self, task_importance, deadline_sec, budget_usd):
        if task_importance == "critical" and deadline_sec > 60:
            return "claude"      # Best reasoning, highest cost

        elif deadline_sec < 30:
            return "gpt-4o-mini" # Fast, cheap, lower quality

        else:
            return "gpt-4o"      # Balance
```

**Pricing baked into the wrappers (current as of March 2026):**

| Model | Input | Output | Status |
|---|---|---|---|
| `claude-sonnet-4-20250514` | $3/1M | $15/1M | ✅ Functional |
| `gpt-4o` | $2.50/1M | $10/1M | ✅ Functional |
| `gpt-4o-mini` | $0.15/1M | $0.60/1M | ✅ Functional |
| `gemini-1.5-pro` | $1.25/1M | $5/1M | 🚧 Stubbed |
| `GLM-4` (self-hosted) | compute only | compute only | 🚧 Stubbed |

The benchmark harness (`model_benchmark.py`) tracks latency, token usage, and cost per call automatically. Accuracy metrics will be populated once real SEC filings are processed against XBRL ground truth.

---

### Component 5: Benchmark Harness

`model_benchmark.py` is infrastructure for measuring — not results yet.

It tracks:
- `latency_ms` per call (wall clock)
- `cost_usd` computed from actual token usage via API response
- `accuracy` compared to ground truth (requires labeled test set)
- `hallucination_rate` via number-in-source-text check

Current test suite has 2 toy examples. The plan for real evaluation:
- Ground truth: XBRL-tagged data from SEC EDGAR (machine-readable, authoritative)
- Test set: 20–30 10-K filings across sectors
- Hallucination check: every extracted number must appear verbatim in source text

---

## What I Learned from Claude Code's Architecture

**1. `isPartialView` as a first-class concern**

Most RAG systems don't track whether they've seen the full document. Claude Code treats partial reads as a distinct state that blocks downstream operations. This is the right engineering instinct — uncertainty about completeness should be explicit, not silently assumed away.

**2. Line numbers as the atomic unit of attribution**

Source attribution at the page level isn't precise enough. Claude Code numbers every line and tracks offsets. I applied this to PDFs via the `page_map` structure. The result: every extracted fact has a `(page, line)` address, not just a document name.

**3. Orchestration logic belongs in prompts, not code**

From `coordinatorMode.ts`: the multi-agent coordinator is a system prompt, not a state machine. Instructions like "do not rubber-stamp weak work" are given to the LLM, not encoded in routing logic. This is more flexible as models improve.

**4. Cache invalidation is an accounting problem**

Claude Code's `promptCacheBreakDetection.ts` tracks 14 vectors that can bust prompt cache. At $3/1M tokens, a cache miss on a 100k-token system prompt costs $0.30. At scale, this matters more than most optimizations.

---

## Design Decisions

### Dynamic RAG vs Static RAG

I read the full document before chunking rather than pre-chunking at ingest. The cost: higher initial token usage per document. The benefit: semantic relationships (like the revenue adjustment example) are preserved.

This is the right tradeoff for a hedge fund use case where the document corpus is small (<1000 filings) and quality matters more than throughput. It's the wrong tradeoff for a search engine indexing millions of documents.

### Knowledge Graph vs pgvector

I chose an in-memory graph (NetworkX-compatible structure) for the prototype. For production, the realistic options are:

- **Neo4j**: Cypher queries, native graph storage, good for complex traversals
- **pgvector + PostgreSQL**: Familiar stack, stores vectors and structured data, simpler ops
- **TigerGraph**: Better at scale, steeper learning curve

For financial data with structured relationships and strict attribution requirements, a proper graph DB beats a vector DB. For unstructured text retrieval, you'd want both.

### Why Not LangChain?

LangChain orchestrates chains of LLM calls. It doesn't solve the chunking problem — it just gives you tools to build chains around a static RAG retriever. The semantic fragmentation issue exists regardless of which orchestration framework you use.

The more fundamental choice is: who decides what context the LLM sees? In static RAG, a pre-chunking step decides. In this system, the LLM reads the full document and decides what to extract. That's the meaningful architectural difference.

---

## Repository Structure

```
src/
├── rag_implementation.py      # Core RAG system (~400 lines)
│   ├── FileState              # Cache entry with isPartialView
│   ├── FileStateCache         # LRU cache (25MB, 100 entries)
│   ├── add_line_numbers()     # PDF attribution
│   ├── read_pdf_with_line_numbers()
│   ├── extract_revenue()      # Regex + LLM extraction
│   ├── extract_margins()
│   ├── extract_risks()
│   ├── KnowledgeGraph         # Nodes + edges + BFS query
│   └── HedgeFundRAG           # Main orchestration class
│
├── model_benchmark.py         # Benchmarking framework (~350 lines)
│   ├── BenchmarkResult        # Dataclass: latency, cost, accuracy
│   ├── ClaudeWrapper          # Functional
│   ├── GPTWrapper             # Functional
│   ├── GLMWrapper             # Stubbed
│   ├── GeminiWrapper          # Stubbed
│   └── ModelBenchmark         # Test harness + report generation
│
├── claude-examples/           # Reference: Claude Code source patterns
├── learnings/                 # Architecture analysis notes
│   ├── cost_latency_optimization.md
│   ├── langgraph_explained.md
│   └── openclaw_vs_claude_code.md
│
└── test-files/                # Sample inputs (no real 10-Ks yet)
```

---

## Next Steps

**To make this real:**

1. **Run against actual SEC filings** — pull 10-Ks from EDGAR, process with the pipeline, compare extracted numbers against XBRL ground truth
2. **Measure, don't estimate** — populate the benchmark harness with real results before publishing any accuracy claims
3. **Implement fork agents** — byte-exact system prompt inheritance for parallel company analysis (designed, not built)
4. **Add persistence** — swap in-memory KnowledgeGraph for Neo4j or pgvector
5. **Handle extraction failures gracefully** — unstructured sections (risk factors prose) need a fallback path to raw text chunks with lower confidence scoring

---

## Running the Code

```bash
pip install pymupdf lru-dict anthropic openai

export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
```

```python
from src.rag_implementation import HedgeFundRAG

rag = HedgeFundRAG(model="claude")

analysis = rag.analyze_10k(
    pdf_path="path/to/10k.pdf",
    ticker="AAPL",
    pages="1-50"
)

answer = rag.query("What's AAPL's revenue?")
# Returns: {'answer': '$394.3B', 'source': '10k.pdf, line 892', 'confidence': 'high'}
```

---

## References

- **Claude Code source** (leaked March 31, 2026) — primary architecture reference
  - `src/utils/fileStateCache.ts` — LRU cache + isPartialView
  - `src/tools/FileReadTool/FileReadTool.ts` — line numbers, source attribution
  - `src/utils/forkedAgent.ts` — fork agents, cache-safe parameters
  - `src/services/compact/autoCompact.ts` — context compaction
  - `src/coordinator/coordinatorMode.ts` — orchestration as prompt

---

*~40 hours of study and implementation. Architecture is complete; validation against real filings is the next step.*