"""Thin wrapper around Groq's API.

Groq speaks the OpenAI API, so we just point the openai client at Groq's URL.
Default model is gpt-oss-20b (fast/cheap); the reasoning agents pass a stronger
one. Needs GROQ_API_KEY (see .env.example).
"""

from __future__ import annotations
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # load .env into the environment

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
    """One-shot chat completion. Low temperature by default — we want boring,
    reproducible output, not flair.

    json_mode=True asks for a JSON object where the model supports it. Still
    parse defensively — it doesn't guarantee valid JSON."""

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
