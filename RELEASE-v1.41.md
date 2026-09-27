# QQ 宠物助手 v1.41

Windows x64 / Android 真机便携版。基于[原项目](https://github.com/490720818/qq-pet-copilot)维护，保留原作者贡献与 GPL-3.0 许可证。

## 下载与使用

下载本次 Release 的 **QQPetCopilot-v1.41-windows-x64.zip**，完整解压到有写入权限的目录，双击 **QQPetCopilot.exe**。Python、Qt、ADB、scrcpy 和 OCR 模型已内置。首次使用请连接手机并授权 USB 调试，在手机登录 QQ、进入宠物页面，检查助手中的设备和任务设置后点击“开始”。

更新已有安装时，先退出旧程序，保留原目录的 `config.yaml`、`profiles.json`、`profiles/` 和 `runs/`，再用本版 EXE 替换旧 EXE。不要把 GitHub 自动生成的 Source code 压缩包当作可运行程序。

## 本版更新

- 发现 GitHub 上有更高版本时自动弹出更新提示，提供“打开发布页”和“稍后”；同一版本在一次程序运行期间只提示一次。更新仍由使用者从发布页下载。
- GitHub API 请求受限时，使用同一仓库的公开 Release 页面检查最新正式版本。
- 修复学园和打工课程卡片在新版 QQ 页面中无法定位的问题；每次进入卡片面板重新确认位置，避免复用上一页坐标。
- 保留 v1.4 的成长福袋、好友列表分界、调度与真机优化。

## 来源与许可

- 本版源码与发行：https://github.com/hu181b/qq-pet-copilot
- 原仓库：https://github.com/490720818/qq-pet-copilot
- 许可证：GPL-3.0，全文见 LICENSE。
