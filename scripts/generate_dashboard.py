from __future__ import annotations

import base64
import csv
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
FONTS = Path(__file__).resolve().parent / "fonts"
GRID_TRADER_POSITIONS = ROOT / "outputs" / "grid_trader_positions.json"
GRID_TRADER_LOG = ROOT / "outputs" / "grid_trader_paper_log.csv"
FOREX_TRADER_POSITIONS = ROOT / "outputs" / "forex_trader_positions.json"
FOREX_TRADER_LOG = ROOT / "outputs" / "forex_trader_paper_log.csv"
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


def read_grid_trader_positions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def grid_trader_position_total(pos: dict) -> Decimal:
    levels = [Decimal(x) for x in pos["levels"]]
    open_qty = [Decimal(x) for x in pos["open_qty"]]
    last_price = Decimal(pos["last_price"])
    unrealized = sum(q * (last_price - lvl) for q, lvl in zip(open_qty, levels))
    total = Decimal(pos["realized"]) + unrealized
    if pos["strategy"] == "trend":
        total += Decimal(pos.get("trend_day_realized", "0"))
    return total


def render_grid_trader_levels_detail(pos: dict) -> str:
    levels = [Decimal(x) for x in pos["levels"]]
    open_qty = [Decimal(x) for x in pos["open_qty"]]
    last_price = Decimal(pos["last_price"])
    rows = []
    for i, (level, qty) in enumerate(zip(levels, open_qty)):
        filled = qty > 0
        unrealized = qty * (last_price - level) if filled else Decimal(0)
        rows.append(f"""
          <tr>
            <td class="dim">#{i}</td>
            <td class="num">${level:,.8f}</td>
            <td>{'<span class="pill pill-open">llena</span>' if filled else '<span class="pill pill-ignore">vacia</span>'}</td>
            <td class="num">{f'{qty:.6f}' if filled else '—'}</td>
            <td class="num {'num-pos' if unrealized >= 0 else 'num-neg'}">{f'${unrealized:+,.2f}' if filled else '—'}</td>
          </tr>""")
    return f"""
    <tr>
      <td colspan="9" style="padding:0;border-bottom:none">
        <details>
          <summary style="cursor:pointer;padding:8px 14px;color:var(--text-dim);font-size:12px">Ver los {len(levels)} niveles del grid</summary>
          <div class="table-scroll" style="margin:0 14px 12px">
            <table>
              <thead><tr><th>#</th><th class="num">Precio nivel</th><th>Estado</th><th class="num">Cantidad</th><th class="num">No realizado</th></tr></thead>
              <tbody>{"".join(rows)}</tbody>
            </table>
          </div>
        </details>
      </td>
    </tr>"""


def render_grid_trader_position_row(pos: dict) -> str:
    total = grid_trader_position_total(pos)
    open_positions = sum(1 for x in pos["open_qty"] if Decimal(x) > 0)
    entry_price = pos.get("entry_price", pos["last_price"])
    return f"""
    <tr>
      <td class="title-cell"><b>{esc(pos['symbol'])}</b></td>
      <td>{esc(pos['strategy'])}</td>
      <td class="dim">{esc(pos['opened_at'])}</td>
      <td class="num">${float(entry_price):,.6g}</td>
      <td class="num">${float(pos['last_price']):,.6g}</td>
      <td class="num">{open_positions}</td>
      <td class="num">{pos['trades']}</td>
      <td class="num {'num-pos' if total >= 0 else 'num-neg'}">${total:+,.2f}</td>
      <td class="num">${float(pos['take_profit_usd']):.0f} / -${float(pos['stop_loss_usd']):.0f}</td>
    </tr>{render_grid_trader_levels_detail(pos)}"""


GRID_TRADER_ACTION_PILL = {
    "OPEN": "pill-open", "CLOSE": "pill-closed", "DAY_REANCHOR": "pill-wait",
    "SKIP_REANCHOR": "pill-warn", "FILL_BUY": "pill-open",
}


