# 麦麦权益先用谁

把麦当劳积分、账户券和兑换成本整理成最多三条有依据的关注项。近期不消费时，直接给出“无需为了权益额外花钱”。

这是麦当劳程序员节创意开发大赛的非官方作品，当前版本 **0.2.1**。适合想知道“我有哪些权益、哪些值得先看”的普通用户。Python 3.10+，仅标准库，无需安装依赖。

## 一分钟体验

在本目录打开终端：

```powershell
python -B -X utf8 scripts/mcd_rights.py --demo --interactive
python -B -X utf8 scripts/mcd_rights.py --demo --catalog --product-id 103 --cash-budget 0
python -B -X utf8 scripts/mcd_rights.py --demo --plan no
```

以上全部使用明确标注的合成数据，不联网。第二条演示：兑换现金0元，但用券另需7元，因此超过0元总预算；另一SKU现金缺失，保留未知。完整输出见[合成演示](examples/demo.md)。

## 查询自己的权益

从[官方平台](https://open.mcd.cn/mcp/doc)获取本人Token。Windows首次运行下面命令，在隐藏输入中粘贴Token；脚本通过Windows DPAPI保存到当前用户的`%LOCALAPPDATA%/McdRights/token.dpapi`，凭证位于技能目录外。

```powershell
python -B -X utf8 scripts/mcd_rights.py --set-token
python -B -X utf8 scripts/mcd_rights.py --live --interactive
python -B -X utf8 scripts/mcd_rights.py --live --focus points --plan no
python -B -X utf8 scripts/mcd_rights.py --live --catalog --plan yes --visit-date 2026-10-25
```

最后一条的日期换成自己的计划。macOS/Linux通过当前进程的`MCD_MCP_TOKEN`环境变量提供Token；Windows也支持该方式。不要把真实Token放入仓库或命令参数。

Windows也可在`--set-token`或`--live`时追加`--token-file "<私有目录下token.dpapi的绝对路径>"`，选择自定义加密凭证文件。显式文件优先于环境变量，读取失败不回退；未指定时依次使用环境变量、默认DPAPI文件。已有凭证经本人授权迁移时，移动原加密文件即可，无需重新输入Token。私有目录应在公开仓库和技能包之外；后续每次实时查询都需指定新路径。

默认查询时间、积分、账户券。只有加`--catalog`才查商品券列表。用户选定列表中的商品ID后，再用`--product-id ID --cash-budget 金额`查该商品的SKU成本。每次先刷新商品列表，未找到ID则拒绝查询详情。`--format json`可查看完整字段依据。

默认只向终端显示，不保存真实账户报告。退出码0表示所请求查询完成；1表示初始化、鉴权或配置失败；2表示部分失败或券列表覆盖不全，应查看仍有效的结果和缺口。

## 在WorkBuddy中使用

技能入口为[SKILL.md](SKILL.md)，脚本与其同目录打包。按照[WorkBuddy技能文档](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)，在“技能 → 添加技能 → 上传技能”导入配套ZIP，启用后新建任务：

> 使用mcd-rights，先运行合成数据演示，解释兑换现金0元为什么仍可能超预算。

配置Token后可说：

> 使用mcd-rights，只查我的积分；我最近不打算消费。

本技能经Python客户端直连官方MCP，不要求把完整麦当劳连接器开放给Agent。宿主需要能执行Python脚本。后台WorkBuddy桥接只支持推理和Read，不具备执行脚本的权限，因此后台评审、Python运行与桌面宿主验收分别记录。

2026-10-23真实桌面新任务已通过自然语言识别并加载已安装技能，实际执行了`--live --focus points --plan no`。原凭证路径在普通本机进程中存在，在WorkBuddy执行环境中返回不存在，查询退出1，未发起账户请求。用户已将精确文件的读取规则设为允许，旧会话复测仍失败，具体隔离原因尚未确认。随后按用户明确要求迁移原DPAPI文件，保持密文不变，新路径本机真实积分查询成功；桌面控制工具当前不可用，迁移后的WorkBuddy三场景尚待验收，见[桌面验收](tests/desktop-validation.json)。不要据此重复配置Token或把失败解释为积分为0。WorkBuddy权限与安全中心的说明见[官方文档](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Permission-Modes)。

## 怎样判断“先用谁”

- 积分按本月、下月汇总，保留接口真实字段`currentMouthExpirePoint`和`nextMouthExpirePoint`，不编造逐笔截止日期。
- 券保留展示起止时间，账户持有或`enable=1`均不证明门店订单可用。缺时间、失效标记、截止边界均标待核实。
- 商品同时检查状态与起止时间。状态在售但时间过期的条目不进入候选；候选仍不保证库存、资格或可兑换。
- SKU成本分开显示积分、兑换现金`price`和用券现金`extTradePrice`。预算按两个现金阶段合计，缺失不当0。
- 关注项先分“先关注／可以稍后看”，同级券按截止时间排列；积分与券不换算成统一价值或最省钱分数。无消费计划时不输出行动推荐。

## 验证与边界

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

验证截至2026-10-23：58项自动测试通过；真实积分、空券列表、商品列表查询通过，9项候选、15项因冲突或不足被排除；真实无效Token返回401。测试摘要见[实时验证](tests/live-validation.json)与[鉴权失败验证](tests/auth-failure-validation.json)，不含账户积分数、券码或凭证。真实账户当时没有券，因此有券排序只通过合成用例验证；2026-10-23已补齐真实商品详情和完整引导查询验证。

客户端在发出请求前限制为5个只读工具，拒绝领券、兑换、订单、抽奖。券每页最多200条、最多5页，重复、页数变化或中途失败均报告覆盖不全；不重试网络失败，不用缓存冒充实时结果。工具、协议和字段见[MCP接入说明](MCP_INTEGRATION.md)。

WorkBuddy已真实参与方案与规则评审，实际任务、用量、失败和采纳情况见[开发记录](workbuddy.md)。不能据此推定赛事联动奖励资格。仓库包含未改动的官方[参赛声明](CONTEST_DECLARATION.md)，公开提交前需参赛者本人审阅。项目仓库：https://github.com/18701789542/mcd-rights 。尚未报名。

修复内容见[版本记录](CHANGELOG.md)。0.2.0修正业务成功标记与错误码冲突、跨时区券排序，以及损坏响应的错误处理。

自编代码采用[MIT](LICENSE)许可；官方参赛声明模板保持原文，不纳入自编代码许可。

0.2.0核心流程：一条引导命令完成消费计划、权益查询、商品选择与SKU判断；同时比较积分余额和两阶段总现金。真实商品详情与引导流程已验证，见[核心实测](tests/core-live-validation.json)。开发验收输入不是用户的真实消费偏好。
