# 分区取水档案串区/混版事故根因诊断报告

诊断对象：`python3 checks.py incident` 回放的事故（`data/captures.json` → `cases.incident`，现场顺序 `observed`，元数据日志 `logs/incident.jsonl`）。本报告只做根因、触发条件与影响范围诊断，不修改生产源码与原始证据。所有结论均可由 `python3 checks.py report` 重放核对（证据见 `evidence.json`，分析器见 `analysis.py`）。

## 1. 结论摘要

事故不是门户发错字节，也不是重开（reopen）损坏缓存镜像，而是采集器缓存的**身份模型缺陷**，两个相互独立的机制缺陷共用同一个根：

- **根因 A（串区）**：缓存槽位只按门户短编号 `asset` 键控（`rangecache.py:13-14`、`collector.py:17`），而 `asset` 按契约是可跨权限分区重复的短编号、不是全局实体标识（`CONTRACT.md:16`）。读取与发布同样只按 `asset` 取槽位、从不核对槽位的 etag/ticket 与 job 打开时的实体上下文（`rangecache.py:47-49`、`collector.py:48`）。于是北区 job N 与南区 job S 共用同一槽位 `daily`，`pn` 把南区字节作为北区档案发布。
- **根因 B（混旧版）**：响应在 `issue` 时深拷贝冻结了 job 及其 ticket（`collector.py:25`），而 `Cache.put` 写入与 `Cache.read` 装配都**从不校验 ticket 是否属于当前槽位代际**（`rangecache.py:32-38`、`rangecache.py:47-53`）。`t0` 换代（v7→v8，ticket 1→2 并清空 span）之后，迟到的 v7 代响应 `NA`/`NB` 仍被照常接收并参与装配，`pt`/`pf` 因此混入旧版字节。
- **自检为何通过（汇总掩盖）**：生产自检只核对覆盖长度并计算已拼接内容的摘要（`CONTRACT.md:13`；`collector.py:48-50`），对任何"恰好拼满"的内容都自洽；`expected_sha256`/`byte_equal`/`origin_equal` 是回放取证比较，不参与发布（`collector.py:63-66`，`CONTRACT.md:13`）。

在事故依赖图允许的**全部 241 种联合归档结果中，没有一种完全干净**，北区档案在 241/241 种结果中都被污染——本次事故是结构性必然，不是调度运气。

## 2. 证据锚点

### 2.1 录制包（`data/captures.json`，单行 JSON，按路径引用）

- `entities.north`：principal `district-n`，强 ETag `"v7"`，body `well,qty\nN001,010\nN002,020\n`（27 字节，sha256 `fe6b8a9c…`）。
- `entities.south`：principal `district-s`，强 ETag `"v7"`，body `well,qty\nS001,020\nS002,010\n`（27 字节，sha256 `284f7bc6…`）。**与 north 同 ETag、同长度、不同分区**——合法（`CONTRACT.md:13`：不同 URL/principal 下相同 ETag 不构成同一实体），但恰好触发 `attach` 的静默复用。
- `entities.north_next`：principal `district-n`，ETag `"v8"`，body `well,qty\nN001,011\nN002,021\n`（sha256 `8328ed55…`）。
- `cases.incident.events`：26 个事件；自由事件仅 `dna`/`dnb`（after `n2`/`n3`+`s3`）、`dta`（after `t2`）、`dtb`（after `t3`）及 `pt`/`pf` 的汇合约束，其余在主链上。

### 2.2 事故日志（`logs/incident.jsonl`，1 起始行号）

| 行 | 事件 | 关键证据 |
|---|---|---|
| L1 | `n0` | job N 打开 north，槽位 `daily` ticket 1 |
| L6 | `s0` | job S 打开 south，**ticket 仍为 1**：`("v7",27)` 与现存槽位行相同，`attach` 复用且未清 span（`rangecache.py:24-25`） |
| L13 | `ps` | south 发布，sha256 `284f7bc6…`（与 south 录制体一致，干净） |
| L14 | `pn` | **north 发布的 sha256 也是 `284f7bc6…`**——与南区档案逐字节相同，串区铁证 |
| L15 | `t0` | job T 打开 north_next，ETag `"v8"` 失配 → ticket 2 并 `DELETE FROM span`（`rangecache.py:26-30`） |
| L20 | `cp` | 重开导出镜像：slots `[[daily,"v8",27,2]]`、spans 仅剩 `TH`——证明 v7 代 span 已被换代清空，镜像本身完好 |
| L21 | `dna` | **`NA` 以 ticket 1 交付**，此时槽位已是 ticket 2；`put` 照常接收（`rangecache.py:32-38`） |
| L23 | `pt` | next 发布，sha256 `ac0741eb…`——不匹配任何录制实体，是 v7/v8 混合体 |
| L25 | `dnb` | `NB` 同样以 ticket 1 迟到交付 |
| L26 | `pf` | drained 发布，sha256 `fe6b8a9c…`——**等于 north v7 录制体的摘要**，旧版整体以 v8 身份发出 |

