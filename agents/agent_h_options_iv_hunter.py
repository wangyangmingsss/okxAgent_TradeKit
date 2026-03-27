#!/usr/bin/env python3
"""
期权波动率猎人 (Options IV Hunter)
Options Implied Volatility Analysis & Trading AI Agent for OKX.

Scans BTC-USD option chains, builds IV surfaces, detects anomalies
(IV skew, term-structure inversions, outlier strikes), generates
trading signals, and executes the best opportunity on the OKX demo account.
"""

import json
import math
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════
# ANSI Terminal Colours
# ══════════════════════════════════════════════════════════════════════

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

def s(text: str, *styles: str) -> str:
    """Apply ANSI styles to text."""
    return "".join(styles) + str(text) + RESET

def hr(char: str = "─", width: int = 78) -> str:
    return s(char * width, DIM)

# ══════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════

OKX_ENV = {
    **os.environ,
    "OKX_API_KEY":    os.environ.get("OKX_API_KEY", ""),
    "OKX_SECRET_KEY": os.environ.get("OKX_SECRET_KEY", ""),
    "OKX_PASSPHRASE": os.environ.get("OKX_PASSPHRASE", ""),
}

UNDERLYING     = "BTC-USD"
SPOT_INST      = "BTC-USDT"
IV_ANOMALY_STD = 1.5        # flag if IV deviates > 1.5σ from expiry mean
PC_IV_GAP_PCT  = 10.0       # flag if put-call IV gap > 10%
TERM_INVERSION = 5.0        # flag if near-term ATM IV exceeds far-term by this (pct-pts)
STRIKE_RANGE   = 0.20       # ±20% of spot price
MAX_EXPIRIES   = 3          # focus on nearest N expiries
TRADE_SIZE     = "1"         # contracts

# ══════════════════════════════════════════════════════════════════════
# OKX CLI Wrapper
# ══════════════════════════════════════════════════════════════════════

def run_okx(*args: str, demo: bool = True, timeout: int = 45) -> Optional[Any]:
    """
    Run an okx CLI command and return parsed JSON, or None on failure.
    Always appends --json. If demo=True, prepends --demo.
    """
    cmd = ["okx"]
    if demo:
        cmd.append("--demo")
    cmd.extend(args)
    if "--json" not in cmd:
        cmd.append("--json")

    cmd_str = " ".join(cmd)
    print(s(f"    $ {cmd_str}", DIM))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=OKX_ENV,
            timeout=timeout,
        )
        stdout = result.stdout.strip()
        if not stdout:
            if result.stderr.strip():
                print(s(f"    [stderr] {result.stderr.strip()[:300]}", RED))
            return None

        # CLI may emit non-JSON preamble; find the first JSON token
        for i, ch in enumerate(stdout):
            if ch in ("{", "["):
                return json.loads(stdout[i:])
        return json.loads(stdout)

    except subprocess.TimeoutExpired:
        print(s("    [超时] 命令执行超时", RED))
        return None
    except json.JSONDecodeError as exc:
        print(s(f"    [JSON错误] {exc}", RED))
        return None
    except FileNotFoundError:
        print(s("    [错误] 'okx' CLI 未找到", RED))
        return None
    except Exception as exc:
        print(s(f"    [错误] {exc}", RED))
        return None


def sf(data: Any, *keys: str, default: float = 0.0) -> float:
    """Safely drill into nested dicts/lists and return a float."""
    obj = data
    for k in keys:
        try:
            if isinstance(obj, list):
                obj = obj[int(k)]
            elif isinstance(obj, dict):
                obj = obj[k]
            else:
                return default
        except (KeyError, IndexError, TypeError, ValueError):
            return default
    try:
        v = float(obj)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def ss(data: Any, *keys: str, default: str = "") -> str:
    """Safely drill into nested dicts/lists and return a string."""
    obj = data
    for k in keys:
        try:
            if isinstance(obj, list):
                obj = obj[int(k)]
            elif isinstance(obj, dict):
                obj = obj[k]
            else:
                return default
        except (KeyError, IndexError, TypeError, ValueError):
            return default
    return str(obj) if obj is not None else default


def extract_data(response: Any) -> List[Dict]:
    """Extract the data list from an OKX API response."""
    if response is None:
        return []
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        # Standard OKX response: {"code": "0", "data": [...]}
        if "data" in response:
            d = response["data"]
            return d if isinstance(d, list) else [d]
        return [response]
    return []


# ══════════════════════════════════════════════════════════════════════
# Data Structures
# ══════════════════════════════════════════════════════════════════════

class OptionInfo:
    """Parsed option with instrument info and greeks."""
    def __init__(self):
        self.inst_id: str = ""
        self.uly: str = ""
        self.strike: float = 0.0
        self.expiry: str = ""          # e.g. "240329"
        self.expiry_dt: Optional[datetime] = None
        self.opt_type: str = ""        # C or P
        self.mark_vol: float = 0.0     # implied volatility (0-1 scale)
        self.mark_px: float = 0.0
        self.delta: float = 0.0
        self.gamma: float = 0.0
        self.theta: float = 0.0
        self.vega: float = 0.0
        self.bid_vol: float = 0.0
        self.ask_vol: float = 0.0
        self.moneyness: float = 0.0    # strike / spot

    @property
    def iv_pct(self) -> float:
        """IV as a percentage."""
        return self.mark_vol * 100.0

    @property
    def days_to_expiry(self) -> int:
        if self.expiry_dt is None:
            return 0
        delta = self.expiry_dt - datetime.now(timezone.utc)
        return max(int(delta.total_seconds() / 86400), 0)

    def __repr__(self):
        return (f"<{self.inst_id} K={self.strike:.0f} "
                f"IV={self.iv_pct:.1f}% Δ={self.delta:.3f}>")


