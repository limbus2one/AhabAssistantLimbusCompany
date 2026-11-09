# 英文卡包截图素材

素材来自 MuMu 中的 Limbus Company 英文游戏画面，采集日期为 2026-10-08 至 2026-10-09。
截图先归一化为 2560×1440，再裁成 192×356 的卡面。

- `en/<编号>.png`：Normal / Hard 第1–5层图鉴中的95种卡包。
- `choice/<编号>.png`：选包页渲染与图鉴不同的卡面补充模板，来自实际选包截图。
- `catalog.json`：英文全名、中文译名、原有权重关键词、适用楼层和截图来源。
  `source.box` 与 `choice_source.box` 均使用归一化截图坐标，SHA-256 指向原始采集文件。
  图鉴原始截图位于本地 `logs/theme_pack_experiment/compendium_v3/`；选包来源路径相对项目目录。原始实验截图和日志不随素材包发布。
- `observe_positions.json`：Normal / Hard 各层的卡包行列，以及 2560×1440 下的列中心、首行中心和行距。

中文译名按游戏数据编号对应零协会的
[MirrorDungeonTheme-1.json](https://github.com/LocalizeLimbusCompany/LocalizeLimbusCompany/blob/231a8bcfc347273e7769b70ebc4f9f3e2fecaa5a/LLC_zh-CN/MirrorDungeonTheme-1.json)，
固定版本为 `231a8bcfc347273e7769b70ebc4f9f3e2fecaa5a`。
译名数据沿用其声明的 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 许可；
英文名按同版本 `EN/MirrorDungeonTheme-1.json` 映射。本表截取了本功能范围内的条目，未修改译名。
游戏画面的权利归 Project Moon 及相应权利人所有。

操作方式、完整名称表及实验范围见 [英文卡包观测](../doc/zh/英文卡包观测.md)。
