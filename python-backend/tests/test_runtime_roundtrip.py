"""Real HTTP, Bun transport, Pydantic/Zod contract, persisted report and Ink render."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest


def test_socket_api_bun_client_reconnect_history_and_terminal_render():
    url = os.getenv('TEST_REDIS_URL')
    bun = shutil.which('bun')
    if not url or not bun:
        pytest.skip('Real Redis and Bun required for cross-runtime E2E')
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'REDIS_URL': url, 'BACKEND_ENV': 'development',
           'EXECUTION_REDIS_PREFIX': 'e2e-'+uuid.uuid4().hex+':',
           'BACKEND_API_KEY': 'fixture-e2e-key', 'PYTHONPATH': str(root/'python-backend'),
           'FINANCIALAGENTIA_BACKEND_URL': f'http://127.0.0.1:{port}'}
    for key in ('JWT_SECRET', 'FINANCIAL_DATASETS_API_KEY'):
        env.pop(key, None)
    # File handles avoid a blocked stderr pipe while the worker emits structured logs.
    import tempfile
    with tempfile.TemporaryFile() as logs:
        server = subprocess.Popen([sys.executable, 'tests/fixture_runtime.py', str(port)],
                                  cwd=root/'python-backend', env=env, stdout=logs, stderr=logs)
        try:
            with httpx.Client(trust_env=False) as http:
                for _ in range(150):
                    if server.poll() is not None:
                        logs.seek(0)
                        pytest.fail('Fixture startup failed: '+logs.read().decode())
                    try:
                        if http.get(f'http://127.0.0.1:{port}/health', timeout=0.2).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail('Fixture server did not start')
            result = subprocess.run([bun, 'run', 'tests/cli-roundtrip.tsx'], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stdout+result.stderr
            assert 'AAPL price: 100' in result.stdout
            assert json.loads(result.stdout.strip().splitlines()[-1])['report_rendered'] is True
        except Exception:
            logs.seek(0)
            print(logs.read().decode())
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            from redis import Redis
            redis = Redis.from_url(url)
            keys = list(redis.scan_iter(match=env['EXECUTION_REDIS_PREFIX']+'*'))
            if keys:
                redis.delete(*keys)
            redis.close()
