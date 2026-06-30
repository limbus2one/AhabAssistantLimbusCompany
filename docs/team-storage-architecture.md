# 队伍储存相关代码架构

team_num 是项目的编队需要
team_number 是游戏内的队伍序号（1~20）


> 基于 `main` 分支，本文档描述队伍（Team）设置数据的**定义 → 持久化 → 导入导出 → UI编辑**完整链路。

---

## 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                           UI 层                                      │
│  [app/team_setting_card.py](../app/team_setting_card.py)  ─── 队伍设置编辑卡片                       │
│       │ 读写 DOM                                                    │
│       ▼                                                              │
├─────────────────────────────────────────────────────────────────────┤
│                       导入导出层                                      │
│  [module/config/team_import_export.py](../module/config/team_import_export.py)  ─── 编队码/YAML 导入导出        │
│       │ 调用 cfg.config.teams                                       │
│       ▼                                                              │
├─────────────────────────────────────────────────────────────────────┤
│                      配置管理层                                       │
│  [module/config/config.py](../module/config/config.py)  ─── Config 单例 (teams, 队列, 迁移)         │
│       │ 属性访问                                                    │
│       ▼                                                              │
├─────────────────────────────────────────────────────────────────────┤
│                      数据模型层                                       │
│  [module/config/config_typing.py](../module/config/config_typing.py)  ─── TeamSetting (Pydantic)          │
│       │ 序列化                                                      │
│       ▼                                                              │
│                     config.yaml (磁盘文件)                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 一、数据模型层

### [module/config/config_typing.py](../module/config/config_typing.py)

#### `TeamSetting(BaseModel)` — 单个队伍的所有设置字段

| 分类 | 字段 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| **基础** | `team_number` | `int` | `1` | 队伍序号 (1~20) |
| | `team_system` | `int` | `0` | 队伍使用的体系 |
| | `remark_name` | `Optional[str]` | `None` | 队伍备注名 |
| **罪人选择** | `chosen_sinners` | `List[int]` | `[0]*12` | 12 名罪人选中状态 |
| | `sinner_order` | `List[int]` | `[0]*12` | 罪人选中顺序 |
| | `sinners_be_select` | `int` | `0` | 已选罪人数量 |
| **饰品体系(弃置)** | `system_burn` ~ `system_blunt` | `bool` (×10) | `False` | 弃置指定体系饰品 |
| **商店策略** | `shop_strategy` | `int` | `0` | 商店策略 |
| | `do_not_buy/fuse/sell/heal/enhance` | `bool` (×5) | `False` | 商店禁止操作 |
| | `only_aggressive_fuse` | `bool` | `False` | 仅激进合成 |
| | `only_system_fuse` | `bool` | `False` | 只合成体系饰品 |
| | `ignore_shop` | `List[int]` | `[0]*5` | 忽略商店楼层 |
| | `max_keyword_refresh` | `int` | `1` | 定向刷新最大次数 |
| | `max_normal_refresh` | `int` | `1` | 普通刷新最大次数 |
| **战斗策略** | `avoid_skill_3` | `bool` | `False` | 避免使用3技能 |
| | `defense_first_round` | `bool` | `False` | 第一回合全员防御 |
| | `re_formation_each_floor` | `bool` | `False` | 每层重新编队 |
| **星光加成** | `use_starlight` | `bool` | `False` | 开局星光换钱 |
| | `opening_bonus` | `List[int]` | `[1,1,1,1,0×6]` | 10 个星光加成等级 |
| **二级体系** | `second_system` + 相关 | `bool/int/List` | 多个 | 第二体系设置 |
| **奖励卡** | `reward_cards` | `bool` | `False` | 奖励卡优先度 |
| **观测饰品** | `observe_ego_gift` | `bool` | `False` | 观测 EGO 饰品 |
| | `observe_ego_gift_selected` | `List[str]` | `[]` | 已选饰品列表 |
| **编队码** | `use_team_code` | `bool` | `False` | 使用编队码 |
| | `team_code` | `str` | `""` | 编队码字符串 |
| **主题包** | `use_custom_theme_pack_weight` | `bool` | `False` | 自定义主题包权重 |
| **统计** | `total_mirror_time_hard/normal` | `List[float]` | `[0,0,0]` | 镜牢用时统计 |
| | `mirror_hard/normal_count` | `int` | `0` | 镜牢次数统计 |

#### `ConfigModel(BaseModel)` — 全局配置模型

关键队伍相关字段（第 505~521 行）：

- `teams_be_select: List[bool]` — 旧字段：队伍是否被选中
- `teams_order: List[int]` — 旧字段：队伍顺序
- `teams_active_queue: List[int]` — **新字段**：镜牢启用队伍执行队列（单一事实源）
- `teams: dict[str, TeamSetting]` — 所有队伍设置，key 为 `"1"`~`"20"`

---

## 二、配置管理层

### [module/config/config.py](../module/config/config.py)

#### `Config` 类（`SingletonMeta` 单例）

负责 `config.yaml` 的完整生命周期管理。

