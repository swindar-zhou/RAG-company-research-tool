# Fork Agents Explained - Simple Mental Model

## The Problem Fork Agents Solve

**Scenario**: You're analyzing 10 tech companies. Each analysis follows the same pattern:
1. Read 10-K
2. Extract revenue, margins, growth
3. Summarize risks
4. Compare to industry average

**Without fork agents:**
```
Parent Agent (full context):
  "Analyze AAPL" → API call with full system prompt → $$$
  "Analyze MSFT" → API call with full system prompt → $$$
  "Analyze GOOGL" → API call with full system prompt → $$$
  ...
  Total cost: 10 × (system prompt + conversation) tokens
```

**With fork agents:**
```
Parent Agent (sets up context):
  System prompt: "You're a hedge fund analyst..."
  Tools: [FileRead, Grep, ...]

  Spawn Child 1 (inherits parent's system prompt): ← CACHE HIT!
    "Analyze AAPL" → Uses parent's cached prompt → $

  Spawn Child 2 (inherits parent's system prompt): ← CACHE HIT!
    "Analyze MSFT" → Uses parent's cached prompt → $

  Spawn Child 3 (inherits parent's system prompt): ← CACHE HIT!
    "Analyze GOOGL" → Uses parent's cached prompt → $

  Total cost: 1 × system prompt + 10 × (small analysis) tokens
```

**Savings**: ~70-90% cost reduction on repetitive tasks!

---

## How It Works Technically

### Parent Agent Sets Up "Cache-Safe Parameters"

From `src/utils/forkedAgent.ts`:
```typescript
export type CacheSafeParams = {
  systemPrompt: SystemPrompt,         // The big expensive prompt
  userContext: { [k: string]: string },
  systemContext: { [k: string]: string },
  toolUseContext: ToolUseContext,     // Tools, model config
  forkContextMessages: Message[],     // Message history prefix
}
```

**The key**: Parent renders system prompt **once**. All children get the **byte-exact copy**.

### Why "Byte-Exact" Matters

Claude API uses **prompt caching**: If the beginning of your prompt matches a previous API call, you don't pay for those tokens again.

**Example**:
```
Call 1: "You are a hedge fund analyst [2000 tokens]... Analyze AAPL [50 tokens]"
        ↓
        API caches first 2000 tokens

Call 2: "You are a hedge fund analyst [2000 tokens]... Analyze MSFT [50 tokens]"
        ↓
        API sees "identical first 2000 tokens" → CACHE HIT!
        You only pay for the 50 new tokens!
```

**Fork agents guarantee byte-exact match** by having children inherit parent's **already-rendered** prompt instead of re-rendering (which might change due to GrowthBook flags, timestamps, etc.)

---

## Visual Diagram

```
┌─────────────────────────────────────────────┐
│ Parent Agent                                │
│ - System Prompt: "You're a hedge fund..."  │ ← Rendered once
│ - Tools: [FileRead, Grep, Bash]            │
│ - Model: claude-sonnet-4                   │
│ - Messages: [User: "Analyze tech stocks"]  │
└─────────────────────────────────────────────┘
                    │
                    ├── Fork Child 1 (AAPL)
                    │   ↓
                    │   Inherits: EXACT system prompt (byte-for-byte)
                    │   Inherits: Same tools
                    │   Inherits: Same model
                    │   Inherits: Parent's message prefix
                    │   NEW: "Analyze AAPL"
                    │
                    │   API sees cached prompt → $0.10 instead of $2.00!
                    │
                    ├── Fork Child 2 (MSFT)
                    │   ↓
                    │   Inherits: EXACT system prompt (byte-for-byte)
                    │   NEW: "Analyze MSFT"
                    │
                    │   API sees cached prompt → $0.10 instead of $2.00!
                    │
                    └── Fork Child 3 (GOOGL)
                        ↓
                        Inherits: EXACT system prompt (byte-for-byte)
                        NEW: "Analyze GOOGL"

                        API sees cached prompt → $0.10 instead of $2.00!
```

---

## Code Walkthrough

