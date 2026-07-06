"""
Runtime state dan statistik bot.
"""
from dataclasses import dataclass, field
from datetime import datetime

from bot.models.enums import BotMode, WorkflowState, RecoveryLevel


@dataclass
class WatchdogMetrics:
    adb_latency_ms: float = 0.0
    xml_dump_duration_ms: float = 0.0
    avg_parse_duration_ms: float = 0.0
    avg_workflow_loop_duration_ms: float = 0.0
    last_state_change: datetime = field(default_factory=datetime.now)
    frozen_threshold_seconds: int = 300       # 5 menit tanpa state change = frozen


@dataclass
class BotStats:
    start_time: datetime = field(default_factory=datetime.now)
    loop_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    purchase_count_session: int = 0           # reset setelah cooldown
    last_loop_duration_ms: float = 0.0
    total_loop_duration_ms: float = 0.0

    @property
    def avg_loop_duration_ms(self) -> float:
        if self.loop_count == 0:
            return 0.0
        return self.total_loop_duration_ms / self.loop_count

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    def record_loop(self, duration_ms: float) -> None:
        self.loop_count += 1
        self.last_loop_duration_ms = duration_ms
        self.total_loop_duration_ms += duration_ms

    def record_success(self) -> None:
        self.success_count += 1
        self.purchase_count_session += 1

    def record_failure(self) -> None:
        self.failure_count += 1

    def reset_session(self) -> None:
        """Dipanggil setelah cooldown selesai."""
        self.purchase_count_session = 0

    def to_dict(self) -> dict:
        return {
            "loop_count": self.loop_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "purchase_count_session": self.purchase_count_session,
            "start_time": self.start_time.isoformat(),
        }


@dataclass
class BotRuntimeState:
    mode: BotMode = BotMode.IDLE
    workflow_state: WorkflowState = WorkflowState.IDLE
    recovery_level: RecoveryLevel = RecoveryLevel.L1_SOFT_RETRY
    stats: BotStats = field(default_factory=BotStats)
    metrics: WatchdogMetrics = field(default_factory=WatchdogMetrics)
    cooldown_until: datetime | None = None
    blackout_active: bool = False

    def update_state(self, new_state: WorkflowState) -> None:
        self.workflow_state = new_state
        self.metrics.last_state_change = datetime.now()
