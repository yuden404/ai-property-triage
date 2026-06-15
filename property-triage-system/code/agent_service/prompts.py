"""Prompts for the LangGraph agent.

TOOL_DESCRIPTIONS is a graded Prompt-Engineering surface: the planner picks tools
from these descriptions, so their precision drives correct routing. Per the
guideline, iterate >=5x and test each version against the same 10 benchmark
queries (logged in docs/prompt_log.md).
"""

# --- Prompt surface: tool descriptions (drive planner routing) ------------- #
TOOL_DESCRIPTIONS = """\
- rag: Searches the agency's knowledge base of past property listings and returns the most
  similar comparable listings plus a short cited market insight. Use it for questions about
  prices, comparables, neighbourhoods, what similar homes offer, or how a property is
  positioned versus the market. Input: a free-text description of the property or the question.
- image: Analyses ONE property photo and returns its room type (kitchen / bathroom / bedroom /
  living room / exterior / other) and a 1-5 condition score with a confidence value. Use it for
  the visual condition of rooms, what renovation a room needs, AND any request to describe,
  characterise, assess, or summarise the property itself whenever a photo is provided — the
  photo is first-hand evidence about the property, so describing the property includes
  describing what the image shows. Requires image URLs; never use it when no images were provided."""

PLANNER_PROMPT = """You are the planner for a real-estate analysis agent. Decide which tools to call to answer the user's question.

Available tools:
{tools}

Images provided with this request: {has_images}
User question: "{query}"

Return ONLY JSON: {{"use_rag": <true|false>, "use_image": <true|false>, "rationale": "<one sentence>"}}.
Set use_image to false if no images were provided. Choose the minimal set of tools that actually answers the question."""

SYNTH_PROMPT = """You are a senior property analyst. Answer the user's question using ONLY the tool results below. Never invent prices, certifications, legal claims, or facts that are not present. If the tools returned nothing relevant, say so plainly.

User question: "{query}"

Tool results:
{findings}

Write a clear, concise answer — a short paragraph or a few bullet points."""
