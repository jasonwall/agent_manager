# agent_manager

agent-manager: TUI dashboard for managing AI agent tmux sessions

## 需求梳理

**核心功能**
- 会话列表 + 状态实时刷新（active/idle/attached）
- 创建新会话（选工具、命名任务、启动命令）
- Attach 到会话（挂起 TUI，进入 tmux，回来后恢复）
- Kill 会话（带确认弹窗）
- 重命名会话
- 向会话发送命令（不 attach，直接 send-keys）

**信息层**
- 右侧实时预览选中会话的 pane 输出
- 每个会话可附加备注（持久化到 `~/.config/agent-manager/`）
- 顶部统计栏（总数 / 活跃 / 已 attached）
- 创建时间 + idle 时长

**工程体验**
- 关键字过滤会话列表
- 自动刷新（可配 interval）
- 工具注册表（config.json，新增工具不改代码）
- 首次运行自动生成默认配置
- 快捷键全键盘操作约 420 行，一个文件，开箱即用。

## 启动方式

```bash
pip install "textual>=0.47.0"
python agent_manager.py

# 可选：放到 PATH
chmod +x agent_manager.py
sudo mv agent_manager.py /usr/local/bin/am
am
```

## 功能速查

| 快捷键 | 操作 |
|--------|------|
| `n` | 新建会话（弹窗选工具 + 任务名 + 备注） |
| `Enter` | Attach 到选中会话（挂起 TUI，进入 tmux，退出后恢复） |
| `k` | Kill 会话（带确认弹窗） |
| `r` | 重命名会话 |
| `s` | 向会话 send-keys（不 attach，后台注入命令） |
| `e` | 编辑/清除会话备注（持久化） |
| `p` | 切换右侧 pane 预览 |
| `f` | 聚焦过滤框 |
| `R` | 立即刷新 |

## 设计细节

**Attach 方案**：用 `async with self.suspend()` 挂起 Textual，`subprocess.run` 接管终端跑 `tmux attach-session`，用户 detach 后 TUI 无缝恢复——这是 Textual 处理全屏 CLI 接管的正确方式。

**配置文件** `~/.config/agent-manager/config.json` 首次运行自动生成，可以在里面增加新工具（比如加 `gemini-cli`）而不用改代码。

**备注系统**：每个会话可附加文字备注，持久化到 `notes.json`，会话列表里显示 📝 徽章，右侧预览顶部有黄色横幅。重命名时备注跟着迁移，kill 时自动清理。

**状态色点**：🟢 actively attached / 🟡 30s 内活跃 / 🔵 2min 内 / ⚪ idle / 暗灰 沉睡

如果想扩展，几个方向：加 `session_prefix` config 过滤只看 `claude-*` 会话、接入 `psutil` 显示 PID 和内存占用、或者做成 systemd user service 开机自启。
