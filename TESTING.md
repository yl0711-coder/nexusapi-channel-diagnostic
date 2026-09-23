# 离线验收清单

产品为独立脚本；本仓库完整离线验收以本文件和 `scripts/test_all.py` 为准，不依赖其他项目的测试套件。

## 环境与入口

直接运行诊断引擎仅需 Python 标准库；Docker 报告控制服务需要 `requirements.txt` 中的 FastAPI/Uvicorn。测试额外需要 `requirements-dev.txt`、Node.js、Playwright 与已安装的 Chromium。依赖位置由 `PLAYWRIGHT_MODULE` 和 `PLAYWRIGHT_CHANNEL` 显式提供。缺少浏览器能力必须记为 incomplete，不能跳过后宣称全量通过。

测试环境、临时文件和结果必须位于源码目录外的隔离根，不复用生产数据。统一入口：

```bash
python3 -B scripts/test_all.py --output /绝对路径/全新验收目录
```

该命令须从源码根执行并事先设置浏览器依赖环境变量。它会逐组输出状态，保存命令、退出码、数量、超时、stdout/stderr、Python 版本和源码 SHA256。结果目录必须全新。各组在独立子进程运行，执行器以进程组终止超时子进程；全部预算上限为 300 秒。任何失败、零用例、跳过、丢失结果或非零退出都不能转为 passed。`--suite` 仅用于定位问题，不代表完整验收。

| suite_id | 实际入口 | 验证范围 | 每次代码交付 | 单组上限 |
| --- | --- | --- | --- | --- |
| syntax | `scripts/test_all.py --suite syntax` | 全部 Python AST、浏览器脚本 JS 语法 | 必跑 | 30 秒 |
| security | `scripts/test_all.py --suite security` | 仓库凭据模式、禁止的本地配置与数据库文件 | 必跑；模式扫描不替代人工审查 | 30 秒 |
| domain | `tests/test_domain.py` | 数值/空值、分母、Juice 阈值、异常响应、后续渠道、锁、中断、旧库、HTML 转义、时区、验收失败聚合 | 必跑 | 60 秒 |
| http | `tests/test_http.py` | 实际回环 HTTP → CLI → 解析 → SQLite → HTML；双模型矩阵、顺序、代理、重定向、超时、闸门和受控时钟调度 | 必跑 | 90 秒 |
| catalog | `tests/test_catalog.py` | 合成渠道导入、倍率与模型快照、私有凭据权限、启停渠道 | 必跑 | 60 秒 |
| control | `tests/test_control.py` | 控制 API 令牌校验、启动/停止/启用路由、Mock 子进程实际中断和退出码、报告刷新、异常退出、重启后的定时任务意愿恢复 | 必跑；需安装 `requirements-dev.txt` | 60 秒 |
| browser | `tests/browser_check.cjs` | 完整报告 22 行双模型细分结果、运行控制按钮及检测中/异常状态、进度和退出码、折叠交互、单点图、桌面与 390px 窄屏、横向表格与截图 | 必跑 | 90 秒 |

测试文件注册在统一入口中；新增 `test_*.py` 必须更新清单与发现规则。夹具只允许独立编写于 `tests/`，不读取真实配置、密钥、业务库或渠道响应。HTTP 服务只绑定 `127.0.0.1`，子进程凭据为合成值；浏览器外部 HTTP(S) 请求被阻断。

产品没有配置格式化器或静态类型工具，不宣称这些检查通过。当前没有网络服务部署、外部通知或真实压力测试；本任务不触发这些检查。真实渠道与长期运行需在部署阶段另行授权并保留对应证据。离线控制测试仅使用本机 Mock，不会触发 `--confirm-live`。

## 版本绑定与独立复核

开发测试以 HEAD 和源码指纹绑定；正式独立验收从完整候选 SHA 检出到外置只读副本，在新 venv 和新数据目录运行相同入口。独立审查应覆盖原始三文件版本到候选提交的完整差异、调用链、测试实现和报告。最终结论绑定实际候选 SHA；修复后按影响重跑，保留首次失败证据。
