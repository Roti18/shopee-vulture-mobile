"""
EventBus — pub/sub sederhana berbasis asyncio.

Subscriber mendaftarkan diri dengan type event.
Emit akan memanggil semua subscriber untuk event tersebut secara async.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine, Type

from bot.utils.logger import get_logger

log = get_logger(__name__)

Handler = Callable[[Any], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: Type[Any], handler: Handler) -> None:
        """Daftarkan async handler untuk event_type tertentu."""
        self._handlers[event_type].append(handler)
        log.debug("EventBus: subscribe %s → %s", event_type.__name__, handler.__name__)

    def unsubscribe(self, event_type: Type[Any], handler: Handler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: Any) -> None:
        """
        Emit event ke semua subscriber terdaftar.
        Setiap handler dipanggil secara concurrent (gather).
        Error di satu handler tidak menghentikan yang lain.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            log.debug("EventBus: no handlers for %s", event_type.__name__)
            return

        log.debug("EventBus: emit %s → %d handler(s)", event_type.__name__, len(handlers))
        results = await asyncio.gather(
            *[h(event) for h in handlers], return_exceptions=True
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                log.error(
                    "EventBus: handler %s raised: %s",
                    handlers[i].__name__,
                    result,
                )
