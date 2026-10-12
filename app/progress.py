"""Execution-local progress hooks; never store provider keys or raw prompts."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

_listener: ContextVar[Callable | None] = ContextVar("agent_progress", default=None)


def emit(kind: str, message: str, **data) -> None:
    listener = _listener.get()
    if listener:
        listener(kind, message, data)


def enabled() -> bool:
    return _listener.get() is not None


@contextmanager
def observe(listener):
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)