### 2.3 生产源码关键行

- `rangecache.py:13-17`：建表。`slot` 以 `asset` 为唯一主键；`span` 携带 `ticket`/`entity` 列但无任何约束——ticket 被记录而从未被强制。
- `rangecache.py:20-30`：`attach`。第 24 行 `row[:2] == (etag, size)` 成立即第 25 行返回旧 ticket（**不清 span**）；否则第 26-29 行递增 ticket 并清空该 asset 全部 span。
- `rangecache.py:32-38`：`put`。按调用方给的 ticket 直接 `INSERT`，不与槽位当前 ticket 比对。
- `rangecache.py:40-62`：`read`。第 47-49 行 `WHERE asset=? ORDER BY seq`——**无 ticket/entity 谓词**；第 50-53 行逐字节后写覆盖；第 54-55 行覆盖不足抛 `Hole`（拒绝，非污染）；第 56-62 行生成逐段 owners。
- `rangecache.py:64-66`：`checkpoint` 提交并完整 `iterdump`——重开镜像忠实。
- `collector.py:15-19`：`open` 以 `asset` 为键 `attach`（第 17 行）。
- `collector.py:21-28`：`issue` 第 25 行 `copy.deepcopy(job)`——把打开时的 ticket 冻结进待投递响应。
- `collector.py:29-36`：`deliver` 第 33 行 `validate` 通过（响应对**签发它的 job**完全合法），第 34 行用冻结的 ticket 写入缓存。
- `collector.py:37-41`：`reopen` 提交、导出、从镜像重建——语义上惰性（见实验 `reopen-boundary-inert`）。
- `collector.py:42-54`：`publish` 第 48 行 `self.cache.read(job["asset"])`——不核对槽位 etag/ticket 与 job 上下文。
- `portal.py:32-38`：`validate` 强校验（状态、强 ETag、Content-Range、长度）——事故中全部响应通过，门户侧无过错证据。

## 3. 根因链

### 3.1 串区（`pn` 发布南区字节）

1. `n0`：job N 以 `asset="daily"` 打开 north，槽位 `(daily,"v7",27,ticket=1)`（`collector.py:17` → `rangecache.py:26-29`）。
2. `dnh`：`NH[0,9)` 北区字节入缓存（ticket 1）。
3. `s0`：job S 以**同一个短编号** `daily` 打开 south。`asset` 不是全局实体标识（`CONTRACT.md:16`），但槽位主键只有 `asset`（`rangecache.py:13-14`）；south 的 `(etag,size)=("v7",27)` 与现存行相同，`attach` 命中 `rangecache.py:24-25`：**复用 ticket 1、不删除任何 span**。两个分区的 job 自此共用一个槽位、一个代际。
4. `dsh`/`dsa`/`dsb`：南区 span 以 ticket 1 进入同一槽位，逐字节覆盖 `NH`。
5. `ps`：读到的是纯南区字节 → south 档案干净（sha256 `284f7bc6…`）。
6. `pn`：`publish` 只按 `asset` 读（`collector.py:48`），`read` 不按 ticket/entity 过滤（`rangecache.py:47-49`）→ job N 拿到的是同一个槽位里的南区内容，**作为北区档案发布**（日志 L14 与 L13 摘要完全相同）。覆盖满 27 字节、摘要自洽、取水总量 30 == 北区正确总量 30（10+20 与 20+10 巧合相等）→ 全部生产自检通过。

### 3.2 混旧版（`pt`/`pf` 混入 v7 字节）

