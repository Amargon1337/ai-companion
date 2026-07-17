import logging
import functools

logger = logging.getLogger(__name__)

try:
    from langfuse.decorators import observe as langfuse_observe
    HAS_LANGFUSE = True
except ImportError:
    HAS_LANGFUSE = False
    logger.warning("Langfuse is not installed. Tracing is disabled. Run `pip install langfuse` to enable.")

def observe(**kwargs):
    """
    Safe wrapper for Langfuse @observe.
    If langfuse is installed, acts as @observe.
    If not, returns the function unmodified.
    """
    if HAS_LANGFUSE:
        return langfuse_observe(**kwargs)
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
            
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
        
    return decorator

import asyncio
