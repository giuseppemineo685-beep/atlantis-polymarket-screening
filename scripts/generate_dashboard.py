from __future__ import annotations

import base64
import csv
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
FONTS = Path(__file__).resolve().parent / "fonts"
BTC5M_TRADE_LOG = ROOT / "outputs" / "trade_log_btc5m.csv"
BTC5M_HEDGE_DECISIONS = ROOT / "outputs" / "btc5m_hedge_paper_decisions.csv"
BTC5M_HEDGE_WINDOW_SUMMARY = ROOT / "outputs" / "btc5m_hedge_paper_window_summary.csv"
BTC5M_MOMENTUM_DECISIONS = ROOT / "outputs" / "btc5m_momentum_paper_decisions.csv"
BTC5M_MOMENTUM_WINDOW_SUMMARY = ROOT / "outputs" / "btc5m_momentum_paper_window_summary.csv"
OUT_PATH = ROOT / "docs" / "index.html"

ZURICH = ZoneInfo("Europe/Zurich")


def font_b64(name: str) -> str:
    return base64.b64encode((FONTS / name).read_bytes()).decode()


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


STRATEGY_LABELS = {
    "A_lag_arbitrage": "A · Arbitraje de rezago",
    "B_momentum": "B · Momentum de cierre",
    "D_cheap_blind": "D · Barato sin filtro",
    "E_scaling_replicator": "E · Réplica de bot real (escalado)",
}

STRATEGY_DESCRIPTIONS = {
    "A_lag_arbitrage": "Compra el lado que el precio de BTC ya favorece, pero solo si esa cuota sigue barata (&lt;20¢) — apuesta a que el libro de órdenes todavía no se re-precio.",
    "B_momentum": "Mira hacia dónde viene la tendencia del precio en el último minuto y compra ese lado, sin importar si la cuota está cara o barata.",
    "D_cheap_blind": "Compra cualquier lado que esté por debajo de 15¢ cerca del cierre, sin mirar hacia dónde va el precio — sirve de comparación para saber si las otras 4 realmente le ganan a comprar barato a ciegas.",
    "E_scaling_replicator": "Va comprando de a poco (cada ~25 seg) del lado que el precio favorece en cada momento, pudiendo cambiar de lado si se da vuelta — inspirada en el patrón real de una wallet que opera estos mercados con ~78% de acierto.",
}


def render_btc5m_row(r: dict, *, extra_class: str = "", show_strategy: bool = True) -> str:
    pct_return = r.get("pct_return", "")
    return_class = ""
    if pct_return not in (None, ""):
        try:
            return_class = "num-pos" if float(pct_return) >= 0 else "num-neg"
        except ValueError:
            return_class = ""
    status = r.get("status", "")
    status_badge = f'<span class="pill pill-{"win" if status == "WIN" else "loss"}">{esc(status)}</span>'
    strategy_cell = (
        f"<td class=\"dim\">{esc(STRATEGY_LABELS.get(r.get('strategy'), r.get('strategy')))}</td>"
        if show_strategy
        else ""
    )
    return f"""
    <tr class="{extra_class}">
      <td>{status_badge}</td>
      {strategy_cell}
      <td class="title-cell">{esc(r.get('window_slug'))}</td>
      <td><b>{esc(r.get('direction'))}</b></td>
      <td class="num">{fmt_price(r.get('entry_price'))}</td>
      <td class="num {return_class}">{fmt_pct(pct_return)}</td>
      <td class="dim">{esc(r.get('date_closed'))}</td>
    </tr>"""


def render_btc5m_strategy_row(name: str, stats: dict) -> str:
    n = stats["n"]
    win_rate = (stats["wins"] / n * 100) if n else 0
    usd_class = "num-pos" if stats["usd_sum"] >= 0 else "num-neg"
    return f"""
    <tr>
      <td>{esc(STRATEGY_LABELS.get(name, name))}</td>
      <td class="num">{n}</td>
      <td class="num">{win_rate:.1f}%</td>
      <td class="num {'num-pos' if stats['pct_sum'] / n >= 0 else 'num-neg'}">{(stats['pct_sum'] / n if n else 0):+.1f}%</td>
      <td class="num {usd_class}">${stats['usd_sum']:+.2f}</td>
    </tr>"""