Let's trace through `src/utils/forkedAgent.ts`:

### Step 1: Parent Saves Cache-Safe Params

After each turn, parent agent saves:
```typescript
const cacheSafeParams: CacheSafeParams = {
  systemPrompt: currentSystemPrompt,  // Already rendered!
  userContext: currentUserContext,
  systemContext: currentSystemContext,
  toolUseContext: {
    tools: currentTools,
    model: currentModel,
    thinkingConfig: currentThinkingConfig,
  },
  forkContextMessages: messageHistory,
}
```

### Step 2: Spawn Fork Child

When spawning a child:
```typescript
function spawnForkChild(
  taskPrompt: string,
  cacheSafeParams: CacheSafeParams
): Agent {
  return new Agent({
    // Use parent's EXACT prompt (not re-rendered)
    systemPrompt: cacheSafeParams.systemPrompt,

    // Use parent's EXACT tools
    tools: cacheSafeParams.toolUseContext.tools,

    // Use parent's EXACT model
    model: cacheSafeParams.toolUseContext.model,

    // Start with parent's message history
    initialMessages: [
      ...cacheSafeParams.forkContextMessages,
      { role: 'user', content: taskPrompt }  // NEW task
    ],

    // Clone file cache (isolation)
    readFileCache: cloneFileStateCache(parentCache),
  })
}
```

### Step 3: API Call Gets Cache Hit

When child calls Claude API:
```
Request to API:
  System: "You are a hedge fund analyst..." [2000 tokens] ← CACHED!
  Messages: [
    {user: "Previous context..."},  ← CACHED!
    {assistant: "..."},             ← CACHED!
    {user: "Analyze AAPL"}          ← NEW (only these 50 tokens charged)
  ]

Cache hit rate: ~97%
Cost: $0.10 instead of $2.00
```

---

## When to Use Fork Agents

### ✅ Use fork agents when:
1. **Repetitive tasks**: Same analysis on different inputs (10 companies, 20 PDFs)
2. **Parallel work**: Can process independently (no shared state needed)
3. **Same context**: All tasks use the same system prompt/tools
4. **Cost-sensitive**: Working with large system prompts or long conversations

### ❌ Don't use fork agents when:
1. **Sequential dependencies**: Task B needs results from Task A
2. **Shared state**: Need to update parent's context based on child results
3. **One-off tasks**: No repetition to amortize setup cost
4. **Different contexts**: Each task needs different tools/prompts

---

## Hedge Fund Example

**Task**: Analyze 50 biotech companies for FDA approval pipeline risk.

**Without forks** (sequential in parent):
```
Turn 1: Read Company1 10-K → Analyze → $2.00
Turn 2: Read Company2 10-K → Analyze → $2.50 (context grew)
Turn 3: Read Company3 10-K → Analyze → $3.00 (context grew more)
...
Turn 50: Read Company50 10-K → Analyze → $10.00 (huge context!)

Total: ~$250
Context pollution: Each company's details pollute the context
```

**With forks** (parallel):
```
Parent: Set up "biotech analyst" context → $2.00

Fork 1: Analyze Company1 → $0.20 (cache hit on parent's prompt)
Fork 2: Analyze Company2 → $0.20 (cache hit on parent's prompt)
Fork 3: Analyze Company3 → $0.20 (cache hit on parent's prompt)
...
Fork 50: Analyze Company50 → $0.20 (cache hit on parent's prompt)

Parent: Collect all 50 summaries → Synthesize → $5.00

Total: ~$17.00
Savings: 93%!
```

---

## Key Insight

Fork agents are like **hiring 50 junior analysts** who all got the **same training** (system prompt) and work **independently** on different companies, then report back to you.

The "same training" is cached, so you don't pay to train each analyst separately!

---

*For technical deep-dive, read:*
- `src/utils/forkedAgent.ts:1-150` - Cache-safe parameter definition
- `src/tools/AgentTool/runAgent.ts:200-300` - Fork vs non-fork logic
- `src/services/api/claude.ts` - Prompt caching implementation
