from pathlib import Path

import pytest
import requests

from module.update import download


class Response:
    def __init__(self, chunks, size):
        self.chunks = chunks
        self.headers = {"content-length": str(size)}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, **_):
        yield from self.chunks


def test_retry_does_not_publish_partial_download(tmp_path, monkeypatch):
    target = tmp_path / "AALC.zip"
    target.write_bytes(b"previous")
    calls = []
    responses = iter([Response([b"truncated"], 100), Response([b"complete"], 8)])

    def get(*_, **__):
        assert target.read_bytes() == b"previous"
        calls.append(True)
        return next(responses)

    monkeypatch.setattr(download.requests, "get", get)
    monkeypatch.setattr(download.time, "sleep", lambda _: None)
    progress = []
    notices = []
    download.download_update("https://example.test/update", str(target), progress.append, notices.append)
    assert target.read_bytes() == b"complete"
    assert len(calls) == 2 and len(notices) == 1
    assert progress.count(100) == 1 and progress[-1] == 100
    assert not Path(f"{target}.temp").exists()


def test_failed_download_preserves_previous_package_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "AALC.zip"
    target.write_bytes(b"previous")
    monkeypatch.setattr(download.requests, "get", lambda *_, **__: Response([b"short"], 100))
    monkeypatch.setattr(download.time, "sleep", lambda _: None)
    progress = []
    notices = []
    with pytest.raises(requests.exceptions.RequestException):
        download.download_update("https://example.test/update", str(target), progress.append, notices.append)
    assert len(notices) == 2
    assert target.read_bytes() == b"previous"
    assert 100 not in progress
    assert not Path(f"{target}.temp").exists()
