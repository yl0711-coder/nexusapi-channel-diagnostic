# 资源包使用与 AI 交接手册

这份手册给第一次接手本资源包的 AI 或部署人员使用。先读本文件，再读 `README.md`；不要从源码中的默认值推测部署状态，部署状态以外置状态目录里的 `config.json` 为准。

## 交接结论

- 实际检测模型固定为 `gpt-5.6-sol` 和 `gpt-6-astra`。渠道文档里的原始模型只作来源记录，不改变检测模型。
- 每个模型执行 55 次请求，所以每个启用渠道每轮执行 110 次：Reasoning 30 次、Juice 25 次，两个模型各一套。
- 只支持 OpenAI 兼容的非流式 Responses 和 Chat Completions 接口。
- 代码、配置状态、凭据、SQLite 数据和 HTML 报告必须分开保存；状态目录必须位于源码目录之外。
- `inspect`、`init`、`import`、`enable`、`disable`、`report` 不发送渠道请求。只有 `run-once` 和 `daemon` 在显式带 `--confirm-live` 时才允许真实请求。报告页的“启用全部渠道并启动”按钮也会启动带有 `--confirm-live` 的 daemon。

## 运行环境

按运行方式准备环境：

| 方式 | 必需环境 | 说明 |
| --- | --- | --- |
| 直接运行 Python | macOS 或 Linux；Python 3.10+；标准库；系统时区数据 | 不需要 `pip install`。源码使用文件锁，Windows 不在支持范围内。 |
| Docker Compose | Docker Engine 和 Compose v2；可构建本地镜像并拉取固定摘要的基础镜像 | 主机不需要安装 Python；容器仍需要访问渠道的 HTTPS 地址。 |
| 离线验收 | Python 3.10+、Node.js、Playwright、Chromium | 只用于 `scripts/test_all.py` 的 browser 组，不是线上运行依赖。 |

部署主机还必须满足：

- 能解析并访问目标渠道的 HTTPS 主机；程序主动禁用代理和 HTTP 重定向。
- 状态目录位于源码目录外，`config.json`、`credentials.json` 可读，`data/` 和 `reports/` 可写。
- `credentials.json` 权限为 `0600` 或 `0400`；Docker Compose 下，运行用户的 UID/GID 要能读 secret 并写入数据和报告目录。
- 默认报告服务只监听 `127.0.0.1:8097`；需要外部访问时由部署方另行配置反向代理和访问控制。
- Docker 的报告控制区需要设置高熵随机 `DIAGNOSTIC_CONTROL_TOKEN`（建议至少 32 字节随机数）；未配置时控制健康检查返回 503，所有写操作禁用。报告页输入同一令牌后，才能启用渠道、启动或停止检测；不要把令牌写入 Git、报告、截图或命令历史。公网反向代理只转发预期路径，并保留 HTTPS 与访问限制。
- 默认每个渠道每小时最多执行 110 次请求（两个模型各 55 次）；按渠道数量、超时和保留天数预留磁盘，SQLite 数据会按 `retention_days` 清理。

Docker 部署前可先准备目录和权限：

```bash
mkdir -p "$STATE/data" "$STATE/reports"
chmod 700 "$STATE" "$STATE/data" "$STATE/reports"
chmod 600 "$STATE/config.json" "$STATE/credentials.json"
```

如果 Compose 使用非默认用户，设置 `DIAGNOSTIC_UID` 和 `DIAGNOSTIC_GID`，并让该用户对 `data/`、`reports/` 有写权限。不要通过扩大目录权限来绕过启动失败。

## 资源包和状态包

代码资源包应包含这些文件：

```text
hourly-channel-diagnostic/
├── hourly_channel_diagnostic.py  # 诊断引擎和 CLI
├── channel_catalog.py             # 渠道文档导入器
├── manage.py                      # 初始化、导入、启停和报告命令
├── config.example.json            # 不含密钥的配置模板
├── README.md
├── DEPLOYMENT.md
├── TESTING.md
├── Dockerfile
├── compose.yaml
├── nginx.conf
├── control_server.py          # 报告页控制 API
├── requirements.txt           # 控制 API 的 FastAPI/Uvicorn 依赖
├── scripts/test_all.py
└── tests/
```

上线还需要一个独立的私有状态目录。以下仅为路径结构示例；实际路径须由部署人员从线上配置核实，不可照抄：