class Anomaly:
    """A detected IV anomaly."""
    def __init__(self, kind: str, description: str, severity: float,
                 options: List[OptionInfo]):
        self.kind = kind
        self.description = description
        self.severity = severity      # higher = more extreme
        self.options = options

    def severity_bar(self) -> str:
        bars = min(int(self.severity * 2), 10)
        color = GREEN if bars < 4 else YELLOW if bars < 7 else RED
        return s("█" * bars + "░" * (10 - bars), color)


class Signal:
    """A trading signal."""
    def __init__(self, action: str, inst_id: str, side: str,
                 reason: str, score: float, ref_px: float, opt: OptionInfo):
        self.action = action      # BUY / SELL / SPREAD
        self.inst_id = inst_id
        self.side = side          # buy / sell
        self.reason = reason
        self.score = score        # 0-10 confidence
        self.ref_px = ref_px
        self.opt = opt


# ══════════════════════════════════════════════════════════════════════
# ASCII Chart Helpers
# ══════════════════════════════════════════════════════════════════════

def ascii_bar_chart(data: List[Tuple[str, float]], title: str,
                    width: int = 40, unit: str = "%",
                    color: str = CYAN) -> None:
    """Print a horizontal bar chart."""
    if not data:
        print(s("    (无数据)", DIM))
        return
    max_val = max(abs(v) for _, v in data) if data else 1.0
    if max_val == 0:
        max_val = 1.0

    print(s(f"\n  {title}", BOLD, WHITE))
    print(s("  " + "─" * (width + 20), DIM))

    for label, val in data:
        bar_len = int(abs(val) / max_val * width)
        bar = "█" * bar_len + "░" * (width - bar_len)
        sign = "+" if val > 0 else ""
        padded_label = label[:12].rjust(12)
        val_str = f"{sign}{val:.1f}{unit}"
        print(f"  {s(padded_label, WHITE)} │{s(bar, color)} {s(val_str, BOLD, WHITE)}")


def ascii_line_chart(data: List[Tuple[float, float]], title: str,
                     x_label: str = "Strike", y_label: str = "IV%",
                     width: int = 60, height: int = 15) -> None:
    """Print a simple ASCII scatter/line chart."""
    if not data:
        print(s("    (无数据)", DIM))
        return

    xs = [d[0] for d in data]
    ys = [d[1] for d in data]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    if x_max == x_min:
        x_max = x_min + 1
    if y_max == y_min:
        y_max = y_min + 1

    # Add some padding
    y_pad = (y_max - y_min) * 0.1
    y_min -= y_pad
    y_max += y_pad

    print(s(f"\n  {title}", BOLD, WHITE))

    grid = [[" " for _ in range(width)] for _ in range(height)]

    for x, y in data:
        col = int((x - x_min) / (x_max - x_min) * (width - 1))
        row = int((1.0 - (y - y_min) / (y_max - y_min)) * (height - 1))
        col = max(0, min(width - 1, col))
        row = max(0, min(height - 1, row))
        grid[row][col] = "●"

    # Print with y-axis
    for i, row in enumerate(grid):
        y_val = y_max - (y_max - y_min) * i / (height - 1)
        line = "".join(row)
        if i == 0 or i == height - 1 or i == height // 2:
            print(f"  {s(f'{y_val:6.1f}', DIM)} │{s(line, CYAN)}")
        else:
            print(f"  {' ':>6s} │{s(line, CYAN)}")

    # X-axis
    print(f"  {'':>6s} └{'─' * width}")
    x_lo = f"{x_min:.0f}"
    x_hi = f"{x_max:.0f}"
    print(f"  {'':>7s}{x_lo}{' ' * (width - len(x_lo) - len(x_hi))}{x_hi}")
    print(s(f"  {'':>7s}{x_label:^{width}s}", DIM))


# ══════════════════════════════════════════════════════════════════════
# Core Analysis Engine
# ══════════════════════════════════════════════════════════════════════

