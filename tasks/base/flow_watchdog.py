from __future__ import annotations

import time
from collections.abc import Callable

from module.logger import log


class FlowRetryWatchdog:
    """大流程连续无进展看门狗。

    业务循环负责在真正推进时调用 :meth:`progress`，并在循环检查点调用
    :meth:`check`。只有连续无进展达到 ``timeout`` 后才执行公共 ``retry``，
    从而避免在正常页面的每一轮都扫描全部公共异常模板。
    """

    def __init__(
        self,
        flow_name: str,
        *,
        timeout: float = 300.0,
        recovery_timeout: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        recovery: Callable[..., object] | None = None,
    ) -> None:
        self.flow_name = flow_name
        self.timeout = float(timeout)
        self.recovery_timeout = float(recovery_timeout)
        self._clock = clock
        self._recovery = recovery
        self._last_progress_at = self._clock()
        self._last_progress_reason = "流程开始"
        self._timeout_count = 0 # retry触发次数

    @property
    def idle_seconds(self) -> float:
        """自上次进展以来的空闲秒数。"""
        return max(self._clock() - self._last_progress_at, 0.0)

    @property
    def timeout_count(self) -> int:
        """已触发公共恢复检查的次数。"""
        return self._timeout_count

    def progress(self, reason: str) -> None:
        """记录业务取得真实进展；重复截图或同一等待状态不应调用; 如果 reason 与上次相同则不更新时间戳则说明有可能处于卡死状态"""
        if reason == self._last_progress_reason:
            return
        else:
            self._last_progress_at = self._clock()
            self._last_progress_reason = reason

    def _expired(self) -> bool:
        """检查是否连续无进展达到 超时。"""
        return self.idle_seconds >= self.timeout

    def check(self) -> bool:
        """检查是否超时；恢复导致游戏重启时返回 ``False``。

        未超时或公共恢复正常结束时返回 ``True``。每次超时恢复结束后重新开始
        300 秒无进展计时，避免在同一帧高频重复执行完整恢复扫描。
        """
        expired = self._expired()
        if not expired:
            return True

        idle = self.idle_seconds
        self._timeout_count += 1
        log.warning(
            "[%s]连续%.1f秒无进展，执行公共重试检查（第%s次，上次进展=%s）",
            self.flow_name,
            idle,
            self._timeout_count,
            self._last_progress_reason,
        )

        recovery = self._recovery
        if recovery is None:
            from tasks.base.retry import retry

            recovery = retry

        result = recovery(timeout=self.recovery_timeout, source=f"流程看门狗.{self.flow_name}")
        self._last_progress_at = self._clock()
        self._last_progress_reason = "超时后完成公共重试检查"

        if result is False:
            return False

        return True