```text
/var/lib/hourly-channel-diagnostic/
├── config.json       # 渠道元数据和 test_models
├── credentials.json  # 凭据；必须是 0600 或 0400
├── data/             # diagnostic.sqlite3 和锁文件
└── reports/          # report.html
```

代码资源包不应包含 `credentials.json`、真实 `config.json`、SQLite、报告或原始渠道文档。原始渠道文档可能含真实密钥，只能作为受保护的导入输入。

## 首次部署（非 Docker）

下面的变量只是假设路径，部署 AI 必须替换成实际的绝对路径：

```bash
CODE=/opt/hourly-channel-diagnostic
STATE=/var/lib/hourly-channel-diagnostic
SOURCE=/secure/inbox/onetoken-0.4x
PYTHON=python3
```

如果已经有转换好的状态目录，跳过 `init` 和 `import`，直接从“检查”开始。首次从渠道文档导入时：

```bash
mkdir -p "$STATE"
"$PYTHON" "$CODE/manage.py" init --state-dir "$STATE"
"$PYTHON" "$CODE/manage.py" import --state-dir "$STATE" --source "$SOURCE"
```

导入会生成渠道 ID、服务商、倍率、Base URL 和独立凭据索引；不会发送渠道请求。导入完成后，原始文档仍按部署环境的密钥管理规则处理，不得复制到代码包或 Git。

## 检查模型和渠道状态

先执行只读检查：

```bash
"$PYTHON" "$CODE/manage.py" inspect --state-dir "$STATE"
```

输出必须包含：

```json
"test_models": ["gpt-5.6-sol", "gpt-6-astra"]
```

如果模型列表不同，先修正 `config.json`，再继续。不要把渠道条目里的 `model` 字段当成实际检测模型。

首次部署时所有渠道默认停用。确认要检测的渠道后启用：

```bash
"$PYTHON" "$CODE/manage.py" enable --state-dir "$STATE" --all
```

也可以只启用指定渠道：

```bash
"$PYTHON" "$CODE/manage.py" enable --state-dir "$STATE" \
  --id ch_XXXXXXXXXXXXXXXX
```

启用命令只修改配置并刷新报告，不发请求。当前实现的实际检测模型已经由顶层 `test_models` 明确指定，因此原渠道文档是否写明模型不会阻止启用。

## 启动小时检测

真实单轮检查必须显式确认：

```bash
"$PYTHON" "$CODE/hourly_channel_diagnostic.py" run-once \
  --config "$STATE/config.json" \
  --credentials "$STATE/credentials.json" \
  --data-dir "$STATE/data" \
  --output "$STATE/reports/report.html" \
  --confirm-live
```

上线后持续整点运行：

```bash
"$PYTHON" "$CODE/hourly_channel_diagnostic.py" daemon \
  --config "$STATE/config.json" \
  --credentials "$STATE/credentials.json" \
  --data-dir "$STATE/data" \
  --output "$STATE/reports/report.html" \
  --confirm-live
```

调度器首次等待配置时区的下一个整点；错过的小时不补跑。每个渠道每个模型的 110 次请求串行执行、不重试。请求失败、空响应、畸形 JSON 和截断响应会保留为失败观测，并继续后续请求；`completed` 只表示矩阵执行结束，不表示渠道通过。

## Docker Compose 部署

Compose 将报告服务、管理工具和检测 worker 分开。由旧版升级时先在旧部署目录确认并停止旧 `worker`，再切换新版 `report + control`；不要同时运行旧 `worker` 和新 `control` 的定时任务。该站点尚未正式使用时可以重建容器，但先保留外置 `config.json`、`credentials.json` 与数据目录的受保护备份；不得使用 `down -v` 删除状态数据。新版 Compose 保留 CPU、内存、进程数及容器日志轮转限制，并为检测容器预留 35 秒停机宽限，以便写入中断状态和刷新报告。

```bash
export DIAGNOSTIC_STATE_DIR=/var/lib/hourly-channel-diagnostic
docker compose build
docker compose --profile monitor up -d
docker compose ps
```

