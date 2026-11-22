"""Own a disposable process/store for a real restart recovery check.

NEO4J_TEST_HOME must point to an unpacked Neo4j distribution. No production
server is stopped, and all database directories are newly created under tmp_path.
"""

import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from uuid import uuid4
import pytest
from dexter_py.graph.config import GraphConfig
from dexter_py.graph.models import GraphEntity, GraphScope
from dexter_py.graph.migrations import migrate
from dexter_py.graph.repository import GraphRepository


@pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_HOME") or os.getenv("GRAPH_TEST_DISPOSABLE") != "yes",
    reason="Opt-in local disposable Neo4j distribution required",
)
def test_database_restart_preserves_committed_records(tmp_path):
    home = Path(os.environ["NEO4J_TEST_HOME"]).resolve()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "conf"
    config.mkdir()
    (config / "neo4j.conf").write_text(
        "\n".join(
            [
                "server.default_listen_address=127.0.0.1",
                f"server.bolt.listen_address=127.0.0.1:{port}",
                "server.http.enabled=false",
                "server.https.enabled=false",
                "server.memory.heap.initial_size=256m",
                "server.memory.heap.max_size=512m",
                "server.memory.pagecache.size=128m",
                "dbms.security.auth_enabled=false",
                f"server.directories.data={tmp_path}/data",
                f"server.directories.logs={tmp_path}/logs",
                f"server.directories.run={tmp_path}/run",
            ]
        )
    )
    env = {**os.environ, "NEO4J_CONF": str(config)}
    scope = GraphScope(kind="principal", identity="restart-" + uuid4().hex)
    entity = GraphEntity(
        scope=scope, entity_type="Company", canonical_name="Synthetic restart fixture"
    )
    settings = GraphConfig(
        enabled=True, uri=f"bolt://127.0.0.1:{port}", password="test-runtime", timeout=5
    )

    def start(log):
        process = subprocess.Popen(
            [str(home / "bin" / "neo4j"), "console"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(120):
            if process.poll() is not None:
                raise AssertionError(
                    "Disposable Neo4j exited; inspect restart-server.log"
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    return process
            except OSError:
                time.sleep(0.5)
        stop(process)
        raise AssertionError("Disposable Neo4j startup timeout")

    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)

    async def write():
        repo = await GraphRepository.connect(settings)
        try:
            await migrate(repo)
            await repo.upsert_entity(scope, entity)
        finally:
            await repo.close()

    async def read():
        repo = await GraphRepository.connect(settings)
        try:
            await migrate(repo)
            assert await repo.get_entity(scope, entity.id) == entity
        finally:
            await repo.close()

    with (tmp_path / "restart-server.log").open("w") as log:
        process = start(log)
        try:
            asyncio.run(write())
        finally:
            stop(process)
        process = start(log)
        try:
            asyncio.run(read())
        finally:
            stop(process)
