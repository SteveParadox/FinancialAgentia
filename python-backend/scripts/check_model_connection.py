"""Inspect model configuration; --live explicitly sends a short model request."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from dexter_py.model.llm import call_llm, model_configuration_ready, model_provider
from dexter_py.utils._utils import LLMConfig, configure_llm, close_llm_clients


async def main(live=False):
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    model = os.getenv('LLM_MODEL', 'gpt-4o-mini')
    configure_llm(LLMConfig(default_model=model, timeout=30, retry_attempts=1))
    status = {'model': model, 'provider': model_provider(model),
              'configured': model_configuration_ready(model), 'live_verified': False}
    if not live:
        print(json.dumps(status))
        return 0 if status['configured'] else 1
    if not status['configured']:
        print(json.dumps({**status, 'error': 'Provider key or SDK missing'}))
        return 1
    try:
        answer = await call_llm('Reply with OK only.', model=model, max_tokens=4096, temperature=0)
        status['live_verified'] = bool(answer.strip())
        print(json.dumps(status))  # Never print keys, raw provider errors or prompts.
        return 0 if status['live_verified'] else 1
    except Exception as error:
        print(json.dumps({**status, 'error_category': type(error).__name__}))
        return 1
    finally:
        await close_llm_clients()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send one model request; API usage may be billed')
    raise SystemExit(asyncio.run(main(parser.parse_args().live)))
