import time
from ctypes import windll

import cv2
import pyautogui
import pywintypes
import win32gui
import win32ui
from PIL import Image

from module.config import cfg
from module.game_and_screen import screen
from module.logger import log


class ScreenShot:
    @staticmethod
    def take_screenshot(gray: bool = True) -> Image.Image | None:
        """
        截取屏幕截图
        Args:
            gray (bool): 是否转为灰度图
        Returns:
            PIL.Image: 截图图像
        """
        if cfg.simulator:
            if cfg.simulator_type == 0:
                try:
                    return ScreenShot.mumu_screenshot(gray)
                except Exception as e:
                    log.debug(f"MUMU截图报错 {type(e).__name__}: {e}")
                    return None
            elif cfg.simulator_type == 10:
                try:
                    return ScreenShot.adb_screenshot(gray)
                except Exception as e:
                    log.debug(f"adb截图报错 {type(e).__name__}: {e}")
                    return None
        else:
            # 将窗口移动到屏幕可见区域，确保获取到完整的内容
            screen.handle.bring_window_into_view(not cfg.background_click)

        if cfg.background_click:
            try:
                return ScreenShot.background_screenshot(gray)
            except Exception as e:
                log.debug(f"后台截图报错 {type(e).__name__}: {e}")
                return None
        else:
            try:
                return ScreenShot.take_screenshot_gdi(gray)
            except Exception as e:
                msg = f"GDI截图失败，尝试使用pyautogui截图，错误信息：{e}"
                log.debug(msg)
                try:
                    return ScreenShot.take_screenshot_pyautogui(gray)
                except Exception as e2:
                    msg = f"pyautogui截图失败，错误信息：{e2}"
                    log.debug(msg)
                    return None

    @staticmethod
    def take_screenshot_gdi(gray: bool = True) -> Image.Image:
        """
        截取屏幕截图（避免HDR/系统渲染差异，直接从GDI获取）。
        Args:
            gray (bool): 是否转为灰度图
        Returns:
            PIL.Image: 截图图像
        """
        # 设置DPI感知，避免缩放影响
        windll.user32.SetProcessDPIAware()

        # 获取屏幕尺寸
        hdc_screen = windll.user32.GetDC(0)
        screen_x, screen_y, right, bottom = screen.handle.monitor_info["Monitor"]
        screen_width = right - screen_x
        screen_height = bottom - screen_y

        # 创建设备上下文
        hdc_mem = windll.gdi32.CreateCompatibleDC(hdc_screen)
        hbitmap = windll.gdi32.CreateCompatibleBitmap(hdc_screen, screen_width, screen_height)
        windll.gdi32.SelectObject(hdc_mem, hbitmap)

        # 使用BitBlt复制屏幕内容到内存DC
        SRCCOPY = 0x00CC0020
        windll.gdi32.BitBlt(
            hdc_mem,
            0,
            0,
            screen_width,
            screen_height,
            hdc_screen,
            screen_x,
            screen_y,
            SRCCOPY,
        )

        # 转换成PIL图像（需要 pywin32）
        import win32ui

        bmp = win32ui.CreateBitmapFromHandle(hbitmap)
        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)

        image = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        )

        # 清理
        windll.gdi32.DeleteObject(hbitmap)
        windll.gdi32.DeleteDC(hdc_mem)
        windll.user32.ReleaseDC(0, hdc_screen)

        # 转灰度
        if gray:
            image = image.convert("L")

        left, top, right, bottom = screen.handle.rect(True)
        crop_box = (
            max(left - screen_x, 0),
            max(top - screen_y, 0),
            min(right - screen_x, image.width),
            min(bottom - screen_y, image.height),
        )
        image = image.crop(crop_box)

        return image

    @staticmethod
    def take_screenshot_pyautogui(gray: bool = True) -> Image.Image:
        """
        截取屏幕截图,使用pyautogui。
        Args:
            gray (bool): 是否将图片转化为灰度图
        Returns:
            screenshot: 截取的屏幕截图。
        """

        """# 如果move参数为True，则尝试移动鼠标到屏幕左上角
        if move:
            try:
                pyautogui.moveTo(1, 1)
            except:
                pass"""

        # 设置进程的DPI感知，以确保截图在不同DPI设置下正确显示
        windll.user32.SetProcessDPIAware()
        # 进行全屏截图
        screenshot_temp = pyautogui.screenshot()
        if gray:
            # 将截图转换为灰度图像
            screenshot = screenshot_temp.convert("L")
        else:
            screenshot = screenshot_temp
        left, top, right, bottom = screen.handle.rect(True)
        crop_box = (
            max(left, 0),
            max(top, 0),
            min(right, screenshot.width),
            min(bottom, screenshot.height),
        )
        screenshot = screenshot.crop(crop_box)

        # 返回裁剪后的截图
        return screenshot

    @staticmethod

    def background_screenshot(
        gray: bool = True,
        region: tuple[int, int, int, int] | None = None,
    ) -> Image.Image:
        """后台截取窗口客户区或客户区中的指定区域。

        Args:
            gray:
                是否转换为灰度图。

            region:
                客户区内的截图区域：
                (left, top, right, bottom)

                例如：
                (100, 200, 500, 400)

                表示从客户区坐标 (100, 200) 开始，
                截取宽 400、高 200 的区域。

                为 None 时截取完整客户区。
        """
        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        save_bitmap = None
        old_obj = None
        hwnd = None

        try:
            hwnd = screen.handle.hwnd

            if screen.handle.isMinimized:
                raise ValueError("窗口最小化，无法截图")

            if screen.handle.isActive and screen.handle.isTransparent:
                screen.handle.set_window_transparent(False)

            # 获取客户区尺寸。
            rect = screen.handle.rect(client=True)
            client_width = rect[2] - rect[0]
            client_height = rect[3] - rect[1]

            if client_width <= 0 or client_height <= 0:
                raise ValueError(
                    f"窗口客户区尺寸无效："
                    f"{client_width}x{client_height}"
                )

            if region is None:
                left = 0
                top = 0
                right = client_width
                bottom = client_height
            else:
                left, top, right, bottom = region

                if not (
                    0 <= left < right <= client_width
                    and 0 <= top < bottom <= client_height
                ):
                    raise ValueError(
                        f"截图区域越界：region={region}，"
                        f"client_size=({client_width}, {client_height})"
                    )

            width = right - left
            height = bottom - top

            # 获取窗口 DC，用于创建兼容 DC 和兼容位图。
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            if not hwnd_dc:
                raise RuntimeError("GetWindowDC 失败")

            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            # 这里只创建区域大小的位图，而不是完整窗口大小。
            save_bitmap = win32ui.CreateBitmap()
            save_bitmap.CreateCompatibleBitmap(
                mfc_dc,
                width,
                height,
            )

            old_obj = save_dc.SelectObject(save_bitmap)

            save_hdc = save_dc.GetSafeHdc()

            # 将窗口客户区中的 (left, top)
            # 映射到目标位图中的 (0, 0)。
            result = windll.gdi32.SetViewportOrgEx(
                save_hdc,
                -left,
                -top,
                None,
            )
            if not result:
                raise RuntimeError("SetViewportOrgEx 失败")

            # 3 = PW_CLIENTONLY | PW_RENDERFULLCONTENT
            success = windll.user32.PrintWindow(
                hwnd,
                save_hdc,
                3,
            )

            if not success:
                raise RuntimeError("PrintWindow 截图失败")

            bmp_info = save_bitmap.GetInfo()
            bmp_data = save_bitmap.GetBitmapBits(True)

            image = Image.frombuffer(
                "RGB",
                (
                    bmp_info["bmWidth"],
                    bmp_info["bmHeight"],
                ),
                bmp_data,
                "raw",
                "BGRX",
                0,
                1,
            )

            if gray:
                image = image.convert("L")

            # 避免返回的图片继续依赖底层位图缓冲区。
            return image.copy()

        finally:
            # 位图被删除前，必须先从内存 DC 中移出。
            if save_dc is not None and old_obj is not None:
                try:
                    save_dc.SelectObject(old_obj)
                except Exception:
                    pass

            if save_bitmap is not None:
                try:
                    win32gui.DeleteObject(save_bitmap.GetHandle())
                except Exception:
                    pass

            if save_dc is not None:
                try:
                    save_dc.DeleteDC()
                except Exception:
                    pass

            # mfc_dc 包装的是 hwnd_dc，不在这里 DeleteDC，
            # 最终使用 ReleaseDC 释放窗口 DC。
            if hwnd_dc is not None and hwnd is not None:
                try:
                    win32gui.ReleaseDC(hwnd, hwnd_dc)
                except Exception:
                    pass


    @staticmethod
    def screenshot_benchmark(test_time: int = 10) -> tuple[bool, float]:
        """
        截图性能测试

        Args:
            test_time (int): 测试次数，默认为10次

        Returns:
            tuple (bool, str):
            - bool: 测试是否成功
            - float: 平均每次截图耗时（毫秒）
        """

        try:
            screen.handle.init_handle()
            if screen.handle.hwnd == 0:
                log.info("未找到游戏窗口，无法进行截图性能测试")
                return False, 0.0
            start_time = time.time()
            for i in range(test_time):
                ScreenShot.take_screenshot(gray=False)
            end_time = time.time()
            avg_time = (end_time - start_time) / test_time * 1000  # 转为毫秒
            log.info(f"截图性能测试: {test_time}次截图平均耗时 {avg_time:.2f} ms")
            return True, avg_time
        except Exception as e:
            log.info("截图性能测试失败")
            log.debug(f"截图性能测试报错: {e}")
            return False, 0.0

    @staticmethod
    def mumu_screenshot(gray: bool = True) -> Image.Image:
        """
        截图

        Args:
            gray (bool): 是否转换为灰度图，默认为True

        Returns:
            Image.Image: 截图图像
        """
        from module.automation.input_handlers.simulator.mumu_control import MumuControl

        if MumuControl.connection_device is not None:
            image = MumuControl.connection_device.screenshot()
            mumu_image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            mumu_image = Image.fromarray(mumu_image)
            if gray:
                mumu_image = mumu_image.convert("L")
            return mumu_image
        else:
            log.error("未连接到MuMu模拟器")
            raise ConnectionError("未连接到MuMu模拟器")

    @staticmethod
    def adb_screenshot(gray: bool = True) -> Image.Image:
        """
        截图

        Args:
            gray (bool): 是否转换为灰度图，默认为True

        Returns:
            Image.Image: 截图图像
        """
        from module.automation.input_handlers.simulator.simulator_control import (
            SimulatorControl,
        )

        if SimulatorControl.connection_device is not None:
            image = SimulatorControl.connection_device.screenshot()
            image = Image.fromarray(image)
            if gray:
                image = image.convert("L")
            return image
        else:
            log.error("未连接到adb设备")
            raise ConnectionError("未连接到adb设备")
