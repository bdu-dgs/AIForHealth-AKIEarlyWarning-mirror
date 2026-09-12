# 交接核对记录

本文只保留接手时的历史事实和差异，不作为当前运行、接口或验证说明。当前操作见 [README.md](README.md)，开发接入见 [DEVELOPMENT.md](DEVELOPMENT.md)，当前证据见 [VALIDATION.md](VALIDATION.md)。

## 原总结与实际仓库

原总结记录 `main=860b68b377b3408b771310601de96c6a7368f34b`，并称仓库只有初始 README。实际接手时 main 已为 `fbee3beddbc550cb9a2cc35b5740a1253d897ff6`。

该版本已有研究 README、三份 pipeline notebook、配置和产物说明，以及仅含 Streamlit 入口注释的 `dashboard/app.py`。notebook 代码格为空且没有执行输出，因此当时仍没有模型或可运行网站。上述远程内容已保留。

## 本地实现与旧总结的差异

用户确认技术选择和网站搭建后，本地工作区新增 React/TypeScript、FastAPI、SQLite 网站、Windows 源码安装和启动脚本。原研究 README 中的小时快照及 8/12/24 小时是研究候选设计，不限制网页输入频率和模型 horizon。

总览采用一位患者一张横向卡片，卡内并列观测与预测；详情支持更长历史和回放。后续又增加了与实时数据库隔离的 MIMIC CSV 只读预览。

这些实现及验证状态以当前源码和 [VALIDATION.md](VALIDATION.md) 为准。它们尚未提交或推送时，远程 GitHub 不包含本地成果。

## 仍适用的交接约束

- 比较不同数据截止时间的预测稳定性。
- 在 validation set 选择并锁定 warning 阈值，并与稳定性结果结合。
- 网页随新数据更新风险、贡献因素和趋势。
- 最终目标包含 Windows 完全本地、无需云服务器并可从 GitHub 获取源码运行。
- 模型、综合可信度、具体安装打包和跨电脑机制仍需根据数据及用户决定继续落实。
- 未经明确要求，不构建 Windows 便携包、EXE 或安装器。
- GitHub 与本地冲突时，列出具体冲突并保留本地成果。
- 不把方案、占位接口、读取成功或演示结果写成已实现模型或临床验证。

本记录不包含凭据、密钥或患者原始数据。
