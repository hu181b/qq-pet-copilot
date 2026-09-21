<!-- markdownlint-disable MD033 MD041 -->

<div align="center">
  <h1>🐧 QQ 宠物自动托管助手</h1>
  <img alt="license" src="https://img.shields.io/github/license/yu-echo/qq-pet-copilot">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Android%20%E7%9C%9F%E6%9C%BA-blueviolet">
  <img alt="python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="gui" src="https://img.shields.io/badge/GUI-PyQt6%20Fluent%20Widgets-00A8F3">
  <img alt="ocr" src="https://img.shields.io/badge/OCR-RapidOCR%20PP--OCRv6-FF6F00">
  <img alt="automation" src="https://img.shields.io/badge/Automation-uiautomator2-4CAF50">
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/yu-echo/qq-pet-copilot">
  <img alt="stars" src="https://img.shields.io/github/stars/yu-echo/qq-pet-copilot?style=social">
</div>

---

用 **uiautomator2 控件定位 + RapidOCR 文字识别** 自动托管 QQ 宠物日常：任务队列按
金币与「学习 / 工作时长」规则推进，Fluent 图形界面内嵌 scrcpy 实时画面，
自动处理被雇佣召回、体力清洁照顾、好友护理与雇佣。分辨率无关，换机型不用改代码。

