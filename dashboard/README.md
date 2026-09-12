# AKI 本地 ICU 工作台

这是课程项目的网站操作说明。当前本地工作区已有可运行的患者录入、历史观察、文件交换和数据集预览；AKI 模型、validation 阈值和临床有效性尚未实现。

文档分工：

| 文档 | 负责内容 |
|---|---|
| 本文 | 安装、启动、日常页面操作和常见启动问题 |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 网站架构、后端及模型接入、开发流程 |
| [DATA-CONTRACT.md](DATA-CONTRACT.md) | JSON 字段、时间、修订、指纹、阈值的规范性定义 |
| [VALIDATION.md](VALIDATION.md) | 已执行测试、环境、结果和未验证范围 |
| [HANDOFF-CHECK.md](HANDOFF-CHECK.md) | 原交接总结与实际仓库的历史差异 |

## 安装与启动

环境：Windows、Python 3.11 或更高版本、Node.js 22.13 或更高版本（建议 Node 24）。首次安装需要联网下载依赖；完成后日常运行不需要云服务器或外网资源。

从 GitHub 获取包含网站代码的版本后：

```powershell
git clone https://github.com/kk-235/AIForHealth-AKIEarlyWarning.git
cd AIForHealth-AKIEarlyWarning
.\Setup-AKI.cmd
.\Start-AKI.cmd
```

当前本地网站代码尚未提交或推送时，GitHub 克隆得到的仍是远程版本，不会自动包含本地修改。

`Setup-AKI.cmd` 创建项目内 Python 环境、安装固定版本依赖并构建前端。`Start-AKI.cmd` 启动本地服务并打开 `http://127.0.0.1:8765`。保持终端打开；按 Ctrl+C 停止。关闭终端或重启电脑后，需要重新运行启动脚本。

如果 Python 不在 PATH：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup-windows.ps1 -PythonExecutable "C:\实际路径\python.exe"
```

该 ExecutionPolicy 设置只作用于本次脚本进程。项目移动或改名后若依赖失效，重新运行 `Setup-AKI.cmd`。前端依赖采用当前项目内的实体文件，不依赖指向旧项目目录的替代链接。

默认实时数据目录为 `%LOCALAPPDATA%\AKIWorkbench\data`。更换目录时：

```powershell
.\Start-AKI.cmd --data-dir "D:\AKI-local-data"
```

本地数据目录不要放入同步盘，也不要让多台电脑直接共享同一个 SQLite 文件。端口被占用时，关闭此前的服务，或运行 `Start-AKI.cmd --port 8766` 并访问相应端口。

浏览器显示“127.0.0.1 拒绝建立连接”通常表示服务没有运行。先启动 `Start-AKI.cmd` 并保持终端窗口打开；若终端显示错误，再根据错误处理，而不是只刷新网页。

## 页面使用

- **患者观察：**每位患者一张横向卡片。左侧显示照片或姓名替代图、ID、床位和提醒；中间显示观测曲线；右侧显示预测曲线。支持搜索、提醒筛选和分页。
- **患者详情：**点击患者卡片进入。可切换变量和模型结果系列、加载更长历史、按当时可用信息回放，并查看模型提供的贡献因素及数据新鲜度。
- **输入信息：**登记患者、上传照片、追加一条或多条观测，或导入本站 JSON/ZIP 交换文件。
- **数据集预览：**只读显示项目内 MIMIC CSV 子集，按患者、住院和 ICU 记录匹配心率及血氧。它不写入实时数据库，也不计算风险。

新数据库为空，不会自动加入演示患者或模拟风险。没有模型时，风险区显示等待状态。

## 本地 CSV 数据集预览

在项目根目录创建 `icu_pre_admission_data`，放入：

- `selected_icu_stays.csv`
- `CHARTEVENTS.csv`

启动后访问 `http://127.0.0.1:8765/dataset`，或点击“数据集预览”。当前不读取 `ADMISSIONS.csv`。

本页同时匹配 `SUBJECT_ID`、`HADM_ID` 和 `ICUSTAY_ID`，每次 ICU 记录独立显示。源文件没有姓名和照片时使用患者 ID，不补造身份信息。当前显示心率 `ITEMID 211/220045` 和血氧 `646/220277`，映射依据为 [MIT-LCP MIMIC-III 生命体征代码](https://github.com/MIT-LCP/mimic-code/blob/main/mimic-iii/concepts_postgres/firstday/vitals_first_day.sql)。

MIMIC 源时间没有时区，网页保留原字符串，并用 `CHARTTIME - INTIME` 的小时差作图。负数表示该文件中入 ICU 前的测量。2100 年等日期属于脱敏后的时间，网页不会改成今天，也不会用当前日期判断它是否过期。

网页约每 10 秒重新读取，也可手动刷新。更新 CSV 时应先写完整临时文件，再原子替换。当前预览适用于小型子集：单文件最多 64 MB、200,000 行，入选 ICU 记录最多 2,000 条。完整数据集应先走独立的离线提取流程。

`icu_pre_admission_data` 已被 Git 忽略；克隆仓库到另一台电脑时，需要自行提供获授权的数据文件。

## 数据与模型边界

实时录入和模型交换的字段规范见 [DATA-CONTRACT.md](DATA-CONTRACT.md)，接入步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)。

当前数据质量只显示每项变量最近测量时间、距查看时刻的时长和近一小时记录数，没有综合可信度公式。风险图上红下绿；模型提供的可信度只影响显示显著程度，不改变风险概率。缺少或未锁定阈值时不生成 AKI 警告。

本次没有构建 Windows EXE、安装器或便携运行包。已完成与未完成的测试范围以 [VALIDATION.md](VALIDATION.md) 为准。
