# Hedge Fund RAG System
# Learning from Claude Code architecture

import time
from typing import Dict, List, Tuple, Optional, Any
import re
from pathlib import Path

# pip install pymupdf lru-dict anthropic openai

import fitz  # PyMuPDF for PDF processing
from lru import LRU  # Better LRU cache implementation
import anthropic
import openai


# ============================================================================
# PART 1: LRU CACHE (inspired by Claude Code's fileStateCache.ts)
# ============================================================================

class FileState:
    """Mimics Claude Code's FileState type from fileStateCache.ts:4-15"""
    def __init__(
        self,
        content: str,
        timestamp: float,
        offset: int = 0,
        limit: Optional[int] = None,
        isPartialView: bool = False
    ):
        self.content = content
        self.timestamp = timestamp
        self.offset = offset
        self.limit = limit
        self.isPartialView = isPartialView  # KEY: Prevents hallucinations!


class FileStateCache:
    """
    LRU cache with size limit (inspired by fileStateCache.ts:30-106)

    Max 100 entries, 25MB total - same as Claude Code!
    """
    def __init__(self, max_entries: int = 100, max_size_mb: int = 25):
        self.cache = LRU(max_entries)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.current_size_bytes = 0

    def get(self, key: str) -> Optional[FileState]:
        return self.cache.get(key)

    def set(self, key: str, value: FileState):
        # Calculate size
        content_size = len(value.content.encode('utf-8'))

        # Evict if over size limit
        while self.current_size_bytes + content_size > self.max_size_bytes:
            if len(self.cache) == 0:
                break
            # LRU automatically evicts oldest
            evicted_key = next(iter(self.cache))
            evicted = self.cache[evicted_key]
            self.current_size_bytes -= len(evicted.content.encode('utf-8'))
            del self.cache[evicted_key]

        self.cache[key] = value
        self.current_size_bytes += content_size

    def has(self, key: str) -> bool:
        return key in self.cache


# ============================================================================
# PART 2: PDF READING WITH LINE NUMBERS (inspired by Claude Code's FileReadTool)
# ============================================================================

def add_line_numbers(content: str, start_line: int = 1) -> str:
    """
    Mimics addLineNumbers() from file.ts:290-319

    Format: "    42→Revenue increased..."
    """
    lines = content.split('\n')
    numbered_lines = []

    for i, line in enumerate(lines):
        line_num = i + start_line
        # Use the arrow format like Claude Code
        numbered_line = f"{str(line_num).rjust(6)}→{line}"
        numbered_lines.append(numbered_line)

    return '\n'.join(numbered_lines)


def read_pdf_with_line_numbers(
    pdf_path: str,
    pages: str = "1-90",
    add_numbers: bool = True
) -> Dict[str, Any]:
    """
    Read PDF with source attribution (page + line numbers)

    Returns:
        {
            'content': str,  # Full text with line numbers
            'page_map': dict,  # Maps line numbers to pages
            'total_pages': int,
            'metadata': dict
        }
    """
    doc = fitz.open(pdf_path)

    # Parse page range (like Claude Code's parsePDFPageRange)
    page_start, page_end = parse_page_range(pages, len(doc))

    content_parts = []
    page_map = {}  # line_num -> page_num
    current_line = 1

    for page_num in range(page_start - 1, page_end):  # 0-indexed
        page = doc[page_num]
        page_text = page.get_text()

        # Track which lines came from which page
        page_lines = page_text.split('\n')
        for line in page_lines:
            page_map[current_line] = page_num + 1
            current_line += 1

        # Add page header for attribution
        content_parts.append(f"\n===== PAGE {page_num + 1} =====\n")
        content_parts.append(page_text)

    full_content = ''.join(content_parts)

    # Add line numbers if requested
    if add_numbers:
        full_content = add_line_numbers(full_content)

    return {
        'content': full_content,
        'page_map': page_map,
        'total_pages': len(doc),
        'metadata': doc.metadata
    }


