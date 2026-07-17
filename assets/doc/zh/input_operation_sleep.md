# AALC 与参照项目输入操作 Sleep 审计

本文整理 AALC 当前输入链路中与点击、拖动、移动、滚轮和键盘操作有关的等待时间，并与 MaaFramework、边狱巴士自动化编辑器进行对比。覆盖：

- MuMu 模拟器原生控制
- 通用模拟器 ADB / minitouch 控制
- PC 前台 PyAutoGUI 控制
- PC 后台 Win32 消息控制
- PC 窗口移动控制
- MaaFramework 的 MuMu `EmulatorExtras` 输入
- 边狱巴士自动化编辑器当前后台输入模式

审计基于当前工作区源码和本地程序文件。除非特别说明，文中的时间单位均为秒。

## 1. 统计口径

输入操作中的等待分为四类：

| 类型 | 说明 | 是否计入“额外等待” |
|---|---|---|
| 动作节流 | 限制两个高层鼠标动作的执行频率 | 是 |
| 固定等待 | 点击按住、到达终点后停留、抬起后等待 | 是 |
| 动作持续时间 | `drag_time`、轨迹点之间的移动时间 | 单独列出 |
| 异常或暂停等待 | 暂停轮询、连接重试、模拟器启动等待 | 不计入正常热路径 |

下列耗时不在本文的固定延迟公式中：

- 图片识别、截图和 `screenshot_interval`
- ADB 命令、MuMu IPC、Win32 API 自身的执行和阻塞时间
- Python、操作系统线程调度误差
- 游戏收到输入后更新画面的时间

因此，本文给出的都是源码可以确定的等待下界或近似值，不是完整的端到端耗时。

## 2. 当前配置与输入后端选择

当前 [`config.yaml`](../../../config.yaml) 中与输入延迟有关的配置为：

```yaml
win_input_type: background
mouse_action_interval: 0.2
mouse_down_duration: 0.05
use_post_message: false
simulator: true
simulator_type: 0
```

