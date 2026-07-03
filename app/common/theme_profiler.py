from contextlib import contextmanager
from time import perf_counter
from weakref import ref

from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtWidgets import QAbstractScrollArea
from qfluentwidgets import qconfig
from qfluentwidgets.common.style_sheet import setStyleSheet, styleSheetManager

from module.logger import log

_active_profile_id = 0
_active_theme_name = ""
_active_start_time = 0.0
_records: dict[str, dict[str, float | int]] = {}


def _theme_name(theme) -> str:
    return getattr(theme, "name", str(theme))


def begin_theme_profile(theme) -> int:
    """开始一次主题切换耗时统计。"""
    global _active_profile_id, _active_theme_name, _active_start_time, _records
    _active_profile_id += 1
    _active_theme_name = _theme_name(theme)
    _active_start_time = perf_counter()
    _records = {}
    log.info(f"[主题耗时#{_active_profile_id}] 开始切换到 {_active_theme_name}")
    return _active_profile_id


@contextmanager
def measure_theme_step(label: str):
    """记录主题切换期间某个步骤的耗时。

    同一个主题切换里部分回调会被多个控件重复触发，因此这里按 label 聚合次数、
    总耗时和单次最慢耗时，而不是每次触发都单独刷一行日志。
    """
    if _active_start_time == 0:
        yield
        return

    started_at = perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        record = _records.setdefault(label, {"count": 0, "total": 0.0, "max": 0.0})
        record["count"] = int(record["count"]) + 1
        record["total"] = float(record["total"]) + elapsed_ms
        record["max"] = max(float(record["max"]), elapsed_ms)


def finish_theme_profile(profile_id: int):
    """输出一次主题切换的耗时汇总。"""
    global _active_start_time
    if profile_id != _active_profile_id or _active_start_time == 0:
        return

    total_ms = (perf_counter() - _active_start_time) * 1000
    log.info(f"[主题耗时#{profile_id}] 切换到 {_active_theme_name} 总耗时 {total_ms:.2f} ms")
    for label, record in sorted(_records.items(), key=lambda item: float(item[1]["total"]), reverse=True):
        log.info(
            f"[主题耗时#{profile_id}] {label}: "
            f"次数={int(record['count'])}, "
            f"总耗时={float(record['total']):.2f} ms, "
            f"最慢单次={float(record['max']):.2f} ms"
        )
    _active_start_time = 0.0


def set_theme_with_profile(theme, save: bool = False, lazy: bool = False):
    """带分段耗时统计的 setTheme 等价实现。"""
    profile_id = begin_theme_profile(theme)
    try:
        with measure_theme_step("qconfig.set(themeMode) + themeChanged回调"):
            qconfig.set(qconfig.themeMode, theme, save)
        with measure_theme_step(f"qfluentwidgets.updateStyleSheet(lazy={lazy})"):
            _update_style_sheet_with_profile(lazy)
        with measure_theme_step("themeChangedFinished回调"):
            qconfig.themeChangedFinished.emit()
    finally:
        finish_theme_profile(profile_id)


def _update_style_sheet_with_profile(lazy: bool = False):
    """等价于 qfluentwidgets.updateStyleSheet，并按控件类记录耗时。"""
    removes = []
    skipped = 0
    skipped_clipped = 0
    deferred = []
    for widget, source in list(styleSheetManager.items()):
        try:
            if lazy and widget.visibleRegion().isNull():
                _mark_dirty_style_sheet(widget, source)
                skipped += 1
                continue

            if lazy and _is_clipped_by_scroll_area(widget):
                _mark_dirty_style_sheet(widget, source)
                skipped_clipped += 1
                continue

            if lazy and _is_in_setting_scroll_content(widget):
                deferred.append((ref(widget), source))
                continue

            label = f"qfluentwidgets.updateStyleSheet.widget.{widget.__class__.__name__}"
            object_name = widget.objectName()
            if object_name:
                label = f"{label}#{object_name}"
            with measure_theme_step(label):
                setStyleSheet(widget, source, qconfig.theme)
        except RuntimeError:
            removes.append(widget)

    if skipped:
        record = _records.setdefault(
            "qfluentwidgets.updateStyleSheet.lazySkippedHidden",
            {"count": 0, "total": 0.0, "max": 0.0},
        )
        record["count"] = int(record["count"]) + skipped

    if skipped_clipped:
        record = _records.setdefault(
            "qfluentwidgets.updateStyleSheet.lazySkippedClippedByScrollArea",
            {"count": 0, "total": 0.0, "max": 0.0},
        )
        record["count"] = int(record["count"]) + skipped_clipped

    if deferred:
        record = _records.setdefault(
            "qfluentwidgets.updateStyleSheet.lazyDeferredSettingContent",
            {"count": 0, "total": 0.0, "max": 0.0},
        )
        record["count"] = int(record["count"]) + len(deferred)
        _schedule_deferred_style_sheets(deferred)

    for widget in removes:
        styleSheetManager.deregister(widget)


def _mark_dirty_style_sheet(widget, source):
    styleSheetManager.register(source, widget)
    widget.setProperty("dirty-qss", True)


def _is_clipped_by_scroll_area(widget) -> bool:
    ancestor = widget.parentWidget()
    while ancestor is not None:
        scroll_area = ancestor.parentWidget()
        if isinstance(scroll_area, QAbstractScrollArea) and scroll_area.viewport() is ancestor:
            top_left = widget.mapTo(ancestor, QPoint(0, 0))
            widget_rect = QRect(top_left, widget.size())
            return not widget_rect.intersects(ancestor.rect())
        ancestor = ancestor.parentWidget()
    return False


def _is_in_setting_scroll_content(widget) -> bool:
    ancestor = widget.parentWidget()
    while ancestor is not None:
        if ancestor.objectName() == "scrollWidget":
            return True
        ancestor = ancestor.parentWidget()
    return False


def _schedule_deferred_style_sheets(items, batch_size: int = 12):
    queue = list(items)

    def apply_batch():
        for _ in range(min(batch_size, len(queue))):
            widget_ref, source = queue.pop(0)
            widget = widget_ref()
            if widget is None:
                continue
            try:
                setStyleSheet(widget, source, qconfig.theme)
            except RuntimeError:
                styleSheetManager.deregister(widget)
        if queue:
            QTimer.singleShot(16, apply_batch)

    QTimer.singleShot(16, apply_batch)