1. `n2`/`n3`：`NA[8,21)`、`NB[15,27)` 在 ticket 1 代签发，`issue` 深拷贝冻结了 `ticket=1`（`collector.py:25`），响应进入外部可靠队列，可在任意迟后时刻交付（`CONTRACT.md:17`）。
2. `t0`：job T 打开 north_next，`("v8",27)` 失配 → ticket 2、`DELETE FROM span`（`rangecache.py:26-30`）。v7 代缓存被合法清空（日志 L20 镜像证实）。
3. `cp`：重开只是提交+导出+重建（`collector.py:37-41`、`rangecache.py:64-66`），忠实且惰性。
4. `dna`/`dnb`：迟到的 v7 代响应交付。`validate` 通过（响应对签发它的 job N 合法，`portal.py:32-38`）；`put` 不比对 ticket（`rangecache.py:32-38`）→ **已取代代际的 span 进入 v8 代缓存**。
5. `pt`/`pf`：`read` 不按 ticket 过滤（`rangecache.py:47-49`），v7 字节与 v8 字节按 seq 逐字节混拼：
   - `pt`（next）：`TH[0,11)` + `NA[8,21)`(v7) + `TB[17,27)` → `well,qty\nN001,010\nN002,021\n`（N001 行是旧版），sha256 `ac0741eb…`，总量 31，不属于任何真实版本。
   - `pf`（drained）：再叠加 `TA`、`NB`(v7) 后 → `well,qty\nN001,010\nN002,020\n`，**逐字节等于 v7 旧版整体**，以 v8 身份发布，sha256 `fe6b8a9c…` 恰等于 north 录制体摘要——单看摘要会误以为"回到了正确历史版本"，实际实体身份完全错误（`CONTRACT.md:26`：不得以摘要相同推断内容正确）。

### 3.3 共同根

两处故障共用一个身份模型缺陷：**缓存把"门户短编号 asset"当成了实体身份，把"ticket"当成了只记录不校验的装饰品**。槽位键不含实体/分区（`rangecache.py:13-14`），写入不验代际（`rangecache.py:32-38`），读取不按代际过滤（`rangecache.py:47-49`），发布不核对上下文（`collector.py:48`）。任何一环核对实体身份或代际，两类污染都进不了档案。

## 4. 触发的充分必要条件

### 4.1 串区（跨实体同槽位）

- **充分必要**：两个不同实体的 job 在同一 `asset` 上生命周期重叠（一方的 publish 落在另一方 span 入缓存之后、且读取时装配了外来实体的字节）。机制上因为 `read`/`publish` 从不核对槽位身份（`rangecache.py:47-49`、`collector.py:48`），只要外来 span 覆盖了被读字节即成立。
- `(etag,size)` 相等（`rangecache.py:24-25` 的静默复用）**不是必要条件**：实验 `identity-foreign-etag` 给 south 换不同 ETag 后，`attach` 改为清 span 升 ticket，`pn` 依然读到南区内容（槽位仍共享、读仍不验代际），bad 集合不变。该相等只决定冲突是否**静默**（不清 span、ticket 不变）。
- 在本事故图中对 `pn` **不可避免**：主链固定 `dnh`<`s0`、`dsh`<`dsa`<`dsb`<`pn`，且 `NH[0,9)` 必被 `SH[0,12)` 覆盖——241 种联合结果中 north 全部污染（§6）。

### 4.2 混旧版（跨代际迟到交付）

- **充分必要**：存在某个 `deliver`，其请求在**已被取代的代际**签发（issue 时冻结的 ticket ≠ 交付时槽位 ticket，`collector.py:25` + 换代发生在 `rangecache.py:26-29`），且其字节到 publish 时未被同代 span 完全覆盖。`put`/`read` 的代际盲（`rangecache.py:32-38`、`47-53`）使该条件一旦满足必现污染。
- **reopen 既非必要也非充分**：实验 `reopen-boundary-inert` 把 `cp` 移到 `pt` 前一刻，结果与 observed 逐字节相同；`checkpoint` 是忠实镜像（`rangecache.py:64-66`，日志 L20）。
- 响应内容本身无过错：全部通过强 ETag + Content-Range 校验（`portal.py:32-38`）。问题不在"收到了什么"，而在"收到了哪个时代的"。

### 4.3 自检失效（汇总掩盖）

- 生产自检 = 覆盖完整（无 `Hole`）+ 对已拼接内容算摘要（`collector.py:48-50`，`CONTRACT.md:13`）。任何拼满 27 字节的组合都通过；长度恒为 27；north 的总量 30 与正确值巧合相等。摘要、总量、最终缓存状态都不能推断历史发布正确（`CONTRACT.md:26`）。