def render_btc5m_strategy_section(strategy_key: str, stats: dict, rows: list[dict]) -> str:
    label = STRATEGY_LABELS.get(strategy_key, strategy_key)
    n = stats.get("n", 0)
    win_rate = (stats["wins"] / n * 100) if n else 0
    avg_pct = (stats["pct_sum"] / n) if n else 0
    usd_sum = stats.get("usd_sum", 0.0)
    avg_class = "num-pos" if avg_pct >= 0 else "num-neg"
    usd_class = "num-pos" if usd_sum >= 0 else "num-neg"
    visible = rows[:15]
    hidden = rows[15:]
    table_id = f"history-table-btc5m-{strategy_key}"
    toggle_id = f"toggle-history-btn-btc5m-{strategy_key}"
    toggle_btn = (
        f'<button class="tab-btn" id="{toggle_id}" data-count="{len(rows)}" data-label="Ver todas">Ver todas ({len(rows)})</button>'
        if hidden
        else ""
    )
    description = STRATEGY_DESCRIPTIONS.get(strategy_key, "")
    return f"""
  <section>
    <div class="section-head">
      <h2>{esc(label)}</h2>
      <p class="strategy-desc">{description}</p>
      <span class="section-note">{n} trades · win rate {win_rate:.1f}% · retorno promedio <span class="{avg_class}">{avg_pct:+.1f}%</span> · USD total <span class="{usd_class}">${usd_sum:+.2f}</span></span>
    </div>
    <div class="table-scroll">
      <table id="{table_id}">
        <thead>
          <tr>
            <th>Estado</th><th>Ventana</th><th>Dirección</th><th>Entrada</th><th>Retorno</th><th>Cerrado</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_btc5m_row(r, show_strategy=False) for r in visible)}
          {"".join(render_btc5m_row(r, extra_class="row-hidden", show_strategy=False) for r in hidden)}
          {'<tr><td colspan="6" class="empty">Todavía no hay trades registrados</td></tr>' if not rows else ''}
        </tbody>
      </table>
    </div>
    {toggle_btn}
  </section>"""


def btc5m_hedge_signal_for_row(r: dict | None) -> str:
    """Same vocabulary the live script prints (BUY UP / BUY DOWN / WAIT /
    LOCKED PROFIT), derived from the decision log's own `action`/`side`
    columns so the dashboard can never drift from what the bot actually
    logged."""
    if r is None:
        return "SIN DATOS"
    action = r.get("action", "")
    if action == "BUY":
        return f"BUY {r.get('side', '')}"
    if action == "LOCKED_PROFIT":
        return "LOCKED PROFIT"
    return "WAIT"


def market_name_for_hedge_slug(slug: str | None) -> str:
    """"BTC 5min · HH:MM UTC" instead of the raw "btc-updown-5m-<epoch>"
    slug - the epoch is the window's own opening instant (see
    market_data.slug_for_window), so this needs no extra lookup."""
    if not slug:
        return "—"
    try:
        ts = int(slug.rsplit("-", 1)[-1])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return f"BTC 5min · {dt.strftime('%H:%M')} UTC"
    except (ValueError, IndexError):
        return slug


def render_btc5m_hedge_live_row(r: dict | None) -> str:
    if r is None:
        return '<tr><td colspan="8" class="empty">El bot todavia no ha corrido</td></tr>'

    def f(key: str) -> float:
        try:
            return float(r.get(key) or 0)
        except ValueError:
            return 0.0

    profit_up, profit_down, guaranteed = f("profit_up_after"), f("profit_down_after"), f("guaranteed_profit_after")
    return f"""
    <tr>
      <td class="title-cell">{esc(market_name_for_hedge_slug(r.get('window_slug')))}</td>
      <td class="num">{f('up_shares_after'):.2f}</td>
      <td class="num">{f('down_shares_after'):.2f}</td>
      <td class="num">${f('cost_after'):.2f}</td>
      <td class="num {'num-pos' if profit_up >= 0 else 'num-neg'}">${profit_up:+.2f}</td>
      <td class="num {'num-pos' if profit_down >= 0 else 'num-neg'}">${profit_down:+.2f}</td>
      <td class="num {'num-pos' if guaranteed >= 0 else 'num-neg'}">${guaranteed:+.4f}</td>
      <td class="dim">{esc(r.get('hedge_mode', ''))}</td>
    </tr>"""


