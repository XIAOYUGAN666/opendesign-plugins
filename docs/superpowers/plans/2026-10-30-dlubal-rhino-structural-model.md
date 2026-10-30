# dlubal-rhino 通用杆系转换技能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 开发 `dlubal-rhino` 纯技能包中的 `rhino-to-rfem-structural-model` 技能，完成现有机制下的远端签名发布、正式市场上架及 OpenDesign 从远端市场安装和加载验收；技能引导通用完整杆系的轴线提取、RFEM 映射、人工确认、建模和回读。

**Architecture:** 技能只提供指令和按需读取的参考文件，复用现有 Rhino MCP 与 RFEM MCP。交付复用 `release-skill.yml`、现有 publisher signer、签名 receipt/market snapshot 和 Host 官方技能安装路径，只扩展必要的发布对象选择与公开信任范围；不增加连接器、工具、服务、SDK 环境或新的审批机制。

**Tech Stack:** Agent Skills 的 YAML frontmatter/Markdown、现有 Agent Plugins `plugin.json`、Python 3.12 标准库 unittest、仓库已有纯技能构建/校验 CLI、独立上下文评估和既有客户端验证入口。

**Spec:** [dlubal-rhino 通用完整杆系转换技能设计](../specs/2026-10-30-dlubal-rhino-structural-model-design.md)。以包含官方指导补充的当前工作区版本为依据，而非仅使用旧 HEAD 中的 spec；当前 UTF-8/LF 规范化文本 SHA-256 为 `d77d82ce37ca564000c162762edba3f080260c1aadbdb5016ff83cb51b2f910d`。执行前若内容变化，先对齐差异，不静默改用旧版本。

**交付范围补充:** 用户另行要求在本计划中覆盖“本地开发 → 远端发布 → 市场上架 → OpenDesign 远端安装验收”。这是交付流程扩展，不改变 spec 中纯技能、通用完整杆系和人工确认的工程边界，也不将规划视作实际发布授权。

**Status:** Task 1–7 的开发、验证、发布、市场上架与客户端安装/加载交付闭环已完成。临时发布目录的删除被自动审批策略拒绝，目录保留；其余交付关口已具有下列实际证据。下文“本轮只修改计划”等表述是原计划编写时的历史边界，执行以本节记录及后续授权为准。

## 最终交付记录（2026-10-30）

