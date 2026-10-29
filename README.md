# 费用分摊与结算系统（Splitwise Clone）

## 在线演示

本地 `python manage.py runserver` 启动后，用浏览器访问下面 5 个页面（Django 模板 + Bootstrap 5，无需额外构建）：

| 页面 | 地址 |
| --- | --- |
| 登录 / 注册 | http://127.0.0.1:8000/login/ |
| 我的群组 | http://127.0.0.1:8000/groups/ |
| 群组详情（支出 / 余额 / 结算） | http://127.0.0.1:8000/groups/24/ |
| AI 记账 | http://127.0.0.1:8000/groups/24/ai/ |
| 根路径 | http://127.0.0.1:8000/ → 自动跳转 `/login/` |

> 群组 ID 以「我的群组」里点开的实际地址为准；演示数据中的「演示群」当前是 `24`。

演示账号（已在本机数据库创建，`演示群` 里有一笔 300 元三人 AA 的火锅支出，尚未结算）：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| `demo_alice` | `Demo123456` | 群组管理员（付款人） |
| `demo_bob` | `Demo123456` | 成员 |
| `demo_carol` | `Demo123456` | 成员 |

未配置 `OPENAI_API_KEY` 时，AI 记账页点击「解析」会同时出现灰色提示「AI 功能未启用，请手动记账」和黄色兜底说明——这是解析接口真实返回 `422 {"fallback": "manual", "reason": "AI 功能未启用"}` 的结果；手动记账、余额与结算功能完全不受影响。

## 项目简介

一个基于 Django REST Framework 的后端 API 项目：多人一起花钱后，逐笔记录支出、按等额 / 精确金额 / 百分比自动分摊到每个人，实时算出「谁欠谁」，并用最少的转账笔数给出结算方案；同时集成了基于 LLM 的自然语言记账——说一句「昨天吃火锅，张三垫了 300 三人 AA」就能生成一笔支出草稿，确认后走与手动录入完全相同的校验与落库路径。

核心能力：
- 群组与成员管理（admin / member 两种角色）
- 三种分摊方式，金额全程 Decimal，误差精确到分，合计严格等于总额
- 账本分录（LedgerEntry）驱动余额，支出支持编辑与软删除，余额自动回滚
- 债务简化：把交叉欠款压缩成最少笔数的转账建议
- 净余额 = 支出账本 + 已登记结算（转出加回、收到扣掉）
- 自然语言记账与智能分类（可选，未配置 API Key 时整体自动禁用）

## 技术栈

| 组件 | 选型 |
| --- | --- |
| Web 框架 | Django 4.2+（当前开发环境 5.0） |
| API | Django REST Framework 3.14+，TokenAuthentication |
| 数据库 | PostgreSQL 14+（psycopg 3） |
| 测试 | PyTest + pytest-django |
| AI | OpenAI 兼容接口（openai SDK，JSON mode） |

## 环境要求

- Python 3.10+
- PostgreSQL 14+，且用于跑测试的账号需要有 **CREATEDB 权限**（pytest 会创建 `test_*` 临时库）
- 可选：OpenAI / DeepSeek / 通义千问 的 API Key（不配也能完整使用核心功能）

## 快速开始

### 1. 克隆并创建虚拟环境

```bash
git clone <your-repo-url> cost_settlement && cd cost_settlement
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 创建数据库

用有建库权限的账号执行（这样测试也能自动创建 `test_*` 库）：

```sql
CREATE USER cost_user WITH PASSWORD 'your-password' CREATEDB;
CREATE DATABASE cost_settlement OWNER cost_user;
```

### 4. 准备环境变量

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

编辑 `.env`，至少把 `DATABASE_URL` 改成上一步的库；`OPENAI_*` 不填则 AI 功能自动关闭。

### 5. 执行迁移

```bash
python manage.py migrate
```

### 6. 启动服务

```bash
python manage.py runserver
```

默认地址 `http://127.0.0.1:8000/`，健康检查：`curl -s http://127.0.0.1:8000/api/health/`。

## 环境变量说明

