"""Environment-only credentials; graph remains optional."""

from __future__ import annotations
import os
from pydantic import Field, SecretStr, model_validator
from .models import Strict


class GraphConfig(Strict):
    enabled: bool = False
    uri: str = "bolt://neo4j:7687"
    username: str = "neo4j"
    password: SecretStr = SecretStr("")
    database: str = "neo4j"
    pool_size: int = Field(default=10, ge=1, le=50)
    timeout: float = Field(default=10, ge=1, le=60)

    @model_validator(mode="after")
    def valid(self) -> GraphConfig:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.uri)
        if (
            parsed.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "Neo4j URI must use supported transport without embedded credentials"
            )
        if self.enabled and (
            not self.password.get_secret_value()
            or not self.username
            or not self.database
        ):
            raise ValueError("Graph enabled requires Neo4j credentials and database")
        return self

    @classmethod
    def from_env(cls) -> GraphConfig:
        return cls.model_validate(
            {
                "enabled": os.getenv("GRAPH_ENABLED", "false"),
                "uri": os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
                "username": os.getenv("NEO4J_USERNAME", "neo4j"),
                "password": os.getenv("NEO4J_PASSWORD", ""),
                "database": os.getenv("NEO4J_DATABASE", "neo4j"),
                "pool_size": os.getenv("NEO4J_POOL_SIZE", "10"),
                "timeout": os.getenv("NEO4J_TIMEOUT_SECONDS", "10"),
            }
        )
