from typing import Dict, Tuple

# Pricing per million tokens (input_cost, output_cost)
MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    "gemini/gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini/gemini-1.5-pro": (3.50, 10.50),
    "gemini-1.5-pro": (3.50, 10.50),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "gpt-4o": (2.50, 10.00),
    "groq/llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llm-stub": (0.0, 0.0),
}

DEFAULT_PRICING = (0.10, 0.30)


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate USD cost for a given model and token usage."""
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    input_price_per_m, output_price_per_m = pricing
    cost = (prompt_tokens / 1_000_000.0) * input_price_per_m + (completion_tokens / 1_000_000.0) * output_price_per_m
    return round(cost, 7)
