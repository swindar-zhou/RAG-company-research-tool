# Model Benchmarking Framework
# For evaluating Claude, GPT, GLM, Gemini, NVIDIA models
# Key metrics: Latency, Cost, Quality, Domain Performance

import time
from typing import Dict, List, Callable, Any
from dataclasses import dataclass
import json
from pathlib import Path

import anthropic
import openai
# from transformers import AutoModel, AutoTokenizer  # For GLM
# import google.generativeai as genai  # For Gemini
# import requests  # For NVIDIA NIM API


# ============================================================================
# BENCHMARK METRICS DEFINITION
# ============================================================================

@dataclass
class BenchmarkResult:
    """Results from a single model evaluation"""
    model_name: str
    task_type: str  # "extraction", "summarization", "reasoning"

    # Performance metrics
    latency_ms: float  # How fast?
    cost_usd: float    # How expensive?
    tokens_used: int   # Input + output tokens

    # Quality metrics
    accuracy: float           # 0-1, compared to ground truth
    hallucination_rate: float  # 0-1, percentage of made-up facts
    source_attribution: bool   # Did it cite sources correctly?

    # Domain-specific metrics
    financial_accuracy: float  # For hedge fund use case
    structured_output_valid: bool  # Did it return valid JSON?

    # Metadata
    timestamp: float
    input_size_chars: int
    output_size_chars: int


# ============================================================================
# MODEL WRAPPERS (Unified Interface)
# ============================================================================

class ModelWrapper:
    """Base class for model wrappers - unified interface"""

    def __init__(self, model_name: str):
        self.model_name = model_name

    def generate(self, prompt: str, max_tokens: int = 1024) -> Dict[str, Any]:
        """
        Generate response with timing and token tracking

        Returns:
            {
                'text': str,
                'latency_ms': float,
                'tokens': {'input': int, 'output': int, 'total': int},
                'cost_usd': float
            }
        """
        raise NotImplementedError


class ClaudeWrapper(ModelWrapper):
    """Claude (Sonnet 4.5) wrapper"""

    # Pricing: https://www.anthropic.com/pricing
    PRICING = {
        'claude-sonnet-4-20250514': {
            'input': 3.00 / 1_000_000,   # $3 per 1M input tokens
            'output': 15.00 / 1_000_000,  # $15 per 1M output tokens
            'cache_write': 3.75 / 1_000_000,
            'cache_read': 0.30 / 1_000_000,
        }
    }

    def __init__(self):
        super().__init__("claude-sonnet-4-20250514")
        self.client = anthropic.Anthropic()

    def generate(self, prompt: str, max_tokens: int = 1024) -> Dict[str, Any]:
        start_time = time.time()

        message = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )

        latency_ms = (time.time() - start_time) * 1000

        # Extract token usage
        usage = message.usage
        tokens = {
            'input': usage.input_tokens,
            'output': usage.output_tokens,
            'total': usage.input_tokens + usage.output_tokens
        }

        # Calculate cost
        pricing = self.PRICING[self.model_name]
        cost_usd = (
            tokens['input'] * pricing['input'] +
            tokens['output'] * pricing['output']
        )

        return {
            'text': message.content[0].text,
            'latency_ms': latency_ms,
            'tokens': tokens,
            'cost_usd': cost_usd
        }


class GPTWrapper(ModelWrapper):
    """GPT-4o wrapper"""

    # Pricing: https://openai.com/api/pricing/
    PRICING = {
        'gpt-4o': {
            'input': 2.50 / 1_000_000,   # $2.50 per 1M input tokens
            'output': 10.00 / 1_000_000,  # $10 per 1M output tokens
        },
        'gpt-4o-mini': {
            'input': 0.15 / 1_000_000,
            'output': 0.60 / 1_000_000,
        }
    }

    def __init__(self, model_name: str = "gpt-4o"):
        super().__init__(model_name)
        self.client = openai.OpenAI()

    def generate(self, prompt: str, max_tokens: int = 1024) -> Dict[str, Any]:
        start_time = time.time()

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens
        )

        latency_ms = (time.time() - start_time) * 1000

        usage = response.usage
        tokens = {
            'input': usage.prompt_tokens,
            'output': usage.completion_tokens,
            'total': usage.total_tokens
        }

        pricing = self.PRICING[self.model_name]
        cost_usd = (
            tokens['input'] * pricing['input'] +
            tokens['output'] * pricing['output']
        )

        return {
            'text': response.choices[0].message.content,
            'latency_ms': latency_ms,
            'tokens': tokens,
            'cost_usd': cost_usd
        }