def render_btc5m_hedge_order_row(o: dict) -> str:
    def f(key: str) -> float:
        try:
            return float(o.get(key) or 0)
        except ValueError:
            return 0.0

    timestamp = o.get("timestamp", "")
    time_only = timestamp.split(" ")[1] if " " in timestamp else timestamp
    side = o.get("side", "")
    try:
        qty_str = f"{float(o.get('quantity') or 0):.2f}"
    except ValueError:
        qty_str = esc(o.get("quantity", ""))
    try:
        price_str = f"${float(o.get('execution_price') or 0):.3f}"
    except ValueError:
        price_str = esc(o.get("execution_price", ""))

    order_cost = f("cost_after") - f("cost_before")
    cumulative_cost = f("cost_after")
    guaranteed_after = f("guaranteed_profit_after")

    return f"""
        <tr>
          <td class="dim">{esc(time_only)}</td>
          <td><b>{esc(side)}</b></td>
          <td class="num">{qty_str}</td>
          <td class="num">{price_str}</td>
          <td class="num">${order_cost:.2f}</td>
          <td class="num">${cumulative_cost:.2f}</td>
          <td class="num {'num-pos' if guaranteed_after >= 0 else 'num-neg'}">${guaranteed_after:+.2f}</td>
          <td class="dim">{esc(o.get('hedge_mode', ''))}</td>
        </tr>"""


def render_btc5m_hedge_window_row(r: dict, orders: list[dict], *, extra_class: str = "") -> str:
    def f(key: str) -> float:
        try:
            return float(r.get(key) or 0)
        except ValueError:
            return 0.0

    guaranteed = f("guaranteed_profit")
    realized_raw = r.get("realized_profit")
    realized_cell = "—"
    realized_class = ""
    if realized_raw not in (None, ""):
        realized = f("realized_profit")
        realized_class = "num-pos" if realized >= 0 else "num-neg"
        realized_cell = f"${realized:+.2f}"
    outcome = r.get("realized_outcome") or "?"

    orders_table = (
        "".join(render_btc5m_hedge_order_row(o) for o in orders)
        if orders
        else '<tr><td colspan="8" class="empty">Sin ordenes en esta ventana</td></tr>'
    )

    return f"""
    <tr class="{extra_class}">
      <td class="title-cell">{esc(market_name_for_hedge_slug(r.get('window_slug')))}</td>
      <td class="num">${f('total_cost'):.2f}</td>
      <td class="num">{f('up_shares'):.2f}</td>
      <td class="num">{f('down_shares'):.2f}</td>
      <td class="num {'num-pos' if guaranteed >= 0 else 'num-neg'}">${guaranteed:+.4f}</td>
      <td>{esc(outcome)}</td>
      <td class="num {realized_class}">{realized_cell}</td>
      <td class="num">{esc(r.get('number_of_orders'))}</td>
      <td class="num dim">P:{esc(r.get('number_of_profit_hedges', 0))} D:{esc(r.get('number_of_defensive_hedges', 0))} E:{esc(r.get('number_of_emergency_hedges', 0))}</td>
    </tr>
    <tr class="{extra_class}">
      <td colspan="9" style="padding: 0; border-top: none;">
        <details>
          <summary style="cursor: pointer; padding: 4px 12px 8px; color: var(--text-dim); font-size: 12px;">Ver {len(orders)} orden(es)</summary>
          <table style="width: 100%;">
            <thead><tr><th>Hora</th><th>Lado</th><th class="num">Cantidad</th><th class="num">Precio</th><th class="num">Costo orden</th><th class="num">Costo acum.</th><th class="num">Guaranteed</th><th>Modo</th></tr></thead>
            <tbody>{orders_table}</tbody>
          </table>
        </details>
      </td>
    </tr>"""


def render_btc5m_momentum_live_row(r: dict | None) -> str:
    if r is None:
        return '<tr><td colspan="7" class="empty">El bot todavia no ha corrido</td></tr>'

    def f(key: str) -> float:
        try:
            return float(r.get(key) or 0)
        except ValueError:
            return 0.0

    momentum_pct = f("momentum_pct") * 100
    side = r.get("side") or "—"
    return f"""
    <tr>
      <td class="title-cell">{esc(market_name_for_hedge_slug(r.get('window_slug')))}</td>
      <td>{esc(r.get('action', ''))}</td>
      <td><b>{esc(side)}</b></td>
      <td class="num {'num-pos' if momentum_pct >= 0 else 'num-neg'}">{momentum_pct:+.4f}%</td>
      <td class="num">{f('quantity'):.4f}</td>
      <td class="num">${f('execution_price'):.3f}</td>
      <td class="num">${f('cost'):.2f}</td>
    </tr>"""


