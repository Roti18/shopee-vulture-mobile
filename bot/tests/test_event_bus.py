"""
Tests untuk Event Bus.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.events.bus import EventBus
from bot.events.events import OrderSuccessEvent, OrderFailedEvent


@pytest.mark.asyncio
async def test_single_subscriber_called():
    bus = EventBus()
    received = []

    async def handler(event: OrderSuccessEvent):
        received.append(event)

    bus.subscribe(OrderSuccessEvent, handler)
    await bus.emit(OrderSuccessEvent(product_name="Test", variant="A", price="Rp1"))

    assert len(received) == 1
    assert received[0].product_name == "Test"


@pytest.mark.asyncio
async def test_multiple_subscribers_all_called():
    bus = EventBus()
    calls = []

    async def h1(e): calls.append("h1")
    async def h2(e): calls.append("h2")

    bus.subscribe(OrderSuccessEvent, h1)
    bus.subscribe(OrderSuccessEvent, h2)
    await bus.emit(OrderSuccessEvent(product_name="X", variant="B", price="Rp2"))

    assert "h1" in calls and "h2" in calls


@pytest.mark.asyncio
async def test_wrong_event_type_not_called():
    bus = EventBus()
    received = []

    async def handler(e): received.append(e)

    bus.subscribe(OrderSuccessEvent, handler)
    # Emit tipe berbeda
    from bot.models.enums import WorkflowState
    await bus.emit(OrderFailedEvent(reason="test", state=WorkflowState.CHECKOUT))

    assert len(received) == 0


@pytest.mark.asyncio
async def test_handler_error_does_not_stop_others():
    bus = EventBus()
    calls = []

    async def bad_handler(e): raise RuntimeError("oops")
    async def good_handler(e): calls.append("good")

    bus.subscribe(OrderSuccessEvent, bad_handler)
    bus.subscribe(OrderSuccessEvent, good_handler)
    # Harus tidak raise
    await bus.emit(OrderSuccessEvent(product_name="Y", variant="C", price="Rp3"))

    assert "good" in calls