| 变量 | 必填 | 示例值 | 说明 |
| --- | --- | --- | --- |
| `DATABASE_URL` | 是 | `postgres://cost_user:pwd@127.0.0.1:5432/cost_settlement` | 数据库连接串，支持 `postgres://` 与 `sqlite:///`；口令含特殊字符时需 URL 编码 |
| `SECRET_KEY` | 生产必填 | `django-insecure-...` | Django 密钥，生产环境务必换成随机值 |
| `AI_ENABLED` | 否 | `True` | AI 总开关，`False` 时即使配了 Key 也禁用 |
| `OPENAI_API_KEY` | 否 | `sk-xxx` | 不填则 `AIService.is_enabled()` 为 False，AI 端点返回 422 人工兜底 |
| `OPENAI_BASE_URL` | 否 | `https://api.deepseek.com/v1` | 兼容接口地址；OpenAI 官方填 `https://api.openai.com/v1`，通义千问填 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `OPENAI_MODEL` | 否 | `deepseek-chat` | 模型名；OpenAI 可用 `gpt-4o-mini`，通义千问可用 `qwen-plus` |
| `OPENAI_EMBEDDING_API_KEY` | 否 | `sk-dashscope-xxx` | 为空时回落 `OPENAI_API_KEY`；chat 用 DeepSeek 时必须单独配置 |
| `OPENAI_EMBEDDING_BASE_URL` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 为空时回落 `OPENAI_BASE_URL` |
| `OPENAI_EMBEDDING_MODEL` | 否 | `text-embedding-v4` | 必须返回 1536 维；通义需显式传 `dimensions=1536` |

补充说明：

- **不配 `OPENAI_API_KEY` 时 AI 功能自动禁用**，手动记账、余额查询、结算全部照常工作；解析端点会返回 `422 {"fallback": "manual", "reason": "AI 功能未启用"}`。
- `OPENAI_BASE_URL` 留空时按官方默认地址走，因此同一套代码可以在 OpenAI / DeepSeek / 通义千问之间切换，只改环境变量。
- 单次 LLM 调用超时默认 10 秒（`OPENAI_TIMEOUT`），超时后同样走降级。

## 运行测试

```bash
pytest -q
```

测试全部 mock 掉 `openai.OpenAI`，不发起任何外部请求，因此**无需配置 `OPENAI_API_KEY`**；同时不依赖任何真实账务数据，pytest-django 会自建并销毁 `test_*` 数据库。

## API 速查

所有接口以 `/api/` 开头，除注册 / 登录外都需要请求头 `Authorization: Token <token>`。
下面示例以 bash 为例（PowerShell 请使用 `curl.exe`），`$TOKEN` 为登录返回的令牌。

### 认证

```bash
curl -s -X POST localhost:8000/api/auth/register/ -H 'Content-Type: application/json' -d '{"username":"alice","password":"Str0ngPassw0rd!","nickname":"爱丽丝"}'
curl -s -X POST localhost:8000/api/auth/login/ -H 'Content-Type: application/json' -d '{"username":"alice","password":"Str0ngPassw0rd!"}'
curl -s -X POST localhost:8000/api/auth/logout/ -H "Authorization: Token $TOKEN"
```

### 群组

```bash
curl -s localhost:8000/api/groups/ -H "Authorization: Token $TOKEN"
curl -s -X POST localhost:8000/api/groups/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"name":"川西自驾"}'
curl -s localhost:8000/api/groups/1/ -H "Authorization: Token $TOKEN"
```

```bash
curl -s -X PATCH localhost:8000/api/groups/1/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"name":"改名"}'
curl -s -X DELETE localhost:8000/api/groups/1/ -H "Authorization: Token $TOKEN"
curl -s -X POST localhost:8000/api/groups/1/invite/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"username":"bob"}'
```

```bash
curl -s -X POST localhost:8000/api/groups/1/leave/ -H "Authorization: Token $TOKEN"
curl -s -X DELETE localhost:8000/api/groups/1/members/2/ -H "Authorization: Token $TOKEN"
curl -s localhost:8000/api/groups/1/balances/ -H "Authorization: Token $TOKEN"
```

