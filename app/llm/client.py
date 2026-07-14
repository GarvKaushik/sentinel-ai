"""
Thin wrapper around Groq's API.

Groq exposes an OpenAI-compatible endpoint, so we just point the
standard `openai` client at Groq's base_url instead of pulling in a
separate SDK. Uses openai/gpt-oss-20b by default — fast and cheap,
appropriate for the Correlator agent's summarization role (NOT for
root-cause reasoning or the Critic's falsification pass, which deserve
a stronger model — swap the default when you build those agents).

Requires GROQ_API_KEY in your environment (.env file, see .env.example).
"""

from __future__ import annotations
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # reads .env into os.environ — this was missing before

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-20b"


def get_groq_client() -> OpenAI:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Copy .env.example to .env and add your key, "
            "or export GROQ_API_KEY directly in your shell."
        )
    return OpenAI(base_url=GROQ_BASE_URL, api_key=api_key)


def chat(
    prompt: str,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    json_mode: bool = False,
) -> str:
    """One-shot chat completion. Low temperature by default — this is an
    incident investigation tool, not a creative writing one; you want
    consistent, boring, reproducible output over flair.

    json_mode=True requests JSON-object output where the model supports
    it (Groq's OpenAI-compatible endpoint honors response_format for
    most chat models). Always still validate/parse defensively — never
    trust that json_mode alone guarantees well-formed output."""

    client = get_groq_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        **kwargs,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    # Manual smoke test — requires a real GROQ_API_KEY in your environment.
    result = chat(
        prompt="In one sentence, what causes a NullPointerException?",
        system="You are a terse backend engineering assistant.",
    )
    print("LLM response:", result)