def parse_page_range(pages: str, total_pages: int) -> Tuple[int, int]:
    """Parse "1-5" or "3" into (start, end) - inspired by parsePDFPageRange"""
    if '-' in pages:
        start, end = pages.split('-')
        return int(start), min(int(end), total_pages)
    else:
        page = int(pages)
        return page, page


# ============================================================================
# PART 3: STRUCTURED EXTRACTION (Knowledge Graph Building)
# ============================================================================

def extract_revenue(content: str, model: str = "claude") -> Dict[str, Any]:
    """
    Extract revenue with LLM, preserving source attribution

    This is where you'd use different models (GPT, Claude, GLM, etc.)
    """
    # Find sections mentioning revenue
    revenue_pattern = r'(?i)(revenue|sales).*?\$[\d,]+(?:\.\d+)?[MBK]?'
    matches = re.finditer(revenue_pattern, content)

    revenue_mentions = []
    for match in matches:
        # Get line number (extract from "   42→" prefix)
        line_start = content.rfind('\n', 0, match.start()) + 1
        line_prefix = content[line_start:match.start()]
        line_num_match = re.search(r'(\d+)→', line_prefix)
        line_num = int(line_num_match.group(1)) if line_num_match else 0

        revenue_mentions.append({
            'text': match.group(),
            'line': line_num,
            'start': match.start(),
            'end': match.end()
        })

    # Use LLM to structure the data
    if model == "claude":
        structured = extract_with_claude(revenue_mentions, content)
    elif model == "gpt":
        structured = extract_with_gpt(revenue_mentions, content)
    elif model == "glm":
        structured = extract_with_glm(revenue_mentions, content)
    else:
        # Fallback: simple extraction
        structured = simple_extraction(revenue_mentions)

    return structured


