# Cost & Latency Optimization — Design Analysis

> **Status note**: This document describes the architectural reasoning and calculated estimates behind the system's cost and latency design. Numbers are derived from published API pricing and assumed workload patterns — **not measured from running real SEC filings through the pipeline**. Where something is implemented vs designed is called out explicitly.

---

## Why This Matters for Hedge Funds

**Scenario**: A PM asks you to screen 50 biotech companies for FDA pipeline risk before end of day.

The naive approach — send every document to the best model, one at a time — has two problems:

1. **Cost spiral**: each company costs roughly the same, and there's no intelligence about which ones deserve deep analysis
2. **Latency ceiling**: sequential processing means the wall-clock time grows linearly with company count

The architecture here addresses both by making two design choices: route documents to cheaper models when precision isn't critical, and process independent analyses in parallel.

Neither of these is novel. The value is in showing you understand *why* they work and *when* they break down.

---

## Part 1: Cost Savings

### 1.1 Prompt Caching — The Mechanism

Claude's API caches the beginning of a request. If the first N tokens sent to the API are byte-identical to a previous request within a cache window (~5 minutes), those tokens are charged at the cache read rate instead of the full input rate.

**Anthropic's published rates (March 2026):**
```
Full input:   $3.00 / 1M tokens
Cache write:  $3.75 / 1M tokens  (slightly more expensive — you pay to store)
Cache read:   $0.30 / 1M tokens  (10x cheaper than full input)
```

**Calculated estimate for 50-company analysis:**
```
System prompt: ~2000 tokens

Without caching:
  50 companies × (2000 tokens × $3/1M) = $0.30

With caching:
  Call 1 (cache miss):  2000 × $3.75/1M = $0.0075
  Calls 2–50 (hits):    49 × (2000 × $0.30/1M) = $0.0294
  Total: ~$0.037

Estimated savings: ~88%
```

**What breaks this:**
- Any dynamic content in the prompt (timestamps, session IDs, random values)
- GrowthBook or feature flag values changing between calls
- Prompt rendered differently per-call (even whitespace differences bust the cache)

The `forkedAgent.ts` pattern in Claude Code exists precisely to prevent these cache misses — by having the parent render the prompt once and passing the already-rendered bytes to children.

---

### 1.2 Fork Agents — Designed, Not Yet Implemented

**Pattern (from `src/utils/forkedAgent.ts` in Claude Code):**

```typescript
type CacheSafeParams = {
  systemPrompt: SystemPrompt,    // Already rendered — byte-exact
  userContext: { [k: string]: string },
  systemContext: { [k: string]: string },
  toolUseContext: ToolUseContext,
  forkContextMessages: Message[],
}
```

The parent agent renders the system prompt once. Each child inherits the exact same bytes, guaranteeing a cache hit on every child API call.

**Calculated estimate:**
```
Parent (cache miss):     $0.006
Child 1–50 (cache hit):  50 × $0.0006 = $0.030
Total: $0.036

vs naive (re-render each time): ~$0.30
Estimated savings: ~88%
```

**Status:** This pattern is documented and understood, but `ForkedAnalysis` is not yet wired into `HedgeFundRAG`. Implementing it requires:
1. Separating system prompt rendering from per-company task generation
2. Passing rendered prompt bytes to parallel `asyncio.gather()` tasks
3. Verifying cache hit rates via API response headers (`x-cache`)

---

### 1.3 Tiered Model Selection — Implemented in ModelRouter

**Principle:** Use cheaper, faster models to discard irrelevant candidates. Apply expensive models only to what survives.

**Published pricing (March 2026):**

| Model | Input | Output | Notes |
|---|---|---|---|
| `claude-sonnet-4-20250514` | $3/1M | $15/1M | Highest quality |
| `gpt-4o` | $2.50/1M | $10/1M | Good balance |
| `gpt-4o-mini` | $0.15/1M | $0.60/1M | Fast, cheap |
| `gemini-1.5-pro` | $1.25/1M | $5/1M | Cost-effective |
| GLM-4 (self-hosted) | $0 API | $0 API | GPU compute cost instead |

**Calculated example — 1000 companies:**

Assumptions: each analysis prompt ~50k input tokens, ~500 output tokens.

