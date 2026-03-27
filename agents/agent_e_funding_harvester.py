#!/usr/bin/env python3
"""
资金费率收割机 (Funding Rate Harvester)
Delta-neutral funding rate arbitrage AI Agent for OKX.

Scans perpetual swap funding rates, ranks opportunities by yield-adjusted
stability score, then executes a hedged spot+swap position on the best
opportunity to harvest funding payments risk-free.

Uses the OKX CLI in demo (simulated trading) mode.
"""

import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# ANSI Colours
# ─────────────────────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
UNDER   = "\033[4m"

RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"

BG_BLUE = "\033[44m"
BG_GRN  = "\033[42m"
BG_YEL  = "\033[43m"
BG_RED  = "\033[41m"
BG_MAG  = "\033[45m"
BG_CYAN = "\033[46m"

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

OKX_ENV = {
    **os.environ,
    "OKX_API_KEY":    os.environ.get("OKX_API_KEY", ""),
    "OKX_SECRET_KEY": os.environ.get("OKX_SECRET_KEY", ""),
    "OKX_PASSPHRASE": os.environ.get("OKX_PASSPHRASE", ""),
}

# Instruments to scan
INSTRUMENTS = [
    {
        "swap": "BTC-USDT-SWAP",
        "spot": "BTC-USDT",
        "ccy": "BTC",
        "spot_sz": "0.001",       # Small demo amount
        "swap_sz": "1",           # 1 contract = 0.01 BTC
        "ct_val": 0.01,           # Contract value in base ccy
    },
    {
        "swap": "ETH-USDT-SWAP",
        "spot": "ETH-USDT",
        "ccy": "ETH",
        "spot_sz": "0.01",
        "swap_sz": "1",
        "ct_val": 0.1,
    },
    {
        "swap": "SOL-USDT-SWAP",
        "spot": "SOL-USDT",
        "ccy": "SOL",
        "spot_sz": "0.1",
        "swap_sz": "1",
        "ct_val": 1.0,
    },
    {
        "swap": "OKB-USDT-SWAP",
        "spot": "OKB-USDT",
        "ccy": "OKB",
        "spot_sz": "0.1",
        "swap_sz": "1",
        "ct_val": 1.0,
    },
]

SWAP_LEVERAGE = "2"
SWAP_MGN_MODE = "cross"
HISTORY_LIMIT = 20

# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}"


def print_banner():
    w = 64
    print()
    print(_c("=" * w, BOLD + CYAN))
    print(_c("  " + " " * 8 + "资 金 费 率 收 割 机" + " " * 8, BOLD + WHITE))
    print(_c("  " + " " * 4 + "Funding Rate Harvester  v1.0" + " " * 4, DIM + WHITE))
    print(_c("  " + " " * 2 + "Delta-Neutral Funding Rate Arbitrage Agent" + " " * 2, DIM + CYAN))
    print(_c("=" * w, BOLD + CYAN))
    print()


def step(num: int, cn: str, en: str):
    print(f"\n{BOLD}{CYAN}[步骤 {num}]{RESET} {BOLD}{cn}{RESET}")
    print(f"  {DIM}{en}{RESET}")
    print(f"  {DIM}{'─' * 56}{RESET}")


def info(label: str, value: Any, indent: int = 4):
    pad = " " * indent
    print(f"{pad}{BLUE}{label:<18}{RESET} {WHITE}{value}{RESET}")


def ok(text: str):
    print(f"    {GREEN}[OK] {text}{RESET}")


def warn(text: str):
    print(f"    {YELLOW}[!!] {text}{RESET}")


