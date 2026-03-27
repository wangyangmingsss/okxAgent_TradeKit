#!/usr/bin/env python3
"""
闪崩猎手 (Flash Crash Hunter) — Crash Detection & Auto-Dip-Buy AI Agent for OKX

Monitors BTC-USDT, ETH-USDT, SOL-USDT in real time for flash crash signals.
Calculates a multi-factor Crash Score (0-100) combining drawdown, orderbook
imbalance, volume spikes, and drop speed. Automatically places dip-buy orders
with stop-loss and take-profit protection when crash thresholds are breached.

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
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

OKX_ENV = {
    **os.environ,
    "OKX_API_KEY": os.environ.get("OKX_API_KEY", ""),
    "OKX_SECRET_KEY": os.environ.get("OKX_SECRET_KEY", ""),
    "OKX_PASSPHRASE": os.environ.get("OKX_PASSPHRASE", ""),
}

MONITORED_PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

# Trade sizes per instrument (conservative demo amounts)
TRADE_SIZES = {
    "BTC-USDT": "0.0005",
    "ETH-USDT": "0.005",
    "SOL-USDT": "0.5",
}

# Crash detection thresholds
DRAWDOWN_THRESHOLD = 0.02      # 2% drop in 1h triggers max drawdown score
IMBALANCE_THRESHOLD = 0.5      # bid/ask ratio below 0.5 is bearish
VOLUME_SPIKE_THRESHOLD = 3.0   # 3x average volume
SPEED_THRESHOLD = 0.01         # 1% drop in 5 minutes

# Action thresholds
STRONG_BUY_SCORE = 70
MODERATE_BUY_SCORE = 50

# Risk management
STOP_LOSS_PCT = 0.03           # -3% stop loss
TAKE_PROFIT_PCT = 0.05         # +5% take profit
LIMIT_DISCOUNT_PCT = 0.01      # -1% below current for limit orders

# ─────────────────────────────────────────────────────────────────────────────
# Terminal colours & drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

class C:
    """ANSI colour shortcuts."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BG_RED  = "\033[41m"
    BG_GRN  = "\033[42m"
    BG_YEL  = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAG  = "\033[45m"


def _cw(text: str, colour: str) -> str:
    return f"{colour}{text}{C.RESET}"


def banner() -> None:
    w = 68
    print()
    print(_cw("╔" + "═" * w + "╗", C.RED))
    print(_cw("║", C.RED) + _cw("                  闪  崩  猎  手                              ", C.BOLD + C.WHITE) + _cw("║", C.RED))
    print(_cw("║", C.RED) + _cw("              Flash Crash Hunter v1.0                         ", C.BOLD + C.CYAN) + _cw("║", C.RED))
    print(_cw("║", C.RED) + _cw("       Crash Detection & Auto-Dip-Buy Agent for OKX          ", C.DIM + C.WHITE) + _cw("║", C.RED))
    print(_cw("╚" + "═" * w + "╝", C.RED))
    print()


def section(title: str) -> None:
    print()
    print(_cw(f"  ┌── {title} ", C.MAGENTA) + _cw("─" * max(0, 58 - len(title)), C.DIM))


def kv(key: str, value: str, indent: int = 4) -> None:
    pad = " " * indent
    print(f"{pad}{_cw('│', C.DIM)} {_cw(key + ':', C.CYAN)} {value}")


def success(text: str) -> None:
    print(f"    {_cw('│', C.DIM)} {_cw('[OK] ' + text, C.GREEN)}")


def warn(text: str) -> None:
    print(f"    {_cw('│', C.DIM)} {_cw('[!] ' + text, C.YELLOW)}")


def error(text: str) -> None:
    print(f"    {_cw('│', C.DIM)} {_cw('[X] ' + text, C.RED)}")


# ─────────────────────────────────────────────────────────────────────────────
# OKX CLI wrapper
# ─────────────────────────────────────────────────────────────────────────────

def okx_cmd(args: list[str], timeout: int = 30) -> Any:
    """Run an okx CLI command with --demo --json and return parsed JSON."""
    cmd = ["okx", "--demo", "--json"] + args
    display = " ".join(cmd)
    print(f"    {_cw('│', C.DIM)} {_cw('$ ' + display, C.DIM)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=OKX_ENV, timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            error(f"CLI 错误 ({' '.join(args[:3])}): {stderr or stdout}")
            return None
        if not stdout:
            return []
        return json.loads(stdout)
    except subprocess.TimeoutExpired:
        error(f"CLI 超时 ({' '.join(args[:3])})")
        return None
    except json.JSONDecodeError as e:
        error(f"JSON 解析失败 ({' '.join(args[:3])}): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TickerData:
    inst_id: str
    last: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    vol_24h: float = 0.0
    vol_ccy_24h: float = 0.0
    open_24h: float = 0.0
    change_24h_pct: float = 0.0


@dataclass
class OrderbookSnapshot:
    bids: list = field(default_factory=list)  # [(price, qty), ...]
    asks: list = field(default_factory=list)
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    imbalance_ratio: float = 1.0
    spread_pct: float = 0.0


@dataclass
class CandleBar:
    ts: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    vol: float = 0.0


@dataclass
class CrashMetrics:
    drawdown_pct: float = 0.0
    imbalance_ratio: float = 1.0
    volume_multiple: float = 1.0
    speed_drop_pct: float = 0.0
    drawdown_score: float = 0.0
    imbalance_score: float = 0.0
    volume_score: float = 0.0
    speed_score: float = 0.0
    total_score: float = 0.0
    signal: str = "MONITOR"
    signal_cn: str = "监控中"


@dataclass
class TradeAction:
    inst_id: str
    action: str           # "STRONG_BUY", "MODERATE_BUY", "MONITOR"
    action_cn: str
    order_type: str       # "market", "limit", "none"
    entry_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    size: str = ""
    order_id: str = ""
    sl_algo_id: str = ""
    tp_algo_id: str = ""
    success: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Market data fetching
# ─────────────────────────────────────────────────────────────────────────────

def fetch_ticker(inst_id: str) -> Optional[TickerData]:
    """Fetch ticker data for an instrument."""
    data = okx_cmd(["market", "ticker", inst_id])
    if not data:
        return None
    rec = data[0] if isinstance(data, list) else data
    try:
        last = float(rec.get("last", 0))
        open_24h = float(rec.get("open24h", 0) or rec.get("sodUtc0", 0))
        high = float(rec.get("high24h", 0))
        low = float(rec.get("low24h", 0))
        vol = float(rec.get("vol24h", 0))
        vol_ccy = float(rec.get("volCcy24h", 0))
        change_pct = ((last - open_24h) / open_24h * 100) if open_24h else 0.0
        return TickerData(
            inst_id=inst_id, last=last, high_24h=high, low_24h=low,
            vol_24h=vol, vol_ccy_24h=vol_ccy, open_24h=open_24h,
            change_24h_pct=change_pct,
        )
    except (KeyError, ValueError, TypeError) as e:
        error(f"解析行情数据失败 ({inst_id}): {e}")
        return None


def fetch_orderbook(inst_id: str) -> Optional[OrderbookSnapshot]:
    """Fetch orderbook depth."""
    data = okx_cmd(["market", "orderbook", inst_id, "--sz", "20"])
    if not data:
        return None
    rec = data[0] if isinstance(data, list) else data
    try:
        bids_raw = rec.get("bids", [])
        asks_raw = rec.get("asks", [])
        bids = [(float(b[0]), float(b[1])) for b in bids_raw if len(b) >= 2]
        asks = [(float(a[0]), float(a[1])) for a in asks_raw if len(a) >= 2]
        bid_depth = sum(p * q for p, q in bids)
        ask_depth = sum(p * q for p, q in asks)
        imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 999.0
        spread_pct = 0.0
        if bids and asks:
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            mid = (best_bid + best_ask) / 2
            spread_pct = ((best_ask - best_bid) / mid * 100) if mid > 0 else 0
        return OrderbookSnapshot(
            bids=bids, asks=asks, bid_depth=bid_depth, ask_depth=ask_depth,
            imbalance_ratio=imbalance, spread_pct=spread_pct,
        )
    except (KeyError, ValueError, TypeError) as e:
        error(f"解析订单簿失败 ({inst_id}): {e}")
        return None


def fetch_recent_trades(inst_id: str) -> Optional[list[dict]]:
    """Fetch recent trades."""
    data = okx_cmd(["market", "trades", inst_id, "--limit", "50"])
    if not data or not isinstance(data, list):
        return None
    parsed = []
    for t in data:
        try:
            parsed.append({
                "px": float(t.get("px", 0)),
                "sz": float(t.get("sz", 0)),
                "side": t.get("side", ""),
                "ts": float(t.get("ts", 0)),
            })
        except (ValueError, TypeError):
            continue
    return parsed if parsed else None


def fetch_candles(inst_id: str) -> Optional[list[CandleBar]]:
    """Fetch 5-minute candles for the last hour (12 bars)."""
    data = okx_cmd(["market", "candles", inst_id, "--bar", "5m", "--limit", "12"])
    if not data or not isinstance(data, list):
        return None
    candles = []
    for c in data:
        try:
            if isinstance(c, list) and len(c) >= 6:
                candles.append(CandleBar(
                    ts=float(c[0]), open=float(c[1]), high=float(c[2]),
                    low=float(c[3]), close=float(c[4]), vol=float(c[5]),
                ))
            elif isinstance(c, dict):
                candles.append(CandleBar(
                    ts=float(c.get("ts", 0)), open=float(c.get("o", 0)),
                    high=float(c.get("h", 0)), low=float(c.get("l", 0)),
                    close=float(c.get("c", 0)), vol=float(c.get("vol", 0)),
                ))
        except (ValueError, TypeError):
            continue
    # Sort by timestamp ascending (oldest first)
    candles.sort(key=lambda x: x.ts)
    return candles if candles else None


# ─────────────────────────────────────────────────────────────────────────────
# Crash detection algorithm
# ─────────────────────────────────────────────────────────────────────────────

def compute_crash_metrics(
    ticker: TickerData,
    orderbook: OrderbookSnapshot,
    candles: list[CandleBar],
    trades: Optional[list[dict]],
) -> CrashMetrics:
    """
    Compute the multi-factor Crash Score (0-100).

    Components:
      - Drawdown (max 40): price drop from recent 1h high
      - Orderbook imbalance (max 20): bid/ask depth ratio
      - Volume spike (max 20): current vol vs rolling average
      - Drop speed (max 20): 5-min price change rate
    """
    m = CrashMetrics()

    # ── 1. Drawdown from recent high ────────────────────────────────────────
    if candles:
        recent_high = max(c.high for c in candles)
        current = candles[-1].close
        if recent_high > 0:
            m.drawdown_pct = (recent_high - current) / recent_high
    # Scale: 0% → 0 points, ≥2% → 40 points (linear clamp)
    m.drawdown_score = min(40.0, (m.drawdown_pct / DRAWDOWN_THRESHOLD) * 40.0)

    # ── 2. Orderbook imbalance ──────────────────────────────────────────────
    m.imbalance_ratio = orderbook.imbalance_ratio
    # Scale: ratio ≥ 0.5 → 0, ratio ≤ 0.0 → 20 (inverted linear)
    if m.imbalance_ratio < IMBALANCE_THRESHOLD:
        raw = (IMBALANCE_THRESHOLD - m.imbalance_ratio) / IMBALANCE_THRESHOLD
        m.imbalance_score = min(20.0, raw * 20.0)
    # Extra penalty if ratio < 0.3 (sells overwhelming buys)
    if m.imbalance_ratio < 0.3:
        m.imbalance_score = 20.0

    # ── 3. Volume spike ─────────────────────────────────────────────────────
    if candles and len(candles) >= 3:
        avg_vol = sum(c.vol for c in candles[:-1]) / max(1, len(candles) - 1)
        latest_vol = candles[-1].vol
        m.volume_multiple = (latest_vol / avg_vol) if avg_vol > 0 else 1.0
    # Scale: ≤1x → 0, ≥3x → 20
    if m.volume_multiple > 1.0:
        raw = (m.volume_multiple - 1.0) / (VOLUME_SPIKE_THRESHOLD - 1.0)
        m.volume_score = min(20.0, raw * 20.0)

    # ── 4. Speed of drop (last 5 minutes) ──────────────────────────────────
    if candles and len(candles) >= 2:
        prev_close = candles[-2].close
        cur_close = candles[-1].close
        if prev_close > 0:
            m.speed_drop_pct = (prev_close - cur_close) / prev_close
    # Scale: ≤0% → 0, ≥1% → 20
    if m.speed_drop_pct > 0:
        raw = m.speed_drop_pct / SPEED_THRESHOLD
        m.speed_score = min(20.0, raw * 20.0)

    # ── Total ───────────────────────────────────────────────────────────────
    m.total_score = (
        m.drawdown_score + m.imbalance_score +
        m.volume_score + m.speed_score
    )
    m.total_score = max(0.0, min(100.0, m.total_score))

    if m.total_score >= STRONG_BUY_SCORE:
        m.signal = "STRONG_BUY"
        m.signal_cn = "强力买入"
    elif m.total_score >= MODERATE_BUY_SCORE:
        m.signal = "MODERATE_BUY"
        m.signal_cn = "适度买入"
    else:
        m.signal = "MONITOR"
        m.signal_cn = "持续监控"

    return m


# ─────────────────────────────────────────────────────────────────────────────
# Trade execution
# ─────────────────────────────────────────────────────────────────────────────

def execute_dip_buy(inst_id: str, metrics: CrashMetrics, ticker: TickerData) -> TradeAction:
    """Execute dip-buy order based on crash score, with SL and TP."""
    size = TRADE_SIZES.get(inst_id, "0.001")
    current_price = ticker.last

    if metrics.signal == "STRONG_BUY":
        action = TradeAction(
            inst_id=inst_id, action="STRONG_BUY", action_cn="强力买入 (市价单)",
            order_type="market", entry_price=current_price, size=size,
            stop_loss_price=round(current_price * (1 - STOP_LOSS_PCT), 6),
            take_profit_price=round(current_price * (1 + TAKE_PROFIT_PCT), 6),
        )
    elif metrics.signal == "MODERATE_BUY":
        limit_px = round(current_price * (1 - LIMIT_DISCOUNT_PCT), 6)
        action = TradeAction(
            inst_id=inst_id, action="MODERATE_BUY", action_cn="适度买入 (限价单)",
            order_type="limit", entry_price=limit_px, size=size,
            stop_loss_price=round(limit_px * (1 - STOP_LOSS_PCT), 6),
            take_profit_price=round(limit_px * (1 + TAKE_PROFIT_PCT), 6),
        )
    else:
        return TradeAction(
            inst_id=inst_id, action="MONITOR", action_cn="仅监控",
            order_type="none", entry_price=current_price, size=size,
        )

    # ── Place main order ────────────────────────────────────────────────────
    if action.order_type == "market":
        order_data = okx_cmd([
            "spot", "place",
            "--instId", inst_id,
            "--side", "buy",
            "--ordType", "market",
            "--sz", size,
            "--tdMode", "cash",
        ])
    else:
        order_data = okx_cmd([
            "spot", "place",
            "--instId", inst_id,
            "--side", "buy",
            "--ordType", "limit",
            "--sz", size,
            "--px", str(action.entry_price),
            "--tdMode", "cash",
        ])

    if order_data and isinstance(order_data, list) and len(order_data) > 0:
        rec = order_data[0] if isinstance(order_data[0], dict) else {}
        action.order_id = rec.get("ordId", rec.get("orderId", ""))
        if action.order_id:
            action.success = True
    elif order_data and isinstance(order_data, dict):
        action.order_id = order_data.get("ordId", order_data.get("orderId", ""))
        if action.order_id:
            action.success = True

    # ── Place stop-loss algo order ──────────────────────────────────────────
    if action.success:
        sl_data = okx_cmd([
            "spot", "algo", "place",
            "--instId", inst_id,
            "--side", "sell",
            "--sz", size,
            "--slTriggerPx", str(action.stop_loss_price),
            "--slOrdPx", "-1",
            "--tdMode", "cash",
        ])
        if sl_data:
            rec = sl_data[0] if isinstance(sl_data, list) and sl_data else sl_data
            if isinstance(rec, dict):
                action.sl_algo_id = rec.get("algoId", rec.get("algoOrdId", ""))

    # ── Place take-profit algo order ────────────────────────────────────────
    if action.success:
        tp_data = okx_cmd([
            "spot", "algo", "place",
            "--instId", inst_id,
            "--side", "sell",
            "--sz", size,
            "--tpTriggerPx", str(action.take_profit_price),
            "--tpOrdPx", "-1",
            "--tdMode", "cash",
        ])
        if tp_data:
            rec = tp_data[0] if isinstance(tp_data, list) and tp_data else tp_data
            if isinstance(rec, dict):
                action.tp_algo_id = rec.get("algoId", rec.get("algoOrdId", ""))

    return action


# ─────────────────────────────────────────────────────────────────────────────
# Visualization helpers
# ─────────────────────────────────────────────────────────────────────────────

def crash_score_meter(score: float) -> str:
    """Render a visual meter bar for the crash score."""
    width = 30
    filled = int(score / 100 * width)
    if score >= STRONG_BUY_SCORE:
        colour = C.RED
        bg = C.BG_RED
        label = "!! 闪崩 !!"
    elif score >= MODERATE_BUY_SCORE:
        colour = C.YELLOW
        bg = C.BG_YEL
        label = "! 警告 !"
    else:
        colour = C.GREEN
        bg = C.BG_GRN
        label = "  正常  "
    bar = f"{bg}{C.BOLD}" + "█" * filled + C.RESET + _cw("░" * (width - filled), C.DIM)
    return f"  [{bar}] {_cw(f'{score:5.1f}/100', colour)} {_cw(label, C.BOLD + colour)}"


def orderbook_depth_chart(ob: OrderbookSnapshot, max_lines: int = 8) -> list[str]:
    """ASCII visualization of orderbook bid/ask depth."""
    lines = []
    chart_width = 20

    # Normalize depths for display
    all_depths = [q for _, q in ob.asks[:max_lines]] + [q for _, q in ob.bids[:max_lines]]
    max_depth = max(all_depths) if all_depths else 1.0

    # Asks (top, reversed so lowest ask is closest to center)
    ask_rows = []
    for i, (px, qty) in enumerate(ob.asks[:max_lines]):
        bar_len = int((qty / max_depth) * chart_width) if max_depth > 0 else 0
        bar_len = max(1, bar_len)
        bar = "█" * bar_len
        ask_rows.append(f"    {_cw('│', C.DIM)} {_cw(f'{px:>12,.2f}', C.RED)} {_cw(bar, C.RED)} {_cw(f'{qty:.4f}', C.DIM)}")
    for row in reversed(ask_rows):
        lines.append(row)

    # Spread line
    lines.append(f"    {_cw('│', C.DIM)} {_cw('─' * 12, C.DIM)} {_cw(f'价差: {ob.spread_pct:.4f}%', C.YELLOW)} {_cw('─' * 10, C.DIM)}")

    # Bids
    for i, (px, qty) in enumerate(ob.bids[:max_lines]):
        bar_len = int((qty / max_depth) * chart_width) if max_depth > 0 else 0
        bar_len = max(1, bar_len)
        bar = "█" * bar_len
        lines.append(f"    {_cw('│', C.DIM)} {_cw(f'{px:>12,.2f}', C.GREEN)} {_cw(bar, C.GREEN)} {_cw(f'{qty:.4f}', C.DIM)}")

    return lines


def sparkline(candles: list[CandleBar]) -> str:
    """ASCII sparkline of close prices over the last hour."""
    if not candles:
        return _cw("(无数据)", C.DIM)
    closes = [c.close for c in candles]
    mn, mx = min(closes), max(closes)
    rng = mx - mn if mx != mn else 1.0
    blocks = " ▁▂▃▄▅▆▇█"
    line = ""
    for v in closes:
        idx = int((v - mn) / rng * (len(blocks) - 1))
        line += blocks[idx]
    first_c = closes[0]
    last_c = closes[-1]
    pct = ((last_c - first_c) / first_c * 100) if first_c else 0
    colour = C.GREEN if pct >= 0 else C.RED
    return (
        _cw(f"{mn:,.2f}", C.DIM) + " " +
        _cw(line, colour) + " " +
        _cw(f"{mx:,.2f}", C.DIM) +
        f"  {_cw(f'{pct:+.2f}%', colour)}"
    )


def volume_analysis_bar(candles: list[CandleBar]) -> str:
    """Bar chart of volume per candle bar."""
    if not candles:
        return _cw("(无数据)", C.DIM)
    vols = [c.vol for c in candles]
    mx_vol = max(vols) if vols else 1.0
    bar_width = 3
    parts = []
    for v in vols:
        height = int((v / mx_vol) * 5) if mx_vol > 0 else 0
        height = max(1, height)
        bar_char = "▇" * bar_width
        colour = C.RED if v == max(vols) else C.BLUE
        parts.append(_cw(bar_char[:height], colour))
    return " ".join(parts)


def format_price(price: float, inst_id: str) -> str:
    """Format price with appropriate decimal places."""
    if "BTC" in inst_id:
        return f"{price:,.2f}"
    elif "ETH" in inst_id:
        return f"{price:,.2f}"
    else:
        return f"{price:,.4f}"


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_coin_dashboard(
    inst_id: str,
    ticker: Optional[TickerData],
    orderbook: Optional[OrderbookSnapshot],
    candles: Optional[list[CandleBar]],
    metrics: Optional[CrashMetrics],
    action: Optional[TradeAction],
) -> None:
    """Render the monitoring dashboard for a single coin."""
    coin_name = inst_id.replace("-USDT", "")
    section(f"  {coin_name} 闪崩监控面板")

    if not ticker:
        error(f"无法获取 {inst_id} 行情数据")
        return

    # ── Price info ──────────────────────────────────────────────────────────
    change_colour = C.GREEN if ticker.change_24h_pct >= 0 else C.RED
    kv("最新价格 (现价)", _cw(format_price(ticker.last, inst_id), C.BOLD + C.WHITE))
    kv("24H 最高/最低", f"{_cw(format_price(ticker.high_24h, inst_id), C.GREEN)} / {_cw(format_price(ticker.low_24h, inst_id), C.RED)}")
    kv("24H 涨跌幅", _cw(f"{ticker.change_24h_pct:+.2f}%", change_colour))
    kv("24H 成交量", _cw(f"{ticker.vol_24h:,.2f} {coin_name}", C.WHITE))

    # ── Crash score meter ───────────────────────────────────────────────────
    if metrics:
        print()
        print(f"    {_cw('│', C.DIM)} {_cw('闪崩评分:', C.BOLD + C.CYAN)}")
        print(crash_score_meter(metrics.total_score))
        print()
        kv("回撤幅度", f"{_cw(f'{metrics.drawdown_pct*100:.3f}%', C.YELLOW)}  ({_cw(f'{metrics.drawdown_score:.1f}', C.WHITE)}/40分)")
        kv("订单簿失衡", f"{_cw(f'{metrics.imbalance_ratio:.3f}', C.YELLOW)}  ({_cw(f'{metrics.imbalance_score:.1f}', C.WHITE)}/20分)")
        kv("成交量倍数", f"{_cw(f'{metrics.volume_multiple:.2f}x', C.YELLOW)}  ({_cw(f'{metrics.volume_score:.1f}', C.WHITE)}/20分)")
        kv("下跌速度", f"{_cw(f'{metrics.speed_drop_pct*100:.3f}%/5min', C.YELLOW)}  ({_cw(f'{metrics.speed_score:.1f}', C.WHITE)}/20分)")
        kv("信号判定", _cw(f"[{metrics.signal}] {metrics.signal_cn}", C.BOLD + (
            C.RED if metrics.signal == "STRONG_BUY" else
            C.YELLOW if metrics.signal == "MODERATE_BUY" else C.GREEN
        )))

    # ── Price sparkline ─────────────────────────────────────────────────────
    if candles:
        print()
        kv("1小时价格走势", sparkline(candles))
        kv("成交量分布", volume_analysis_bar(candles))

    # ── Orderbook depth ─────────────────────────────────────────────────────
    if orderbook:
        print()
        print(f"    {_cw('│', C.DIM)} {_cw('订单簿深度 (卖方 / 买方):', C.BOLD + C.CYAN)}")
        depth_lines = orderbook_depth_chart(orderbook, max_lines=5)
        for line in depth_lines:
            print(line)
        kv("买盘深度 (USDT)", _cw(f"{orderbook.bid_depth:,.2f}", C.GREEN))
        kv("卖盘深度 (USDT)", _cw(f"{orderbook.ask_depth:,.2f}", C.RED))
        kv("买卖比", _cw(f"{orderbook.imbalance_ratio:.4f}", C.YELLOW if orderbook.imbalance_ratio < 0.5 else C.WHITE))

    # ── Trade actions ───────────────────────────────────────────────────────
    if action and action.order_type != "none":
        print()
        print(f"    {_cw('│', C.DIM)} {_cw('交易执行:', C.BOLD + C.CYAN)}")
        action_colour = C.RED if action.action == "STRONG_BUY" else C.YELLOW
        kv("操作类型", _cw(action.action_cn, C.BOLD + action_colour))
        kv("订单类型", _cw(action.order_type.upper(), C.WHITE))
        kv("入场价格", _cw(format_price(action.entry_price, inst_id), C.WHITE))
        kv("下单数量", _cw(action.size, C.WHITE))
        kv("止损价格 (-3%)", _cw(format_price(action.stop_loss_price, inst_id), C.RED))
        kv("止盈价格 (+5%)", _cw(format_price(action.take_profit_price, inst_id), C.GREEN))

        if action.success:
            success(f"主订单已提交  订单号: {action.order_id}")
            if action.sl_algo_id:
                success(f"止损单已设置  算法号: {action.sl_algo_id}")
            else:
                warn("止损单提交失败")
            if action.tp_algo_id:
                success(f"止盈单已设置  算法号: {action.tp_algo_id}")
            else:
                warn("止盈单提交失败")
        else:
            error("订单提交失败 — 请检查账户余额和API权限")
    elif action and action.order_type == "none":
        print()
        kv("交易动作", _cw("暂不操作 — 继续监控", C.DIM))

    print(f"    {_cw('└' + '─' * 56, C.DIM)}")


def render_summary(all_metrics: dict[str, CrashMetrics], all_actions: dict[str, TradeAction]) -> None:
    """Render the final summary table."""
    section("  汇总面板 — 闪崩猎手总览")
    print()

    # Header
    hdr = (
        f"    {_cw('│', C.DIM)} "
        f"{_cw('币种', C.BOLD + C.CYAN):>24s}  "
        f"{_cw('崩盘评分', C.BOLD + C.CYAN):>24s}  "
        f"{_cw('回撤', C.BOLD + C.CYAN):>20s}  "
        f"{_cw('失衡比', C.BOLD + C.CYAN):>20s}  "
        f"{_cw('量比', C.BOLD + C.CYAN):>20s}  "
        f"{_cw('信号', C.BOLD + C.CYAN):>24s}"
    )
    print(hdr)
    print(f"    {_cw('│', C.DIM)} {_cw('─' * 80, C.DIM)}")

    for inst_id in MONITORED_PAIRS:
        m = all_metrics.get(inst_id)
        a = all_actions.get(inst_id)
        if not m:
            print(f"    {_cw('│', C.DIM)} {inst_id:<12s}  {'N/A':>8s}")
            continue

        score_colour = C.RED if m.total_score >= 70 else (C.YELLOW if m.total_score >= 50 else C.GREEN)
        signal_colour = C.RED if m.signal == "STRONG_BUY" else (C.YELLOW if m.signal == "MODERATE_BUY" else C.GREEN)
        trade_mark = ""
        if a and a.success:
            trade_mark = _cw(" [已下单]", C.BOLD + C.RED)

        row = (
            f"    {_cw('│', C.DIM)} "
            f"{_cw(inst_id, C.WHITE):<24s}  "
            f"{_cw(f'{m.total_score:.1f}', score_colour):>24s}  "
            f"{_cw(f'{m.drawdown_pct*100:.2f}%', C.YELLOW):>20s}  "
            f"{_cw(f'{m.imbalance_ratio:.3f}', C.YELLOW):>20s}  "
            f"{_cw(f'{m.volume_multiple:.1f}x', C.YELLOW):>20s}  "
            f"{_cw(m.signal_cn, signal_colour):>24s}{trade_mark}"
        )
        print(row)

    print(f"    {_cw('└' + '─' * 80, C.DIM)}")

    # Trade summary
    trades_made = [a for a in all_actions.values() if a.order_type != "none"]
    if trades_made:
        print()
        print(f"    {_cw('交易统计:', C.BOLD + C.CYAN)}")
        for a in trades_made:
            status = _cw("成功", C.GREEN) if a.success else _cw("失败", C.RED)
            print(f"    {_cw('│', C.DIM)} {a.inst_id}: {a.action_cn}  状态: {status}  订单号: {a.order_id or 'N/A'}")
    else:
        print()
        print(f"    {_cw('暂无交易触发 — 市场平稳运行中', C.DIM)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    banner()

    # Validate environment
    missing = []
    for var in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        print(_cw(f"  [!] 缺少环境变量: {', '.join(missing)}", C.YELLOW))
        print(_cw("  [!] 将尝试使用默认配置继续运行...", C.YELLOW))
        print()

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(_cw(f"  扫描时间: {timestamp}", C.DIM))
    print(_cw(f"  监控标的: {', '.join(MONITORED_PAIRS)}", C.DIM))
    print(_cw(f"  模式: 模拟交易 (--demo)", C.DIM))
    print(_cw(f"  止损: {STOP_LOSS_PCT*100:.0f}%  止盈: {TAKE_PROFIT_PCT*100:.0f}%", C.DIM))

    all_metrics: dict[str, CrashMetrics] = {}
    all_actions: dict[str, TradeAction] = {}

    for inst_id in MONITORED_PAIRS:
        coin = inst_id.replace("-USDT", "")
        section(f"  数据采集: {coin}")

        # ── Fetch all market data ───────────────────────────────────────────
        kv("获取行情", _cw("请求中...", C.DIM))
        ticker = fetch_ticker(inst_id)

        kv("获取订单簿", _cw("请求中...", C.DIM))
        orderbook = fetch_orderbook(inst_id)

        kv("获取近期成交", _cw("请求中...", C.DIM))
        trades = fetch_recent_trades(inst_id)

        kv("获取5分钟K线", _cw("请求中...", C.DIM))
        candles = fetch_candles(inst_id)

        # ── Run crash detection ─────────────────────────────────────────────
        metrics = None
        action = None

        if ticker and orderbook and candles:
            metrics = compute_crash_metrics(ticker, orderbook, candles, trades)
            all_metrics[inst_id] = metrics

            # ── Decide and execute ──────────────────────────────────────────
            if metrics.signal in ("STRONG_BUY", "MODERATE_BUY"):
                kv("执行策略", _cw(f"{metrics.signal_cn} — 准备下单...", C.BOLD + C.RED))
                action = execute_dip_buy(inst_id, metrics, ticker)
            else:
                action = TradeAction(
                    inst_id=inst_id, action="MONITOR", action_cn="仅监控",
                    order_type="none", entry_price=ticker.last,
                    size=TRADE_SIZES.get(inst_id, "0.001"),
                )
            all_actions[inst_id] = action
        else:
            warn(f"数据不完整，跳过 {inst_id} 的崩盘检测")
            if ticker:
                action = TradeAction(
                    inst_id=inst_id, action="MONITOR", action_cn="数据不足",
                    order_type="none", entry_price=ticker.last if ticker else 0,
                    size=TRADE_SIZES.get(inst_id, "0.001"),
                )
                all_actions[inst_id] = action

        # ── Render per-coin dashboard ───────────────────────────────────────
        render_coin_dashboard(inst_id, ticker, orderbook, candles, metrics, action)

    # ── Final summary ───────────────────────────────────────────────────────
    render_summary(all_metrics, all_actions)

    print()
    print(_cw("═" * 70, C.RED))
    print(_cw("  闪崩猎手扫描完成", C.BOLD + C.WHITE) + _cw(f"  {time.strftime('%H:%M:%S')}", C.DIM))
    print(_cw("═" * 70, C.RED))
    print()


if __name__ == "__main__":
    main()