上游 [hu181b/qq-pet-copilot](https://github.com/hu181b/qq-pet-copilot) v1.4，
原作者 [490720818/qq-pet-copilot](https://github.com/490720818/qq-pet-copilot)，
保留原作者贡献与 GPL-3.0 许可证。

✨ 如果这个项目帮到你，欢迎在右上角点亮 Star ✨

---

## 功能介绍

### 🎮 自动化任务

- 🎁 **SVIP 会员礼包**：每天到点自动领一次，自动区分「立即领取 / 明日再来 / 开通 SVIP」
  三种弹窗状态；非会员自动关闭该任务，关闭期间每天复查一次会员状态，恢复会员自动重开
- 💰 **成长福袋**：自动点取好友列表里的成长福袋，推荐区域用「他们都在玩」分界线排除；
  领到的金币累计显示在今日统计的「福袋」格
- 🗓️ **任务队列调度（默认 `task_queue`）**：按 `tasks.order` 扫描执行，每个任务独立
  enabled / trigger（间隔 / 每日时间点窗口）/ 时间窗 / 成功失败退避；冒险·学习·打工·雇佣好友
  互斥且**非阻塞延时收尾**（进行中登记 pending，到点自动收尾计数，期间先跑其他任务）
- 🎓 **学习**：出门 → 学校 → OCR 识别学园阶段（初级/中级/高级/进修）→ 选课（力量/智力/魅力）
  → 上课计数；每节按学园累计时长：10 / 20 / 30 / 45 分钟
- 🔨 **打工**：出门 → 小镇 → OCR 识别地点（下拉可选 8 个）→ 按 `work.duration`
  选时长（10分钟/45分钟/2小时）→ 顺带雇佣好友 → 开工
- ⚖️ **学习工作时长上限**：累计学习+打工 >= `schedule.daily_hour_limit`（小时，0=不限）后
  今天不再学习只打工；老进度只有次数时按旧系数自动换算成时长（只迁移一次）
- ⚔️ **冒险**：每天到点优先冒险，连跑 `adventure.batch` 次；可开启「天色不对」自动召回
- 🤝 **被雇佣检查 / 召回**：按时间段出门检测，按「等到 25/75」或「立刻召回」处理；
  雇佣自然到期（面板消失）不会卡住等待，自动判定结束继续调度
- 👥 **好友护理 / 雇佣好友**：按时间段巡检指定好友家（ocr检测 / 一键护理）；
  雇佣会检测 CD、出门前预检进行中活动，主动延后不误点
- 👣 **踩踩 / PK**：踩踩自动跳过已踩过的好友，PK 每个好友打 3 次后自动换下一个

### 🧴 状态照顾

- 🩺 任务前读取体力/清洁/心情，按阈值自动喂食、洗澡（持续按压搓洗，按回合复测，
  连续不提升自动抬手重按自愈，达上限仍不达标则跳过本次洗澡）
- ⚠️ **护理相关勋章要拿的话不能一键**：一键护理不计入勋章进度，
  必须把护理方式配成 `ocr检测` 手动喂食 / 洗澡（`care.method` / `friend_care.method`）

### 🛡️ 控制与恢复

- 🎯 **控制方案**：`injectInputEvent`（默认，真机推荐）/ `minitouch`；
  minitouch 在非 Root / SELinux 不可用时自动回退并写回配置
- 🔁 **异常自动恢复**：页面错误先尝试返回主页，失败按配置重启手机或 QQ，再走官方入口恢复
- 🚨 **失败告警通知**：主任务多次重试仍失败时发 Windows Toast + OnePush 多渠道推送
  （Bark / PushPlus / Server酱 / SMTP / 自定义 webhook），并附当前手机截图

### 📊 界面与数据

- 🖥️ **Fluent 图形界面**：导航（主页/调度/统计/任务/设置）+ 顶部常驻工具栏
  （开始/停止/画面镜像/连接测试/手动重启 + 运行时间）；主页 = scrcpy 实时画面
  （前后台切换镜像常驻不重启，避免手机屏幕闪烁）+ 宠物状态 / 任务队列 / 今日统计 / 日志卡片
- 🐣 **账号与宠物识别**：宠物状态卡显示当前**账号名称 / 宠物名称**；
  换宠物时自动重置全部每日任务进度（历史记录保留）
- 💾 **每日计数持久化**：次数与时长按天记录在 `runs/*.json`（含历史），
  中途停止重跑接着计数，跨天自动归档清零
- 📈 **统计页**：各任务近 N 天次数的平滑折线图；主题支持跟随系统 / 深色 / 浅色
- 🔧 **配置热加载**：调度/任务/设置页可视化改 `config.yaml`，保存后即时生效，无需重启
- 🔍 **分辨率无关定位**：优先 u2 控件选择器 → OCR 文字；纯图形图标（SVIP 企鹅帽、
  好友福袋徽章）走**模板匹配**（截图归一到 1080 宽后多尺寸 `matchTemplate` + 阈值/颜色校验）

---

## 效果预览

```text
[09:05:00] SVIP礼包: 尝试领取每日会员礼包
[09:05:01] SVIP礼包弹窗按钮是"明日再来"：今天已领取过
[09:05:03] 成长福袋：已处理结果（5个福袋共 63 金币，已领2个）
[09:05:03] 福袋累计: 本次 +63 金币，共 63 金币（1 个，宠物 咕咕嘎嘎）
[09:10:12] ===== 第 1 轮（连跑 12 次冒险）=====
[09:10:15] 检测到被雇佣中，等待召回...
[09:12:40] 仍在被雇佣中...
[09:15:02] 被雇佣状态已自然结束（找不到被雇佣面板），返回主页面
[09:15:05] 金币 3400 >= 阈值 2000，去学习
```

---

## 使用说明

### 1. 准备手机

- 开启 **USB 调试** 并连接电脑，首次连接授权本机
- 登录 QQ 并进入过一次宠物页面

### 2. 启动程序

**方式一：直接用打包好的 EXE（推荐，无需 Python）**

1. 把 `QQPetCopilot.exe` 放在一个空文件夹里
2. 打开后在**设置页**选择手机序列号
3. 点**开始**

> 本分支只支持 Android 真机（上游 v1.4 已移除模拟器支持）。
> 配置与运行记录保存在程序旁边。

**方式二：源码运行（需要改代码时）**

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python main.py              # GUI
.venv/Scripts/python scenarios/runner.py  # 控制台模式（无 GUI）
```

scrcpy、OCR 模型与 minitouch 由 `tools/` 下对应下载脚本准备（打包版已内置）。

### 3. 规划任务

用设置页的时间段规划每天的任务，可参考：

| 玩家类型 | 推荐配置 |
| --- | --- |
| 战力玩家 | 每天学习 19 小时左右，剩下 5 小时优先完成 600 次冒险（设置跳过天色） |
| 均衡玩家 | 学习 15 小时 + 冒险 5 小时（600 次）+ 被小号雇佣 4 小时 |
| 职业玩家 | 优先打工拿工分升级职业，职业升级卡住了再去学习 |
| 勋章玩家 | 按勋章要求来，护理记得选 `ocr检测` |

---

## 配置说明（config.yaml 主要项）

| 配置 | 说明 |
| --- | --- |
| `adb.path` / `adb.device_serial` | adb 路径（默认用 scrcpy 自带）/ 设备序列号（空 = 第一台） |
| `gui.theme` / `gui.mirror` | 界面主题（跟随系统/深色/浅色）/ 画面镜像开关状态持久化 |
| `control.method` | `injectInputEvent`（真机推荐，默认）/ `minitouch` |
| `school.attribute` | 属性点课程：力量 / 智力 / 魅力 |
| `school.times_per_day` | 每天学习次数上限，0 不限 |
| `work.location` | 打工地点（下拉 8 选 1） |
| `work.duration` | 打工时长：10分钟 / 45分钟 / 2小时 |
| `work.times_per_day` | 每天打工次数上限，0 不限 |
| `schedule.coin_threshold` | 金币阈值：>= 优先学习，< 先打工 |
| `schedule.daily_hour_limit` | 学习工作时长上限（小时，0=不限） |
| `schedule.check_interval` | 进行中状态的统一检查间隔（秒） |
| `schedule.back_method` | 返回方式：`系统返回`（默认）/ `返回图标` |
| `adventure.times_per_day` / `start_time` / `batch` | 每天冒险次数 / 调度时间 / 单轮连跑次数 |
| `adventure.skip_bad_weather` | 遇到「天色不对」自动召回计入一次冒险 |
| `care.energy_threshold` / `clean_threshold` | 体力 / 清洁阈值，低于则喂食 / 洗澡 |
| `friend_care.*` / `hire_friend.*` | 好友护理 / 雇佣好友的开关、时间段、好友名、间隔、次数 |
| `employed.*` | 被雇佣检查的开关、时间段、间隔、召回策略 |
| `tasks.order` / `tasks.failure_interval` | 任务执行顺序 / 统一失败重试间隔（秒） |
| `tasks.svip.daily_times` | SVIP 礼包每日领取时间 |
| `recover.method` | 异常恢复方式：重启手机 / 重启 QQ 游戏 |

> 旧版 `school_factor` / `work_factor` / `daily_point_limit` 仅用于首次运行迁移老进度
> （次数换算成时长），不再参与调度、也不在设置页显示。

---

## 运行状态文件

| 路径 | 内容 |
| --- | --- |
| `runs/school_progress.json` | 学习次数 + 历史 + 当前学园 + 今日学习时长 |
| `runs/work_progress.json` | 打工次数 + 历史 + 本次时长 + 今日打工时长 |
| `runs/adventure_progress.json` 等 | 冒险/踩踩/PK/被雇佣/雇佣好友/经验日常的每日次数 + 历史 |
| `runs/svip_progress.json` | SVIP 礼包当天是否领取 + 非会员自动关闭标记 |
| `runs/moneybag_stats.json` | 成长福袋累计金币 / 个数及宠物名 |
| `runs/status_cache.json` | 宠物状态缓存（体力/清洁/心情/金币/库存 + 账号名/宠物名） |
| `runs/queue_status.json` | 任务队列状态（当前任务/下一任务/倒计时） |
| `runs/logs/YYYY-MM-DD.log` | 按天的运行日志 |

---

## 单模块测试

```bash
.venv/Scripts/python scenarios/runner.py --test coins              # 主页金币 OCR
.venv/Scripts/python scenarios/runner.py --test recover           # 异常恢复链路
.venv/Scripts/python scenarios/runner.py --test opener            # 官方 scheme 打开宠物主页
.venv/Scripts/python scenarios/runner.py --test work.select_place # 只跑某个阶段方法
.venv/Scripts/python scenarios/runner.py --test care.read_status  # 体力/清洁识别
```

场景脚本也可单独跑：`python scenarios/school.py --times 1`（work / adventure / care 同理）。
回归测试：`python -m unittest tools.test_full_regression`。

---

## 打包 EXE

```bash
.venv/Scripts/python build.py
.venv/Scripts/python build.py --onedir
```

仅生成真机版 `QQPetCopilot.exe`。首次启动生成配置，`runs/` 保存运行数据。

---

## 目录结构

```
main.py               # PyQt6 GUI 入口（Fluent 导航 + 工具栏 + scrcpy 嵌入 + 调度控制）
build.py              # PyInstaller 打包脚本
config.yaml           # 全部可调配置
scenarios/
  runner.py           # 统一调度器（task_queue / legacy 两种引擎）
  school.py           # 学习场景（学园识别、选课、毕业处理）
  work.py             # 打工场景（OCR 选地点、选时长、雇佣好友）
  adventure.py        # 冒险场景（连跑、天色不对召回）
  care.py             # 体力/清洁检查与喂食/洗澡
  visit.py / pk.py    # 踩踩 / PK
  friend_care.py / hire_friend.py / employed.py
  svip.py             # QQ SVIP 会员礼包每日领取（含入口模板匹配）
src/
  u2dev.py            # uiautomator2 封装 + 控制方案 + 掉线重连
  locators.py         # UI 定位注册表（控件选择器 + OCR + 相对坐标兜底）
  scenario.py         # 场景基类：导航、回主页面、延时收尾、被雇佣召回
  moneybag.py         # 成长福袋（模板匹配 + 推荐区域排除）
  recover.py / opener.py / adb/device.py
  ocr.py / coins.py   # RapidOCR 封装 / 主页金币 OCR
  progress.py / progress_store.py  # 持久化入口 / 原子写入与跨天归档
  stats_chart.py / status_cache.py / queue_status.py
  notify.py           # 失败告警（Windows Toast + OnePush）
  settings.py / config.py
resources/
  scrcpy-win64/       # scrcpy 二进制（不入库，build 时准备）
  minitouch/          # minitouch 二进制
  svip-entry.png      # SVIP 入口企鹅帽模板图
tools/                # 资源下载、控件树导出、定位测试脚本
```

---

## 定位方式

界面元素登记在 `src/locators.py` 的 `LOCATORS` 表，按序尝试：
u2 控件选择器（原生弹窗等）→ OCR 文字（游戏内自绘按钮）→ 相对坐标兜底。
纯图形图标走模板匹配：截图归一到 1080 宽后在限定区域多尺寸 `matchTemplate`，与分辨率无关。

游戏更新后识别失败时，用 `--test` 单测真机逐屏校准即可，不用重新截图做模板。

---

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 画面黑屏 / 一直重连 | 手机未授权 USB 调试或线材问题 | 手机确认授权弹窗，换线重试 |
| 找不到 SVIP 礼包入口 | 状态卡图标列布局变化 | 看日志重试次数提示；必要时重裁 `resources/svip-entry.png` |
| 护理不涨勋章 | 用了「一键护理」 | 改 `care.method = ocr检测` |
| 任务一直不执行 | 不在执行时间窗 / 当天次数已满 | 看调度页「下次执行」，或查 `runs/*_progress.json` |
| 前后台切换手机屏幕闪一下 | scrcpy 被重启 | 已改为镜像常驻，若仍出现请带机型与日志反馈 |

> 💡 排查时**不要只看界面有没有报错**：任务没跑通常是时间窗或次数上限问题，看调度页与进度文件。

---

## 鸣谢

- [scrcpy](https://github.com/Genymobile/scrcpy) —— Android 画面镜像与控制
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) —— Fluent Design 组件库
- [RapidOCR](https://github.com/RapidAI/RapidOCR) / [PP-OCRv6](https://github.com/PaddlePaddle/PaddleOCR) —— 文字识别引擎与模型
- [uiautomator2](https://github.com/openatx/uiautomator2) —— Android UI 自动化框架
- [minitouch](https://github.com/DeviceFarmer/minitouch) —— 底层触摸注入
- 上游 [490720818/qq-pet-copilot](https://github.com/490720818/qq-pet-copilot) 与
  [hu181b/qq-pet-copilot](https://github.com/hu181b/qq-pet-copilot) 的原创工作与 v1.4 优化

---

## License

[GPL-3.0](./LICENSE)

<p align="center">
  <sub>本项目仅供学习研究自动化与 OCR 识别技术使用，自动化操作可能违反游戏服务条款，后果由使用者自行承担</sub>
</p>
