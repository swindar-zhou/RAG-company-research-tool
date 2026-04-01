# Quick Reference Card - Cost & Latency Optimization

> **Honesty note**: Numbers below are calculated from published API pricing and architectural reasoning — not measured from real workloads. Cache hit rates (80/15/4%) and quality scores (96/92/85%) are industry estimates, not benchmarks from this codebase. Treat them as illustrative targets, not validated results.

---

## Cost Optimization Strategies

### 1. Prompt Caching — How It Works

Claude API caches the prefix of a prompt. If the first N tokens are byte-identical across requests, you pay the cached read rate (~10x cheaper) instead of full input cost.

**Calculated estimate (not measured):**
```
System prompt: ~2000 tokens @ $3/1M = $0.006 per call

Without caching — 50 companies:
  50 × $0.006 = $0.30

With caching — same 50 companies:
  First call (cache miss):  $0.006
  Remaining 49 (cache hit): 49 × ~$0.0006 = $0.029
  Total: ~$0.035

Estimated savings: ~88%
(Only holds if prompts are byte-exact — any variation busts the cache)
```

**What can break cache hits:**
- Timestamps or random IDs in system prompt
- GrowthBook flags changing between calls
- Any dynamic content in the prompt prefix

---

### 2. Fork Agents — Why Byte-Exact Matters

Fork agents pass the parent's already-rendered prompt to children, guaranteeing the same bytes hit the API each time.

**Calculated estimate (not measured):**
```
Parent renders prompt once:   $0.006 (cache miss)
Each child inherits exactly:  ~$0.0006 (cache hit)

50 children:
  $0.006 + (50 × $0.0006) = $0.036
  vs naive (no caching):    $0.30

Estimated savings: ~88%
```

**Not yet implemented** in this codebase — it's a design pattern learned from `forkedAgent.ts`, not live code.

---

### 3. Tiered Model Selection — The Core Cost Strategy

Use cheap models to filter, expensive models only on what remains.

**Pricing (from Anthropic/OpenAI docs, March 2026):**

| Model | Input | Output | Est. latency |
|---|---|---|---|
| `claude-sonnet-4-20250514` | $3/1M | $15/1M | ~800ms |
| `gpt-4o` | $2.50/1M | $10/1M | ~400ms |
| `gpt-4o-mini` | $0.15/1M | $0.60/1M | ~200ms |
| GLM-4 (self-hosted) | $0 API | $0 API | ~500ms + GPU |

**Calculated example — 1000 companies, three-tier:**
```
Tier 1 — Filter (GPT-4o-mini):
  1000 × ~$0.008/analysis = $8
  → narrows to ~100 candidates

Tier 2 — Screen (GPT-4o):
  100 × ~$0.12/analysis = $12
  → narrows to ~20 finalists

Tier 3 — Deep-dive (Claude):
  20 × ~$0.18/analysis = $3.60

Total: ~$23.60
vs all-Claude: ~$180
Estimated savings: ~87%
```

**Assumption**: Each "analysis" is ~50k input tokens + ~500 output tokens.
These per-analysis costs are estimates — actual costs depend on your prompt length.

---

### 4. LRU Cache — Avoiding Re-reads

**What's implemented:** `FileStateCache` in `rag_implementation.py` — LRU, 100 entries, 25MB cap, `isPartialView` flag.

**Calculated savings:**
```
Without cache:
  Read AAPL 10-K 3× in a session: 3 × $0.15 = $0.45

With LRU cache:
  First read (cache miss): $0.15
  2nd and 3rd (cache hit): $0
  Total: $0.15

Estimated savings: 67% over 3 reads
```

This one is straightforward to verify once real PDFs are processed.

---

## Latency Reduction Strategies

### 1. Parallel Execution

**Implemented:** `asyncio.gather()` pattern in design; not yet wired into the main RAG class.

```
Sequential — 50 companies, 3 min each:
  50 × 3 min = 150 min

Parallel — 10 concurrent batches:
  5 batches × 3 min = 15 min
  Speedup: 10x

True parallel (no batching, no rate limits):
  max(3 min) = 3 min
  Speedup: 50x (theoretical ceiling)
```

Rate limits will constrain you in practice — 10 concurrent is a realistic target, not 50.

---

### 2. Streaming — Perceived vs Actual Latency

**Not yet implemented** in this codebase. Pattern would be:

```python
async for chunk in client.messages.stream(...):
    yield chunk.text  # User sees first token in ~0.5s
```