## 5. 反事实隔离实验

每个实验只改单一旋钮（响应顺序 / 重开边界 / 单一身份字段），全部直接调用原生产入口 `replay.replay`，完整记录见 `evidence.json` → `experiments`（label 可索引，签名为真实重放值）。

| label | 改动 | 结果（bad 集合） | 隔离结论 |
|---|---|---|---|
| `order-stale-before-reset` | 仅响应顺序：`dna`/`dnb` 移到 `pn` 后、`t0` 前（被换代清空），`dta`/`dtb` 移到 `pt` 前 | `[north]` | 同样的响应、同样的 reopen，仅改时序即消除全部旧版混入 → 旧版混入的触发器是**跨代迟到交付**；north 依旧坏 → 串区与混版是两个独立缺陷 |
| `reopen-boundary-inert` | 仅重开边界：`cp` 移到 `dtb` 后、`pt` 前 | `[north,next,drained]`，与 observed 逐字节相同 | reopen 语义惰性，**不是根因**；缓存镜像完好 |
| `identity-foreign-etag` | 仅单一身份：`entities.south.etag` 改为 `"v7s"`（其余不变，包仍合法） | `[north,next,drained]`，文本全同 | `(etag,size)` 相等**不是**串区的必要条件；根因是槽位键控与代际盲读 |
| `identity-shared-body` | 仅单一身份：`entities.south.body` 改为与 north 逐字节相同（principal/URL/ETag 不动） | north `byte_equal=true`、`origin_equal=false` | **仅血缘错**：任何字节/摘要/长度/总量检查都结构性不可见，只有逐段 owners 能识别（`CONTRACT.md:26`） |
| `order-stale-before-publish` | 仅响应顺序：`dna`/`dnb` 移到 `dsb` 后、`ps` 前 | north 字节全对但血缘错；south 错字节；next **拒绝**（`INCOMPLETE_COVERAGE`）；drained 干净 | 原包内即可达仅血缘错；**覆盖不足是拒绝、不算已发布污染**（`CONTRACT.md:26`）；同一机制按时序表现为不同故障类别 |

四种类别由此分清：**错字节**（observed 的 north/next/drained）、**仅血缘错**（`identity-shared-body` 与 `order-stale-before-publish` 的 north，以及联合结果中 35 种 north、22 种 south、27 种 next/drained 的 origin-only 变体）、**汇总掩盖**（observed 的 north：长度 27、摘要自洽、总量 30==30 全部通过）、**后续污染**（`pt` 之后迟到的 `dta`/`dnb` 继续改变缓存，使 `pf` 与 `pt` 不同；已发布的 next 不可变——`CONTRACT.md:20`，但同一槽位上的后续发布持续暴露，缓存终态仍混有 ticket 1 的 `NA`/`NB` span，未来同 `(etag,size)` 的 `open` 还会静默继承它们——`rangecache.py:24-25`）。

## 6. 影响范围

### 6.1 观察到的影响（observed，即 `evidence.json` joint 中 order 等于 `observed` 的条目）

- `south`：干净（字节与血缘均属南区）。
- `north`：**错字节+错血缘**。内容是南区档案整体（sha256 `284f7bc6…`）；长度、摘要自检、取水总量（30==30）全部通过。
- `next`：**错字节+错血缘**。v7/v8 混合体（`N001,010` 来自旧版），sha256 `ac0741eb…` 不属于任何录制实体，总量 31 与真实 v8（32）不符。
- `drained`：**错字节+错血缘**。逐字节等于 v7 旧版整体（sha256 `fe6b8a9c…`），以 v8 身份发布；单看摘要会误判为"正确的历史版本"。
- 缓存终态：槽位 `(daily,"v8",27,ticket=2)` 下混存 ticket 1（`NA`/`NB`）与 ticket 2 span——后续发布风险持续存在（§5 后续污染）。

### 6.2 事故依赖图允许的全部联合结果（241 种，`evidence.json` → `joint`）

| 档案 | 干净 | 错字节+错血缘 | 仅血缘错 | 拒绝 |
|---|---|---|---|---|
| south | 106 | 113 | 22 | 0 |
| north | **0** | 206 | 35 | 0 |
| next | 100 | 101 | 10 | 30 |
| drained | 109 | 115 | 17 | 0 |

