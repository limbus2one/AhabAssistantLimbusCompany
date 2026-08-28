from app.common.ui_config import _colorref


def test_colorref_uses_windows_bgr_byte_order():
    assert _colorref("#123456") == 0x563412
