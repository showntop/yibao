from yibao_brain.interaction import UserInputLeaseGuard


class _InputClock:
    def __init__(self, now: float, last_input_at: float):
        self.now = now
        self.last_input_at = last_input_at

    def clock(self) -> float:
        return self.now

    def age(self) -> float:
        return self.now - self.last_input_at


def test_input_lease_allows_after_quiet_model_inference():
    sample = _InputClock(now=100.0, last_input_at=90.0)
    guard = UserInputLeaseGuard(sample.age, clock=sample.clock, idle_seconds=0.8)

    lease = guard.checkpoint()
    sample.now = 102.0

    allowed, reason = guard.permit(lease)
    assert allowed and reason is None


def test_input_lease_rejects_input_that_happened_during_inference():
    sample = _InputClock(now=100.0, last_input_at=90.0)
    guard = UserInputLeaseGuard(sample.age, clock=sample.clock, idle_seconds=0.8)

    lease = guard.checkpoint()
    sample.now = 102.0
    sample.last_input_at = 101.0

    allowed, reason = guard.permit(lease)
    assert not allowed
    assert "用户正在操作" in reason


def test_input_lease_requires_an_idle_window_before_injection():
    sample = _InputClock(now=100.0, last_input_at=99.5)
    guard = UserInputLeaseGuard(sample.age, clock=sample.clock, idle_seconds=0.8)

    lease = guard.checkpoint()
    sample.now = 100.2

    allowed, reason = guard.permit(lease)
    assert not allowed
    assert "用户正在操作" in reason


def test_input_lease_fails_closed_without_input_monitoring_permission():
    sample = _InputClock(now=100.0, last_input_at=90.0)
    guard = UserInputLeaseGuard(
        sample.age,
        clock=sample.clock,
        idle_seconds=0.8,
        available=False,
    )

    allowed, reason = guard.permit(guard.checkpoint())
    assert not allowed
    assert "输入监控" in reason
