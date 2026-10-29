# AALC 项目架构

| 位置 | 职责 |
| --- | --- |
| `main.py` / `main_dev.py` | 正式启动入口 / 开发热重载入口。 |
| `app/my_app.py`、`app/*_interface.py` | 主窗口、功能页、设置页、托盘及更新确认。 |
| `app/base_tools.py`、`app/base_combination.py`、`app/card/`、`app/widget/` | 基础控件、组合卡片和弹窗。 |
| `app/mediator.py` | 页面与任务线程之间的信号。 |
| `tasks/` | 日常、战斗、镜牢等任务编排与执行。 |
| `module/automation/`、`module/game_and_screen/`、`module/ocr/` | 输入、截图、窗口管理及识别。 |
| `module/config/`、`module/logger/` | 配置读写与日志。 |
| `module/update/`、`module/resource_sync/` | 软件更新检查/下载与图片资源同步。 |
| `updater.py` / `updater.spec` | 独立更新器及其无控制台打包配置。 |
| `assets/`、`i18n/`、`tests/` | 资源与文档、翻译、回归测试。 |

## 主体风格文件位置

- [`app/common/ui_config.py`](../../../app/common/ui_config.py)：主界面字体、深浅色窗口背景、标题栏、边框、卡片和导航等 QSS；修改主体风格优先查看这里。
- [`app/my_app.py`](../../../app/my_app.py)：`setThemeColor()` 设置品牌色，`setTheme()` 与 `_apply_theme_styles()` 应用主题。
- [`assets/styles/`](../../styles/)：内嵌 Markdown 页面的深浅色 CSS，由 `app/page_card.py` 加载。
- 专用控件的局部样式还位于各自的 `setStyleSheet()` 或绘制方法中，详见 [UI 组件说明](ui_components.md)。

独立更新器使用 Windows 标准对话框和进度条，只显示解压、校验、关闭进程、安装、清理等阶段，不导入主界面的 Qt 样式。完整包和增量包沿用原版安装流程，不提供自动备份或回滚；错误写入 `update_temp/updater.log` 并弹窗提示。

开发与构建命令见[开发指南](develop_guide.md)和[构建指南](build_guide.md)。更新回归运行 `python -m pytest tests/test_update.py -q`；窗口显示及重启需另做打包验证。
