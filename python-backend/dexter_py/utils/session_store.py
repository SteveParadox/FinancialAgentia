"""Session store for managing user conversation histories across requests.

Supports in-memory storage with optional Redis backend for distributed systems.
Each session is identified by a unique session ID (UUID) and stores MessageHistory objects.
"""

from typing import Optional, Dict, Any
import threading
import time
import json
import os
from datetime import datetime
from ..utils.message_history import MessageHistory


class InMemorySessionStore:
    """Thread-safe in-memory session store for conversation histories.
    
    Perfect for single-instance deployments. For distributed systems,
    use RedisSessionStore instead.
    """
    
    def __init__(self, default_expiry: int = 86400) -> None:
        """Initialize in-memory session store.
        
        Args:
            default_expiry: Session expiry time in seconds (default: 24 hours)
        """
        self._store: Dict[str, MessageHistory] = {}
        self._lock = threading.RLock()
        if default_expiry <= 0:
            raise ValueError("default_expiry must be positive")
        self.default_expiry = default_expiry
        self._expires_at: Dict[str, float] = {}
    
    def get(self, session_id: str, default: Optional[MessageHistory] = None) -> MessageHistory:
        """Retrieve a session's message history.
        
        Args:
            session_id: Unique session identifier (UUID)
            default: Default MessageHistory if session not found
            
        Returns:
            MessageHistory object for the session, or default if not found
        """
        with self._lock:
            expires_at = self._expires_at.get(session_id)
            if expires_at is not None and expires_at <= time.monotonic():
                self._store.pop(session_id, None)
                self._expires_at.pop(session_id, None)
            stored = self._store.get(session_id)
            return stored if stored is not None else (default if default is not None else MessageHistory())
    
    def set(self, session_id: str, history: MessageHistory) -> None:
        """Store or update a session's message history.
        
        Args:
            session_id: Unique session identifier (UUID)
            history: MessageHistory object to store
        """
        with self._lock:
            self._store[session_id] = history
            self._expires_at[session_id] = time.monotonic() + self.default_expiry
    
    def delete(self, session_id: str) -> None:
        """Delete a session's message history.
        
        Args:
            session_id: Unique session identifier (UUID)
        """
        with self._lock:
            self._store.pop(session_id, None)
            self._expires_at.pop(session_id, None)
    
    def exists(self, session_id: str) -> bool:
        """Check if a session exists.
        
        Args:
            session_id: Unique session identifier (UUID)
            
        Returns:
            True if session exists, False otherwise
        """
        with self._lock:
            expires_at = self._expires_at.get(session_id)
            if expires_at is not None and expires_at <= time.monotonic():
                self._store.pop(session_id, None)
                self._expires_at.pop(session_id, None)
                return False
            return session_id in self._store
    
    def clear_all(self) -> None:
        """Clear all sessions from store."""
        with self._lock:
            self._store.clear()
            self._expires_at.clear()
    
    def __repr__(self) -> str:
        with self._lock:
            return f"<InMemorySessionStore sessions={len(self._store)}>"


class RedisSessionStore:
    """Redis-backed session store for distributed/scalable deployments.
    
    Sessions are stored as JSON in Redis with optional TTL (Time-To-Live).
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 86400,
    ) -> None:
        """Initialize Redis session store.
        
        Args:
            redis_url: Redis connection URL
            default_ttl: Session TTL in seconds (default: 24 hours)
            
        Raises:
            ImportError: If redis module not installed
        """
        try:
            import redis
        except ImportError:
            raise ImportError("redis package required for RedisSessionStore. Install with: pip install redis")
        
        self.redis_url = redis_url
        self.default_ttl = default_ttl
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.client.ping()  # Required configured store must be reachable at startup.
        self._prefix = "session:"
    
    def get(self, session_id: str, default: Optional[MessageHistory] = None) -> MessageHistory:
        """Retrieve a session's message history from Redis.
        
        Args:
            session_id: Unique session identifier (UUID)
            default: Default MessageHistory if session not found
            
        Returns:
            MessageHistory object for the session, or default if not found
        """
        try:
            key = f"{self._prefix}{session_id}"
            data = self.client.get(key)
            if data:
                # Reconstruct MessageHistory from JSON
                import json
                from ..utils.message_history import Message
                
                obj = json.loads(data)
                history = MessageHistory(model=obj.get("model"))
                
                # Restore messages
                for msg_data in obj.get("messages", []):
                    record = dict(msg_data)
                    record["timestamp"] = datetime.fromisoformat(record["timestamp"]) if record.get("timestamp") else datetime.now()
                    history._messages.append(Message(**record))
                    history._next_id = max(history._next_id, msg_data["id"] + 1)
                
                return history
            return default or MessageHistory()
        except Exception as e:
            raise RuntimeError(f"Redis session read failed: {session_id}") from e
    
    def set(self, session_id: str, history: MessageHistory) -> None:
        """Store or update a session's message history in Redis.
        
        Args:
            session_id: Unique session identifier (UUID)
            history: MessageHistory object to store
        """
        try:
            import json
            
            key = f"{self._prefix}{session_id}"
            data = {
                "model": history._model,
                "messages": [
                    {
                        "id": msg.id,
                        "query": msg.query,
                        "answer": msg.answer,
                        "summary": msg.summary,
                        "timestamp": msg.timestamp.isoformat(),
                        "embedding_hash": msg.embedding_hash,
                    }
                    for msg in history._messages
                ],
            }
            self.client.setex(key, self.default_ttl, json.dumps(data))
        except Exception as e:
            raise RuntimeError(f"Redis session write failed: {session_id}") from e
    
    def delete(self, session_id: str) -> None:
        """Delete a session from Redis.
        
        Args:
            session_id: Unique session identifier (UUID)
        """
        try:
            key = f"{self._prefix}{session_id}"
            self.client.delete(key)
        except Exception as e:
            raise RuntimeError(f"Redis session delete failed: {session_id}") from e
    
    def exists(self, session_id: str) -> bool:
        """Check if a session exists in Redis.
        
        Args:
            session_id: Unique session identifier (UUID)
            
        Returns:
            True if session exists, False otherwise
        """
        try:
            key = f"{self._prefix}{session_id}"
            return bool(self.client.exists(key))
        except Exception as e:
            raise RuntimeError(f"Redis session lookup failed: {session_id}") from e
    
    def __repr__(self) -> str:
        return f"<RedisSessionStore url={self.redis_url}>"


def get_session_store() -> InMemorySessionStore | RedisSessionStore:
    """Factory function to get appropriate session store based on environment.
    
    Uses Redis if REDIS_URL env var is set, otherwise uses in-memory store.
    
    Returns:
        RedisSessionStore if REDIS_URL configured, else InMemorySessionStore
    """
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return RedisSessionStore(redis_url=redis_url)
    return InMemorySessionStore()