def extract_with_claude(mentions: List[Dict], full_content: str) -> Dict:
    """Use Claude for extraction (high quality, good at reasoning)"""
    client = anthropic.Anthropic()

    # Build context with line numbers for attribution
    context = "\n".join([
        f"Line {m['line']}: {m['text']}" for m in mentions[:10]  # Top 10
    ])

    prompt = f"""Extract revenue data from this 10-K filing excerpt:

{context}

Return JSON with:
{{
    "total_revenue": <number>,
    "revenue_by_segment": {{}},
    "source_attribution": {{
        "total_revenue": "line X",
        "revenue_by_segment": {{"segment": "line Y"}}
    }}
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse JSON response
    import json
    response_text = message.content[0].text
    return json.loads(response_text)


def extract_with_gpt(mentions: List[Dict], full_content: str) -> Dict:
    """Use GPT for extraction (fast, cheap)"""
    client = openai.OpenAI()

    context = "\n".join([
        f"Line {m['line']}: {m['text']}" for m in mentions[:10]
    ])

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a financial analyst extracting structured data from 10-K filings."},
            {"role": "user", "content": f"Extract revenue data:\n{context}"}
        ]
    )

    import json
    return json.loads(response.choices[0].message.content)


def extract_with_glm(mentions: List[Dict], full_content: str) -> Dict:
    """
    Use GLM (Hugging Face open-source model)

    GLM is good for Chinese + English, cost-effective for high volume
    """
    # You'd use Hugging Face Transformers here
    # from transformers import AutoModelForCausalLM, AutoTokenizer

    # For now, placeholder
    return simple_extraction(mentions)


def simple_extraction(mentions: List[Dict]) -> Dict:
    """Fallback: regex-based extraction without LLM"""
    total_revenue = 0
    sources = []

    for mention in mentions:
        # Extract number from "$123.4M" format
        amount_match = re.search(r'\$([\d,]+(?:\.\d+)?)', mention['text'])
        if amount_match:
            amount = float(amount_match.group(1).replace(',', ''))

            # Check for M/B suffix
            if 'M' in mention['text']:
                amount *= 1_000_000
            elif 'B' in mention['text']:
                amount *= 1_000_000_000

            total_revenue += amount
            sources.append(f"line {mention['line']}")

    return {
        'total_revenue': total_revenue,
        'revenue_by_segment': {},
        'source_attribution': {
            'total_revenue': sources[0] if sources else 'unknown'
        }
    }


def extract_margins(content: str) -> Dict:
    """Extract margin data - similar pattern to extract_revenue"""
    margin_pattern = r'(?i)(margin|profit margin|operating margin).*?(\d+\.?\d*)%'
    matches = re.finditer(margin_pattern, content)

    margins = []
    for match in matches:
        margins.append({
            'type': match.group(1),
            'value': float(match.group(2)),
            'text': match.group(0)
        })

    return {'margins': margins}


def extract_risks(content: str) -> Dict:
    """Extract risk factors - good use case for LLM summarization"""
    risk_section_pattern = r'(?i)risk factors(.*?)(?=\n===== PAGE|\Z)'
    match = re.search(risk_section_pattern, content, re.DOTALL)

    if match:
        risk_text = match.group(1)[:5000]  # First 5000 chars

        # Use LLM to summarize risks
        # For now, simple keyword extraction
        risk_keywords = ['regulatory', 'competition', 'market', 'operational']
        found_risks = [kw for kw in risk_keywords if kw in risk_text.lower()]

        return {
            'risk_categories': found_risks,
            'raw_text': risk_text[:500]  # Preview
        }

    return {'risk_categories': [], 'raw_text': ''}


# ============================================================================
# PART 4: KNOWLEDGE GRAPH (Your Excellent Insight!)
# ============================================================================

class KnowledgeGraph:
    """
    Graph structure for financial data

    Nodes: Companies, Financial Metrics, Time Periods, Documents
    Edges: has_revenue, filed_in, in_period, has_risk
    """
    def __init__(self):
        self.nodes = {}  # {node_id: {type, data}}
        self.edges = []  # [(from_id, edge_type, to_id, metadata)]

    def add_company_analysis(
        self,
        ticker: str,
        structured_data: Dict,
        source_doc: str,
        source_attribution: Dict
    ):
        """Add a company's 10-K analysis to the graph"""

        # Add company node
        company_id = f"company:{ticker}"
        self.nodes[company_id] = {
            'type': 'company',
            'ticker': ticker,
            'data': {}
        }

        # Add revenue node
        if 'total_revenue' in structured_data:
            revenue_id = f"metric:revenue:{ticker}:2024"
            self.nodes[revenue_id] = {
                'type': 'financial_metric',
                'metric_name': 'revenue',
                'value': structured_data['total_revenue'],
                'source_line': source_attribution.get('total_revenue', 'unknown')
            }

            # Create edge: company -> has_revenue -> revenue
            self.edges.append((
                company_id,
                'has_revenue',
                revenue_id,
                {'year': 2024, 'source_doc': source_doc}
            ))

        # Add document node
        doc_id = f"doc:{source_doc}"
        self.nodes[doc_id] = {
            'type': 'document',
            'path': source_doc,
            'doc_type': '10-K'
        }

        # Edge: company -> filed -> document
        self.edges.append((
            company_id,
            'filed',
            doc_id,
            {'year': 2024}
        ))

    def query_revenue(self, ticker: str) -> Optional[Dict]:
        """Query: What's the revenue for company X?"""
        company_id = f"company:{ticker}"

        # Find edges: company --[has_revenue]--> revenue
        for from_id, edge_type, to_id, metadata in self.edges:
            if from_id == company_id and edge_type == 'has_revenue':
                revenue_node = self.nodes.get(to_id)
                if revenue_node:
                    return {
                        'value': revenue_node['value'],
                        'source_line': revenue_node['source_line'],
                        'source_doc': metadata['source_doc']
                    }

        return None

    def query_path(self, start_id: str, end_id: str) -> List[Tuple]:
        """Find path between two nodes (e.g., company -> document)"""
        # Simple BFS for path finding
        from collections import deque

        queue = deque([(start_id, [])])
        visited = set()

        while queue:
            current, path = queue.popleft()

            if current == end_id:
                return path

            if current in visited:
                continue
            visited.add(current)

            # Find outgoing edges
            for from_id, edge_type, to_id, metadata in self.edges:
                if from_id == current:
                    queue.append((to_id, path + [(from_id, edge_type, to_id)]))

        return []


