import os

from google import genai
from google.adk.apps import App, ResumabilityConfig

from tax_concierge_agent.agent import root_agent

app = App(
    name="app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)


def gemini_client_from_env() -> genai.Client:
    """Build a Gemini client from local configuration without hardcoded secrets."""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    return genai.Client(api_key=api_key) if api_key else genai.Client()


__all__ = ["app", "gemini_client_from_env", "root_agent"]
