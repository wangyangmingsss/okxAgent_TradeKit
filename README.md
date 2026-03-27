# OKX Agent Trade Kit — 四大进阶 AI 交易智能体

![OKX AI Hackathon](https://img.shields.io/badge/%23OKXAI%E6%9D%BE-Hackathon-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Agents](https://img.shields.io/badge/Agents-4-orange)

> 基于 OKX API 构建的四大进阶 AI 交易智能体，覆盖资金费率套利、闪崩抄底、动量轮动、期权波动率狩猎四大策略场景。

---

## 概览

| 智能体 | 名称 | 策略 | 使用 API |
|--------|------|------|----------|
| **Agent E** | 资金费率收割机 | Delta 中性资金费率套利 | market + trade + portfolio |
| **Agent F** | 闪崩猎手 | 闪崩检测 & 自动抄底 | market + trade |
| **Agent G** | 动量轮动引擎 | 跨资产动量排名 & 轮动 | market + trade + portfolio |
| **Agent H** | 期权波动率猎人 | IV 曲面异常检测 & 交易 | market + option |

---

## Agent E — 资金费率收割机

**策略描述：** 自动监控全市场永续合约资金费率，当费率超过阈值时，通过现货买入 + 永续做空构建 Delta 中性头寸，稳定收割资金费率收益。

**核心功能：**
- 实时扫描全市场资金费率，筛选高费率币种
- 自动构建 Delta 中性对冲组合（现货多 + 永续空）
- 动态监控对冲比例，自动 rebalance
- 风险评估与头寸管理

**Demo 运行结果：**

| 指标 | 数值 |
|------|------|
| BTC 年化收益率 | **8.60%** |
| 稳定性评分 | **1.3558** |
| 对冲方式 | Delta-Neutral Hedge |

![Agent E Demo](screenshots/screenshot_agent_e.png)

---

## Agent F — 闪崩猎手

**策略描述：** 实时监控市场价格异动，通过多维度指标（价格跌幅、成交量暴增、深度失衡）综合评估闪崩概率，在极端行情中自动执行抄底策略。

**核心功能：**
- 多维度闪崩评分系统（价格、量能、深度）
- 实时监控 BTC、ETH、SOL 等主流资产
- 自动触发分批抄底逻辑
- 止损止盈自动管理

**Demo 运行结果：**

| 币种 | 闪崩评分 | 状态 |
|------|----------|------|
| BTC | **4.8** | MONITOR |
| ETH | **12.1** | MONITOR |
| SOL | **9.3** | MONITOR |

![Agent F Demo](screenshots/screenshot_agent_f.png)

---

## Agent G — 动量轮动引擎

**策略描述：** 基于多时间框架动量因子，对主流加密资产进行排名，自动卖出弱势资产、买入强势资产，实现跨资产动量轮动。

**核心功能：**
- 多时间框架动量因子计算（1h / 4h / 1d）
- 跨资产强弱排名系统
- 自动执行轮动交易（卖弱买强）
- 持仓权重动态优化

**Demo 运行结果：**

| 操作 | 详情 |
|------|------|
| 排名第一 | **SOL** — STRONG |
| 卖出 | OKB (WEAK) |
| 买入 | SOL (STRONG) |

![Agent G Demo](screenshots/screenshot_agent_g.png)

---

## Agent H — 期权波动率猎人

**策略描述：** 实时扫描 BTC/ETH 期权链的隐含波动率（IV）曲面，检测 IV 异常偏离（z-score），捕捉波动率均值回归交易机会。

**核心功能：**
- 全期限 IV 曲面实时构建
- z-score 异常检测算法
- 自动识别 IV 过高/过低的期权合约
- 波动率套利策略执行

**Demo 运行结果：**

| 指标 | 数值 |
|------|------|
| 检测到 IV 异常数 | **14** |
| Top 异常 | BTC-USD-260325-62000-C |
| IV | **96.1%** |
| z-score | **+2.89** |

![Agent H Demo](screenshots/screenshot_agent_h.png)

---

## 快速开始

### 环境要求

- OKX CLI (`okx-cli`) 已安装并配置 API Key
- Python 3.8+
- Bash 环境

### 安装

```bash
git clone https://github.com/wangyangmingsss/okxAgent_TradeKit.git
cd okxAgent_TradeKit
```

### 运行单个 Agent

```bash
# 资金费率收割机
python agents/agent_e_funding_harvester.py

# 闪崩猎手
python agents/agent_f_flash_crash_hunter.py

# 动量轮动引擎
python agents/agent_g_momentum_rotation.py

# 期权波动率猎人
python agents/agent_h_options_iv_hunter.py
```

### 一键运行全部

```bash
bash run_all.sh
```

---

## 架构

```
┌─────────────────────────────────────────────────────┐
│                   OKX Agent Trade Kit                │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐
│  │  Agent E   │  │  Agent F   │  │  Agent G   │  │  Agent H   │
│  │  Funding   │  │  Flash     │  │  Momentum  │  │  Options   │
│  │  Harvester │  │  Crash     │  │  Rotation  │  │  IV Hunter │
│  │            │  │  Hunter    │  │  Engine    │  │            │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
│        │              │              │              │        │
│        ▼              ▼              ▼              ▼        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              OKX CLI / OKX API v5                    │    │
│  ├──────────┬──────────┬──────────┬────────────────────┤    │
│  │  market  │  trade   │portfolio │     option         │    │
│  └──────────┴──────────┴──────────┴────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 项目结构

```
okxAgent_TradeKit/
├── agents/
│   ├── __init__.py
│   ├── agent_e_funding_harvester.py   # 资金费率收割机
│   ├── agent_f_flash_crash_hunter.py  # 闪崩猎手
│   ├── agent_g_momentum_rotation.py   # 动量轮动引擎
│   └── agent_h_options_iv_hunter.py   # 期权波动率猎人
├── screenshots/                        # Demo 运行截图
├── docs/
│   └── index.html
├── run_all.sh                          # 一键运行脚本
├── requirements.txt
├── LICENSE
└── README.md
```

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

## 致谢

- [@okxchinese](https://twitter.com/okxchinese) — OKX 中文社区
- [@OpenClaw](https://twitter.com/OpenClaw) — 开源贡献

---

**#OKXAI松 #OKX #AgentTradeKit**
