"""AALC 独立更新器。

参考 MAA.Updater（MaaAssistantArknights）的设计，把更新做成完全自动的静默流程：

- 无终端窗口：随 ``updater.spec`` 以无窗口（console=False）方式打包，全部输出
  重定向到 ``update_temp/updater.log``，任何子进程都以无窗口标记启动；
- 无人工确认：不含任何 ``input()`` 等待，失败自动重试，最终失败通过原生
  MessageBox 提示（而非终端），并尽量恢复旧版本可用后自动重启 AALC；
- 可视进度：用 Win32 原生小窗口展示阶段与进度（``--no-progress-ui`` 可关闭），
  参考 MAA.Updater 的进度窗口；
- 平滑交接：支持 ``--parent-pid`` 等待主程序优雅退出（超时取消安装），
  避免更新器主动杀掉正在保存配置的主程序。

命令行兼容形式：
    AALC Updater.exe <file_name>
    AALC Updater.exe --apply-update <file_name>
    任意位置可附加 --parent-pid <pid> / --no-progress-ui
"""

import argparse
import contextlib
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import winreg
from pathlib import Path, PurePosixPath

import psutil

from update_support import InstallationLock, incomplete_update_path

# Windows 进程创建标记：统一无窗口启动，避免任何控制台闪现
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200

_PARENT_EXIT_WAIT_SECONDS = 15
_INSTALL_LOCK_WAIT_SECONDS = 30

# 进度窗配色：与主程序保持一致（app/my_app.py 的主题色、app/common/ui_config.py 的
# 窗口/内容背景）。深色：窗口 #1C1C1C、内容 #2D2D2D；亮色：窗口纯白、内容浅灰；
# 品牌色 #9C080B 两种模式下都用作进度填充。
_THEME_PALETTES = {
    "dark": {
        "dark_title": True,
        "bg": 0x1C1C1C,
        "track": 0x2D2D2D,
        "fill": 0x9C080B,
        "stage": 0xFFFFFF,
    },
    "light": {
        "dark_title": False,
        "bg": 0xFFFFFF,
        "track": 0xEBEBEB,
        "fill": 0x9C080B,
        "stage": 0x1C1C1C,
    },
}


def _resolve_theme_palette():
    """按主程序的主题设置选择进度窗配色。

    读取安装目录 config.yaml 的 theme_mode（AUTO/LIGHT/DARK）；AUTO 或读取
    失败时跟随 Windows 系统应用主题，兜底深色。
    """
    mode = ""
    try:
        with open("config.yaml", encoding="utf-8") as f:
            match = re.search(r"^\s*theme_mode:\s*([A-Za-z]+)", f.read(), re.MULTILINE)
        if match:
            mode = match.group(1).upper()
    except OSError:
        pass
    if mode == "DARK":
        return _THEME_PALETTES["dark"]
    if mode == "LIGHT":
        return _THEME_PALETTES["light"]
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            apps_light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return _THEME_PALETTES["light" if apps_light else "dark"]
    except OSError:
        return _THEME_PALETTES["dark"]


def _ancestor_pids():
    """返回当前进程的祖先 pid 集合。

    PyInstaller onefile 在 Windows 上是双进程模型：引导父进程与 Python 子进程
    的 exe 同名，进程枚举时会把引导父进程误判为"其他更新器"，这里显式排除，
    避免等待/清扫逻辑杀掉自己的引导进程导致收尾挂起。
    """
    pids = set()
    try:
        for parent in psutil.Process(os.getpid()).parents():
            pids.add(parent.pid)
    except psutil.Error:
        pass
    return pids


_EXTRACT_MAX_ATTEMPTS = 3
_FS_OP_MAX_ATTEMPTS = 4
_FS_OP_RETRY_DELAY_SECONDS = 0.5
# 自复制时限时重试，容忍扫描软件的短暂文件占用。
_SELF_COPY_MAX_ATTEMPTS = 15
_SELF_COPY_RETRY_DELAY_SECONDS = 2
_LOG_ROTATE_SIZE = 512 * 1024

TEMP_DIR_NAME = "update_temp"
LOG_FILE_NAME = "updater.log"


class UpdateManifestError(ValueError):
    """更新清单包含不能安全应用的内容。"""


class UpdatePayloadError(RuntimeError):
    """更新包无法解压或缺失关键内容。"""


_LOG_STREAM = None


def _setup_logging():
    """把 stdout/stderr 重定向到日志文件（无窗口模式下可追溯更新全过程）。"""
    global _LOG_STREAM
    log_dir = os.path.abspath(f"./{TEMP_DIR_NAME}")
    log_path = os.path.join(log_dir, LOG_FILE_NAME)
    try:
        os.makedirs(log_dir, exist_ok=True)
        try:
            # 简单轮转：超过阈值时把旧日志挪到 .1，避免无限膨胀
            if os.path.exists(log_path) and os.path.getsize(log_path) > _LOG_ROTATE_SIZE:
                backup_path = f"{log_path}.1"
                with contextlib.suppress(OSError):
                    os.remove(backup_path)
                os.replace(log_path, backup_path)
        except OSError:
            pass
        _LOG_STREAM = open(log_path, "a", encoding="utf-8", buffering=1, errors="replace")
    except OSError:
        _LOG_STREAM = open(os.devnull, "w", encoding="utf-8")
        log_path = None
    sys.stdout = _LOG_STREAM
    sys.stderr = _LOG_STREAM
    return log_path


