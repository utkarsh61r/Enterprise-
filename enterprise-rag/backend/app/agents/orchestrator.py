"""
Enterprise Knowledge Assistant - LangGraph Multi-Agent System

Implements a multi-agent RAG workflow using LangGraph:
  Planner → Retriever → Writer → FactChecker → Reviewer → Response

Each agent has a specific responsibility, enabling complex multi-step
research tasks that go beyond simple single-turn QA.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

logger = structlog.get_logger(__name__)


# =============================================================================
# Graph State
# =============================================================================

class AgentState(TypedDict):
    """
    Shared state passed between agents in the graph.

    Each agent reads from and writes to this state dict.
    """
    # Input
    query: str
    organization_id: str
    user_role: str
    conversation_history: list[dict]

    # Planning
    sub_queries: list[str]
    plan: str

    # Retrieval
    retrieved_chunks: list[dict]
    context: str

    # Writing
    draft_answer: str

    # Fact checking
    fact_check_result: dict
    is_grounded: bool
    hallucination_issues: list[str]

    # Final output
    final_answer: str
    citations: list[dict]
    suggested_questions: list[str]

    # Metadata
    messages: Annotated[list, add_messages]
    iterations: int
    error: str | None


# =============================================================================
# Individual Agents
# =============================================================================

class PlannerAgent:
    """
    Analyzes the query and creates a research plan.

    Breaks complex questions into sub-queries for targeted retrieval.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def __call__(self, state: AgentState) -> dict:
        """Plan the retrieval strategy."""
        logger.info("Planner agent running", query=state["query"][:100])

        prompt = f"""You are a research planner. Analyze this question and create a retrieval plan.

Question: {state["query"]}

Break this down into 1-3 specific sub-queries that together will answer the full question.
Return a JSON object with:
- "sub_queries": list of specific search queries
- "plan": brief explanation of your approach

Return ONLY valid JSON, no other text."""

        response = await self.llm.generate(prompt, max_tokens=400)

        try:
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            data = json.loads(clean)
            sub_queries = data.get("sub_queries", [state["query"]])
            plan = data.get("plan", "Direct retrieval")
        except (json.JSONDecodeError, KeyError):
            sub_queries = [state["query"]]
            plan = "Direct retrieval"

        return {
            "sub_queries": sub_queries[:3],  # Cap at 3 to control cost
            "plan": plan,
            "messages": [AIMessage(content=f"Plan created: {plan}")],
        }


class RetrieverAgent:
    """
    Executes retrieval for each sub-query and aggregates results.
    """

    def __init__(self, searcher, reranker):
        self.searcher = searcher
        self.reranker = reranker

    async def __call__(self, state: AgentState) -> dict:
        """Retrieve relevant chunks for all sub-queries."""
        logger.info("Retriever agent running", num_queries=len(state["sub_queries"]))

        all_chunks = []
        seen_ids: set[str] = set()

        for sub_query in state["sub_queries"]:
            chunks = await self.searcher.search(
                sub_query,
                state["organization_id"],
                state["user_role"],
            )
            for chunk in chunks:
                if chunk.chunk_id not in seen_ids:
                    all_chunks.append(chunk)
                    seen_ids.add(chunk.chunk_id)

        # Rerank all collected chunks against the original query
        if all_chunks:
            all_chunks = await self.reranker.rerank(state["query"], all_chunks)

        # Serialize chunks for state storage
        chunk_dicts = [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "document_title": c.document_title,
                "content": c.content,
                "page_number": c.page_number,
                "section": c.section,
                "heading": c.heading,
                "score": c.final_score,
            }
            for c in all_chunks
        ]

        # Build context string
        context_parts = []
        for chunk in all_chunks[:5]:  # Top 5 for context
            ref = f"[{chunk.document_title}"
            if chunk.page_number:
                ref += f", p.{chunk.page_number}"
            ref += "]"
            context_parts.append(f"{ref}\n{chunk.content}")
        context = "\n\n---\n\n".join(context_parts)

        return {
            "retrieved_chunks": chunk_dicts,
            "context": context,
            "messages": [AIMessage(content=f"Retrieved {len(chunk_dicts)} relevant chunks.")],
        }


