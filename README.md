# 视感（Sight Sense）0.1.0

为 LangBot 提供本人电脑上的软件/网站使用日志。由 **Windows 本机采集器、Chromium 浏览器扩展、LangBot 插件**组成。仅运行在本人主动启动的桌面会话，不安装服务或自启动；本次制作不会自动开始采集或发送消息。

## 已实现

- 默认每 5 秒读取前台进程名；连续 5 分钟无键鼠输入、锁屏、采集暂停、采集器退出时不计。后台驻留进程不计。
- 可选扩展报告前台普通标签页的域名，并与对应前台浏览器进程匹配；不读取历史数据库。只有扩展和采集器双方启用才保留域名。
- 当天累计非浏览器软件使用 **≥90 分钟**、浏览器合计使用 **≥60 分钟**，通常在下一次30秒轮询时触发私聊通知；软件各一次、浏览器合计一次，并附已观测的网站明细。单个网站不必达到60分钟。阈值可配置。跨天重新累计，并按时区切分午夜附近的采样。
- 对应本人账号与 bot 的私聊可获得当天摘要，作为本轮回复的观测信息；群聊不会注入。今日/近七天报表按需查询。
- 原始时间段、暂停控制及投递标记存储于本地 SQLite；默认保留 30 天。日志只保存进程名、域名和起止时间，不保存窗口标题、完整 URL、查询参数、网页内容、截图或键盘输入。
- 画像分析待定：没有自动推断性格/心理/兴趣，也没有写入长期记忆。周报仅为按时长排序的描述统计。

## 1. 初始化本机采集器

需要 Windows 10/11、交互式桌面和 Python 3.10+（插件运行环境使用 LangBot SDK）。在 PowerShell 中：

```powershell
Set-Location C:\path\to\sight-sense
python -m pip install -r requirements.txt
python collector.py init
```

这会生成 `collector-config.json` 和 `data/activity.sqlite3`，**不开始采集**。配置文件含浏览器配对令牌，不应上传或分享。该共享目录只用于一个人的一台电脑；不要让多个采集器共用同一数据库。不要放在网盘同步或网络共享盘。

编辑 `collector-config.json`：

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| sample_seconds | 5 | 采样间隔，1–20 秒 |
| idle_seconds | 300 | 无输入多久算离开，30–3600 秒 |
| retention_days | 30 | 采集器保留天数，1–365；插件固定最多保留30天 |
| collect_domains | false | 是否接收网站域名 |
| browser_port | 19876 | 仅监听127.0.0.1；扩展固定19876，修改需同步改扩展代码/权限 |
| excluded_apps | [] | 排除进程，例如 `["wechat.exe"]`，小写 |
| excluded_domains | [] | 排除域名及其子域名，例如 `["example.com"]` |

本人确认后启动：

```powershell
python collector.py run --consent
```

保持该控制台运行，Ctrl+C 停止。修改采集器配置后重启。采样是近似值：切换软件/网站的边界时间段会保守丢弃；超过空闲阈值前的静止阅读/离开仍会计时，超过后静止看视频或阅读会少计；休眠、系统时钟跳变不会补记。不会把“进程运行时长”误当成“用户使用时长”。

## 2. 可选：安装浏览器域名扩展

1. 在采集器配置设置 `collect_domains: true` 并重启采集器。
2. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`，开启开发者模式，加载已解压的 `browser-extension` 目录。
3. 点击扩展图标打开设置，填写本机配置中的 `browser_token`、选择**当前浏览器**，勾选同意并保存。每个浏览器各自配对，不要在同一浏览器多个配置文件重复启用。
4. 正常网页会显示 ON。扩展未安装、未启用、采集器未允许、扩展后台休眠或断连时，只记录软件，不猜测网站。非 HTTP(S) 页面、无痕页不提供域名。Firefox 扩展暂未提供。

扩展只向 `http://127.0.0.1:19876/v1/tab` 发送域名，使用令牌和扩展 Origin 校验。域名按精确主机名统计，`www.example.com` 与 `example.com` 是不同项。标签页切换立即更新，并每5秒发送心跳；超过12秒无心跳即失效。浏览器/扩展调度延迟可能造成短暂漏记或边界误差，不适合作精确工时或审计记录。

