"""下载完整更新包后才替换正式文件，失败可重试且不留下半包。"""

import os
import time

import requests


def download_update(url, destination, progress, retry_notice, attempts=3):
    temporary = f"{destination}.temp"
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        for attempt in range(1, attempts + 1):
            try:
                with requests.get(url, stream=True, timeout=(10, 30)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0))
                    downloaded = 0
                    last_progress = -1
                    progress(0)
                    with open(temporary, "wb") as stream:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            stream.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                percent = min(99, int(downloaded * 100 / total))
                                if percent != last_progress:
                                    progress(percent)
                                    last_progress = percent
                    if not downloaded or (total and not response.headers.get("content-encoding") and downloaded != total):
                        raise requests.exceptions.RequestException("更新包下载不完整")
                os.replace(temporary, destination)
                progress(100)
                return
            except (requests.exceptions.RequestException, OSError) as exc:
                if attempt == attempts:
                    raise
                retry_notice(f"下载失败（{attempt}/{attempts}），将重试: {exc}")
                time.sleep(attempt)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