def render_btc5m_momentum_window_row(r: dict, *, extra_class: str = "") -> str:
    def f(key: str) -> float:
        try:
            return float(r.get(key) or 0)
        except ValueError:
            return 0.0

    side = r.get("side") or "sin apuesta"
    momentum_pct = f("momentum_pct") * 100
    realized_raw = r.get("realized_profit")
    realized_cell = "—"
    realized_class = ""
    if realized_raw not in (None, ""):
        realized = f("realized_profit")
        realized_class = "num-pos" if realized >= 0 else "num-neg"
        realized_cell = f"${realized:+.2f}"
    outcome = r.get("realized_outcome") or "?"

    return f"""
    <tr class="{extra_class}">
      <td class="title-cell">{esc(market_name_for_hedge_slug(r.get('window_slug')))}</td>
      <td><b>{esc(side)}</b></td>
      <td class="num {'num-pos' if momentum_pct >= 0 else 'num-neg'}">{momentum_pct:+.4f}%</td>
      <td class="num">{f('quantity'):.4f}</td>
      <td class="num">${f('cost'):.2f}</td>
      <td>{esc(outcome)}</td>
      <td class="num {realized_class}">{realized_cell}</td>
    </tr>"""


def fmt_pct(value: str) -> str:
    if value in (None, ""):
        return "—"
    try:
        v = float(value)
    except ValueError:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def fmt_price(value: str) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):.3f}"
    except ValueError:
        return "—"


def fmt_money(value: str | None) -> str:
    try:
        num = float(value or 0)
    except ValueError:
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:,.0f}"


def main() -> None:
    btc5m_rows = read_csv(BTC5M_TRADE_LOG)
    btc5m_rows.sort(key=lambda r: r.get("date_closed", ""), reverse=True)

    # Every btc5m row opens and resolves within the same script run (see
    # scripts/run_btc5m_paper_trading.py) - there's no OPEN status here,
    # just WIN/LOSS per strategy per window, so the scoreboard is a
    # straight per-strategy rollup rather than realized/unrealized split.
    btc5m_by_strategy: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "pct_sum": 0.0, "usd_sum": 0.0})
    for r in btc5m_rows:
        d = btc5m_by_strategy[r.get("strategy", "")]
        d["n"] += 1
        try:
            pct = float(r.get("pct_return") or 0)
        except ValueError:
            pct = 0.0
        try:
            stake = float(r.get("stake_usd") or 1)
        except ValueError:
            stake = 1.0
        d["pct_sum"] += pct
        d["usd_sum"] += stake * pct / 100
        if r.get("status") == "WIN":
            d["wins"] += 1
    btc5m_rows_by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in btc5m_rows:
        btc5m_rows_by_strategy[r.get("strategy", "")].append(r)

    # BTC5m cross-side hedge bot - PAPER ONLY (see atlantis/btc5m_hedge/,
    # scripts/run_btc5m_hedge_paper_trading.py). Doesn't predict Up/Down -
    # buys both sides aiming for min(profit_if_up, profit_if_down) > 0
    # regardless of outcome. Decisions are appended in chronological
    # order (every poll, WAIT included), so the LAST row is always the
    # bot's current live state - no separate "current window" state file
    # to read.
    btc5m_hedge_decisions = read_csv(BTC5M_HEDGE_DECISIONS)
    btc5m_hedge_latest = btc5m_hedge_decisions[-1] if btc5m_hedge_decisions else None
    btc5m_hedge_orders_by_slug: dict[str, list[dict]] = defaultdict(list)
    for d in btc5m_hedge_decisions:
        if d.get("action") == "BUY":
            btc5m_hedge_orders_by_slug[d.get("window_slug", "")].append(d)
    btc5m_hedge_window_rows = read_csv(BTC5M_HEDGE_WINDOW_SUMMARY)
    btc5m_hedge_window_rows.sort(key=lambda r: r.get("window_closed_at", ""), reverse=True)
    btc5m_hedge_resolved = [r for r in btc5m_hedge_window_rows if r.get("realized_outcome")]
    btc5m_hedge_locked = [r for r in btc5m_hedge_window_rows if float(r.get("guaranteed_profit") or 0) > 0]
    btc5m_hedge_realized_sum = sum(float(r.get("realized_profit") or 0) for r in btc5m_hedge_resolved if r.get("realized_profit"))
    btc5m_hedge_visible = btc5m_hedge_window_rows[:20]
    btc5m_hedge_hidden = btc5m_hedge_window_rows[20:]

    # BTC5m momentum bot - PAPER ONLY, separate strategy from the hedge
    # bot above (see atlantis/btc5m_momentum/, added 2026-08-09). One
    # decision per window (at open), so the decisions log doubles as its
    # own "live status" the same way the hedge one does.
    btc5m_momentum_decisions = read_csv(BTC5M_MOMENTUM_DECISIONS)
    btc5m_momentum_latest = btc5m_momentum_decisions[-1] if btc5m_momentum_decisions else None
    btc5m_momentum_window_rows = read_csv(BTC5M_MOMENTUM_WINDOW_SUMMARY)
    btc5m_momentum_window_rows.sort(key=lambda r: r.get("window_closed_at", ""), reverse=True)
    btc5m_momentum_resolved = [r for r in btc5m_momentum_window_rows if r.get("realized_outcome") and r.get("side")]
    btc5m_momentum_wins = [r for r in btc5m_momentum_resolved if float(r.get("realized_profit") or 0) > 0]
    btc5m_momentum_realized_sum = sum(float(r.get("realized_profit") or 0) for r in btc5m_momentum_resolved)
    btc5m_momentum_visible = btc5m_momentum_window_rows[:20]
    btc5m_momentum_hidden = btc5m_momentum_window_rows[20:]

    now_utc = datetime.now(timezone.utc)
    now = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    now_zurich = now_utc.astimezone(ZURICH).strftime("%Y-%m-%d %H:%M")

    fonts_css = f"""
    @font-face {{
      font-family: 'Big Shoulders Display';
      font-weight: 700;
      font-style: normal;
      src: url(data:font/woff2;base64,{font_b64('BigShouldersDisplay-Bold.woff2')}) format('woff2');
      font-display: swap;
    }}
    @font-face {{
      font-family: 'IBM Plex Mono';
      font-weight: 400;
      font-style: normal;
      src: url(data:font/woff2;base64,{font_b64('IBMPlexMono-Regular.woff2')}) format('woff2');
      font-display: swap;
    }}
    @font-face {{
      font-family: 'IBM Plex Mono';
      font-weight: 500;
      font-style: normal;
      src: url(data:font/woff2;base64,{font_b64('IBMPlexMono-Medium.woff2')}) format('woff2');
      font-display: swap;
    }}
    @font-face {{
      font-family: 'IBM Plex Mono';
      font-weight: 600;
      font-style: normal;
      src: url(data:font/woff2;base64,{font_b64('IBMPlexMono-SemiBold.woff2')}) format('woff2');
      font-display: swap;
    }}
    """

    html_out = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATLANTIS — Screening en vivo</title>
