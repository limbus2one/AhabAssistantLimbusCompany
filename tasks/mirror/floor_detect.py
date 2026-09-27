# -*- coding: utf-8 -*-
# 镜牢楼层识别（颜色计数）标定参数与掩码
# 独立轻模块：不依赖 automation/config，便于离线测试导入
# 标定来源：1080p/900p 实况样本（tests/fixtures/mirror_floor/），改动参数需同步更新对应测试

import numpy as np

# 楼层进度条横带 ROI（1440 高度基准坐标，由 match_color_regions 按实际截图高度自动缩放；
# x 取全宽以兼容 zh/en 面板布局差异）
FLOOR_BAND_ROI_1440 = (0, 495, 2560, 590)

# CLEAR 标记 = 金色菱形上的红色字母。判别目标是红色字母本身，不是金色菱形底
# （金色底 H≈24-26 与当前层标记同色，按色相会把"当前位置"误计入，参见 #927 分析）。
# 红色占优门限实测：红字母 R-max(G,B) p95≈190；金色底/当前标记 p95≈40；木纹 p95≈21。
FLOOR_RED_DOM_MIN = 60
# 亮度下限：排除面板外的暗红壁纸（R≈120-160）
FLOOR_RED_R_MIN = 190
# CLEAR 标记连通域面积下限（1440 高度基准像素数；900p 实测约 489px）
FLOOR_CLEAR_MIN_AREA = 300


def clear_badge_mask(roi_bgr):
    """CLEAR 标记掩码：红色占优（R-max(G,B)>=60）且足够亮（R>=190）的像素置 255。"""
    b = roi_bgr[:, :, 0].astype(np.int16)
    g = roi_bgr[:, :, 1].astype(np.int16)
    r = roi_bgr[:, :, 2].astype(np.int16)
    m = ((r - np.maximum(g, b)) >= FLOOR_RED_DOM_MIN) & (r >= FLOOR_RED_R_MIN)
    return m.astype(np.uint8) * 255


# 适用状态说明：
# - 卡包界面（主识别点）：未进入的层都是暗菱形，亮色只有已通关 CLEAR 的红字母
#   -> 计数+1=当前层，精确。
# - 地图探索页：当前层的金色"当前位置"菱形不含红字母，红字母掩码同样只数出已通关层，
#   但生产上地图页不做识别，直接以 pack_count 为准（见 mirror.py 地图分支）。
