from functools import lru_cache

from module.logger import log


@lru_cache(maxsize=1)
def _get_ocr():
    """首次真正识别文字时才导入 RapidOCR 并创建 ONNX 会话。"""
    from module.ocr.ocr import OCR

    return OCR(log)


class _LazyOCR:
    def __getattr__(self, name):
        return getattr(_get_ocr(), name)


ocr = _LazyOCR()
