"""Generate versioned wire schemas. --check fails when generated specs drift."""
import json
from pathlib import Path
import sys

from dexter_py.agent.contracts import AgentExecution
from dexter_py.agent.financial_evidence import ResearchReport

root = Path(__file__).resolve().parents[2] / 'contracts'
for name, model in [('research-report.v1', ResearchReport), ('agent-execution.v1', AgentExecution)]:
    rendered = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + '\n'
    target = root / (name + '.schema.json')
    if '--check' in sys.argv:
        if not target.exists() or target.read_text() != rendered:
            raise SystemExit(f'Contract drift: regenerate {target.name}')
    else:
        root.mkdir(exist_ok=True)
        target.write_text(rendered)