class OptionsIVHunter:
    """Main analysis engine."""

    def __init__(self):
        self.spot_price: float = 0.0
        self.options: List[OptionInfo] = []
        self.by_expiry: Dict[str, List[OptionInfo]] = {}
        self.expiry_dates: List[str] = []        # sorted nearest first
        self.atm_iv: Dict[str, float] = {}       # expiry -> ATM IV%
        self.anomalies: List[Anomaly] = []
        self.signals: List[Signal] = []
        self.mark_prices: Dict[str, float] = {}  # instId -> mark price
        self.executed_trades: List[Dict] = []

    # ── Step 1: Fetch Data ───────────────────────────────────────────

    def fetch_data(self) -> bool:
        """Fetch all required data from OKX."""
        self._step_header(1, "数据采集 — Option Chain Scanning")

        # 1a. Spot price
        print(s("\n  ▸ 获取BTC现货价格", BOLD, WHITE))
        ticker = run_okx("market", "ticker", SPOT_INST)
        ticker_data = extract_data(ticker)
        if ticker_data:
            self.spot_price = sf(ticker_data[0], "last") or sf(ticker_data[0], "askPx")
        if self.spot_price <= 0:
            # Try idxPx fallback
            print(s("    尝试备选价格源...", DIM))
            idx = run_okx("market", "index-tickers", "--instId", SPOT_INST)
            idx_data = extract_data(idx)
            if idx_data:
                self.spot_price = sf(idx_data[0], "idxPx")
        if self.spot_price <= 0:
            print(s("  ✗ 无法获取BTC现货价格", RED, BOLD))
            return False
        print(s(f"  ✓ BTC 现货价格: ${self.spot_price:,.2f}", GREEN, BOLD))

        # 1b. Option instruments
        print(s("\n  ▸ 扫描期权合约列表", BOLD, WHITE))
        instruments = run_okx("option", "instruments", "--uly", UNDERLYING)
        inst_data = extract_data(instruments)
        if not inst_data:
            print(s("  ✗ 未获取到期权合约数据", RED, BOLD))
            return False
        print(s(f"  ✓ 发现 {len(inst_data)} 个期权合约", GREEN))

        # 1c. Greeks
        print(s("\n  ▸ 获取期权希腊值 (Greeks)", BOLD, WHITE))
        greeks = run_okx("option", "greeks", "--uly", UNDERLYING)
        greeks_data = extract_data(greeks)
        greeks_map: Dict[str, Dict] = {}
        for g in greeks_data:
            iid = ss(g, "instId")
            if iid:
                greeks_map[iid] = g
        print(s(f"  ✓ 获取 {len(greeks_map)} 个合约的Greeks数据", GREEN))

        # 1d. Mark prices (for all options)
        print(s("\n  ▸ 获取期权标记价格", BOLD, WHITE))
        marks = run_okx("market", "mark-price", "--instType", "OPTION")
        marks_data = extract_data(marks)
        for m in marks_data:
            iid = ss(m, "instId")
            mp = sf(m, "markPx")
            if iid and mp > 0:
                self.mark_prices[iid] = mp
        print(s(f"  ✓ 获取 {len(self.mark_prices)} 个合约标记价格", GREEN))

        # 1e. Parse & filter options
        print(s("\n  ▸ 解析和过滤期权数据", BOLD, WHITE))
        self._parse_options(inst_data, greeks_map)

        if not self.options:
            print(s("  ✗ 过滤后无可用期权数据", RED, BOLD))
            return False

        print(s(f"  ✓ 有效期权: {len(self.options)} 个 | "
                f"到期日: {len(self.expiry_dates)} 个 | "
                f"行权价范围: ${self.spot_price*(1-STRIKE_RANGE):,.0f} – "
                f"${self.spot_price*(1+STRIKE_RANGE):,.0f}", GREEN, BOLD))

        return True

    def _parse_options(self, instruments: List[Dict], greeks_map: Dict[str, Dict]):
        """Parse instrument list and merge with greeks data."""
        lo_strike = self.spot_price * (1 - STRIKE_RANGE)
        hi_strike = self.spot_price * (1 + STRIKE_RANGE)

        # Collect all expiry dates from instruments
        expiry_set: Dict[str, datetime] = {}
        for inst in instruments:
            inst_id = ss(inst, "instId")
            if not inst_id:
                continue

            # Parse instId like BTC-USD-260328-90000-C
            parts = inst_id.split("-")
            if len(parts) < 5:
                continue

            try:
                expiry_str = parts[2]  # e.g. "260328"
                strike = float(parts[3])
                opt_type = parts[4]  # C or P
            except (ValueError, IndexError):
                continue

            # Filter by strike range
            if strike < lo_strike or strike > hi_strike:
                continue

            # Parse expiry date
            try:
                if len(expiry_str) == 6:
                    expiry_dt = datetime.strptime(expiry_str, "%y%m%d").replace(
                        tzinfo=timezone.utc)
                else:
                    continue
            except ValueError:
                continue

            expiry_set[expiry_str] = expiry_dt

        if not expiry_set:
            return

        # Sort by date and pick nearest N
        sorted_expiries = sorted(expiry_set.items(), key=lambda x: x[1])
        selected = sorted_expiries[:MAX_EXPIRIES]
        selected_strs = {e[0] for e in selected}

        # Now build OptionInfo objects for selected expiries
        for inst in instruments:
            inst_id = ss(inst, "instId")
            if not inst_id:
                continue

            parts = inst_id.split("-")
            if len(parts) < 5:
                continue

            try:
                expiry_str = parts[2]
                strike = float(parts[3])
                opt_type = parts[4]
            except (ValueError, IndexError):
                continue

            if expiry_str not in selected_strs:
                continue
            if strike < lo_strike or strike > hi_strike:
                continue

            o = OptionInfo()
            o.inst_id = inst_id
            o.uly = UNDERLYING
            o.strike = strike
            o.expiry = expiry_str
            o.expiry_dt = expiry_set.get(expiry_str)
            o.opt_type = opt_type
            o.moneyness = strike / self.spot_price if self.spot_price > 0 else 0.0
            o.mark_px = self.mark_prices.get(inst_id, 0.0)

            # Merge greeks
            g = greeks_map.get(inst_id, {})
            o.mark_vol = sf(g, "markVol")
            o.delta = sf(g, "deltaPA") or sf(g, "delta")
            o.gamma = sf(g, "gammaPA") or sf(g, "gamma")
            o.theta = sf(g, "thetaPA") or sf(g, "theta")
            o.vega = sf(g, "vegaPA") or sf(g, "vega")
            o.bid_vol = sf(g, "bidVol")
            o.ask_vol = sf(g, "askVol")

            # If markVol is 0, try to get from askVol/bidVol midpoint
            if o.mark_vol <= 0 and (o.bid_vol > 0 or o.ask_vol > 0):
                vals = [v for v in [o.bid_vol, o.ask_vol] if v > 0]
                o.mark_vol = statistics.mean(vals) if vals else 0.0

            # Only include options with some IV data
            if o.mark_vol > 0:
                self.options.append(o)

        # Group by expiry
        self.by_expiry = {}
        for o in self.options:
            self.by_expiry.setdefault(o.expiry, []).append(o)

        self.expiry_dates = sorted(self.by_expiry.keys(),
                                   key=lambda e: expiry_set.get(e, datetime.max.replace(tzinfo=timezone.utc)))

    # ── Step 2: IV Surface Analysis ──────────────────────────────────

    def analyze_iv_surface(self):
        """Build IV surface, compute ATM IV, term structure, and skew."""
        self._step_header(2, "波动率曲面分析 — IV Surface Analysis")

        # ATM IV per expiry
        print(s("\n  ▸ 计算ATM隐含波动率", BOLD, WHITE))
        for exp in self.expiry_dates:
            opts = self.by_expiry[exp]
            # ATM = closest strike to spot
            calls = [o for o in opts if o.opt_type == "C"]
            puts = [o for o in opts if o.opt_type == "P"]
            all_sorted = sorted(opts, key=lambda o: abs(o.strike - self.spot_price))

            if all_sorted:
                # Use average of nearest call and put if both exist
                nearest_strike = all_sorted[0].strike
                at_money = [o for o in opts if abs(o.strike - nearest_strike) < 1.0]
                ivs = [o.iv_pct for o in at_money if o.iv_pct > 0]
                if ivs:
                    self.atm_iv[exp] = statistics.mean(ivs)
                else:
                    self.atm_iv[exp] = all_sorted[0].iv_pct

                dte = all_sorted[0].days_to_expiry
                print(f"    {s(exp, BOLD, WHITE)} (DTE={dte:>3d}d): "
                      f"ATM IV = {s(f'{self.atm_iv[exp]:.1f}%', BOLD, CYAN)} "
                      f"(K={nearest_strike:,.0f}, n={len(at_money)})")

        # Term structure chart
        if self.atm_iv:
            term_data = []
            for exp in self.expiry_dates:
                if exp in self.atm_iv:
                    opts = self.by_expiry[exp]
                    dte = opts[0].days_to_expiry if opts else 0
                    label = f"{exp}({dte}d)"
                    term_data.append((label, self.atm_iv[exp]))
            ascii_bar_chart(term_data, "📊 IV期限结构 (ATM IV Term Structure)",
                            width=35, color=MAGENTA)

        # IV smile for nearest expiry
        if self.expiry_dates:
            nearest = self.expiry_dates[0]
            opts = sorted(self.by_expiry[nearest], key=lambda o: o.strike)
            calls = [(o.strike, o.iv_pct) for o in opts if o.opt_type == "C" and o.iv_pct > 0]
            puts = [(o.strike, o.iv_pct) for o in opts if o.opt_type == "P" and o.iv_pct > 0]
            combined = calls + puts
            combined.sort(key=lambda x: x[0])
            if combined:
                # Deduplicate by averaging at same strike
                strike_iv: Dict[float, List[float]] = {}
                for k, iv in combined:
                    strike_iv.setdefault(k, []).append(iv)
                deduped = [(k, statistics.mean(vs)) for k, vs in sorted(strike_iv.items())]
                ascii_line_chart(deduped,
                                 f"📈 IV微笑曲线 — 最近到期 {nearest} (IV Smile)",
                                 x_label="行权价 Strike", y_label="IV%")

        # Skew analysis per expiry
        print(s("\n  ▸ 波动率偏斜分析 (IV Skew)", BOLD, WHITE))
        for exp in self.expiry_dates:
            opts = self.by_expiry[exp]
            otm_puts = [o for o in opts if o.opt_type == "P"
                        and o.strike < self.spot_price * 0.97 and o.iv_pct > 0]
            otm_calls = [o for o in opts if o.opt_type == "C"
                         and o.strike > self.spot_price * 1.03 and o.iv_pct > 0]

            if otm_puts and otm_calls:
                put_iv = statistics.mean([o.iv_pct for o in otm_puts])
                call_iv = statistics.mean([o.iv_pct for o in otm_calls])
                skew = put_iv - call_iv
                skew_color = RED if skew > 5 else YELLOW if skew > 0 else GREEN
                print(f"    {s(exp, BOLD, WHITE)}: "
                      f"OTM Put IV={s(f'{put_iv:.1f}%', YELLOW)} | "
                      f"OTM Call IV={s(f'{call_iv:.1f}%', CYAN)} | "
                      f"偏斜={s(f'{skew:+.1f}pp', skew_color, BOLD)}")
            else:
                print(f"    {s(exp, BOLD, WHITE)}: "
                      f"{s('OTM数据不足，无法计算偏斜', DIM)}")

    # ── Step 3: Anomaly Detection ────────────────────────────────────

    def detect_anomalies(self):
        """Find IV anomalies: outliers, put-call gaps, term inversions."""
        self._step_header(3, "异常检测 — Anomaly Detection")
        self.anomalies = []

        # 3a. IV outliers per expiry
        print(s("\n  ▸ 检测IV异常值 (>1.5σ)", BOLD, WHITE))
        for exp in self.expiry_dates:
            opts = self.by_expiry[exp]
            ivs = [o.iv_pct for o in opts if o.iv_pct > 0]
            if len(ivs) < 3:
                continue
            mean_iv = statistics.mean(ivs)
            std_iv = statistics.stdev(ivs) if len(ivs) > 1 else 0.0
            if std_iv < 0.01:
                continue

            for o in opts:
                if o.iv_pct <= 0:
                    continue
                z = (o.iv_pct - mean_iv) / std_iv
                if abs(z) > IV_ANOMALY_STD:
                    direction = "高" if z > 0 else "低"
                    a = Anomaly(
                        kind="IV异常",
                        description=(f"{o.inst_id}: IV={o.iv_pct:.1f}% "
                                     f"({direction}, z={z:+.2f}, "
                                     f"均值={mean_iv:.1f}%, σ={std_iv:.1f}%)"),
                        severity=abs(z),
                        options=[o],
                    )
                    self.anomalies.append(a)
                    color = RED if z > 0 else GREEN
                    print(f"    {s('⚠', color)} {a.description}")

        # 3b. Put-Call IV divergence at same strike/expiry
        print(s("\n  ▸ 检测看涨/看跌IV偏差 (Put-Call IV Gap)", BOLD, WHITE))
        for exp in self.expiry_dates:
            opts = self.by_expiry[exp]
            by_strike: Dict[float, Dict[str, OptionInfo]] = {}
            for o in opts:
                by_strike.setdefault(o.strike, {})[o.opt_type] = o

            for strike, cp in by_strike.items():
                if "C" in cp and "P" in cp:
                    c_iv = cp["C"].iv_pct
                    p_iv = cp["P"].iv_pct
                    if c_iv <= 0 or p_iv <= 0:
                        continue
                    avg_iv = (c_iv + p_iv) / 2.0
                    if avg_iv <= 0:
                        continue
                    gap_pct = abs(p_iv - c_iv) / avg_iv * 100.0
                    if gap_pct > PC_IV_GAP_PCT:
                        a = Anomaly(
                            kind="Put-Call IV偏差",
                            description=(f"{exp} K={strike:,.0f}: "
                                         f"Call IV={c_iv:.1f}%, Put IV={p_iv:.1f}% "
                                         f"(差距={gap_pct:.1f}%)"),
                            severity=gap_pct / 10.0,
                            options=[cp["C"], cp["P"]],
                        )
                        self.anomalies.append(a)
                        print(f"    {s('⚠', YELLOW)} {a.description}")

        # 3c. Term structure inversion
        print(s("\n  ▸ 检测期限结构倒挂", BOLD, WHITE))
        sorted_atm = [(exp, self.atm_iv[exp]) for exp in self.expiry_dates
                       if exp in self.atm_iv]
        if len(sorted_atm) >= 2:
            for i in range(len(sorted_atm) - 1):
                near_exp, near_iv = sorted_atm[i]
                far_exp, far_iv = sorted_atm[i + 1]
                if near_iv > far_iv + TERM_INVERSION:
                    diff = near_iv - far_iv
                    near_opts = self.by_expiry.get(near_exp, [])
                    far_opts = self.by_expiry.get(far_exp, [])
                    a = Anomaly(
                        kind="期限结构倒挂",
                        description=(f"近期({near_exp}) ATM IV={near_iv:.1f}% > "
                                     f"远期({far_exp}) ATM IV={far_iv:.1f}% "
                                     f"(倒挂={diff:.1f}pp)"),
                        severity=diff / 5.0,
                        options=near_opts[:2] + far_opts[:2],
                    )
                    self.anomalies.append(a)
                    print(s(f"    ⚠ {a.description}", RED, BOLD))
        else:
            print(s("    到期日不足，无法分析期限结构", DIM))

        # Sort anomalies by severity
        self.anomalies.sort(key=lambda a: a.severity, reverse=True)

        if not self.anomalies:
            print(s("\n  ✓ 未检测到显著异常 — 市场波动率表面正常", GREEN, BOLD))
        else:
            print(s(f"\n  ⚠ 共检测到 {len(self.anomalies)} 个异常", YELLOW, BOLD))

    # ── Step 4: Trading Signals ──────────────────────────────────────

    def generate_signals(self):
        """Generate trading signals from anomalies."""
        self._step_header(4, "交易信号生成 — Trading Signals")
        self.signals = []

        for exp in self.expiry_dates:
            opts = self.by_expiry[exp]
            ivs = [o.iv_pct for o in opts if o.iv_pct > 0]
            if len(ivs) < 3:
                continue
            mean_iv = statistics.mean(ivs)
            std_iv = statistics.stdev(ivs) if len(ivs) > 1 else 0.0
            if std_iv < 0.01:
                continue

            for o in opts:
                if o.iv_pct <= 0 or o.mark_px <= 0:
                    continue
                z = (o.iv_pct - mean_iv) / std_iv

                if z > IV_ANOMALY_STD:
                    # High IV → SELL signal (mean reversion)
                    sig = Signal(
                        action="SELL",
                        inst_id=o.inst_id,
                        side="sell",
                        reason=(f"IV偏高 (z={z:+.2f}): "
                                f"IV={o.iv_pct:.1f}% vs 均值={mean_iv:.1f}% → 做空波动率"),
                        score=min(z * 2, 10.0),
                        ref_px=o.mark_px,
                        opt=o,
                    )
                    self.signals.append(sig)

                elif z < -IV_ANOMALY_STD:
                    # Low IV → BUY signal (cheap option)
                    sig = Signal(
                        action="BUY",
                        inst_id=o.inst_id,
                        side="buy",
                        reason=(f"IV偏低 (z={z:+.2f}): "
                                f"IV={o.iv_pct:.1f}% vs 均值={mean_iv:.1f}% → 买入廉价期权"),
                        score=min(abs(z) * 2, 10.0),
                        ref_px=o.mark_px,
                        opt=o,
                    )
                    self.signals.append(sig)

        # Sort by score descending
        self.signals.sort(key=lambda s: s.score, reverse=True)

        if not self.signals:
            print(s("  ⓘ 当前无显著交易信号", DIM))
        else:
            print(s(f"\n  发现 {len(self.signals)} 个交易信号:", BOLD, WHITE))
            for i, sig in enumerate(self.signals[:10]):
                icon = s("▼", RED) if sig.action == "SELL" else s("▲", GREEN)
                score_bar = s("★" * min(int(sig.score), 10), YELLOW)
                print(f"    {i+1:>2d}. {icon} {s(sig.action, BOLD)} "
                      f"{s(sig.inst_id, CYAN)} "
                      f"| 评分={score_bar} ({sig.score:.1f}) "
                      f"| 参考价={sig.ref_px:.4f}")
                print(f"        {s(sig.reason, DIM)}")

    # ── Step 5: Execute Demo Trade ───────────────────────────────────

    def execute_best_trade(self):
        """Execute the highest-scored signal on demo."""
        self._step_header(5, "模拟交易执行 — Demo Trade Execution")

        if not self.signals:
            print(s("  ⓘ 无交易信号，跳过执行", DIM))
            return

        best = self.signals[0]
        print(s(f"\n  选中最佳信号:", BOLD, WHITE))
        print(f"    合约: {s(best.inst_id, BOLD, CYAN)}")
        print(f"    方向: {s(best.action, BOLD, GREEN if best.action == 'BUY' else RED)}")
        print(f"    原因: {best.reason}")
        print(f"    参考价: {s(f'{best.ref_px:.4f}', BOLD, WHITE)}")
        print(f"    评分: {s(f'{best.score:.1f}/10', BOLD, YELLOW)}")

        # Use mark price as limit price, with small buffer
        if best.action == "SELL":
            # Sell slightly above mark
            limit_px = best.ref_px * 1.002
        else:
            # Buy slightly below mark
            limit_px = best.ref_px * 0.998

        limit_px_str = f"{limit_px:.4f}"

        print(s(f"\n  ▸ 下单执行...", BOLD, WHITE))
        print(f"    限价: {s(limit_px_str, BOLD, WHITE)}")
        print(f"    数量: {TRADE_SIZE} 张")
        print(f"    模式: 全仓 (cross)")

        result = run_okx(
            "trade", "order",
            "--instId", best.inst_id,
            "--tdMode", "cross",
            "--side", best.side,
            "--ordType", "limit",
            "--sz", TRADE_SIZE,
            "--px", limit_px_str,
            demo=True,
        )

        trade_record = {
            "inst_id": best.inst_id,
            "side": best.side,
            "action": best.action,
            "px": limit_px_str,
            "sz": TRADE_SIZE,
            "reason": best.reason,
            "score": best.score,
            "result": None,
        }

        rdata = extract_data(result)
        if rdata:
            order_id = ss(rdata[0], "ordId") or ss(rdata[0], "clOrdId")
            s_code = ss(rdata[0], "sCode")
            s_msg = ss(rdata[0], "sMsg")
            trade_record["result"] = {
                "orderId": order_id, "sCode": s_code, "sMsg": s_msg
            }
            if s_code == "0" or order_id:
                print(s(f"  ✓ 下单成功! 订单ID: {order_id}", GREEN, BOLD))
            else:
                print(s(f"  ✗ 下单失败: [{s_code}] {s_msg}", RED, BOLD))
        else:
            code = ""
            msg = ""
            if isinstance(result, dict):
                code = ss(result, "code")
                msg = ss(result, "msg")
            trade_record["result"] = {"error": msg or "未知错误", "code": code}
            print(s(f"  ✗ 下单失败: {code} {msg}", RED, BOLD))

        self.executed_trades.append(trade_record)

    # ── Step 6: Final Report ─────────────────────────────────────────

    def print_report(self):
        """Print comprehensive analysis dashboard."""
        self._step_header(6, "综合分析报告 — Options IV Dashboard")

        # ── Header ───────────────────────────────────────────────────
        print()
        print(s("═" * 78, BOLD, MAGENTA))
        print(s("  期权波动率猎人 — 分析报告", BOLD, WHITE))
        print(s(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                f"  |  标的: {UNDERLYING}  |  现货: ${self.spot_price:,.2f}", DIM))
        print(s("═" * 78, BOLD, MAGENTA))

        # ── IV Term Structure ────────────────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  一、IV期限结构 (Term Structure)                            │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        if self.atm_iv:
            term_data = []
            for exp in self.expiry_dates:
                if exp in self.atm_iv:
                    opts = self.by_expiry[exp]
                    dte = opts[0].days_to_expiry if opts else 0
                    label = f"{exp}({dte}d)"
                    term_data.append((label, self.atm_iv[exp]))
            ascii_bar_chart(term_data, "ATM IV 期限结构",
                            width=40, color=MAGENTA)

            # Check for inversion
            if len(term_data) >= 2:
                ivs_sorted = [v for _, v in term_data]
                if ivs_sorted[0] > ivs_sorted[-1]:
                    print(s("    ⚠ 期限结构呈倒挂形态 — 近期波动率高于远期", YELLOW, BOLD))
                else:
                    print(s("    ✓ 期限结构正常 — 远期波动率高于近期", GREEN))
        else:
            print(s("    (无ATM IV数据)", DIM))

        # ── IV Smile/Skew ────────────────────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  二、IV微笑曲线 (Nearest Expiry Smile)                      │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        if self.expiry_dates:
            nearest = self.expiry_dates[0]
            opts = sorted(self.by_expiry[nearest], key=lambda o: o.strike)
            strike_iv: Dict[float, List[float]] = {}
            for o in opts:
                if o.iv_pct > 0:
                    strike_iv.setdefault(o.strike, []).append(o.iv_pct)
            deduped = [(k, statistics.mean(vs)) for k, vs in sorted(strike_iv.items())]
            if deduped:
                ascii_line_chart(deduped,
                                 f"到期日 {nearest} — IV vs 行权价",
                                 x_label="行权价 (Strike)",
                                 y_label="IV%",
                                 width=55, height=12)
                # Mark spot
                print(s(f"    ↕ 现货位置: ${self.spot_price:,.0f}", DIM))
            else:
                print(s("    (数据不足)", DIM))

        # ── Top Anomalies ────────────────────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  三、异常排名 (Top Anomalies)                               │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        if self.anomalies:
            print(f"\n  {'#':>3s}  {'类型':<20s}  {'严重度':<12s}  {'描述'}")
            print(s("  " + "─" * 74, DIM))
            for i, a in enumerate(self.anomalies[:15]):
                kind_color = (RED if a.kind == "期限结构倒挂"
                              else YELLOW if a.kind == "Put-Call IV偏差"
                              else MAGENTA)
                kind_padded = a.kind.ljust(20)
                print(f"  {i+1:>3d}  {s(kind_padded, kind_color)}  "
                      f"{a.severity_bar()}  {a.description}")
        else:
            print(s("    ✓ 未发现显著异常", GREEN))

        # ── Greeks Summary for Flagged ───────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  四、异常合约希腊值 (Greeks Summary)                        │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        flagged_opts = set()
        for a in self.anomalies[:10]:
            for o in a.options:
                flagged_opts.add(o.inst_id)

        flagged_list = [o for o in self.options if o.inst_id in flagged_opts]
        if flagged_list:
            print(f"\n  {'合约ID':<30s} {'类型':>4s} {'IV%':>7s} "
                  f"{'Delta':>8s} {'Gamma':>8s} {'Theta':>9s} {'Vega':>8s}")
            print(s("  " + "─" * 78, DIM))
            for o in sorted(flagged_list, key=lambda x: x.inst_id)[:20]:
                d_color = GREEN if o.delta > 0 else RED
                inst_col = o.inst_id[:30].ljust(30)
                type_col = o.opt_type.rjust(4)
                iv_col = f"{o.iv_pct:>7.1f}"
                delta_col = f"{o.delta:>8.4f}"
                gamma_col = f"{o.gamma:>8.6f}"
                theta_col = f"{o.theta:>9.4f}"
                vega_col = f"{o.vega:>8.4f}"
                print(f"  {s(inst_col, WHITE)} "
                      f"{s(type_col, CYAN)} "
                      f"{s(iv_col, YELLOW)} "
                      f"{s(delta_col, d_color)} "
                      f"{gamma_col} "
                      f"{theta_col} "
                      f"{vega_col}")
        else:
            print(s("    (无异常合约)", DIM))

        # ── Trading Signals ──────────────────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  五、交易信号 (Trading Signals)                             │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        if self.signals:
            print(f"\n  {'#':>3s}  {'动作':<6s} {'合约':<32s} {'评分':>5s} {'参考价':>10s}")
            print(s("  " + "─" * 70, DIM))
            for i, sig in enumerate(self.signals[:10]):
                act_color = GREEN if sig.action == "BUY" else RED
                act_padded = sig.action.ljust(6)
                inst_padded = sig.inst_id.ljust(32)
                score_str = f"{sig.score:>5.1f}"
                print(f"  {i+1:>3d}  {s(act_padded, act_color, BOLD)} "
                      f"{s(inst_padded, WHITE)} "
                      f"{s(score_str, YELLOW)} "
                      f"{sig.ref_px:>10.4f}")
                print(s(f"       {sig.reason}", DIM))
        else:
            print(s("    ⓘ 当前无交易信号", DIM))

        # ── Executed Trades ──────────────────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  六、已执行交易 (Executed Trades)                           │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        if self.executed_trades:
            for t in self.executed_trades:
                r = t.get("result", {}) or {}
                oid = r.get("orderId", "")
                err = r.get("error", "")
                s_code = r.get("sCode", "")

                status_str = ""
                if oid:
                    status_str = s(f"✓ 成功 (ID: {oid})", GREEN, BOLD)
                elif s_code == "0":
                    status_str = s("✓ 已提交", GREEN, BOLD)
                else:
                    status_str = s(f"✗ 失败: {err or s_code}", RED, BOLD)

                side_color = GREEN if t["side"] == "buy" else RED
                print(f"\n    合约:  {s(t['inst_id'], BOLD, CYAN)}")
                print(f"    方向:  {s(t['side'].upper(), side_color, BOLD)} "
                      f"({t['action']})")
                print(f"    价格:  {t['px']}")
                print(f"    数量:  {t['sz']} 张")
                print(f"    评分:  {t['score']:.1f}/10")
                print(f"    状态:  {status_str}")
                print(f"    原因:  {s(t['reason'], DIM)}")
        else:
            print(s("    (未执行交易)", DIM))

        # ── Market Summary ───────────────────────────────────────────
        print(s("\n┌─────────────────────────────────────────────────────────────┐", CYAN))
        print(s("│  七、市场概览 (Market Summary)                              │", BOLD, CYAN))
        print(s("└─────────────────────────────────────────────────────────────┘", CYAN))

        total_calls = sum(1 for o in self.options if o.opt_type == "C")
        total_puts = sum(1 for o in self.options if o.opt_type == "P")
        all_ivs = [o.iv_pct for o in self.options if o.iv_pct > 0]
        avg_iv = statistics.mean(all_ivs) if all_ivs else 0.0
        max_iv_opt = max(self.options, key=lambda o: o.iv_pct) if self.options else None
        min_iv_opt = min((o for o in self.options if o.iv_pct > 0),
                         key=lambda o: o.iv_pct, default=None)

        print(f"\n    标的资产:     {s(UNDERLYING, BOLD, WHITE)}")
        print(f"    现货价格:     {s(f'${self.spot_price:,.2f}', BOLD, WHITE)}")
        print(f"    分析合约数:   {s(f'{len(self.options)}', BOLD, WHITE)} "
              f"({total_calls} Calls + {total_puts} Puts)")
        print(f"    到期日数量:   {s(f'{len(self.expiry_dates)}', BOLD, WHITE)} "
              f"({', '.join(self.expiry_dates)})")
        print(f"    平均IV:       {s(f'{avg_iv:.1f}%', BOLD, YELLOW)}")
        if max_iv_opt:
            print(f"    最高IV:       {s(f'{max_iv_opt.iv_pct:.1f}%', BOLD, RED)} "
                  f"({max_iv_opt.inst_id})")
        if min_iv_opt:
            print(f"    最低IV:       {s(f'{min_iv_opt.iv_pct:.1f}%', BOLD, GREEN)} "
                  f"({min_iv_opt.inst_id})")
        print(f"    异常数量:     {s(f'{len(self.anomalies)}', BOLD, YELLOW)}")
        print(f"    信号数量:     {s(f'{len(self.signals)}', BOLD, CYAN)}")

        # Footer
        print()
        print(s("═" * 78, BOLD, MAGENTA))
        print(s("  期权波动率猎人 v1.0 — 分析完成", BOLD, WHITE))
        print(s("  Options IV Hunter — Analysis Complete", DIM))
        print(s("═" * 78, BOLD, MAGENTA))
        print()

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _step_header(num: int, text: str):
        print(f"\n{s('═' * 78, BOLD, BLUE)}")
        print(f"  {s(f'[步骤 {num}]', BOLD, CYAN)} {s(text, BOLD, WHITE)}")
        print(s("═" * 78, BOLD, BLUE))


