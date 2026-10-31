"""Use the shipped MP3 through the GUI signal without external player probes."""

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget


def test_audio_signal_plays_mp3_on_gui_thread_without_subprocess():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    events = []
    active = [True]
    sys.addaudithook(lambda event, args: events.append(event)
                    if active[0] and event in {"subprocess.Popen", "os.system"} else None)
    from app import mediator
    from app.my_app import MainWindow

    class AudioHost(QWidget):
        play_audio = MainWindow.play_audio

    app = QApplication.instance() or QApplication([])
    host = AudioHost()
    mediator.play_audio.connect(host.play_audio)
    sound = Path(__file__).resolve().parents[1] / "assets/audio/This_is_all_your_fault_1.mp3"
    worker = threading.Thread(target=mediator.play_audio.emit, args=(str(sound),))
    worker.start()
    worker.join(timeout=2)
    errors = []
    try:
        app.processEvents()
        player = host.findChild(QMediaPlayer)
        assert player is not None
        assert player.thread() == app.thread()
        player.audioOutput().setMuted(True)
        player.errorOccurred.connect(lambda _code, message: errors.append(message))
        deadline = time.monotonic() + 8
        while player.position() == 0 and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert not errors
        assert player.position() > 0
        assert "playsound3" not in sys.modules
        assert not events
    finally:
        active[0] = False
        mediator.play_audio.disconnect(host.play_audio)
        host.deleteLater()
        app.processEvents()