- **241 种联合结果无一完全干净**；north 在全部 241 种中被污染（串区不可避免，§4.1）。
- 30 种结果中 next 因覆盖空洞被拒绝（`INCOMPLETE_COVERAGE`）——拒绝不是污染；这 30 种里 drained 全部成功发布。
- 典型见证（joint 数组下标）：observed=`joint[211]`；仅 north 坏=`joint[135]`；north 仅血缘错=`joint[5]`；next 拒绝=`joint[4]`；south 仅血缘错=`joint[69]`；四档案全坏=`joint[15]`。每个条目都附一条完整合法调度作为见证，`checks.py report` 已逐条重放核对。

## 7. 联合结果集合的完整性论证与组合排除依据

**完整性。** `analysis.analyze` 在"机器状态等价"的商上做可达性搜索：状态 =（已完成事件集合， 缓存 SQLite 镜像， jobs, pending, publications）（`analysis.py:_snapshot/_key`）。`Collector.step` 是（机器状态， 事件）的确定性函数，门户响应是不可变录制（`CONTRACT.md:13`），日志不影响后续迁移——因此到达同一状态的两条部分运行有完全相同的未来，合并它们**不多不少**恰好保持可达签名集合：每条合法调度都是商图中的一条路径，其终态签名必被记录；反之每个被记录的签名都附带一条真实合法调度作见证（`checks.py:85-86` 逐条重放验证）。搜索只展开状态、从不枚举调度排列：本例探索 4,019 个合并状态、得到 241 种签名，而该偏序共有 3,984 个完整调度。

**组合排除依据。** 联合结果由**整轮运行仿真**得出，不是各档案独立可能性的拼接。拼接会得出错误结论，例如：

- "south 干净"（106 种可达）与 "north 干净"各自看都似乎可能，但 "south 干净 ∧ north 干净" 在 241 种中**不存在**——同一槽位耦合了两者：north 要干净就必须在 `pn` 前用 `NA`/`NB` 盖回北区字节，而这必然把 north 的 span 带进 `ps` 的装配（如 `joint[5]`：north 字节全对、血缘错，south 同时被污染）。
- "next 干净"与"drained 干净"也不能与"north 干净"组合（`joint[135]` 是最好情形：仅 north 血缘/字节坏）。
- 30 种 next 拒绝精确对应"`dna` 在 `t0` 前（被换代清空）且 `dta` 在 `pt` 后"，此时字节 11–16 无覆盖——覆盖判据与污染判据互斥（拒绝不算已发布污染）。

因此本报告只枚举真实可达的联合签名（按 `replay.signature` 去重，`replay.py:30-34`），不枚举每档案独立支持集的组合，也不枚举全部 3,984 个完整调度。

## 8. 通用污染判据及完整性论证（任意合法有限录制包）

设合法有限录制包中，某 `publish` 事件的 job J 打开实体为 E（上下文含强 ETag 与长度，`portal.py:11-20`），发布时槽位为 `(asset A, ticket T)`。装配按 `read` 语义：每个字节位置 i 的属主 = 覆盖 i 的 span 中 seq 最大者（`rangecache.py:47-53`）。

**判据（发布件 P 被污染，当且仅当以下至少一条成立）：**

1. **字节污染**：`text(P) ≠ entities[E].body`（基线为 ASCII/identity，`CONTRACT.md:22`）。
2. **血缘污染**：存在某属主段 `owner.entity ≠ E`（即使对应字节与 `body(E)` 相同）。

机制层等价表述：进入缓存的 span 若 `entity` 不等于当前代际的实体、或 `ticket` 不等于槽位当前 ticket，即为**外来 span**；外来 span 在装配中贡献了至少一个字节 ⟺ 污染。拒绝（覆盖空洞）不是污染。

**完备性论证。** 装配结果的每个字节恰有一个属主 span（max seq），其 `(request, entity, ticket)` 完整记录在 owners 中（`rangecache.py:50-62`）。若所有属主 entity 均等于 E 且逐字节等于 `body(E)`，则 P 就是 E 的录制版本，无污染；反之任何偏离要么改变至少一个字节（判据 1 捕获），要么字节全对但某字节属主不是 E（判据 2 捕获）。两者穷举且机制闭合——span 只能经 `deliver`→`put` 进入缓存，外来性在写入点即可判定，读取点必然可见于 owners。四类边角均被覆盖：