# ══════════════════════════════════════════════════════════════════════
# Banner
# ══════════════════════════════════════════════════════════════════════

def print_banner():
    banner_lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║                                                            ║",
        "║       期 权 波 动 率 猎 人                                 ║",
        "║       Options IV Hunter  v1.0                              ║",
        "║                                                            ║",
        "║       Implied Volatility Analysis & Trading Agent          ║",
        "║       BTC-USD Options on OKX (Demo Mode)                   ║",
        "║                                                            ║",
        "╚══════════════════════════════════════════════════════════════╝",
    ]
    print()
    for line in banner_lines:
        print(s(f"  {line}", BOLD, MAGENTA))
    print()


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    print_banner()

    # Verify environment
    missing = []
    for var in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        print(s(f"  ⚠ 环境变量缺失: {', '.join(missing)}", YELLOW))
        print(s("    将继续运行，但API调用可能失败", DIM))
        print()

    hunter = OptionsIVHunter()

    # Step 1: Fetch data
    if not hunter.fetch_data():
        print(s("\n  ✗ 数据采集失败，无法继续分析", RED, BOLD))
        print(s("    请检查网络连接和OKX CLI配置", DIM))
        sys.exit(1)

    # Step 2: IV surface analysis
    hunter.analyze_iv_surface()

    # Step 3: Anomaly detection
    hunter.detect_anomalies()

    # Step 4: Generate signals
    hunter.generate_signals()

    # Step 5: Execute best trade
    hunter.execute_best_trade()

    # Step 6: Print report
    hunter.print_report()


if __name__ == "__main__":
    main()