### 支出

```bash
curl -s localhost:8000/api/groups/1/expenses/ -H "Authorization: Token $TOKEN"
curl -s -X POST localhost:8000/api/groups/1/expenses/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"payer":1,"amount":"300.00","split_type":"EQUAL","participants":[1,2,3]}'
curl -s localhost:8000/api/expenses/1/ -H "Authorization: Token $TOKEN"
curl -s -X PATCH localhost:8000/api/expenses/1/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"amount":"600.00"}'
curl -s -X DELETE localhost:8000/api/expenses/1/ -H "Authorization: Token $TOKEN"
```

### 结算

```bash
curl -s localhost:8000/api/groups/1/settle/ -H "Authorization: Token $TOKEN"
curl -s -X POST localhost:8000/api/groups/1/settlements/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"from_user_id":2,"to_user_id":1,"amount":"100.00"}'
curl -s localhost:8000/api/groups/1/settlements/ -H "Authorization: Token $TOKEN"
```

### AI

```bash
curl -s -X POST localhost:8000/api/groups/1/expenses/parse/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"text":"昨天吃火锅，张三垫了300三人AA"}'
curl -s -X POST localhost:8000/api/groups/1/expenses/from-draft/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"payer_id":1,"amount":"300.00","participants":[1,2,3],"split_type":"EQUAL","splits":null,"note":"火锅","category":"FOOD","confidence":0.85}'
curl -s -X POST localhost:8000/api/groups/1/query/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{"question":"上个月吃火锅花了多少钱？"}'
```

## 向量检索

每笔支出都会生成一条 `ExpenseEmbedding`（`content` 是可以排查问题的原文，`embedding` 是 `vector(1536)`，带 HNSW 索引、余弦距离）。

- **生成时机**：`ExpenseService.create_expense` / `update_expense` 末尾后置触发，失败只记 `warning`，绝不影响支出创建；软删除保留向量，检索时用 `expense__is_deleted=False` 排除。
- **历史数据**：`python manage.py backfill_embeddings`（支持 `--group-id`、`--dry-run`）。
- **独立配置**：embedding 可以走与 chat 不同的厂商。例如 chat 用 DeepSeek、embedding 用通义，只需配 `OPENAI_EMBEDDING_API_KEY` / `OPENAI_EMBEDDING_BASE_URL` / `OPENAI_EMBEDDING_MODEL`，不配则自动回落到 `OPENAI_*`。

## AI 问答

`POST /api/groups/{id}/query/`：把一句自然语言问题变成「检索 + 解释」。

- 意图先由 LLM 解析成 `search_expenses`（找支出）或 `ask_balance`（问欠款）；
- `ask_balance` 完全不走 LLM 生成，直接返回 `GroupService.get_user_net_balance` 的确定性结果；
- `search_expenses` 用 pgvector 余弦距离 + ORM 过滤（分类 / 日期 / 付款人）取前 10 条，涉及「总共 / 合计」时金额由 `Sum()` 精确汇总；
- LLM 只负责把结果解释成人话，并且返回的 `sources` 带 `expense_id`，答案可溯源；
- 降级：AI 未启用 / 无法理解 → 422，向量检索不可用 → 503，解释生成失败 → 仍返回检索结果 + 固定文案。

## 设计说明（面试时能讲的部分）

**1. 债务简化算法：双优先队列贪心，最小化转账笔数**
`simplify_debts(net_balances)` 是纯函数：两堆都用负数入堆（heapq 是最小堆），弹出的就是欠款 / 应收绝对值最大的一方，每次转账 `min(欠款, 应收)`，因此每笔至少清零一个人，转账笔数上界是「非零人数 - 1」。金额全程 `Decimal`，输入合计必须严格等于 0，输出前还有一层不变量自检（每人归零、转出合计 == 转入合计、笔数不超上限），不通过就抛异常而不是悄悄返回错误方案。因为不碰 ORM，可以直接脱离数据库测试。