**队伍相关方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `get_team_numbers()` | #340 | 获取已有配置的队伍编号（排序列表） |
| `get_all_team_slots()` | #362 | 返回 1..MAX_TEAM_COUNT 的固定槽位 |
| `ensure_team_slots()` | #364 | 确保配置中存在 1..MAX_TEAM_COUNT 的固定槽位 |
| `_normalize_team_queue()` | #353 | 去重、过滤无效编号，返回干净队列 |
| `migrate_legacy_team_queue()` | #367 | 从旧字段 (`teams_order`/`teams_be_select`) 迁移队列 |
| `_sync_legacy_team_state()` | #398 | 将队列写回旧字段以保持兼容 |
| `normalize_and_sync_team_state()` | #414 | 归一化并对齐旧字段（可持久化） |
| `reindex_team_queue()` | #425 | 队伍编号压缩后重新索引 |
| `rotate_team_queue()` | #435 | 队首轮转到队尾 |
| `remove_team_from_queue()` | #443 | 从队列移除指定队伍 |
| `clear_team_queue()` | #449 | 清空所有启用的队伍队列 |
| `set_team_enabled()` | #453 | 启用/禁用指定队伍 |

**保存机制：**

- `set_value()` (#484) → 设置值 → `_schedule_save()` → 1s 防抖合并
- `save(instant=True)` → 立即写盘
- 后台线程 `_writer_loop` 监听 `_writer_event` 异步落盘
- `atexit.register(self.flush)` 确保退出时数据不丢失

### [module/config/__init__.py](../module/config/__init__.py)

模块入口，创建全局单例：

```python
cfg = Config(VERSION_PATH, EXAMPLE_PATH, CONFIG_PATH)        # 队伍设置
theme_list = Theme_pack_list(...)                             # 主题包权重
```

通过 `from module.config import cfg, theme_list` 即可在任意模块访问配置。

---

## 三、导入导出层

### [module/config/team_import_export.py](../module/config/team_import_export.py)

提供队伍设置的 YAML 文件导入导出功能。

| 函数 | 行号 | 签名 | 说明 |
|------|------|------|------|
| `generate_team_export_filename()` | #13 | `(team_num: int) -> str` | 生成导出文件名（含备注名和日期） |
| `export_team_settings()` | #27 | `(team_num: int, file_path: str) -> bool` | 导出队伍设置到 YAML 文件，包含主题包权重 |
| `import_team_settings()` | #65 | `(file_path: str, team_num: int) -> tuple[TeamSetting?, dict?, list[str]]` | 从 YAML 导入，返回 `(设置, 主题包权重, 缺失字段)`；缺失字段自动用默认值补全 |
| `apply_team_settings()` | #105 | `(team_num: int, team_setting: TeamSetting, theme_pack_weight: dict?) -> None` | 将导入的设置写入 `cfg.config.teams` 并保存 |

**导入容错：** `import_team_settings` 对缺失字段使用 `model_construct` 容错构造，不会因少字段而拒绝整个文件。

---

## 四、UI 编辑层

### [app/team_setting_card.py](../app/team_setting_card.py)

#### `TeamSettingCard(QFrame)` — 主设置卡片 (#62)

`__init__` 流程：
1. `__init_widget()` — 创建所有 UI 控件（导航面板、滚动区域、罪人网格、按钮）
2. `__init_card()` — 创建 12 个 `SinnerSelect`、体系选择器、饰品复选框等
3. `__init_layout()` — 组装布局
4. 从 `cfg.config.teams[f"{team_num}"]` 深拷贝获取 `TeamSetting`
5. `read_settings()` / `refresh_starlight_select()` — 填充 UI

**关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `setting_team()` | #339 | mediator 信号槽：接收单项修改并更新内存中的 `team_setting` |
| `save_team_setting()` | #378 | 将 `team_setting` 写入 `cfg.config.teams` 并持久化 |
| `cancel_team_setting()` | ~#380 | 放弃修改，从配置文件重新加载 |
| `read_settings()` | #439 | 从 `team_setting` 读取值填充 UI 控件 |
| `on_export_settings()` | #497 | 导出按钮回调 → 调用 `export_team_settings()` |
| `on_import_settings()` | #527 | 导入按钮回调 → 调用 `import_team_settings()` + `apply_team_settings()` |
| `refresh_starlight_select()` | #398 | 刷新 10 个星光加成选择器的状态 |
| `refresh_sinner_order()` | #429 | 刷新罪人选中的顺序标签 |

#### 内部组件类

| 类 | 行号 | 说明 |
|------|------|------|
| `CustomizeSettingsModule` | #616 | 自定义设置面板（体系选择、商店、战斗、星光等） |
| `SystemIconButton` | #1056 | 饰品体系图标按钮 |
| `PreviewGiftLabel` | #1106 | 观测饰品预览标签 |
| `ObserveEgoGiftModule` | #1132 | 观测 EGO 饰品模块 |
| `CustomizeInfoModule` | #1422 | 编队统计信息面板 |

---

## 五、数据流总结

```
用户操作 (UI)
    │
    ▼
TeamSettingCard.setting_team(data_dict)     ← mediator 信号槽单项修改
    │
    ├─ 修改 self.team_setting (内存副本)
    │
    ├─ 用户点击保存
    │       │
    │       ▼
    │  save_team_setting()
    │       │
    │       ▼
    │  cfg.set_value("1", self.team_setting, config_obj=cfg.config.teams)
    │       │
    │       ▼
    │  _schedule_save() → _writer_loop → _save_config() → config.yaml (磁盘)
    │
    └─ 用户点击取消
            │
            ▼
       cancel_team_setting()
            │
            ▼
       从 cfg.config.teams 重新深拷贝恢复
```

**持久化格式：** `config.yaml` 中 `teams` 字段为 `{"1": {...字段}, "2": {...字段}, ...}`，每个字段值由 `TeamSetting.model_dump()` 序列化。旧字段 `teams_be_select` / `teams_order` 由 `_sync_legacy_team_state` 保持同步，新逻辑以 `teams_active_queue` 为单一事实源。