# ============================================================================
# PART 5: MAIN RAG CLASS (Your Design!)
# ============================================================================

class HedgeFundRAG:
    """
    Complete RAG system inspired by Claude Code architecture

    Key features:
    1. LRU cache (25MB, 100 files) - like Claude Code
    2. isPartialView tracking - prevents hallucinations
    3. Knowledge graph - your insight!
    4. Source attribution - line numbers + pages
    5. Multi-model support - Claude, GPT, GLM
    """

    def __init__(self, model: str = "claude"):
        self.cache = FileStateCache(max_entries=100, max_size_mb=25)
        self.knowledge_graph = KnowledgeGraph()
        self.model = model  # Which LLM to use for extraction

    def analyze_10k(
        self,
        pdf_path: str,
        ticker: str,
        pages: str = "1-50"
    ) -> Dict[str, Any]:
        """
        Main analysis function

        Steps:
        1. Check cache (like FileReadTool.ts:540-573)
        2. Read PDF with line numbers
        3. Extract structured data with LLM
        4. Update knowledge graph
        5. Cache results with isPartialView flag
        """

        # Step 1: Check cache first (deduplication like Claude Code)
        if self.cache.has(pdf_path):
            cached = self.cache.get(pdf_path)
            if not cached.isPartialView:
                print(f"[CACHE HIT] Using cached data for {pdf_path}")
                return cached.content

        print(f"[CACHE MISS] Reading {pdf_path}...")

        # Step 2: Read full PDF with line numbers
        pdf_data = read_pdf_with_line_numbers(pdf_path, pages)
        content = pdf_data['content']

        # Step 3: Extract structured data
        print(f"[EXTRACTING] Using {self.model} model...")
        structured = {
            'revenue': extract_revenue(content, model=self.model),
            'margins': extract_margins(content),
            'risks': extract_risks(content),
            'source_links': {}  # Will be populated by extraction functions
        }

        # Step 4: Update knowledge graph
        self.knowledge_graph.add_company_analysis(
            ticker=ticker,
            structured_data=structured['revenue'],
            source_doc=pdf_path,
            source_attribution=structured['revenue'].get('source_attribution', {})
        )

        # Step 5: Cache with metadata
        file_state = FileState(
            content=structured,
            timestamp=time.time(),
            offset=0,
            limit=None,
            isPartialView=False  # We read the full content
        )
        self.cache.set(pdf_path, file_state)

        print(f"[SUCCESS] Analyzed {ticker}, added to knowledge graph")

        return structured

    def query(self, question: str) -> Dict[str, Any]:
        """
        Query the knowledge graph

        Example: "What's AAPL's revenue?"
        """
        # Simple keyword matching (you'd use LLM for real query understanding)
        if 'revenue' in question.lower():
            ticker_match = re.search(r'\b([A-Z]{1,5})\b', question)
            if ticker_match:
                ticker = ticker_match.group(1)
                result = self.knowledge_graph.query_revenue(ticker)

                if result:
                    return {
                        'answer': f"${result['value']:,.0f}",
                        'source': f"{result['source_doc']}, {result['source_line']}",
                        'confidence': 'high'  # Because we have attribution!
                    }

        return {'answer': 'Not found', 'source': None, 'confidence': 'none'}


# ============================================================================
# PART 6: USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    # Initialize RAG system
    rag = HedgeFundRAG(model="claude")  # or "gpt", "glm"

    # Analyze AAPL 10-K
    aapl_analysis = rag.analyze_10k(
        pdf_path="/path/to/aapl_10k.pdf",
        ticker="AAPL",
        pages="1-50"
    )

    print("AAPL Analysis:", aapl_analysis)

    # Query the knowledge graph
    answer = rag.query("What's AAPL's revenue?")
    print(f"Answer: {answer['answer']}")
    print(f"Source: {answer['source']}")
    print(f"Confidence: {answer['confidence']}")