**2. 服务层模式：View 只编排，业务逻辑全在 Service**
视图只做三件事：认证、取当前用户可见的对象、调用 Service、整形响应。输入格式校验在 serializer，跨对象校验与事务边界在 Service（`@transaction.atomic`），异常统一抛 `BusinessError` 由 DRF 异常处理器翻译成 `{"detail", "code"}` 结构。这样同一套业务规则可以被 HTTP 接口、AI 草稿落库、pytest 直接复用。

**3. 软删除 + 账本回滚**
余额不直接存在用户表上，而是由 `LedgerEntry` 累加得出。创建支出时为每个参与人写一条分录（payer 记「垫付 - 自己份额」，其他人记「-自己份额」）；编辑支出时在同一事务内删除旧分录与旧分摊、按新参数重建；软删除时只标记 `is_deleted` 并清空分录、保留分摊历史。这种「账本 + 重算」的做法让余额天然可回滚，也避免了更新余额时的并发脏写。

**4. AI 集成：LLM 只产草稿，落库走正常 Service 路径**
`AIService` 不 import 任何业务 Model，只接收 `{username: user_id}` 这样的纯数据，返回纯 dict；LLM 输出要过 11 条 schema 校验（成员归属、金额精度、分摊合计、置信度等）。三层降级：未配置 Key 直接返回 None、调用异常/超时吞掉并记录类型、结构不合法返回 None——端点统一回 `422 {"fallback": "manual"}`，绝不把堆栈丢给用户。草稿落库时复用 `ExpenseService.create_expense`，AI 没有任何特权校验通道。

**5. 结算口径：净余额 = 支出账本 + 已登记结算**
`Settlement` 不修改 `LedgerEntry`，而是作为独立聚合项参与计算：转出方余额加回、接收方余额扣掉；两两欠款同样用已登记结算做冲减。这样「A 欠 B 100」在 A 实际转账后双方都归零，历史支出与结算记录互不破坏，也方便对账。

**6. 为什么用 RAG 而不是直接调用 LLM**
当前数据量下直接调用也能实现问答，但 RAG 在三个维度上更优——上下文成本随支出数量线性增长、LLM 对长上下文的中间部分注意力衰减、检索结果可附带原始支出 ID 让答案可溯源。RAG 在本项目中只负责「发现与解释」，不参与金额计算、余额判断和权限校验，这些仍然由确定性代码保证。

## 项目结构

```
cost_settlement/            # 项目根目录（manage.py 所在处）
├── cost_Settlement/        # settings / urls / wsgi / asgi
├── apps/
│   ├── core/               # BusinessError、统一异常处理、分页、Decimal 工具
│   ├── users/              # 自定义 User、注册 / 登录 / 登出
│   ├── groups/             # Group、GroupMember、成员管理、余额报告
│   ├── expenses/           # Expense / ExpenseSplit / LedgerEntry、ExpenseService
│   ├── settlements/        # Settlement、simplify_debts、结算登记
│   └── ai/                 # AIService、自然语言解析与智能分类
├── conftest.py             # pytest 共享 fixture
├── pytest.ini
├── requirements.txt
└── .env.example
```

每个业务 app 内部统一为 `models.py / serializers.py / views.py / services.py / urls.py / tests/`，迁移文件在 `migrations/` 下。

## 已知限制 / 后续可优化

- 净余额查询当前是三次聚合（LedgerEntry 一次 + Settlement 转出 / 转入各一次），群组成员很多时可以合并成单次 `UNION ALL` 或物化一张余额表。
- AI 依赖外部接口，不同厂商对 JSON mode 的支持程度有差异；返回结构由代码侧 schema 校验兜底，但复杂长文本的解析准确率仍取决于模型。
- 未做测试覆盖率统计（未引入 pytest-cov），当前以关键业务路径的行为测试为主。
- 结算按「净欠款」上限校验，因此可能出现某笔转账金额大于两人之间的两两欠款（例如 C 欠 A 70、欠 B 30，却按建议直接转 A 100），此时两两视图会显示反向应收，属于净额与两两口径的固有差异。
