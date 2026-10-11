# schemas.py
from typing import List, Optional, Dict, Any, Literal, Union
from pydantic import BaseModel, Field, validator
from datetime import datetime
from enum import Enum

class EntityType(str, Enum):
    TICKER = "ticker"
    COMPANY = "company"
    DATE = "date"
    METRIC = "metric"
    SECTOR = "sector"
    CURRENCY = "currency"

class Entity(BaseModel):
    type: EntityType
    value: str
    normalized: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class Understanding(BaseModel):
    """Structured output for understand phase"""
    intent: str = Field(description="Clear statement of what user wants")
    entities: List[Entity] = Field(default_factory=list)
    complexity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    temporal_constraints: Optional[Dict[str, Any]] = None
    required_data_sources: List[str] = Field(default_factory=list)
    
    class Config:
        json_schema_extra = {
            "example": {
                "intent": "Analyze Apple stock performance",
                "entities": [{"type": "ticker", "value": "AAPL"}],
                "complexity_score": 0.7
            }
        }

class TaskType(str, Enum):
    RESEARCH = "research"
    CALCULATION = "calculation"
    VALIDATION = "validation"
    API_CALL = "api_call"

class ToolCall(BaseModel):
    tool: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(default=30, ge=1, le=300)

class PlanTask(BaseModel):
    id: str = Field(..., description="Unique task identifier")
    description: str
    task_type: TaskType = TaskType.RESEARCH
    tool_calls: List[ToolCall] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list, description="IDs of prerequisite tasks")
    max_retries: int = Field(default=2, ge=0, le=5)
    
    # Runtime fields (not part of LLM schema)
    status: str = "pending"
    attempt_count: int = 0
    result: Optional[Any] = None
    error: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

class Plan(BaseModel):
    """Structured output for plan phase"""
    summary: str = Field(description="Brief overview of the research plan")
    tasks: List[PlanTask] = Field(default_factory=list)
    
    class Config:
        json_schema_extra = {
            "example": {
                "summary": "Research AAPL financials",
                "tasks": [{"id": "1", "description": "Get stock price", "task_type": "api_call"}]
            }
        }
    
    def get_execution_order(self) -> List[List[str]]:
        """Validated topological batches. Invalid plans must never partially execute."""
        from collections import defaultdict, deque

        ids = [task.id for task in self.tasks]
        if not all(task_id.strip() for task_id in ids):
            raise ValueError("Task IDs must be non-empty")
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate task IDs in plan")

        known = set(ids)
        indegree = {task_id: 0 for task_id in ids}
        children = defaultdict(list)
        for task in self.tasks:
            for dependency in task.depends_on:
                if dependency not in known:
                    raise ValueError(f"Unknown dependency {dependency!r} for {task.id!r}")
                if dependency == task.id:
                    raise ValueError(f"Self-dependency in task {task.id!r}")
                if task.depends_on.count(dependency) > 1:
                    raise ValueError(f"Repeated dependency {dependency!r} for {task.id!r}")
                indegree[task.id] += 1
                children[dependency].append(task.id)

        queue = deque(task_id for task_id in ids if indegree[task_id] == 0)
        batches: List[List[str]] = []
        visited = 0
        while queue:
            batch = list(queue)
            queue.clear()
            batches.append(batch)
            visited += len(batch)
            for task_id in batch:
                for child in children[task_id]:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        queue.append(child)
        if visited != len(ids):
            raise ValueError("Cyclic task dependencies in plan")
        return batches

class ReflectionOutput(BaseModel):
    """Structured output for reflection phase"""
    is_complete: bool = Field(description="Whether research is sufficient")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in current results")
    reasoning: str = Field(description="Analysis of current state")
    missing_info: List[str] = Field(default_factory=list)
    needs_replanning: bool = Field(default=False)
    tasks_to_retry: List[str] = Field(default_factory=list)
    suggested_next_steps: Optional[str] = None