# Brain miner

可配置的 WorldQuant BRAIN Alpha 研究工作流：**字段同步 → LLM 生成 → 本地校验 → 队列回测 → 指标筛选 → 变体增强 → 反馈反思**。

这是原有个人挖掘流程的独立、精简重构版，不包含私人账号、历史因子库、平台字段数据或第三方付费论坛文章。不是 WorldQuant 官方项目，也不是自动实盘交易工具。

## 安装

需要 Python 3.11+，以及具有相应模拟和数据访问权限的 BRAIN 账号、支持 OpenAI Chat Completions 接口的 LLM 服务。

```bash
git clone https://github.com/fuzhekai37-lgtm/brain-miner.git
cd brain-miner
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -e .
```

复制 `.env.example` 为 `.env`，填写以下五项（系统环境变量优先）：

```dotenv
BRAIN_EMAIL=your-email
BRAIN_PASSWORD=your-password
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model
```

支持兼容该协议的 OpenAI、Kimi 等服务；不保证每个模型的 JSON 输出或第三方接口均兼容。对本地模型允许 `http://localhost` 或 `http://127.0.0.1`。密钥只发送到你配置的 LLM 地址。

## 首次使用

```bash
brain-miner doctor
brain-miner sync-fields --dataset pv1
brain-miner run --max-simulations 10 --max-llm-calls 3
brain-miner status
brain-miner export
```

`doctor` 是离线检查，不验证登录。`sync-fields` 首次登录 BRAIN 并获取账号可访问的字段；数据集和 universe 需符合自己的权限。初版只向 LLM 提供 MATRIX 字段，暂不自动处理 VECTOR / GROUP 字段。

BRAIN 可能要求额外的人机或身份验证；CLI 会停止并给出说明，不绕过验证。API 行为、账户权限和模型输出可能变化，**当前版本没有完成真实账户端到端验收**，不能承诺只填凭据就能在所有账号无障碍运行。

## 用你的流程开展研究

```bash
# 先从自己的账户同步期权字段（需要权限）
brain-miner sync-fields --dataset option8
brain-miner run --max-simulations 30 --max-llm-calls 5 --workers 1 --enhance --reflect --idea "Compare call-put implied volatility differences and ratios; study smoothing rather than arbitrary complexity"
```

- `--enhance`：对 Sharpe ≥ 0.6 的结果产生均值、排名、标准化和行业中性化变体；负向强信号尝试反向。最多两阶段，不无限嵌套。
- `--reflect`：把近期表达式和摘要指标发送给所配置 LLM，总结研究观察并供后续生成参考。**这会向该服务发送你的研究表达式与指标，不会发送 BRAIN 密码。**
- `--workers`：默认 1，最多 3 个本地回测线程；以账号实际限制为准。它是单账户并行，不是多账号扩容。
- `--max-simulations`：每次命令的提交尝试预算，失败也计入。不是账户全天配额。
- `--max-llm-calls`：候选生成与反思共用预算。

生成器不会自动执行 LLM 输出的 Python，只把限制长度及字段的表达式交给平台。校验是启发式预筛查，不保证表达式编译通过。网络失败与超时不会自动重新提交，以避免重复远端任务。后续再次运行会消费相同研究上下文的 pending 队列；running/failed 不自动重置，应先人工核对平台状态。Ctrl+C 不保证取消远端模拟。

## 参数与可复现记录

`settings.json` 控制 Region、Universe、Delay、Decay、中性化等完整模拟参数。默认 USA/TOP3000、Delay 1、Decay 0、SUBINDUSTRY。修改市场范围后重新同步字段。移动均值和 Decay 是信号平滑，不代表周/月调仓。

SQLite 中的实验键包含表达式、完整 settings 和研究上下文（字段缓存摘要与研究方向）。同一公式在不同设置下可以分别评估；增强子项显式继承父设置并保存真实父 ID。保存原始 Alpha 响应以便以后检查，而不只保存三个指标。字段摘要不是市场数据版本，平台历史数据修订仍可能影响复现。

本地通过：`1.25 < Sharpe <= 5`、`Fitness > 1`、`1% < Turnover < 70%`。Sharpe 上限是异常复核的本地启发式条件，不是平台官方规定。通过不等于平台提交成功、低相关性、样本外稳健或可交易。

## 相关性剪枝（可选、离线）

准备 JSON 列表，每项包含 `id`、`sharpe`、`returns`，其中 `returns` 是 `{"2025-01-02": 0.001, ...}` 形式的**按日期排列的日收益**，不是累计 PnL。然后运行：

```bash
brain-miner prune daily-returns.json
```

按 Sharpe 贪心选择，绝对相关阈值 0.8，至少 30 个重叠日期。缺失或不可比较记录标为 `unknown`，不会冒充已证明独立。初版不自动下载或转换平台 PnL，也不自动将剪枝结果反馈进在线队列。

## 架构与原系统对应

| 模块 | 文件 | 当前实现 |
|---|---|---|
| 配置与凭据 | config.py | 环境变量、完整模拟设置、离线检查 |
| BRAIN / LLM 客户端 | clients.py | 认证、字段分页、模拟轮询、兼容 Chat Completions |
| 数据与队列 | store.py | SQLite WAL、事务领取、父子关系、原始结果 |
| 研究规则 | research.py | 白名单预检、本地筛选、增强与负信号抢救、离线剪枝 |
| 编排与监控 | cli.py | 有预算的生成／并发回测循环、反思、状态及导出 |

原系统中的第三方论坛配方、模糊字段替换、三阶段双信号组合、多账户 worker 和自动配方权重反馈未直接移植。先用自然语言 `--idea` 指定研究方向；对这些能力的扩展需要独立验证，不能把旧代码存在等同于本版本已支持。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试不需要真实凭据、不连接外部服务。覆盖配置、设置隔离、并发领取、原始结果、父子关系、缺失相关性、模拟轮询、预算和增强行为。真实 API 登录、权限及模型兼容需要使用自己的账号小规模验收。

## 数据与开源边界

`.env`、`data/`、数据库和日志均被 Git 忽略。发布前还应检查 staged 文件。不要分享账号、平台原始数据、研究结果或无授权转载内容。本仓库只开放软件实现，数据使用遵守平台及数据提供方条款。MIT 许可见 LICENSE。

后续研究重点：独立样本外、参数敏感性、成本与借券费、多空贡献、持仓集中度、相似表达式和组合增量价值。本项目不代替这些验证。
