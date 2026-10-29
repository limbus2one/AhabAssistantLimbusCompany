# 开发指南

模块职责、调用关系与主题样式位置见[项目架构](architecture.md)，控件使用约定见 [UI 组件开发说明](ui_components.md)。

## 环境配置

1. **Python 版本**：项目要求 Python 3.12
2. **windows 环境**：目前仅支持在windows环境下开发

### 使用 uv（推荐）

```bash
uv venv --python=3.12
# After activate the env
uv sync
```

### 使用 Conda

```bash
# 创建 Python 3.12 虚拟环境
conda create -n aalc python=3.12
conda activate aalc

# 升级 pip 并安装依赖
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 启动开发服务器

```bash
# 默认：启用热重载（自动重启 + Ctrl+R）
python main_dev.py

# 仅手动重载：禁用自动热重载，但保留 Ctrl+R
python main_dev.py --no-reload
```

## 开发特性

- **热重载**：
  - 默认：代码修改后自动重新加载
  - `--no-reload`：只在按下 `Ctrl+R` 时才重启
- **快捷键**：
  - `Ctrl+R`：手动触发重载
  - `Ctrl+C`：退出程序

## 更新器验证与恢复

更新包先下载到临时文件，完整下载后再进入安装队列。主任务、排队任务、工具和资源同步结束后，主程序退出，独立更新器接管安装。更新器与打包后的主程序共用安装锁；开发模式不使用该锁。

兼容旧发布包：包内若仍是控制台更新器，由当前窗口版完成安装，并在安装时保留窗口版更新器；包内为窗口版时继续使用现有自更新交接流程。交接失败会中止更新，不会退回当前更新器继续安装。

在 `aalc` 环境运行离线回归测试：

```powershell
python -m pytest tests/test_update.py -q -p no:cacheprovider --basetemp .tmp-update-tests
```

测试覆盖缺失载荷、文件写入失败、备份恢复失败、跨进程互斥、下载重试及进程交接。打包验收还应在独立目录中使用测试程序和测试更新包，检查旧更新器自复制、新更新器接管、文件替换和重启结果；不要在真实游戏目录中注入故障。

安装失败会尝试恢复旧文件；恢复失败或进程被中断时，`update_temp/install-incomplete.json` 保留未完成标记，主程序会阻止启动。旧文件和路径映射位于该标记指定的 `backup` 目录及 `journal.jsonl` 中，日志为 `update_temp/updater.log`。此时须使用完整包修复，不能继续叠加增量包：把正式完整包按归档根目录命名为 `AALC.zip` 或 `AALC.7z` 放到 `update_temp`，在安装根目录运行：

```powershell
& '.\AALC Updater.exe' AALC.7z
```

成功后更新器自动清除未完成标记，旧文件备份及 `journal.jsonl` 保留在本次 `update_temp/session-*/backup` 中，到下一次更新确认包文件存在后清理。缺包会立即取消，保留上次备份。失败会话的备份不会被自动清理。修复前保留备份；不要仅删除标记绕过检查。完整更新会替换图片目录以及包内提供的 `_internal` 运行库目录，其他根目录文件沿用覆盖安装策略。

## 翻译

```powershell
# 提取可翻译文本到 .ts 文件
uv run .\scripts\translation_files_build.py
# 或者
python scripts\translation_files_build.py

# 手动编辑翻译
pyside6-Linguist  .\i18n\myapp_en.ts

# 更编译生成 .qm 文件
uv run .\scripts\translation_files_compile.py
# 或者
python scripts\translation_files_compile.py
# 或者
pyside6-lrelease i18n/myapp_en.ts -qm i18n/myapp_en.qm

```
