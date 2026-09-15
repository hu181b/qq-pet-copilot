# QQ 宠物自动化助手（qq-pet-copilot）

## v1.4 真机优化版

本仓库由 [hu181b](https://github.com/hu181b) 维护，基于原仓库 [490720818/qq-pet-copilot](https://github.com/490720818/qq-pet-copilot) 二次修改，保留原作者贡献和 GPL-3.0 许可证。

从 [本仓库 Releases](https://github.com/hu181b/qq-pet-copilot/releases) 下载 `QQPetCopilot-v1.4-windows-x64.zip`，解压后双击 EXE，无需安装 Python，ADB、scrcpy 和 OCR 模型已内置。首次连接仍需手机开启并授权 USB 调试，登录 QQ 并进入宠物页面；请先检查任务设置再点击开始。

v1.4 新增原子文件写入、调度与结算边界修复、配置提示框修复和程序图标。本版本移除模拟器支持，修复护理、打工和学园导航，减少后台 CPU、视频和磁盘开销，增加独立多配置、窗口缩放及关闭按钮最小化选项。完整说明见 [v1.4 优化与使用说明](RELEASE-v1.4.md)。下方上游功能介绍与本版本不一致时，以本段和版本说明为准。

基于 uiautomator2 控件定位 + RapidOCR 文字识别的 QQ 宠物自动托管工具（分辨率无关）。
PyQt6（Fluent Widgets）图形界面内嵌 scrcpy 实时画面，任务队列自动调度，按金币和**学习/工作时长**规则推进，
并自动处理**被雇佣召回**、**体力/清洁照顾**、**好友护理/雇佣**等日常。

> **游戏机制注意：护理相关勋章如果要拿的话不能一键！！！**
> 一键护理不计入勋章进度，要拿勋章必须把护理方式配成"ocr检测"手动喂食/洗澡
> （配置项 `care.method` / `friend_care.method`）。

推荐使用调度任务中设置时间段来规划每天的任务。推荐配置：

| 玩家类型 | 推荐配置 |
| --- | --- |
| 战力玩家 | 每天学习 19 小时左右，剩下 5 小时优先完成 600 次冒险（设置跳过天色；有条件的话用一个小号给大号一直护理），还有剩余可以小号雇佣大号 |
| 均衡玩家 | 每天学习 15 小时，5 小时冒险 600 次（设置跳过天色），4 小时被小号雇佣 |
| 职业玩家 | 优先打工拿工分升级职业，职业升级卡住了再去学习 |
| 勋章玩家 | 无推荐配置，按勋章要求来即可 |

## 功能

- **任务队列调度（默认 `task_queue`）**：按 `tasks.order` 顺序扫描执行，每个任务独立
  enabled / trigger（interval 间隔 / daily 每日时间点窗口）/ 执行时间窗 / 成功失败退避
  （失败退避统一由设置页"任务失败重试间隔"`tasks.failure_interval` 控制）；
  冒险/学习/打工/雇佣好友互斥，作为主任务组统一调度且**非阻塞延时收尾**（进行中 OCR 剩余
  时间登记 pending，到点自动收尾计数，期间先跑其他任务）；踩踩/PK/好友护理/被雇佣检查按各自
  定时与次数排期，失败自动延后重试。另有旧 `legacy` 引擎（顺序写死）可切换。
- **学习场景**：出门 → 学校 → OCR 识别学园阶段（初级/中级/高级/进修，选课顺序自动适配）→
  归位选课（力量/智力/魅力）→ 上课 → 下课计数；毕业自动切下一阶段课程。
  每节按学园累计学习时长：初级 10 / 中级 20 / 高级 30 / 进修 45 分钟。
- **打工场景**：出门 → 小镇 → OCR 识别打工地点进入（设置页下拉可选 8 个地点）→
  按 `work.duration` 选时长（10分钟/45分钟/2小时）→ 顺带雇佣好友 → 开工。
  每次按所选时长累计打工时长。
- **学习工作时长上限**：学习/打工时长按学园与所选时长结算累计，
  累计 >= `schedule.daily_hour_limit`（小时，0=不限）后**今天不再学习只打工**；
  首次运行新版本时，老进度只有次数会按旧版点数系数自动换算成时长（只迁移一次）。
- **冒险场景**：每天到达配置时间后优先冒险，连跑 `adventure.batch` 次；
  可开启"天色不对"自动召回（点完"确认召回"会验证生效，召回后直接回出门页连跑）。
- **被雇佣检查/召回**：按时间段定时出门检测被雇佣中，按配置"等到 25/75"或"立刻召回"处理，
  召回自动计数后回主页面；被雇佣期间主任务不触发。
- **好友护理 / 雇佣好友**：按时间段 + 调度间隔巡检指定好友家（ocr检测/一键护理）；
  雇佣好友会检测雇佣 CD、出门前预检进行中活动，主动延后不误点。
- **踩踩 / PK**：好友互动按各自 `start_time` 与每天次数调度；踩踩自动跳过已踩过的好友，
  PK 每个好友可打 3 次、打完自动切换下一个好友。
- **状态照顾**：任务前读取体力/清洁/心情，按阈值自动喂食、洗澡（持续按压搓洗，
  搓洗按回合复测、连续不提升自动抬手重按自愈，达到上限仍不达标则跳过本次洗澡）；支持一键护理。
- **控制方案**（设置页下拉）：`injectInputEvent`（默认，真机推荐，uiautomator2 事件注入）/
  `minitouch`（openstf minitouch socket 直发）；minitouch 因
  非 Root/SELinux 不可用时会自动回退 injectInputEvent 并写回配置。
- **异常自动恢复**：页面错误先尝试返回主页，失败按配置重启手机或 QQ，再通过官方入口恢复。
- **失败告警通知**：主任务多次重试仍失败时发 Windows Toast + OnePush 多渠道推送
  （Bark / PushPlus / Server酱 / SMTP / 自定义 webhook），并附当前手机截图。
- **每日计数与时长持久化**：各场景次数与累计时长按天记录在 `runs/*.json`（含历史），
  中途停止重跑接着计数，跨天自动归档清零（单账号，不按账号拆分）。
- **GUI（PyQt6-Fluent-Widgets）**：左侧 Fluent 导航（主页/调度/统计/任务/设置），顶部全局
  工具栏常驻（开始/停止/画面镜像/连接测试/手动重启 + 运行时间，画面镜像开关状态持久化
  `gui.mirror`）；主页 = scrcpy 实时画面（9:16 竖屏等比自适应嵌入）+ 宠物状态/任务队列/
  今日统计/日志卡片——任务队列卡调度器未运行时也按配置推算下一任务，今日统计以
  学习(h)/工作(h) 时长 + 各任务当日次数的两行网格展示；统计页为各任务近 N 天次数的
  平滑折线图；主题支持 跟随系统/深色/浅色（`gui.theme`，即时切换）；
  调度/任务/设置页可视化修改 config.yaml，保存后**热加载即时生效**（无需重启）；
  "手动重启"按配置执行一次异常恢复，恢复后自动重启调度器；
  设置页"检查更新"：启动后自动检查一次、之后每 6 小时一次，发现新版本显示 Release 下载链接。
- **分辨率无关定位**：优先 u2 控件选择器，游戏内自绘按钮用 RapidOCR（PP-OCRv6 tiny）
  整屏文字识别，换分辨率/机型无需改代码。

## 快速开始

两种方式二选一：**直接下载 Releases 打包好的 exe 使用**（推荐，无需 Python），或 **源码运行**（开发者 / 需要改代码）。

### 方式一：直接下载 Releases 打包好的 exe（无需 Python）

1. 使用本地交付的 `QQPetCopilot.exe`；此分支只支持 Android 真机。
2. 手机开启 USB 调试并连接电脑，在设置页选择手机序列号。
3. 点击“开始”；配置与运行记录保存在程序旁边。

### 方式二：源码运行（开发者 / 需要改代码）

需要 **Python 3.12**：

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

1. 手机开 USB 调试并连接电脑（可用 `resources/scrcpy-win64/adb.exe devices` 确认）。
2. 编辑 `config.yaml`（或在运行后在 GUI 设置页里改）。
3. 启动：

```bash
.venv/Scripts/python main.py            # GUI：画面 + 日志 + 控制按钮
.venv/Scripts/python scenarios/runner.py  # 控制台模式（无 GUI）
```

GUI 打开后自动嵌入手机画面，点**开始**启动调度器（子进程），点**停止**立即结束。
scrcpy、OCR 模型及 minitouch 可由 tools 下对应下载脚本准备。

## 配置（config.yaml，主要项）

| 配置 | 说明 |
| --- | --- |
| `adb.path` / `adb.device_serial` | adb 路径（默认用 scrcpy 自带）/ 设备序列号（空 = 第一台；设置页下拉带实例名/手机型号标注，离线模拟器实例也会列出） |
| `gui.theme` / `gui.mirror` | 界面主题（跟随系统/深色/浅色）/ 画面镜像开关状态持久化（仅 GUI） |
| `control.method` | 控制方案：`injectInputEvent`（真机推荐，默认）/ `minitouch`（模拟器推荐） |
| `school.attribute` | 属性点课程：力量 / 智力 / 魅力 |
| `school.times_per_day` | 每天学习次数上限，0 不限 |
| `work.location` | 打工地点（设置页下拉：风铃旅社/彩虹画室/迷雾侦探所/星尘魔法塔/咕噜厨房/竹影武馆/云朵梦舍/闪耀星屋） |
| `work.duration` | 打工时长：10分钟 / 45分钟 / 2小时 |
| `work.times_per_day` | 每天打工次数上限，0 不限 |
| `schedule.coin_threshold` | 金币阈值：>= 优先学习，< 先打工 |
| `schedule.daily_hour_limit` | 学习工作时长上限（小时，0=不限）：累计学习+打工时长 >= 上限后只打工 |
| `schedule.check_interval` | 上课/打工/冒险/被雇佣进行中状态的统一检查间隔（秒） |
| `schedule.back_method` | 返回方式：`系统返回`（Android 返回键，默认）/ `返回图标`（定位 back 按钮点击） |
| `adventure.times_per_day` / `start_time` / `batch` | 每天冒险次数 / 调度时间（HH:MM）/ 单轮连跑次数 |
| `adventure.skip_bad_weather` | 遇到"天色不对"自动召回计入一次冒险 |
| `care.energy_threshold` / `clean_threshold` | 体力 / 清洁阈值，低于则喂食 / 洗澡 |
| `friend_care.*` / `hire_friend.*` | 好友护理 / 雇佣好友的开关、时间段、好友名、调度间隔、次数 |
| `employed.*` | 被雇佣检查的开关、时间段、间隔、召回策略 |
| `tasks.failure_interval` | 所有任务统一的失败重试间隔（秒，设置页"任务失败重试间隔"） |
| `recover.method` | 异常恢复方式：重启手机 / 重启 QQ 游戏 |

> 旧版 `school_factor` / `work_factor` / `daily_point_limit` 仅保留用于首次运行迁移老进度
> （把已有次数换算成时长），不再参与调度、也不在设置页显示。

## 运行状态文件

| 路径 | 内容 |
| --- | --- |
| `runs/school_progress.json` | 学习次数 + 历史 + 当前学园（`school`）+ 今日学习时长（`study_secs`） |
| `runs/work_progress.json` | 打工次数 + 历史 + 本次打工时长（`duration`）+ 今日打工时长（`work_secs`） |
| `runs/adventure_progress.json` 等 | 冒险/踩踩/PK/被雇佣/雇佣好友/经验日常的每日次数 + 历史 |
| `runs/status_cache.json` | 宠物状态缓存（体力/清洁/心情/金币/库存），GUI 状态条读取 |
| `runs/queue_status.json` | 任务队列状态（当前任务/下一任务/倒计时），GUI 调度页读取 |
| `runs/logs/YYYY-MM-DD.log` | 按天的运行日志 |

## 单模块测试

```bash
.venv/Scripts/python scenarios/runner.py --test coins              # 只测主页金币 OCR
.venv/Scripts/python scenarios/runner.py --test recover           # 只测异常恢复链路
.venv/Scripts/python scenarios/runner.py --test opener            # 模拟器：直接用 opener 打开宠物主页
.venv/Scripts/python scenarios/runner.py --test work.select_place # 只跑某个阶段方法
.venv/Scripts/python scenarios/runner.py --test care.read_status  # 只测体力/清洁识别
```

场景脚本也可单独跑：`python scenarios/school.py --times 1`（work / adventure / care 同理）。

模拟器诊断（QQ 更新后排查"访问好友"跳转）：用 [capture_visit_jump.py](tools/capture_visit_jump.py)
在真机/模拟器上抓 `mqqapi://qpet/open` 跳转的完整 URL 与 attrs，对比差异定位问题：

```bash
.venv/Scripts/python tools/capture_visit_jump.py -s 127.0.0.1:7555 -c   # 模拟器自动点
.venv/Scripts/python tools/capture_visit_jump.py -s ba286ada            # 真机手动点
```

## 打包 exe

```bash
.venv/Scripts/python build.py
.venv/Scripts/python build.py --onedir
```

仅生成真机版 QQPetCopilot.exe。首次启动生成配置，runs 保存运行数据。

## 目录结构

```
main.py               # PyQt6 GUI 入口（Fluent 导航 + 全局工具栏 + scrcpy 9:16 嵌入 + 调度控制）
build.py              # PyInstaller 打包脚本（真机版）
config.yaml           # 全部可调配置
scenarios/
  runner.py           # 统一调度器（task_queue 任务队列 / legacy 两种引擎）
  school.py           # 学习场景（学园识别、选课、毕业处理）
  work.py             # 打工场景（OCR 选地点、选时长、雇佣好友）
  adventure.py        # 冒险场景（连跑、天色不对召回）
  care.py             # 体力/清洁检查与喂食/洗澡（搓洗）
  visit.py            # 踩踩（好友列表导航基类）
  pk.py               # PK
  friend_care.py      # 好友护理
  hire_friend.py      # 雇佣好友
  employed.py         # 被雇佣检测
src/
  u2dev.py            # uiautomator2 封装 + 控制方案（injectInputEvent/minitouch）+ 设备掉线重连
  locators.py         # UI 定位注册表（u2 控件选择器 + OCR 文字 + 相对坐标兜底）
  scenario.py         # 场景基类：定位导航、回主页面、等待/延时收尾、被雇佣召回、鼓励宠物
  recover.py          # 异常恢复链路（重启设备/游戏、模拟器实例重启、opener 重试）
  opener.py           # 真机官方 scheme 跳转
  adb/device.py       # adb 封装：设备在线管理、屏幕属性读取、远程模拟器 connect
  ocr.py              # RapidOCR 封装（整屏 OCR、剩余时间/面板解析）
  coins.py            # 主页金币 OCR
  progress.py         # 日志 + 每日次数/时长持久化对外入口（兼容层）
  progress_store.py   # 进度文件统一管理（跨天归档/原子写入/损坏兜底）
  stats_chart.py      # 统计页平滑折线图（QPainter 自绘）
  status_cache.py     # 宠物状态缓存
  queue_status.py     # 任务队列状态缓存
  version.py / update_checker.py  # 版本常量 / GitHub Release 更新检查
  notify.py           # 失败告警通知（Windows Toast + OnePush）
  settings.py         # config.yaml 读写（保留注释）
  config.py           # 配置加载与路径规划（兼容 PyInstaller）
resources/                   # 第三方二进制/离线包（不入库，build 时下载或本地放入）
  scrcpy-win64/              # scrcpy 二进制
  minitouch/                 # minitouch 控制方案二进制（x86_64 / arm64-v8a）
tools/
  fetch_scrcpy.py / fetch_minitouch.py / fetch_ocr_models.py
  dump_hierarchy.py / test_locator.py / capture_visit_jump.py
```

## 定位方式

界面元素定位登记在 `src/locators.py` 的 `LOCATORS` 表：优先 u2 控件选择器
（原生弹窗等），游戏内 canvas 自绘按钮靠 OCR 文字。游戏更新后如识别失败，
用 `--test` 单测真机逐屏校准该表即可，无需重新截图做模板。

## 致谢

- [scrcpy](https://github.com/Genymobile/scrcpy)
  Android 画面镜像与控制工具，本项目的实时画面嵌入和 adb 能力实现。
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/PyQt6)（PyPI 包名 `PyQt6-Fluent-Widgets`，代码在主仓库的 PyQt6 分支）
  Fluent Design 组件库，GUI 的导航栏、卡片与控件实现。
- 历史版本参考 qqpet-module-opener；本分支已移除模拟器管理、注入及机型伪装。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) / [PP-OCRv6](https://github.com/PaddlePaddle/PaddleOCR)
  文字识别引擎与模型（本项目用 PP-OCRv6 tiny），游戏内自绘按钮、金币/状态等数字识别全靠它。
- [uiautomator2](https://github.com/openatx/uiautomator2)
  Android UI 自动化框架，控件定位、点击/滑动与截图实现。
- [minitouch](https://github.com/DeviceFarmer/minitouch)
  底层触摸注入工具（触摸控制方案），socket 直发触摸事件。

## 免责声明

本项目仅供学习研究自动化与 OCR 识别技术使用。自动化操作可能违反游戏服务条款，
由此产生的一切后果由使用者自行承担。

## 许可证

本项目采用 GNU General Public License v3.0 (GPLv3)，详见根目录 [LICENSE](LICENSE)。

新增成长福袋领取、推荐区域排除与列表翻页，并修复调度/镜像句柄异常，详见 [v1.4 更新说明](RELEASE-v1.4.md)。
