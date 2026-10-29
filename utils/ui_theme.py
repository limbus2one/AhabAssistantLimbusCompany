"""主界面与独立更新器共用的样式数据；保持无 Qt、配置及其他运行时依赖。"""

THEME_COLOR = "#9c080b"

MAIN_WINDOW_STYLES = {
    "dark": {"bg_color": "#1c1c1c"},
    "light": {"bg_color": "#ffffff"},
}

UPDATER_WINDOW_STYLES = {
    "dark": {
        **MAIN_WINDOW_STYLES["dark"],
        "dark_title": True,
        "text_color": "#ffffff",
        "track_color": "#2d2d2d",
        "bar_color": THEME_COLOR,
    },
    "light": {
        **MAIN_WINDOW_STYLES["light"],
        "dark_title": False,
        "text_color": "#1c1c1c",
        "track_color": "#ebebeb",
        "bar_color": THEME_COLOR,
    },
}
