"""Phase 5: Executive Architecture — Plugin orchestration and Executive Controller."""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class CognitiveModule(ABC):
    """Base plugin interface for all cognitive engines."""
    
    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def process_turn(self, query: str, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the module's core logic for the current turn."""
        pass

    def evaluate(self, result: dict[str, Any]) -> dict[str, Any]:
        """Optional post-execution evaluation."""
        return {}

    def learn(self, result: dict[str, Any]) -> None:
        """Optional offline or post-turn learning step."""
        pass


class ExecutivePipeline:
    """Represents the execution state of the current turn."""
    def __init__(self, query: str, context: Any = None):
        self.query = query
        self.context = context
        self.state: dict[str, Any] = {}
        self.metrics: dict[str, float] = {}
        self.traces: list[str] = []

    def add_trace(self, module_name: str, message: str) -> None:
        self.traces.append(f"[{module_name}] {message}")


class ExecutiveController:
    """Orchestrates the pipeline of CognitiveModules."""

    def __init__(self, modules: list[CognitiveModule]) -> None:
        self.modules = modules

    def execute_turn(self, query: str, context: Any = None) -> ExecutivePipeline:
        pipeline = ExecutivePipeline(query, context)
        
        for module in self.modules:
            start_t = time.perf_counter()
            try:
                result = module.process_turn(pipeline.query, pipeline.state)
                pipeline.state[module.name] = result
                
                # Optional evaluation
                eval_res = module.evaluate(result)
                if eval_res:
                    pipeline.state[f"{module.name}_eval"] = eval_res
                    
                pipeline.add_trace(module.name, "Execution successful.")
            except Exception as e:
                logger.error(f"Module {module.name} failed: {e}")
                pipeline.add_trace(module.name, f"FAILED: {e}")
            finally:
                latency = (time.perf_counter() - start_t) * 1000
                pipeline.metrics[f"{module.name}_ms"] = round(latency, 2)
                
        return pipeline

    def trigger_learning(self, pipeline: ExecutivePipeline) -> None:
        """Triggers the learning phase across all modules post-turn."""
        for module in self.modules:
            if module.name in pipeline.state:
                try:
                    module.learn(pipeline.state[module.name])
                except Exception as e:
                    logger.error(f"Module {module.name} learning failed: {e}")
