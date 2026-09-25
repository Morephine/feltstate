# feltstate 深度评估与缺陷调查报告

> **评估对象**：`Morephine/feltstate` @ `3a9f7f5`（版本 0.2.0a4，Alpha）
> **评估日期**：2026-09-25
> **范围**：`feltstate/` 全部源码（约 14.3k 行）、`tests/`（约 8.4k 行，575 个用例）、`examples/`（约 4.3k 行）、README / PHILOSOPHY / SECURITY / CHANGELOG / `docs/` 集成手册、打包配置。
> **结论先行**：架构思路清晰，工程纪律好（lint / 格式 / 类型检查 / 575 个测试全绿），但在**持久化层**、**时间积分语义**、**信任边界净化**三处存在系统性缺陷。本次共确认 **113 项缺陷**：**严重 1**（一个坏字节即可清空整个记忆库）、**高 14**、**中 48**、**低 50**。每一项都经过可运行脚本复现，并逐条二次复核。

---

## 目录

- [0. 结论速览](#0-结论速览)
- [1. 项目理解](#1-项目理解)
- [2. 评估方法与基线](#2-评估方法与基线)
- [3. 总体评价](#3-总体评价)
- [4. 缺陷详情](#4-缺陷详情)
  - [4.1 严重](#41-严重1-项)
  - [4.2 高](#42-高14-项)
  - [4.3 中](#43-中48-项)
  - [4.4 低](#44-低50-项)
- [5. 测试缺口](#5-测试缺口)
- [6. 修复路线图](#6-修复路线图)
- [附录 A：关键复现脚本](#附录-a关键复现脚本)

---

## 0. 结论速览

### 0.1 质量总评

| 维度 | 评价 | 说明 |
|---|---|---|
| 架构设计 | 优 | 评估与回复分离、"只给状态不下指令"、缓存安全注入等原则在代码中真实落实 |
| 代码规范 | 优 | ruff / ruff format / mypy 全部通过；核心纯标准库；参数集中且附理由 |
| 测试 | 中 | 数量多、运行快（约 5 秒），但偏重稳态与正常路径，多处测试无法失败 |
| 数据完整性 | **差** | 单字节损坏即清空记忆库；特定 Unicode 字符导致事实静默消失；归档事实无法删除 |
| 安全 | 中下 | 仪表盘存储型 XSS 与"只读"却写库；所有写入会把 0600 权限重置为 0644；重定向泄露 API key |
| 行为正确性 | 中下 | 时间积分顺序错误使间隔后的事件几乎被抹掉；渲染的主情绪标签会冻结 |
| 文档一致性 | 中 | 文档诚实且详尽，但多处承诺领先于实现 |

### 0.2 最需要立即处理的问题

1. **C-01** 一个非 UTF-8 字节或一次瞬时读错误 → 整个 `Canon` 被读为空 → 下一次例行 `compact()` 把空列表写回，**全部记忆被删除且不进隔离区**。
2. **H-02** 关键字匹配作用于整条记录的 JSON 文本：`retract("call")` 撤回了 "sister's cat is named Miso"（因为字段名 `recalls` 含 "call"）。这正是暴露给 LLM 的记忆工具接口。
3. **H-01** 含 U+2028 / U+2029 / U+0085 的事实写入"成功"但立刻不可读，并在下一次重写时被物理删除。
4. **H-03** 能被检索到的归档事实无法撤回或更正——"忘掉它"对较暗淡的记忆无效。
5. **H-04** 引擎把"事件之前的沉默"的衰减结算在事件之后：README 快速开始的用法下，间隔十几分钟以上的消息几乎不留痕迹。
6. **H-05** 渲染给回复模型的主情绪标签会冻结：心境效价 −0.24 时仍显示 `mood: content`。
7. **H-06 / H-07** 仪表盘存储型 XSS 可外传整个记忆库；宣称"只读、零写入"却在 GET 请求中改写记忆库，且不校验 Host。
8. **H-08** 所有持久化写入都把受限权限重置为 0644，抵消了 SECURITY.md 自己推荐的防护。
9. **H-09 / H-10** 伴侣层：主动发言失败后无限重发（绕过每日配额）；`say()` 阻塞事件循环并可能与心跳死锁。
10. **H-11 ~ H-14** 生命周期：审计链在第二次保留期修剪后永久验证失败；回收器误删被豁免的行；账本一行撕裂即永久卡死；否定漂移检查在现实长度下几乎不触发。

### 0.3 缺陷统计

| 子系统 | 严重 | 高 | 中 | 低 | 合计 |
|---|---:|---:|---:|---:|---:|
| 记忆核心（canon / keyweb / ladder / skill / extract / context） | 1 | 3 | 14 | 10 | 28 |
| 引擎·状态·情感动力学（engine / state / config / affect / sleep / dream / timeawareness） | 0 | 2 | 14 | 10 | 26 |
| 记忆生命周期（consistency / smelt / fingerprint / clocks / drill / gc / reaper / chain） | 0 | 4 | 12 | 8 | 24 |
| 伴侣编排层（companion） | 0 | 2 | 5 | 9 | 16 |
| 情感源·网络·仪表盘·示例 | 0 | 2 | 3 | 10 | 15 |
| 横切（持久化权限）·打包·测试 | 0 | 1 | 0 | 3 | 4 |
| **合计** | **1** | **14** | **48** | **50** | **113** |

---

## 1. 项目理解

### 1.1 定位

feltstate 自称"AI 智能体的角色引擎"：一个纯标准库、带明确观点的参考实现，让长期运行的智能体拥有可持续、可调的"性格"。它由三套互锁的系统加一张"记忆网"组成：

1. **情感引擎**：由独立于回复模型的 `AffectSource` 估计每轮情感（回复模型无法写入自己的持久情感），再在多个时间尺度上积分——快心境、会"释放"的压力条、慢气质、永久烙印、约 180 天尺度的可塑性。
2. **记忆生产线**：逐字对话记录在底层；蒸馏为 5W1H 事实（显著性会衰减、因重复而增强、因被召回而延寿）；可审计的出生到死亡生命周期（指纹、融合谱系、墓碑优先删除、哈希链审计）。
3. **时间纪律**：时间戳由程序而非模型生成；事实为双时态（`valid_at` 与记录时间），可做"截至某时刻的信念"查询。
4. **Key web**：每条事实出生时印上单词键；由 judge 逐对判定亲缘并在两行写入 `relates` 边；`Canon.reach` 以键碰撞进入、沿边收集、按事件时间排序，"链尾即现在"。

外加一个伴侣参考编排层（`companion/`）与一个记忆图查看器（`dashboard.py`）。

三条贯穿全局的设计原则在代码中确实落实了：**估计而非自述**（`source.read`）、**工具而非控制器**（只渲染第一人称状态描述，从不写"现在要难过"）、**缓存安全注入**（`build_injection` 是纯字符串拼接，把 felt block 放在最新用户消息前，不触碰系统提示）。

### 1.2 模块地图

| 包 / 模块 | 约行数 | 职责 |
|---|---:|---|
| `engine.py` | 817 | `Engine` 门面：`tick / render / inject / dream / maybe_dream`；持久化 `state.json` 与 `<stem>.meta.json` |
| `state.py`、`config.py` | 466 / 543 | 数据类模式与 JSON 往返；全部可调参数（"角色配置器"）与 `PersonaDials` |
| `affect/` | 2,030 | `pressure`（五条压力条的积累—释放循环与可塑性）、`traits`（非对称 EWMA 与心境）、`imprint`（永久烙印）、`relationship`、`tide`、`smooth`（标签滞回） |
| `sources/` | 1,580 | `AffectSource` 接口；`KeywordSource`（规则）、`LLMSource`（OpenAI 兼容 HTTP）、`VheartSource`（LoRA 适配器） |
| `render/` | 700 | felt block 渲染、缓存安全注入、agent 渲染、记忆频道标签 |
| `memory/canon.py` | 1,334 | JSONL 事实库：主 / 待定 / 归档三文件，显著性衰减，双时态，召回，线程锁 + `flock` |
| `memory/keyweb.py`、`ladder.py` | 572 / 342 | 词键与边、碰撞 digest、`reach` 链查询；日 → 周 → 月 → 年结晶阶梯 |
| `memory/skill.py`、`extract.py`、`context.py`、`feeling.py` | 575 / 323 / 175 / 95 | 技能区（人类 1/2/3 评分）；LLM 事实抽取；对话上下文回溯；证据加权的事实情感 |
| `memory/lifecycle/` | 1,930 | 一致性闸门、smelt、指纹、老化时钟、drill、死亡计划（gc）、回收器（reaper）、哈希链（chain） |
| `companion/` | 1,740 | `Companion`、`CompanionScheduler`（心跳线程）、`companion_turn`、主动行为源、适配器接口 |
| `dream.py`、`sleep.py` | 317 / 159 | 零 LLM 梦境重组；单一睡眠压力累加器 |
| `dashboard.py` | 289 | 基于 `http.server` 的记忆图查看器 |
| `timeawareness/` | 152 | 模糊时距短语与精确"现在" |

### 1.3 一次回合：`Engine.tick()` 的数据流

1. `source.read(messages, baseline, persona)` → `AffectDelta`（估计值）。
2. `_trusted_reading`：置信度低于 0.2 时清零 `valence` 与 `labels`。
3. `update_traits(elapsed)`：有信号时做非对称 EWMA，再按流逝时间向静息点回拉。
4. `update_mood`：余味混合 + EWMA（每次调用一次）+ 特质引力。
5. `smooth_labels`：主标签滞回（新标签需连续出现 2 次才上位）。
6. 烙印：衰减 → 回响 → 摄入新里程碑 → 一次性特质偏移 → 从烙印重算特质基线。
7. `update_relationship`，再补扣（elapsed − 1）个 tick 的张力衰减。
8. `pressure.step(elapsed)`：阶段到期 → 可塑性愈合 → 积累 → 冷却与下限 → 释放选择 → calm / building。
9. 原始读数写入 `history`（最近 50 条）→ 计算 `tide`。
10. 时间锚点：最后一次真实用户消息的时间，以及"回归时"的时距短语。
11. `tiredness.rise`、梦境残留衰减、`save()`。

**时间模型**：`_REFERENCE_TICK_SECONDS = 60`，`elapsed_ticks = max(1, Δ秒 / 60)`。`config.py` 中所有"每 tick"的速率实际上都是"每分钟"速率。

### 1.4 持久化布局

| 文件 | 写者 | 格式 | 写入方式 |
|---|---|---|---|
| `state.json` | `AffectState.save` | JSON | 固定名 `.tmp` + `replace`，无 fsync，无锁 |
| `<stem>.meta.json` | `Engine._save_meta` | JSON（烙印、标签滞回、梦境、疲劳） | 同上 |
| `canon.jsonl` / `.pending.jsonl` / `.archived.jsonl` | `Canon` | JSONL | 追加（`open("a")`）或 `.tmp` + `replace`；可重入线程锁 + `fcntl.flock`，读不加锁 |
| `*.corrupt*` | 各加载器 | 隔离的坏行 / 坏文件 | — |
| 调度器状态 / `topics.jsonl` | `CompanionScheduler` / `JsonlTopicsStore` | JSON / JSONL | `.tmp` + `replace` |
| 审计账本 / pending | `Chain` / 回收器 | JSONL / JSON | 追加；"原子"写（先 rename 后 fsync） |

### 1.5 记忆子系统

- **Canon 行结构**：`who / what / why / when / where`、`intensity`（出生强度）、`confidence`、`recalls`、`_reinforce_count`、`affect{pos,neg,neu,w}`、`sources`、`birth_affect`、`keys`、`relates`、`valid_at / invalid_at`、`supersedes / _superseded_by / _retracted`。id 不存储，而是由 `sha256("[region|]actor|object")` 计算。
- **显著性**：linear 曲线 `base − age/90 + 0.1×强化 + min(0.2, 0.02×召回)`（技能为 1/180），或 fsrs 拉伸指数曲线；出生强度 ≥ 0.85 永久；分层 visible ≥ 0.30 / archived ≥ 0.10 / forgotten。
- **Key web**：新事实只做一次碰撞——候选的出生强度 × 相关度（至多 2 倍）须超过随年龄差上升的门槛（该公式与文档一致）；judge 决定亲缘；`reach` 键碰撞 → 沿边 → 事件时间排序。
- **Ladder**：日晶体按共享 key 聚类，由 smelt 熔成周 / 月 / 年晶体。
- **Lifecycle 管线**：源证据指针 → 一致性闸门 → smelt（封印 + 出生热度 + 指纹）→ 老化 / 融合 / 谱系 → drill 回溯至对话 → 死亡计划 → 墓碑 → 回收（活库 + 快照）→ 哈希链审计。

### 1.6 伴侣层

- **前台 `say()`**：异步锁 + 线程 `RLock` → `companion_turn`（追加用户轮 → `tick` → `inject` → `[system, *prior, injected]` → `backend.complete` → 追加回复并截断）→ 表情 → 语音。
- **心跳线程**：每 `tick_interval_s`（默认 300 秒）执行 `tick_once`：忙碌门控 → 日配额重置 → `eng.tick([])` 空闲衰减 → 按优先级 `propose` → `dispatch` → 仅在确认送达后 `commit`，每个心跳至多触发一次。

---

## 2. 评估方法与基线

**基线门禁**（均通过）：

```text
python3 -m ruff check .            All checks passed!
python3 -m ruff format --check .   150 files already formatted
python3 -m mypy feltstate          Success: no issues found in 58 source files
python3 -m pytest -q               575 passed in ~5s
```

以上使用与 CI 相同的工具版本（ruff 0.16.9、mypy 2.3.1）。注意：本环境 PATH 上的 `pytest` 与 `ruff` 属于另外的 uv 工具安装——直接运行 `pytest` 会在收集阶段全部报 `ModuleNotFoundError`；PATH 上的 `ruff` 是 0.15.8，不格式化 Markdown 中的 Python 代码块（只检查 123 个文件），而 CI 安装的 ruff 0.16.9 会。因此需用 `python3 -m pytest` / `python3 -m ruff`。

**方法**：

1. 按子系统并行深审（引擎与状态、情感动力学、记忆核心、生命周期、伴侣与渲染、情感源 / 网络 / 仪表盘 / 示例），逐行阅读，要求每条缺陷附可运行复现。
2. **逐条复核**：重新运行每一个复现脚本并阅读对应代码行；对严重 / 高危项另写独立复现（C-01、H-01、H-02、H-04、H-05、H-08 等）。未能复现的不列入本报告；"推断"成分在条目中明确标注。
3. **补充测试**：9,000 次随机化 `tick` 浸泡（值域、有限性、磁盘往返、重启等价性）；wheel / sdist 构建与内容检查；文档内链接与 API 名称交叉检查；Python 3.10 与 3.11 对照；无头 Chromium 驱动的仪表盘测试（阻断全部外网请求）。
4. 全程只读：仓库未被修改（只新增本报告）。

**严重度定义**：

- **严重**：常见条件下造成大范围、不可恢复的数据丢失。
- **高**：文档所述用法下核心功能失效；静默数据丢失或误删；可利用的安全问题；永久卡死。
- **中**：合理场景下结果错误或违背文档契约，并有实际影响。
- **低**：边缘条件、文档漂移、可维护性与测试质量问题。

---

## 3. 总体评价

### 3.1 优点（经验证）

- **边界清晰且真实落实**：估计与回复分离、只提供状态不下指令、缓存安全注入——不是口号，代码结构确实如此。
- **工程纪律**：lint / 格式 / 类型检查全绿；575 个测试 5 秒跑完；核心零依赖；每个参数都附一行理由。
- **诚实的自我认知**：SECURITY.md 如实说明 URL 守卫不防 SSRF、指纹不是加密；README 明确参数是"性格旋钮"而非心理学测量。
- **防御性设计意识**：损坏文件隔离而非静默重置；在 `AffectDelta` 边界拒绝 NaN；2026-07-18 的审计批次修复了大量问题。
- **经验证成立的性质**：
  - `Canon` 的写者锁在 4 进程并发压力下零丢失；
  - 回收器的单事务重放在 6 个崩溃点都幂等；
  - drill 正确处理自环与菱形谱系；规范化 JSON 确定；
  - 记忆频道标签协议对所有 Unicode 换行符都安全；
  - key web 的准入公式与文档一致；README 中 `examples/key_web.py` 的输出与实际逐字一致；
  - 9,000 次随机 tick 下所有状态值保持在界内、有限，磁盘往返一致；重启后的引擎与未重启的引擎只相差持久化舍入（≤ 5e-5，且随后收敛）。

### 3.2 系统性根因

1. **时间语义迁移只完成了一半**（H-04、H-05、M-02、M-03、M-05、M-06、M-11、M-12、M-13、L-02、L-08）：把"每 tick"改为"按流逝时间"后，衰减被结算在事件之后；快心境与标签滞回仍按调用次数计；多处舍入把小增量吃掉；naive / "Z" 时间戳有三套互相矛盾的约定。
2. **持久化层各自为政**（C-01、H-01、H-08、M-04、M-15、M-43、M-47、L-11、L-18）：至少 6 处独立实现"临时文件 + rename"——固定 `.tmp` 名、不 fsync 或顺序错误、不保留权限、不解析符号链接；JSONL 写（`ensure_ascii=False`）与读（`splitlines()`）不对称；读失败被当成"空"并写回。建议抽出一个共享的 `feltstate._io` 模块统一处理。
3. **信任边界净化不完整**（M-01、M-07、M-34、M-38、M-48、L-03、L-20）：`AffectDelta` 只净化三个字段；`max/min` 夹紧会把 NaN 变成极值；`json.loads` 接受 NaN、Infinity 与超大整数；多处"永不抛出"的承诺被 `OverflowError` / `AttributeError` 打破。
4. **文档承诺领先于实现**（H-07、H-14、M-01、M-29、M-31、M-33、L-16、L-29、L-35、L-49）：只读仪表盘、永不抛出、置信度门控、缓存稳定、否定检查、"边落在两行"、精确时钟、版本一致性检查……
5. **测试结构偏向稳态与正常路径**（L-50 与第 5 节）：频率不变性测试只比较收敛后的状态，且事件恰好落在两种频率共享的网格点上；缺少故障注入、跨时区、并发、恶意输入、HTTP 层、仪表盘与示例的测试。

---

## 4. 缺陷详情

每条的格式：**位置 / 现象 / 影响 / 复现（关键输出）/ 修复建议**。行号以 `3a9f7f5` 为准。

### 4.1 严重（1 项）

#### C-01 一个非 UTF-8 字节或一次瞬时读错误即可清空整个记忆库

- **位置**：`feltstate/memory/canon.py:219-223`（`_load_jsonl` 对整个文件捕获 `OSError` / `UnicodeDecodeError` 后返回 `[]`）；写回点 `canon.py:1311`（`compact`）、`canon.py:860`（`confirm`）、`memory/skill.py:425`（`recall_skills`）。
- **现象**：读取失败被当成"库为空"，读—改—写路径把这个空列表当作真实内容写回主文件。
- **触发条件**：崩溃恰好截断一个多字节字符的追加（模块文档承诺"残缺尾行会被隔离"）；用 Latin-1 / cp1252 编辑器手改过一次；任何瞬时 `EIO` / `EMFILE`；Windows 上同步盘或杀毒软件造成的共享冲突。
- **影响**：全部事实（包括强度 ≥ 0.85 的"永久"事实）被永久删除，连 `.corrupt` 隔离文件都不会生成——与模块"坏行隔离、绝不无痕消失"的承诺完全相反。
- **复现**（附录 A.1）：4 条永久事实 → 替换 1 个字节为 `\xe9` → `view()` 返回 `[]` → `compact()` 后 `canon.jsonl` 为 **0 字节**，无隔离文件。另：`compact()` 读取时注入一次 `PermissionError` → 剩余 0 行；`recall_skills()` 重载时遇到一次 `EIO` → 剩余 0 行（事实也一并被删）。
- **修复**：以字节读取、逐行解码，不可解码的行单独以 `invalid-utf8` 原因隔离；读—改—写路径上的 `OSError` 必须向上抛出（例如 `_load_jsonl(path, strict=True)`），绝不能返回 `[]`；`recall_skills` 仅在确有计数变化时才重写。

### 4.2 高（14 项）

#### H-01 ［记忆］含 U+2028 / U+2029 / U+0085 的事实被静默丢弃

- **位置**：写入 `canon.py:370`、`376`（`json.dumps(..., ensure_ascii=False)` 原样写出这三个字符）；读取 `canon.py:220`、`339`（`str.splitlines()` 会在这三个字符处断行）。同样的不对称存在于 `companion/topics.py:89`、`lifecycle/chain.py:109/142/238`、`lifecycle/reaper.py:58`。
- **现象**：`add()` 返回成功，但该行读取时被切成两段非法 JSON → 进入 `.corrupt` → `search` / `recall` 永远查不到 → 下一次重写后从主文件物理消失；同一事实再次添加也会再次被隔离。UTF-8 BOM 也会使第一行被隔离。话题队列中的同类话题同样被永久删除。
- **影响**：从网页、PDF、JS 源码或 LLM 输出粘贴的文本常含 U+2028；这类记忆"写入成功但并不存在"。
- **复现**（附录 A.2）：`c.add("user", "moved to Kyoto in spring")` → 磁盘上 2 个物理行；`search("Kyoto") == []`；生成 `.corrupt`；`compact()` 后主文件不再含 "Kyoto"。
- **修复**：读取改为按 `"\n"`（或字节 `b"\n"`）切分并以 `utf-8-sig` 解码；或写入改用 `ensure_ascii=True`。所有 JSONL 存储统一使用同一个读写助手。

#### H-02 ［记忆］关键字匹配作用于整条记录的 JSON 文本（含字段名与时间戳）

- **位置**：`canon.py:179-181`（`_entry_text = json.dumps(entry).lower()`），被 `_find_active`（`canon.py:664-678`）、`confirm`、`search`、`recall`、`history`、`skill.record_rating` 使用。
- **现象**：任何出现在字段名、时间戳或簿记值里的子串都会命中——`call`（recalls）、`here`（where）、`who`、`at`、`valid`、`source`、`2026`……`retract` / `correct` 作用于第一个命中的活动行，`confirm` 提升所有命中的待定行。
- **影响**：这正是 `examples/memory_tools.py` 暴露给 LLM 的工具接口——模型说"把那个电话的事撤回"，就可能删掉一条毫不相干的记忆。
- **复现**（附录 A.3）：库中没有任何事实提到电话或年份，`retract("call")` 撤回了 "sister's cat is named Miso"；`retract("2026")` 撤回了 "works as a nurse"；`confirm("here")` 提升了 "might move to Berlin" 与 "maybe allergic to cats"。
- **修复**：只在内容投影（actor / action / object / why / when / where / how / keys）上匹配；破坏性操作要求精确 id 或唯一匹配，歧义时返回候选列表而不执行。

#### H-03 ［记忆］归档事实可被检索，却无法撤回或更正

- **位置**：`canon.py:626, 664-678, 692, 1232, 1282, 1323-1334`；`keyweb.py:417-419`。读路径（`search` / `recall` / `history` / `as_of`）包含归档文件，写路径只加载主文件。
- **现象**：`search` 刚返回的归档事实，`retract(id)` / `correct(id)` 却返回 `{}`；重新 `add` 会产生新副本，强化计数、召回数、keys、边与来源全部"消失"，主文件优先的去重把归档版本从 `history` / `as_of` 中隐藏；下一次 `compact()` 的 `_dedup_archive`（后者覆盖前者）直接覆盖归档行，历史被销毁。原地复活的事实停留在归档文件中却处于 visible 层，默认 `view()` 看不到，`compact` 也不会把它移回主文件。
- **影响**：用户说"忘掉它"对较暗淡的记忆无效（隐私请求无法兑现）；审计历史被静默改写。
- **复现**：`search("Sam") → [("ex-partner's name is Sam", "archived")]`，随后 `retract(id) → {}`、`correct(id) → {}`，"忘掉"后 `search` 仍返回它；重新添加并 compact 后，来源 `chat#1 / #9`、keys、强化计数全部丢失。
- **修复**：`_find_active` / `_write_or_reinforce_locked` 依次查主文件与归档（持有两把锁），命中归档时先移回主文件再修改；`compact()` 把重新变为 visible 的归档行移回主文件；`_dedup_archive` 合并而非覆盖。

#### H-04 ［引擎］间隔衰减结算在事件之后——事件被"之前的沉默"吞掉

- **位置**：`engine.py:269`（计算 elapsed）、`285-287`、`319-343`；`affect/traits.py:148-158`（先 EWMA，再 `(1-bp)**ticks` 回拉）；`affect/pressure.py:745-757`（先 `_accumulate`，再 `_decay_and_floor(..., ticks)`）；`engine.py:327-333`（先加里程碑张力，再扣除 k−1 个 tick 的张力衰减）。
- **现象**：`elapsed_ticks` 度量的是本轮事件**之前**流逝的时间，但所有积分器都是先把本轮读数加进去，再把整个间隔的衰减作用上去——等于把事件当作发生在上一次 tick 的时刻。
- **影响**：README 快速开始的用法（每条用户消息 tick 一次、不开心跳）下，引擎对间隔稍长的消息几乎没有反应。
  - 严重度 1.0 的 conflict：上一次 tick 在 1 分钟前 → 张力 0.15；15 分钟前 → 0.01；30 分钟前 → **0.00**。
  - 同一条"悲伤 + 严重度 0.9 的 loss 里程碑"消息：1 分钟后再次出现，悲伤压力条为 0.742；**4 小时后再次出现，仅 0.027**（低于事件前的 0.371）。
  - 沉默 24 小时后说出"my dog died"：抑郁特质抬升 **0.0000**（每分钟空闲 tick 的对照组为 0.0398）。
  - 这同时违背了 `tick()` 文档宣称的频率不变性；现有测试把事件放在两种频率共享的网格点上、且只比较稳态，所以没有发现。
- **复现**：附录 A.4。
- **修复**：先把状态在空闲区间 `ticks − 1` 上老化，再以单位 tick 积分本轮事件。`ticks == 1` 时行为不变（原型补丁下现有 575 个测试全部通过）。

#### H-05 ［引擎 / 渲染］标签滞回在空读数时清空挑战者，且已显示标签永不过期

- **位置**：`affect/smooth.py:88-89`（空读数返回 `(committed, None, 0)`）；`engine.py:291-298`（每次 tick 都调用，包括空闲与被门控的读数）。
- **现象**：新主标签需要连续两次同标签读数才能上位，但中间任何一次空读数都会清零挑战：心跳 `eng.tick([])`（每 300 秒）、`KeywordSource` 对无线索消息给出的 0.15 置信度读数（被门控为空标签）都会打断它。同时没有任何机制让旧标签在沉默后退场。
- **影响**：每轮注入给回复模型的"主情绪"可以与实际相反并长期滞留：
  - 连续 10 个"sad"用户回合（中间各有一次心跳）后，心境效价为 −0.242，渲染却是 `mood: content`；
  - 一次悲伤回合后空闲一天，效价已回到 −0.0，仍渲染 `mood: sad, lonely`。
- **复现**：附录 A.5。
- **修复**：空读数时保留挑战状态（`return list(committed), candidate, streak`），或仅在 `eff.labels` 非空时调用滞回；为已提交标签增加 TTL（如 `MoodConfig.label_ttl_minutes`），心境回到平静带且超时后清除。

#### H-06 ［安全 / 仪表盘］存储型 XSS，可外传整个记忆库

- **位置**：`dashboard.py:195-207`（`openSide` 把 `object / action / actor / why / keys / sources` 及每个亲缘行的 `object / why` 直接拼进 `innerHTML`）、`216-226`（状态视图同样拼接）。
- **现象**：事实文本来自对话（事实抽取、`/remember`）以及 judge LLM 写的 `why`，全部未转义。操作者点击该事实，或搜索其任一 key（`hunt()` 会自动打开链中最新的事实），注入脚本即在仪表盘源中执行，可读取全部 `/api/*` 数据并发往任意地址。vis-network 从 unpkg 加载且无 SRI，页面无 CSP。SECURITY.md 没有提到仪表盘。
- **影响**：在多人或聊天桥接部署（`docs/MULTI_PERSON.md`、`docs/BRIDGE_ETIQUETTE.md` 描述的场景）中，第三方可以通过对话在记忆库中植入载荷。
- **复现**：无头 Chromium，阻断所有外网请求。存入对象 `<img src=x onerror="fetch('/api/graph')...">`，打开事实卡片后 `document.title == "PWNED 929"`，`window.__stolen` 含 `/api/graph` 的完整 JSON。
- **修复**：所有字段先转义（或用 DOM 节点 + `textContent` 构建）；返回 `Content-Security-Policy`（把内联脚本移到 `/app.js`）与 `X-Content-Type-Options: nosniff`；vis 脚本加 `integrity=` 或随包分发。

#### H-07 ［安全 / 仪表盘］宣称"只读、零写入"的仪表盘会改写记忆库，且不校验 Host

- **位置**：`dashboard.py:262-266`（`GET /api/reach` → `Canon.reach` → `_bump_recalls`，`canon.py:1202`）；"只读"声明见 `dashboard.py:1, 7, 18-19` 与 README。
- **现象**：每次搜索都会增加 `recalls`、写 `_last_recalled` 并重写 `canon.jsonl`；每次召回显著性 +0.02（上限 +0.20），可把归档事实抬回可见层。读路径还会创建 `.lock` 与父目录，遇坏行会写 `.corrupt`。不校验 Host 头：DNS rebinding 可让攻击者页面同源读取 `/api/graph`（整个记忆库）；有副作用的 GET 可被任何能访问回环地址的页面用 `<img>` 触发。它还是 SECURITY.md 明确不建议的"第二个写进程"。
- **复现**：
  - 3 次 `GET /api/reach?q=rent` 使 `recalls` +3，`canon.jsonl` 哈希改变；
  - 带 `Host: attacker.example` 的请求照常返回 929 字节的图数据；
  - 攻击页面上的 5 个 `<img>` 把归档事实 "old rent receipt in the drawer" 提升进 `view()`；
  - 在无 `fcntl` 的代码路径（Windows）下，代理写入 400 条事实的同时仪表盘搜索，最终只剩约 210 条。
- **修复**：`Canon.reach / search / recall` 增加 `bump: bool = True`，仪表盘使用 `bump=False` 并走一条不加锁、不隔离的只读加载路径；拒绝 Host 不是绑定地址或 localhost 的请求（403）。

#### H-08 ［安全 / 持久化］所有写入都会把受限权限重置为 0644，并把符号链接替换为普通文件

- **位置**：`state.py:410-412`、`engine.py:815-817`、`canon.py:373-379`、`lifecycle/reaper.py:79-86`、`companion/scheduler.py:165-167`、`companion/topics.py`——都是"新建临时文件 → rename 覆盖"，临时文件使用 umask 默认权限，且不解析符号链接。
- **现象**：SECURITY.md 建议"用文件系统权限限制这些文件"，但下一次保存就会静默撤销：`state.json`、`state.meta.json`、`canon.jsonl` 在 `chmod 600` 后，经过一次 tick 或 `compact()`（甚至一次 `search()`）都回到 `0644`。回收器对快照路径上的符号链接执行 `os.replace`，替换掉的是链接本身，真实备份文件里的"已遗忘"行依然存在。
- **影响**：多用户主机上，其他本地用户可以读取智能体对某个具体的人的感受与记忆；通过符号链接管理的快照无法被真正清除。
- **复现**：附录 A.6（三个文件 `0o600 → 0o644`）。
- **修复**：统一的原子写助手：`realpath` 解析 → `mkstemp(dir=parent)` → 写入 → `flush` + `fsync` → `chmod` 为原文件权限 → `os.replace` → fsync 目录。

#### H-09 ［伴侣］副作用发生后才失败的主动回合会被无限重发

- **位置**：`companion/scheduler.py:220-246`、`companion/app.py:259-262`、`companion/round.py:103-124`、`companion/sources_ref.py:74-81`。
- **现象**：主动回合在调用模型、写入历史、甚至已经播放语音之后才失败（语音或前端适配器抛异常；后端抛异常——自带的 `OpenAICompatBackend` 就会，见 M-32；`commit` 写话题文件失败），调度器便记为"未送达"：不提交、不计 `today_count`、不记 `last_trigger_ts`。同一个心跳接着对每个低优先级源各跑一遍完整回合，下一个心跳全部重来。
- **影响**：
  - 语音适配器抛异常时，1 小时（12 个心跳）内调用模型 24 次，而配置是 `daily_max=2`、`min_gap=30min`；
  - 语音"播完才抛"时，用户在 2 小时内听到同一句话 24 次；
  - 后端连续故障 10 次后，历史里只剩 7 条孤立的 `proactive` 记录和 1 条回复，真实用户对话被 `history_cap` 全部挤掉。
- **修复**：把任何已派发的载荷视为一次尝试——记录退避时间戳并在第一次尝试后 `break`；`PendingTopicsSource.commit` 先 `_mark_ordinary_fire` 再 `mark_consumed`；`companion_turn` 在 `backend.complete` 成功返回后才把用户或主动记录写入历史。

#### H-10 ［伴侣］`say()` 在协程中阻塞等待线程锁：冻结事件循环，并可与心跳死锁

- **位置**：`companion/app.py:182-183`、`205-206`（协程内 `with self._lock:`）；`scheduler.py:185`（派发期间一直持锁）；`app.py:261`（主动回合在心跳线程中用 `asyncio.run` 起临时事件循环）。
- **现象**：心跳持锁执行主动回合（模型调用 + TTS）期间，应用事件循环上的 `say()` 会阻塞整个循环——UI、websocket、其他桥接用户全部停顿。任何需要回到主循环的适配器（websocket / aiohttp 会话属于主循环）都会死锁：心跳等主循环，主循环等心跳的锁。
- **复现**：`say()` 等锁期间，50 ms 节拍器的最长间隔为 1.45 秒；一个用 `run_coroutine_threadsafe` 回到主循环的语音适配器（3 秒超时）让心跳等待 3.0 秒后放弃——这句话从未送出，却被记为已送达（M-29）。适配器若不自带超时则永久挂起。
- **修复**：不在事件循环线程上阻塞（`while not lock.acquire(blocking=False): await asyncio.sleep(0.01)`）；更好的做法是把主动回合 `run_coroutine_threadsafe` 到应用自己的事件循环，线程锁只包住引擎状态修改，不跨越对适配器的 await。

#### H-11 ［生命周期］诚实的审计链从第二次保留期修剪起永久验证失败

- **位置**：`lifecycle/chain.py:259-275`（`_prune` 保留旧 epoch 的同时又在最前面插入新 epoch）、`chain.py:214-218`（`verify_full` 每遇到一个 epoch 行就重置 `prev`）。
- **现象**：第一次修剪插入 epoch E1；第二次修剪时 E1 自身的 `ts` 很新，不会被修剪，新的 E2 被插在 E1 前面。`verify_full` 先读 E2 再读 E1，`prev` 被 E1 覆盖，与第一条存活链接的 `prev` 对不上。
- **影响**：按日巡检、`keep_days=60` 的部署从第 62 天起 `verify_full()` 永远返回 `False`（250 天模拟中 188 天失败，账本里累积了 61 个 epoch 标记）。审计链因此再也无法区分篡改与正常运行——而这是它存在的唯一目的。
- **复现**：一个内容不变的存储、每日 `patrol()`、没有任何篡改（只替换了 `datetime.now`）：第 61 天为 `True`，第 62 天起为 `False`。
- **修复**：`_prune` 插入新锚点前先丢弃已有的 epoch 行（始终只保留一个头部 epoch）；`verify_full` 只接受出现在第一条链接之前、且不带 payload / state 的 epoch（同时修复 M-37）。

#### H-12 ［生命周期］回收器会删除判定器明确豁免的行

- **位置**：`lifecycle/gc.py:106-114`（重复 mid 检查只在可回收行之间比较）；`lifecycle/reaper.py:89-90, 161, 173`（按 `fp.mid` 删除所有匹配的行）。
- **现象**：回填行或无法验证的行若与某个可回收行共享 `mid`，死亡计划中只列出这个 mid，而回收器会把活库与快照中所有带该 mid 的行一并删除——包括被判定为"不可追溯、禁止删除"的行。这与 README"收集器拒绝删除它无法追溯的记录"相反。Canon 会以同一内容派生 id 保留已被取代或撤回的历史行，因此共享 mid 在实践中是可能的。
- **复现**：三行共享 mid `M7`（一行有效、一行回填、一行核心损坏）：计划为 `dead_ids=['M7']`、`skipped_legacy=2`；回收后活库 `[]`、快照 `[]`。
- **修复**：`resolve_deaths` 在可回收的 mid 同时出现在任何其他行时抛 `GCError`；可选地在计划中携带 `fp_id`，回收器只删除 `fp_id` 匹配的行。

#### H-13 ［生命周期］共享审计账本中的一行撕裂会永久卡死回收器与启动重放

- **位置**：`lifecycle/reaper.py:55-58`（严格的 `_read_jsonl`）、`93-97`（`_tombstone_exists`）、`141-156`；`chain.py:187-188`（无 fsync、不检查换行的追加）。
- **现象**：链与回收器向同一个账本追加，且都不 fsync、不补换行。断电留下半行后，`execute` 在写完 pending 文件之后于 `_tombstone_exists` 抛出 `JSONDecodeError`；此后每次启动时的 `replay_if_pending` 都抛同一个异常；链的下一条链接也被并入撕裂行，`verify_full` 变为 `False`。
- **影响**：按 SECURITY.md 的指引"启动时调用 `replay_if_pending`"的应用会在每次启动时抛异常，删除永远无法完成——与"任一步断电都可恢复"的承诺相反。
- **修复**：`_tombstone_exists` 容错解析（跳过无法解析或非对象的行）；账本追加通过一个助手：文件末尾不是换行时先补 `\n`，写入后 fsync。

#### H-14 ［生命周期］一致性闸门的"否定漂移"检查在现实长度下几乎从不触发

- **位置**：`lifecycle/consistency.py:168-177`（按"每 10 个字符"计否定标记）、`241-244`（与绝对阈值 `neg_delta = 0.7` 比较）、`56-59`。
- **现象**：来源中没有否定词时，只有摘要不超过 14 个字符，一个翻转的否定标记才能触发。文档自己的示例（"it worked" → "it failed"）在任何现实长度下都会被接受。阈值看起来是按 CJK（10 字约一个分句）设计后照搬到了英文。弯撇号 `n’t`、`dont`、`doesnt`、`fails` 也不在标记表里。
- **复现**（来源均为肯定句）：`"rin shipped the release and it failed"` → accept；`"… and it did not work"` → accept；`"mia never agreed to the plan"` → accept；`"rin said the deploy to production didn’t work on monday"` → accept。
- **修复**：增加计数条件——摘要中的否定标记数多于全部来源之和即判失败；把 `’` 规范化为 `'` 并扩充标记表。（原型验证：上述用例全部被捕获，现有 16 个一致性测试与 55 个 smelt / ladder / lifecycle / drill 测试仍通过。）

### 4.3 中（48 项）

#### 引擎·状态·情感动力学

| 编号 | 位置 | 问题 | 影响 / 证据 | 修复要点 |
|---|---|---|---|---|
| M-01 | `engine.py:114-118, 352-362` | 置信度门控只清零 valence 与 labels；arousal、`mixed_blend`、`anticipation` 照常积分；tide 与梦境素材使用未门控的原始读数 | 10 次置信度 0.05 的读数：arousal 0.40→0.81，joy 压力条 0→0.82（自定义源带 anticipation 时触发 burst_joy 释放），渲染出 "(dread tinged with glee)"、"sinking"；与文档"不可信读数按空闲 tick 处理、不整合任何东西"相反 | 门控全部通道（arousal 保持当前值、`mixed_blend` / `anticipation` 置空）；history 标记 `trusted=False`，tide 与 `gather_fragments` 跳过不可信条目 |
| M-02 | `affect/traits.py:230-233`；`engine.py:633-652` | 快心境的 EWMA 按调用次数而非流逝时间；梦境残留按分钟衰减，而实际心境按调用衰减 | 只在用户消息时 tick 的应用：沉默 3 天后慢层已完全回归（抑郁 0.694→0.500、压力条→0），快心境仍保留 90% 的负效价，渲染 "mood: sad … still carrying a tense heaviness"；5 分钟心跳下梦境文本 10 分钟就被"忘记"，而心境仍保留 72% 的梦境推动 | EWMA 之前按 `ticks−1` 让心境向中性松弛（或修正文档）；残留追踪使用与心境相同的衰减因子 |
| M-03 | `config.py:519-543`；`engine.py:81` | `agent_scale_config` 的承诺按"步"计，但引擎现在按"分钟"扣衰减；`_REFERENCE_TICK_SECONDS = 60` 不可配置；CHANGELOG 未提及这次重新标定 | 每步都 frustrated 的 16 步卡死：步长 60 秒时 anger 0.672（"strongly … building"），120 秒时 0.402，240 秒时 **0.000**（"steady and settled"） | 新增 `Config.reference_tick_seconds`（`None` 表示按调用），agent 预设设为 `None`；在 config 与 CHANGELOG 中写明"按分钟"语义 |
| M-04 | `state.py:405-412`；`engine.py:813-817` | 所有写者使用同名 `<file>.tmp`，无锁、无 fsync；Companion 的 `RLock` 只保护它自己的 Engine | 两个线程各 300 次 tick：51–88 次 `FileNotFoundError`；轮询读者观察到 32–36 次撕裂 / 部分文件；重启读到撕裂文件时会隔离并以默认人格启动 | `mkstemp` 唯一临时名 + fsync 文件与目录；读—改—写加咨询锁（复用 Canon 的 flock 助手）或按 `generation` 做比较交换 |
| M-05 | `engine.py:363, 379, 648`；`sleep.py:93, 111` | 负的流逝时间被压成 1 tick，但 `last_tick_ts`、`_last_user_ts`、疲劳 `last_update_ts`、残留时间戳都被改写成更早的时刻；时钟恢复后，被跳过的时段会再算一次 | 中间一次 tick 的时间戳早 1 小时：下一次 tick 被计为 61 分钟；anger 0.846→0.000，optimism 0.765→0.523，渲染出 "about an hour since we last spoke"。NTP 校正、虚拟机快照恢复、DST 回拨时的 naive 本地时间都可能触发 | 锚点保持单调：`now < prev` 时记录日志、积分 0、不改写锚点 |
| M-06 | `engine.py:319-343`；`affect/pressure.py:228-231` | 引擎先扣完整段张力衰减，再用期末张力判断慢炖阈值（>0.5 喂 anger、>0.6 喂 boundary），并乘以整个间隔 | 张力 0.7、anger = boundary = 0.75，安静 30 分钟：1 分钟频率下 anger 0.457，30 分钟频率下 0.210 | 对阈值以上的时间做积分，或前 ≤100 分钟按 1 分钟子步老化 |
| M-07 | `state.py:69-75`；`affect/pressure.py:394-396`；`dream.py:175-187`；`engine.py:550-561` | `AffectDelta` 只在构造时净化 3 个字段；里程碑 severity、anticipation、构造后赋值的字段、Fragment 数值都不净化；`max/min` 夹紧把 NaN 变成上界 | `severity=NaN`（`json.loads` 接受）→ 三条压力条 1.0、collapse 释放、最高严重度烙印；构造后赋 NaN → 心境钉在 +1.0、`state.json` 写入 `NaN` 标记，之后每次 `maybe_dream` 都抛 `ValueError`，疲劳永不释放；非 dict 里程碑或 `severity=None/'high'` 直接让 `tick` 抛异常 | 在引擎边界（`source.read` 之后）重新净化所有数值；`dream` 丢弃非有限 Fragment；保存时使用 `allow_nan=False` |
| M-08 | `sleep.py:102-111` | 自加速疲劳的 `math.exp` 在 `level_cap` 之前求值 | 仅当 `self_accel_alpha > 0`：alpha=1、arousal 0.4 时离线约 591 天后，`tick()` / `maybe_dream()` 永久抛 `OverflowError`，重启后依旧 | 先用对数判断是否会超过上限，超过则直接取上限 |
| M-09 | `affect/traits.py:148-149`；`config.py:423, 431` | EWMA 向信号的"水平"靠拢，低于 0.5 的信号反而把特质往下拉 | `KeywordSource` 对 "broken / bug / stuck / crash / ugh" 给出 frustrated（depression 0.4、anxiety 0.4）：60 分钟后 depression 与 anxiety 都降到 0.406，power 从 0.500 升到 0.540（从压抑翻转为表达）；`relieved` 会拉低本已偏高的 optimism | 把映射值当作推动强度：`val += alpha * signal * (1 - val)`，或 `target = 0.5 + 0.5 * signal` |
| M-10 | `affect/imprint.py:118-134`；`affect/relationship.py:28-34, 91-110`；`affect/pressure.py:398` | 里程碑 kind 在三个模块中用三种规则分类（子串负面优先 / 子串正面优先 / 精确匹配加别名） | `broken_trust`、`distrust` → +1 信任烙印；`scared`（含 "care"）、`unloved`、`careless` → +1 关怀烙印并提升信任；`love_betrayal` 在烙印中是伤害、在关系中却提升信任；`grief`、`abandonment`、`deception` 不产生压力。自带的源不产生里程碑，只影响自定义源——而文档鼓励自定义 | 一个共享分类器：精确或整词匹配 + 统一别名表，负面优先，未知 kind 不产生效果 |
| M-11 | `affect/imprint.py:463-466` | 每次衰减都推进锚点，但数值舍入到 4 位小数 | 短于约 1.2 小时的调用周期都把衰减量舍成 0：5 分钟心跳下 7 天后强度仍为 1.0（应为 0.993）；1–3.6 小时之间反而向上取整 | 工作值不舍入，只在 `to_dict` 中保留至少 8 位 |
| M-12 | `affect/pressure.py:217-231, 278-282` | 可塑性"命中"计量的是乘了 ticks 的背景慢炖流入 | depression 固定 0.8、完全空闲 180 天：1 分钟频率下 sadness 敏感度 0.500，5 分钟频率（默认心跳）0.671（增益 ×1.205），60 分钟频率 0.528 | 只对本轮事件流入（标签、效价推动、里程碑）计量命中 |
| M-13 | `affect/pressure.py:245-261, 158` | anticipation 的"下限"每次调用都作为流入加入，不按时间缩放，还计入可塑性命中；缺 `since_ts` 时进度只有 0 或 1 | 1 分钟频率下 3 小时内 7 次 burst_joy 释放；5 分钟频率下 joy 条为 0（自定义源才会产生 anticipation） | 作为冷却之后的下限而非流入；不计命中；缺 `since_ts` 时进度返回 1.0 |
| M-14 | `affect/traits.py:184-189, 258-262` | 引力强度取任一特质偏离的绝对值，而静息点忽略低于基线的特质，静息 arousal 恒 ≥ 0.5 | 同样 25 次悲伤读数：平和者 −0.562，悲观者 −0.400，抑郁者 −0.438（抑郁者反而更不悲伤）；非中性气质都把平静时的 arousal 抬向 0.5 | 按轴分别计算强度与有符号静息点，或让引力只单侧生效 |

#### 记忆核心

| 编号 | 位置 | 问题 | 影响 / 证据 | 修复要点 |
|---|---|---|---|---|
| M-15 | `canon.py:366-370` | `_append_jsonl` 不检查文件末尾是否有换行 | 崩溃留下半行后，下一次 `add()` 返回成功（tier visible），但记录与残片落在同一物理行并被隔离：`search("penicillin") == []`。模块文档声称残缺尾行已被处理 | 追加前检查末字节，不是 `\n` 就先补换行 |
| M-16 | `canon.py:1112`；`skill.py:391` | `recall` 在评分前按文件顺序（最旧优先）截断到 `max(limit*8, 40)` | 45 条暗淡笔记 + 最新一条 0.84 的鲜明事实：`recall` 返回 'coffee note 39 / 38 / 37'，最新的事实不在其中；灰色新技能 300 次抽样一次也没被抽中（违背"探索"） | 先按显著性或权重排序再截断，或全部评分后再截断 |
| M-17 | `canon.py:1104, 194-205` | `recall` 第一阶段要求整个查询是记录的子串，scorer 只能给子串命中的候选打分；默认词法分数对所有存活候选都是 1.0 | `recall("coffee morning") == []`；自定义恒为 1 的 scorer 下 `recall("beverage") == []`、`recall("喜欢咖啡") == []`（事实为"喜欢喝咖啡"）——README 所说"CJK 请接入分词 scorer"的接缝实际无效 | 预过滤改为"任一 token 命中"，或提供 scorer 时跳过子串过滤 |
| M-18 | `keyweb.py:570-571`；`canon.py:983, 1009` | chain / history / as_of 按 ISO 字符串排序，而各行带不同的 UTC 偏移（库写本地偏移：出差、DST 回拨、多进程在不同时区） | 东京写入的旧事实与洛杉矶写入的新事实：`reach()["current"]` 返回旧事实（"链尾即现在"不成立）；`history` 顺序颠倒 | 按解析后的 aware datetime 排序；可选地统一写 UTC |
| M-19 | `canon.py:931-936` | `correct()` 不检查新 id 是否已被活动行占用 | 出现两个 id 相同的活动行，`retract` 只隐藏其中一个；只改 why / when 时 `_superseded_by` 指向自己 | 新 id 已存在则强化该行并让旧行指向它；同 id 更正使用带版本的指针 |
| M-20 | `canon.py:853-858` | `confirm()` 的追加分支原样复制待定行，保留旧的 `ts` 与 0.40 的待定强度 | 28 天后确认的猜测到达时已是 forgotten（0.089），下一次 `compact` 被删除；10 天后确认的已是 archived | 追加时刷新 `ts`，并把强度提升到至少 `default_intensity` |
| M-21 | `ladder.py:188, 219-226, 259` | `cast_day_crystals` 无法处理 Canon 自己的行：原始行没有 id（被跳过）；渲染行没有 who / what（actor 变成 "None"）；读的是当前显著性而非出生强度；已铸造集合不更新 | 晶体文本为 'None (years of work)'；没有 why 的行一个也铸不出；同一事实被铸造并熔炼两次（周晶体为 "ash rent went up; ash rent dispute; ash rent went up"） | 兼容两种行形状，使用 `base_intensity`，铸造后 `already.add(rid)` |
| M-22 | `ladder.py:283, 321, 329/339, 146-147, 236` | `ladder_pass` 把 `now.isoformat()` 当作 `ts_utc` 传给只接受 UTC 的指纹 | aware +02:00 的 now：熔炼 0 次且不计入 waiting；naive now：`TypeError`；`heat_now` 把 naive 当 UTC（与 canon "naive 即本地"的规则矛盾） | 入口处统一转换为 aware UTC，并使用 canon 的 `_parse_ts` |
| M-23 | `extract.py:122, 141, 148, 263-310` | `extract()` 文档称永不抛出，但 `_format_transcript` 与 `_clean_facts` 在 try 之外；解析回退为"第一个 `[` 到最后一个 `]`"；引用编号不校验 | sources 为 `1e400` / `NaN` → `OverflowError` / `ValueError`；消息结构异常 → `AttributeError`；JSON mode 的 `{"facts": [...]}` 包装、`max_tokens` 截断、前后文含方括号 → 0 条事实；不存在的第 7、999 轮被存为来源 | 两处调用移入 try；逐位置 `raw_decode` 并从截断数组中抢救完整项；按有效编号过滤引用 |
| M-24 | `skill.py:137, 364-366, 441-443, 266-275` | 技能 API 只读主文件与待定文件 | 约 2.5 个月未用的已证明技能被归档后，`recall_skills` / `review_skills` 都看不到；`record_rating(<id>)` 找不到，便以十六进制 id 为能力文本新建一个灰色技能 | 技能读取包含归档；32 位十六进制未命中时返回 `{}` |
| M-25 | `canon.py:664-678, 821-828` | `confirm / retract / correct / history / as_of` 不按 region 过滤 | `confirm("tea")` 把技能 "brew tea the gongfu way" 直接提升为已证明（0 次评分）——绕过"技能地位只能由人类评分改变"的闸门；`retract("tea")` 撤回了一个技能 | 增加 `region` 参数（默认 `"fact"`），在 `_find_active` 与 `_confirm_locked` 中过滤 |
| M-26 | `canon.py:1037-1041` | `search(actor=)` 用子串过滤 actor | `docs/MULTI_PERSON.md` 把按人记忆定义为同一存储上的过滤器：`search("is", actor="Al")` 返回 Sally 的 "is in debt" 与 Alice 的 "is pregnant, hasn't told anyone yet" | 精确比较（strip + lower），另设 `actor_contains=` |
| M-27 | `keyweb.py:461` | `digest_canon` 为每个归档行重建一次主文件 id 集合（每行一次 sha256），复杂度 O(main × archive)，且全程持写锁 | 零新条目时：2000/2000 行 6.3 秒，8000/2000 行 28.4 秒；夜间 digest 期间前台 `add()` 被阻塞 26–28 秒——与 README"约 40 万行也只需几秒夜间批处理、对话路径从不付出此成本"相反 | 在循环外只构建一次 `main_ids` |
| M-28 | `canon.py:416-418, 436, 441-442` | 合法 JSON 但字段类型错误的行（手工编辑这份"可审计"文件即可产生）不在隔离范围内 | `intensity=None` 或 `affect.pos=None` → `view()` / `compact()` 抛 `TypeError`；`_reinforce_count=inf` → `OverflowError`——一行坏数据让整库不可用 | 数值字段经有限性检查后强制转换，或以 `bad-field` 原因隔离该行 |

#### 伴侣编排层

| 编号 | 位置 | 问题 | 影响 / 证据 | 修复要点 |
|---|---|---|---|---|
| M-29 | `companion/app.py:226-234, 259-262` | 适配器的"软失败"约定（`synthesize` 返回 None、`should_speak` 返回 False、`push_expression` 返回 False）被派发器忽略，一律返回 True | TTS 宕机或 VAD 判定用户在说话：话题被消耗、配额被扣，但没人听到；`NullVoice`（文档推荐给纯文本 / Discord）下主动回复没有任何输出通道，却每次都被提交——与 INTEGRATION.md"只在真正送达后才标记已消耗"相反 | `_express_and_speak` 返回是否真正发声并逐层返回；为无声应用提供 `on_proactive(result)` 输出钩子 |
| M-30 | `companion/scheduler.py:198-199` | `boot_ts` 只写一次并持久化 | `boot_grace_s`（"启动后的静默窗口，避免唤醒爆发"）只在有史以来第一次运行时生效，之后每次重启的第一个心跳就能触发；首个 tick 时钟前跳会持久化一个未来的 `boot_ts`，此后 60 天内所有受门控的行为都静默 | 启动时间按进程保存（不入持久状态）；夹紧未来的时间戳 |
| M-31 | `companion/round.py:44-52, 115-117, 126-128`；`app.py:78` | 历史达到上限后每轮丢弃最旧一轮，`messages[1]` 每次都变 | 默认 `history_cap=40`：约第 21 轮起，每次请求只有系统提示可以复用缓存，40 条历史全部未缓存重算——与 INTEGRATION.md / PROMPT_STACK.md"先前轮次逐字节不变"相反 | 分块截断（超限时一次删掉一半），让窗口起点在两次截断之间保持固定 |
| M-32 | `companion/backends_ref.py:61-71`；`round.py:103-128` | 文档称"瞬时故障绝不抛出"，但只捕获 `URLError / OSError / ValueError`，解析在 try 之外；4xx 被吞且不记日志 | 连接中途断开 → `IncompleteRead`；JSON 为数组 / null 或 choices 形状异常 → `AttributeError` / `KeyError`；前台 `say()` 在 tick 已持久化、用户轮已追加之后才抛出，重试会重复计入情感估计；401 / 404 返回 `""` 且日志 0 条；空回复被记为 assistant 轮并在后续请求中重放 | 一个 try 同时包住请求与解析（含 `http.client.HTTPException`），用 `isinstance` 校验结构，按状态码记日志；异常时回滚历史，不记录空回复 |
| M-33 | `companion/app.py:145, 203`；`sources_ref.py:224-269` | Companion 总是安装载荷为 `""` 的 `IntrospectSource`，而空载荷会被 `_proactive_say` 丢弃；同优先级的用户 `IntrospectSource` 排在其后，永远不会触发 | 一天内"内省"触发 4 次，到达回复模型的内省提示 0 次；README 第 7 点与 INTEGRATION §4 宣传的内省实际是空操作，还会吃掉窗口、结束当次心跳 | `CompanionConfig` 增加 `introspect_payload` / runner；仅在配置时安装内置源；`extra_sources` 提供同 kind 时跳过内置源 |

#### 情感源与网络

| 编号 | 位置 | 问题 | 影响 / 证据 | 修复要点 |
|---|---|---|---|---|
| M-34 | `sources/llm.py:146, 302`；`sources/vheart.py:158, 199`；`state.py:37-40` | 超过 308 位的整数让 `float()` 抛 `OverflowError`，而 `_delta_from_estimate` 与提示构建在 try 之外 | 模型返回 `{"valence": <400 位整数>}`（退化重复或恶意端点）→ `LLMSource.read()` 抛出（文档称永不抛出），`Engine.tick` 中止 | `_coerce_float` / `_finite` 捕获 `OverflowError`；解析与提示构建移入 try |
| M-35 | `sources/llm.py:199-200`；`memory/extract.py:154-155`；`companion/backends_ref.py:54-61` | `urlopen` 默认跟随重定向：把包括 `Authorization` 在内的请求头发给任意 Location（跨主机、https→http 降级），还会跟随到 `ftp://`；301 / 302 / 303 会把 POST 变成 GET | 实测 `Bearer sk-SECRET-123` 被发到另一主机的 `/collect`；ftp 监听收到 `USER anonymous`——`_net` 存在的理由（只允许 http/https）被绕过；SECURITY.md 只从 SSRF 角度免责，没有提到 token 泄露 | 在 `_net` 中提供禁止重定向的 opener（3xx 以 `HTTPError` 暴露），三个调用点统一使用 |
| M-36 | `sources/vheart.py:357-398` | 没有用户文本时，`VheartSource` 仍对 `Latest user input: ''` 运行完整的 `generate()`，引擎吸收其结果 | 每个心跳（300 秒）都跑一次 GPU 推理；桩适配器下 10 次空闲 tick 把心境从 0 推到 −0.38（'lonely'）；违背 `Engine.tick` 文档"空消息即空闲衰减" | `read()` 开头：没有用户文本就直接返回低置信的中性读数 |

#### 记忆生命周期

| 编号 | 位置 | 问题 | 影响 / 证据 | 修复要点 |
|---|---|---|---|---|
| M-37 | `chain.py:214-218, 118-121` | `verify_full` 对任何位置、任何带 `"epoch": true` 的行都跳过哈希检查 | 不需要重算任何哈希：给篡改过的中间链接加上 epoch 标志即可通过——改写"蒸发"记录、删除链接、改写最新 state 后静默删行，`verify_full` 都返回 True。比 README 所述"只不防御能重算全部哈希的攻击者"的门槛更低 | epoch 只能出现在第一条链接之前，且不得带 payload / state；`_links()` 跳过 epoch 行 |
| M-38 | `fingerprint.py:103-111, 124-126, 262-268` | `verify_fingerprint` 先 strip / float 规范化再哈希；只捕获 `ValueError` 等 | 把 `file` 改成带尾随空格（另一路径）、`t0` 改成 `'\n03:00\t'`、`1.0` 改成 `1`，字节不同却仍验证通过；超大整数的 affect 抛 `OverflowError`，使 `is_collectable` / `resolve_deaths` 在这一行上崩溃（违背"失败关闭"） | 校验后比较原始指针与规范化指针是否一致；捕获 `ArithmeticError` |
| M-39 | `consistency.py:76, 125-126, 234-238` | 数字检查比较的是 `\d+` 片段的集合 | 以下全部被接受：$1,000→$1,000,000；38.5→5.38；−200→200；$3→$3M；3→3 million；2→twelve；日期重排；`when` 时间戳里的数字"支持"了无关数字。反过来，来源写 "two" 而摘要写 "2" 被判可疑 | 能识别小数、千分位、正负号的数字正则，外加数字词与量级词表 |
| M-40 | `consistency.py:137-165` | 来源是晶体（smelt 自己的输出，也是融合与 ladder 的输入）时，`_src_text` 回退为 `str(row)` | 晶体文本里一个数字都没有，却有 51 个来自哈希 / 时间戳的数字片段被当作"证据"；编造了数字 17 的融合摘要被接受；335 字符的摘要对 54 字符真实文本的膨胀比被算成 0.27 | Mapping 使用其 `text` 字段，或只取字符串值（排除 id / hash / ts） |
| M-41 | `smelt.py:183-193`；`gc.py:58-69, 128-130` | smelt 输出 `fingerprint` 而没有 `kind`，gc / reaper / chain 读取的却是 `fp` 与 `kind` | 按原样持久化的晶体（ladder 就是这样做的）永远不会死，也永远不保护其 `src` 中的事实；豁免（不朽）的旧版蒸馏记忆同样不保护其来源——与"课程活着时其事实不可被回收"相反 | smelt 输出 `fp` 与 `kind: "distilled"`，或各处兼容两个键；gc 用非可回收保护者的 `src` 初始化 shield |
| M-42 | `consistency.py:257-282` | 人称漂移检查只看代词 I / he / she / they / you 紧邻动词 | 用名字把事迹挪给自己或对方、来源为混合 actor 时的互换均被接受；原文照抄的"代词 + 动词"反被判可疑 | 把 `self_names` 与来源 actor 名纳入模式；只标记来源中不存在的 actor + 动词对 |
| M-43 | `reaper.py:79-86`；`chain.py:276-278` | "原子 + fsync"写入在数据 fsync 之前就 rename；`_prune` 完全不 fsync（strace 确认顺序；断电丢数据为**推断**） | 在没有 ext4 rename 启发式的文件系统上，rename 与 fsync 之间断电可能留下零长度的存储或账本 | rename 前 fsync 临时文件，rename 后 fsync 目录（与 H-08 共用一个助手） |
| M-44 | `gc.py:62-69, 81-83, 116-118, 149-152`；`chain.py:66, 99-103, 151, 211-221` | 合法 JSON 但结构畸形的行会让判定器与看门狗崩溃 | `fp` 为字符串或列表、`core` 为字符串、`source_ptrs` 含 null → `resolve_deaths` 抛 `AttributeError`；账本中出现 `[1, 2]`、缺 `prev`、`prev=5`、含 NaN 的链接 → `verify_full` 抛异常而不是返回 False；存储中的非对象行 → `patrol` 抛异常 | 各处加 `isinstance(..., Mapping)` 守卫；`verify_full` 对非 dict 返回 False，并用 try 包住重算 |
| M-45 | `drill.py:63, 75-82` | 环检测以节点自身的非空 mid 为键，空 mid 节点每一层都被重新展开 | `{"mid": "", "lineage": ["x"], "src": ["x"]}`：`max_depth=18` 时 52 万次解析调用（7–8 秒）；默认 `max_depth=64` 约 2^65 次，`drill` 永不返回 | 按边的值去重；不展开无 mid 的节点；设置总节点预算 |
| M-46 | `reaper.py:117-132, 170-175, 212` | `execute` 静默覆盖另一个未完成 txid 的 pending 文件；快照路径按原样（相对路径）保存；缺失的快照被静默跳过 | tx-1 中途崩溃后执行 tx-2：tx-1 的快照永久保留已死的行，而 pending 已被清除；从另一个工作目录重放时找不到相对路径的快照，跳过后照样清除 pending | 存在不同 txid 的 pending 时拒绝执行（先重放）；保存 resolve 后的绝对路径；缺失快照视为错误并保留 pending |
| M-47 | `reaper.py:82`；`chain.py:46-47`；`fingerprint.py:78-81, 184` | 孤立代理项转义（例如在 emoji 中间截断的文本，属于合法 JSON）在 `ensure_ascii=False` 序列化或 UTF-8 编码时抛 `UnicodeEncodeError` | 回收器在第 3 步（墓碑已写入）崩溃：行被宣告死亡却仍在活库中，pending 残留，每次重放都崩溃；`Chain.patrol` 崩溃；`verify_source_ptr` / `make_source_ptr` 抛出，而不是返回 False 或 `FingerprintError` | 原样写回未改动行的原始字节，或使用 `ensure_ascii=True`；哈希时使用 `surrogatepass` |
| M-48 | `gc.py:120`；`clocks.py:91-96`；`smelt.py:52-58` | `intensity > death_line` 对 NaN 为 False；`ClockConfig` 接受 NaN 的 gear；`born_heat` 把 NaN 夹紧成最大值 | NaN gear 配置下，1 天大、强度 0.8 的记忆被判死；`intensity_fn` 返回 NaN 即判死（与判定器"失败安全"的姿态相反）；NaN 强度的 smelt 输出 heat 0.9（最高） | 未知即保留：`not isfinite(v) or v > death_line`；在 `__post_init__` 中校验配置；`_intensity` 对非有限值返回 None |

### 4.4 低（50 项）

| 编号 | 位置 | 问题与证据 |
|---|---|---|
| L-01 | `engine.py:491-492`；`timeawareness/relative_time.py:101-102, 108-114` | 间隔超过约 75 天时时间行为 "back on Mar 02 since we last spoke"：不通顺且没有年份，400 天的间隔读起来像一个月；周三 00:30 渲染为 "Wed night 12:30"，容易被理解为周三晚上 |
| L-02 | `sleep.py:92, 119`；`relative_time.py:70`；`affect/pressure.py:675`；`engine.py:489` | 三套 naive / "Z" 时间戳约定：Python 3.10 上 "Z" 后缀使 24 小时疲劳累积为 0、"1 小时前做过梦"被读成 inf 从而绕过 10 小时不应期、时间行为 None、`pressure.step` 直接抛 `ValueError`（3.11+ 正常，而 CI 覆盖 3.10）；`tick()` 把 naive now 当 UTC，`render()` 却当本地时间 |
| L-03 | `state.py:400, 434`；`engine.py:621-625, 749, 754` | 损坏隔离契约的漏洞：`"generation": 1e999` → `Engine()` 启动即抛 `OverflowError`（不在 except 元组中）；数字形式的 `last_tick_ts` 加载时没有任何警告，之后每次 tick 抛 `AttributeError`；NaN / Infinity 被静默加载，一次 tick 后变成极值 |
| L-04 | `state.py:380-402`；`engine.py:449` | 往返丢数据：未知键（新版本或其他工具写入）在旧版本第一次 tick 时被静默丢弃，且没有 schema 版本号；侧车文件被隔离或丢失时，`state.json` 中持久化的烙印基线在下一次 tick 被抹掉（"永久"的提升 3 小时后消失） |
| L-05 | `dream.py:285-300` | 默认梦境素材取历史峰值的第一个标签且不去重：8 次悲伤回合后素材为 6 个 'sad'，梦境形如 "I was sad, and at the same time, sad, and underneath it, sad, and it kept slipping."（梦境文本随机，但模式恒定） |
| L-06 | `state.py`（`to_dict` 舍入） | 每个请求新建 `Engine` 的部署中，每次保存都舍入到 4 位小数，每分钟的微小回归量被舍掉：空闲 24 小时后慢特质停在 0.50995（连续进程为 0.50015） |
| L-07 | `affect/imprint.py:421-424` | 烙印基线对总和夹紧：16 次温暖烙印之后，再来 1 次或 3 次背叛，optimism 基线仍为 0.95——饱和掩盖了相反符号，与"总为相反符号留出余地"的注释相反 |
| L-08 | `affect/pressure.py:355, 365, 379` | 可塑性愈合：陈旧的时间戳把锚点往回拨，重叠时段被愈合两次（`examples/plasticity.py` 第 90 天即如此）；每分钟调用时在偏差约 1.4e-3 处因 8 位舍入而停止愈合 |
| L-09 | `affect/traits.py:240-253`；`config.py:70-78` | A1 动量（默认关闭）：文档称 μ = 0.3–0.5 产生"可信的低谷与缓慢恢复"，实测低谷反而更浅（−0.317 → −0.303），恢复时间不变 |
| L-10 | `affect/pressure.py:617, 625-626` | releasing 相缺少 `release_ends_ts`（部分或手改的状态）时永不结束；aftertaste 的逃逸路径不做 settle，同一 tick 内会再次释放 |
| L-11 | `canon.py:208-255` | 读取不加锁：读者看到并发追加的半行，就把完全合法的记录写入 `.corrupt`（虚假证据）；每个部分快照哈希不同，`.corrupt` 可以无限增长 |
| L-12 | `canon.py:295-310` | `_LOCK_DEPTH` 在 try 之前自增：`flock` 等待被 Ctrl-C 中断后深度永不归零，此后该进程的所有写入都跳过跨进程锁 |
| L-13 | `canon.py:996-1007` | `as_of()` 的 `when` 无法解析时，两个窗口检查都被跳过，返回全部版本（含已撤回、已取代的） |
| L-14 | `skill.py:541-542` | `RatingGate.allow(datetime.now())` 在一次 `stamp` 之后抛 `TypeError`（naive 减 aware） |
| L-15 | `canon.py:445-456` | `skill_gray_decay_per_day`（技能比事实衰减得慢）只在 linear 曲线下生效；fsrs 曲线下技能与事实同速衰减 |
| L-16 | `keyweb.py:442-446, 463-465, 368-372` | 归档候选上的反向边不会持久化（README 说"边落在两行"，但从归档事实进入无法走到其亲缘）；judge（生产中是 LLM）在"边是否已存在"检查之前调用，每次重跑都为已有的边重复付费 |
| L-17 | `skill.py:369, 385` | 温度 0（"纯利用"）被夹紧为 1e-6：存在已证明技能时 `w ** 1e6` 溢出抛 `OverflowError`；否则全部下溢为 0，退化为均匀抽样（497 / 503）——与"利用"相反 |
| L-18 | `canon.py:373-379` | Canon 的重写是"写临时文件 → rename"，不 fsync 文件与目录（而生命周期回收器会 fsync） |
| L-19 | `memory/context.py:80, 87, 129, 171` | `get_turn_context` 不夹紧负的 before / after；字符串 "3" 被当作时间戳比较并命中最后一轮；分钟精度或带 `+00:00` 的"闭区间"不包含边界轮；`load_turns` 遇非 UTF-8 抛出（文档称任何读取错误都返回 `[]`） |
| L-20 | `feeling.py:44`；`extract.py:197, 321`；`keyweb.py:108, 168, 281`；`canon.py:172` | 输入校验：`observe(NaN)` → (1, 0, 0)（与 `sources.llm` 已修复的 NaN 洗白同类）；`commit_to_canon` 不夹紧强度（7 变成永久）；`max_facts=0` 仍返回 1 条；整句 CJK 被当作"单词"键；大小写不同的重复键让一个词就满足 `SharedKeyJudge(2)`；actor / object 中的 `\|` 与整串 strip 导致 id 冲突 |
| L-21 | `companion/scheduler.py:125-146, 162-169, 212` | 调度器状态：合法 JSON 但类型错误（如 `"last_tick_ts": null`）使每个 tick 在衰减之前就抛出，且不隔离；`_save_state` 只捕获 `OSError`，孤立代理项或非 JSON 值让每个 tick 都抛出，状态从此不再持久化（台词却已送达） |
| L-22 | `companion/scheduler.py:171-175, 193-195`；`sources_ref.py` | 日配额与窗口标志以 `now` 所带时区的日期字符串为键，任何不等即重置：向西旅行时 16.5 小时内触发 18 次（`daily_max=8`）；传入 aware UTC 的 now 时按 UTC 小时判断窗口 |
| L-23 | `companion/sources_ref.py:100, 246, 324` | 窗口判断为 `start <= hour < end`：(22, 2) 这类跨午夜窗口被接受，却永远不会打开（`TimeWindowSource`、`IntrospectSource`、`diary_window` 均如此） |
| L-24 | `companion/topics.py:85-134`；`sources_ref.py:74-81` | 话题存储："容忍坏行"不成立——非对象的 JSON 行让每次读取都抛 `AttributeError`；`{"text": null}` 让队列看似为空并阻塞后续话题；非字符串的 text 永远匹配不上，却每天耗尽全部配额；`commit` 重读"最旧未消费"而非 propose 的那一条，可能消耗一条没说出口的话题；没有去重、过期与压缩 |
| L-25 | `companion/round.py:118-122` | 发给后端的是历史中的活 dict：常见的"原地给最后一条前缀消息打 `cache_control` 断点"做法会改写持久历史，断点逐轮累积（0 → 6 个，而 Anthropic 每个请求最多 4 个） |
| L-26 | `companion/round.py:30, 64-69`；`app.py:54-59`；`voice.py:20` | 情感标签取回复中任意位置的第一个 `[word]`（文档说句首）：`[i]`、`[docs]` 被当成标签并从朗读文本中删除；中文、带连字符或带空格的标签既不提取也不删除，会被读出来；`_NON_SPEAKABLE` 本想包含弯引号，却写成了重复的直引号 |
| L-27 | `companion/scheduler.py:218` | `tick_once(now=...)` 把 now 传给各个源（进而传给 `maybe_dream`），却以 `eng.tick([])` 调用引擎而不传 now：模拟或重放时钟下，疲劳在两个时钟之间跳变，每个 tick 多出（墙钟 − 模拟钟）小时，产生虚假的梦 |
| L-28 | `companion/sources_ref.py:319-335` | `DiarySource` 在 `propose` 中运行 `diary_runner` 并设置 `diary_done_date`（契约要求在 `commit` 中设置），派发失败即丢失当天的日记，且不会重试 |
| L-29 | `PHILOSOPHY.md:389`；`engine.py:489-494` | 文档称"精确时钟只随重新接入的那一轮出现"，实际上每轮注入块都带分钟级时钟（"now Fri morning 9:03"），相邻两轮永远不会逐字节相同 |
| L-30 | `_net.py:39-50` | `require_http_url` 对 strip 后的串做校验却返回原串，也不校验端口：带尾随换行或空格（从文件或 `.env` 读取时很常见）或非法端口的 URL 通过了构造，之后 urllib 拒绝每一个请求，`read()` 永远静默返回中性，从不联系端点 |
| L-31 | `sources/llm.py:85-87, 172, 200-201` | `timeout` 按 socket 操作而非整个请求计（`timeout=1` 的滴灌服务器使 `read()` 耗时 6.0 秒，且采用了迟到的答案）；响应体没有上限（100 MiB 使 RSS 从 24 MiB 涨到 224 MiB）；最新的用户消息不截断（22.5 万字符原样进入提示） |
| L-32 | `dashboard.py:216-226` | 状态视图：`bar()` 把值夹到 [0, 1]，valence −0.6 显示为 0.00（看起来是中性）；`walkObj` 在深度 2 截止，文档承诺的压力条从不显示；整数 `generation` 被画成 1.00 的条 |
| L-33 | `dashboard.py:55, 256, 283-284` | 一行非数字的 intensity 或数字类型的 object 让 `/api/graph`、`/api/reach` 直接断开连接（页面空白）；`--host ::1` 抛 `gaierror`；`--port 0` 打印 `:0`；这个"只读"查看器在拼错的 `--canon` 路径下会创建目录与 `.lock` 文件 |
| L-34 | `examples/with_llm.py:257`；`examples/vheart_source.py:25` | 示例把 `state.json` / `state.meta.json` 写进仓库的 `examples/` 目录（无论当前工作目录）或当前工作目录（均被 gitignore） |
| L-35 | `README.md:177-182`；`examples/*`；`docs/INTEGRATION.md:157, 192` | 文档或示例的叙述与实际输出不符：README 快速开始的示例输出（没有 "with you: / mood: / inside:" 前缀，含渲染器不存在的 "warm"、"no friction" 短语）不可能由渲染器产生；`nightly.py` 说暗淡的行已进入归档（实际已被删除且没有归档文件）；`quickstart.py` 关于 `neutral_confidence` 的说明与实测不符；`emergence.py` 说 RES 随显著性轮换，实际 10 轮都是同一条事实；`game_director.py` 在 "lands in plain sight" 之后打印 "every change landed out of sight"；INTEGRATION.md 的对话记录缺少 `TemplateBackend` 总会追加的 "(feeling …)" 后缀；`style_spectrum.py` 说五种状态，实际六种；`vheart_source.py` 说适配器随库发布，而 THIRD_PARTY_NOTICES 说明并未分发 |
| L-36 | `examples/maze_game/director.py:71, 97, 196-199`；`play.py:133-134` | "坏的模型回合永远不会让迷宫崩溃"不成立：裸数组、`actions: null`、字符串 action、`1e400` 坐标、回复为数字，都会逃逸到主循环 |
| L-37 | `sources/keyword.py:498-499, 569-571, 598, 661-662, 675` | 弯撇号（iOS / macOS 智能标点）使所有含撇号的短语失效（`I can’t wait!` 被读成中性）；强化词用裸子串匹配（also 匹配 "so "、every 匹配 "very "）；道歉标志是子串匹配（"he apologized but i'm angry" 得到 angry + relieved 混合）；注释说否定词会降低分数，但并没有实现；`max_labels=5` 返回 5 个（schema 为 0–3），`max_labels=0` 仍返回 1 个 |
| L-38 | `sources/llm.py:19-22, 261`；`sources/vheart.py:124, 172+` | 类型错误的字段不会降低置信度（`{"valence": "very happy", "confidence": 0.95}` → 0.95）；`<think>…{x}…</think>{json}` 或 JSON 后跟含花括号的文字时解析返回 None；vheart 的 nan / inf 权重回退为 0.5（与测试注释相反）；vheart 的 `mixed_blend` 结构（weights 列表）与 `state.py` 文档中的 `primary_score / secondary_score` 不一致 |
| L-39 | `examples/itt_bridge/pad_daemon.py:36, 239-249` | `from ctypes import windll` 位于友好的 ImportError 守卫之前，非 Windows 上报原始 ImportError；无认证的本地控制 API 接受任意 Content-Type 的 JSON，跨站 `text/plain` POST 可以按键或 `/quit`（未实测） |
| L-40 | `chain.py:98-103, 170`；`reaper.py:136-140` | 合法死亡以 `k.split("::")[-1]` 匹配且不区分文件：含 `::` 的 cid 不被自己的墓碑覆盖（误报）；cid `42` 的墓碑覆盖了 `team::42` 的静默删除（漏报）；整数 cid 的回退键含行号，一次合法回收后后续所有行都显示为"蒸发" |
| L-41 | `reaper.py:100-109, 114, 135, 150, 200`；`chain.py:246-247` | `execute()` 不校验输入：默认 `now_iso=""` 的墓碑永不过期，并为重用的 cid 永久"担保"；无法解析的 `now_iso` 让此后每次 `patrol` 在追加链接后抛出；txid 重用静默丢弃第二个墓碑；空 txid 被接受但重放时被拒绝；字符串 `dead_ids` 被 `set("ABC")` 拆成字符；撕裂的 pending 抛 `JSONDecodeError`，而不是文档所述的 `ReaperError` |
| L-42 | `consistency.py:75, 117` | 没有 Unicode 规范化：与来源完全相同的 NFD 摘要（如越南语）anchor 从 0.94 降到 0.12，被判可疑，smelt heat 从 0.82 被打折到 0.492 |
| L-43 | `consistency.py:77, 221-232` | 拼接检测只按标点分句：用 "and" 连接的外来分句被接受；复用来源 8 个词中 1 个的外来分句（0.125 ≥ 0.12）也被接受 |
| L-44 | `gc.py:72-78, 147-162` | 源引用计数把遗留的 16 位十六进制指针与完整指针当成不同来源，而 `verify_source_ptr` 视两者等价：仍被活记忆（经遗留指针）引用的窗口被标记为可清除 |
| L-45 | `reaper.py:79-84, 159-167`；`chain.py:68` | 回收器会重新序列化未改动的存活行：存储若不是以与回收器完全相同的 JSON 设置写入，一次合法回收就会让存活行在链上显示为"被篡改"并报警 |
| L-46 | `chain.py:63-69` | 链只封存 `text` 与 `fp_id`：修改 `fp.backfill`（不朽）或添加 `kind: distilled` 与 `src` 边（保护）会改变死亡计划，却不触发任何警报 |
| L-47 | `clocks.py:91-96` | β 作用于年龄的指数：年龄小于 1 天时负面记忆反而衰减得更快（与"负面记忆走更黏的曲线"相反）；只拒绝 `gear ≤ 0`，而 `base_lambda = 0` 或负数、`β = 0`、`gear = inf` 都被接受（负的 λ 使 10 年后强度达到 2.1e15） |
| L-48 | `pyproject.toml`（`force-include`） | `THIRD_PARTY_NOTICES.md` 被映射到 wheel 根目录，安装为 `site-packages/THIRD_PARTY_NOTICES.md`，污染全局命名空间并可能与其他包冲突（已通过构建 wheel 确认） |
| L-49 | `CHANGELOG.md`（0.2.0a4）；`feltstate/__init__.py:68`；`pyproject.toml:9` | CHANGELOG 称 `__version__` 与 pyproject "今后会一并检查"，但没有任何测试或 CI 步骤做这件事——而 a1–a3 正是因此发布了错误的版本号 |
| L-50 | `tests/` | 无法失败或证明力不足的测试：`test_topics.py:59-68`（从未调用调度器）；`test_sleep.py:137-150`（梦境时间戳固定在某个日期而 tick 使用墙钟，第一次 tick 就把梦遗忘）；`test_traits_decay.py:106-108`（'joyful' 不带抑郁信号，断言靠空闲回拉单独就能满足）；`test_pressure.py:103-135`（没有传 `elapsed_ticks`，传入后断言失败）；`test_vheart_source.py:60-66`（错误总是来自 torch 导入，变异体照样通过）；`test_lifecycle` 的"断电重放"在级联完成之后才重写 pending；`test_drill_three_levels_deep` 无法检测它声称的性质；`test_companion_app.py:187` 使用共享的 `/tmp/state.json` 而不是 `tmp_path` |

---

## 5. 测试缺口

建议优先补充以下回归测试（大多数都能在修复前直接失败，从而锁住修复）：

1. **频率不变性**：事件之前有 N 分钟空白 vs 中间有空闲 tick，比较同一事件的效果（H-04、M-06）；张力超过 0.5 的场景。
2. **标签**：空闲 / 被门控的读数穿插在带标签读数之间；长时间沉默后标签是否退场（H-05）。
3. **持久化故障注入**：不可解码字节、读取时 `OSError`、残缺尾行后再追加、U+2028 / U+2029 / U+0085、BOM、字段类型错误、NaN / Infinity / 超大整数、非字符串时间戳（C-01、H-01、M-15、M-28、L-03）。
4. **文件元数据**：`chmod 600` 后经过一次保存权限保持不变；符号链接存储（H-08）。
5. **Canon 语义**：关键字恰好命中字段名；对归档行执行 retract / correct / add / confirm / record_rating；多于 40 个候选的 recall；自定义 scorer；多种 UTC 偏移下的 chain / history 排序（H-02、H-03、M-16 ~ M-18）。
6. **生命周期**：连续两次以上的保留期修剪；共享 mid；撕裂的账本；epoch 出现在链中间；mid 为空的节点；smelt → gc → reaper 端到端（H-11 ~ H-13、M-37、M-41、M-45）。
7. **一致性闸门**：真实长度的单次否定翻转；千分位、小数、量级词、数字词；以名字表达的人称互换；Unicode 规范化（H-14、M-39、M-42、L-42）。
8. **伴侣层**：副作用之后的派发失败；适配器软失败；在真实事件循环上 `say()` 与心跳并发；达到 `history_cap` 后前缀是否稳定；重启后的 boot grace（H-09、H-10、M-29 ~ M-31）。
9. **安全**：仪表盘 HTML 转义；GET 请求后存储字节不变；Host 校验；HTTP 层的重定向与 Authorization 头（H-06、H-07、M-35）。
10. **流程**：`__version__ == pyproject` 的版本一致性测试；在 CI 中对确定性示例做子进程冒烟测试并比对 README / docs 中引用的输出（L-35、L-49）。

---

## 6. 修复路线图

### P0 — 数据完整性与安全（建议在下一个 alpha 之前完成）

1. **共享 IO 模块**（一次修复一批）：按 `"\n"` 切分、逐行解码；读—改—写路径读失败即抛出；追加前补换行；`mkstemp` + fsync + 保留权限 + `realpath`。覆盖 C-01、H-01、H-08、M-04、M-15、M-43、L-18，并惠及 topics、调度器、账本。
2. **Canon 匹配与归档**：只在内容投影上匹配，破坏性操作要求唯一匹配；写路径覆盖归档；按 region 过滤；actor 精确过滤（H-02、H-03、M-24 ~ M-26）。
3. **仪表盘**：转义 + CSP + SRI；Host 校验；`reach(bump=False)`（H-06、H-07）。
4. **引擎时间积分**：先老化后积分；标签滞回保留挑战并增加 TTL（H-04、H-05）。
5. **伴侣层**：尝试即退避；成功后才写历史；不在事件循环上阻塞；如实返回是否送达（H-09、H-10、M-29）。
6. **生命周期**：只保留一个 epoch 且校验其位置；拒绝共享 mid；账本容错读取 + 补换行 + fsync；否定计数条件（H-11 ~ H-14、M-37）。

### P1 — 正确性与契约

- 统一的时间戳解析助手（处理 "Z"、naive 的明确约定、非字符串），并在 engine / sleep / relative_time / pressure / ladder / skill 中统一使用（L-02、M-22、L-14）。
- 引擎边界的全面净化与 `allow_nan=False`（M-01、M-07、M-34、M-48、L-03、L-20）。
- 快心境按流逝时间；可塑性、烙印、张力、anticipation 的频率不变性（M-02、M-06、M-11 ~ M-13）。
- 标签信号语义与引力（M-09、M-14）；里程碑 kind 统一分类（M-10）。
- 一致性闸门：数字、晶体来源、人称、Unicode（M-39、M-40、M-42、L-42）；smelt 与 gc 的模式统一（M-41）。
- 网络：禁止重定向；"永不抛出"的承诺真正兑现（M-32、M-34、M-35、M-23）。
- 伴侣层：boot grace 按进程计；分块截断历史；内省可配置（M-30、M-31、M-33）。

### P2 — 低优先级与卫生

- 第 4.4 节的低严重度项；文档与示例同步（L-35）；打包修正（L-48）；版本一致性测试（L-49）；修正无法失败的测试（L-50）；把确定性示例纳入 CI 冒烟测试。

---

## 附录 A：关键复现脚本

以下脚本在仓库外的临时目录运行即可复现（需 `pip install -e .`）。输出为本次评估的实际结果。

### A.1 C-01：一个坏字节清空整个记忆库

```python
import logging

logging.basicConfig(level=logging.ERROR)
from pathlib import Path
from feltstate import Canon

p = Path("wipe/canon.jsonl")
c = Canon(p)
for obj in ("likes oolong tea", "has a cat named Mochi", "works as a nurse", "moved to Kyoto"):
    c.add("user", obj, why="told me", intensity=0.9)  # 4 条"永久"事实
p.write_bytes(
    p.read_bytes().replace(b"likes oolong tea", b"likes oolong t\xe9")
)  # 1 个 Latin-1 字节
print(c.view())  # []  —— 整库读为空
c.compact()  # 例行维护
print(p.stat().st_size)  # 0   —— 全部删除
print(Path("wipe/canon.jsonl.corrupt").exists())  # False —— 连隔离文件都没有
```

### A.2 H-01：含 U+2028 的事实静默消失

```python
from pathlib import Path
from feltstate import Canon

p = Path("ls/canon.jsonl")
c = Canon(p)
c.add("user", "moved to Kyoto in spring", why="new job")
c.add("user", "has a cat named Mochi", why="loves her")
print([r["object"] for r in c.search("Kyoto")])  # []
print([r["object"] for r in c.search("Mochi")])  # ['has a cat named Mochi']
c.compact()
print(b"Kyoto" in p.read_bytes())  # False —— 已从主文件物理删除
```

### A.3 H-02：`retract("call")` 撤回了无关事实

```python
from feltstate import Canon

c = Canon("kw/canon.jsonl")
c.add("user", "sister's cat is named Miso", why="family")
c.add("user", "works as a nurse", why="job")
print(c.retract("call").get("object"))  # sister's cat is named Miso（字段名 "recalls" 含 "call"）
print([r["object"] for r in c.view()])  # ['works as a nurse']
```

### A.4 H-04：间隔越长，同一事件越被抹掉

```python
from datetime import datetime, timedelta, timezone
from feltstate import Engine, AffectDelta
from feltstate.sources.base import AffectSource


class S(AffectSource):
    def read(self, m, *, baseline, persona=""):
        return AffectDelta(
            valence=-0.8,
            arousal=0.6,
            labels=["sad"],
            confidence=0.9,
            milestones=[{"kind": "loss", "severity": 0.9, "actor": "user"}],
        )


for gap in (1, 5, 30, 240, 1440):
    e = Engine(source=S(), state_path=f"gap{gap}/state.json")
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    e.tick([{"role": "user", "content": "hi"}], now=t0 - timedelta(minutes=gap))
    before = e.state.pressure.bars.sadness
    e.tick([{"role": "user", "content": "my dog died"}], now=t0)
    print(gap, round(before, 3), "->", round(e.state.pressure.bars.sadness, 3))
# 1 0.371 -> 0.742 | 5 0.371 -> 0.67 | 30 0.371 -> 0.22 | 240 0.371 -> 0.027 | 1440 0.371 -> 0.018
```

### A.5 H-05：心境为负，渲染的主情绪却是 "content"

```python
from datetime import datetime, timedelta, timezone
from feltstate import Engine, AffectDelta
from feltstate.sources.base import AffectSource


class S(AffectSource):
    d = None

    def read(self, m, *, baseline, persona=""):
        return self.d


s = S()
e = Engine(source=s, state_path="lab/state.json")
t = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
s.d = AffectDelta(valence=0.6, arousal=0.5, labels=["content"], confidence=0.9)
e.tick([{"role": "user", "content": "nice"}], now=t)
for _ in range(10):  # 10 个悲伤回合，中间各有一次心跳
    s.d = AffectDelta(valence=-0.7, arousal=0.5, labels=["sad"], confidence=0.9)
    t += timedelta(minutes=3)
    e.tick([{"role": "user", "content": "sad"}], now=t)
    s.d = AffectDelta(valence=0.0, arousal=0.4, labels=[], confidence=0.1)  # 心跳 / 无线索消息
    t += timedelta(minutes=2)
    e.tick([], now=t)
print(round(e.state.mood.valence, 3))  # -0.242
print([l for l in e.render(now=t).splitlines() if l.startswith("mood")])
# ['mood: content | a little low, mild energy · lifting']
```

### A.6 H-08：一次保存把 0600 重置为 0644

```python
import os, stat
from pathlib import Path
from feltstate import Engine, KeywordSource, Canon

os.umask(0o022)
d = Path("perms")
d.mkdir(exist_ok=True)
e = Engine(source=KeywordSource(), state_path=d / "state.json")
e.tick([{"role": "user", "content": "hello"}])
c = Canon(d / "canon.jsonl")
c.add("user", "likes tea", why="calms her")
for f in ("state.json", "state.meta.json", "canon.jsonl"):
    os.chmod(d / f, 0o600)  # 按 SECURITY.md 的建议收紧权限
e.tick([{"role": "user", "content": "still here"}])
c.compact()
print(
    {
        f: oct(stat.S_IMODE((d / f).stat().st_mode))
        for f in ("state.json", "state.meta.json", "canon.jsonl")
    }
)
# {'state.json': '0o644', 'state.meta.json': '0o644', 'canon.jsonl': '0o644'}
```

### A.7 M-01：低置信读数仍然改变状态与渲染

```python
from datetime import datetime, timedelta, timezone
from feltstate import Engine, AffectDelta
from feltstate.sources.base import AffectSource


class S(AffectSource):
    def read(self, m, *, baseline, persona=""):
        return AffectDelta(
            valence=0.0,
            arousal=1.0,
            labels=[],
            confidence=0.05,  # 低于 0.2 的信任下限
            mixed_blend={
                "primary": "dread",
                "secondary": "glee",
                "primary_score": 0.9,
                "secondary_score": 0.8,
            },
            anticipation={"valence": 1.0, "arousal": 1.0, "weight": 1.0},
        )


e = Engine(source=S(), state_path="gate/state.json")
t = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
for _ in range(10):
    t += timedelta(minutes=1)
    e.tick([{"role": "user", "content": "..."}], now=t)
print(round(e.state.mood.arousal, 3), round(e.state.pressure.bars.joy, 3))  # 0.814 0.82
print([l for l in e.render(now=t).splitlines() if l.startswith(("mood", "inside"))])
# ['mood: neutral | level, keyed up (dread tinged with glee)', 'inside: pressure clear, joy brimming | still echoing']
```
