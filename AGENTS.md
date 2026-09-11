# 项目维护说明

本仓库为 hu181b 维护的真机优化版，原仓库为 https://github.com/490720818/qq-pet-copilot 。保留 GPL-3.0 许可证与上游历史。当前发行 v1.3，修改概览和使用说明见 RELEASE-v1.3.md。

- 不重新引入模拟器、Frida、门禁改写。
- 关注后台 CPU/内存/视频/磁盘开销，保留后台托管。
- 各配置的数据独立；APP_ROOT 是共享资源根目录，DATA_ROOT/PROJECT_ROOT 是当前配置数据目录。
- 侧栏 48、标题栏 32、图标尺寸保留；四边/四角可缩放。移除 WS_CAPTION 后保留最大/最小化与缩放样式。
- gui.close_action 只控制标题栏 ×，不要拦截程序正常退出、Alt+F4 或配置切换。
- school 导航使用 study 入口，academy_* 出现后不重复点击背景地图，明确等待去上课。
- 不覆盖用户未提交源码、配置和进度；测试用临时目录，外部通知和手机操作应隔离。
- 修改后运行两轮相关回归测试，记录通过、跳过和未覆盖项目；不能把缺失截图算作验证通过。私人运行截图不随源码发布。
- Windows/Python 3.12：pip install -r requirements.txt -c build-constraints.txt。依赖已测试版本不要随意升级。
- 运行 tools/fetch_ocr_models.py、tools/fetch_scrcpy.py，按需 tools/fetch_minitouch.py --arch arm64-v8a；然后 tools/write_version.py --tag v1.3 --repo hu181b/qq-pet-copilot，python -m PyInstaller --noconfirm QQPetCopilot.spec。
- 用 tools/package_release.py 打包白名单文件；不上传 config.yaml、runs、profiles、凭据或维护者本机交接记录。发布包必须在干净目录测试首次启动。

- 配置错误提示使用独立 QMessageBox；持久化共用 src/atomic_file.py。保留进行中 pending，缺少结算退出按钮不得清除或计数；两轮测试包含 test_review_regression、test_profile_dialog。