def _log(message):
    """写一行带时间戳的更新日志；日志不可用时静默丢弃。"""
    if _LOG_STREAM is None:
        return
    try:
        _LOG_STREAM.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _retry_fs_op(operation, description, attempts=_FS_OP_MAX_ATTEMPTS, delay=_FS_OP_RETRY_DELAY_SECONDS):
    """对文件操作做有限次自动重试，应对杀软扫描、目标被占用等短暂失败。"""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except OSError as exc:
            if attempt == attempts:
                raise
            _log(f"{description} 失败(第 {attempt}/{attempts} 次): {exc}，{delay}s 后自动重试")
            time.sleep(delay)


def show_error_dialog(title, text, owner=None):
    """用原生 MessageBox 提示致命错误（无控制台场景下的兜底交互）。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(ctypes.c_void_p(owner), ctypes.c_wchar_p(text), ctypes.c_wchar_p(title), 0x00000010)
    except Exception:
        pass


class _NullProgress:
    """进度接口的空实现（进度窗被禁用或不可用时使用）。"""

    def start(self):
        pass

    def update(self, stage=None, detail=None, value=None, maximum=None):
        pass

    def close(self):
        pass

    def get_window_handle(self):
        return None


class _ProgressMessage(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", ctypes.c_uint32), ("lpData", ctypes.c_void_p)]


class _RemoteProgress(_NullProgress):
    """apply 更新器把进度发送到引导进程持有的窗口，不创建第二个窗口。"""

    def __init__(self, window_handle):
        self.window_handle = window_handle

    def get_window_handle(self):
        return self.window_handle

    def update(self, stage=None, detail=None, value=None, maximum=None):
        if not self.window_handle:
            return
        data = json.dumps(dict(stage=stage, detail=detail, value=value, maximum=maximum)).encode("utf-8")
        buffer = ctypes.create_string_buffer(data)
        message = _ProgressMessage(0x41414C43, len(data), ctypes.cast(buffer, ctypes.c_void_p))
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p,
                         ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t()
        # 接收进程退出或失去响应时放弃显示，不阻塞安装。
        if not send(self.window_handle, 0x004A, 0, ctypes.byref(message), 0x0002, 200, ctypes.byref(result)):
            self.window_handle = None


class UpdateProgressWindow:
    """Win32 原生更新进度窗口（参考 MAA.Updater）。

    - 窗口与消息泵运行在独立线程，更新逻辑线程只写入共享状态；
    - 进度未知时展示阶段文字，进度已知时交给系统控件显示；
    - 任何 Win32 调用失败都静默降级为无进度窗，绝不阻塞更新流程。
    """

    def __init__(self, enabled=True):
        self._enabled = enabled and os.name == "nt"
        self._lock = threading.Lock()
        self._stage = "正在准备更新..."
        self._detail = ""
        self._value = 0
        self._maximum = 0  # 0 表示不定进度
        self._closing = False
        self._thread = None
        self._hwnd = None
        self._ready = threading.Event()

    @property
    def enabled(self):
        """进度窗是否处于启用状态（Win32 失败后会自动降级为 False）。"""
        return self._enabled

    def start(self):
        if not self._enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._window_thread, name="AALCUpdaterProgressUI", daemon=True)
        self._thread.start()

    def update(self, stage=None, detail=None, value=None, maximum=None):
        if not self._enabled:
            return
        with self._lock:
            if stage is not None:
                if stage != self._stage:
                    self._detail = ""
                    self._value = self._maximum = 0
                self._stage = stage
            if detail is not None:
                self._detail = detail
            if value is not None:
                self._value = max(0, int(value))
            if maximum is not None:
                self._maximum = max(0, int(maximum))

    def close(self):
        if not self._enabled:
            return
        with self._lock:
            self._closing = True
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def get_window_handle(self):
        if self._thread is not None and self.enabled:
            self._ready.wait(timeout=2)
        return self._hwnd

    def _receive_progress(self, address):
        message = ctypes.cast(address, ctypes.POINTER(_ProgressMessage)).contents
        if message.dwData != 0x41414C43 or not 0 < message.cbData <= 65536 or not message.lpData:
            return False
        try:
            data = json.loads(ctypes.string_at(message.lpData, message.cbData).decode("utf-8"))
            if not isinstance(data, dict) or data.keys() - {"stage", "detail", "value", "maximum"}:
                return False
            if any(data.get(key) is not None and not isinstance(data[key], str) for key in ("stage", "detail")):
                return False
            if any(data.get(key) is not None and type(data[key]) is not int for key in ("value", "maximum")):
                return False
            self.update(**data)
            return True
        except (ValueError, TypeError, UnicodeError):
            return False

    def _window_thread(self):
        try:
            self._window_loop()
        except Exception as exc:  # 进度窗失败不允许影响更新流程
            _log(f"进度窗口不可用，已降级为无进度窗更新: {exc}")
            self._enabled = False
        finally:
            self._ready.set()

    def _window_loop(self):
        """复用 Windows 文本和进度条控件，免去 GDI 自绘、双缓冲及 ctypes API 声明。"""
        import commctrl
        import win32api
        import win32con as wc
        import win32event
        import win32gui as gui

        gui.InitCommonControls()
        colors = _resolve_theme_palette()
        def color(key):
            rgb = colors[key]
            return win32api.RGB(rgb >> 16, (rgb >> 8) & 255, rgb & 255)

        brush = gui.CreateSolidBrush(color("bg"))
        fonts = []
        for size in (24, 18):
            font = gui.LOGFONT()
            font.lfFaceName, font.lfHeight = "Microsoft YaHei UI", -size
            fonts.append(gui.CreateFontIndirect(font))

        def window_proc(hwnd, message, wparam, lparam):
            if message == wc.WM_CLOSE:
                return 0
            if message == wc.WM_COPYDATA:
                return int(self._receive_progress(lparam)) if lparam else 0
            if message == wc.WM_CTLCOLORSTATIC:
                gui.SetTextColor(wparam, color("stage"))
                gui.SetBkColor(wparam, color("bg"))
                return brush
            return gui.DefWindowProc(hwnd, message, wparam, lparam)

        window_class = gui.WNDCLASS()
        window_class.lpszClassName = "AALCUpdaterProgressWnd"
        window_class.lpfnWndProc = window_proc
        window_class.hInstance = win32api.GetModuleHandle(None)
        window_class.hbrBackground = brush
        window_class.hCursor = gui.LoadCursor(None, wc.IDC_ARROW)
        gui.RegisterClass(window_class)
        hwnd = None
        try:
            width, height = 650, 240
            hwnd = gui.CreateWindowEx(wc.WS_EX_APPWINDOW | wc.WS_EX_DLGMODALFRAME,
                                      window_class.lpszClassName, "AALC 更新", wc.WS_CAPTION,
                                      (win32api.GetSystemMetrics(0) - width) // 2,
                                      (win32api.GetSystemMetrics(1) - height) // 2,
                                      width, height, 0, 0, window_class.hInstance, None)
            def control(kind, text, y, height, font):
                child = gui.CreateWindowEx(0, kind, text, wc.WS_CHILD | wc.WS_VISIBLE,
                                           24, y, 594, height, hwnd, 0, window_class.hInstance, None)
                gui.SendMessage(child, wc.WM_SETFONT, font, True)
                return child

            stage = control("STATIC", "", 20, 40, fonts[0])
            detail = control("STATIC", "", 68, 50, fonts[1])
            bar = control(commctrl.PROGRESS_CLASS, "", 128, 20, fonts[1])
            percent = control("STATIC", "", 164, 28, fonts[1])
            with contextlib.suppress(Exception):
                ctypes.windll.uxtheme.SetWindowTheme(ctypes.c_void_p(bar), "", "")
                dark = ctypes.c_int(colors["dark_title"])
                ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(dark), 4)
            gui.SendMessage(bar, commctrl.PBM_SETBARCOLOR, 0, color("fill"))
            gui.SendMessage(bar, commctrl.PBM_SETBKCOLOR, 0, color("track"))
            self._hwnd = hwnd
            self._ready.set()
            gui.ShowWindow(hwnd, wc.SW_SHOWNORMAL)
            with contextlib.suppress(Exception):
                gui.SetForegroundWindow(hwnd)
            previous = None
            while not self._closing:
                gui.PumpWaitingMessages()
                with self._lock:
                    state = (self._stage, self._detail, self._value, self._maximum)
                if state != previous:
                    title, lines, value, maximum = state
                    gui.SetWindowText(stage, title)
                    gui.SetWindowText(detail, lines)
                    gui.SendMessage(bar, commctrl.PBM_SETRANGE32, 0, maximum or 1)
                    gui.SendMessage(bar, commctrl.PBM_SETPOS, min(value, maximum), 0)
                    gui.SetWindowText(percent, f"{min(100, value * 100 // maximum)}%" if maximum else "正在处理…")
                    previous = state
                win32event.MsgWaitForMultipleObjects([], False, 100, wc.QS_ALLINPUT)
        finally:
            if hwnd:
                gui.DestroyWindow(hwnd)
            self._hwnd = None
            gui.UnregisterClass(window_class.lpszClassName, window_class.hInstance)
            for handle in fonts:  # 窗口类的背景刷由 UnregisterClass 释放。
                gui.DeleteObject(handle)


def parse_updater_args(argv):
    """保留旧参数形式和未知参数兼容性，交给标准库解析。"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("file_name", nargs="?")
    parser.add_argument("--apply-update", dest="apply_mode", action="store_true")
    parser.add_argument("--no-progress-ui", action="store_true")
    parser.add_argument("--work-dir")
    for name in ("parent-pid", "progress-window"):
        parser.add_argument(f"--{name}", type=lambda value: int(value) if value.isdecimal() and int(value) > 0 else None)
    return parser.parse_known_args(argv)[0]