def fail(text: str):
    print(f"    {RED}[ERR] {text}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# OKX CLI wrapper
# ─────────────────────────────────────────────────────────────────────────────

def run_okx(*args: str, timeout: int = 30) -> Any:
    """Execute an okx CLI command with --demo --json flags. Returns parsed JSON."""
    cmd = ["okx", "--demo", "--json"] + list(args)
    display_cmd = " ".join(cmd)
    print(f"    {DIM}$ {display_cmd}{RESET}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=OKX_ENV,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(f"命令超时: {display_cmd}")
        return None
    except FileNotFoundError:
        fail("okx CLI 未找到，请确保已安装 okx 命令行工具")
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or "(无输出)"
        fail(f"命令失败 (exit {result.returncode}): {detail}")
        return None

    output = result.stdout.strip()
    if not output:
        return None

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        fail(f"JSON 解析失败，原始输出: {output[:200]}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FundingData:
    """Holds funding rate data for a single instrument."""
    inst_id: str
    spot_id: str
    ccy: str
    spot_sz: str
    swap_sz: str
    ct_val: float
    current_rate: float = 0.0
    next_rate: Optional[float] = None
    funding_time: int = 0
    historical_rates: list = field(default_factory=list)
    avg_rate: float = 0.0
    std_dev: float = 0.0
    annualized_yield: float = 0.0
    score: float = 0.0
    mark_price: float = 0.0


@dataclass
class TradeResult:
    """Holds execution result for a pair of trades."""
    inst: FundingData
    strategy: str  # "buy_spot_short_swap" or "sell_spot_long_swap"
    spot_order: Optional[dict] = None
    swap_order: Optional[dict] = None
    leverage_set: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Sparkline generator
# ─────────────────────────────────────────────────────────────────────────────

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], colour: str = WHITE) -> str:
    """Generate a Unicode sparkline from a list of values."""
    if not values:
        return _c("(无数据)", DIM)
    mn = min(values)
    mx = max(values)
    rng = mx - mn if mx != mn else 1.0
    chars = []
    for v in values:
        idx = int((v - mn) / rng * (len(SPARK_CHARS) - 1))
        idx = max(0, min(len(SPARK_CHARS) - 1, idx))
        chars.append(SPARK_CHARS[idx])
    return _c("".join(chars), colour)


def rate_colour(rate: float) -> str:
    """Return ANSI colour based on rate sign and magnitude."""
    if rate > 0.0005:
        return GREEN + BOLD
    elif rate > 0:
        return GREEN
    elif rate < -0.0005:
        return RED + BOLD
    elif rate < 0:
        return RED
    return DIM


# ─────────────────────────────────────────────────────────────────────────────
# Core logic
# ─────────────────────────────────────────────────────────────────────────────

class FundingHarvester:
    """资金费率收割机 — main agent class."""

    def __init__(self):
        self.instruments: list[FundingData] = []
        self.best: Optional[FundingData] = None
        self.trade_result: Optional[TradeResult] = None

    # ── Step 1: Scan funding rates ───────────────────────────────────────

    def scan_funding_rates(self) -> list[FundingData]:
        step(1, "扫描资金费率", "Scanning Funding Rates for Top Perpetual Swaps")

        results = []
        for inst_cfg in INSTRUMENTS:
            swap_id = inst_cfg["swap"]
            info("扫描合约", _c(swap_id, BOLD + WHITE))

            fd = FundingData(
                inst_id=swap_id,
                spot_id=inst_cfg["spot"],
                ccy=inst_cfg["ccy"],
                spot_sz=inst_cfg["spot_sz"],
                swap_sz=inst_cfg["swap_sz"],
                ct_val=inst_cfg["ct_val"],
            )

            # Fetch current funding rate
            data = run_okx("market", "funding-rate", swap_id)
            if data and isinstance(data, list) and len(data) > 0:
                entry = data[0]
                fd.current_rate = float(entry.get("fundingRate", 0) or 0)
                fd.next_rate = float(entry.get("nextFundingRate", 0) or 0) if entry.get("nextFundingRate") else None
                fd.funding_time = int(entry.get("fundingTime", 0) or 0)
                ok(f"当前费率: {fd.current_rate:+.6f}")
            else:
                warn(f"无法获取 {swap_id} 的资金费率")

            # Fetch historical funding rates
            hist_data = run_okx("market", "funding-rate", swap_id, "--history", "--limit", str(HISTORY_LIMIT))
            if hist_data and isinstance(hist_data, list) and len(hist_data) > 0:
                rates = []
                for h in hist_data:
                    r = float(h.get("fundingRate", 0) or 0)
                    rates.append(r)
                fd.historical_rates = rates
                ok(f"获取 {len(rates)} 条历史费率记录")
            else:
                warn(f"无法获取 {swap_id} 的历史费率")

            # Fetch mark price for P&L calculation
            ticker = run_okx("market", "ticker", swap_id)
            if ticker and isinstance(ticker, list) and len(ticker) > 0:
                fd.mark_price = float(ticker[0].get("last", 0) or 0)

            results.append(fd)
            print()

        self.instruments = results
        return results

    # ── Step 2: Rank opportunities ───────────────────────────────────────

    def rank_opportunities(self) -> list[FundingData]:
        step(2, "排名套利机会", "Ranking Arbitrage Opportunities by Score")

        for fd in self.instruments:
            # Annualized yield = |rate| * 3 funding periods/day * 365 days * 100%
            fd.annualized_yield = abs(fd.current_rate) * 3 * 365 * 100

            # Calculate average and std dev of historical rates
            if fd.historical_rates:
                n = len(fd.historical_rates)
                abs_rates = [abs(r) for r in fd.historical_rates]
                fd.avg_rate = sum(abs_rates) / n
                if n > 1:
                    mean = sum(fd.historical_rates) / n
                    variance = sum((r - mean) ** 2 for r in fd.historical_rates) / (n - 1)
                    fd.std_dev = math.sqrt(variance)
                else:
                    fd.std_dev = 0.0
            else:
                fd.avg_rate = abs(fd.current_rate)
                fd.std_dev = 0.0

            # Score = annualized_yield / (1 + stability)
            # Higher yield and lower variance = better score
            fd.score = fd.annualized_yield / (1 + fd.std_dev * 10000)

        # Sort by score descending
        self.instruments.sort(key=lambda x: x.score, reverse=True)

        # Display ranking table
        print()
        hdr = f"    {_c('排名', BOLD + WHITE):>12s}  " \
              f"{_c('合约', BOLD + WHITE):<20s}  " \
              f"{_c('当前费率', BOLD + WHITE):>18s}  " \
              f"{_c('年化收益', BOLD + WHITE):>18s}  " \
              f"{_c('波动率', BOLD + WHITE):>16s}  " \
              f"{_c('评分', BOLD + WHITE):>14s}"
        print(hdr)
        print(f"    {DIM}{'─' * 90}{RESET}")

        for i, fd in enumerate(self.instruments):
            rank = f"#{i + 1}"
            rc = rate_colour(fd.current_rate)
            rate_str = f"{fd.current_rate:+.6f}"
            yield_str = f"{fd.annualized_yield:.2f}%"
            std_str = f"{fd.std_dev:.8f}"
            score_str = f"{fd.score:.4f}"

            highlight = BG_GRN + BLACK if i == 0 else ""
            marker = " <-- 最佳" if i == 0 else ""

            if i == 0:
                print(f"    {_c(rank, GREEN + BOLD):>12s}  "
                      f"{_c(fd.inst_id, GREEN + BOLD):<20s}  "
                      f"{_c(rate_str, rc):>18s}  "
                      f"{_c(yield_str, GREEN + BOLD):>18s}  "
                      f"{_c(std_str, DIM):>16s}  "
                      f"{_c(score_str, GREEN + BOLD):>14s}  "
                      f"{_c('<<< 最佳机会', YELLOW + BOLD)}")
            else:
                print(f"    {_c(rank, DIM):>12s}  "
                      f"{_c(fd.inst_id, WHITE):<20s}  "
                      f"{_c(rate_str, rc):>18s}  "
                      f"{_c(yield_str, WHITE):>18s}  "
                      f"{_c(std_str, DIM):>16s}  "
                      f"{_c(score_str, WHITE):>14s}")

        return self.instruments

    # ── Step 3: Select best opportunity ──────────────────────────────────

    def select_best(self) -> Optional[FundingData]:
        step(3, "选择最佳机会", "Selecting Best Opportunity")

        if not self.instruments:
            fail("没有可用的套利机会")
            return None

        self.best = self.instruments[0]
        fd = self.best

        if fd.current_rate > 0:
            strategy = "正费率: 买入现货 + 做空合约 (多头付费给空头)"
            strategy_en = "Positive rate: BUY spot + SHORT swap (longs pay shorts)"
        elif fd.current_rate < 0:
            strategy = "负费率: 卖出现货 + 做多合约 (空头付费给多头)"
            strategy_en = "Negative rate: SELL spot + LONG swap (shorts pay longs)"
        else:
            warn("当前费率为零，无套利机会")
            return None

        print()
        info("选中合约", _c(fd.inst_id, GREEN + BOLD))
        info("当前费率", _c(f"{fd.current_rate:+.6f}", rate_colour(fd.current_rate)))
        if fd.next_rate is not None:
            info("预测下期费率", _c(f"{fd.next_rate:+.6f}", rate_colour(fd.next_rate)))
        info("年化收益", _c(f"{fd.annualized_yield:.2f}%", GREEN + BOLD))
        info("评分", _c(f"{fd.score:.4f}", GREEN))
        info("标记价格", f"{fd.mark_price:,.2f} USDT")
        print()
        info("策略", _c(strategy, YELLOW + BOLD))
        print(f"      {DIM}{strategy_en}{RESET}")

        return fd

    # ── Step 4: Execute delta-neutral position ───────────────────────────

    def execute_trades(self) -> Optional[TradeResult]:
        step(4, "执行Delta中性头寸", "Executing Delta-Neutral Position")

        if not self.best:
            fail("没有选定的交易机会")
            return None

        fd = self.best

        if fd.current_rate > 0:
            # Positive funding: buy spot, short swap
            spot_side = "buy"
            swap_side = "sell"
            strategy = "buy_spot_short_swap"
        else:
            # Negative funding: sell spot, long swap
            spot_side = "sell"
            swap_side = "buy"
            strategy = "sell_spot_long_swap"

        result = TradeResult(inst=fd, strategy=strategy)

        # ── Set swap leverage ────────────────────────────────────────────
        info("设置杠杆", f"{fd.inst_id} -> {SWAP_LEVERAGE}x ({SWAP_MGN_MODE})")
        lev_result = run_okx(
            "swap", "leverage",
            "--instId", fd.inst_id,
            "--lever", SWAP_LEVERAGE,
            "--mgnMode", SWAP_MGN_MODE,
        )
        if lev_result:
            result.leverage_set = True
            ok(f"杠杆已设置为 {SWAP_LEVERAGE}x")
        else:
            warn(f"杠杆设置可能失败，继续执行")
        print()

        # ── Place spot order ─────────────────────────────────────────────
        info("现货交易", f"{spot_side.upper()} {fd.spot_sz} {fd.ccy} ({fd.spot_id})")
        spot_result = run_okx(
            "spot", "place",
            "--instId", fd.spot_id,
            "--side", spot_side,
            "--ordType", "market",
            "--sz", fd.spot_sz,
            "--tdMode", "cash",
        )
        if spot_result:
            result.spot_order = spot_result
            if isinstance(spot_result, list) and len(spot_result) > 0:
                entry = spot_result[0]
                s_code = entry.get("sCode", "")
                ord_id = entry.get("ordId", "N/A")
                if s_code == "0" or not s_code:
                    ok(f"现货订单成功 (ordId: {ord_id})")
                else:
                    warn(f"现货订单返回: [{s_code}] {entry.get('sMsg', '')}")
            else:
                ok(f"现货订单已提交")
        else:
            fail("现货订单失败")
        print()

        time.sleep(1)

        # ── Place swap order ─────────────────────────────────────────────
        info("合约交易", f"{swap_side.upper()} {fd.swap_sz} 张 ({fd.inst_id})")
        swap_result = run_okx(
            "swap", "place",
            "--instId", fd.inst_id,
            "--side", swap_side,
            "--ordType", "market",
            "--sz", fd.swap_sz,
            "--tdMode", SWAP_MGN_MODE,
        )
        if swap_result:
            result.swap_order = swap_result
            if isinstance(swap_result, list) and len(swap_result) > 0:
                entry = swap_result[0]
                s_code = entry.get("sCode", "")
                ord_id = entry.get("ordId", "N/A")
                if s_code == "0" or not s_code:
                    ok(f"合约订单成功 (ordId: {ord_id})")
                else:
                    warn(f"合约订单返回: [{s_code}] {entry.get('sMsg', '')}")
            else:
                ok(f"合约订单已提交")
        else:
            fail("合约订单失败")

        self.trade_result = result
        return result

    # ── Step 5: P&L projection ───────────────────────────────────────────

    def project_pnl(self) -> dict:
        step(5, "收益预测", "Projecting Funding Income")

        if not self.best:
            fail("无法预测：无选中合约")
            return {}

        fd = self.best
        rate = abs(fd.current_rate)
        price = fd.mark_price if fd.mark_price > 0 else 1.0

        # Position notional = contract_count * contract_value * mark_price
        contract_count = int(fd.swap_sz)
        notional = contract_count * fd.ct_val * price

        # Funding income per period = notional * rate
        income_per_period = notional * rate

        # 3 funding periods per day (every 8 hours)
        daily = income_per_period * 3
        weekly = daily * 7
        monthly = daily * 30
        yearly = daily * 365

        # APR based on notional
        apr = (yearly / notional * 100) if notional > 0 else 0

        projection = {
            "notional": notional,
            "rate": rate,
            "per_period": income_per_period,
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
            "yearly": yearly,
            "apr": apr,
        }

        print()
        info("持仓名义价值", f"{notional:,.2f} USDT")
        info("当前费率(绝对值)", f"{rate:.6f}")
        print()

        # Income table
        tw = 52
        print(f"    {_c('┌' + '─' * tw + '┐', DIM)}")
        print(f"    {_c('│', DIM)} {_c('收益预测 (Funding Income Projection)', BOLD + WHITE):<{tw + len(BOLD + WHITE + RESET) - 1}s}{_c('│', DIM)}")
        print(f"    {_c('├' + '─' * tw + '┤', DIM)}")

        rows = [
            ("每期收入 (Per Period / 8h)", f"{income_per_period:.4f} USDT"),
            ("每日收入 (Daily)",           f"{daily:.4f} USDT"),
            ("每周收入 (Weekly)",          f"{weekly:.4f} USDT"),
            ("每月收入 (Monthly / 30d)",   f"{monthly:.4f} USDT"),
            ("年化收入 (Yearly / 365d)",   f"{yearly:.4f} USDT"),
            ("年化收益率 (APR)",           f"{apr:.2f}%"),
        ]
        for label, val in rows:
            line = f"  {label:<32s} {_c(val, GREEN + BOLD)}"
            # Approximate visible length for padding
            print(f"    {_c('│', DIM)} {line:<{tw + len(GREEN + BOLD + RESET) - 1}s}{_c('│', DIM)}")

        print(f"    {_c('└' + '─' * tw + '┘', DIM)}")

        return projection

    # ── Step 6: Dashboard report ─────────────────────────────────────────

    def print_report(self, projection: dict):
        step(6, "收割机仪表盘", "Funding Rate Harvester Dashboard")

        w = 72
        print()
        print(_c("╔" + "═" * w + "╗", CYAN + BOLD))
        print(_c("║", CYAN) + _c("  资金费率收割机 — 综合仪表盘", BOLD + WHITE).ljust(w + len(BOLD + WHITE + RESET) - 1) + _c("║", CYAN))
        print(_c("║", CYAN) + _c("  Funding Rate Harvester — Dashboard", DIM + WHITE).ljust(w + len(DIM + WHITE + RESET) - 1) + _c("║", CYAN))
        print(_c("╠" + "═" * w + "╣", CYAN + BOLD))

        # ── All scanned rates ────────────────────────────────────────────
        print(_c("║", CYAN) + _c("  [一] 全市场资金费率扫描", BOLD + YELLOW).ljust(w + len(BOLD + YELLOW + RESET) - 1) + _c("║", CYAN))
        print(_c("╟" + "─" * w + "╢", DIM))

        for fd in self.instruments:
            is_best = (self.best and fd.inst_id == self.best.inst_id)
            marker = _c(" *** 最佳 ***", GREEN + BOLD) if is_best else ""
            name_clr = GREEN + BOLD if is_best else WHITE
            rc = rate_colour(fd.current_rate)

            print(_c("║", CYAN) + f"  {_c(fd.inst_id, name_clr):<24s} "
                  f"费率: {_c(f'{fd.current_rate:+.6f}', rc)}  "
                  f"年化: {_c(f'{fd.annualized_yield:.2f}%', GREEN if fd.annualized_yield > 5 else WHITE):<12s} "
                  f"评分: {_c(f'{fd.score:.4f}', BOLD if is_best else DIM)}"
                  f"{marker}".ljust(w + 80) + "")
            # Historical sparkline
            if fd.historical_rates:
                abs_hist = [abs(r) for r in fd.historical_rates]
                spark = sparkline(abs_hist, CYAN if fd.current_rate >= 0 else MAGENTA)
                avg_str = f"{fd.avg_rate:.6f}"
                std_str = f"{fd.std_dev:.8f}"
                print(_c("║", CYAN) + f"  {'':>24s} "
                      f"历史: {spark}  "
                      f"均值: {_c(avg_str, DIM)}  "
                      f"标准差: {_c(std_str, DIM)}")
            print(_c("║", CYAN))

        # ── Strategy detail ──────────────────────────────────────────────
        print(_c("╟" + "─" * w + "╢", DIM))
        print(_c("║", CYAN) + _c("  [二] 最佳套利策略", BOLD + YELLOW).ljust(w + len(BOLD + YELLOW + RESET) - 1) + _c("║", CYAN))
        print(_c("╟" + "─" * w + "╢", DIM))

        if self.best:
            fd = self.best
            if fd.current_rate > 0:
                strat_cn = "正费率套利: 买入现货 + 做空合约"
                strat_en = "Positive Rate: BUY Spot + SHORT Swap"
                strat_logic = "多头支付资金费率给空头 -> 我们收取费率"
            else:
                strat_cn = "负费率套利: 卖出现货 + 做多合约"
                strat_en = "Negative Rate: SELL Spot + LONG Swap"
                strat_logic = "空头支付资金费率给多头 -> 我们收取费率"

            print(_c("║", CYAN) + f"  合约:     {_c(fd.inst_id, GREEN + BOLD)}")
            print(_c("║", CYAN) + f"  策略:     {_c(strat_cn, YELLOW + BOLD)}")
            print(_c("║", CYAN) + f"            {_c(strat_en, DIM)}")
            print(_c("║", CYAN) + f"  原理:     {_c(strat_logic, CYAN)}")
            print(_c("║", CYAN) + f"  杠杆:     {_c(f'{SWAP_LEVERAGE}x ({SWAP_MGN_MODE})', WHITE)}")
            print(_c("║", CYAN) + f"  现货数量: {_c(f'{fd.spot_sz} {fd.ccy}', WHITE)}")
            print(_c("║", CYAN) + f"  合约张数: {_c(f'{fd.swap_sz} 张 (1张={fd.ct_val} {fd.ccy})', WHITE)}")
        else:
            print(_c("║", CYAN) + _c("  (未选择策略)", DIM))

        # ── Execution results ────────────────────────────────────────────
        print(_c("╟" + "─" * w + "╢", DIM))
        print(_c("║", CYAN) + _c("  [三] 交易执行结果", BOLD + YELLOW).ljust(w + len(BOLD + YELLOW + RESET) - 1) + _c("║", CYAN))
        print(_c("╟" + "─" * w + "╢", DIM))

        if self.trade_result:
            tr = self.trade_result
            lev_icon = _c("[OK]", GREEN) if tr.leverage_set else _c("[--]", YELLOW)
            print(_c("║", CYAN) + f"  杠杆设置: {lev_icon} {SWAP_LEVERAGE}x {SWAP_MGN_MODE}")

            # Spot order
            spot_status = _c("[OK]", GREEN) if tr.spot_order else _c("[FAIL]", RED)
            spot_oid = ""
            if tr.spot_order and isinstance(tr.spot_order, list) and len(tr.spot_order) > 0:
                spot_oid = tr.spot_order[0].get("ordId", "")
            print(_c("║", CYAN) + f"  现货订单: {spot_status}  "
                  f"{'ordId: ' + spot_oid if spot_oid else '(无订单号)'}")

            # Swap order
            swap_status = _c("[OK]", GREEN) if tr.swap_order else _c("[FAIL]", RED)
            swap_oid = ""
            if tr.swap_order and isinstance(tr.swap_order, list) and len(tr.swap_order) > 0:
                swap_oid = tr.swap_order[0].get("ordId", "")
            print(_c("║", CYAN) + f"  合约订单: {swap_status}  "
                  f"{'ordId: ' + swap_oid if swap_oid else '(无订单号)'}")
        else:
            print(_c("║", CYAN) + _c("  (未执行交易)", DIM))

        # ── Income projection table ──────────────────────────────────────
        print(_c("╟" + "─" * w + "╢", DIM))
        print(_c("║", CYAN) + _c("  [四] 预期收益表", BOLD + YELLOW).ljust(w + len(BOLD + YELLOW + RESET) - 1) + _c("║", CYAN))
        print(_c("╟" + "─" * w + "╢", DIM))

        if projection:
            rows = [
                ("时间周期",    "预期收入 (USDT)",   "说明"),
                ("─" * 16,      "─" * 18,            "─" * 24),
                ("每 8 小时",   f"{projection['per_period']:.6f}", "每个资金费率周期"),
                ("每日 (3次)",  f"{projection['daily']:.6f}",      "3 个资金费率周期/天"),
                ("每周",        f"{projection['weekly']:.6f}",     "7 天累计"),
                ("每月 (30天)", f"{projection['monthly']:.6f}",    "30 天累计"),
                ("每年 (365天)",f"{projection['yearly']:.4f}",     "365 天累计"),
            ]
            for period, income, note in rows:
                print(_c("║", CYAN) + f"  {_c(period, WHITE):<22s}"
                      f" {_c(income, GREEN + BOLD):>28s}"
                      f"  {_c(note, DIM)}")

            print(_c("║", CYAN))
            apr_val = projection['apr']
            notional_val = projection['notional']
            print(_c("║", CYAN) + f"  {_c('年化收益率 (APR):', BOLD + WHITE)}  "
                  f"{_c(f'{apr_val:.2f}%', GREEN + BOLD)}")
            print(_c("║", CYAN) + f"  {_c('持仓名义价值:', DIM)}      "
                  f"{_c(f'{notional_val:,.2f} USDT', WHITE)}")
        else:
            print(_c("║", CYAN) + _c("  (无预测数据)", DIM))

        # ── Footer ───────────────────────────────────────────────────────
        print(_c("╟" + "─" * w + "╢", DIM))
        print(_c("║", CYAN) + _c("  [注意事项]", BOLD + RED).ljust(w + len(BOLD + RED + RESET) - 1) + _c("║", CYAN))
        print(_c("║", CYAN) + f"  {_c('1. 资金费率每 8 小时结算一次，费率可能变化', DIM)}")
        print(_c("║", CYAN) + f"  {_c('2. Delta 中性策略的价差风险来自现货/合约价格偏离', DIM)}")
        print(_c("║", CYAN) + f"  {_c('3. 本次执行使用模拟盘 (Demo)，无真实资金风险', DIM)}")
        print(_c("║", CYAN) + f"  {_c('4. 实际收益受滑点、手续费和基差变动影响', DIM)}")
        print(_c("╚" + "═" * w + "╝", CYAN + BOLD))
        print()

    # ── Main run ─────────────────────────────────────────────────────────

    def run(self):
        print_banner()

        try:
            # Step 1: Scan
            self.scan_funding_rates()

            # Step 2: Rank
            self.rank_opportunities()

            # Step 3: Select
            best = self.select_best()
            if not best:
                warn("没有可执行的套利机会，退出")
                return

            # Step 4: Execute
            self.execute_trades()

            # Step 5: Project P&L
            projection = self.project_pnl()

            # Step 6: Report
            self.print_report(projection)

        except KeyboardInterrupt:
            print(f"\n{YELLOW}用户中断执行{RESET}")
            sys.exit(130)
        except Exception as exc:
            fail(f"未预期的错误: {exc}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        print(_c("  资金费率收割机运行完毕 — 模拟盘 (Demo) 模式", CYAN + BOLD))
        print()


# Needed for the dashboard colour reference (not used in ANSI but keeps
# the f-string from raising a NameError when BG_GRN + BLACK is evaluated)
BLACK = "\033[30m"

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main():
    harvester = FundingHarvester()
    harvester.run()


if __name__ == "__main__":
    main()
