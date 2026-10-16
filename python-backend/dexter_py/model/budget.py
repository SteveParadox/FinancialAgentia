"""Per-attempt, task-local reservations. These are ceilings, not billed usage."""
from contextvars import ContextVar
from dataclasses import dataclass


class ModelBudgetExceeded(ValueError):
    pass


@dataclass
class ModelBudget:
    max_calls: int
    max_reserved_output_tokens: int
    calls: int = 0
    reserved_output_tokens: int = 0
    max_input_characters: int = 350000
    input_characters_reserved: int = 0

    def reserve(self, tokens: int, input_characters: int = 0):
        if (self.calls >= self.max_calls or self.reserved_output_tokens + tokens > self.max_reserved_output_tokens
                or self.input_characters_reserved + input_characters > self.max_input_characters):
            raise ModelBudgetExceeded("Research model budget exhausted")
        self.calls += 1
        self.reserved_output_tokens += tokens
        self.input_characters_reserved += input_characters


current_budget: ContextVar[ModelBudget | None] = ContextVar("research_model_budget", default=None)


def reserve_model_call(tokens: int, input_characters: int = 0):
    budget = current_budget.get()
    if budget is not None:
        budget.reserve(tokens, input_characters)
