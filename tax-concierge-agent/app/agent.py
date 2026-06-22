import os

from google import genai

from tax_concierge_agent.agent import app, root_agent


def gemini_client_from_env() -> genai.Client:
    """Build a Gemini client from local configuration without hardcoded secrets."""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    return genai.Client(api_key=api_key) if api_key else genai.Client()


__all__ = ["app", "gemini_client_from_env", "root_agent"]
