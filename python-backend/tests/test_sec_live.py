"""Explicit opt-in SEC smoke; never pretends fixture validation is live access."""

import asyncio
import os
import pytest

from dexter_py.tools.sec_edgar import SecEdgarClient
from dexter_py.agent.financial_evidence import normalize_tool_evidence


def test_live_sec_configured_issuer():
    if (
        os.getenv("RUN_LIVE_SEC") != "1"
        or not os.getenv("SEC_USER_AGENT")
        or not os.getenv("SEC_COMPANIES")
    ):
        pytest.skip("Live SEC contact/mapping/explicit opt-in missing; NOT VERIFIED")
    import json

    async def scenario():
        provider = SecEdgarClient(
            os.environ["SEC_USER_AGENT"], json.loads(os.environ["SEC_COMPANIES"])
        )
        try:
            ticker = next(iter(provider.companies))
            result = await provider.facts(ticker)
            sources, evidence = normalize_tool_evidence(
                [{"tool_results": [{"result": result}]}]
            )
            assert (
                sources and evidence and all(row.symbol == ticker for row in evidence)
            )
        finally:
            await provider.close()

    asyncio.run(scenario())
