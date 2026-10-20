"""One non-customer API request; only pass/fail is printed."""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from .model import configured_model_name


async def main():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        async with AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"],
                               base_url=os.getenv("OPENAI_BASE_URL"), timeout=15, max_retries=0) as client:
            result = await client.responses.create(model=configured_model_name(),
                input="Reply OK.", max_output_tokens=32, store=False)
            passed = bool(result.output_text.strip())
    except Exception:
        passed = False
    print("API authentication and model access: " + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
