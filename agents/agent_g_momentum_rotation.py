#!/usr/bin/env python3
"""
动量轮动引擎 (Momentum Rotation Engine)
========================================
Cross-asset momentum ranking and auto-rotation AI Agent for OKX.

Scans 8 major coins across multiple timeframes, computes composite momentum
scores, ranks and classifies them, then automatically rotates portfolio out
of weak momentum assets and into strong momentum assets.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Optional

# ─── ANSI Colors ─────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
ULINE   = "\033[4m"

RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"

BG_RED    = "\033[41m"
BG_GREEN  = "\033[42m"
BG_YELLOW = "\033[43m"
BG_BLUE   = "\033[44m"
BG_MAGENTA = "\033[45m"
BG_CYAN   = "\033[46m"

# ─── Configuration ───────────────────────────────────────────────────────────

OKX_ENV = {
    **os.environ,
    "OKX_API_KEY":    os.environ.get("OKX_API_KEY", ""),
    "OKX_SECRET_KEY": os.environ.get("OKX_SECRET_KEY", ""),
    "OKX_PASSPHRASE": os.environ.get("OKX_PASSPHRASE", ""),
}

# Universe of coins to scan
UNIVERSE = [
    {"instId": "BTC-USDT",  "name": "Bitcoin",   "emoji": "BTC", "min_sz": "0.00001"},
    {"instId": "ETH-USDT",  "name": "Ethereum",  "emoji": "ETH", "min_sz": "0.0001"},
    {"instId": "SOL-USDT",  "name": "Solana",    "emoji": "SOL", "min_sz": "0.01"},
    {"instId": "OKB-USDT",  "name": "OKB",       "emoji": "OKB", "min_sz": "0.01"},
    {"instId": "DOGE-USDT", "name": "Dogecoin",  "emoji": "DOGE","min_sz": "1"},
    {"instId": "XRP-USDT",  "name": "XRP",       "emoji": "XRP", "min_sz": "1"},
    {"instId": "ADA-USDT",  "name": "Cardano",   "emoji": "ADA", "min_sz": "1"},
    {"instId": "AVAX-USDT", "name": "Avalanche", "emoji": "AVAX","min_sz": "0.01"},
]

# Momentum weights
W_7D   = 0.40   # short-term momentum
W_14D  = 0.35   # medium-term momentum
W_30D  = 0.15   # long-term momentum
W_ACCEL = 0.10  # acceleration (ROC of ROC)

# Classification thresholds (top 3 / middle 2 / bottom 3)
TOP_N    = 3
BOTTOM_N = 3

# Per-position allocation in USDT (demo)
POSITION_SIZE_USDT = 10.0

# DCA bot parameters
DCA_LEVER         = "2"
DCA_DIRECTION     = "long"
DCA_INIT_AMT      = "10"
DCA_MAX_SAFETY    = "2"
DCA_TP_PCT        = "3"
DCA_SAFETY_AMT    = "10"
DCA_PX_STEPS      = "1"
DCA_PX_STEPS_MULT = "1"
DCA_VOL_MULT      = "1"

# ─── CLI Helper ──────────────────────────────────────────────────────────────

def run_okx(*args: str, silent: bool = False) -> Any:
    """Execute an okx CLI command with --demo --json flags. Returns parsed JSON."""
    cmd = ["okx", "--demo", "--json"] + list(args)
    if not silent:
        display = " ".join(cmd)
        print(f"    {DIM}$ {display}{RESET}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=OKX_ENV,
            timeout=30,
        )
        stdout = result.stdout.strip()
        if not stdout:
            if result.returncode != 0:
                stderr_msg = result.stderr.strip()
                if not silent:
                    print(f"    {RED}[CLI错误] {stderr_msg}{RESET}")
            return None
        return json.loads(stdout)
    except json.JSONDecodeError:
        if not silent:
            print(f"    {RED}[JSON解析错误] {' '.join(args[:3])}{RESET}")
        return None
    except subprocess.TimeoutExpired:
        if not silent:
            print(f"    {RED}[超时] {' '.join(args[:3])}{RESET}")
        return None
    except FileNotFoundError:
        print(f"    {RED}[错误] okx CLI 未找到，请确保已安装{RESET}")
        sys.exit(1)


def run_okx_market(*args: str) -> Any:
    """Run a market data command (no --demo needed, but included for consistency)."""
    return run_okx(*args, silent=True)

# ─── Display Helpers ─────────────────────────────────────────────────────────

def print_banner():
    """Print the startup banner."""
    w = 66
    print()
    print(f"  {BOLD}{BG_MAGENTA}{WHITE}{'=' * w}{RESET}")
    print(f"  {BOLD}{BG_MAGENTA}{WHITE}{'':>8}动 量 轮 动 引 擎{'':>8}{RESET}")
    print(f"  {BOLD}{BG_MAGENTA}{WHITE}{'':>8}Momentum Rotation Engine  v1.0{'':>8}{RESET}")
    print(f"  {BOLD}{BG_MAGENTA}{WHITE}{'':>8}Cross-Asset Momentum Ranking & Auto-Rotation{'':>8}{RESET}")
    print(f"  {BOLD}{BG_MAGENTA}{WHITE}{'=' * w}{RESET}")
    print(f"  {DIM}  扫描 {len(UNIVERSE)} 个主流币种 | 多周期动量分析 | 自动轮动{RESET}")
    print()


def step_header(num: int, title_cn: str, title_en: str):
    """Print a numbered step header."""
    print(f"\n  {BOLD}{CYAN}{'━' * 62}{RESET}")
    print(f"  {BOLD}{CYAN}[步骤 {num}]{RESET} {BOLD}{WHITE}{title_cn}{RESET} {DIM}({title_en}){RESET}")
    print(f"  {BOLD}{CYAN}{'━' * 62}{RESET}")


def sub_step(text: str):
    print(f"  {MAGENTA}>>>{RESET} {text}")


def info(label: str, value: Any):
    print(f"    {BLUE}{label:<22}{RESET} {WHITE}{value}{RESET}")


def ok(text: str):
    print(f"    {GREEN}[OK] {text}{RESET}")


def warn(text: str):
    print(f"    {YELLOW}[!!] {text}{RESET}")


def fail(text: str):
    print(f"    {RED}[XX] {text}{RESET}")


def pct_color(pct: float) -> str:
    """Return colored percentage string."""
    if pct > 0:
        return f"{GREEN}+{pct:.2f}%{RESET}"
    elif pct < 0:
        return f"{RED}{pct:.2f}%{RESET}"
    else:
        return f"{DIM}0.00%{RESET}"


def score_bar(score: float, width: int = 20) -> str:
    """Render a horizontal bar for a momentum score [-1, +1] range approx."""
    clamped = max(-1.0, min(1.0, score))
    mid = width // 2
    bar_chars = [" "] * width

    if clamped >= 0:
        fill = round(clamped * mid)
        for i in range(mid, mid + fill):
            if i < width:
                bar_chars[i] = "\u2588"
        color = GREEN
    else:
        fill = round(abs(clamped) * mid)
        for i in range(mid - fill, mid):
            if 0 <= i < width:
                bar_chars[i] = "\u2588"
        color = RED

    bar_str = ""
    for i, ch in enumerate(bar_chars):
        if ch == "\u2588":
            bar_str += f"{color}{ch}{RESET}"
        elif i == mid:
            bar_str += f"{DIM}|{RESET}"
        else:
            bar_str += f"{DIM}\u2591{RESET}"

    return bar_str


def classification_badge(cls: str) -> str:
    """Return a colored badge for STRONG/NEUTRAL/WEAK."""
    if cls == "STRONG":
        return f"{BG_GREEN}{WHITE}{BOLD} STRONG {RESET}"
    elif cls == "WEAK":
        return f"{BG_RED}{WHITE}{BOLD}  WEAK  {RESET}"
    else:
        return f"{BG_YELLOW}{WHITE}{BOLD}NEUTRAL {RESET}"


# ─── Data Fetching ───────────────────────────────────────────────────────────

def fetch_daily_candles(inst_id: str, limit: int = 30) -> list[dict]:
    """Fetch 1D candles, return sorted oldest-first list of dicts."""
    raw = run_okx_market("market", "candles", inst_id, "--bar", "1D", "--limit", str(limit))
    if not raw or not isinstance(raw, list) or len(raw) == 0:
        return []
    candles = []
    for entry in raw:
        try:
            candles.append({
                "ts":    int(entry[0]),
                "open":  float(entry[1]),
                "high":  float(entry[2]),
                "low":   float(entry[3]),
                "close": float(entry[4]),
                "vol":   float(entry[5]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c["ts"])
    return candles


def fetch_4h_candles(inst_id: str, limit: int = 42) -> list[dict]:
    """Fetch 4H candles (42 bars ~ 7 days), return sorted oldest-first."""
    raw = run_okx_market("market", "candles", inst_id, "--bar", "4H", "--limit", str(limit))
    if not raw or not isinstance(raw, list) or len(raw) == 0:
        return []
    candles = []
    for entry in raw:
        try:
            candles.append({
                "ts":    int(entry[0]),
                "open":  float(entry[1]),
                "high":  float(entry[2]),
                "low":   float(entry[3]),
                "close": float(entry[4]),
                "vol":   float(entry[5]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c["ts"])
    return candles


def fetch_ticker(inst_id: str) -> Optional[dict]:
    """Fetch current ticker data."""
    raw = run_okx_market("market", "ticker", inst_id)
    if not raw:
        return None
    if isinstance(raw, list) and len(raw) > 0:
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return None


def fetch_balance() -> dict:
    """
    Fetch account balance. Returns dict mapping currency -> available amount.
    E.g. {"USDT": 1000.0, "BTC": 0.001}
    """
    raw = run_okx("account", "balance")
    balances = {}
    if not raw:
        return balances

    # OKX account balance response: list with one element containing "details"
    data = raw
    if isinstance(data, list) and len(data) > 0:
        data = data[0]

    details = data.get("details", []) if isinstance(data, dict) else []
    for item in details:
        ccy = item.get("ccy", "")
        avail = float(item.get("availBal", 0) or 0)
        if ccy and avail > 0:
            balances[ccy] = avail
    return balances


# ─── Momentum Calculation ────────────────────────────────────────────────────

def calc_return(candles: list[dict], days: int) -> Optional[float]:
    """
    Calculate the return over the last N days from daily candles.
    Returns percentage (e.g. 5.2 for +5.2%).
    """
    if len(candles) < days + 1:
        return None
    current_close = candles[-1]["close"]
    past_close = candles[-(days + 1)]["close"]
    if past_close == 0:
        return None
    return ((current_close - past_close) / past_close) * 100.0


def calc_volume_trend(candles: list[dict], window: int = 7) -> float:
    """
    Compute volume trend as ratio of recent average volume to older average.
    Returns value > 1.0 if volume is increasing, < 1.0 if decreasing.
    """
    if len(candles) < window * 2:
        return 1.0
    recent_vols = [c["vol"] for c in candles[-window:]]
    older_vols = [c["vol"] for c in candles[-(window * 2):-window]]
    avg_recent = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
    avg_older = sum(older_vols) / len(older_vols) if older_vols else 1.0
    if avg_older == 0:
        return 1.0
    return avg_recent / avg_older


def calc_acceleration(candles: list[dict]) -> float:
    """
    Rate of change acceleration: difference between recent 7d return and
    the prior 7d return. Positive means momentum is increasing.
    """
    if len(candles) < 15:
        return 0.0
    # Recent 7-day return
    cur = candles[-1]["close"]
    mid = candles[-8]["close"]  # 7 days ago
    old = candles[-15]["close"]  # 14 days ago

    if mid == 0 or old == 0:
        return 0.0

    recent_ret = (cur - mid) / mid
    prior_ret = (mid - old) / old
    return (recent_ret - prior_ret) * 100.0


def compute_momentum(daily_candles: list[dict], candles_4h: list[dict]) -> dict:
    """
    Compute composite momentum score and all sub-metrics.
    Returns dict with all computed values.
    """
    ret_7d = calc_return(daily_candles, 7)
    ret_14d = calc_return(daily_candles, 14)
    ret_30d = calc_return(daily_candles, 30)

    acceleration = calc_acceleration(daily_candles)
    vol_trend = calc_volume_trend(daily_candles, 7)

    # Normalize returns to a comparable scale for composite
    # Use raw percentages but cap extreme values
    def cap(val, limit=50.0):
        if val is None:
            return 0.0
        return max(-limit, min(limit, val))

    r7  = cap(ret_7d)
    r14 = cap(ret_14d)
    r30 = cap(ret_30d)
    acc = cap(acceleration, 30.0)

    # Volume confirmation: boost or dampen score
    vol_factor = 1.0
    if vol_trend > 1.2:
        vol_factor = 1.1  # volume confirming, slight boost
    elif vol_trend < 0.8:
        vol_factor = 0.9  # volume declining, slight dampen

    composite = (W_7D * r7 + W_14D * r14 + W_30D * r30 + W_ACCEL * acc) * vol_factor

    return {
        "ret_7d":       ret_7d,
        "ret_14d":      ret_14d,
        "ret_30d":      ret_30d,
        "acceleration": acceleration,
        "vol_trend":    vol_trend,
        "vol_factor":   vol_factor,
        "composite":    composite,
    }


# ─── Universe Scanning ───────────────────────────────────────────────────────

def scan_universe() -> list[dict]:
    """
    Scan all coins in UNIVERSE: fetch candles & ticker, compute momentum.
    Returns list of dicts with coin info + momentum data.
    """
    results = []
    total = len(UNIVERSE)

    for idx, coin in enumerate(UNIVERSE, 1):
        inst_id = coin["instId"]
        name = coin["name"]
        print(f"\n    {CYAN}[{idx}/{total}]{RESET} {BOLD}{inst_id}{RESET} ({name})")

        # Fetch daily candles
        print(f"      {DIM}获取日线数据 (1D x 30)...{RESET}", end="", flush=True)
        daily = fetch_daily_candles(inst_id, limit=30)
        print(f" {GREEN}{len(daily)} 根K线{RESET}")

        # Fetch 4H candles
        print(f"      {DIM}获取4小时数据 (4H x 42)...{RESET}", end="", flush=True)
        candles_4h = fetch_4h_candles(inst_id, limit=42)
        print(f" {GREEN}{len(candles_4h)} 根K线{RESET}")

        # Fetch ticker
        print(f"      {DIM}获取实时价格...{RESET}", end="", flush=True)
        ticker = fetch_ticker(inst_id)
        current_price = 0.0
        if ticker:
            current_price = float(ticker.get("last", 0) or 0)
        print(f" {GREEN}${current_price:,.4f}{RESET}")

        # Compute momentum
        if len(daily) >= 8:
            momentum = compute_momentum(daily, candles_4h)
        else:
            warn(f"数据不足，跳过 {inst_id}")
            momentum = {
                "ret_7d": None, "ret_14d": None, "ret_30d": None,
                "acceleration": 0.0, "vol_trend": 1.0, "vol_factor": 1.0,
                "composite": 0.0,
            }

        results.append({
            **coin,
            "price": current_price,
            "daily_candles": daily,
            "candles_4h": candles_4h,
            **momentum,
        })

        # Brief pause to avoid rate limiting
        time.sleep(0.3)

    return results


# ─── Ranking & Classification ────────────────────────────────────────────────

def rank_and_classify(coins: list[dict]) -> list[dict]:
    """
    Sort coins by composite momentum score descending.
    Assign classification: STRONG (top 3), NEUTRAL (middle 2), WEAK (bottom 3).
    """
    ranked = sorted(coins, key=lambda c: c["composite"], reverse=True)

    for i, coin in enumerate(ranked):
        coin["rank"] = i + 1
        if i < TOP_N:
            coin["classification"] = "STRONG"
        elif i >= len(ranked) - BOTTOM_N:
            coin["classification"] = "WEAK"
        else:
            coin["classification"] = "NEUTRAL"

    return ranked


# ─── Portfolio Operations ────────────────────────────────────────────────────

def get_holdings(balances: dict) -> dict:
    """
    Map coin instIds to held quantities from balance.
    E.g. "BTC-USDT" -> check if we hold BTC.
    """
    holdings = {}
    for coin in UNIVERSE:
        ccy = coin["instId"].split("-")[0]
        held = balances.get(ccy, 0.0)
        if held > 0:
            holdings[coin["instId"]] = held
    return holdings


def sell_coin(inst_id: str, amount: float, min_sz: str) -> bool:
    """Place a market sell order. Returns True on success."""
    # Round amount to appropriate precision based on min_sz
    decimals = len(min_sz.split(".")[-1]) if "." in min_sz else 0
    sz = f"{amount:.{decimals}f}"

    print(f"      {YELLOW}卖出{RESET} {inst_id} 数量: {sz}")
    result = run_okx(
        "spot", "place",
        "--instId", inst_id,
        "--side", "sell",
        "--ordType", "market",
        "--sz", sz,
        "--tdMode", "cash",
    )
    if result:
        ord_id = ""
        if isinstance(result, list) and len(result) > 0:
            ord_id = result[0].get("ordId", "")
        elif isinstance(result, dict):
            ord_id = result.get("ordId", "")
        if ord_id:
            ok(f"卖出订单成功 ordId={ord_id}")
            return True
    fail(f"卖出 {inst_id} 失败")
    return False


def buy_coin(inst_id: str, usdt_amount: float, price: float, min_sz: str) -> bool:
    """Place a market buy order using USDT amount. Returns True on success."""
    if price <= 0:
        fail(f"价格无效，跳过买入 {inst_id}")
        return False

    # Calculate quantity from USDT amount
    qty = usdt_amount / price
    # Round to min_sz precision
    decimals = len(min_sz.split(".")[-1]) if "." in min_sz else 0
    sz = f"{qty:.{decimals}f}"

    if float(sz) <= 0:
        fail(f"计算数量为零，跳过买入 {inst_id}")
        return False

    print(f"      {GREEN}买入{RESET} {inst_id} 数量: {sz} (~{usdt_amount:.1f} USDT)")
    result = run_okx(
        "spot", "place",
        "--instId", inst_id,
        "--side", "buy",
        "--ordType", "market",
        "--sz", sz,
        "--tdMode", "cash",
    )
    if result:
        ord_id = ""
        if isinstance(result, list) and len(result) > 0:
            ord_id = result[0].get("ordId", "")
        elif isinstance(result, dict):
            ord_id = result.get("ordId", "")
        if ord_id:
            ok(f"买入订单成功 ordId={ord_id}")
            return True
    fail(f"买入 {inst_id} 失败")
    return False


def create_dca_bot(inst_id: str) -> bool:
    """Create a DCA bot for the given instrument (SWAP). Returns True on success."""
    swap_id = inst_id.replace("-USDT", "-USDT-SWAP")
    print(f"      {CYAN}创建DCA机器人{RESET} {swap_id}")
    result = run_okx(
        "bot", "dca", "create",
        "--instId", swap_id,
        "--lever", DCA_LEVER,
        "--direction", DCA_DIRECTION,
        "--initOrdAmt", DCA_INIT_AMT,
        "--maxSafetyOrds", DCA_MAX_SAFETY,
        "--tpPct", DCA_TP_PCT,
        "--safetyOrdAmt", DCA_SAFETY_AMT,
        "--pxSteps", DCA_PX_STEPS,
        "--pxStepsMult", DCA_PX_STEPS_MULT,
        "--volMult", DCA_VOL_MULT,
    )
    if result:
        algo_id = ""
        if isinstance(result, list) and len(result) > 0:
            algo_id = result[0].get("algoId", "")
        elif isinstance(result, dict):
            algo_id = result.get("algoId", "")
        if algo_id:
            ok(f"DCA机器人创建成功 algoId={algo_id}")
            return True
    warn(f"DCA机器人创建失败 (可能不支持该币种)")
    return False


# ─── Rotation Logic ─────────────────────────────────────────────────────────

def execute_rotation(ranked: list[dict], balances_before: dict) -> list[dict]:
    """
    Execute the rotation strategy:
    1. Sell any WEAK holdings
    2. Buy TOP coins with freed capital
    Returns list of action records.
    """
    actions = []
    holdings = get_holdings(balances_before)
    freed_usdt = 0.0

    # --- Phase 1: Sell WEAK holdings ---
    sub_step(f"{RED}Phase 1: 卖出弱势币种{RESET}")
    weak_coins = [c for c in ranked if c["classification"] == "WEAK"]
    sold_any = False

    for coin in weak_coins:
        inst_id = coin["instId"]
        if inst_id in holdings and holdings[inst_id] > 0:
            amount = holdings[inst_id]
            estimated_value = amount * coin["price"] if coin["price"] > 0 else 0
            success = sell_coin(inst_id, amount, coin["min_sz"])
            if success:
                freed_usdt += estimated_value
                actions.append({
                    "action": "SELL",
                    "instId": inst_id,
                    "amount": amount,
                    "value_usdt": estimated_value,
                    "reason": f"弱势排名 #{coin['rank']}",
                })
                sold_any = True
        else:
            print(f"      {DIM}未持有 {inst_id}，无需卖出{RESET}")

    if not sold_any:
        print(f"      {DIM}无弱势持仓需要卖出{RESET}")

    # --- Phase 2: Buy STRONG coins ---
    sub_step(f"{GREEN}Phase 2: 买入强势币种{RESET}")
    strong_coins = [c for c in ranked if c["classification"] == "STRONG"]

    # Determine available USDT: freed capital + existing USDT
    available_usdt = balances_before.get("USDT", 0.0) + freed_usdt

    # Allocate equally to top coins, capped at POSITION_SIZE_USDT each
    per_coin = min(POSITION_SIZE_USDT, available_usdt / max(len(strong_coins), 1))

    if per_coin < 1.0:
        warn(f"可用USDT不足 ({available_usdt:.2f} USDT)，无法建仓")
    else:
        for coin in strong_coins:
            inst_id = coin["instId"]
            # Check if already holding
            ccy = inst_id.split("-")[0]
            existing = balances_before.get(ccy, 0.0)
            existing_value = existing * coin["price"] if coin["price"] > 0 else 0

            if existing_value >= POSITION_SIZE_USDT * 0.8:
                print(f"      {DIM}已持有 {inst_id} (价值 ~{existing_value:.1f} USDT)，跳过{RESET}")
                continue

            success = buy_coin(inst_id, per_coin, coin["price"], coin["min_sz"])
            if success:
                actions.append({
                    "action": "BUY",
                    "instId": inst_id,
                    "amount": per_coin / coin["price"] if coin["price"] > 0 else 0,
                    "value_usdt": per_coin,
                    "reason": f"强势排名 #{coin['rank']}",
                })

    return actions


# ─── DCA Integration ─────────────────────────────────────────────────────────

def setup_dca_bots(ranked: list[dict]) -> list[dict]:
    """Create DCA bots for all STRONG coins."""
    dca_actions = []
    strong = [c for c in ranked if c["classification"] == "STRONG"]

    for coin in strong:
        success = create_dca_bot(coin["instId"])
        dca_actions.append({
            "instId": coin["instId"],
            "success": success,
        })
        time.sleep(0.5)

    return dca_actions


# ─── Report / Dashboard ─────────────────────────────────────────────────────

def print_leaderboard(ranked: list[dict]):
    """Print the full momentum leaderboard."""
    w = 90
    print(f"\n  {BOLD}{BG_CYAN}{WHITE}{'=' * w}{RESET}")
    title = "动量排行榜 / MOMENTUM LEADERBOARD"
    print(f"  {BOLD}{BG_CYAN}{WHITE}{title:^{w}}{RESET}")
    print(f"  {BOLD}{BG_CYAN}{WHITE}{'=' * w}{RESET}\n")

    # Header row
    print(f"  {BOLD}{ULINE}"
          f"{'排名':>4}  "
          f"{'币种':<12}  "
          f"{'价格':>12}  "
          f"{'7D收益':>9}  "
          f"{'14D收益':>9}  "
          f"{'30D收益':>9}  "
          f"{'动量':>8}  "
          f"{'分类':>10}  "
          f"{'图示':<20}"
          f"{RESET}")

    for coin in ranked:
        rank = coin["rank"]
        inst = coin["instId"].replace("-USDT", "")
        price = coin["price"]
        ret7 = coin.get("ret_7d")
        ret14 = coin.get("ret_14d")
        ret30 = coin.get("ret_30d")
        comp = coin["composite"]
        cls = coin["classification"]

        # Format price
        if price >= 1000:
            price_str = f"${price:>10,.2f}"
        elif price >= 1:
            price_str = f"${price:>10,.4f}"
        else:
            price_str = f"${price:>10,.6f}"

        # Rank decoration
        if rank == 1:
            rank_str = f"{YELLOW}{BOLD} #1 {RESET}"
        elif rank == 2:
            rank_str = f"{WHITE}{BOLD} #2 {RESET}"
        elif rank == 3:
            rank_str = f"{CYAN}{BOLD} #3 {RESET}"
        else:
            rank_str = f"{DIM} #{rank} {RESET}"

        r7s  = pct_color(ret7)  if ret7  is not None else f"{DIM}  N/A  {RESET}"
        r14s = pct_color(ret14) if ret14 is not None else f"{DIM}  N/A  {RESET}"
        r30s = pct_color(ret30) if ret30 is not None else f"{DIM}  N/A  {RESET}"

        # Composite score color
        if comp > 0:
            comp_str = f"{GREEN}{BOLD}{comp:>+7.2f}{RESET}"
        elif comp < 0:
            comp_str = f"{RED}{BOLD}{comp:>+7.2f}{RESET}"
        else:
            comp_str = f"{DIM}{comp:>+7.2f}{RESET}"

        badge = classification_badge(cls)
        bar = score_bar(comp / 30.0)  # normalize for display

        print(f"  {rank_str}  "
              f"{BOLD}{inst:<10}{RESET}  "
              f"{price_str}  "
              f"{r7s:>18}  "
              f"{r14s:>18}  "
              f"{r30s:>18}  "
              f"{comp_str}  "
              f"{badge}  "
              f"{bar}")

    print()


def print_details_table(ranked: list[dict]):
    """Print detailed momentum metrics."""
    print(f"  {BOLD}{MAGENTA}>>> 详细动量指标{RESET}")
    print(f"  {MAGENTA}{'─' * 75}{RESET}\n")

    for coin in ranked:
        inst = coin["instId"]
        cls = coin["classification"]
        badge = classification_badge(cls)
        vol_trend = coin.get("vol_trend", 1.0)
        accel = coin.get("acceleration", 0.0)
        vol_factor = coin.get("vol_factor", 1.0)

        vol_dir = "上升" if vol_trend > 1.05 else ("下降" if vol_trend < 0.95 else "平稳")
        vol_color = GREEN if vol_trend > 1.05 else (RED if vol_trend < 0.95 else YELLOW)

        accel_dir = "加速" if accel > 0.5 else ("减速" if accel < -0.5 else "稳定")
        accel_color = GREEN if accel > 0.5 else (RED if accel < -0.5 else YELLOW)

        print(f"    {BOLD}{inst:<12}{RESET} {badge} "
              f" 成交量趋势: {vol_color}{vol_trend:.2f}x ({vol_dir}){RESET}"
              f" | 动量加速度: {accel_color}{accel:>+.2f}% ({accel_dir}){RESET}"
              f" | 量价因子: {DIM}{vol_factor:.2f}{RESET}")

    print()


def print_rotation_report(actions: list[dict], dca_actions: list[dict]):
    """Print rotation actions taken."""
    w = 70
    print(f"\n  {BOLD}{BG_YELLOW}{WHITE}{'=' * w}{RESET}")
    title = "轮动操作报告 / ROTATION ACTIONS"
    print(f"  {BOLD}{BG_YELLOW}{WHITE}{title:^{w}}{RESET}")
    print(f"  {BOLD}{BG_YELLOW}{WHITE}{'=' * w}{RESET}\n")

    if not actions:
        print(f"    {DIM}本次无轮动操作{RESET}\n")
    else:
        for act in actions:
            if act["action"] == "SELL":
                icon = f"{RED}[卖出]{RESET}"
            else:
                icon = f"{GREEN}[买入]{RESET}"
            print(f"    {icon} {BOLD}{act['instId']:<12}{RESET} "
                  f"~{act['value_usdt']:.1f} USDT  "
                  f"原因: {act['reason']}")
        print()

    # DCA bots
    if dca_actions:
        print(f"  {BOLD}{CYAN}>>> DCA机器人状态:{RESET}")
        for da in dca_actions:
            status = f"{GREEN}[已创建]{RESET}" if da["success"] else f"{RED}[失败]{RESET}"
            print(f"    {status} {da['instId']}-SWAP")
        print()


def print_portfolio_comparison(before: dict, after: dict):
    """Print before/after portfolio comparison."""
    w = 65
    print(f"\n  {BOLD}{BG_GREEN}{WHITE}{'=' * w}{RESET}")
    title = "投资组合对比 / PORTFOLIO COMPARISON"
    print(f"  {BOLD}{BG_GREEN}{WHITE}{title:^{w}}{RESET}")
    print(f"  {BOLD}{BG_GREEN}{WHITE}{'=' * w}{RESET}\n")

    # Collect all currencies
    all_ccy = sorted(set(list(before.keys()) + list(after.keys())))

    print(f"    {BOLD}{ULINE}"
          f"{'币种':<10} {'操作前':>15} {'操作后':>15} {'变化':>15}{RESET}")

    for ccy in all_ccy:
        b = before.get(ccy, 0.0)
        a = after.get(ccy, 0.0)
        diff = a - b

        if abs(diff) < 1e-10:
            diff_str = f"{DIM}    ---{RESET}"
        elif diff > 0:
            diff_str = f"{GREEN}+{diff:.6f}{RESET}"
        else:
            diff_str = f"{RED}{diff:.6f}{RESET}"

        print(f"    {BOLD}{ccy:<10}{RESET} {b:>15.6f} {a:>15.6f} {diff_str}")

    print()


# ─── Main Execution ─────────────────────────────────────────────────────────

def main():
    start_time = time.time()
    print_banner()

    # Validate environment
    missing_vars = []
    for var in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"):
        if not os.environ.get(var):
            missing_vars.append(var)
    if missing_vars:
        fail(f"缺少环境变量: {', '.join(missing_vars)}")
        warn("请设置 OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE")
        sys.exit(1)

    # ── Step 1: Universe Scanning ──
    step_header(1, "全域扫描", "Universe Scanning")
    print(f"    {DIM}扫描 {len(UNIVERSE)} 个币种，获取多周期K线数据...{RESET}")
    coins = scan_universe()
    ok(f"扫描完成: {len(coins)} 个币种数据已获取")

    # ── Step 2: Momentum Calculation & Ranking ──
    step_header(2, "多周期动量计算与排名", "Multi-Period Momentum Calculation & Ranking")

    ranked = rank_and_classify(coins)
    ok(f"排名完成")

    # Print quick summary
    for coin in ranked:
        comp = coin["composite"]
        cls = coin["classification"]
        badge = classification_badge(cls)
        inst = coin["instId"]
        print(f"    #{coin['rank']}  {BOLD}{inst:<12}{RESET}  "
              f"动量分: {comp:>+8.2f}  {badge}")

    # ── Step 3: Detailed Report ──
    step_header(3, "动量排行榜", "Momentum Leaderboard Dashboard")
    print_leaderboard(ranked)
    print_details_table(ranked)

    # ── Step 4: Portfolio Check & Rotation ──
    step_header(4, "轮动执行", "Rotation Execution")

    sub_step("获取当前账户余额...")
    balances_before = fetch_balance()
    if balances_before:
        ok(f"账户包含 {len(balances_before)} 种资产")
        for ccy, amt in sorted(balances_before.items()):
            info(f"  {ccy}", f"{amt:.6f}")
    else:
        warn("账户余额为空或获取失败")

    # Execute rotation
    sub_step("执行轮动策略...")
    actions = execute_rotation(ranked, balances_before)
    ok(f"轮动操作完成: {len(actions)} 笔交易")

    # ── Step 5: DCA Bots ──
    step_header(5, "DCA机器人部署", "DCA Bot Integration")
    sub_step("为强势币种创建DCA机器人...")
    dca_actions = setup_dca_bots(ranked)

    # ── Step 6: Final Report ──
    step_header(6, "综合报告", "Final Report")

    # Fetch updated balance
    sub_step("获取更新后的账户余额...")
    balances_after = fetch_balance()
    if not balances_after:
        balances_after = balances_before.copy()

    # Print all reports
    print_rotation_report(actions, dca_actions)
    print_portfolio_comparison(balances_before, balances_after)

    # Final summary
    elapsed = time.time() - start_time
    strong = [c for c in ranked if c["classification"] == "STRONG"]
    weak = [c for c in ranked if c["classification"] == "WEAK"]

    print(f"  {BOLD}{BG_BLUE}{WHITE}{'=' * 62}{RESET}")
    print(f"  {BOLD}{BG_BLUE}{WHITE}{'  任务完成  /  MISSION COMPLETE':^62}{RESET}")
    print(f"  {BOLD}{BG_BLUE}{WHITE}{'=' * 62}{RESET}\n")

    info("运行时间",        f"{elapsed:.1f} 秒")
    info("扫描币种",        f"{len(UNIVERSE)} 个")
    info("强势币种",        ", ".join(c["instId"] for c in strong))
    info("弱势币种",        ", ".join(c["instId"] for c in weak))
    info("执行交易",        f"{len(actions)} 笔")
    info("DCA机器人",       f"{sum(1 for d in dca_actions if d['success'])} / {len(dca_actions)} 创建成功")

    # Top momentum coin highlight
    if ranked:
        top = ranked[0]
        print(f"\n    {BOLD}{YELLOW}{'*' * 40}{RESET}")
        print(f"    {BOLD}{YELLOW}  当前最强动量: {top['instId']}{RESET}")
        print(f"    {BOLD}{YELLOW}  动量分数: {top['composite']:+.2f}{RESET}")
        r7 = top.get('ret_7d')
        if r7 is not None:
            print(f"    {BOLD}{YELLOW}  7日收益: {r7:+.2f}%{RESET}")
        print(f"    {BOLD}{YELLOW}{'*' * 40}{RESET}")

    print(f"\n  {DIM}动量轮动引擎执行完毕 | Momentum Rotation Engine finished{RESET}\n")


if __name__ == "__main__":
    main()