def render_grid_trader_log_row(r: dict, *, extra_class: str = "") -> str:
    def f(key: str) -> float:
        try:
            return float(r.get(key) or 0)
        except ValueError:
            return 0.0

    action = r.get("action") or ""
    if action == "FILL_SELL":
        pill_class = "pill-win" if f("realized_profit") >= 0 else "pill-loss"
    else:
        pill_class = GRID_TRADER_ACTION_PILL.get(action, "pill-wait")
    realized_raw = r.get("realized_profit")
    realized_cell = "—"
    realized_class = ""
    if realized_raw not in (None, ""):
        realized = f("realized_profit")
        realized_class = "num-pos" if realized >= 0 else "num-neg"
        realized_cell = f"${realized:+.2f}"

    return f"""
    <tr class="{extra_class}">
      <td class="dim">{esc(r.get('timestamp'))}</td>
      <td class="title-cell"><b>{esc(r.get('symbol'))}</b></td>
      <td>{esc(r.get('strategy'))}</td>
      <td><span class="pill {pill_class}">{esc(action)}</span></td>
      <td class="dim">{esc(r.get('reason'))}</td>
      <td class="num {realized_class}">{realized_cell}</td>
    </tr>"""


def main() -> None:
    # Unified grid trader (flat + trend, one bot) - see
    # atlantis/grid_trader/ and scripts/run_grid_trader_paper.py, added
    # 2026-08-16. Positions read from the bot's own persisted state
    # (outputs/grid_trader_positions.json), marked to market using
    # last_price from the bot's own last poll (not a fresh fetch here -
    # the dashboard generator stays network-free).
    grid_trader_positions = read_grid_trader_positions(GRID_TRADER_POSITIONS)
    grid_trader_positions.sort(key=lambda p: grid_trader_position_total(p), reverse=True)
    grid_trader_total = sum(grid_trader_position_total(p) for p in grid_trader_positions)
    grid_trader_flat_n = sum(1 for p in grid_trader_positions if p["strategy"] == "flat")
    grid_trader_trend_n = sum(1 for p in grid_trader_positions if p["strategy"] == "trend")
    grid_trader_log_rows = read_csv(GRID_TRADER_LOG)
    grid_trader_log_sorted = sorted(grid_trader_log_rows, key=lambda r: r.get("timestamp", ""), reverse=True)
    grid_trader_closed = [r for r in grid_trader_log_rows if r.get("action") == "CLOSE"]
    grid_trader_closed_wins = [r for r in grid_trader_closed if float(r.get("realized_profit") or 0) > 0]
    grid_trader_closed_pnl = sum(float(r.get("realized_profit") or 0) for r in grid_trader_closed)
    grid_trader_log_visible = grid_trader_log_sorted[:40]
    grid_trader_log_hidden = grid_trader_log_sorted[40:]

    # Forex grid trader (flat + trend, OANDA practice account, paper
    # only) - see atlantis/forex/ and scripts/run_forex_grid_trader_paper.py,
    # added 2026-08-16. Same position/log shape as the crypto bot, so the
    # crypto render functions above are reused unchanged.
    forex_trader_positions = read_grid_trader_positions(FOREX_TRADER_POSITIONS)
    forex_trader_positions.sort(key=lambda p: grid_trader_position_total(p), reverse=True)
    forex_trader_total = sum(grid_trader_position_total(p) for p in forex_trader_positions)
    forex_trader_flat_n = sum(1 for p in forex_trader_positions if p["strategy"] == "flat")
    forex_trader_trend_n = sum(1 for p in forex_trader_positions if p["strategy"] == "trend")
    forex_trader_log_rows = read_csv(FOREX_TRADER_LOG)
    forex_trader_log_sorted = sorted(forex_trader_log_rows, key=lambda r: r.get("timestamp", ""), reverse=True)
    forex_trader_closed = [r for r in forex_trader_log_rows if r.get("action") == "CLOSE"]
    forex_trader_closed_wins = [r for r in forex_trader_closed if float(r.get("realized_profit") or 0) > 0]
    forex_trader_closed_pnl = sum(float(r.get("realized_profit") or 0) for r in forex_trader_closed)
    forex_trader_log_visible = forex_trader_log_sorted[:40]
    forex_trader_log_hidden = forex_trader_log_sorted[40:]

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
    <button class="tab-btn active" data-tab="grid-trader">Grid trader (Binance)</button>
    <button class="tab-btn" data-tab="forex-trader">Forex (OANDA demo)</button>
  </div>

  <div class="tab-panel active" data-tab-panel="grid-trader">

  <section>
    <div class="section-head">
      <h2>Grid trader (Binance) — plano + tendencia, un solo bot (paper trading, sin dinero real)</h2>
      <span class="section-note">Escanea el universo cada 15 min, clasifica el regimen de cada par y abre grid plano o de tendencia segun corresponda - gate de BTC + stop-loss 35% + take-profit 10% + filtro posicion 20-80%/correlacion BTC &lt;0.5, todo calibrado contra abr-jul 2026 reales (ver docs/GRID_TRADER_STRATEGIES.md)</span>
    </div>
  </section>

  <div class="scoreboard">
    <div class="stat">
      <div class="stat-label">Posiciones abiertas</div>
      <div class="stat-value">{len(grid_trader_positions)} <span class="dim" style="font-size:16px">({grid_trader_flat_n} plano / {grid_trader_trend_n} tendencia)</span></div>
    </div>
    <div class="stat">
      <div class="stat-label">Total no realizado (abiertas)</div>
      <div class="stat-value {'pos' if grid_trader_total >= 0 else 'neg'}">${grid_trader_total:+,.2f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Cerradas (ganadas/total)</div>
      <div class="stat-value">{len(grid_trader_closed_wins)} / {len(grid_trader_closed)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">PnL realizado (cerradas)</div>
      <div class="stat-value {'pos' if grid_trader_closed_pnl >= 0 else 'neg'}">${grid_trader_closed_pnl:+,.2f}</div>
    </div>
  </div>

  <section>
    <div class="section-head">
      <h2>Posiciones abiertas</h2>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Par</th><th>Estrategia</th><th>Abierta</th><th class="num">Precio entrada</th><th class="num">Precio actual</th>
            <th class="num">Niveles activos</th><th class="num">Trades</th><th class="num">Total</th><th>TP / SL</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_grid_trader_position_row(p) for p in grid_trader_positions)}
          {'<tr><td colspan="9" class="empty">Sin posiciones abiertas</td></tr>' if not grid_trader_positions else ''}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Historial de eventos</h2>
      <span class="section-note">{len(grid_trader_log_rows)} eventos registrados · ultimos 40 visibles</span>
    </div>
    <div class="table-scroll">
      <table id="history-table-grid-trader">
        <thead>
          <tr>
            <th>Fecha (UTC)</th><th>Par</th><th>Estrategia</th><th>Accion</th><th>Motivo</th><th class="num">Profit</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_grid_trader_log_row(r) for r in grid_trader_log_visible)}
          {"".join(render_grid_trader_log_row(r, extra_class="row-hidden") for r in grid_trader_log_hidden)}
          {'<tr><td colspan="6" class="empty">Todavia no hay eventos registrados</td></tr>' if not grid_trader_log_rows else ''}
        </tbody>
      </table>
    </div>
    {f'<button class="tab-btn" id="toggle-history-btn-grid-trader" data-count="{len(grid_trader_log_rows)}" data-label="Ver todos">Ver todos ({len(grid_trader_log_rows)})</button>' if grid_trader_log_hidden else ''}
  </section>

  <section>
    <div class="section-head">
      <h2>Historial de backtests (walk-forward, sin look-ahead)</h2>
      <span class="section-note">Config final: gate BTC + stop-loss 35% + take-profit 10% + filtro posicion 20-80%/correlacion BTC &lt;0.5 - clasificado en un punto T0 pasado, simulado hacia adelante, sin usar datos futuros · detalle completo en docs/GRID_TRADER_STRATEGIES.md</span>
    </div>
    <div class="strategy-desc">Grid plano - 4 de 4 meses positivos:</div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Mes (T0)</th><th class="num">Candidatos</th><th class="num">Win rate</th><th class="num">PnL</th><th class="num">ROI</th></tr>
        </thead>
        <tbody>
          <tr><td class="title-cell">Abril 2026</td><td class="num">9</td><td class="num">100%</td><td class="num num-pos">+$367.05</td><td class="num num-pos">+10.2%</td></tr>
          <tr><td class="title-cell">Mayo 2026</td><td class="num">25</td><td class="num">72.0%</td><td class="num num-pos">+$343.32</td><td class="num num-pos">+3.43%</td></tr>
          <tr><td class="title-cell">Junio 2026 <span class="dim">(mes de correccion BTC -20%)</span></td><td class="num">21</td><td class="num">61.9%</td><td class="num num-pos">+$97.96</td><td class="num num-pos">+1.17%</td></tr>
          <tr><td class="title-cell">Julio 2026</td><td class="num">15</td><td class="num">80.0%</td><td class="num num-pos">+$208.85</td><td class="num num-pos">+3.48%</td></tr>
        </tbody>
      </table>
    </div>
    <div class="strategy-desc" style="margin-top:16px">Grid de tendencia - misma ventana, muestra mas chica, sin el mismo nivel de optimizacion todavia:</div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Mes (T0)</th><th class="num">Candidatos</th><th class="num">Win rate</th><th class="num">ROI</th></tr>
        </thead>
        <tbody>
          <tr><td class="title-cell">Abril 2026</td><td class="num">0</td><td class="num dim">—</td><td class="num dim">—</td></tr>
          <tr><td class="title-cell">Mayo 2026</td><td class="num">1</td><td class="num">100%</td><td class="num num-pos">+2.77%</td></tr>
          <tr><td class="title-cell">Junio 2026</td><td class="num">9</td><td class="num">66.7%</td><td class="num num-pos">+5.23%</td></tr>
          <tr><td class="title-cell">Julio 2026</td><td class="num">1</td><td class="num num-neg">0%</td><td class="num num-neg">-2.16%</td></tr>
        </tbody>
      </table>
    </div>
    <div class="strategy-desc" style="margin-top:16px">
      El score compuesto original (rubrica del screener manual) no predijo el resultado real (correlacion +0.03, ruido) - se reemplazo por dos features que si correlacionan: posicion en el rango (30d) y correlacion con BTC. Confirmado con abril como holdout genuino (nunca usado para calibrar el filtro): ROI +6.26%→+10.2%, win rate 86.7%→100%.
    </div>
  </section>

  </div>

  <div class="tab-panel" data-tab-panel="forex-trader">

  <section>
    <div class="section-head">
      <h2>Forex (OANDA demo) — plano + tendencia, mismo motor que el grid de cripto (paper trading, cuenta practice de OANDA, sin dinero real)</h2>
      <span class="section-note">Mismo motor que Grid trader (Binance), adaptado a forex: cierra el mercado vie 21:00 - dom 21:00 UTC, sin filtro de correlacion ni gate de mercado todavia (hallazgos especificos de cripto, no validados aun para forex) - take-profit 3% / stop-loss 10%, sin calibrar todavia con el mismo rigor que cripto (ver atlantis/forex/config.yaml)</span>
    </div>
  </section>

  <div class="scoreboard">
    <div class="stat">
      <div class="stat-label">Posiciones abiertas</div>
      <div class="stat-value">{len(forex_trader_positions)} <span class="dim" style="font-size:16px">({forex_trader_flat_n} plano / {forex_trader_trend_n} tendencia)</span></div>
    </div>
    <div class="stat">
      <div class="stat-label">Total no realizado (abiertas)</div>
      <div class="stat-value {'pos' if forex_trader_total >= 0 else 'neg'}">${forex_trader_total:+,.2f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Cerradas (ganadas/total)</div>
      <div class="stat-value">{len(forex_trader_closed_wins)} / {len(forex_trader_closed)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">PnL realizado (cerradas)</div>
      <div class="stat-value {'pos' if forex_trader_closed_pnl >= 0 else 'neg'}">${forex_trader_closed_pnl:+,.2f}</div>
    </div>
  </div>

  <section>
    <div class="section-head">
      <h2>Posiciones abiertas</h2>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Par</th><th>Estrategia</th><th>Abierta</th><th class="num">Precio entrada</th><th class="num">Precio actual</th>
            <th class="num">Niveles activos</th><th class="num">Trades</th><th class="num">Total</th><th>TP / SL</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_grid_trader_position_row(p) for p in forex_trader_positions)}
          {'<tr><td colspan="9" class="empty">Sin posiciones abiertas</td></tr>' if not forex_trader_positions else ''}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Historial de eventos</h2>
      <span class="section-note">{len(forex_trader_log_rows)} eventos registrados · ultimos 40 visibles</span>
    </div>
    <div class="table-scroll">
      <table id="history-table-forex-trader">
        <thead>
          <tr>
            <th>Fecha (UTC)</th><th>Par</th><th>Estrategia</th><th>Accion</th><th>Motivo</th><th class="num">Profit</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_grid_trader_log_row(r) for r in forex_trader_log_visible)}
          {"".join(render_grid_trader_log_row(r, extra_class="row-hidden") for r in forex_trader_log_hidden)}
          {'<tr><td colspan="6" class="empty">Todavia no hay eventos registrados</td></tr>' if not forex_trader_log_rows else ''}
        </tbody>
      </table>
    </div>
    {f'<button class="tab-btn" id="toggle-history-btn-forex-trader" data-count="{len(forex_trader_log_rows)}" data-label="Ver todos">Ver todos ({len(forex_trader_log_rows)})</button>' if forex_trader_log_hidden else ''}
  </section>

  <section>
    <div class="section-head">
      <h2>Backtesting</h2>
      <span class="section-note">Walk-forward sin look-ahead, mismo motor y metodologia que cripto (ver scripts/backtest_forex_walkforward.py)</span>
    </div>
    <div class="strategy-desc">
      Todavia en etapa inicial: se corrio un walk-forward de 30 dias contra el universo completo de OANDA para validar que el pipeline funciona con datos reales, pero no se hizo el mismo barrido de calibracion que cripto (que uso 4 meses reales para fijar el gate de BTC, los thresholds de TP/SL y el filtro de posicion/correlacion). Se confirmo que los thresholds originales de cripto (10%/35%) practicamente nunca se activan en forex porque los movimientos de PnL absoluto son mucho mas chicos - se ajusto a 3%/10% como primer intento, sin validar todavia con el mismo rigor. Faltan: barrido de thresholds contra varios meses reales, una investigacion de que features predicen resultado real en forex (el equivalente a posicion-en-rango/correlacion-BTC de cripto), y un gate de mercado (equivalente al gate de BTC).
    </div>
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
wireHistoryToggle('toggle-history-btn-grid-trader', 'history-table-grid-trader');
wireHistoryToggle('toggle-history-btn-forex-trader', 'history-table-forex-trader');
</script>
</body>
</html>
"""

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_out)
    print(f"Dashboard generado: {OUT_PATH}")


if __name__ == "__main__":
    main()
