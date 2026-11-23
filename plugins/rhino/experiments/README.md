# Rhino 和 Grasshopper 工程评估

任务、原生测量、评判阈值、校准变体和修复范围检查取自 [RhinoMCP 固定提交](https://github.com/jingcheng-chen/rhinomcp/tree/3c192bd8c800360f9fbce70d921d9d94bc7dc77f/experiments)。上游许可随插件源码保留于 [LICENSE.upstream](../LICENSE.upstream)。测量脚本沿用 RhinoCommon 对保存模型的读数。回归夹具、保存后的模型和执行记录存放在专用临时目录。

在 OpenDesign 用 GPT-6 Luna 执行 `tasks/` 里的任务。用 Tauri MCP 核对消息、工具回执和审批，用 agent-desktop 核对 Rhino 或 Grasshopper 中的实际对象和定义。工程评估在与候选包匹配的独立 Rhino 进程上读取保存模型，Grasshopper 验证使用实际求解与输出快照。请将工具记录和工程判定一同保存。

先将候选包安装到专用 Rhino 实例。该实例在启动时应处于确认静止状态。从插件源目录，用已验证候选的内置 Python 运行原生正反例。端口从 OpenDesign 的 Rhino 设置读取。

```powershell
& $candidatePython -B -c 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("experiments.validate_live",run_name="__main__")' (Get-Location).Path --bridge-port 1999 --output $testEvidence
```

已保存模型的单项评估使用 `experiments.runner`，提供 `--task`、`--input`、`--output` 和 Rhino `--bridge-port`，Grasshopper 使用 `--input` 传入与上游夹具相同结构的实际快照 JSON。JSON 报告记录任务、制品和测量源摘要、读数与判定。

候选修复使用 `experiments.repair`。提供固定 `--base`、仓库外全新的 `--candidate`、经审阅的 `--patch` 以及逐项 `--allow` 的仓库相对路径。校验保留上游文件清单、文件模式和修改集合比较。候选目录中的源文件可交给既有独立构建，按相同阈值对照基线、候选及还原基线。补丁、编译制品及结果保留在候选工作目录。