class WriterAgent:
    """
    Generates the initial answer draft from retrieved context.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def __call__(self, state: AgentState) -> dict:
        """Write a draft answer from context."""
        logger.info("Writer agent running")

        if not state.get("context"):
            return {
                "draft_answer": "I couldn't find relevant information in the knowledge base.",
                "messages": [AIMessage(content="No context available for writing.")],
            }

        prompt = f"""You are a technical writer for an enterprise knowledge system.

Write a comprehensive, accurate answer to the question using ONLY the provided context.

Context:
{state["context"]}

Question: {state["query"]}

Requirements:
- Use markdown formatting
- Reference sources with [Document Name, Page X] notation
- Be concise but complete
- If context is insufficient, clearly state what is missing
- Never invent information not in the context

Answer:"""

        draft = await self.llm.generate(prompt, max_tokens=1000)
        return {
            "draft_answer": draft,
            "messages": [AIMessage(content="Draft answer written.")],
        }


class FactCheckerAgent:
    """
    Verifies the draft answer against the retrieved context.

    Detects hallucinations and unsupported claims.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def __call__(self, state: AgentState) -> dict:
        """Check draft answer for factual accuracy."""
        logger.info("Fact checker agent running")

        if not state.get("draft_answer") or not state.get("context"):
            return {
                "fact_check_result": {},
                "is_grounded": True,
                "hallucination_issues": [],
            }

        prompt = f"""You are a fact-checker. Verify the answer against the provided context.

Context:
{state["context"]}

Answer to verify:
{state["draft_answer"]}

Check each claim in the answer. Return a JSON object with:
- "is_grounded": true if all claims are supported by context, false otherwise  
- "issues": list of specific unsupported claims or hallucinations found
- "confidence": float 0-1 representing overall groundedness score

Return ONLY valid JSON."""

        response = await self.llm.generate(prompt, max_tokens=500)

        try:
            clean = response.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            result = json.loads(clean)
            is_grounded = result.get("is_grounded", True)
            issues = result.get("issues", [])
        except (json.JSONDecodeError, KeyError):
            is_grounded = True
            issues = []
            result = {}

        return {
            "fact_check_result": result,
            "is_grounded": is_grounded,
            "hallucination_issues": issues,
            "messages": [AIMessage(
                content=f"Fact check complete. Grounded: {is_grounded}. Issues: {len(issues)}"
            )],
        }


class ReviewerAgent:
    """
    Final review and refinement of the answer.

    Improves clarity, adds missing context, and formats citations.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def __call__(self, state: AgentState) -> dict:
        """Review and finalize the answer."""
        logger.info("Reviewer agent running")

        issues_text = ""
        if state.get("hallucination_issues"):
            issues_text = f"\nKnown issues to fix:\n- " + "\n- ".join(state["hallucination_issues"])

        prompt = f"""You are a senior editor. Review and improve this answer.

Original question: {state["query"]}

Draft answer:
{state.get("draft_answer", "")}
{issues_text}

If there are issues, fix them using ONLY information from the context.
If the answer is already good, return it as-is.
Format the final answer clearly with proper markdown.

Also generate 3 relevant follow-up questions the user might want to ask.

Return a JSON object with:
- "answer": the final polished answer (markdown)
- "suggested_questions": list of 3 follow-up questions

