"""
Event dataclasses — semua event yang bisa di-emit oleh workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bot.models.enums import WorkflowState, RecoveryLevel


# ── Bot Lifecycle ────────────────────────────────────────────────────────────

@dataclass
class BotStartedEvent:
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class BotStoppedEvent:
    reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class BotPausedEvent:
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class BotResumedEvent:
    timestamp: datetime = field(default_factory=datetime.now)


# ── Stock Events ──────────────────────────────────────────────────────────────

@dataclass
class VariantStockDetectedEvent:
    """
    Dikirim saat parser berhasil membaca stok > 0 dari popup varian.
    Ini adalah event yang memicu Telegram alert SEBELUM checkout dimulai.

    Flow:
      Popup open → Read "Stok: N" → N meets threshold
      → emit VariantStockDetectedEvent → Telegram → Select → Buy
    """
    product_name: str
    variant: str
    stock_count: int
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class StockEmptyEvent:
    """Stok 0 atau tidak memenuhi minimum_stock threshold."""
    variant: str = ""
    stock_count: int = 0
    threshold: int = 1
    timestamp: datetime = field(default_factory=datetime.now)


# ── Order Events ──────────────────────────────────────────────────────────────

@dataclass
class OrderSuccessEvent:
    """Dikirim setelah order berhasil dibuat (CREATE_ORDER state)."""
    product_name: str
    variant: str
    price: str
    screenshot_path: Path | None = None
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class CheckoutFailedEvent:
    """
    Stok terdeteksi tetapi checkout/order gagal.
    Dikirim dari CREATE_ORDER atau VERIFY_PAYMENT jika gagal.
    """
    product_name: str
    variant: str
    reason: str
    state: WorkflowState = WorkflowState.CHECKOUT
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class OrderFailedEvent:
    """Generic order/workflow failure."""
    reason: str
    state: WorkflowState
    timestamp: datetime = field(default_factory=datetime.now)


# ── Workflow ─────────────────────────────────────────────────────────────────

@dataclass
class StateChangedEvent:
    previous: WorkflowState
    current: WorkflowState
    timestamp: datetime = field(default_factory=datetime.now)


# ── Recovery ─────────────────────────────────────────────────────────────────

@dataclass
class RecoveryStartedEvent:
    level: RecoveryLevel
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class RecoverySuccessEvent:
    level: RecoveryLevel
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class PanicEvent:
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)


# ── Scheduler ────────────────────────────────────────────────────────────────

@dataclass
class HeartbeatEvent:
    runtime_seconds: float
    cpu_percent: float
    ram_used_mb: float
    ram_total_mb: float
    adb_connected: bool
    current_state: str
    loop_count: int
    success_count: int
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class DailyReportEvent:
    loops: int
    success: int
    failure: int
    avg_loop_ms: float
    date: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class WatchdogAlertEvent:
    frozen_seconds: float
    last_state: WorkflowState
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class BlackoutStartEvent:
    window: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class BlackoutEndEvent:
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class CooldownStartEvent:
    hours: float
    purchase_count: int
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class CooldownEndEvent:
    timestamp: datetime = field(default_factory=datetime.now)
