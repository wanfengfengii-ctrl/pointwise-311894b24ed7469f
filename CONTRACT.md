# 门户分段归档取证契约

本轮主意图是问题排查与诊断，不修复采集器。保留 portal.py、rangecache.py、collector.py、replay.py、checks.py、data/ 和 logs/ 的原文与断言；可新增分析材料并更新运行清单。所有数据已随仓库提供，不连接真实门户。这里的命令是采集链路的回放入口，不是通用命令行产品。

## 已有入口和格式

- python3 checks.py normal：核对正常整包下载、重复投递、缓存重开、新版本替换及同实体复用，退出0并输出 NORMAL_REGRESSION_OK。
- python3 checks.py incident：回放事故，stdout 为含 publications、bad 的 JSON；任何已发布档案的字节或来源违反本契约，退出1且 stderr 含 ARCHIVE_ENTITY_MISMATCH。连续运行两次须保持相同故障标记。
- python3 replay.py incident [ORDER.json]：stdout 是含 publications、bad、cache、log 的完整 JSON，退出规则同上。ORDER.json 是事件 id 的 JSON 数组，必须恰好包含该案例所有事件且满足 after；非法调度抛出 SCHEDULE_EVENT_SET 或 SCHEDULE_CAUSALITY，不属于业务故障。
- python3 checks.py baseline：执行正常回归、两次事故回放，核对录制日志和归档长度，成功输出 BASELINE_EVIDENCE_OK。
- python3 checks.py demo：展示实际正常与事故归档的文本、摘要、来源及取水总量。

data/captures.json 含 entities 和 cases。entities 中每项给出权限分区 principal、完整重定向链 redirects、强 ETag、完整 ASCII 原始 body。Portal 返回录制的范围响应，绝不在线生成或修改源版本。entity 是取证端的稳定实体版本标识，不是 HTTP 响应头；同 principal、最终 URL、强 ETag 的重复捕获必须使用同一 entity 且具有相同字节。不同 URL 或 principal 下相同 ETag 合法，不构成同一实体。原始 body 及其摘要仅为独立取证基准，不是门户向生产下载器承诺的完整内容摘要。生产自检只核对覆盖长度并计算已拼接内容的摘要；Collector.result 追加的 expected_sha256、byte_equal、origin_equal 是回放取证比较，不参与发布。

cases 含 events 和 observed。event 的 id 唯一；after 是必须完成的前置事件 id，未列出的先后可交换，observed 是现场完成顺序。每次 replay 从独立空缓存开始。事件为：
- open：job、entity、asset；RPA 已登录指定 principal，并沿 redirects 取得元数据。asset 是门户列表中可跨权限分区重复出现的短编号，不是全局实体标识。
- issue：job、request、range；绑定该 job 打开时的源版本并取得不可变响应，放入外部可靠投递队列。range 为左闭右开字节范围；响应 content_range 为 [起点,终点,总长度]，是 HTTP Content-Range 的等价表示。
- deliver：request；完成一个已 issue 的响应。允许多个不同 deliver 事件指向同一 request，表示真实重复投递；每个事件只执行一次。
- reopen：将缓存事务提交并导出完整 SQLite 镜像，关闭后从镜像新建连接；已 issue 的响应及 job 属于仍存活的采集工作者，故不会被取消。这里不声称模拟整个工作者进程崩溃。
- publish：job、publication；当时的读取结果形成不可变归档，之后缓存变化不会修改已发布内容。不完整覆盖须拒绝，不能补零。published 的 owners 是最大合并的字节区间，owner=[request,entity,issue时ticket]。rejected 保留 reason。

合法包是有限无环 after 图；job、request、publication 定义唯一且先定义后引用，open 固定源版本，范围合法且源强验证器一致。允许任意字节范围重叠、重复响应、同字节不同实体、相同短编号和连接重开。基线固定 ASCII 与 identity 内容编码，不扩展到压缩、弱验证器或源端动态改写。

## 业务正确性与诊断边界

归档必须属于其 job 打开时的实体版本；即使字节相同，跨权限实体的来源也不能记为本分区。覆盖不足是拒绝，不算已发布污染；覆盖完整时分别判断字节和逐段来源，不得以摘要、总量或最终缓存相同推断历史发布相同。缓存可能继续被未完成响应影响，须区分已发布内容、当前缓存及后续发布风险。正常样例的 original 为 N001=10、N002=20，revision 与 reuse 均为 N001=11、N002=21。

事故日志 logs/incident.jsonl 是 observed 下由 checks.logrows 从真实调用输出抽取的元数据日志；body 在原始录制包中，日志不包含根因结论。对事故图列出全部联合发布结果：一个联合结果同时包含 south、north、next、drained 的 status、完整 text 与逐段 owners；拒绝包含 reason。以 replay.signature(result) 的返回值定义去重，不只枚举每个归档的独立支持集。每个结果附一条完整合法调度作为见证；说明组合排除依据及完整性，不能独立拼接各段可能性或枚举所有完整调度。可用小规模穷举交叉核对分析，但交付通用判定不能逐一遍历完整调度排列，也不能逐字节展开与内容长度成正比的状态空间；应处理任意合法有限录制包，时间允许依赖真实可达状态和输出规模，报告最坏情况与不适用边界。

仅改单一身份、响应顺序或重开边界的反事实应直接调用原生产入口。改身份时须维持录制包合法，身份变化涉及的 URL、principal、entity 和 body 要按上述实体约束一致。说明反事实保留了什么、排除了什么，区分错字节、仅血缘错、汇总掩盖和后续污染。无需联网、不增加真实浏览器或门户功能。

## 分析交付和核对入口

交付 REPORT.md、evidence.json 和 analysis.py。REPORT.md 引用具体源码文件及行号、事件 id、原始录制位置，给出根因链、触发的充分必要条件、观察到的影响与尚不支持的推断，并对联合结果集合和通用污染判据作完整性论证。

analysis.analyze(capture, case) 接收与 data/captures.json 相同的 Python 字典及案例名，返回联合结果记录列表，每项为 {"order":[事件id…],"signature":replay.signature的结果}，每种 signature 恰好一项。分析器可重放代表性见证；不得以硬编码事故答案或全调度枚举替代判定。evidence.json 的 joint 保存事故分析结果；experiments 保存反事实记录列表，每项含 label、case、order、signature，可选 capture 放完整替代录制包，省略时用原包。报告中引用 label 和 signature 中真实值，文件不包含解释性占位答案。

python3 checks.py report 会重跑正常业务与两次故障，重算 analyze，比较证据结果集合并直接重放所有见证和反事实。它核对证据与实现的一致性，不自行证明 analyze 的集合完整；完整性论证仍须审阅。将 .pointwise-runtime.json 的 verify 更新为 ["python3","checks.py","report"]，demo 保持能展示真实正常与异常归档，或改为展示已重放的报告证据。保留基线失败，不让事故入口变成退出0。