class GLMWrapper(ModelWrapper):
    """
    GLM (Hugging Face) wrapper

    GLM-4 is good for:
    - Chinese + English (bilingual)
    - Cost-effective (self-hosted = $0)
    - High volume processing

    Tradeoffs:
    - Need GPU for inference (NVIDIA T4/A100)
    - Initial setup cost
    - Lower quality than Claude/GPT on complex reasoning
    """

    def __init__(self, model_name: str = "THUDM/glm-4-9b"):
        super().__init__(model_name)
        # Placeholder - you'd load the model with:
        # from transformers import AutoModel, AutoTokenizer
        # self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        # self.model = AutoModel.from_pretrained(model_name)

    def generate(self, prompt: str, max_tokens: int = 1024) -> Dict[str, Any]:
        """
        For self-hosted models:
        - Latency depends on your GPU (T4: ~500ms, A100: ~100ms)
        - Cost is compute cost (AWS p3.2xlarge: ~$3/hour)
        """
        # Placeholder implementation
        return {
            'text': "GLM response (not implemented yet)",
            'latency_ms': 500.0,  # Estimate for T4 GPU
            'tokens': {'input': 100, 'output': 50, 'total': 150},
            'cost_usd': 0.0  # Self-hosted = no API cost
        }


class GeminiWrapper(ModelWrapper):
    """Google Gemini wrapper"""

    PRICING = {
        'gemini-1.5-pro': {
            'input': 1.25 / 1_000_000,   # Cheaper than Claude!
            'output': 5.00 / 1_000_000,
        }
    }

    def __init__(self, model_name: str = "gemini-1.5-pro"):
        super().__init__(model_name)
        # import google.generativeai as genai
        # genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        # self.model = genai.GenerativeModel(model_name)

    def generate(self, prompt: str, max_tokens: int = 1024) -> Dict[str, Any]:
        # Placeholder
        return {
            'text': "Gemini response (not implemented)",
            'latency_ms': 800.0,
            'tokens': {'input': 100, 'output': 50, 'total': 150},
            'cost_usd': 0.0001
        }


# ============================================================================
# BENCHMARK SUITE
# ============================================================================

