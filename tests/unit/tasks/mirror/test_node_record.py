import csv

from tasks.mirror.node_record import CSV_FIELDS, MirrorNodeRecorder


def test_finish_node_appends_complete_record(tmp_path):
    timestamps = iter([1_000.0, 1_002.345, 2_000.0, 2_001.0])
    csv_path = tmp_path / "mirror_node_records.csv"
    recorder = MirrorNodeRecorder(csv_path=csv_path, clock=lambda: next(timestamps))

    recorder.start_node(
        "Focused_Encounter",
        is_hard=True,
        floor=3,
        theme_pack="faith",
        aalc_team=2,
    )
    first_row = recorder.finish_node()
    recorder.start_node(
        "Risky Encounter",
        is_hard=False,
        floor=4,
        theme_pack="event_pack_leftmost",
        aalc_team=5,
    )
    second_row = recorder.finish_node()

    assert first_row["duration_seconds"] == 2.345
    assert second_row["duration_seconds"] == 1.0
    assert recorder.has_active_node is False

    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)

    assert tuple(reader.fieldnames) == CSV_FIELDS
    assert len(rows) == 2
    assert rows[0]["node_type"] == "Focused_Encounter"
    assert rows[0]["duration_seconds"] == "2.345"
    assert rows[0]["is_hard"] == "true"
    assert rows[0]["floor"] == "3"
    assert rows[0]["theme_pack"] == "faith"
    assert rows[0]["aalc_team"] == "2"
    assert rows[0]["started_at"]
    assert rows[0]["finished_at"]
    assert rows[1]["node_type"] == "Risky Encounter"


def test_finish_node_without_active_node_does_not_create_file(tmp_path):
    csv_path = tmp_path / "mirror_node_records.csv"
    recorder = MirrorNodeRecorder(csv_path=csv_path)

    assert recorder.finish_node() is None
    assert csv_path.exists() is False