- `control` 提供报告页的启动/停止 API；它使用 `/state/config.json`、私有 Compose secret 中的凭据、`/state/data` 和 `/state/reports`，并将定时检测的启停意愿写入权限 `0600` 的 `/state/control_state.json`。首次部署该文件不存在时不会自动发请求；控制容器重启后只恢复此前明确启动的定时任务，不重放单轮。
- `report` 默认只绑定 `127.0.0.1:8097`，访问 `/` 或 `/report.html` 查看报告；容器健康检查直接读取 `/report.html`，报告缺失或因目录权限不可读时不会误报健康。
- `report`、`worker` 和 `toolbox` 使用同一组 `DIAGNOSTIC_UID`/`DIAGNOSTIC_GID`。保持状态目录为 `0700` 时，该 UID/GID 必须与目录属主一致。
- `worker` 保留为命令行兼容入口，使用 `docker compose --profile worker up -d` 可绕过网页控制直接等待下一个整点；网页控制部署不要启用此 profile。
- 管理操作通过 tools profile 执行，例如：

```bash
docker compose --profile tools run --rm toolbox inspect --state-dir /state
docker compose --profile tools run --rm toolbox enable --state-dir /state --all
```

凭据文件由 Compose secret 挂载为 `/run/secrets/channel_credentials`，不要把它写入镜像层、环境变量、日志或报告。
部分 Compose 实现会忽略本地文件 secret 的 `uid`、`gid`、`mode` 声明；以宿主机实际文件属主和 `0600/0400` 权限为准，并在启动前确认容器运行 UID 可读取该文件。不要为解决读取失败而开放全局读权限。

启动网页控制服务时：

```bash
# 从部署方的密钥管理系统读取随机令牌，避免把令牌字面值写入命令历史。
printf '控制令牌：'
read -r -s DIAGNOSTIC_CONTROL_TOKEN
echo
export DIAGNOSTIC_CONTROL_TOKEN
docker compose --profile monitor up -d
```

打开报告页，在“运行控制”区域输入同一令牌。点击“启用全部渠道并启动”会先把 16 个渠道设为启用，再启动持续检测；点击“启动检测”只启动已经启用的渠道；“立即执行一轮”会立刻执行完整矩阵，每个启用渠道最多发送 110 次真实请求。持续 daemon 首次执行会等待配置时区的下一个整点；其运行时单轮按钮禁用，需要先停止定时检测。检查 `/api/status` 应显示进程阶段、最近一轮已执行/计划、真实退出码和结束原因；完整报告的小时指标只纳入完整轮次。

## 离线验证和报告

本地验证必须使用独立的新数据目录和 Mock：

```bash
"$PYTHON" "$CODE/hourly_channel_diagnostic.py" run-once \
  --config "$CODE/config.example.json" \
  --data-dir /tmp/hourly-diagnostic-mock \
  --mock
```

完整验收入口：

```bash
PLAYWRIGHT_MODULE=/path/to/playwright \
PLAYWRIGHT_CHANNEL=chromium \
"$PYTHON" "$CODE/scripts/test_all.py" \
  --output /外置/全新验收目录
```

验收结果必须逐组检查 `syntax`、`security`、`domain`、`http`、`catalog`、`control`、`browser`，不能只看最后一行。报告可随时重建：

```bash
"$PYTHON" "$CODE/manage.py" report --state-dir "$STATE"
```

## Tag 与线上部署

GitHub 的 `v*.*.*` tag 工作流只验证测试、镜像构建、控制 API 和资源包；它不会自动更新线上 `diagnostic.nexusapi.link`。推送 tag 后先确认该工作流全部通过，再由部署人员按目标主机的既有流程更新容器。更新前确认旧 `worker` 已停止、外置状态目录有备份，且 `control_state.json` 的自动恢复意愿符合预期。更新后检查容器健康、`/report.html` 与 `/api/status`，并观察首轮完整执行；未授权前不要通过网页或命令触发真实渠道检测。

## 给另一个 AI 的执行边界

1. 开始前读取 `README.md`、`DEPLOYMENT.md`、`TESTING.md` 和当前 `config.json`；记录源码路径、状态路径、候选版本和 `test_models`。
2. 永远把真实凭据留在外置权限目录；不要打印、复制、提交或放入 ZIP 的密钥和原始响应。
3. 不要把渠道条目的来源模型改成检测模型；实际检测模型只能由顶层 `test_models` 控制，并保持为两个指定值。
4. 修改代码后使用全新的外置验收目录跑完整测试；不要复用旧 SQLite 或把 Mock 结果当真实渠道质量。
5. 没有部署负责人明确授权时，不执行 `run-once`/`daemon --confirm-live`，不推送、不发布、不修改远端。

交付记录至少包含：候选源码 SHA、`test_models`、启用渠道数量、状态目录位置、完整测试逐组结果、报告位置和未验证边界。