class ModelBenchmark:
    """
    Comprehensive benchmarking suite

    Tests:
    1. Latency (speed)
    2. Cost (efficiency)
    3. Quality (accuracy, hallucination rate)
    4. Domain performance (financial extraction)
    """

    def __init__(self):
        self.results: List[BenchmarkResult] = []

    def benchmark_extraction_task(
        self,
        model: ModelWrapper,
        test_cases: List[Dict[str, Any]]
    ) -> BenchmarkResult:
        """
        Benchmark structured data extraction

        Test cases should have:
        - input: str (text to extract from)
        - expected: dict (ground truth)
        """
        total_latency = 0
        total_cost = 0
        total_tokens = 0
        correct_extractions = 0
        hallucinations = 0

        for case in test_cases:
            prompt = self._build_extraction_prompt(case['input'])

            # Generate response
            response = model.generate(prompt)

            total_latency += response['latency_ms']
            total_cost += response['cost_usd']
            total_tokens += response['tokens']['total']

            # Evaluate quality
            try:
                extracted = json.loads(response['text'])

                # Check accuracy
                if self._compare_extraction(extracted, case['expected']):
                    correct_extractions += 1

                # Check for hallucinations
                if self._has_hallucination(extracted, case['input']):
                    hallucinations += 1

            except json.JSONDecodeError:
                # Invalid JSON = failed extraction
                pass

        # Calculate metrics
        accuracy = correct_extractions / len(test_cases)
        hallucination_rate = hallucinations / len(test_cases)
        avg_latency = total_latency / len(test_cases)

        return BenchmarkResult(
            model_name=model.model_name,
            task_type="extraction",
            latency_ms=avg_latency,
            cost_usd=total_cost,
            tokens_used=total_tokens,
            accuracy=accuracy,
            hallucination_rate=hallucination_rate,
            source_attribution=True,  # Would check this separately
            financial_accuracy=accuracy,  # For financial extraction
            structured_output_valid=True,
            timestamp=time.time(),
            input_size_chars=sum(len(c['input']) for c in test_cases),
            output_size_chars=0  # Would track from responses
        )

    def _build_extraction_prompt(self, text: str) -> str:
        """Build prompt for extraction task"""
        return f"""Extract financial metrics from this text:

{text}

Return JSON with:
{{
    "revenue": <number or null>,
    "margin": <number or null>,
    "year": <number or null>,
    "source_line": <string>
}}"""

    def _compare_extraction(self, extracted: Dict, expected: Dict) -> bool:
        """Check if extraction matches expected (with tolerance)"""
        for key, expected_value in expected.items():
            if key not in extracted:
                return False

            actual_value = extracted[key]

            # For numbers, allow 5% tolerance
            if isinstance(expected_value, (int, float)):
                if abs(actual_value - expected_value) / expected_value > 0.05:
                    return False
            else:
                if actual_value != expected_value:
                    return False

        return True

    def _has_hallucination(self, extracted: Dict, source_text: str) -> bool:
        """
        Check if extraction contains facts not in source

        Simple version: check if all numbers appear in source
        """
        for key, value in extracted.items():
            if isinstance(value, (int, float)):
                # Check if this number appears in source
                if str(value) not in source_text and str(int(value)) not in source_text:
                    return True  # Hallucinated number!

        return False

    def run_comprehensive_benchmark(
        self,
        models: List[ModelWrapper],
        test_suite: Dict[str, List[Dict]]
    ) -> Dict[str, List[BenchmarkResult]]:
        """
        Run full benchmark across all models and tasks

        test_suite = {
            'extraction': [...],
            'summarization': [...],
            'reasoning': [...]
        }
        """
        results = {}

        for model in models:
            print(f"\n{'='*60}")
            print(f"Benchmarking: {model.model_name}")
            print(f"{'='*60}")

            model_results = []

            # Extraction tasks
            if 'extraction' in test_suite:
                print(f"  [1/3] Testing extraction...")
                result = self.benchmark_extraction_task(
                    model,
                    test_suite['extraction']
                )
                model_results.append(result)
                print(f"    ✓ Accuracy: {result.accuracy:.2%}")
                print(f"    ✓ Latency: {result.latency_ms:.0f}ms")
                print(f"    ✓ Cost: ${result.cost_usd:.4f}")

            # Add more task types here (summarization, reasoning, etc.)

            results[model.model_name] = model_results

        return results

    def generate_report(self, results: Dict[str, List[BenchmarkResult]]) -> str:
        """Generate markdown report"""

        report = "# Model Benchmark Report\n\n"
        report += f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # Summary table
        report += "## Summary\n\n"
        report += "| Model | Latency (ms) | Cost ($) | Accuracy | Hallucination Rate |\n"
        report += "|-------|--------------|----------|----------|--------------------|\n"

        for model_name, model_results in results.items():
            # Average across tasks
            avg_latency = sum(r.latency_ms for r in model_results) / len(model_results)
            total_cost = sum(r.cost_usd for r in model_results)
            avg_accuracy = sum(r.accuracy for r in model_results) / len(model_results)
            avg_hallucination = sum(r.hallucination_rate for r in model_results) / len(model_results)

            report += f"| {model_name} | {avg_latency:.0f} | ${total_cost:.4f} | {avg_accuracy:.2%} | {avg_hallucination:.2%} |\n"

        # Detailed results per task
        report += "\n## Detailed Results\n\n"
        for model_name, model_results in results.items():
            report += f"### {model_name}\n\n"
            for result in model_results:
                report += f"**Task: {result.task_type}**\n"
                report += f"- Latency: {result.latency_ms:.0f}ms\n"
                report += f"- Cost: ${result.cost_usd:.4f}\n"
                report += f"- Accuracy: {result.accuracy:.2%}\n"
                report += f"- Hallucination Rate: {result.hallucination_rate:.2%}\n"
                report += f"- Tokens Used: {result.tokens_used:,}\n\n"

        # Recommendations
        report += "\n## Recommendations\n\n"
        report += self._generate_recommendations(results)

        return report

    def _generate_recommendations(self, results: Dict[str, List[BenchmarkResult]]) -> str:
        """Generate recommendations based on results"""

        # Find best model for each metric
        best_latency = min(results.items(), key=lambda x: sum(r.latency_ms for r in x[1]))[0]
        best_cost = min(results.items(), key=lambda x: sum(r.cost_usd for r in x[1]))[0]
        best_accuracy = max(results.items(), key=lambda x: sum(r.accuracy for r in x[1]))[0]

        recs = f"**For hedge fund use case:**\n\n"
        recs += f"- **High-stakes analysis** (earnings reports, investment memos): Use **{best_accuracy}** (highest accuracy)\n"
        recs += f"- **High-volume screening** (50+ companies): Use **{best_cost}** (lowest cost)\n"
        recs += f"- **Real-time analysis** (live earnings calls): Use **{best_latency}** (lowest latency)\n\n"

        recs += "**Quality vs Cost tradeoff:**\n"
        recs += "- Claude: Best quality, highest cost (~3-4x GPT)\n"
        recs += "- GPT-4o: Good balance of quality and cost\n"
        recs += "- GLM (self-hosted): Lowest cost for high volume, requires GPU setup\n"
        recs += "- Gemini: Cheapest API option, good for summarization\n"

        return recs


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    # Define test cases
    test_suite = {
        'extraction': [
            {
                'input': 'Revenue for fiscal year 2023 was $394.3 billion, up 5% from prior year.',
                'expected': {
                    'revenue': 394.3e9,
                    'year': 2023,
                }
            },
            {
                'input': 'Operating margin improved to 28.5% in Q4 2024.',
                'expected': {
                    'margin': 28.5,
                    'year': 2024,
                }
            },
            # Add more test cases...
        ]
    }

    # Initialize models
    models = [
        ClaudeWrapper(),
        GPTWrapper("gpt-4o"),
        GPTWrapper("gpt-4o-mini"),
        # GLMWrapper(),  # Uncomment if you have GLM set up
        # GeminiWrapper(),
    ]

    # Run benchmark
    benchmark = ModelBenchmark()
    results = benchmark.run_comprehensive_benchmark(models, test_suite)

    # Generate report
    report = benchmark.generate_report(results)
    print("\n" + report)

    # Save report
    Path("benchmark_report.md").write_text(report)
    print("\nReport saved to: benchmark_report.md")
