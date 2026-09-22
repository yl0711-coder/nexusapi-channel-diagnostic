# 独立诊断与可控部署

目标：修复两个附件脚本整合后的错误统计与异常处理，补齐逐接口、逐档位诊断，并为 Docker 报告页提供受保护的渠道启停控制。

工作目录：`/Users/lmurder/Desktop/api中转站/小时渠道诊断独立版`。当前分支为 `feature/channel-release-package`，远端为 `yl0711-coder/newapi-evaluator`。原始三文件基线为 `2f9e8ea`。适用规范为父目录 AGENTS.md 引用的 AI_RULES v1.0。

范围：独立脚本、报告前端控制区、FastAPI 控制服务、Docker/Compose、使用说明、回归夹具和完整验收入口。保留原始诊断矩阵与默认请求量；元数据补列保留旧记录。控制服务只接受部署令牌，不保存或返回凭据、请求正文。

状态入口：最终交付状态、候选完整 SHA、审查发现及复核结论统一记于证据根的 HANDOFF.md；本文件只维护任务范围和交接入口。

证据根：`/Users/lmurder/Desktop/api中转站/中转站极限测试数据/hourly-diagnostic-fix-zu5uem8m`。`check-1` 为首次沙箱受限记录；`check-2` 为中间版本通过记录，不能替代最终候选。

验收入口：独立任务从候选提交检出，按 TESTING.md 执行完整离线清单与源代码审查。交付以外置 HANDOFF.md 中对应实际 SHA 的记录为准。

未决事项：真实渠道质量、部署目标、令牌配置、容器权限和长期整点运行尚未在目标服务器验证，留给部署阶段；离线控制 API 与浏览器按钮已在候选环境验证。