- **重叠范围**：按字节最后写者胜（`rangecache.py:50-53`），重叠本身合法；判据逐字节取 max-seq 属主，与范围如何切分无关。
- **重复响应**：同一请求的重复投递产生同 entity/同字节/同 ticket 的多条 span（`CONTRACT.md:18`）；同代重复不改变任何字节的属主实体与内容，判为干净（正常回归事件 `c`/`e` 即此情形，`checks.py:42-53`）；跨代重复（换代后重投旧响应）ticket 失配，判为外来。
- **同字节异来源**：字节判据漏掉、血缘判据捕获（实验 `identity-shared-body`；联合结果中 35+22+10+17 种 origin-only 变体）。契约明确要求即使字节相同，跨分区来源也不能记为本分区（`CONTRACT.md:26`）。
- **缓存换代**：`attach` 失配时清 span 并升 ticket（`rangecache.py:26-30`）——换代本身不污染；污染来自换代后仍被接收的旧 ticket span（`put`/`read` 代际盲）；`(etag,size)` 相等时旧代际静默延续（`rangecache.py:24-25`），此时外来性按 entity 判定。

**判据的可计算性。** 对任意合法有限包，判据只需：录制体（字节基准）、发布件的 owners（血缘基准），均为有限区间记录，计算量与内容长度成线性、与调度数量无关。`collector.py:63-69` 的 `byte_equal`/`origin_equal` 正是该判据的回放实现；本报告的全部污染分类与它一致。

## 9. 分析器复杂度、最坏情况与不适用边界

- 状态数 ≤（after 偏序的理想数）×（每理想的不同机器状态数）。对宽反链，理想数 2^n 而调度数 n!——分析器按状态展开，永不枚举排列；对本事故图（窄链+4 个自由事件）：4,019 个合并状态、241 种签名、约 4 秒（`python3 analysis.py`）。
- 状态表示为 SQLite 区间转储（span 表），**状态空间大小与内容长度无关**；单次 publish 的 O(长度) 开销就是输出本身（签名含完整 text），不属于状态膨胀。
- 不适用边界：仅覆盖契约基线（ASCII/identity、强验证器、不可变录制，`CONTRACT.md:22`）；不模拟工作者进程崩溃（reopen 按契约是原子检查点，`CONTRACT.md:19`）；压缩、弱 ETag、源端动态改写不在本判据与本分析器范围内。

## 10. 观察到的影响与尚不支持的推断

已证实：§6 的全部已发布污染与缓存终态风险；根因 A/B 的充分必要条件（§4）；reopen 惰性、ETag 相等非必要、仅血缘错可达（§5）。

尚不支持（证据不足，不得外推）：

- **门户发错字节**：所有响应通过强 ETag + Content-Range 校验（`portal.py:32-38`），录制不可变；无此证据。
- **SQLite 镜像在重开时损坏**：镜像忠实（`rangecache.py:64-66`，日志 L20），移动重开边界结果不变（`reopen-boundary-inert`）。
- **RPA 登录/会话串号**：录制中 principal 与实体一致（`data/captures.json` → `entities.*.principal`）；冲突发生在采集器缓存键控，不在登录层。
- **向压缩/弱验证器/源端改写场景外推**（`CONTRACT.md:22`），以及向工作者进程崩溃场景外推（`CONTRACT.md:19`）。
- 联合结果集合只刻画本事故图；其他录制包需重新运行 `analysis.analyze`。

## 11. 交付物与核对入口

- `analysis.py`：`analyze(capture, case)` 返回每种签名恰一条 `{"order", "signature"}`；`build_experiments` 定义反事实；`python3 analysis.py` 重新生成 `evidence.json`。
- `evidence.json`：`joint`（241 种联合结果及见证调度）、`experiments`（5 条反事实，含 label/case/order/signature，改身份者附完整替代录制包 `capture`）。
- 核对：`python3 checks.py report` = 正常回归 + 两次同标记事故回放（`ARCHIVE_ENTITY_MISMATCH`，退出 1 保留）+ 录制日志比对 + 重算 `analyze` 比对证据 + 逐条重放全部见证与反事实 → `REPORT_EVIDENCE_REPLAY_OK`。运行清单 `.pointwise-runtime.json` 的 `verify` 已更新为该命令；`demo` 保持展示真实正常与事故归档（文本、摘要、来源、取水总量）。
