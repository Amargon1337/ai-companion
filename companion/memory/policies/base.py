"""Base class and data structures for memory policies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from companion.models import Fact


@dataclass
class PolicyDecision:
    """Represents a decision made by a memory policy."""

    approved: bool
    action: str
    updates: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    policy_name: str = ""


class Policy(ABC):
    """Abstract base class for all memory mutation policies."""

    @abstractmethod
    def evaluate(
        self,
        rec: Any,
        fact: dict[str, Any] | Fact,
        target_fact: dict[str, Any] | Fact | None = None,
    ) -> PolicyDecision:
        """Evaluate a recommendation against the given fact(s) and return a decision."""
        ...