class Updater:
    """应用程序更新器，负责检查、下载、解压和安装最新版本的应用程序。"""

    def __init__(self, file_name, no_progress_ui=False, work_dir=None):
        self.updater_name = "AALC Updater.exe"
        self.apply_updater_name = "AALC Updater.apply.exe"

        self.temp_path = os.path.abspath(f"./{TEMP_DIR_NAME}")
        self.file_name = file_name
        if not self.file_name or os.path.basename(self.file_name) != self.file_name:
            raise UpdatePayloadError("更新包必须是 update_temp 内的文件名")
        self.download_file_path = os.path.join(self.temp_path, self.file_name)
        if not os.path.isfile(self.download_file_path):
            raise UpdatePayloadError(f"更新包不存在或不是文件，更新已取消: {self.download_file_path}")
        self.work_dir = _update_work_dir(work_dir)

        self.no_progress_ui = no_progress_ui
        self.log_path = os.path.join(self.temp_path, LOG_FILE_NAME)

        self.cover_folder_path = os.path.abspath("./")

        self.exe_path = os.path.abspath("./assets/binary/7za.exe")
        self.delete_folder_path = os.path.abspath("./assets/images")
        self.changes_file_path = os.path.join(self.work_dir, "changes.json")

        self.extract_folder_path = os.path.join(self.work_dir, self.file_name.rsplit(".", 1)[0])

        self.progress = _NullProgress()
        self._incremental_update_plan = None
        # 是否已开始改动安装目录（删除/覆盖文件）：决定失败后能否安全重启旧版本
        self._install_touched = False
        self._undo = []
        self._backup_dir = os.path.join(self.work_dir, "backup")


    def run(self, apply_mode=False, parent_pid=None, progress=None):
        """执行完整更新流程，返回是否成功。"""
        self.progress = progress or _NullProgress()
        try:
            self._wait_for_parent_exit(parent_pid)
        except UpdatePayloadError as exc:
            self._abort_safely(str(exc), install_modified=False, restart=False)
            return False
        self.progress.start()
        self.progress.update(stage="正在准备更新...", detail="校验更新包")
        try:
            self._prepare_update_payload(apply_mode)
        except UpdatePayloadError as exc:
            self._abort_safely(str(exc), install_modified=False)
            return False

        try:
            self.validate_update_payload()
            legacy_updater = self._replace_legacy_updater()
        except (UpdateManifestError, UpdatePayloadError) as exc:
            self._abort_safely(f"更新清单无效，已取消更新: {exc}", install_modified=False)
            return False

        # 引导进程把剩余工作交给包内新更新器（其自身体积大、可能需要先自更新）；
        # parent-pid 必须一并转发，否则新版更新器会跳过"等待主程序优雅退出"
        if not apply_mode and not legacy_updater and self._handoff_to_new_updater(parent_pid=parent_pid):
            # 保持当前进度窗及消息循环存活，直到 apply 完成；失败状态也必须向上传递。
            return self._apply_process.wait() == 0

        install_lock = InstallationLock(self.cover_folder_path)
        if not install_lock.acquire(timeout=_INSTALL_LOCK_WAIT_SECONDS):
            self._abort_safely("安装目录正被其他 AALC 或更新器使用，请稍后重试。", install_modified=False, restart=False)
            return False
        try:
            self.progress.update(stage="正在关闭 AALC 进程...", detail="清理当前安装目录内占用文件的进程")
            self.terminate_processes()
            marker = incomplete_update_path(self.cover_folder_path)
            was_incomplete = os.path.exists(marker)
            if was_incomplete and self._incremental_update_plan is not None:
                self._abort_safely("上次安装未完成，请使用完整更新包修复。", install_modified=True)
                return False
            with open(marker, "w", encoding="utf-8") as stream:
                json.dump({"backup": self._backup_dir, "package": self.file_name}, stream, ensure_ascii=False)
            try:
                self.cover_folder()
            except Exception as exc:
                restored = self._rollback() and not was_incomplete
                if restored:
                    os.remove(marker)
                install_lock.close()
                self._abort_safely(f"覆盖安装失败: {exc}", install_modified=not restored)
                return False
            os.remove(marker)
            self.progress.update(stage="正在清理临时文件...", detail="")
            self.cleanup()
            # 当前 exe 尚在运行，下一次更新再清理已完成的会话目录。
            with contextlib.suppress(OSError):
                Path(self.work_dir, "completed.flag").touch()
        finally:
            install_lock.close()

        relaunch_app(self.progress)
        return True

    def _abort_safely(self, reason, install_modified, restart=True):
        """更新中止：弹窗说明原因；未改动安装时自动恢复旧版本运行。"""
        _log(f"更新中止: {reason}")
        if install_modified:
            hint = "\n\n本次更新未完成，安装目录可能不完整，请重新更新或手动恢复。"
        else:
            hint = "\n\n当前版本文件保持完整。" + ("将自动重启 AALC。" if restart else "")
        show_error_dialog("AALC 更新失败", f"{reason}{hint}\n\n详细日志: {self.log_path}", self.progress.get_window_handle())
        self.progress.update(stage="更新失败", detail=reason)
        if not install_modified and restart:
            relaunch_app(self.progress)

    def _wait_for_parent_exit(self, parent_pid):
        """等待主程序优雅退出（MAA.Updater 的 ParentPid 等待策略）。

        先静默等待，再显示倒计时；仍未退出则取消，保留主程序当前任务和配置。
        """
        if not parent_pid:
            return
        _log(f"等待主程序退出 (PID={parent_pid})，最多 {_PARENT_EXIT_WAIT_SECONDS}s ...")
        self.progress.update(stage="正在等待 AALC 退出...", detail="主程序正在保存配置并退出")
        deadline = time.monotonic() + _PARENT_EXIT_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                if not psutil.pid_exists(parent_pid):
                    _log("主程序已退出")
                    return
            except psutil.Error:
                return
            time.sleep(0.2)
        self.progress.start()
        for remaining in range(60, 0, -1):
            if not psutil.pid_exists(parent_pid):
                return
            self.progress.update(stage="正在等待 AALC 退出...", detail=f"主程序尚未退出，{remaining} 秒后取消本次更新")
            time.sleep(1)
        # 有安装锁时不能绕过仍在保存配置的主程序；超时取消比强杀更安全。
        raise UpdatePayloadError("主程序未能正常退出，本次更新已取消，请关闭 AALC 后重试。")


    def extract_file(self):
        """解压下载的更新包，自动重试有限次数。"""
        _log("开始解压更新包...")
        self.progress.update(stage="正在解压更新包...", detail=self.file_name)
        last_error = None
        for attempt in range(1, _EXTRACT_MAX_ATTEMPTS + 1):
            try:
                self._reset_extraction_workspace()
                if os.path.exists(self.exe_path):
                    self._run_7zip_extract()
                else:
                    shutil.unpack_archive(self.download_file_path, self.work_dir)
                _log("解压完成")
                return True
            except Exception as exc:
                last_error = exc
                _log(f"解压失败(第 {attempt}/{_EXTRACT_MAX_ATTEMPTS} 次): {exc}")
                time.sleep(1)
        _log("多次解压失败，请查看日志或手动下载更新")
        self._last_extract_error = last_error
        return False

    def _run_7zip_extract(self):
        """调用 7za 解压；无窗口启动并捕获输出，避免控制台闪现。"""
        try:
            result = subprocess.run(
                [self.exe_path, "x", self.download_file_path, f"-o{self.work_dir}", "-aoa"],
                check=True,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
            if result.stdout:
                _log(result.stdout.decode("utf-8", "replace").strip())
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            if detail:
                _log(f"7za 输出: {detail}")
            raise

    def _reset_extraction_workspace(self):
        """清理本次解压目标，避免复用上一次更新残留的载荷或清单。"""
        if os.path.isdir(self.extract_folder_path):
            shutil.rmtree(self.extract_folder_path)
        if os.path.exists(self.changes_file_path):
            os.remove(self.changes_file_path)

    def _prepare_update_payload(self, apply_mode):
        # 引导进程已解压过同一归档（handoff 只发生在解压成功后），apply 模式直接复用，避免重复解压。
        if apply_mode and os.path.isdir(self.extract_folder_path) and os.listdir(self.extract_folder_path):
            _log("复用引导进程的解压结果...")
            return
        if not self.extract_file():
            raise UpdatePayloadError(f"更新包解压失败（最后错误: {self._last_extract_error}）")

    def validate_update_payload(self):
        """在终止应用进程前验证增量更新清单及其所有目标路径。"""
        self._incremental_update_plan = None
        if not os.path.exists(self.changes_file_path):
            if not os.path.isfile(os.path.join(self.extract_folder_path, "AALC.exe")):
                raise UpdatePayloadError("完整更新包缺少 AALC.exe")
            self._collect_copy_entries()  # 写入前检查全部目标路径
            return
        self._incremental_update_plan = self._load_incremental_update_plan()
        for kind in ("added", "modified"):
            for name, source, _destination in self._incremental_update_plan[kind]:
                if not os.path.isfile(source):
                    raise UpdatePayloadError(f"更新包缺少清单中的文件: {name}")


    def _replace_legacy_updater(self):
        """旧发布包的控制台更新器不支持窗口、备份及交接参数，保留当前窗口版。"""
        path = os.path.join(self.extract_folder_path, self.updater_name)
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "rb") as stream:
                if stream.read(2) != b"MZ":
                    raise UpdatePayloadError("包内更新器不是有效的 Windows 程序")
                stream.seek(0x3C)
                stream.seek(int.from_bytes(stream.read(4), "little"))
                header = stream.read(94)
            # PE 签名 + 文件头共 24 字节；OptionalHeader.Subsystem 位于其后 68 字节。
            if len(header) != 94 or header[:4] != b"PE\0\0":
                raise UpdatePayloadError("包内更新器的 PE 头不完整")
            if int.from_bytes(header[92:94], "little") != 3:  # IMAGE_SUBSYSTEM_WINDOWS_CUI
                return False
            _retry_fs_op(lambda: shutil.copy2(sys.executable, path), "保留窗口版更新器")
        except OSError as exc:
            raise UpdatePayloadError(f"准备包内更新器失败: {exc}") from exc
        _log("更新包内是旧控制台更新器，保留当前窗口版并继续安装")
        return True

    def _handoff_to_new_updater(self, current_executable=None, parent_pid=None):
        extracted_updater_path = os.path.join(self.extract_folder_path, self.updater_name)
        if not os.path.exists(extracted_updater_path):
            return False

        current_executable_path = os.path.abspath(current_executable or sys.argv[0])
        staged_updater_path = os.path.join(self.work_dir, self.apply_updater_name)

        if os.path.normcase(os.path.abspath(extracted_updater_path)) == os.path.normcase(current_executable_path):
            return False

        shutil.copy2(extracted_updater_path, staged_updater_path)
        extra_args = ["--work-dir", self.work_dir]
        if parent_pid:
            # 交接必须保留父进程等待语义：新版更新器同样要先等 AALC 优雅退出
            extra_args += ["--parent-pid", str(parent_pid)]
        if self.no_progress_ui:
            extra_args.append("--no-progress-ui")
        else:
            handle = self.progress.get_window_handle()
            if handle:
                extra_args += ["--progress-window", str(handle)]
        self._apply_process = subprocess.Popen(
            [staged_updater_path, "--apply-update", self.file_name, *extra_args],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            cwd=self.cover_folder_path,
        )
        _log("已切换到新版本更新器继续更新...")
        return True


    def terminate_processes(self):
        """终止相关进程以准备更新。"""
        _log("开始清理占用安装目录的进程...")
        install_root = os.path.normcase(os.path.abspath(self.cover_folder_path)) + os.sep
        ancestors = _ancestor_pids()
        for proc in psutil.process_iter(attrs=["pid", "name"]):
            if proc.pid == os.getpid():
                continue  # 更新器自身位于 update_temp（安装根目录内），不能杀自己
            if proc.pid in ancestors:
                continue  # onefile 引导父进程与本更新器同名同目录，杀掉会导致收尾挂起
            try:
                exe_path = proc.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            # 覆盖 AALC 及安装目录内的附属进程（如 adb 服务），否则其可执行文件会被占用无法覆盖
            is_install_binary = os.path.normcase(exe_path).startswith(install_root)
            # 其他更新器可能正在准备独立工作目录，不能按名称或安装目录误杀。
            if (proc.info.get("name") or "").lower().startswith("aalc updater"):
                continue
            if is_install_binary:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)  # 等待最多10秒
                    except psutil.TimeoutExpired:
                        proc.kill()  # 超时强制终止
                        proc.wait(timeout=5)  # 再次等待
                    _log(f"已终止进程 PID: {proc.info['pid']} ({proc.info['name']})")
                except psutil.AccessDenied:
                    _log(f"无权限终止进程 PID: {proc.info['pid']}")
                except psutil.NoSuchProcess:
                    _log(f"进程 PID: {proc.info['pid']} 已退出")
        _log("进程清理完成")


    def cover_folder(self):
        """覆盖安装最新版本的文件。"""
        # 从这里开始可能改动安装目录，失败后不能再假设旧版本完整
        self._install_touched = True
        if os.path.exists(self.changes_file_path):
            self._apply_incremental_update()
            return

        # 全量包：先移除旧资源目录，再整体覆盖
        _log("开始覆盖安装...")
        if not os.path.isdir(self.extract_folder_path):
            raise UpdatePayloadError(f"解压目录缺失，更新包内容不完整: {self.extract_folder_path}")
        entries = self._collect_copy_entries()
        self.progress.update(stage="正在备份旧文件...", detail="安装失败时将恢复旧版本")
        self._backup_existing(self.delete_folder_path)
        # PyInstaller 运行库由完整包管理，整体替换可清除旧版本遗留 DLL。
        # 只处理包内提供的运行库目录，不清扫安装根目录中的用户文件。
        if os.path.isdir(os.path.join(self.extract_folder_path, "_internal")):
            self._backup_existing(os.path.join(self.cover_folder_path, "_internal"))
        total = len(entries)
        self.progress.update(stage="正在安装新文件...", detail=f"共 {total} 个文件待写入", value=0, maximum=total)
        for index, (source, destination) in enumerate(entries, start=1):
            self._copy_file_with_retry(source, destination)
            if index % 25 == 0 or index == total:
                self.progress.update(value=index, detail=f"已写入 {index} / {total}")
        _log(f"覆盖安装完成，共写入 {len(entries)} 个文件")

    def _collect_copy_entries(self):
        """枚举解压目录内所有待复制文件，按稳定顺序返回 (源, 目标) 列表。"""
        entries = []
        for current_root, dir_names, file_names in os.walk(self.extract_folder_path):
            dir_names.sort()
            relative_root = os.path.relpath(current_root, self.extract_folder_path)
            destination_root = self.cover_folder_path if relative_root == "." else os.path.join(self.cover_folder_path, relative_root)
            for file_name in sorted(file_names):
                source = os.path.join(current_root, file_name)
                relative = os.path.relpath(os.path.join(destination_root, file_name), self.cover_folder_path)
                destination = self._resolve_path_within_root(self.cover_folder_path, relative)
                self._check_install_path(relative)
                entries.append((source, destination))
        return entries

    def _copy_file_with_retry(self, source, destination):
        """先写临时文件，再备份旧文件、原子改名；失败由本次安装统一恢复。"""
        self._ensure_directory(os.path.dirname(destination))
        descriptor, temporary = tempfile.mkstemp(prefix=".aalc-update-", dir=os.path.dirname(destination))
        os.close(descriptor)
        try:
            _retry_fs_op(lambda: shutil.copy2(source, temporary), f"写入 {os.path.basename(destination)}")
            if not self._backup_existing(destination):
                self._undo.append(("file", destination, None))
            _retry_fs_op(lambda: os.replace(temporary, destination), f"替换 {os.path.basename(destination)}")
        finally:
            with contextlib.suppress(OSError):
                os.remove(temporary)

    def _ensure_directory(self, path):
        missing = []
        while not os.path.exists(path):
            missing.append(path)
            path = os.path.dirname(path)
        for directory in reversed(missing):
            os.mkdir(directory)
            self._undo.append(("directory", directory, None))

    def _backup_existing(self, path):
        if not os.path.lexists(path):
            return False
        os.makedirs(self._backup_dir, exist_ok=True)
        backup = os.path.join(self._backup_dir, str(len(self._undo)))
        # 保留路径映射，异常退出后仍可根据 journal.jsonl 手动恢复备份。
        with open(os.path.join(self._backup_dir, "journal.jsonl"), "a", encoding="utf-8") as journal:
            journal.write(json.dumps({"target": path, "backup": backup}, ensure_ascii=False) + "\n")
        _retry_fs_op(lambda: os.replace(path, backup), f"备份 {path}")
        self._undo.append(("restore", path, backup))
        return True

    def _rollback(self):
        """恢复本进程已完成的变更；恢复失败时保留标记、日志与备份。"""
        restored = True
        self.progress.update(stage="正在恢复旧版本...", detail="安装未完成，撤销本次变更")
        for kind, target, backup in reversed(self._undo):
            try:
                if kind == "directory":
                    _retry_fs_op(lambda: os.rmdir(target), f"移除新目录 {target}")
                elif kind == "file":
                    if os.path.lexists(target):
                        _retry_fs_op(lambda: os.remove(target), f"移除新文件 {target}")
                else:
                    _retry_fs_op(lambda: os.replace(backup, target), f"恢复 {target}")
            except OSError as exc:
                restored = False
                _log(f"恢复失败: {target}: {exc}")
        self._install_touched = not restored
        return restored

    def _apply_incremental_update(self):
        changes = self._incremental_update_plan
        total = sum(map(len, changes.values()))
        self.progress.update(stage="正在应用增量更新...", value=0, maximum=total)
        done = 0
        for kind in ("deleted_dir", "deleted", "added_dir", "added", "modified"):
            for _name, *paths in changes[kind]:
                if kind in ("deleted_dir", "deleted"):
                    self._backup_existing(paths[0])
                elif kind == "added_dir":
                    self._ensure_directory(paths[0])
                else:
                    self._copy_file_with_retry(*paths)
                done += 1
                if done % 25 == 0 or done == total:
                    self.progress.update(value=done, detail=f"已处理 {done} / {total} 项变更")
        _log("增量更新完成")


    def _normalize_manifest_path(self, relative_path):
        """兼容带归档根目录前缀与普通相对路径的增量清单。"""
        if not isinstance(relative_path, str) or not relative_path or "\0" in relative_path:
            raise UpdateManifestError("更新清单包含空路径或非字符串路径")

        portable_path = relative_path.replace("\\", "/")
        if portable_path.startswith("/") or (len(portable_path) >= 2 and portable_path[1] == ":"):
            raise UpdateManifestError(f"更新清单包含绝对路径: {relative_path}")

        raw_parts = PurePosixPath(portable_path).parts
        if any(part == ".." or ":" in part for part in raw_parts):
            raise UpdateManifestError(f"更新清单包含非法相对路径: {relative_path}")

        parts = [part for part in raw_parts if part not in ("", ".")]
        if not parts:
            raise UpdateManifestError("更新清单包含空路径")

        archive_root_name = os.path.basename(os.path.normpath(self.extract_folder_path))
        if os.path.normcase(parts[0]) == os.path.normcase(archive_root_name):
            parts = parts[1:]

        if not parts:
            raise UpdateManifestError("更新清单不能指向归档根目录")

        return os.path.join(*parts)

    @staticmethod
    def _resolve_path_within_root(root_path, relative_path):
        """解析路径并拒绝经父目录或链接离开可信根目录的情况。"""
        try:
            root = Path(root_path).resolve()
            candidate = (root / relative_path).resolve()
        except OSError as exc:
            raise UpdateManifestError(f"无法解析更新清单路径: {relative_path}") from exc
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise UpdateManifestError(f"更新清单路径逃离可信根目录: {relative_path}") from exc
        return os.fspath(candidate)

    def _load_incremental_update_plan(self):
        try:
            with open(self.changes_file_path, "r", encoding="utf-8") as f:
                raw_changes = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateManifestError("无法读取更新清单") from exc

        if not isinstance(raw_changes, dict):
            raise UpdateManifestError("更新清单必须是对象")

        plan = {"deleted_dir": [], "deleted": [], "added_dir": [], "added": [], "modified": []}
        for operation, entries in raw_changes.items():
            if operation not in plan:
                raise UpdateManifestError(f"更新清单包含未知操作: {operation}")
            if not isinstance(entries, list):
                raise UpdateManifestError(f"更新清单操作 {operation} 必须是路径列表")

            for original_path in entries:
                normalized_path = self._normalize_manifest_path(original_path)
                self._check_install_path(normalized_path)
                target_path = self._resolve_path_within_root(self.cover_folder_path, normalized_path)
                if operation in {"added", "modified"}:
                    source_path = self._resolve_path_within_root(self.extract_folder_path, normalized_path)
                    plan[operation].append((original_path, source_path, target_path))
                else:
                    plan[operation].append((original_path, target_path))

        return plan

    @staticmethod
    def _check_install_path(relative_path):
        # 工作目录中的锁、备份和未完成标记不能被更新包覆盖或删除。
        if relative_path.replace("\\", "/").split("/")[0].lower() == TEMP_DIR_NAME.lower():
            raise UpdateManifestError("更新包不能修改 update_temp 工作目录")


    def cleanup(self):
        # 成功备份保留到下次更新，由 _update_work_dir 清理已完成会话。
        for path in (self.download_file_path, self.extract_folder_path, self.changes_file_path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                _log(f"清理失败: {path}: {exc}")


def relaunch_app(progress=None):
    """启动（新版本）AALC 主程序，替代旧的 ``cmd /c start`` 方案（无终端闪现）。"""
    exe_path = os.path.abspath("./AALC.exe")
    if not os.path.exists(exe_path):
        _log("未找到 AALC.exe，跳过自动重启")
        return
    _log("正在启动 AALC...")
    if progress is not None:
        progress.update(stage="正在启动 AALC...", detail="更新完成")
    try:
        subprocess.Popen(
            [exe_path],
            cwd=os.path.dirname(exe_path) or None,
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except OSError as exc:
        _log(f"AALC 启动失败: {exc}")
        show_error_dialog("AALC 更新完成", f"更新已完成，但自动启动 AALC 失败，请手动运行 AALC.exe。\n\n{exc}")


def _update_work_dir(work_dir=None):
    root = Path(TEMP_DIR_NAME).resolve()
    root.mkdir(exist_ok=True)
    if work_dir is None:
        for completed in root.glob("session-*/completed.flag"):
            if completed.parent.resolve().parent != root:
                continue
            try:
                shutil.rmtree(completed.parent)
            except OSError:
                # 并发更新时上一个会话的 exe 可能尚未退出，留待下次重试。
                with contextlib.suppress(OSError):
                    completed.touch()
        return tempfile.mkdtemp(prefix="session-", dir=root)
    candidate = Path(work_dir).resolve()
    if candidate.parent != root or not candidate.name.startswith("session-"):
        raise UpdatePayloadError("更新工作目录必须位于 update_temp/session-* 内")
    candidate.mkdir(exist_ok=True)
    return str(candidate)


def _maybe_relaunch_from_temp(work_dir):
    """引导：把更新器自身复制到 update_temp 再重启，避免安装根目录内的自身被覆盖。"""
    temp_path = work_dir
    file_path = sys.argv[0]
    destination_path = os.path.join(temp_path, os.path.basename(file_path))

    if os.path.normcase(os.path.abspath(file_path)) == os.path.normcase(os.path.abspath(destination_path)):
        return

    try:
        if os.path.exists("./Update.exe"):
            os.remove("./Update.exe")
        os.makedirs(temp_path, exist_ok=True)
        # 每次更新使用独立目录，有限重试用于应对扫描软件的短暂占用。
        _retry_fs_op(
            lambda: shutil.copy(file_path, destination_path),
            "复制更新器到临时目录",
            attempts=_SELF_COPY_MAX_ATTEMPTS,
            delay=_SELF_COPY_RETRY_DELAY_SECONDS,
        )
    except OSError as exc:
        _log(f"初始化更新器失败: {exc}")
        show_error_dialog("AALC 更新失败", f"初始化更新器失败: {exc}\n\n详细日志: {os.path.abspath(TEMP_DIR_NAME)}/{LOG_FILE_NAME}")
        relaunch_app()
        sys.exit(2)

    subprocess.Popen(
        [destination_path] + sys.argv[1:] + (["--work-dir", work_dir] if "--work-dir" not in sys.argv else []),
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        cwd=os.path.abspath("./"),
    )
    sys.exit(0)


def main():
    # 先拒绝未打包运行，再初始化日志：避免源码运行在开发目录里创建 update_temp/
    if not getattr(sys, "frozen", False):
        show_error_dialog("AALC 更新器", "更新程序只支持在打包后的 AALC 安装目录中运行。")
        sys.exit(1)

    args = parse_updater_args(sys.argv[1:])
    if not args.file_name:
        show_error_dialog("AALC 更新器", "缺少更新包文件名参数，更新已取消。")
        sys.exit(2)

    try:
        updater = Updater(args.file_name, no_progress_ui=args.no_progress_ui, work_dir=args.work_dir)
    except UpdatePayloadError as exc:
        show_error_dialog("AALC 更新失败", str(exc))
        sys.exit(2)

    log_path = _setup_logging()
    _log(f"更新器启动: argv={sys.argv[1:]}, apply_mode={args.apply_mode}, log={log_path}")
    _maybe_relaunch_from_temp(updater.work_dir)

    if args.progress_window and not args.no_progress_ui:
        progress = _RemoteProgress(args.progress_window)
        _log(f"复用更新进度窗口: hwnd={args.progress_window}")
    else:
        progress = UpdateProgressWindow(enabled=not args.no_progress_ui)
    try:
        succeeded = updater.run(apply_mode=args.apply_mode, parent_pid=args.parent_pid, progress=progress)
    except Exception as exc:
        _log(f"更新过程出现未处理异常: {exc}")
        _log(traceback.format_exc())
        show_error_dialog("AALC 更新失败", f"{exc}\n\n详细日志: {updater.log_path}", progress.get_window_handle())
        if not updater._install_touched:
            # 异常发生在改动安装目录之前，旧版本仍完整，自动恢复运行
            relaunch_app()
        succeeded = False
    finally:
        progress.close()
    _log(f"更新器退出，结果: {'成功' if succeeded else '失败'}")
    sys.exit(0 if succeeded else 2)


if __name__ == "__main__":
    main()
