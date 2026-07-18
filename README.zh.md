# feltstate

**给 LLM agent 一份能带着走的心情——和会老化、会改写、会死去的记忆。**

> 机制说明: 持久感情由回复模型之外的独立组件估计, 结构化记忆通过显式的存储、
> 召回、溯源、老化与删除工具管理。这是一套提示词/接口与状态管理设计——
> 不是关于意识、主观体验、真实情感或类人记忆的任何主张。

[![CI](https://github.com/Morephine/feltstate/actions/workflows/ci.yml/badge.svg)](https://github.com/Morephine/feltstate/actions/workflows/ci.yml)
&nbsp;![Python](https://img.shields.io/badge/python-3.10%2B-blue)
&nbsp;![License: MIT](https://img.shields.io/badge/license-MIT-green)
&nbsp;[English](README.md) | [中文](README.zh.md)

feltstate 是一个小而有主张的参考库, 做长期运行 LLM 陪伴体的**持久感情与可溯源记忆**。
它把感情判读放在回复模型之外, 维护一份会淡忘、会强化、会修正、会合并、会退役的
结构化记忆, 并把紧凑的状态作为上下文交回——而不是作为行为指令。
引擎之外还配了一套[集成手册](#集成手册): 十五章讲这些零件如何装配成一个
会说话、有脸、会干活、会出错、还保持住自己的陪伴体——手册里引用的每段对话
都能用可运行的示例复现。

它借鉴了情感计算、agent 记忆、appraisal 理论与选择性遗忘的研究, 但架构是自己的:
感情在回复模型之外判读、模型无权自写; 结构化记忆带出生到死亡全程可审计的溯源;
持久状态在多个时间尺度上演化; 动态上下文的注入不改写静态提示词。其中几样在
agent 库里并不常见——能*死亡*且留下可追谱系的记忆、持久感情的硬所有权边界、
离线零 LLM 的做梦——它们是有意做成这样的, 不是现成零件的拼装。

> 由一个私有陪伴体原型中使用的机制提炼、重写为干净的通用库。原型的私有数据、
> 训练模型与人格均不包含在内——这里只有本仓库所呈现的实现与设计选择。

---

## 为什么做这个

多数 agent 记忆库专注于文本的存与取, 多数 agent 情绪 demo 把心情简化成提示词里的
一个数值。feltstate 探究一个更窄但更深的问题: **持久感情、记忆、能力与主动行为,
如何在一个长期陪伴体的生命周期里一起演化。**

主要设计选择:

1. **记忆有生命周期, 不只是检索索引。** `Canon` 存储紧凑的 5W1H 事实, 显著度会
   衰减、会因重复而加强、被想起时活得更久。可选的 lifecycle 工具再加上出生指纹、
   融合谱系、按类老化时钟、纯粹死亡计划、墓碑先行的删除、快照清理, 以及用于
   发现无解释变动或消失的哈希链审计账本。
2. **感情是独立估计的, 不是自我报告。** 由配置的 `AffectSource` 产出感情信号,
   回复模型无法随意书写自己的持久状态。
3. **状态在多个时间尺度上演化。** 快的心情、慢的性情、关系、压力、余味、期待
   与可选的印痕, 按可配置的、类人的不对称动力学整合——每个速率都是性格旋钮,
   不是拟合出来的常数 (见["数值从哪来"](#数值从哪来))。
4. **能力与心情分开评价。** 可选的技能记忆区用人工 1/2/3 评分、晋升、退役与
   有界探索, 而不是让回复模型自行宣称胜任。
5. **主动行为是被门控的, 不只是被调度的。** 参考调度器把空闲时间、在场、冷却、
   每日配额、待聊话题、时间窗、做梦、内省与日记行为组合进
   propose/dispatch/commit 流程。
6. **持久状态是上下文, 永远不是命令。** 回复模型收到的是紧凑的第一人称描述,
   而不是数值控制或"表现得悲伤一点"这类指令。
7. **注入路径对缓存友好。** 静态人格文本保持稳定前缀, 动态状态搭在最新一条
   用户消息上。
8. **做梦是可选的状态实验。** 零 LLM 地重组带感情标签的碎片, 留下一缕临时的
   心情余味, 且不向回复模型提供显式因果叙事。

它的贡献是一套具体、可检视、有测试的架构, 在判读、持久状态、记忆生命周期与
回复生成之间划出明确的所有权边界——多数陪伴体糊在一起的部分, 这里刻意地、
可见地分开。上面每一条选择都在[集成手册](#集成手册)里配着可运行代码和真实
对话展开。

### 数值从哪来

那些动力学常数——EWMA 速率、衰减曲线、压力阈值、各处不对称——是**性格参数,
不是心理学测量值**。它们是在一个长期运行的私有部署里手工磨出来的, 就像游戏
设计师调移动手感曲线: 目标是角色在数月尺度上保持自洽, 不是对上某篇论文里的
常数。每一个都在 `config.py` 里, 配一行"动它会怎样"的说明; 角色相关的值走
`PersonaDials`; 默认值合起来就是一套参考性情。重调它们不是在破坏模型——
那正是写出*另一个*角色的方式: 更快原谅、更慢热络、更不容易累。性格可调
本身就是设计, 不是免责声明。它们唯一不是的, 是对人类数据的拟合; 这里不作
这种主张, 它们的职责也不需要这种主张。

### 关于记忆指纹的说明

lifecycle 包用 SHA-256 指纹和哈希链账本做溯源与篡改留痕。这**不是加密**,
不是数字签名, 也不防能改写所有文件并重算所有哈希的攻击者。它的用途是:
当账本作为可信记录保存时, 让平常的谱系、变动与删除可审计。

---

## 快速上手

```python
from feltstate import Engine, KeywordSource

# KeywordSource 是零依赖、基于规则的参考信号源——看清循环怎么转足够了。
# 实际使用换成 LLMSource (任意 OpenAI 兼容端点) 或你自己微调的分类器。
eng = Engine(source=KeywordSource(), state_path="state.json",
             persona="a dry-humoured, loyal friend")

eng.tick([{"role": "user", "content": "I finally shipped it!! couldn't have done it without you"}])

print(eng.render())
# [how I feel right now]
# close · trusted · mostly safe · no friction
# curious, content | warm, mild energy
# pressure low, joy bright | building
# ...

# 缓存安全地喂回你的回复模型, 作为第一人称上下文:
prompt = eng.inject("so what should we build next?")
# -> 你的静态 system prompt 原样不动 (缓存继续命中);
#    felt block 搭在最新一条用户消息上。
```

跑完整 demo:

```bash
python examples/quickstart.py     # 纯标准库, 无需安装
```

想装成完整桌宠——脸、嗓子、心跳、记忆工具? 从
[docs/INTEGRATION.md](docs/INTEGRATION.md) 进。

---

## 检索之外的记忆

默认的 `Canon` 是平文件的结构化记忆存储, 不是向量数据库。事实以紧凑的 5W1H
记录表示, 可以强化、修正、撤回、按过去某时刻查询 (bi-temporal)、经可插拔的
打分器召回, 还能展开回产生它的原文对话上下文。

可选的 `feltstate.memory.lifecycle` 包建模一条更长的路:

```text
源证据 → 零 LLM 一致性闸 → 封印的蒸馏记忆
      → 老化 / 融合 / 谱系 → 下钻回源上下文
      → 死亡计划 → 墓碑 → 受管存储删除 → 审计链
```

回收器拒绝删除无法溯源的记录, 活着的蒸馏记忆能保护它依赖的事实, 收割器通过
可重放的待决事务从活存储和显式提供的快照中移除死亡记录。受管存储之外的源材料
行只做删除标记; 本库不宣称密码学意义上的擦除。

追溯路径是显式的, 不是魔法:

- `check_consistency()` 是对"由源行生成的摘要"的可配置词法护栏。它不用再调一次
  模型就能抓出无依据数字、否定漂移、主体漂移、拼接从句、夸大与空洞文本。它
  **不是**语义证明; 非空格分词的语言应提供自己的分词器和语言表。
- `smelt()` 把这道闸与出生显著度、溯源指纹组合。默认拒绝未封印输出; 调用方可
  显式选择未封印回退。
- `drill()` 沿 `src` 与融合 `lineage` 走回调用方持有的记忆存储。`leaf_pointers()`
  在谱系只有部分幸存时仍保住原始证据。`trace_contexts()` 把每个指针的完整
  `t0`–`t1` 区间解析成对话轮, 并可选校验源文本逐字一致。
- `trace_memory()` 把谱系树、叶子证据、对话区间、感情轨迹、丢失分支数与可选的
  源哈希校验拼成一份报告。
- `verify_source_ptr()` 用指针哈希校验源文本逐字一致。它不能恢复已删除的文本,
  不能自动定位文件, 也不能把哈希变成加密。

```python
from feltstate.memory.lifecycle import trace_memory

report = trace_memory(
    memory_fp,
    fingerprint_store.get,
    transcript_loader,
    before=3,
    after=3,
    load_source_text=exact_source_text_loader,  # 可选的哈希校验
)
```

`Canon`、lifecycle 指纹与对话存储是可组合的零件, 不是隐藏的自动流水线。只有当
应用保存了指纹、被引用的源档案, 以及这些存储的解析器/加载器, 一条记忆才是
完整可追溯的。

```bash
python examples/memory_lifecycle.py
```

---

## 感情如何运转

```
            ┌─────────────┐   独立估计 (不是自我报告)
 messages → │ AffectSource │ ──────────────► AffectDelta (本轮的估计)
            └─────────────┘                        │
                                                    ▼
   ┌──────────────────── Engine.tick() 沿时间整合 ────────────────────────────┐
   │  traits    不对称 EWMA——好心情消得快, 坏心情赖得久                        │
   │  mood      体感的 valence/arousal, 被性情所暗示的方向牵引                 │
   │  pressure  5 根压力条 (sadness/anger/anxiety/boundary/joy) 充能、越阈、    │
   │            *释放*、再落定——不会一直顶在最大值                            │
   │  imprint   可选: 深刻时刻留下永久印痕 (对称: 既有伤口也有暖意,           │
   │            agent 不会只留疤)                                              │
   └────────────────────────────────────────────────────────────────────────────┘
                                                    │  持久化的 AffectState
                                                    ▼
            ┌─────────────┐  render_felt_block + 时间感 (模糊的"我们多久没
 reply  ◄── │ render/inject│  说话了", 精确的"现在几点")
 model      └─────────────┘  → 第一人称状态块, 缓存安全地注入
```

回复模型把 felt block 当作生成回复时的补充上下文。本库永远不会往提示词里写
"现在悲伤起来"——它只提供状态。

### 微调信号源

`KeywordSource` 和 `LLMSource` 是核心自带的两个示例信号源。可选的第三个,
`feltstate.sources.vheart.VheartSource`, 从 Hub 加载 LoRA 适配器。引用了两个小的
实验性适配器:
[`kaishuiji/vheart-affect-v8`](https://huggingface.co/kaishuiji/vheart-affect-v8)
(1.5B 底座) 和
[`kaishuiji/vheart-affect-v9`](https://huggingface.co/kaishuiji/vheart-affect-v9)
(4B 底座)。

请把这些适配器当作接口演示——更接近研究玩具而非生产分类器。训练数据未发布,
本仓库没有公开可复现的基准, 也不作任何准确率或适用性主张。它们适合用来
跑通集成路径, 不作为模型推荐。

```bash
pip install "feltstate[vheart]"
```

```python
from feltstate import Engine
from feltstate.sources.vheart import VheartSource

eng = Engine(source=VheartSource("kaishuiji/vheart-affect-v9"))
```

构造 `VheartSource` 会下载底座模型与适配器并加载到 GPU (或 CPU), 体积数 GB,
启动有可见停顿。构造期间的下载、加载与网络故障会向外抛出。构造完成后,
`read()` 本身永不抛错——分词、生成与解析的失败都塌缩为一次低置信的中性读数。

*离开*这条每轮路径, agent 会**做梦**: `Engine.maybe_dream()` 只在单一睡眠压力
累积器 (由 arousal 驱动, 不看钟) 说足够困时触发——把存下的带感情标签碎片重组成
一个短而不合逻辑的梦, 留下一缕微弱的心情余味, 其因果线索不会作为显式原因呈现给
回复模型, 并在接下来几小时像任何感觉一样衰减。见
[PHILOSOPHY.md](PHILOSOPHY.md) §5。

---

## 布局

| 模块 | 是什么 |
|---|---|
| `feltstate/state.py` | 全部 schema: `AffectState`、`AffectDelta`、`Mood`、`Traits`、`Relationship`、`PressureState`。纯 dataclass, JSON 往返。 |
| `feltstate/config.py` | 所有可调参数集中一处 (EWMA 速率、衰减、压力阈值、标签映射) + `PersonaDials`。 |
| `feltstate/sources/` | `AffectSource` 接口 + `KeywordSource` (规则, 零依赖) + `LLMSource` (任意 OpenAI 兼容端点)。可插拔的感情估计缝。 |
| `feltstate/affect/` | 动力学: `pressure` (多条释放)、`traits` (不对称适应)、`imprint` (永久印痕)、`relationship` (纽带演化)、`tide` (心情涨落)、`smooth` (标签滞回)。 |
| `feltstate/memory/` | `Canon`——会衰减的 5W1H 事实存储; `feeling`——可选的按事实证据加权感情; `recall` 与 bi-temporal 历史; `extract` 与 `context`; `skill`——人工评分的能力区; `lifecycle`——可选的溯源指纹、谱系、老化时钟、死亡规划、墓碑先行删除、快照清理与哈希链审计账本。 |
| `feltstate/dream.py` | 离线、零 LLM: 把 agent 的带电材料 (`Fragment`) 重组成*不合逻辑*的梦, 留下不向回复模型显式归因的微弱心情余味。换掉 `Phrasebook` 即换语言。 |
| `feltstate/sleep.py` | 单一睡眠压力累积器 (`Tiredness`), 决定*何时*做梦: 随 arousal 上升, 由阈值 + 空闲 + 硬不应期地板门控, 由一个梦泄放。稳态驱动, 不是钟表驱动。 |
| `feltstate/timeawareness/` | 模糊的"我们多久没说话了" + 精确的"现在"。 |
| `feltstate/render/` | `render_felt_block` (状态 → 第一人称块) + `build_injection` (缓存安全)。 |
| `feltstate/engine.py` | `Engine`——收拢一切的门面: `tick()`、`render()`、`inject()`、`dream()`、`maybe_dream()`。 |
| `feltstate/companion/` | 参考编排层: `LLMBackend` / `FrontendAdapter` / `VoiceAdapter` / `UserPresenceAdapter` 四道缝, `companion_turn` 走完一轮估计→回复→表情→出声, `CompanionScheduler` 负责可选的主动行为。 |

---

## 陪伴体循环

核心引擎把持久感情状态以第一人称交给 agent; `feltstate.companion` 提供一副
*参考陪伴体骨架*。实现两个适配器——`FrontendAdapter` (你的立绘/皮) 和
`VoiceAdapter` (你的 TTS)——带上一个 `AffectSource`、一个回复 `LLMBackend`
和一份人格, `Companion` 负责其余: 前台 `say()` 轮 (感受 → 回复 → 表情 → 出声),
以及 `CompanionScheduler` 心跳, 按配置的时机与门控检查可选的主动说话、内省、
做梦或写日记——时机与门控均改编自一个私有陪伴体原型使用的机制, 端点与提示词
留给你。

```bash
python examples/companion.py       # 可运行的桩陪伴体——零依赖零网络
python examples/companion_live.py  # 交互式: 真实心跳、主动起话头、
                                   # 重启后仍在的记忆
```

---

## 集成手册

引擎是造陪伴体较小的那一半; 较大的一半是集成——什么进提示词、按什么顺序,
回复怎么变成脸和声音, agent 干活、出错、思考时用户看见什么。`docs/` 各章
把这一半写成落在本库真实接缝上的具体模式。它们描述的是装配陪伴体的一种
自洽方式——即本库所提炼自的那个私有参考实现的形状——不是唯一方式。
章节里引用的对话都是可运行示例的真实输出。

| 章节 | 讲什么 |
|---|---|
| [INTEGRATION](docs/INTEGRATION.md) | 装机手册: 接线图、提示词分割与缓存经济学、心跳职责、主动路径、适配器替换、壳/桥/魂分层、隐私边界 |
| [PROMPT_STACK](docs/PROMPT_STACK.md) | 静/动分割、三明治顺序, 以及遗忘探针——不出事就零成本的人格保养 |
| [PROMPT_SHAPES](docs/PROMPT_SHAPES.md) | 一个中性人格、三个预置时刻的完整消息数组; 从状态波段到措辞的变体总表 |
| [STYLE_SPECTRUM](docs/STYLE_SPECTRUM.md) | 可选的表达注记——感觉如何握笔: 只管形式, 永不管内容 |
| [OUTPUT_CHAIN](docs/OUTPUT_CHAIN.md) | 回复 → 脸和声音: 双信号通道、首句抢发 TTS、渲染器可移植到一座热键桥 |
| [AGENT_WORK_UX](docs/AGENT_WORK_UX.md) | 长时间干活不失踪也不出戏: 罐头声带、旁白限流、隔轮续工 |
| [FAILURE_IN_CHARACTER](docs/FAILURE_IN_CHARACTER.md) | 两个受众两份真相: 体感故障分类、看门狗案例、恢复礼仪 |
| [BRIDGE_ETIQUETTE](docs/BRIDGE_ETIQUETTE.md) | 在聊天平台上做一个人: 回执、打字灯、附件, 与应急命令道 |
| [INTERRUPTION](docs/INTERRUPTION.md) | 被优雅地打断: 免耳机抢话、急停链、被掐断后的姿态 |
| [PERCEPTION](docs/PERCEPTION.md) | 图与屏作为输入: 落盘 → 感知 → 回应, 以及拉式眼睛 |
| [INNER_LIFE](docs/INNER_LIFE.md) | 静默思考通道、轮与轮之间自己会动的脸, 与自纠轮 |
| [MULTI_PERSON](docs/MULTI_PERSON.md) | 一魂多人: 按说话人分键的关系账本与零污染规则 |
| [GAME_SHELL](docs/GAME_SHELL.md) | 游戏作为第三面: 分钟级导演意图对秒级引擎、视线门, 以及实机驱动 It Takes Two |
| [MEMORY_TOOLS](docs/MEMORY_TOOLS.md) | 把 Canon 暴露成五个 function-calling 工具, 附真实的 bi-temporal 追溯 |
| [PHILOSOPHY](PHILOSOPHY.md) | 为什么持久状态只对模型描述、从不命令 |

配套的可运行示例, 全部确定性或离线:

```bash
python examples/prompt_shapes.py    # 三个时刻, 完整消息数组
python examples/memory_tools.py     # 五个工具 + 分发器, 端到端
python examples/agent_narration.py  # 罐头声带池、限流、故障台词
python examples/style_spectrum.py   # 状态波段 → 表达注记
python examples/companion_live.py   # 交互循环 (FELTSTATE_LIVE_FAST=1 加速)
python examples/game_director.py    # 游戏壳: 意图队列、视线门、一张不停的嘴
```

---

## 范围——是什么, 不是什么

- **是:** 这些想法的一份干净、可运行的*参考实现*, 核心零依赖。自带你的
  `AffectSource`、人格文本和状态存放处。
- **不是:** 成品。没有捆绑人格、没有验证过的感情模型、没有对话数据、立绘或
  TTS。`feltstate.companion` 是参考编排骨架, 不是完整的桌宠应用。桩 demo 见
  `examples/companion.py`, 交互版见 `examples/companion_live.py`。
- 默认的 `KeywordSource` 有意做得粗糙。`LLMSource` 仍是另一次模型调用产出的
  估计, 可选的 Vheart 适配器是实验演示而非验证过的分类器。
- 默认的 `Canon` 每次操作整文件重载 (O(n), 词面打分)——按用途定尺寸: 一个
  陪伴体的数千条蒸馏事实, 可审计的平文件胜过不透明的数据库, 衰减与压实会把
  活跃集保持在小规模。要上大规模语料或语义检索, 就在同一接口后面换真数据库。

---

## 安装

```bash
pip install -e .          # 核心是纯标准库
pip install -e ".[dev]"   # + pytest, ruff, mypy
```

需要 Python 3.10+。

## 开发

```bash
ruff check .          # lint
ruff format .         # 格式化
mypy feltstate        # 类型检查
pytest -q             # 测试
```

四项都在 CI (`.github/workflows/ci.yml`) 上跑, Python 3.10–3.13。提 PR 前请看
[CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

MIT——见 [LICENSE](LICENSE)。
