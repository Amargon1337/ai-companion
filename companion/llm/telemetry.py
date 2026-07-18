import logging
import functools
import asyncio
from companion.config import LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse
    from langfuse import observe as langfuse_observe
    
    # Инициализация Langfuse Cloud клиента
    if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
        langfuse_client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST
        )
        HAS_LANGFUSE = True
        logger.info("Langfuse Cloud client initialized successfully.")
    else:
        HAS_LANGFUSE = False
        logger.warning("Langfuse credentials missing. Tracing will be disabled.")
except ImportError as e:
    HAS_LANGFUSE = False
    import sys
    logger.warning(f"Langfuse import failed (Python: {sys.executable}). Error: {e}. Tracing is disabled.")

def observe(**kwargs):
    """
    Safe wrapper for Langfuse @observe.
    If langfuse is installed and configured, acts as @observe.
    If not, returns the function unmodified.
    """
    if HAS_LANGFUSE:
        return langfuse_observe(**kwargs)
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs_inner):
            return func(*args, **kwargs_inner)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs_inner):
            return await func(*args, **kwargs_inner)
            
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
        
    return decorator
