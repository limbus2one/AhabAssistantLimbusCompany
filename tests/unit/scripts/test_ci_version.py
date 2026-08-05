from datetime import datetime, timezone
from pathlib import Path
from runpy import run_path


reserves_channel_number = run_path(
    Path(__file__).parents[3] / "scripts" / "ci_version.py"
)["reserves_channel_number"]


def test_feature_branch_push_reserves_alpha_version():
    published_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    run = {
        "created_at": "2026-08-05T00:00:00Z",
        "event": "push",
        "head_branch": "road",
    }

    assert reserves_channel_number(run, "alpha", published_at)

    run["head_branch"] = "main"
    assert not reserves_channel_number(run, "alpha", published_at)
