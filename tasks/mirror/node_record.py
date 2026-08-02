import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable

CSV_FIELDS = (
    "node_type",
    "duration_seconds",
    "is_hard",
    "floor",
    "theme_pack",
    "aalc_team",
    "started_at",
    "finished_at",
)


class MirrorNodeRecorder:
    """将每个已完成镜牢节点的上下文和耗时追加到独立 CSV。"""

    _write_lock = Lock()

    def __init__(
        self,
        csv_path: str | Path = Path("logs") / "mirror_node_records.csv",
        clock: Callable[[], float] = time.time,
    ):
        self.csv_path = Path(csv_path)
        self._clock = clock
        self._active_node: dict | None = None

    @property
    def has_active_node(self) -> bool:
        return self._active_node is not None

    def start_node(
        self,
        node_type: str,
        *,
        is_hard: bool,
        floor: int,
        theme_pack: str,
        aalc_team: int,
    ) -> None:
        if self._active_node is not None:
            self.finish_node()

        self._active_node = {
            "node_type": node_type or "unknown",
            "is_hard": bool(is_hard),
            "floor": int(floor),
            "theme_pack": theme_pack or "unknown",
            "aalc_team": int(aalc_team),
            "started_at": self._clock(),
        }

    def finish_node(self) -> dict | None:
        if self._active_node is None:
            return None

        finished_at = self._clock()
        active_node = self._active_node
        row = {
            "node_type": active_node["node_type"],
            "duration_seconds": round(max(0.0, finished_at - active_node["started_at"]), 3),
            "is_hard": str(active_node["is_hard"]).lower(),
            "floor": active_node["floor"],
            "theme_pack": active_node["theme_pack"],
            "aalc_team": active_node["aalc_team"],
            "started_at": self._format_timestamp(active_node["started_at"]),
            "finished_at": self._format_timestamp(finished_at),
        }
        self._append_row(row)
        self._active_node = None
        return row

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")

    def _append_row(self, row: dict) -> None:
        with self._write_lock:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
            encoding = "utf-8-sig" if write_header else "utf-8"
            with self.csv_path.open("a", newline="", encoding=encoding) as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
