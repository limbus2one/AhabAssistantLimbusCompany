# 项目图片识别机制文档

## 坐标系统

图片的坐标原点为 **游戏窗口客户区的左上角**。

find_element 返回的坐标是当前游戏窗口客户区坐标，而硬编码的坐标基于 2560×1440 分辨率。因此，在使用find_element返回的坐标时需要**缩放**。

## 图片路径与匹配机制

### 路径结构

图片按 **主题**（`dark` / `default`）和 **语言**（`zh_cn` / `en` / `share`）组织，目录层级为 `{主题}/{语言}/`。

搜索优先级如下（按顺序尝试，命中即停）：
`dark/zh_cn` $\to$ `dark/en` $\to$ `dark/share` $\to$ `default/zh_cn` $\to$ `default/en` $\to$ `default/share`

### 主题判定

1. `dark` 存在且命中 → 主题为 `dark`
2. `dark` 不存在，`default` 命中 → 主题无法确定，仍需检查两种主题
3. `dark` 存在但未命中，`default` 存在且命中 → 主题为 `default`，移除 `dark` 路径
4. `dark` 存在但未命中，`default` 存在但未命中 → 主题无法确定

### 语言判定

5. `zh_cn` 存在且命中 → 语言为 `zh_cn`
6. `zh_cn` 不存在，`en` 存在且命中 → 语言为 `en`；仅 `share` 存在且命中 → 语言无法确定
7. `zh_cn` 存在但未命中，`en` 或 `share` 存在且命中 → 语言为 `en`
8. `zh_cn` 存在但未命中，`en` 和 `share` 均未命中 → 语言无法确定

### 判定流程

**主题和语言判断相互独立**，但优先使用已确定的维度缩小搜索范围：

| 场景 | 行为 |
|------|------|
| 主题 `dark`，语言 `zh_cn` | 直接匹配 `dark/zh_cn`，命中即停 |
| 主题 `default`，语言 `zh_cn` | 移除 `dark/*`路径，匹配 `default/zh_cn` |
| 主题 `dark`，语言 `en` | 移除 `dark/zh_cn` 和 `default/zh_cn`，匹配 `dark/en` |
| 主题 `default`，语言 `en` | 移除 `dark/*` 和 `default/zh_cn`，匹配 `default/en` |

主题和语言均已确定时，首个匹配即停止搜索；否则收集所有路径的匹配结果，汇总后进行主题/语言判定，再返回首个命中。

### 并发匹配机制

启动时根据游戏语言初始化图片搜索路径。

匹配并非串行"先 `dark` 后 `default`"：所有存在目标图片的路径**并发匹配**，汇总结果后比较 `dark` 与 `default` 的得分：
- 仅 `dark` 命中 → 主题为 `dark`
- `dark` 存在但未命中、`default` 命中 → 主题为 `default`，移除 `dark` 路径
- 两者均命中 → 比较最高匹配值，差距超过阈值（0.15）时胜出方确定为当前主题

## 图像识别

### 图片模板

在使用 `find_element` 方法时，可以使用两种类型的图片模板：

1. **含黑幕的核心图像元素** - 2560×1440 等大尺寸图片，包含黑幕背景和核心UI元素
2. **仅核心图像元素** - 仅包含核心UI元素的矩形图片

例如：
![window_assets](../../images/default/share/home/window_assets.png) ![pass_coin](../../images/default/share/pass/pass_coin.png)

### 图片处理逻辑

| 对比维度 | 黑幕+核心图像元素 | 仅核心图像元素 |
|----------|-------------------|----------------|
| **搜索区域** | 自动选择 bbox(非黑幕区域) | 默认全屏 |

### 图片制作准则

1. **基准分辨率**：所有图片资源steam端**全屏 2560×1440** 分辨率下截取
2. **文件命名**：含黑幕的完整截图使用 `assets` 后缀，如果只需要bbox用 `bbox` 后缀，只有核心图像的截图不使用 `assets` 后缀
3. **优先使用含黑幕的完整截图**：便于系统自动计算有效区域，适应UI变化

### 参数

- 匹配阈值：`threshold=0.8` (80% 相似度)

### 脚本
- `scripts/match_steam_image.py`：运行游戏，在终端中运行。截图保存在项目根目录下 `screenshot.png`
- `scripts/image_similarity.py`: 检查图片相似度

## E.G.O 饰品识别

已连接游戏时，调用专用接口识别当前截图：

```python
from module.automation import auto

gifts = auto.find_ego_gifts(take_screenshot=True, conf=0.35)
for gift in gifts:
    print(gift.gift_id, gift.name_zh, gift.system, gift.confidence, gift.xyxy, gift.center)
```

已有图片时可直接调用底层接口：

```python
from module.yolo import yolo

gifts = yolo.find_gifts(image, conf=0.35)
```

输入为 PIL 图片或 RGB HWC 数组；OpenCV 的 BGR 图片需要先转 RGB。
返回按置信度降序排列的 `Detection` 列表，
自动排除 `enhance_1` / `enhance_2` 强化标记。`gift_id` 是稳定饰品 ID 字符串，
`name_zh` / `name_en` 是中英文名，`system` 是体系（通用饰品为 `None`），`tier` 是等级。

`xyxy` 和 `center` 均为**输入截图的像素坐标**，不是 2560×1440 基准坐标。
`auto.find_ego_gifts(my_crop=(x1, y1, x2, y2))` 只按中心点过滤结果，不裁剪输入。
无结果或模型/截图不可用时返回 `[]`，失败原因记录在日志中。该接口只识别，不点击；
检测完成后自动化层会把缓存帧恢复为灰度，供后续模板匹配使用。

模型与元数据使用 `assets/models/ego_gift_yolo26n.onnx` 和 `ego_gift_classes.yaml`，
来源为 `yoloego`：381 类饰品与 2 类强化标记，640×640 输入。
运行时复用 ONNX Runtime，无需安装训练工程、PyTorch 或 Ultralytics。

### MuMu 截图验证

在仓库根目录、`aalc` 环境下运行（MuMu 已连接 ADB，设备序列号以实际连接为准）：

```powershell
python scripts/check_ego_gifts.py --serial emulator-5554
```

也可用 `--image screenshot.png` 验证已有截图，用 `--conf 0.5` 调整阈值。
原图、英文标注图和包含 ID、中英文名、体系、置信度、坐标、模型哈希的 JSON
默认保存到 `logs/ego_gifts/`；可用 `--output` 指定目录，重复运行会覆盖该目录下的同名文件。
耗时字段包含首次模型加载。脚本仅截取当前画面，不启动游戏或操作饰品选择。
