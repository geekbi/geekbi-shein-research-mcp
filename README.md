# 极鲸云 SHEIN 数据分析与市场调研 MCP

极鲸云 SHEIN 数据分析与市场调研 MCP 是由极鲸云（GeekBI）提供的 SHEIN 电商数据分析服务，通过 MCP 协议为 AI Agent 提供专业的市场调研工具。查询商品、图搜同款、店铺、类目、关键词和评论，研究需求、价格带、竞争、托管模式与用户反馈。

## 能力

| MCP 工具 | 可以解决的问题 |
| --- | --- |
| `shein_site_list` | 实时查询 SHEIN 可用站点、站点 ID 和币种。用户指定非默认市场时先调用；不猜测站点 ID。 |
| `shein_goods_search` | 搜索和筛选 SHEIN 商品，分析价格、销量、销售额、评分、上架时间及当前接口提供的竞争指标。只生成用户所需的最小条件，未覆盖全部分页时明确为样本。 |
| `shein_image_search` | 用本地图片、URL、Data URI 或 Base64 图片搜索 SHEIN 视觉同款，并在视觉候选池内叠加商品条件。视觉相似不等于规格、材质或供应链完全相同。 |
| `shein_mall_search` | 搜索和筛选 SHEIN 店铺，分析经营规模、增长、供给结构和竞争表现。未覆盖全部分页时明确为样本。 |
| `shein_category_search` | 搜索 SHEIN 类目及市场指标，取得可信类目 ID，研究规模、需求、供给与竞争；类目 ID 不可猜测。 |
| `shein_keyword_search` | 搜索 SHEIN 关键词及需求供给指标，研究热度、增长和竞争；不能把局部样本表述为全市场。 |
| `shein_review_search` | 按明确商品 ID 查询 SHEIN 评论，分析卖点、痛点和规格风险；不能用于全站找商品，也不以单条评论外推。 |

## 安装 / 更新

请直接将下面这段话发送给支持 MCP 的 AI 助手：

```text
安装这个MCP: https://github.com/geekbi/geekbi-shein-research-mcp
```

## 手动安装

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后将以下 JSON 粘贴到客户端的 Stdio 服务配置中：

```json
{
  "mcpServers": {
    "geekbi-shein-research": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/geekbi/geekbi-shein-research-mcp@master",
        "geekbi-shein-mcp"
      ]
    }
  }
}
```

安装完成后，重新启动客户端或新建会话即可使用。