输入后端由 [`Automation.init_input`](../../../module/automation/automation.py#L43) 选择：

| 条件 | 实际后端 |
|---|---|
| `simulator=true` 且 `simulator_type=0` | `MumuControl` |
| `simulator=true` 且 `simulator_type!=0` | `SimulatorControl`，ADB + minitouch |
| `simulator=false` 且 `win_input_type=foreground` | `Input`，PyAutoGUI 前台输入 |
| `simulator=false` 且 `win_input_type=background` | `BackgroundInput`，Win32 消息输入 |
| `simulator=false` 且 `win_input_type=window_move` | `WindowMoveInput`，通过移动窗口改变相对点击位置 |

## 3. 所有后端之前的高层动作节流

通过 `click_element` 或 `mouse_action_with_pos` 发起的 `click`、`drag`、`drag_down` 和 `scroll`，在进入具体输入后端之前会经过统一节流。源码位于 [`automation.py`](../../../module/automation/automation.py#L217)：

```python
if cfg.mouse_action_interval and interval == 0.5:
    interval = cfg.mouse_action_interval

if self.last_click_time == 0:
    self.last_click_time = time.time()
if time.time() - self.last_click_time < interval:
    time.sleep(interval)
```

当前默认调用会把 `interval=0.5` 替换为配置的 `mouse_action_interval=0.2`。

需要特别注意：这里不是“补足到 200ms”，而是在间隔不足时直接睡完整的 200ms。例如距离上次动作只过去 150ms，仍会再睡 200ms，而不是只睡 50ms。

其他细节：

- 第一次高层鼠标动作也会触发完整的 `interval` 等待。
- 多目标点击会递归传入 `interval=1`，目标之间可能直接睡完整的 1 秒。
- 直接调用 `input_handler.mouse_click()` 等底层方法不会经过这一层。
- `last_click_time` 虽然名为“点击时间”，拖动和滚轮也会更新它。

## 4. MuMu 模拟器原生控制

相关源码：[`mumu_control.py`](../../../module/automation/input_handlers/simulator/mumu_control.py#L812)。

### 4.1 点击与键盘

| 操作 | Sleep 顺序 | 固定等待 |
|---|---|---:|
| `key_press` | `key_down` → 15ms → `key_up` | 15ms |
| `click` / `mouse_click` | `down` → 15ms → `up` → 35ms | 50ms/次 |
| `long_click(duration)` | `down` → `duration` → `up` → 50ms | `duration + 50ms` |
| `input_text` | 无显式 sleep | 0 |

`mouse_click(times=N)` 会完整调用 `click` N 次，所以底层固定等待为 `N × 50ms`。

### 4.2 `swipe`

[`swipe`](../../../module/automation/input_handlers/simulator/mumu_control.py#L898) 的逻辑为：

```python
for point in points:
    self.down(*point)
    time.sleep(duration / min_distance)

time.sleep(0.2)
self.up()
time.sleep(0.050)
```

设生成的轨迹点数量为 `N`，则显式等待为：

```text
N × duration / min_distance + 200ms + 50ms
```

其中最后的 250ms 是与轨迹点数量无关的固定等待：到达终点后按住 200ms，抬起后再等 50ms。

`mouse_drag_down` 固定调用 `swipe(..., duration=0.4, min_distance=10)`，所以每个轨迹点等待 40ms，完整等待为：

```text
N × 40ms + 250ms
```

这里的 `duration=0.4` 不是整个拖动严格持续 400ms，而是参与计算“每个轨迹点等待 40ms”。总移动时间仍取决于 `insert_swipe` 生成了多少点。

### 4.3 `mouse_drag`

[`mouse_drag`](../../../module/automation/input_handlers/simulator/mumu_control.py#L970) 对每个轨迹点固定等待 20ms，抵达终点后再等待：

```text
hold = max(500ms, drag_time × 0.3)
```

设轨迹点数量为 `N`，总显式等待为：

```text
N × 20ms + hold
```

这个实现中，`drag_time` 不控制轨迹移动速度，只控制抵达终点后的按住时间；通常 `drag_time <= 1.667s` 时，终点固定按住 500ms。抬起后没有额外 sleep。

### 4.4 `mouse_drag_link`

设第 `i` 段生成 `N_i` 个轨迹点、`min_distance=M`、`drag_time=T`，等待时间为：

```text
Σ(N_i × T / M) + 500ms + 50ms
```

即各段轨迹移动结束后，固定在最终点按住 500ms，抬起后再等待 50ms。

### 4.5 无实际输入的占位操作

MuMu 当前的 `mouse_scroll`、`mouse_to_blank` 和 `mouse_move` 是占位实现，没有 sleep，也不会发送对应输入。`mouse_click_blank` 会转调普通点击，因此每次仍有 50ms 固定等待。

## 5. 通用模拟器：ADB + minitouch

高层源码：[`simulator_control.py`](../../../module/automation/input_handlers/simulator/simulator_control.py#L298)。  
minitouch 封装：[`pyminitouch/actions.py`](../../../module/automation/input_handlers/simulator/pyminitouch/actions.py#L12)。

### 5.1 ADB 操作

| 操作 | 实现 | 显式 sleep |
|---|---|---:|
| `mouse_click` | `adb shell input tap` | 0 |
| `key_press` | `adb shell input keyevent` | 0 |
| `input_text` | `adb shell input text` | 0 |

这里的“0”仅表示项目没有额外调用 `sleep`。`adbutils` 会同步等待 shell 命令完成，因此仍有 ADB 往返和命令执行耗时。

### 5.2 minitouch 的统一 50ms publish 延迟

每次 `CommandBuilder.publish()` 都会执行：

```python
time.sleep(self._delay / 1000 + config.DEFAULT_DELAY)
```

其中 [`DEFAULT_DELAY`](../../../module/automation/input_handlers/simulator/pyminitouch/config.py#L10) 为 50ms。因此：

- 没有协议 `wait` 的 publish 也会固定等待 50ms。
- 带协议 `wait` 的 publish 会等待“所有协议 wait 之和 + 50ms”。
- 一个动作拆成多次 publish 时，每次都会重复增加 50ms。

### 5.3 `mouse_drag`

普通拖动由三次 publish 构成：

1. 按下：固定 50ms。
2. 批量移动：轨迹协议等待之和 + 50ms。
3. 单独抬起：固定 50ms。

移动结束、抬起之前还会额外等待：

```text
hold = max(500ms, drag_time × 0.3)
```

因此固定部分至少为：

```text
3 × 50ms + 500ms = 650ms
```

此外，调用传给每个插值点的协议等待是 `drag_time × 1000 / 10` 毫秒。若移动阶段有 `K` 个点间移动，协议等待总计约为：

```text
K × drag_time / 10
```

所以完整显式等待近似为：

```text
K × drag_time / 10 + 150ms + hold
```

这意味着这里的 `drag_time` 同样不等于整段手势的严格总时长，总时长会随插值点数变化。

`mouse_drag_down` 转调 `mouse_drag(..., drag_time=0.4)`，也会继承上述至少 650ms 的固定部分。

### 5.4 `mouse_drag_link`

链式拖动使用一次按下 publish、一次移动 publish、一次抬起 publish。设路径共有 `P` 个原始点，则共有 `P-1` 段，每段协议等待 `drag_time`：

```text
(P - 1) × drag_time + 150ms
```

这一方法没有额外的 500ms 终点按住。

## 6. PC 前台 PyAutoGUI 输入

相关源码：[`input.py` 中的 `Input`](../../../module/automation/input_handlers/input.py#L140)。

### 6.1 隐式的 100ms PyAutoGUI PAUSE

PC 前台源码中点击、移动、滚轮和按键几乎看不到显式 sleep，但 AALC 当前环境使用 PyAutoGUI 0.9.54，且项目没有修改其默认配置：

```text
pyautogui.PAUSE = 0.1
```

PyAutoGUI 每次公开操作调用完成后会自动 sleep 100ms。可以在 `aalc` 环境中验证：

```powershell
conda run -n aalc python -c "import pyautogui; print(pyautogui.PAUSE)"
```

因此，以下调用每次都隐式增加约 100ms：

- `click`
- `moveTo`
- `mouseDown`
- `dragTo`
- `mouseUp`
- `scroll`
- `press`
- `typewrite`
- `hotkey`

### 6.2 前台操作耗时

| 操作 | 项目显式 sleep | PyAutoGUI 隐式等待 |
|---|---:|---:|
| 单次点击 | 0 | 100ms |
| 鼠标移动 | 0 | 100ms |
| 滚轮 | 0 | 100ms |
| 按键 | 0 | 100ms |
| 文本粘贴 | 0 | `hotkey` 后 100ms |
| 文本输入回退 | 0 | `typewrite` 后 100ms |

多次点击在 Python 循环中逐次调用 `pyautogui.click`，所以是 `times × 100ms`，不是只在整个循环后等待一次。

### 6.3 前台拖动

`mouse_drag` 的调用顺序为：

```text
moveTo → mouseDown → moveTo(duration=drag_time)
→ max(500ms, drag_time × 0.3)
→ mouseUp → 默认移回原位置
```

默认 `move_back=True` 时共有 5 次 PyAutoGUI 公开调用，因此除移动本身的 `drag_time` 外，额外等待近似为：

```text
max(500ms, drag_time × 0.3) + 5 × 100ms
```

通常为至少 1 秒。若 `move_back=False`，少一次 `moveTo`，减少约 100ms。

`mouse_drag_down` 固定使用 `dragTo(..., duration=0.4)`。不算这 400ms 动作持续时间，默认移回原位置时共有约 500ms PyAutoGUI 隐式等待，因此整体约为 900ms。

`mouse_drag_link` 设路径点数为 `P`，每个路径点都会单独调用一次 `moveTo(duration=drag_time)`：

```text
动作持续时间：P × drag_time
隐式等待：(P + 3) × 100ms
```

其中额外的 3 次分别是起点 `moveTo`、`mouseDown` 和 `mouseUp`；若开启移回原位置，再增加 100ms。

## 7. PC 后台 Win32 消息输入

相关源码：[`input.py` 中的 `BackgroundInput`](../../../module/automation/input_handlers/input.py#L294)。

### 7.1 底层原语

| 原语 | `SendMessage` | `PostMessage` |
|---|---:|---:|
| `mouse_down` 后 | 10ms | `20ms + mouse_down_duration` |
| `mouse_up` 后 | 10ms | 20ms |
| 普通点击在 down/up 之间额外等待 | 30ms | 30ms |
| 带 duration 的鼠标移动 | 每个步进 10ms，近似 duration | 同左 |
| 文本输入 | 每个字符后 10ms | 每个字符后 10ms |
| 按键 down/up | 0 | 0 |

当前 `use_post_message=false`，所以默认走 `SendMessage` 分支。`mouse_down_duration=50ms` 只在 `PostMessage` 分支生效；它不会影响当前默认的 `SendMessage` 点击。

### 7.2 组合操作

当前 `SendMessage` 模式下：

- 普通点击：`10 + 30 + 10 = 50ms/次`。
- 普通拖动：`drag_time + max(500ms, drag_time × 0.3) + 20ms`。
- 向下拖动：`400ms` 移动时间 + `20ms` 按下/抬起等待，约 420ms。
- 链式拖动：`P × drag_time + 20ms`，其中 `P` 为路径点数。
- 输入 `C` 个字符：固定等待 `C × 10ms`。

若启用 `PostMessage`，按当前 `mouse_down_duration=50ms`：

- 普通点击：`(20 + 50) + 30 + 20 = 120ms/次`。
- 普通拖动：`drag_time + max(500ms, drag_time × 0.3) + 90ms`。
- 向下拖动：`400ms + 90ms`，约 490ms。
- 链式拖动：`P × drag_time + 90ms`。

当游戏窗口处于最小化状态时，`set_active` 恢复窗口后还会额外等待 500ms。正常非最小化热路径不会触发。

## 8. PC 窗口移动输入

相关源码：[`input.py` 中的 `WindowMoveInput`](../../../module/automation/input_handlers/input.py#L644)。

该后端复用了后台输入的 `mouse_down`、`mouse_up`、键盘和文本输入等待，但通过移动窗口，让当前真实鼠标位置对应到目标客户区坐标。

### 8.1 与 BackgroundInput 的主要差异

- 普通点击没有额外的 30ms down/up 间等待。
- 带 duration 的窗口移动按约 70Hz 分步，并用 sleep 尽量维持指定 duration。
- 窗口最小化时，首次恢复窗口额外等待 100ms。
- `mouse_drag` 仍会在终点等待 `max(500ms, drag_time × 0.3)`。

### 8.2 组合操作

当前 `SendMessage` 模式下：

- 普通点击：`10 + 10 = 20ms/次`。
- 普通拖动：`drag_time + max(500ms, drag_time × 0.3) + 20ms`。
- 向下拖动：固定请求 600ms 窗口移动，加 20ms 按下/抬起等待，约 620ms。
- 链式拖动：`P × drag_time + 20ms`。
- 输入 `C` 个字符：`C × 10ms`。

启用 `PostMessage` 后：

- 普通点击：`(20 + mouse_down_duration) + 20ms`，按当前配置为 90ms。
- 普通拖动：`drag_time + max(500ms, drag_time × 0.3) + 90ms`。
- 向下拖动：约 `600ms + 90ms = 690ms`。
- 链式拖动：`P × drag_time + 90ms`。

窗口移动的实际持续时间还会受到 `SetWindowPos` 调用耗时影响，源码只在单步剩余时间大于 10ms 时执行 sleep，因此它不是严格的实时调度器。

## 9. 暂停、连接与启动等待

以下 sleep 不属于正常连续点击/拖动热路径，但会在特定状态下阻塞操作：

| 场景 | 等待 |
|---|---:|
| 任意后端 `wait_pause` | 暂停期间每 1 秒检查一次 |
| MuMu 启动轮询 | 每 1 秒检查一次，直到超时 |
| MuMu 启动游戏失败 | 等待 5 秒后重试 |
| 通用模拟器重建连接 | 重连前等待 1 秒 |
| 通用模拟器初始化重试 | 每次失败后等待 1 秒 |
| 通用模拟器启动游戏失败 | 等待 5 秒后重试 |
| minitouch 服务启动 | 固定等待 1 秒后做心跳检查 |
| `safe_device` 退出 | 关闭连接前等待 50ms |

## 10. MaaFramework：MuMu EmulatorExtras

本节基于本地 MaaFramework `v5.12.1-1-g76385c88`，只分析 Windows 下 MuMu 12 的专用 `EmulatorExtras` 路径：

```text
Pipeline Action
→ ControllerAgent
→ InputAgent
→ MuMuPlayerExtras
→ MuMu external_renderer_ipc.dll
```

`InputAgent` 在启用 `MaaAdbInputMethod_EmulatorExtras` 后优先创建 `MuMuPlayerExtras`。MuMu 控制单元声明了 `UseMouseDownAndUpInsteadOfClick`，所以实际点击和滑动由上层 `ControllerAgent` 拆成 `touch_down`、`touch_move`、`touch_up`。参见 MaaFramework 的 [`InputAgent.cpp`](https://github.com/MaaXYZ/MaaFramework/blob/main/source/MaaAdbControlUnit/Manager/InputAgent.cpp)、[`MuMuPlayerExtras.cpp`](https://github.com/MaaXYZ/MaaFramework/blob/main/source/MaaAdbControlUnit/EmulatorExtras/MuMuPlayerExtras.cpp) 和 [`ControllerAgent.cpp`](https://github.com/MaaXYZ/MaaFramework/blob/main/source/MaaFramework/Controller/ControllerAgent.cpp)。

如果只是在 MuMu 中运行，但没有启用 `EmulatorExtras`，而是实际落到了 MaaTouch、minitouch 或 ADB Shell，则本节时间不适用。

### 10.1 MuMu 底层原语

`MuMuPlayerExtras` 的 `touch_down`、`touch_move`、`touch_up` 只是同步调用 MuMu DLL，没有显式 sleep。等待全部由 `ControllerAgent` 和 Pipeline 节点层添加。

### 10.2 点击

MuMu 点击执行顺序为：

```text
touch_down
→ sleep 50ms
→ touch_up
```

控制器层固定等待为 50ms，抬起后没有额外的控制器 sleep。

Maa Pipeline 节点默认还有：

```text
pre_delay 200ms
→ 点击 50ms
→ post_delay 200ms
```

所以默认 `Click` 节点从识别命中到开始识别 `next` 的固定等待约为：

```text
200 + 50 + 200 = 450ms
```

将节点的 `pre_delay` 和 `post_delay` 都设为 0 后，才会接近单纯的 50ms 控制器点击。Pipeline 默认值参见 MaaFramework 的[任务流水线协议](https://github.com/MaaXYZ/MaaFramework/blob/main/docs/zh_cn/3.1-%E4%BB%BB%E5%8A%A1%E6%B5%81%E6%B0%B4%E7%BA%BF%E5%8D%8F%E8%AE%AE.md)。

### 10.3 滑动

MuMu 滑动使用固定 10ms 时间片：

```text
touch_down(begin)
→ 每 10ms touch_move 一次
→ touch_move(end)
→ 再等待约 10ms
→ end_hold
→ touch_up
```

设单段配置的滑动时长为 `duration`，则控制器层等待近似为：

```text
ceil(duration / 10ms) × 10ms + 10ms + end_hold
```

其中：

- `duration` 默认 200ms。
- `end_hold` 默认 0。
- 额外的约 10ms 来自到达终点后、抬起之前的最后一个时间片。
- 多个 `end` 组成折线时，每一段都会重复计算自己的 `duration + 约 10ms + end_hold`，但整条折线只在最开始按下一次、最后抬起一次。

默认单段滑动的控制器等待约为：

```text
200 + 10 = 210ms
```

再计入 Pipeline 默认前后延迟：

```text
pre_delay 200ms + 控制器 210ms + post_delay 200ms
= 约 610ms
```

如果把 `pre_delay=0`、`post_delay=0`，默认滑动约为 210ms。`rate_limit` 是识别循环的速率限制，不属于点击或滑动控制器本身，因此没有加入上述公式。

## 11. 边狱巴士自动化编辑器

本节分析工作区中的 `边狱巴士_自动化编辑器`。程序主体是 Python 3.8 32 位 Cython 扩展 [`minloo.pyd`](../../../边狱巴士_自动化编辑器/minloo.pyd)，没有可直接阅读的原始 `minloo.py`。

为避免仅根据脚本格式猜测，本次使用兼容的 Python 3.8 32 位解释器隔离导入该扩展，将 Win32 输入 API 和 `time.sleep` 替换为只记录调用、不发送真实输入的桩对象，然后直接调用 `Control.click`、`Control.move` 和 `Control.delay`。因此下面的点击与滑动时间来自实际二进制执行路径。

当前 [`Config.ini`](../../../边狱巴士_自动化编辑器/Config.ini) 为：

```ini
mouseof = False
drive = False
activate = False
track = False
mouse_S = 0
```

所以本节只统计当前使用的 Win32 后台消息路径，不统计前台鼠标、驱动级操作和轨迹增强模式。

### 11.1 点击

二进制中的实际函数签名为：

```python
Control.click(self, x, y, t=50, md="L", tc=0, yc=0)
```

当前后台模式的普通左键 `L` 和右键 `R` 执行顺序为：

```text
发送鼠标移动消息
→ 发送按下消息
→ sleep(t / 1000)
→ 发送抬起消息
```

因此：

- 默认普通点击按住 50ms。
- `点击=[x, y, 25]` 会按住 25ms。
- 抬起后没有底层固定 sleep。
- 在当前后台路径的隔离测试中，改变 `tc`、`yc` 没有增加固定 sleep；其其他业务语义不属于本文范围。

双击模式 `md="2L"` 不使用传入的默认 `t=50`，二进制路径中固定 sleep 20ms，然后发送后续双击消息。

脚本中的 `延时` 是动作之外的另一层等待。例如：

```text
{点击 | [1544,51,50] | 延时=500}
```

其可确定的等待为：

```text
点击按住 50ms + 动作后延时 500ms = 550ms
```

### 11.2 滑动/拖动

二进制中的滑动函数签名为：

```python
Control.move(
    self,
    x1, y1, x2, y2,
    jd=10,   # 相邻轨迹点距离，像素
    tm=10,   # 每个轨迹点等待，毫秒
    t=50,    # 到达终点后、抬起前等待，毫秒
    td=0,    # 按下后、开始移动前等待，毫秒
)
```

设滑动距离为：

```text
D = sqrt((x2-x1)² + (y2-y1)²)
```

当 `D > 5px` 时，轨迹点数量为：

```text
N = round(D / jd) + 1
```

底层显式等待为：

```text
td + N × tm + t
```

默认参数下即：

```text
(round(D / 10) + 1) × 10ms + 50ms
```

执行顺序是“按下 → 可选 `td` → 每个轨迹点等待 `tm` → 终点等待 `t` → 抬起”，抬起后没有固定 sleep。

实测示例：

| 距离和参数 | 轨迹等待 | 终点等待 | 底层合计 |
|---|---:|---:|---:|
| 100px，默认 `jd=10, tm=10, t=50` | 11 × 10ms | 50ms | 160ms |
| 200px，默认参数 | 21 × 10ms | 50ms | 260ms |
| 100px，`jd=20, tm=20, t=50` | 6 × 20ms | 50ms | 170ms |

当前脚本中的实例：

- [`X714.ml`](../../../边狱巴士_自动化编辑器/脚本/镜板-X714/X714.ml#L1667) 的 280px 默认滑动：`29 × 10 + 50 = 340ms`，没有动作后 `延时`。
- [`7.114.ml`](../../../边狱巴士_自动化编辑器/脚本/主板-7.114/7.114.ml#L1592) 的 200px 默认滑动再加 `延时=500`：`260 + 500 = 760ms`。
- 同文件 `257px、jd=20、tm=20` 的滑动再加 `延时=1000`：底层约 `14 × 20 + 50 = 330ms`，总计约 1330ms。

### 11.3 脚本级 sleep

编辑器的脚本级等待与点击/滑动底层等待相互叠加：

| 脚本字段 | 实际行为 |
|---|---|
| `延时=N` | 动作完成后 `sleep(N / 1000)` |
| `{延时 | N}` | 独立等待 N ms |
| `循环=['T', 次数, N]` | 每次循环按配置等待 N ms |

对当前两个 `.ml` 脚本排除整行注释后统计：

- 显式 `延时` 共 1387 处，范围为 10ms～9000ms。
- 最常见的是 500ms（255 处）、200ms（217 处）、300ms（183 处）、100ms（182 处）、40ms（145 处）和 1000ms（99 处）。
- `循环` 间隔共 189 处，最常见的是 100ms（94 处）和 250ms（56 处）。

这些是业务脚本主动配置的等待，不是编辑器给每次点击强制增加的全局 sleep。没有 `延时` 或 `循环` 字段时，普通点击只有默认 50ms 按住时间。

## 12. 当前配置下的单次点击等待对比

### 12.1 AALC 各后端

下面假设 AALC 动作通过 `mouse_action_with_pos` 发起，并且距离上次动作不足 200ms，因此触发完整的 200ms 高层节流。未计入图片识别、ADB/IPC/API 调用耗时和窗口恢复：

| 后端 | 高层节流 | 底层固定等待 | 合计下界 |
|---|---:|---:|---:|
| MuMu | 200ms | 50ms | 约 250ms |
| 通用模拟器 ADB 点击 | 200ms | 0 | 约 200ms + ADB 耗时 |
| PC 前台 | 200ms | PyAutoGUI 100ms | 约 300ms |
| PC 后台 SendMessage | 200ms | 50ms | 约 250ms |
| PC 后台 PostMessage | 200ms | 120ms | 约 320ms |
| PC 窗口移动 SendMessage | 200ms | 20ms | 约 220ms |
| PC 窗口移动 PostMessage | 200ms | 90ms | 约 290ms |

如果识别、截图或业务逻辑已经消耗了至少 200ms，高层节流不会触发，上表应减去 200ms。直接调用底层输入方法时同样没有这 200ms。

### 12.2 MuMu 跨项目对比

| 项目和配置 | 动作前 | 点击按住 | 动作后 | 合计固定等待 |
|---|---:|---:|---:|---:|
| AALC MuMu，触发高层节流 | 200ms | 15ms | 35ms | 约 250ms |
| AALC MuMu，未触发节流 | 0 | 15ms | 35ms | 50ms |
| Maa MuMu，Pipeline 默认 | 200ms | 50ms | 200ms | 450ms |
| Maa MuMu，`pre/post_delay=0` | 0 | 50ms | 0 | 50ms |
| 自动化编辑器，普通后台点击、无脚本延时 | 0 | 50ms | 0 | 50ms |
| 自动化编辑器，普通点击、`延时=500` | 0 | 50ms | 500ms | 550ms |

三者的纯底层单击实际上都约为 50ms。速度差距主要来自 AALC 的动作节流、Maa Pipeline 的前后延迟，以及编辑器脚本是否显式填写 `延时`。

## 13. MuMu 滑动等待对比

| 项目 | 控制器/底层等待公式 | 动作外等待 |
|---|---|---|
| AALC `mouse_drag` | `N × 20ms + max(500ms, drag_time×0.3)` | 可能再加 200ms 高层节流 |
| AALC `mouse_drag_down` | `N × 40ms + 250ms` | 可能再加 200ms 高层节流 |
| Maa MuMu 默认单段 Swipe | 约 `200ms + 10ms = 210ms` | Pipeline 默认再加 200ms + 200ms，总计约 610ms |
| Maa MuMu，`pre/post_delay=0` | 约 210ms | 0 |
| 自动化编辑器默认滑动 | `(round(D/10)+1) × 10ms + 50ms` | 再加脚本 `延时`，没有全局固定节流 |

编辑器默认 200px 滑动的底层等待约 260ms；Maa 默认距离无关、由 `duration` 控制，控制器约 210ms；AALC 的普通 MuMu 拖动仅终点固定停留就至少 500ms。因此，在没有额外业务延时的拖动场景中，AALC 当前实现通常明显更慢。

## 14. 主要性能热点

从固定等待角度看，最值得关注的是：

1. AALC 高层节流在不足间隔时睡完整 `interval`，没有只补足剩余时间。
2. AALC 多目标操作使用 1 秒间隔。
3. AALC MuMu `swipe` 在终点固定按住 200ms，抬起后再等 50ms。
4. AALC MuMu `mouse_drag` 和通用模拟器 `mouse_drag` 在终点至少按住 500ms。
5. AALC minitouch 每次 publish 固定增加 50ms，一个拖动通常至少 publish 三次。
6. AALC PC 前台每个 PyAutoGUI 公开调用都隐式增加 100ms，复合拖动会累计多次。
7. AALC PC 后台点击在底层 down/up sleep 之外，又额外加入 30ms。
8. Maa MuMu 控制器本身较轻，但 Pipeline 默认 `pre_delay + post_delay` 会额外增加 400ms。
9. 自动化编辑器没有统一的动作外固定节流，但业务脚本大量使用 100～500ms `延时`；分析速度时必须区分底层 50ms 点击和脚本延时。

优化时应先区分“保证游戏接收输入所需的最短按住时间”和“为了保险而增加的动作后等待”。前者通常只需要几十毫秒；后者更适合由下一画面状态、局部图片轮询或超时机制控制，而不是长期固定 sleep。