Return ONLY valid JSON."""

        response = await self.llm.generate(prompt, max_tokens=1200)

        try:
            clean = response.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            data = json.loads(clean)
            final_answer = data.get("answer", state.get("draft_answer", ""))
            suggested = data.get("suggested_questions", [])
        except (json.JSONDecodeError, KeyError):
            final_answer = state.get("draft_answer", "Unable to generate answer.")
            suggested = []

        # Build citations from retrieved chunks
        citations = [
            {
                "document_id": c["document_id"],
                "document_title": c["document_title"],
                "page_number": c.get("page_number"),
                "section": c.get("section"),
                "excerpt": c["content"][:300],
                "confidence": round(c.get("score", 0), 3),
            }
            for c in state.get("retrieved_chunks", [])[:5]
        ]

        return {
            "final_answer": final_answer,
            "citations": citations,
            "suggested_questions": suggested[:3],
            "messages": [AIMessage(content="Review complete. Final answer ready.")],
        }


# =============================================================================
# Routing Logic
# =============================================================================

def should_refine(state: AgentState) -> str:
    """
    Decide whether to refine the answer or proceed to review.

    If fact check found issues AND we haven't tried too many times,
    loop back to the writer for refinement.
    """
    iterations = state.get("iterations", 0)
    if not state.get("is_grounded") and iterations < 2:
        return "refine"
    return "review"


# =============================================================================
# Graph Builder
# =============================================================================

def build_rag_graph(llm_client, searcher, reranker) -> StateGraph:
    """
    Build the LangGraph agent workflow.

    Graph: planner → retriever → writer → fact_checker → [refine loop] → reviewer → END
    """
    planner = PlannerAgent(llm_client)
    retriever = RetrieverAgent(searcher, reranker)
    writer = WriterAgent(llm_client)
    fact_checker = FactCheckerAgent(llm_client)
    reviewer = ReviewerAgent(llm_client)

    async def increment_iterations(state: AgentState) -> dict:
        return {"iterations": state.get("iterations", 0) + 1}

    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("planner", planner)
    graph.add_node("retriever", retriever)
    graph.add_node("writer", writer)
    graph.add_node("fact_checker", fact_checker)
    graph.add_node("increment", increment_iterations)
    graph.add_node("reviewer", reviewer)

    # Add edges
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "retriever")
    graph.add_edge("retriever", "writer")
    graph.add_edge("writer", "fact_checker")
    graph.add_edge("fact_checker", "increment")
    graph.add_conditional_edges(
        "increment",
        should_refine,
        {"refine": "writer", "review": "reviewer"},
    )
    graph.add_edge("reviewer", END)

    return graph.compile()


class AgentOrchestrator:
    """
    High-level interface for running the multi-agent RAG workflow.
    """

    def __init__(self, llm_client, searcher, reranker):
        self.graph = build_rag_graph(llm_client, searcher, reranker)

    async def run(
        self,
        query: str,
        organization_id: str,
        user_role: str,
        conversation_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Run the full agent pipeline and return the result."""
        initial_state: AgentState = {
            "query": query,
            "organization_id": organization_id,
            "user_role": user_role,
            "conversation_history": conversation_history or [],
            "sub_queries": [],
            "plan": "",
            "retrieved_chunks": [],
            "context": "",
            "draft_answer": "",
            "fact_check_result": {},
            "is_grounded": True,
            "hallucination_issues": [],
            "final_answer": "",
            "citations": [],
            "suggested_questions": [],
            "messages": [],
            "iterations": 0,
            "error": None,
        }

        try:
            final_state = await self.graph.ainvoke(initial_state)
            return {
                "answer": final_state["final_answer"],
                "citations": final_state["citations"],
                "suggested_questions": final_state["suggested_questions"],
                "plan": final_state["plan"],
                "chunks_retrieved": len(final_state["retrieved_chunks"]),
                "is_grounded": final_state["is_grounded"],
            }
        except Exception as e:
            logger.error("Agent orchestration failed", error=str(e), exc_info=True)
            return {
                "answer": "An error occurred while processing your request. Please try again.",
                "citations": [],
                "suggested_questions": [],
                "error": str(e),
            }