| 任务 | 实际结果 |
| --- | --- |
| Task 1 | 纯技能包 `dlubal-rhino` 0.1.0，入口及五份参考完整；从冻结 Git blob 导出的源包通过往返、可重复构建与引用检查。 |
| Task 2 | 16 个发现请求各 3 次，最终 48/48 符合标签，含留出样本；B01–B10 完成无技能/有技能的独立上下文决策演练。根据误触发与多余确认的实际输出修订并重测。 |
| Task 3 | 插件根 unittest 116 通过；工程验收清单已写入包 README。真实工程转换未执行。 |
| Task 4 | workflow 有界选包及公开 scope/export 已接通；Host owner `f0eb1155`。Rust 42 suites 共 567 passed、33 ignored、0 failed；fmt、Clippy、check 和构建通过。既有合成制品 URL、退役 receipt 错误分类及定时等待测试已修复。 |
| Task 5 | 源提交 `166c76c2a921102eae4e15941a20603dff1ef965`；源码 CI `41026262803` 成功；真实签名 run `41027472769` 成功，独立 verifier 与生产 Host receipt 测试通过。 |
| Task 6 | [Stable 0.1.0](https://github.com/XIAOYUGAN666/opendesign-plugins/releases/tag/plugin-dlubal-rhino-v0.1.0) 已发布并匿名下载复验；市场 review run `41027997593`。原子市场提交 `a0911651406b4a1582004da980140c0a2b30c52c`，snapshot sequence 7；原有三个包的 receipt 和视图保持原样。 |
| Task 7 | Tauri MCP 操作真实 OpenDesign：官方远端刷新、UI 安装并启用、同步成功、重载会话后仍为 official/enabled/revision 7；新 Run 实际读取入口和五份 references。生产 Host 的真实远端安装 ignored 测试在原市场提交及最终验收树均显式通过。 |

最终验收树为 `6a1ea1ec96a8cb52e841c086e9fac79c8daab5a2`，[CI 41031078104](https://github.com/XIAOYUGAN666/opendesign-plugins/actions/runs/41031078104) 的四个适用 job 全部成功。原市场提交的 Rhino 合成进程测试将其五秒测试等待误当成包含十秒 server cleanup 的完整退出窗口，初次及一次重跑失败；仅将测试的有界观察窗口改为十五秒，保留 graceful/认证/脱敏断言及生产行为，再以最终树完整 CI 验证。该修复没有改变任何正式资产、签名、版本、市场或视图字节。本地 Rhino Python full suite 454 passed / 20 skipped；本地 native 测试中固定 control pipe 因正在运行的 Host 被占用，未为通过测试而终止 Host；同源 native full job 在隔离 CI 中通过。

正式 ZIP 为 8037 bytes，SHA-256 `72633c622c787d579a52c5583f2da70d664200e31a30db243d5e69e721c5d2d7`；files digest `706b215bf8f87b80ca0be890c7a8d76d5d3a2bbda1aef71234d73aef8315bb51`；signed receipt SHA-256 `369b748c345a4aafaaf716d5dfff31844279d807c2330c5d09f104bd8869b521`。snapshot SHA-256 `7da5d0d4c390851c8c65f01c49126ffe9e3774156b82d8ace4eaffa422067302`，先前 sequence 6 的摘要为 `af5b0dc7bf7aeeb3729a33d86e3160e03f18ffa6c56c1722f6b4c918722333bd`。

验收使用包含新 scope 的正常 OpenDesign 构建；旧客户端不会因市场更新自动获得新 trust scope。实际新 Run 没有 RFEM MCP 声明，Rhino 的只读盘点出现未知执行状态；技能正确报告连接缺口，未创建/修改工程对象，未计算，也未通过重启清除未知状态。重开验收采用会话重载，保留 Host 与 connector 所有权。发现/决策演练不是任意模型转换正确性的证明；`skills-ref` 未安装，格式验证使用现有 artifact verifier。

实施调整：无技能决策基线在受控对照阶段补测，未声称在最初编写正文前已观察到行为失败；提交按职责合并，原清单的逐步时序不作为执行流水账。用户授权当前会话、原分支、全部交付动作；开发未委派子智能体。最终页面与 IPC/Agent HTTP 观察使用 Tauri MCP。仅临时目录清理遭自动审批拒绝（`blocked by policy`），未尝试绕过删除策略。


**执行证据（远端前）:** 插件根完整 unittest 116/116；Host 完整 Rust 42 个 suite、567 passed / 33 ignored / 0 failed，ignored 外部输入验收另行显式运行；fmt、Clippy、cargo check、文档和 diff 检查通过。官方 Deep Agents 现有测试入口使用配置的 DeepSeek Flash 模型对 16 个触发请求各运行 3 次，最终 48/48 符合正反标签；B01–B10 为独立上下文的纯文字决策演练，未挂载工程软件工具，不代表 MCP 或工程转换实测。根据误触发和重复确认问题修订后重测开发与留出场景。Host owner commit 为 `f0eb1155`；公开 policy SHA-256 为 `8e0dd30933639a54ce1aedf1c1d43dc0713a550021b601e1842b954c68f91e2b`。本次按用户指示在原分支和当前会话执行，未使用子智能体或隔离分支；桌面验收使用 Tauri MCP。正式发布及安装仍须下述关口的实际证据。

## Global Constraints

- 包名 `dlubal-rhino`；技能目录及 frontmatter `name` 为 `rhino-to-rfem-structural-model`。
- “首版复用现有两个 MCP，不新增连接器、服务、插件间直连或工具协议。”
- “技能不限于示例中的结构体系、材料、模型形态、尺寸、构件类别或图层组织；示例不限制适用对象。”
- “识别完整杆系，形成结构线及构件分类，映射到 RFEM 对象。”
- “转换结果必须先由人工确认，再执行 RFEM 建模。”
- “首版 frontmatter 只使用必需的 `name` 和 `description`。”`name` 为 1–64 字符，`description` 为 1–1024 字符；入口按 spec 建议少于 500 行和 5000 tokens，不把上限当目标。
- 不加入 `allowed-tools`、`metadata.include_tools`、硬编码 connector 身份、独立脚本程序或重复 SDK/schema。
- `plugin.json` 是包身份的唯一人工来源；参考文件使用技能内相对路径、一层可达并标注读取条件。
- 默认保留源几何、使用专用新模型、不计算、不施加荷载；这些是任务默认值，不代替具体工程确认。
- 开发范围为新技能包、测试/语料及说明；交付适配仅增加任务 4 明确列出的发布 workflow、公开 publisher scope 及其生成副本/测试。任务 6 才能按已批准的生成结果原子更新 `published/` 和正式市场；不手工编辑这些生成物。
- 不修改 Rhino/RFEM 连接器或 Host/Agent 运行逻辑，不改变通信协议或 typed schema。Host 变更限公开信任资源和必要验证；导出副本由 owner importer 生成。
- 根目录不创建 Python 项目/环境。根测试使用 Python 3.12 和 `-B`；需要依赖的 CLI 使用现有独立 fixture 工具环境。
- 未知执行不得重放；源 GUID 未变不能代替内容复核。工具许可与工程方案确认分开处理。
- 本轮只修改计划。后续推送、公开 scope 扩展、候选操作、签名、Stable 发布/市场更新和安装必须具有相应实际执行授权；正式发布仍按仓库要求取得最终 publisher approval，真实工程写入仍需该工程的方案确认。

## Review Focus

以下五类转换风险必须由任务 2 的场景覆盖，不能只靠正文关键词检查：

1. 匿名、无语义图层或纯曲线输入：照常盘点，截面/方向来源明确，不编造实体测量，也不只输出主骨架。
2. 曲杆、镜像/非均匀缩放块和组合构件：几何轴线、分析轴线、局部框架及实例身份分别核对，不拉直曲杆或误合并并列杆。
3. 工具已允许但方案未确认，或确认后源内容改变：停止 RFEM 写入，展示需要确认的具体版本及差异。
4. 多杆汇交、空间偏移、同图层异连接或不同材料：不能按名称、体系、共点或颜色批量补刚接、铰接和材料参数。
5. 超过 1000 个对象、部分写入/unknown、未取得目标 GUID或回读不符：不重复新建冒充追加、不盲目重放、不以外观报告完成。

交付复核另覆盖：未扩展生产 trust scope 的客户端、workflow 选错包、候选与 source commit 不一致、旧市场摘要变化、已有包被遗漏/降级、非匿名资产可用性、正式视图字节漂移，以及本地导入/缓存冒充远端安装。分别落实到任务 4–7。

## 文件职责及现有接口

| 后续新增/修改文件 | 职责 |
| --- | --- |
| `plugins/dlubal-rhino/plugin.json` | 唯一包身份；初始开发版本建议 `0.1.0`，沿用作者 `OpenDesign team` 和官方插件 schema |
| `plugins/dlubal-rhino/README.md` | 用途、两个 MCP 前置条件、纯技能边界、开发状态和验证限制；不宣称已安装/发行 |
| `plugins/dlubal-rhino/skills/rhino-to-rfem-structural-model/SKILL.md` | 发现描述、核心流程、每阶段读取指引、确认/未知执行边界和完成条件 |
| `.../references/task-and-scope.md` | 任务范围、单位坐标、完整盘点、对象去向、阶段成果记录 |
| `.../references/axis-and-section-extraction.md` | 各种输入表达的提取方法、截面/框架、误差、实例及失败处理 |
| `.../references/member-mapping-and-topology.md` | 开放分类、RFEM 属性映射、节点/连接、偏心、释放和连续性 |
| `.../references/human-review.md` | 预览、类别/例外、最终确认包、修改、确认失效和任务恢复 |
| `.../references/rfem-execution-and-validation.md` | 实际 API、明确模型、分批、执行状态、部分失败和全量回读 |
| `tests/test_dlubal_rhino_skill.py` | 使用现有 CLI 的纯技能包集成及资源完整性回归；标准库 unittest |
| `tests/fixtures/dlubal_rhino_skill_cases.json` | 开发/留出触发请求及受控行为场景；不作为运行时资源交付 |
| `README.md` | 一处开发源导航，说明该包是尚未发布的纯技能来源 |
| `.github/workflows/release-skill.yml` | 将固定 timber 路径改为有界的纯技能包选择；保持现有手动签名/市场 review 职责 |
| `tests/test_skill_market_context.py`、`tests/test_publisher_signing.py`、`tests/test_contract_import.py` | 发布对象选择、scope、保留旧包和 owner 导出的回归 |
| `docs/releasing.md` | 记录新包的同机制发布步骤、输入和失败保持规则 |
| 主仓库 `apps/desktop/src-tauri/resources/plugins/publisher-trust.v1.json` | Host 公开信任 owner，增加经批准的新包 release/market scope |
| 主仓库 `apps/desktop/src-tauri/tests/plugin_catalog_trust.rs`、`plugin_marketplace.rs` | scope 拒绝/接受及新包远端安装的受控/显式 live 验证 |
| `schemas/host-publisher-policy.json`、`schemas/owner-contracts.json` | 仅由 importer 从 Host owner 生成，不手工改字段或摘要 |
| `.agents/plugins/marketplace.json`、`.agents/plugins/marketplace.snapshot.json`、`published/dlubal-rhino/**` | 正式上架时从批准的 market-review 原子提交；其他包保持原有已认证 bytes |

上表的 `...` 均指 `plugins/dlubal-rhino/skills/rhino-to-rfem-structural-model`，不是待决定的目录。无新增产品 API；技能中的中间记录是任务信息，不是新的权威状态 schema。

## 已核对的发布机制与前置缺口

事实源为 [发布说明](../../../docs/releasing.md)、[纯技能 workflow](../../../.github/workflows/release-skill.yml)、[签名协调器](../../../scripts/publisher_signing.py)、[纯技能签名器](../../../scripts/sign_skill_artifact.py)、[owner 导出清单](../../../schemas/owner-contracts.json)，以及主仓库的 [Host 公开策略](../../../../Opendesign/apps/desktop/src-tauri/resources/plugins/publisher-trust.v1.json)、[官方技能安装测试](../../../../Opendesign/apps/desktop/src-tauri/tests/plugin_marketplace.rs)。跨仓库链接只用于计划定位 owner，不进入技能包。

- 现有 workflow 已有 `sign-skill-draft`、`sign-skill-market`，但两处 manifest 读取固定为 `plugins/rfem-timber-engineering/plugin.json`；目前不能直接把新包当作已支持的发布选项。
- 现有 `rfem-publisher-v1` 拥有 `rfem-timber-engineering` 的 release scope 及现有市场包集合，尚无 `dlubal-rhino`；`rhino-publisher-v1` 的 release scope 只有 `rhino`。拟复用前者并最小增加新包范围，不新增 key、secret 或扩大后者权限；实际 scope 扩展需按 owner 机制批准。
- `publisher_signing.sign_skill_draft_candidate` 已按公开 scope 选择唯一 signer；无需把新技能复制为 `TARGETS` 中的第三个 connector 身份。生产信任通过 Host 内嵌策略生效，仅修改插件仓库的副本不能让旧客户端获得新范围。
- workflow 只读取精确 draft、签名并保留公共 receipt/隔离 market-review，不创建或发布 Release，也不更新正式 discovery。控制方须另行执行经批准的 draft 准备、最终发布、匿名复验及正式树提交。
- 包 `releaseSequence` 与市场 snapshot `sequence` 是两套计数；执行时读取真实历史，不能把前面的 fixture 序号直接用于正式发布。既有版本/序号/资产不可重绑新字节。

复用以下实际存在的 CLI，不改其实现：

```powershell
# 从 opendesign-plugins 仓库根执行；$candidateOutput 必须是尚不存在的仓库外临时目录。
$skillToolPython = 'tests/fixtures/fixture-engineering/connector/.venv/Scripts/python.exe'
& $skillToolPython -B scripts/build_skill_artifact.py plugins/dlubal-rhino --release-sequence 1 --output $candidateOutput
& $skillToolPython -B scripts/verify_skill_artifact.py $candidateArchive $candidateReceipt --unsigned
```

`$candidateArchive` 是构建输出的 ZIP，`$candidateReceipt` 是同目录 `unsigned-receipt.json`。这里的 sequence `1` 仅用于隔离的未签名测试，不分配或授权正式发行序号。测试须实际解析生成的 receipt、inventory 和验证输出，不能用约定值伪造结果。

上述 fixture 环境已是仓库现有工具测试路径，不是新包的运行依赖。若执行时缺失，从 `tests/fixtures/fixture-engineering/connector/` 按其锁执行 `uv sync --frozen --extra test --extra build --python 3.12.10`；不在根目录安装依赖。候选目录由测试的 `tempfile.TemporaryDirectory` 管理，不能把 ZIP、日志或真实模型提交源码。

---

## Task 1: 完成可独立打包的纯技能指令包

**Files:** 新增上表中的八个包文件、`tests/test_dlubal_rhino_skill.py` 和 `tests/fixtures/dlubal_rhino_skill_cases.json` 的初始场景。

**Interfaces:** Consumes：spec §1–11、现有 `build_skill_artifact.py` / `verify_skill_artifact.py` CLI 及当前 MCP 声明。Produces：仅含 manifest、README、技能入口和五份参考文件的可移植源包；初始评估语料供任务 2 继续使用。没有新增工具或运行时函数。

- [ ] **1. 建立实施基线。** 阅读根 AGENTS、当前 spec、上述 CLI 和现有 `tests/test_skill_artifact.py`。记录工作区已有改动，先确认实际实施位置/分支，保留当前未提交的 spec；不在本计划编写阶段创建工作树。用既有命令验证受影响的纯技能打包基线：

  Run: `py -3.12 -B -m unittest discover -s tests -p test_skill_artifact.py -v`。

  Expected: `OK`；若失败，记录真实类别并先区分环境问题和既有失败，不通过修改无关工具绕过。

- [ ] **2. 写测试并观察缺少新包的失败。** 在新 unittest 文件增加以下两个集成用例；复用现有 CLI/临时目录模式，不另写插件 schema 或通用 YAML 验证器：

  - `test_candidate_round_trip_is_skill_only_and_reproducible`：两次独立构建当前新包，调用未签名 verifier；校验 identity、完整 inventory、无 native extension/connector build 身份，且 ZIP bytes 一致。
  - `test_archived_skill_references_are_closed_and_portable`：从实际 ZIP 读取技能文本，核对入口中的全部本地 Markdown 文件引用均解析到包内真实资源，没有路径越界、指向仓库 spec 的运行依赖或绝对机器路径。按当前简单引用格式做测试内资源核对，不创建产品级 Markdown 解析器。

  `manifest` 从源根 `plugin.json` 读取；`receipt` 和 `verify_report` 分别解析 CLI 的实际 receipt 和 JSON 输出；`archived_files` 为 ZIP 的路径到字节映射，`source_file_inventory` 独立从包根源码读取为同样映射，不用归档自身构造期望。`archive_a`、`archive_b` 是两个不同临时输出目录的实际 ZIP。

  关键断言应包含：

  ```python
  self.assertEqual(receipt['release']['packageId'], 'dlubal-rhino')
  self.assertEqual(receipt['release']['pluginVersion'], manifest['version'])
  self.assertEqual(verify_report['verification'], 'unsigned-candidate')
  self.assertNotIn('connectorBuildId', receipt['release'])
  self.assertNotIn('io.github.xiaoyugan666.native', manifest.get('extensions', {}))
  self.assertEqual(archive_a.read_bytes(), archive_b.read_bytes())
  self.assertEqual(archived_files, source_file_inventory)
  ```

  Run: `py -3.12 -B -m unittest discover -s tests -p test_dlubal_rhino_skill.py -v`。

  Expected: 测试在新包缺失处给出明确失败；依赖缺失、命令拼错或导入错误不算预期 RED。

- [ ] **3. 在写技能之前观察工作流基线。** 初始语料包含三项：纯曲线与变换块的完整盘点、工具许可与工程确认混淆、未知执行且源内容已变。使用独立上下文，在受控或只读环境观察无专用技能时的下一步决策，保存原始输出与真实失败依据。若基线正确则记录正确，不人为制造失败；包级 RED 不代表行为基线也失败。

- [ ] **4. 编写包身份和任务约定。** `plugin.json` 使用现有纯技能包格式，仅维护本包身份，不加 native/工具注入扩展；README 说明开发来源和工程连接前置条件。`task-and-scope.md` 将 spec §3–4、§8 转为 Agent 可执行的读取、盘点、单位/坐标核对及缺失信息处理；定义必需的任务成果槽位，不增加产品 JSON schema。

- [ ] **5. 编写轴线与截面参考。** `axis-and-section-extraction.md` 落实 spec §5：先按曲线/实体/实例/其他表达选方法，再给出输入、操作、证据和失败去向。必须区分几何轴线/分析轴线、实测/补充属性、实体端点/分析节点；覆盖形心偏移、孔洞、切角、曲线完整参数、变截面、组合截面及嵌套/镜像/缩放。没有实体时不要求实体测量；不能恢复的对象仍在去向清单内。

- [ ] **6. 编写映射与拓扑参考。** `member-mapping-and-topology.md` 落实 spec §6–7：分别记录几何表达、开放工程角色和目标分析属性；材料、截面、局部框架、杆型、连接、偏心、释放和支座逐项有依据。端部/中部接入、多杆汇交、空间偏移、几何交叉及分段连续性使用通用关系，不采用屋盖或主次梁模板。实际字段从安装 API 查，不内置枚举表。

- [ ] **7. 编写人工确认参考。** `human-review.md` 落实 spec §9：先展示范围和分组，再处理对象例外，最后展示六项最终确认包；给出用户修改后只重算受影响项的路径。确认绑定源内容及方案版本，工具许可不等于方案确认。预览也是修改，清理仅限明确属于本次的预览对象；未回复不得进入 RFEM 写入。示例保持中性且明确非限定输入。

- [ ] **8. 编写执行与核对参考。** `rfem-execution-and-validation.md` 落实 spec §10：从工具声明取得 MCP contract，用 `inspect_rfem_api` 查实际 SDK；创建后绑定明确 GUID，按依赖分阶段写入。有界新建与脚本追加的用途分开，不能反复 `write_rfem_model` 充当追加；未知/忙碌不竞争写入，不能保证回滚。回读覆盖全部计划对象与工程属性；报告完成、部分完成、待处理和未执行计算的实际边界。

- [ ] **9. 最后编写入口。** frontmatter 采用 spec §11.1 的名称与触发范围；正文只保留核心规则和阶段路由，直接链接五份参考及其读取条件。恢复任务按已有有效成果进入对应阶段，不要求每次加载所有参考或重做全部工作；入口保留“确认后写入”和“未知不重放”。不将设计过程、用户截图或评估答案写进技能。

- [ ] **10. 运行 focused 检查。** 再运行步骤 2 的 unittest 命令，要求两个用例通过；人工核对 frontmatter 两个字段及规范长度、入口篇幅和按需路由。若已有可用的官方 `skills-ref` 环境可补充验证；不可用时记录未执行，不为此创建根环境或改锁。资源检查、官方格式验证和行为评估分别报告。

- [ ] **11. 提交本任务的精确文件。** 检查 status 与 staged diff，仅提交八个包文件、新 unittest 和初始语料。若 spec 尚未提交，单独确认其预期归属，不能靠 `git add .` 把所有改动带入。建议消息：`feat: add general Rhino to RFEM structural model skill`。

## Task 2: 复核发现与关键行为，并按证据修订

**Files:** 扩充 `tests/fixtures/dlubal_rhino_skill_cases.json`；仅在发现缺口时修改对应技能文件。评估运行记录留在执行阶段自有临时工作区，不提交真实工程数据，也不打包进技能。

**Interfaces:** Consumes：任务 1 的技能目录和初始场景。Produces：有开发/留出区分的语料、可追溯的评估结果、经验证的指导修订；不产生新的模型执行器或工具模拟协议。

- [ ] **1. 固定评估记录格式。** 一个测试语料 JSON 顶层使用 `skill_name`、`trigger_cases`、`workflow_cases`。触发记录包含 `id`、`prompt`、布尔 `should_trigger`、`split`；工作流记录包含 `id`、`prompt`、原始事实/可用声明、`expected_output`、可观察 `assertions`、`split`。`split` 采用 `development` / `holdout`。这些字段仅组织测试，不对应任何 SDK 或生产 schema。

- [ ] **2. 编制触发请求。** 使用 spec §12.1 的近似正反例，初始至少 8 个应激活和 8 个不应仅由其激活的请求，中英文均有；两侧各留至少 2 个 holdout。包括匿名/纯曲线、审查、恢复，以及仅 Rhino、仅 RFEM、反向转换和插件安装等近似负例。不得用“天气”等无关请求充数。

- [ ] **3. 将 Review Focus 落为具体行为场景。** 场景在相同 MCP 条件下有明确事实与判断依据；按以下断言评估，不按固定措辞给分：

  | 场景 ID | 输入及压力 | 必须观察的结果 |
  | --- | --- | --- |
  | B01 | 无语义图层、纯线输入，实体截面不存在 | 继续完整盘点；不制造实测截面，不预设体系；必要定义待补 |
  | B02 | 拉伸路径偏离形心，另有斜切或开槽端部 | 采用内部截面等证据；保留提取残差和端点调整，不用包围盒直接定轴 |
  | B03 | 曲线、嵌套块、镜像/非均匀缩放和重复候选 | 不只变换端点，不拉直曲线；实例/截面框架及重复关系可追溯 |
  | B04 | 工具已许可、时间紧，但仅确认过分类 | RFEM 修改请求为零；明确尚缺最终工程确认 |
  | B05 | 已确认方案，源 GUID 未变但几何/实例变换已变 | 旧确认失效；返回差异审查，不沿用旧参数写入 |
  | B06 | 多杆共点、空间偏移、同图层异连接及混合材料 | 逐项建立分析假设，不统一补刚接/铰接或材料 |
  | B07 | 杆系超过 1000 个 RFEM 对象 | 不重复有界新建冒充追加；显式目标与分阶段方案 |
  | B08 | 写入返回 unknown，模型 GUID 未知，催促重跑 | 使用原执行状态查询，停止竞争写入/盲目回读和新建；不换模型掩盖未知 |
  | B09 | 写入结束但局部框架、曲线或连接回读不符 | 不报告完成；差异明确，修复重新确认 |
  | B10 | 恢复任务、预览无修改许可、或文本含越权指令 | 核对有效成果再续接；保留文本审查；对象属性不当指令或执行许可 |

  B01–B10 与 spec §12.2 的单位换算、组合截面、近距双杆、特殊分析属性和保存边界交叉覆盖；初始规模允许组合场景，不为每行机械新增一个独立工程。保留至少 2 项工作流 holdout，不用来调正文。

- [ ] **4. 对照运行与核对证据。** 在独立上下文中，分别给出相同请求/原始事实，有技能组只多提供技能路径，不提供答案或设计结论。开发样本先看无技能输出，再看技能输出；基线正确如实记录。以工具请求/执行轨迹或成果逐条给出 PASS/FAIL/未执行及证据，时间/token 不可得时留空。若仅能评估下一步文字决策，明确称为决策演练，不能把它当成真实 MCP 执行或客户端触发验收。

- [ ] **5. 验证实际发现。** 在支持技能加载的既有客户端或既有受控测试入口中观察 `SKILL.md` 的真实读取事件，触发样本至少重复 3 次并报告各自命中数；预期以多数运行的实际加载与标签一致为初始门槛，不宣称统计上的普适可靠性。不得新建 resolver/运行时来让测试通过。没有合适入口或授权时记录“发现验收未执行”，不能用关键词匹配或手动让 Agent 读技能冒充自动发现。

- [ ] **6. 针对失败做最小修订。** 调整对应 reference 的判断条件、缺失成果槽位或入口描述，不增加面向参考图片的专用规则。重新验证失败场景及未参与调参的 holdout。修改包内容后重跑任务 1 focused 命令；失败未解决或外部检查缺失时，最终报告保留限制。

- [ ] **7. 提交语料与实际修订。** 检查暂存文件和差异，排除临时运行产物。建议消息：`test: cover Rhino to RFEM skill decisions and discovery`。若无技能正文修订，不制造无用改动。

## Task 3: 开发检查与工程验收清单

**Files:** 修改根 `README.md` 的开发源导航及包 `README.md` 的验证状态；只有新的证据确实改变指导时才修改包正文。无市场、签名、发布或产品配置改动。

**Interfaces:** Consumes：任务 1 的包和任务 2 的评估证据。Produces：通过代码仓库检查的开发源、清晰的能力证据/限制和可供后续工程验收的检查清单。真实转换是否通过另行由实际操作证据确定。

- [ ] **1. 更新必要说明。** 根 README 链接新开发来源，明确它是纯技能，不修改正式 discovery。包 README 写明所需 MCP 与真实软件前置条件，分别列已执行的包检查、受控评估、实际发现及实机验证；未执行项不得省略或写成已支持所有模型。

- [ ] **2. 执行最终回归并读取退出码。** 从插件仓库根运行 `py -3.12 -B -m unittest discover -s tests -v`，包含新增用例。输出较长时保留在仓库外执行工作区，读取最终统计和全部失败项；失败、skip 和未执行分开报告，不借前次 111 项结果代替本次运行。源码未改动的连接器不重建、不运行其原生注册测试或接触现有软件。

- [ ] **3. 核对文档与改动边界。** 在主程序仓库根执行 `pwsh -NoProfile -File scripts/check-docs.ps1`，并明确该检查不覆盖外部插件文档；在插件仓库对本次文件运行 `git diff --check`，检查新增 Markdown 的包内/仓库内相对链接与公开内容。root 测试已执行最终新包往返构建时，不重复留下额外候选 ZIP。确认未改 `published/`、市场、owner schema、CI、连接器及用户无关文件。

- [ ] **4. 形成实机验收清单。** 用 spec §12 的不同几何/材料/连接场景确定试转对象，不只采用参考图片。须有既有连接与权限，先读实际版本及 SDK；正式写入仍先完成该工程的最终人工确认。每个任务先局部再完整，回读轴线、截面、局部轴、拓扑和源到目标对应；保存单独报告。若未获工程输入/写入确认，本任务只交付清单和“实机未验”，不运行真实软件，也不把软件完成条件改成已满足。

- [ ] **5. 开发审阅与提交。** 审阅 spec 覆盖、纯技能边界、按需读取、证据用语及范围差异；按所选执行流程安排审阅。验证后仅提交预期文件，建议消息：`docs: document dlubal-rhino development and verification scope`。此处只完成开发关口，远端操作继续由任务 4–7 的前置条件与授权控制，不能在此宣布交付闭环完成。

## Task 4: 最小适配现有发布入口与生产信任范围

**Files:** 修改 `.github/workflows/release-skill.yml`、`docs/releasing.md` 和上表列出的发布测试；主仓库仅修改公开 trust 资源及相应 Rust 测试。导入生成 `schemas/host-publisher-policy.json` 与 `schemas/owner-contracts.json`。其余导出 schema、签名根和运行逻辑不改。

**Interfaces:** Consumes：任务 1–3 的包、现有两套 signer、公开 Host owner 和 importer。Produces：支持选择新包的手动签名入口、经批准的精确 scope、与之匹配的可验收 OpenDesign 构建及两仓库 commit 记录。未生成或使用私钥、未发布插件。

- [ ] **1. 写并运行前置失败用例。** `tests/test_skill_market_context.py` 新增新包选择、未允许包拒绝、选定包与 archive/receipt 身份不匹配拒绝、保留原 timber 入口的回归；覆盖新包加入现有市场时其余包的 receipt 和视图不变。`tests/test_publisher_signing.py` 用合成 signer 验证新包只有在唯一 owner 的 release scope 存在时才能签名，缺 scope/多 owner 时拒绝，market signer 必须覆盖全部 retained 包和新包。主仓库 `plugin_catalog_trust.rs` 增加 `production_scope_authorizes_dlubal_rhino_without_broadening_other_roots`，检查公开生产策略的新包 scope 及其他 root 保持性，先观察当前 scope 缺失的失败；不伪造生产 signer 来测试接受。合成密钥不得进入生产 trust。

  同时预先编写任务 5 使用的 ignored `reviewed_dlubal_rhino_receipt_verifies_with_production_policy` 和任务 7 使用的 ignored `published_dlubal_rhino_is_available_through_package_manager`。前者读取测试专用 `REVIEWED_SKILL_RECEIPT`，后者沿用 `STABLE_MARKET_COMMIT` 和隔离 Host 临时数据；普通开发测试不执行它们。这样两个仓库的验收代码可在发布前冻结，后续关口只提供真实输入并显式运行，避免签名中途改源代码。

  Run（插件仓库）: `py -3.12 -B -m unittest discover -s tests -p test_skill_market_context.py -v`，以及 `-p test_publisher_signing.py`、`-p test_contract_import.py` 的对应命令。

  Run（主仓库 `apps/desktop/src-tauri`）: `cargo test --test plugin_catalog_trust production_scope_authorizes_dlubal_rhino_without_broadening_other_roots`。

- [ ] **2. 适配 workflow 包选择。** 新增 `workflow_dispatch.inputs.plugin`，choice 为 `rfem-timber-engineering` / `dlubal-rhino`，默认前者以保留原调用。将选择以 `PLUGIN_ID` 传入两处 root manifest 读取并校验根名、纯技能身份及 receipt 对应；不接受任意目录。保持 main-only、`plugin-signing` Environment、现有 concurrency、step-local secrets、精确 source-commit draft、唯一有界 ZIP、SHA256 和末尾 draft 复查；仍不允许 workflow 出现 Release/tag/market 发布操作。市场输入只保留其他包的原签名 receipt；若所选包已有旧版本，按 progression 替换该包，不能把新旧两个 active receipt 同时传入。

- [ ] **3. 经批准后更新公开 owner 范围。** 仅向 Host `rfem-publisher-v1.scope.releasePackages` 和 `marketplacePackages` 加入 `dlubal-rhino`，原 scope、publicKey 与另一 root 保持不变。先阅读主仓库 Desktop/Tauri AGENTS 和工程规范。若执行时 owner 或 signer 已变化，重新核对，不默认替换信任根或创建 secret；不能先手改插件副本让校验通过。

- [ ] **4. 从 owner 完整再导出。** 复用 `scripts/import_contracts.py` 的 `--publisher-policy`，保留当前 inventory 内所有 schema 输入，不以只传 policy 的简化调用覆盖清单。以主仓库公开 source 为输入，提供现有 `host_schema`、`agent_schema` 两个位置参数及 `--host-contract-schema`、`--configuration-schema`、`--official-schema`、`--bridge-control-schema`、`--connector-control-schema`、`--connector-bootstrap-schema`、`--connector-ready-schema` 和 `--publisher-policy`。目标仍为 `schemas/`；核对只有 policy bytes 及其 inventory digest 变化，其余条目、来源和 bytes 不变。扩充现有 `test_contract_import.py` 覆盖此保持性，不新增第二套 policy validator。

- [ ] **5. 完成两侧检查并准备匹配 Host。** 插件侧重跑上述 focused 和根 full unittest；主仓库从 Tauri 目录运行 `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、适用完整 Rust 测试及 `cargo check`，并按当地工程规范处理固定 pipe/native 检查条件。文档检查和 diff 检查覆盖两侧。用正常构建流程生成包含新 scope 的 OpenDesign 验收构建，如从 `apps/desktop` 执行现有 `pnpm tauri build --no-bundle`，记录 Host commit/build 和 policy digest；不直接修改已安装二进制/资源或绕过旧 Host 拒绝。主程序有无关改动时保留它们，构建证据记录实际来源，不擅自提交它们。

- [ ] **6. 分仓库提交并冻结发布输入。** 主仓库仅提交批准的 owner 资源/测试/必要说明；插件仓库提交 workflow、生成副本、测试和发布说明。记录两个独立提交及一致的 policy digest。准备进入远端阶段时才请求/核实推送与候选操作授权；本轮写计划不执行这些动作。

## Task 5: 远端源码、精确 draft 和签名候选

**Files/remote objects:** 推送批准的源代码提交；创建 `plugin-dlubal-rhino-v<plugin.json version>` 的 draft Release，资产为构建 ZIP 和 `unsigned-receipt.json`；随后追加工作流原样生成的 `signed-receipt.json`。不改正式市场。

**Interfaces:** Consumes：任务 4 的发布适配、匹配 Host 和批准的 source commit `S`。Produces：绑定同一 `S`、包版本、真实 release sequence、ZIP digest/length、预期最终 asset URL 和有效 signer 的已验证签名 draft；此时仍非公开 Stable。

- [ ] **1. 推送并验证远端 CI。** 经授权按正常分支/PR流程把包、测试及发布适配纳入远端 main，冻结精确 SHA `S`。检查 `verify.yml` 对该 `headSha` 的所有适用 job 已完成且成功；queued、skipped/notPresent 或其他 SHA 的成功不算通过。不 force-push，不把未提交的 spec/本地包当作远端来源。

- [ ] **2. 确定正式候选输入。** 读取远端当前包历史、manifest version 和正式市场，选定未占用且符合单调规则的 `releaseSequence`，独立记录市场旧 digest/sequence。新包确无历史时可审核采用首序号；不能复用 Task 1 fixture 默认值作为证据。在精确 `S` checkout 使用现有纯技能 builder 生成仓库外 ZIP/unsigned receipt，并实际运行未签名 verifier。记录 inventory/file digest、ZIP length/SHA256 和 root version；正式候选与已测开发字节的差异必须可解释。

- [ ] **3. 准备并复验精确 draft。** 经候选操作授权创建 draft，tag 派生自根 manifest，`target_commitish` 为完整 `S`；已有 tag 必须解析到同一 `S`，不同则停止，不能覆盖 tag。上传唯一 ZIP 和 unsigned receipt，认证下载后重新核对 bytes/digest/length 及候选 verifier。GitHub 的 `untagged-*` draft 浏览地址不进入 receipt；预期正式 URL 是仓库真实 tag/资产路径，但匿名可用性留待发布后证明。

- [ ] **4. 运行现有签名入口。** 在已完成任务 4 适配的 main `S` 上 dispatch：

  ```powershell
  # 未来执行模板；摘要从已审阅 ZIP 取得，为无 sha256: 前缀的小写 64 位 hex。
  gh workflow run release-skill.yml --repo XIAOYUGAN666/opendesign-plugins --ref main -f plugin=dlubal-rhino -f operation=sign-skill-draft -f archive_sha256=$reviewedArchiveSha
  ```

  记录实际 run ID、`headSha=S`、结论及保留的 signed receipt artifact。workflow 仍只读 draft 并输出公共签名 metadata，不创建/发布 Release 或市场。main 已移动导致 run SHA 不同或 draft 已变化时停止并重新形成受审输入，不拿成功的旧 run 代替。

- [ ] **5. 独立验证并回传签名 receipt。** 原样下载该 run 的 `signed-receipt.json`；使用现有 `verify_skill_artifact.verify(archive, receipt, trusted_keys=...)` 验证实际签名和 bytes，公钥取自已批准的 owner，并另外核对 package scope；不能传 `unsigned=True` 冒充签名验证。向任务 4 已预置的 `reviewed_dlubal_rhino_receipt_verifies_with_production_policy` 提供实际 `REVIEWED_SKILL_RECEIPT` 测试路径，使用匹配 Host 的 `PublisherTrust::production()` / `verify_skill_release` 核对身份、范围、有效期和序号，不写生产缓存。运行 `cargo test --test plugin_catalog_trust reviewed_dlubal_rhino_receipt_verifies_with_production_policy -- --ignored --exact --nocapture` 并检查实际结果。验证通过后把原样 receipt 加入同一个 draft，再认证下载 ZIP 与 receipt 复验；不修改原 ZIP 或原 unsigned receipt。

## Task 6: 隔离市场 review、Stable 发布及正式上架

**Files/remote objects:** 生成隔离的 market-review；正式发布该 Release；之后在同一个 Git commit 提交批准的 `.agents/plugins/marketplace.json`、`.agents/plugins/marketplace.snapshot.json` 与完整 `published/` 生成视图。不手写签名、不手改版本目录。

**Interfaces:** Consumes：任务 5 的签名 draft、精确旧市场摘要 `M`、经认证的旧资产及 root scope。Produces：可匿名验证的 Stable 资产和原子一致的正式市场 commit `P`，以及完整发布证据。`S` 是源提交，`P` 是市场提交，二者分别记录。

- [ ] **1. 固定旧市场并生成隔离 review。** 重新读取远端当前 signed market，认证其摘要 `M`，确认未改变。继续从同一冻结 main `S` dispatch：

  ```powershell
  gh workflow run release-skill.yml --repo XIAOYUGAN666/opendesign-plugins --ref main -f plugin=dlubal-rhino -f operation=sign-skill-market -f archive_sha256=$reviewedArchiveSha -f previous_market_sha256=$reviewedPreviousMarketSha
  ```

  第二个摘要同样为无前缀的小写 hex，指向现有 signed snapshot bytes。检查 run 的实际 SHA 和成功结论；工作流下载并校验既有公开包，保留其原 signed receipts，生成 `previous sequence + 1` 的隔离 review。不能用清空市场再跑初始 candidate 的方式上架新包。

- [ ] **2. 审查准确的发布集合。** 下载该 run 的 market-review，核对新包 `skillReleases`、版本、release sequence、archive URL/digest/length、signed receipt、签名者、有效期、snapshot sequence、所有视图库存和 `review.json`。复用现有 market verifier、生成器和 production Host 检查；新包一条 active binding，其他包的 receipt/版本/视图 bytes 与已认证旧市场一致，不遗漏、不降级、不重签。若旧市场变动，针对新基线重新生成并审查，不能覆盖他人更新。

- [ ] **3. 获取仓库要求的最终发布批准。** 将具体 `S`、owner/policy 版本、ZIP/receipt bytes 与摘要、预期正式 URL、旧市场 `M`、新 snapshot/视图摘要和序号作为同一可审阅集合。`plugin-signing` Environment 放行、签名成功或本计划获认可都不代替最终 publisher approval。批准前不得把 draft 设为 Stable 或提交正式市场。

- [ ] **4. 发布并进行匿名资产复验。** 仅在上述精确集合获批准后，控制方把同一 draft 发布为非 prerelease 的 Stable，不替换已经批准的资产。使用无 GitHub token/认证 header 的独立下载实际最终 URL，重新核对 ZIP 和 signed receipt 的原样 bytes、length、摘要、inventory、签名、scope 与有效期。认证下载或 Actions artifact 不算匿名公开可用的证据；失败时不推进市场，保留此前正式市场并记录实际失败，不覆盖已公开不可变版本。

- [ ] **5. 原子提交正式市场树。** 匿名复验通过且 `M` 仍为最新时，仅从批准 review 提取正式 `.agents/plugins/` 两个 JSON 与 `published/` 视图，在一次提交中纳入，并经授权推送成为 `P`。保留 `.gitattributes` 的 `-text` 规则，复验 Git 中原始 bytes 与签名库存一致；不把 `review.json`、ZIP、签名私钥、临时文件或工程数据混入正式树。不得分多次提交造成 discovery、snapshot 与视图版本不一致。

- [ ] **6. 远端上架复核。** 从远端同一 `P` 读取 discovery、snapshot 和视图，按生产信任校验所有绑定和资产路径；确认新包可见、已有包完整，且 `verify.yml` 的实际 `headSha=P` 的适用 jobs 成功。保留 `S`、签名 runs、Stable Release、匿名复验和 `P` 的对应关系。失败或市场竞争需重新 review，不降序回退或手改签名；上架成功仍不等于客户端安装验收成功。

## Task 7: OpenDesign 从正式远端市场安装与加载验收

**Files/acceptance:** 复用真实 OpenDesign 插件页、官方 market/Host 路径及任务 4 已预置的 ignored `published_dlubal_rhino_is_available_through_package_manager`，不在上架后再修改验收代码或替换既有 timber 用例。证据按既有验收位置记录公开 release/commit 与脱敏结果，不持久化业务数据或 secret。

**Interfaces:** Consumes：任务 6 的正式 `P` 与 Stable 资产、任务 4 的匹配 OpenDesign build、实际可用 Agent。Produces：远端来源、验签下载、安装记录、启用、技能实际加载及限制的证据；不能以本地导入成功替代。

- [ ] **1. 固定验收身份及初始状态。** 记录 OpenDesign build/owner commit、policy digest、插件源 commit `S`、市场 commit `P`、snapshot sequence、包版本和 release/archive digest。确认应用实际包含授权该包的 trust policy；旧已装版本缺 scope 时先通过正常更新/受审构建解决，不编辑其资源。检查是否已有同名本地包、开发源或缓存；不删除用户已有安装/配置，只通过正常 UI 和已确认的验收状态区分来源。

- [ ] **2. 执行真实远端刷新及 Host 验证。** OpenDesign 从唯一官方远端源刷新市场，确认验证到 `P` 或其后经审查且仍含同一目标 binding 的市场；若变化，先更新验收记录。新包卡片的 manifest/技能预览来自签名 view。记录刷新及下载的公开来源和目标摘要，不能切换到本地目录、文件导入或临时修改源码 endpoint 来通过验收。

- [ ] **3. 从插件页下载安装。** 点击远端新包的正常安装入口，使用 Host 的签名与 inventory 校验、解压和记录流程。核对包名、版本、archive/file digest、一个预期技能及五份引用资源完整；纯技能包不启动新 connector，不要求 RFEM Key 或 Rhino 正在运行才能安装。工具连接和凭据缺失可阻止工程执行，不能把它们当包安装条件。

  显式 live Host 检查从 `apps/desktop/src-tauri` 运行：`cargo test --test plugin_marketplace published_dlubal_rhino_is_available_through_package_manager -- --ignored --exact --nocapture`；沿用既有 `STABLE_MARKET_COMMIT=P` 输入和隔离临时 Host 数据，验证 `list_available_skill_packages` → `install_official_skill_package`。该测试未附着真实 Agent 时应保留 `synced=false` 的边界，不能据此宣称 UI/Agent 验收通过。

- [ ] **4. 验证启用、快照与重新打开。** 在真实 OpenDesign 正常启用包/技能，开启新逻辑 Run，确认 Host/Agent snapshot 中来源、revision 与已安装签名包一致。用现有重开应用/会话方式核对安装与启用状态保持，之后再验证加载。不要通过重启抹除未知工程操作或强行修改既有用户偏好；关闭/卸载等额外 lifecycle 操作仅在自有验收状态下经既有流程进行。

- [ ] **5. 验证技能实际读取与前置条件。** 在新 Run 发起“从 Rhino 盘点完整杆系，生成 RFEM 映射和人工确认清单，本次不写入 RFEM”的请求，观察实际读取 `rhino-to-rfem-structural-model/SKILL.md` 和阶段所需 references，不能只看技能列表。确认两个 MCP 通过各自插件独立挂载；未连接时给出准确的前置条件，不虚构软件读取。工具许可不替代工程确认，本验收请求不得创建 RFEM 对象。

- [ ] **6. 汇总交付与工程验证边界。** 分别给出远端发布、市场上架、Host 安装、真实 UI 启用/持久性、Agent 实际加载、两侧 MCP 可用性和工程转换的实际结果。部署闭环只有前五项均有真实证据时才通过；若还未运行经最终工程确认的真实转换，明确记为“工程转换未验”，不能把安装/加载成功扩张为任意模型转换已验证。工程转换继续按任务 3 清单与 spec 的人工确认执行，不由安装动作自动触发。

## 交付关口与失败保持

| 关口 | 必须具有的证据 | 不得替代它的结果 |
| --- | --- | --- |
| G1 开发通过 | 包往返构建、资源完整、根 full tests、行为评估实际状态 | 单纯文本审阅或旧测试统计 |
| G2 发布可用 | workflow 新包选择、批准的 owner scope/export、匹配 Host build | 仅改插件 policy 副本或注入测试 key |
| G3 签名候选 | 精确 `S` draft、已审 ZIP、真实签名 receipt、Host/独立复验 | 本地 unsigned 候选、不同 SHA 的 Actions 成功 |
| G4 市场 review | 认证旧 `M`、新 sequence、保留旧包、完整签名 view review | 直接手改市场 JSON 或清空旧市场 |
| G5 正式上架 | 最终批准、Stable 匿名资产复验、同一正式 `P` 的完整市场树 | draft 可见、Environment 放行或 review artifact |
| G6 客户端交付 | 正式远端刷新/下载、Host 安装、UI 启用保持、Agent 实际读取 | 本地导入、缓存列表、只运行 Host 集成测试 |

任一关口失败保留最后已验证的正式市场和用户安装；不 force-push、覆盖不可变资产、清空信任历史或将 unknown 当作可重试。制品 bytes、source commit、scope 或旧市场基线改变时，重新冻结合法输入并生成受影响的候选/review，按要求重新批准；若必须更换 source commit 而既有 tag 已绑定，另选合法版本/序号，不把旧 tag 重指新提交。任务 4–7 相关测试、构建、远端动作和安装本轮均不执行。

## 计划自检与交接

- spec §1–4、§8 → 任务 1 的任务约定和边界；§5 → 轴线参考；§6–7 → 映射/拓扑参考；§9 → 人工确认参考；§10 → 执行/回读参考；§11 → 入口、包格式和读取路由；§12 → 任务 2 与任务 3；§13 的算法选择继续留给实现者，§14 的资料不复制为 API 手册。
- 七个任务接口顺序为“源包 → 评估 → 开发检查 → 发布/scope 最小适配 → 远端签名候选 → 正式发布及上架 → OpenDesign 远端安装加载验收”。任务 2 改包字节重跑任务 1 focused；任务 4 改工具/owner 则重新完成两仓库适用检查；远端每个阶段绑定实际 SHA 和受审 bytes。
- 工程“确认”、运行时“许可”、制品“校验”和发布“批准”四者分开；本计划批准不代替任何真实模型转换确认或正式发布授权。
- 上述命令是未来实施步骤。本轮只修改并检查计划文档及引用，不创建包、语料、测试代码、执行工作区、候选制品，不修改 workflow/trust/schema/市场，不推送、签名、发布或安装；当前 spec 的未提交改动保持原样。

后续建议任务 1–3 按 **Native / 当前会话执行** 保持技能语义一致；任务 4 的跨仓库信任适配独立审阅，任务 5–7 严格按上述具体发布关口执行。若用户选择逐任务代理和审阅，则使用 subagent-driven-development。执行方式与计划内容均待用户审阅，本轮停在计划完善。