<meta http-equiv="refresh" content="60">
<style>
{fonts_css}
:root {{
  --bg: #0A0E14;
  --surface: #121826;
  --surface-2: #1A2233;
  --border: #242E44;
  --text: #E7ECF5;
  --text-dim: #8C97AF;
  --accent: #E8A33D;
  --win: #34D399;
  --loss: #FB7185;
  --open: #5AA9FA;
  --wait: #6B7690;
  --closed: #2DD4BF;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--bg); overflow-x: hidden; }}
body {{
  color: var(--text);
  font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  min-height: 100vh;
  background:
    radial-gradient(1200px 400px at 15% -10%, rgba(232,163,61,0.10), transparent),
    var(--bg);
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 40px 24px 80px; }}

header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--border);
  padding-bottom: 20px;
  margin-bottom: 32px;
}}
h1 {{
  font-family: 'Big Shoulders Display', ui-sans-serif, sans-serif;
  font-weight: 700;
  font-size: 44px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  margin: 0;
  line-height: 1;
  text-wrap: balance;
}}
h1 span {{ color: var(--accent); }}
.subtitle {{ color: var(--text-dim); font-size: 13px; margin-top: 6px; }}
.updated {{
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 12px;
  color: var(--text-dim);
  text-align: right;
}}
.updated b {{ color: var(--accent); font-weight: 600; }}

.scoreboard {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 40px;
}}
.stat {{
  background: var(--surface);
  padding: 20px 22px;
}}
.stat-label {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  margin-bottom: 8px;
}}
.stat-value {{
  font-family: 'Big Shoulders Display', ui-sans-serif, sans-serif;
  font-weight: 700;
  font-size: 40px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}}