```
Per-analysis cost estimates:
  GPT-4o-mini: (50k × $0.15 + 0.5k × $0.60) / 1M ≈ $0.0075 + $0.0003 = $0.008
  GPT-4o:      (50k × $2.50 + 0.5k × $10.00) / 1M ≈ $0.125  + $0.005  = $0.13
  Claude:      (50k × $3.00 + 0.5k × $15.00) / 1M ≈ $0.150  + $0.0075 = $0.158

Three-tier pipeline:
  Tier 1 — GPT-4o-mini, 1000 companies: 1000 × $0.008 = $8.00
  Tier 2 — GPT-4o, top 100:             100  × $0.13  = $13.00
  Tier 3 — Claude, top 20:              20   × $0.158 = $3.16

  Total: ~$24.16
  vs all-Claude: 1000 × $0.158 = $158

Estimated savings: ~85%
```

**Key assumption:** The filter stage achieves ≥95% recall (doesn't miss good candidates). If recall is lower, you're not saving money — you're losing signal. This is the real design risk and needs to be measured, not assumed.

---

### 1.4 LRU Cache — Implemented

**What exists:** `FileStateCache` in `rag_implementation.py` — 100 entries, 25MB total, LRU eviction, `isPartialView` tracking.

**Calculated savings on repeated reads:**
```
A 10-K PDF ≈ 100 pages × ~500 tokens/page = ~50k tokens
Cost to process: 50k × $3/1M = $0.15

Without cache (3 reads in a session):  3 × $0.15 = $0.45
With LRU cache (1 miss + 2 hits):      1 × $0.15 = $0.15

Savings: 67% over 3 reads
```

This is straightforward to verify once real PDFs are processed. The math here is reliable since token count per document is measurable.

---

## Part 2: Latency Reduction

### 2.1 Parallel Execution — Designed, Not Yet Wired

**Pattern:**
```python
async def analyze_portfolio(self, tickers: List[str]) -> List[Dict]:
    tasks = [self.analyze_10k_async(ticker) for ticker in tickers]
    # Process in batches to respect API rate limits
    results = []
    for i in range(0, len(tasks), 10):  # 10 concurrent max
        batch_results = await asyncio.gather(*tasks[i:i+10])
        results.extend(batch_results)
    return results
```

**Theoretical speedup:**
```
Sequential — 50 companies, 3 min each:
  50 × 3 = 150 min

Batched parallel — 10 concurrent:
  5 batches × 3 min = 15 min
  Speedup: 10x

Unlimited parallel (ceiling):
  max(3 min) = 3 min
  Speedup: 50x
```

**Realistic ceiling:** API rate limits and token-per-minute caps will constrain throughput. The actual speedup depends on your Anthropic tier. Batch size of 10 is a conservative starting point.

---

### 2.2 Streaming — Not Yet Implemented

Streaming doesn't reduce actual latency — it reduces *perceived* latency by delivering the first tokens to the user sooner.

**How to add it:**
```python
async def analyze_with_streaming(self, pdf_path: str):
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[...]
    ) as stream:
        async for text in stream.text_stream:
            yield text  # caller sees tokens as they arrive
```

**Effect:**
- Without streaming: user waits ~30–90s seeing nothing
- With streaming: user sees first token in ~0.5s, full response still takes ~30–90s

Worth implementing; estimated ~1 day of work to add to the existing wrappers.

---

### 2.3 Multi-Tier Cache — L1 Implemented, L2/L3 Designed

**What's built:** In-memory LRU (L1) via `FileStateCache`.

**What's designed but not built:**
- L2: Redis for team-wide sharing across analyst sessions
- L3: PostgreSQL for persistent knowledge graph storage

**Theoretical latency breakdown (assumed hit rates, not measured):**

```
L1 in-memory:  ~0ms    — assumed 80% hit rate (same session, same files)
L2 Redis:      ~5ms    — assumed 15% hit rate (team sharing, prev sessions)
L3 PostgreSQL: ~50ms   — assumed 4%  hit rate (structured KG queries)
L4 LLM call:  ~2000ms  — assumed 1%  hit rate (truly new queries)

Weighted average:
  0.80×0 + 0.15×5 + 0.04×50 + 0.01×2000
  = 0 + 0.75 + 2 + 20
  = ~23ms

vs naive (always LLM): ~2000ms
Theoretical speedup: ~87x
```

**The 80/15/4/1% split is an assumption.** In practice, hit rates depend heavily on query distribution. A team where every analyst asks about different companies will see much lower L2 hit rates than a team focused on the same sector.

---

### 2.4 Model Latency — Published / Community Benchmarks

These figures are approximate, from public sources and community testing. Not measured from this code:

```
GPT-4o-mini:  ~200ms   (time-to-first-token, typical)
GPT-4o:       ~400ms
Gemini 1.5:   ~600ms
Claude Sonnet: ~800ms

Ratio (Claude → GPT-mini): ~4x
```

Latency varies significantly with load, region, request size, and streaming vs non-streaming. Treat these as order-of-magnitude references.

---

## Part 3: Trade-Off Framework

### Cost vs Quality

The right model isn't the best model — it's the cheapest model that's accurate enough for the decision being made.

**Heuristic:**
```
Quality threshold for filtering:
  85% recall (GPT-4o-mini) is acceptable if:
  - False negatives get caught in a downstream stage
  - The cost savings justify the occasional missed candidate

Quality threshold for finals:
  96% accuracy (Claude) is required if:
  - This is the last step before a decision
  - The cost of a wrong answer >> cost of the API call
```

**Example calculation:**
```
$100M investment decision, Claude at $0.18/analysis:
  Cost of error (approx): $100M × some probability
  Cost of model upgrade: $0.18 → $0.18 (already using best)
  → Quality is paramount, model cost irrelevant

Screening 1000 companies, GPT-mini at $0.008/analysis:
  Total screen cost: $8
  If 5% false negatives at filter stage: miss ~50 companies
  If screening threshold is conservative, these get caught later
  → GPT-mini acceptable
```

### Dynamic RAG vs Static RAG

**The core design choice:**

Static (LangChain default): pre-chunk at ingest time, retrieve top-k chunks at query time.

Dynamic (this system): read full document, let LLM extract structure, cache the result.

**Trade-off:**
```
Static RAG:
  ✅ Scales to millions of documents (pre-indexed)
  ✅ Fast retrieval (vector similarity search)
  ❌ Loses relationships that span chunk boundaries
  ❌ No source attribution at line level

Dynamic RAG:
  ✅ Preserves semantic completeness
  ✅ Line-level attribution possible
  ❌ Higher per-document cost (read full doc each time)
  ❌ Doesn't scale beyond ~1000 docs without optimization
```

**For hedge fund use case:** corpus is small (<1000 filings per analyst), quality is critical, and the cost-per-document is justified. Dynamic RAG is the right choice here.

---

## Example Scenarios (Calculated, Not Measured)

### Scenario A: 50 Biotech Companies, FDA Pipeline Screen

```
Assumptions:
- Each 10-K: ~100 pages, ~50k tokens
- 3 minutes per analysis (sequential)
- GPT-4o-mini for filter, Claude for finals

Without optimization:
  Cost: 50 × $0.158 = $7.90
  Time: 50 × 3 min = 150 min

Two-stage with parallelism:
  Filter (GPT-mini, 10 concurrent): $0.40, ~15 min
  Deep-dive (Claude, 5 concurrent on top 10): $1.58, ~6 min
  Total: ~$1.98, ~21 min

Estimated savings:
  Cost: ~75% ($7.90 → $1.98)
  Time: ~86% (150 min → 21 min)
```

These numbers depend heavily on the assumed token counts and parallelism. Real numbers will differ.

---

### Scenario B: Real-Time Dashboard, 20 Companies

```
Assumptions:
- 80% of queries hit L1 cache (same session)
- 15% hit Redis L2 (team cache, prev sessions)
- 5% need LLM call (truly new queries)
- LLM calls use GPT-4o-mini (200ms) not Claude (800ms)

Weighted latency:
  0.80 × 0ms + 0.15 × 5ms + 0.05 × 200ms
  = 0 + 0.75 + 10
  = ~11ms average

vs naive (always LLM, Claude):
  2000ms average

Theoretical speedup: ~180x
```

Reality check: the 80% L1 hit rate only holds if users repeatedly query the same companies. First session of the day sees 0% L1 hits and depends entirely on L2 and L3.

---

## What Would Make These Numbers Real

To validate these estimates against actual workloads:

1. **Download 10 real 10-Ks from SEC EDGAR** (freely available, no account needed)
2. **Process them through `HedgeFundRAG.analyze_10k()`** and record actual token counts from API responses
3. **Compare extracted financials against XBRL data** (machine-readable structured data that SEC mandates alongside 10-Ks — exact ground truth)
4. **Measure actual LLM latency** from the `BenchmarkResult.latency_ms` field
5. **Count cache hits/misses** by logging `FileStateCache.get()` calls

This would replace all the "estimated" numbers in this document with measured ones, and give the `ModelBenchmark` framework real data to report on.