## 3. 安装 LangBot 插件

从 [GitHub Release](https://github.com/lin-rog/sight-sense/releases/tag/v0.1.0) 下载 `local-sight-sense-0.1.0.lbpkg`，上传到 LangBot 插件管理。配置 `target_user_id` 为本人私聊账号，`bot_uuid` 选择对应机器人；默认关闭自动推送与上下文读取，配置完成后手动启用 `enabled`。

Docker 中的插件无法直接观察 Windows 桌面，需要显式挂载共享数据目录。此目录当前只有 LangBot 主进程的 `/app/data` 挂载不够，**插件运行时也必须能访问数据**。提供的附加 compose 文件只挂载视感目录：

```powershell
Set-Location C:\path\to\LangBot\docker
# 先用 config 检查合并结果；此命令不修改运行中的服务。
$env:SIGHT_SENSE_DATA_DIR = 'C:/path/to/sight-sense/data'
docker compose -f docker-compose.yaml -f C:/path/to/sight-sense/docker-compose.sight-sense.yaml config
# 在日常启动命令的现有 -f 参数之后追加此附加文件，并按原方式重建插件运行时。
```

插件 `database_path` 使用 `/app/data/sight-sense/activity.sqlite3`。如果插件直接运行在 Windows，改为 `C:/path/to/sight-sense/data/activity.sqlite3`。沙箱/Box 运行方式需要将同一目录挂载到实际插件执行容器；不能仅挂载主进程。没有挂载或采集器尚未初始化时明确报错，不创建一份空数据库掩盖问题。

## 私聊控制

仅绑定本人账号、对应 bot 的私聊生效：

| 命令 | 行为 |
| --- | --- |
| `!视感` | 帮助 |
| `!视感 状态` | 查看启用/暂停及最近采集心跳 |
| `!视感 今日` | 当天累计日志 |
| `!视感 周报` | 今天及之前6个自然日的使用统计 |
| `!视感 暂停` | 暂停采集、自动推送和本轮观测读取 |
| `!视感 恢复` | 恢复；需采集器运行且插件已启用 |
| `!视感 清空` | 删除本地使用记录及投递标记；后续仍可继续采集 |

控制台也支持 `python collector.py status/pause/resume/clear`（每次只传其中一个命令）。停用插件的 `enabled` 只关闭 bot 自动读取和推送；要停止本机记录，使用暂停或退出采集器。暂停后仍可手动查询已有日志。清空不会撤回已经发到聊天平台的消息，或删除 bot 过去回复中引用的信息。

## 投递与数据说明

发送前持久化一次性标记，成功后标为 sent；发送异常/超时标为 uncertain，进程崩溃可能留在 reserved。两种不确定状态均不自动重发，以避免重复通知。因而网络故障或标记后崩溃可能漏发，但可用今日报表补查。离线后恢复会检查当天所有达标对象，旧日期不补发。多个对象同时达标会分别通知。

摘要作为观测数据出现在对应私聊的本轮模型输入中；启用前请确认所用模型服务允许接收这些使用信息。插件不会主动把摘要写入长期记忆，但其它已安装记忆插件可能保存聊天中的通知或回复，需按那些插件自己的功能管理。

## 开发验证

```powershell
Set-Location C:\path\to\sight-sense
python -m unittest discover -s tests -v
node tests/test_extension.cjs
lbp build -o dist
```

本次打包和插件测试使用 `langbot-plugin==0.5.5`，运行开发验证前安装此 SDK。插件按 LangBot 官方事件监听 API 实现：[事件监听器](https://langbot.app/docs/zh/plugin/dev/components/event-listener/)。测试使用合成数据，不采集或发送真实使用日志。