.stat-value.pos {{ color: var(--win); }}
.stat-value.neg {{ color: var(--loss); }}
.stat-value.accent {{ color: var(--accent); }}

section {{ margin-bottom: 44px; }}
.section-head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 14px;
}}
h2 {{
  font-family: 'Big Shoulders Display', ui-sans-serif, sans-serif;
  font-weight: 700;
  font-size: 22px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin: 0;
}}
.section-note {{ font-size: 12px; color: var(--text-dim); }}
.strategy-desc {{ font-size: 13px; color: var(--text); opacity: 0.85; margin: 4px 0 8px; max-width: 720px; }}

.table-scroll {{
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
}}
table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
thead th {{
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim);
  font-weight: 500;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
  white-space: nowrap;
}}
tbody td {{
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 13.5px;
  vertical-align: middle;
}}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover {{ background: var(--surface-2); }}
.title-cell {{ max-width: 320px; }}
.num {{
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  text-align: right;
}}
.num-pos {{ color: var(--win); }}
.num-neg {{ color: var(--loss); }}
.dim {{
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  color: var(--text-dim);
  font-size: 12px;
}}

.pill {{
  display: inline-block;
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid transparent;
  white-space: nowrap;
}}
.pill-copy, .pill-win {{ background: rgba(52,211,153,0.12); color: var(--win); border-color: rgba(52,211,153,0.35); }}
.pill-loss {{ background: rgba(251,113,133,0.12); color: var(--loss); border-color: rgba(251,113,133,0.35); }}
.pill-open, .pill-wait {{ background: rgba(90,169,250,0.12); color: var(--open); border-color: rgba(90,169,250,0.35); }}
.pill-conflict {{ background: rgba(232,163,61,0.14); color: var(--accent); border-color: rgba(232,163,61,0.4); }}
.pill-ignore {{ background: rgba(107,118,144,0.14); color: var(--wait); border-color: rgba(107,118,144,0.35); }}
.pill-warn {{ background: rgba(232,163,61,0.14); color: var(--accent); border-color: rgba(232,163,61,0.4); }}
.pill-closed {{ background: rgba(45,212,191,0.14); color: var(--closed); border-color: rgba(45,212,191,0.4); }}

.tabs {{
  display: flex;
  gap: 8px;
  margin-bottom: 28px;
}}
.tab-btn {{
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 18px;
  cursor: pointer;
}}
.tab-btn.active {{ color: var(--accent); border-color: var(--accent); background: rgba(232,163,61,0.08); }}
.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; }}

tr.row-hidden {{ display: none; }}
table.show-all tr.row-hidden {{ display: table-row; }}
#toggle-history-btn {{ margin-top: 12px; }}

.empty {{ padding: 28px; text-align: center; color: var(--text-dim); font-size: 13px; }}

footer {{
  margin-top: 40px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-dim);
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}}
footer a {{ color: var(--accent); text-decoration: none; }}
footer a:hover {{ text-decoration: underline; }}

