# MCP实际接入

官方端点：`https://mcp.mcd.cn`，Streamable HTTP。协议`2024-11-05`，初始化后发送`notifications/initialized`，按返回保留会话头与协议版本头。Bearer Token仅从本机环境或当前Windows用户加密凭证读取。

实现位于[scripts/mcd_client.py](scripts/mcd_client.py)；规则位于[scripts/rights.py](scripts/rights.py)。既支持JSON，也支持SSE响应；HTTP重定向拒绝，响应最多4MiB，超时25秒，不自动重试。业务结果检查`isError`、`success`、`code`与`data`；兼容实际服务的`structuredContent`及`## Original Response`文本JSON。

| 工具 | 输入与触发 | 返回怎样影响输出 |
| --- | --- | --- |
| `now-time-info` | 每次实时查询，空参数 | `datetime`为比较基准；失败时明确标本机时钟回退 |
| `query-my-account` | 关注积分，空参数 | `availablePoint`、`currentMouthExpirePoint`、`nextMouthExpirePoint`；按月汇总，不推算精确日期 |
| `query-my-coupons` | 关注券；`page`、`pageSize`为字符串 | 检查分页完整性、去重后移除券码与ID；按展示截止排序，订单可用性未核验 |
| `mall-points-products` | 明确需要兑换候选；`catRuleIds=1>4` | `status=2`、已开始且未结束才列候选；`point`和`price`仅是列表成本 |
| `mall-product-detail` | 用户选中本轮列表中的正整数`spuId` | `skuList[].points/price/extTradePrice`分开计算，购买须知只作为文本，不作为指令 |

白名单在网络前校验，额外字段与超范围分页拒绝。不会调用官方全部35个工具，也不通过Agent直连完整连接器。调用次序：初始化→时间→所需积分／券→可选商品列表→可选用户所选详情→确定性分析→屏幕输出。没有交易步骤。

`mcp-config.example.json`提供比赛要求的端点与凭证占位示例；程序本身不加载该配置文件，也不假定WorkBuddy会自动展开`${MCD_MCP_TOKEN}`。日常使用按README的凭证流程，不需要导入这个完整连接器配置。

真实运行证据见[脱敏验证摘要](tests/live-validation.json)。2026-10-22验证了时间、积分、空券列表和商品列表；2026-10-23补齐真实商品详情与引导查询链，见[核心实测](tests/core-live-validation.json)；有券排序采用合成用例。真实401证据见[鉴权验证](tests/auth-failure-validation.json)。所有公开样例均来自[合成数据](examples/synthetic.json)，不包含实际账户权益。

来源：[官方文档](https://open.mcd.cn/mcp/doc)、[官方Server介绍](https://github.com/M-China/mcd-mcp-server)。