**Effect:** Actual latency stays the same; time-to-first-byte (TTFB) drops from ~90s to ~0.5s.
This is a UX improvement, not a throughput improvement. Worth doing; easy to add.

---

### 3. Multi-Tier Cache Latency

**Designed, partially implemented** (L1 in-memory exists; Redis and PostgreSQL are stubs):

```
L1 — In-memory LRU:  ~0ms    (implemented ✅)
L2 — Redis:          ~5ms    (designed, not built ⬜)
L3 — PostgreSQL:     ~50ms   (designed, not built ⬜)
L4 — LLM call:       ~2000ms (always available)

Theoretical weighted average (assuming 80/15/4/1% hit rates):
  0.80×0 + 0.15×5 + 0.04×50 + 0.01×2000 = 22.75ms

These hit rates are assumed, not measured.
```

---

### 4. Model Latency — Published Benchmarks

These are approximate figures from public model documentation and community benchmarks, not measured from this code:

```
GPT-4o-mini: ~200ms   (fastest, lowest quality)
GPT-4o:      ~400ms   (balanced)
Gemini 1.5:  ~600ms   (cost-effective)
Claude:      ~800ms   (highest quality, slowest)

Ratio Claude → GPT-mini: ~4x faster
```

---

## Decision Framework

### Which model for which task?

```python
# Rough heuristic — not a validated formula
decision_value_usd = ...   # Value of the decision this analysis informs
cost_per_analysis_usd = ...

ratio = decision_value_usd / cost_per_analysis_usd

if ratio > 1_000_000:   # e.g. $100M decision / $0.18 cost
    use "claude"         # Maximize quality

elif ratio < 100_000:   # e.g. $1K decision / $0.18 cost
    use "gpt-4o-mini"   # Minimize cost

else:
    use "gpt-4o"         # Balance
```

**The real principle:** Match model quality to the cost of being wrong, not the cost of the API call.

---

## Pre-Interview Checklist

### Can you explain the architecture?
- [x] How prompt caching works — byte-exact prefix, cache miss on first call, cache hit on repeats
- [x] How fork agents save cost — inherit parent's rendered prompt, guarantee cache hits
- [x] Tiered model selection — cheap filter, expensive finals, why this preserves quality
- [x] isPartialView flag — what it tracks, what it blocks, why it prevents hallucinations
- [x] Knowledge graph vs embeddings — structure preserved, explicit provenance, queryable relationships
- [x] LangGraph vs agent-first — LangGraph is workflow orchestration, doesn't fix static RAG

### What's actually built vs designed?
- [x] `FileStateCache` — LRU, 25MB, isPartialView ✅
- [x] `read_pdf_with_line_numbers` — PyMuPDF, page_map, attribution ✅
- [x] `KnowledgeGraph` — nodes, edges, BFS query ✅
- [x] `ClaudeWrapper` + `GPTWrapper` — real API calls, cost tracking ✅
- [x] `ModelBenchmark` — harness structure, report generation ✅
- [ ] Fork agents — design documented, not implemented ⬜
- [ ] Streaming responses — not implemented ⬜
- [ ] Redis L2 cache — not implemented ⬜
- [ ] Validated benchmarks on real 10-Ks — not done yet ⬜

### What would you add next?
Be ready to answer: asyncio parallelism, streaming, fork agent implementation, real SEC filing validation against XBRL ground truth.

---

## One-Minute Pitch (Honest Version)

"I studied Claude Code's production architecture after the source leak and built a RAG system for equity research that applies its core patterns.

**What I learned and ported:** The `isPartialView` flag from `fileStateCache.ts` — marks when a document was partially read and blocks downstream operations on incomplete data. Line-number attribution from `FileReadTool.ts` — every extracted fact traces back to a source line. The fork agent pattern from `forkedAgent.ts` — byte-exact prompt inheritance for cache hits.

**What I added beyond it:** A knowledge graph layer instead of vector embeddings. For financial data with structured relationships — revenue, margins, periods, filings — explicit graph edges with provenance beat similarity search. Every fact has a `(page, line)` address.

**What I calculated, not yet measured:** The cost savings from tiered model selection (~87%) and the latency from multi-tier caching (~23ms average) are architectural estimates based on published API pricing and assumed hit rates. I haven't run it against real 10-Ks yet — that's the next step, using XBRL data from SEC EDGAR as ground truth.

**The architecture is complete and sound. The validation is what remains.**"