@media (max-width: 720px) {{
  .scoreboard {{ grid-template-columns: repeat(2, 1fr); }}
  h1 {{ font-size: 32px; }}
  div[style*="grid-template-columns: 1fr 1fr"] {{ grid-template-columns: 1fr !important; }}
}}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <h1>ATLANTIS <span>SCREENING</span></h1>
      <div class="subtitle">BTC "Up or Down 5m" · Polymarket</div>
    </div>
    <div class="updated">Última corrida (UTC)<br><b>{esc(now)}</b><br>Hora Zúrich<br><b>{esc(now_zurich)}</b></div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" data-tab="btc5m-hedge">BTC 5m (Hedge)</button>
    <button class="tab-btn" data-tab="btc5m-momentum">BTC 5m (Momentum)</button>
    <button class="tab-btn" data-tab="btc5m">BTC 5m (Paper)</button>
  </div>

  <div class="tab-panel" data-tab-panel="btc5m">

  <section>
    <div class="section-head">
      <h2>BTC "Up or Down 5m" — 4 estrategias (paper trading, sin dinero real)</h2>
      <span class="section-note">A/B/D: $1 por señal (una entrada) · E: $0.20 por entrada, varias por ventana, inspirada en una wallet real que opera estos mercados · cada ventana abre y resuelve en el mismo ciclo</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Estrategia</th><th class="num">Trades</th><th class="num">Win rate</th>
            <th class="num">Retorno promedio</th><th class="num">USD total</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_btc5m_strategy_row(name, stats) for name, stats in btc5m_by_strategy.items()) or '<tr><td colspan="5" class="empty">Todavía no hay resultados</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  {"".join(render_btc5m_strategy_section(name, btc5m_by_strategy.get(name, {"n": 0, "wins": 0, "pct_sum": 0.0, "usd_sum": 0.0}), btc5m_rows_by_strategy.get(name, [])) for name in STRATEGY_LABELS)}

  </div>

  <div class="tab-panel active" data-tab-panel="btc5m-hedge">

  <section>
    <div class="section-head">
      <h2>BTC "Up or Down 5m" — hedge Up+Down (paper trading, sin dinero real)</h2>
      <span class="section-note">No predice dirección: compra Up y Down buscando que el payout garantizado (min(up_shares, down_shares) a $1/share) supere el costo total sin importar el resultado — reverse-engineered de una wallet real 2026-08-08 · primera versión, solo paper, sin credenciales ni órdenes reales</span>
    </div>
  </section>

  <div class="scoreboard">
    <div class="stat">
      <div class="stat-label">Señal actual</div>
      <div class="stat-value {'pos' if btc5m_hedge_latest and btc5m_hedge_latest.get('action') in ('BUY', 'LOCKED_PROFIT') else ''}">{esc(btc5m_hedge_signal_for_row(btc5m_hedge_latest))}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Ventana actual</div>
      <div class="stat-value">{esc(btc5m_hedge_latest.get('window_slug')) if btc5m_hedge_latest else '—'}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Guaranteed profit (en vivo)</div>
      <div class="stat-value {'pos' if btc5m_hedge_latest and float(btc5m_hedge_latest.get('guaranteed_profit_after') or 0) >= 0 else 'neg'}">${float(btc5m_hedge_latest.get('guaranteed_profit_after') or 0) if btc5m_hedge_latest else 0:+.4f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Ventanas con hedge logrado</div>
      <div class="stat-value">{len(btc5m_hedge_locked)} / {len(btc5m_hedge_window_rows)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Profit realizado (resueltas)</div>
      <div class="stat-value {'pos' if btc5m_hedge_realized_sum >= 0 else 'neg'}">${btc5m_hedge_realized_sum:+.2f}</div>
    </div>
  </div>

  <section>
    <div class="section-head">
      <h2>Estado en vivo</h2>
      <span class="section-note">{f"última actualización {esc(btc5m_hedge_latest.get('timestamp'))} · {esc(btc5m_hedge_latest.get('reason', ''))}" if btc5m_hedge_latest else 'el bot todavia no ha corrido'}</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Mercado</th><th class="num">UP shares</th><th class="num">DOWN shares</th><th class="num">Costo total</th>
          <th class="num">P/L si UP</th><th class="num">P/L si DOWN</th><th class="num">Guaranteed</th><th>Modo</th></tr>
        </thead>
        <tbody>
          {render_btc5m_hedge_live_row(btc5m_hedge_latest)}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Historial de ventanas</h2>
      <span class="section-note">{len(btc5m_hedge_window_rows)} ventanas registradas · últimas 20 visibles · ordenado por cierre</span>
    </div>
    <div class="table-scroll">
      <table id="history-table-btc5m-hedge">
        <thead>
          <tr>
            <th>Ventana</th><th class="num">Costo</th><th class="num">UP sh</th><th class="num">DOWN sh</th>
            <th class="num">Guaranteed</th><th>Resultado</th><th class="num">Profit real</th><th class="num">Órdenes</th><th>Modos</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_btc5m_hedge_window_row(r, btc5m_hedge_orders_by_slug.get(r.get('window_slug', ''), [])) for r in btc5m_hedge_visible)}
          {"".join(render_btc5m_hedge_window_row(r, btc5m_hedge_orders_by_slug.get(r.get('window_slug', ''), []), extra_class="row-hidden") for r in btc5m_hedge_hidden)}
          {'<tr><td colspan="9" class="empty">Todavía no hay ventanas registradas</td></tr>' if not btc5m_hedge_window_rows else ''}
        </tbody>
      </table>
    </div>
    {f'<button class="tab-btn" id="toggle-history-btn-btc5m-hedge" data-count="{len(btc5m_hedge_window_rows)}" data-label="Ver todas">Ver todas ({len(btc5m_hedge_window_rows)})</button>' if btc5m_hedge_hidden else ''}
  </section>

  </div>

  <div class="tab-panel" data-tab-panel="btc5m-momentum">

  <section>
    <div class="section-head">
      <h2>BTC "Up or Down 5m" — Momentum direccional (paper trading, sin dinero real)</h2>
      <span class="section-note">Toma riesgo direccional a propósito (a diferencia del bot de hedge) - una sola entrada por ventana, basada en el momentum de BTC de los 3 minutos antes de que abra · reverse-engineered de la wallet 0x3048...e7537 2026-08-09 · $2 por apuesta, corte de sesion en -$20</span>
    </div>
  </section>

  <div class="scoreboard">
    <div class="stat">
      <div class="stat-label">Ultima decision</div>
      <div class="stat-value {'pos' if btc5m_momentum_latest and btc5m_momentum_latest.get('action') == 'BET' else ''}">{esc(btc5m_momentum_latest.get('action')) if btc5m_momentum_latest else '—'}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Ventana actual</div>
      <div class="stat-value">{esc(btc5m_momentum_latest.get('window_slug')) if btc5m_momentum_latest else '—'}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Ventanas apostadas (de las resueltas)</div>
      <div class="stat-value">{len(btc5m_momentum_wins)} / {len(btc5m_momentum_resolved)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Profit realizado (resueltas)</div>
      <div class="stat-value {'pos' if btc5m_momentum_realized_sum >= 0 else 'neg'}">${btc5m_momentum_realized_sum:+.2f}</div>
    </div>
  </div>

  <section>
    <div class="section-head">
      <h2>Estado en vivo</h2>
      <span class="section-note">{f"última actualización {esc(btc5m_momentum_latest.get('timestamp'))} · {esc(btc5m_momentum_latest.get('reason', ''))}" if btc5m_momentum_latest else 'el bot todavia no ha corrido'}</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Mercado</th><th>Accion</th><th>Lado</th><th class="num">Momentum</th>
          <th class="num">Cantidad</th><th class="num">Precio</th><th class="num">Costo</th></tr>
        </thead>
        <tbody>
          {render_btc5m_momentum_live_row(btc5m_momentum_latest)}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Historial de ventanas</h2>
      <span class="section-note">{len(btc5m_momentum_window_rows)} ventanas registradas · últimas 20 visibles · ordenado por cierre</span>
    </div>
    <div class="table-scroll">
      <table id="history-table-btc5m-momentum">
        <thead>
          <tr>
            <th>Ventana</th><th>Lado</th><th class="num">Momentum</th><th class="num">Cantidad</th>
            <th class="num">Costo</th><th>Resultado</th><th class="num">Profit real</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_btc5m_momentum_window_row(r) for r in btc5m_momentum_visible)}
          {"".join(render_btc5m_momentum_window_row(r, extra_class="row-hidden") for r in btc5m_momentum_hidden)}
          {'<tr><td colspan="7" class="empty">Todavía no hay ventanas registradas</td></tr>' if not btc5m_momentum_window_rows else ''}
        </tbody>
      </table>
    </div>
    {f'<button class="tab-btn" id="toggle-history-btn-btc5m-momentum" data-count="{len(btc5m_momentum_window_rows)}" data-label="Ver todas">Ver todas ({len(btc5m_momentum_window_rows)})</button>' if btc5m_momentum_hidden else ''}
  </section>

  </div>

  <footer>
    <span>Generado automáticamente por un cron en VPS cada 2 min.</span>
    <a href="https://github.com/giuseppemineo685-beep/atlantis-polymarket-screening" target="_blank">Ver repositorio</a>
  </footer>

</div>
<script>
document.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tab-btn[data-tab]').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.tabPanel === tab));
  }});
}});

function wireHistoryToggle(btnId, tableId) {{
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.addEventListener('click', () => {{
    const table = document.getElementById(tableId);
    const showingAll = table.classList.toggle('show-all');
    btn.textContent = showingAll ? 'Ver menos' : `${{btn.dataset.label}} (${{btn.dataset.count}})`;
  }});
}}
wireHistoryToggle('toggle-history-btn-btc5m-hedge', 'history-table-btc5m-hedge');
wireHistoryToggle('toggle-history-btn-btc5m-momentum', 'history-table-btc5m-momentum');
{"".join(f"wireHistoryToggle('toggle-history-btn-btc5m-{name}', 'history-table-btc5m-{name}');" + chr(10) for name in STRATEGY_LABELS)}
</script>
</body>
</html>
"""

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_out)
    print(f"Dashboard generado: {OUT_PATH}")


if __name__ == "__main__":
    main()
