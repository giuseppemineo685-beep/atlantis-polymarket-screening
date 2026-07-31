from __future__ import annotations

import base64
import csv
import html
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
FONTS = Path(__file__).resolve().parent / "fonts"
TRADE_LOG = ROOT / "outputs" / "trade_log.csv"
SIGNALS = ROOT / "outputs" / "active_portfolio_signals.csv"
TRADERS = ROOT / "outputs" / "traders.csv"
PERFORMANCE = ROOT / "outputs" / "trader_performance.csv"
ELON_TRADERS = ROOT / "outputs" / "elon_traders.csv"
OUT_PATH = ROOT / "docs" / "index.html"

PROFILE_URL = "https://polymarket.com/profile/{wallet}"
MARKET_URL = "https://polymarket.com/market/{slug}"

ACTION_ORDER = {"COPY": 0, "WAIT": 1, "CONFLICT": 2, "IGNORE": 3}
STATUS_ORDER = {"OPEN": 0, "CLOSED": 1, "WIN": 2, "LOSS": 3}
ZURICH = ZoneInfo("Europe/Zurich")


def parse_utc(date_str: str) -> datetime | None:
    """Parse our "YYYY-MM-DD HH:MM UTC" timestamps into an aware datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.replace(" UTC", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def zurich_day(date_str: str) -> str:
    dt = parse_utc(date_str)
    if dt is None:
        return "?"
    return dt.astimezone(ZURICH).strftime("%Y-%m-%d")


def font_b64(name: str) -> str:
    return base64.b64encode((FONTS / name).read_bytes()).decode()


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


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


def market_link(title: str, slug: str) -> str:
    title_html = esc(title)
    if not slug:
        return title_html
    url = esc(MARKET_URL.format(slug=slug))
    return f'<a href="{url}" target="_blank" rel="noopener">{title_html}</a>'


def render_signal_row(row: dict) -> str:
    action = row.get("action", "IGNORE")
    return f"""
    <tr>
      <td><span class="pill pill-{action.lower()}">{esc(action)}</span></td>
      <td class="title-cell">{market_link(row.get('title'), row.get('slug'))}</td>
      <td><b>{esc(row.get('outcome'))}</b></td>
      <td class="num">{fmt_price(row.get('current_price'))}</td>
      <td class="num">{esc(row.get('supporting_traders'))}</td>
      <td class="num">{esc(row.get('conviction'))}</td>
      <td class="num">${esc(row.get('stake'))}</td>
    </tr>"""


def render_log_row(row: dict) -> str:
    status = row.get("status", "OPEN")
    consensus_active = row.get("consensus_active", "yes") == "yes"
    pct_return = row.get("pct_return", "")

    # For OPEN trades, pct_return in the CSV is only set once the trade
    # closes/resolves - it's blank the whole time it's open, which hid
    # unrealized losses entirely from this table (they only showed up in
    # the live Telegram alert text). Compute a live mark-to-market % here
    # too, so an open position that's currently underwater shows red.
    is_live_estimate = False
    if pct_return in (None, ""):
        try:
            entry = float(row.get("entry_price") or 0)
            current = float(row.get("current_price") or 0)
            if entry > 0:
                pct_return = f"{(current / entry - 1) * 100:.2f}"
                is_live_estimate = True
        except ValueError:
            pct_return = ""

    return_class = ""
    if pct_return not in (None, ""):
        try:
            return_class = "num-pos" if float(pct_return) >= 0 else "num-neg"
        except ValueError:
            return_class = ""

    if status == "OPEN" and not consensus_active:
        status_badge = '<span class="pill pill-warn">SIN CONSENSO</span>'
    elif status == "CLOSED":
        status_badge = '<span class="pill pill-closed">CERRADO (salida temprana)</span>'
    else:
        status_badge = f'<span class="pill pill-{status.lower()}">{esc(status)}</span>'

    return_suffix = "*" if is_live_estimate else ""

    return f"""
    <tr>
      <td>{status_badge}</td>
      <td class="title-cell">{market_link(row.get('title'), row.get('slug'))}</td>
      <td><b>{esc(row.get('outcome'))}</b></td>
      <td class="num">{fmt_price(row.get('entry_price'))}</td>
      <td class="num">{fmt_price(row.get('exit_price') or row.get('current_price'))}</td>
      <td class="num {return_class}">{fmt_pct(pct_return)}{return_suffix}</td>
      <td class="dim">{esc(row.get('date_first_seen'))}</td>
      <td class="dim">{esc(row.get('traders'))}</td>
    </tr>"""


VERDICT_PILL = {
    "WATCHLIST_STRONG": "win",
    "WATCHLIST": "open",
    "PAPER_ONLY": "wait",
    "REJECT": "loss",
}


def render_trader_row(row: dict) -> str:
    wallet = row.get("wallet", "")
    username = row.get("username") or "(sin username)"
    profile = esc(PROFILE_URL.format(wallet=wallet))
    verdict = row.get("verdict", "")
    pill_class = VERDICT_PILL.get(verdict, "ignore")
    short_wallet = f"{wallet[:6]}…{wallet[-4:]}" if len(wallet) > 12 else wallet
    return f"""
    <tr>
      <td><b>{esc(row.get('label'))}</b></td>
      <td><a href="{profile}" target="_blank" rel="noopener">{esc(username)}</a></td>
      <td class="dim">{esc(short_wallet)}</td>
      <td><span class="pill pill-{pill_class}">{esc(verdict)}</span></td>
      <td class="dim">{esc(row.get('status'))}</td>
    </tr>"""


def render_elon_candidate_row(row: dict) -> str:
    wallet = row.get("wallet_address", "")
    username = row.get("username") or "(sin username)"
    profile = esc(PROFILE_URL.format(wallet=wallet))
    verdict = row.get("verdict", "")
    pill_class = VERDICT_PILL.get(verdict, "ignore")
    short_wallet = f"{wallet[:6]}…{wallet[-4:]}" if len(wallet) > 12 else wallet
    return f"""
    <tr>
      <td><a href="{profile}" target="_blank" rel="noopener">{esc(username)}</a></td>
      <td class="dim">{esc(short_wallet)}</td>
      <td><span class="pill pill-{pill_class}">{esc(verdict)}</span></td>
      <td class="num">{esc(row.get('copy_score'))}</td>
      <td class="num">{fmt_money(row.get('elon_volume'))}</td>
      <td class="num dim">{esc(row.get('elon_trades'))}</td>
      <td class="num dim">{esc(row.get('risk_score'))}</td>
    </tr>"""


FLAG_PILL = {
    "DECLINING": "loss",
    "LOW_SAMPLE": "wait",
    "OK": "win",
}


def fmt_money(value: str | None) -> str:
    try:
        num = float(value or 0)
    except ValueError:
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:,.0f}"


def render_performance_row(row: dict) -> str:
    flag = row.get("flag", "")
    pill_class = FLAG_PILL.get(flag, "wait")
    pnl_7d = row.get("pnl_7d")
    pnl_class = "num-pos" if (pnl_7d and float(pnl_7d) >= 0) else "num-neg"
    pnl_30d = row.get("pnl_30d")
    pnl_30d_class = "num-pos" if (pnl_30d and float(pnl_30d) >= 0) else "num-neg"
    return f"""
    <tr>
      <td><b>{esc(row.get('label'))}</b></td>
      <td><span class="pill pill-{pill_class}">{esc(flag)}</span></td>
      <td class="num {pnl_class}">{fmt_money(pnl_7d)}</td>
      <td class="num">{fmt_pct(row.get('win_rate_7d'))}</td>
      <td class="num dim">{esc(row.get('resolved_7d'))}</td>
      <td class="num {pnl_30d_class}">{fmt_money(pnl_30d)}</td>
      <td class="num">{fmt_pct(row.get('win_rate_30d'))}</td>
      <td class="num dim">{esc(row.get('resolved_30d'))}</td>
    </tr>"""


def main() -> None:
    log_rows = read_csv(TRADE_LOG)
    signal_rows = read_csv(SIGNALS)
    trader_rows = read_csv(TRADERS)
    performance_rows = read_csv(PERFORMANCE)
    elon_rows = read_csv(ELON_TRADERS)

    log_rows.sort(key=lambda r: (STATUS_ORDER.get(r.get("status", "OPEN"), 9), r.get("last_updated", "")), reverse=False)
    signal_rows.sort(key=lambda r: ACTION_ORDER.get(r.get("action", "IGNORE"), 9))
    trader_rows.sort(key=lambda r: r.get("label", ""))
    performance_rows.sort(key=lambda r: float(r.get("pnl_7d") or 0))
    elon_rows.sort(key=lambda r: float(r.get("copy_score") or 0), reverse=True)

    resolved = [r for r in log_rows if r.get("status") in ("WIN", "LOSS", "CLOSED")]
    wins = [r for r in resolved if r["status"] in ("WIN", "CLOSED")]
    open_trades = [r for r in log_rows if r.get("status") == "OPEN"]
    win_rate = (len(wins) / len(resolved) * 100) if resolved else 0.0

    # Historial de trades: abiertos primero (siempre visibles, son pocos),
    # despues solo los ultimos 10 cerrados - por fecha, no por resultado -
    # para que la tabla no crezca sin limite con el historico.
    open_by_date = sorted(open_trades, key=lambda r: r.get("date_first_seen", ""), reverse=True)
    resolved_recent = sorted(resolved, key=lambda r: r.get("last_updated", ""), reverse=True)[:10]
    history_rows = open_by_date + resolved_recent

    copy_signals = [r for r in signal_rows if r.get("action") == "COPY"]
    other_signals = [r for r in signal_rows if r.get("action") != "COPY"][:20]

    now_utc = datetime.now(timezone.utc)
    now = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    now_zurich = now_utc.astimezone(ZURICH).strftime("%Y-%m-%d %H:%M")

    # --- Retorno realizado (trades cerrados) vs no realizado (abiertos) ---
    # Averages only - a sum of independent trades' % returns isn't a real
    # portfolio metric (it inflates with trade count regardless of money
    # actually made), so we don't compute or show it anymore.
    realized_pct = [float(r["pct_return"]) for r in resolved if r.get("pct_return") not in (None, "")]
    realized_avg = sum(realized_pct) / len(realized_pct) if realized_pct else 0.0

    unrealized_pct = []
    for r in open_trades:
        try:
            entry = float(r.get("entry_price") or 0)
            current = float(r.get("current_price") or 0)
            if entry > 0:
                unrealized_pct.append((current / entry - 1) * 100)
        except ValueError:
            continue
    unrealized_avg = sum(unrealized_pct) / len(unrealized_pct) if unrealized_pct else 0.0

    # Headline "Retorno promedio" blends both pools (all trades, closed or
    # not) so it moves with whichever side - realized or unrealized - is
    # actually driving performance right now, instead of only reflecting
    # closed trades.
    all_pct = realized_pct + unrealized_pct
    avg_return = sum(all_pct) / len(all_pct) if all_pct else 0.0

    # Por dia (hora Zurich): realizado = dia en que cerro (last_updated),
    # no realizado = dia en que se detecto la posicion (date_first_seen)
    realized_by_day: dict[str, list[float]] = defaultdict(list)
    for r in resolved:
        if r.get("pct_return") not in (None, ""):
            realized_by_day[zurich_day(r.get("last_updated", ""))].append(float(r["pct_return"]))

    unrealized_by_day: dict[str, list[float]] = defaultdict(list)
    for r, pct in zip(
        [r for r in open_trades if r.get("entry_price") and r.get("current_price")], unrealized_pct
    ):
        unrealized_by_day[zurich_day(r.get("date_first_seen", ""))].append(pct)

    def day_rows(by_day: dict[str, list[float]]) -> str:
        rows = []
        for day in sorted(by_day.keys(), reverse=True):
            vals = by_day[day]
            avg = sum(vals) / len(vals)
            cls = "num-pos" if avg >= 0 else "num-neg"
            rows.append(
                f'<tr><td class="dim">{esc(day)}</td><td class="num">{len(vals)}</td>'
                f'<td class="num {cls}">{avg:+.1f}%</td></tr>'
            )
        return "".join(rows) or '<tr><td colspan="3" class="empty">Sin datos</td></tr>'

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
<meta http-equiv="refresh" content="120">
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
      <div class="subtitle">Señales de consenso · Polymarket sports · 14 traders verificados</div>
    </div>
    <div class="updated">Última corrida (UTC)<br><b>{esc(now)}</b><br>Hora Zúrich<br><b>{esc(now_zurich)}</b></div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" data-tab="sports">Deportes</button>
    <button class="tab-btn" data-tab="elon">Elon Musk</button>
  </div>

  <div class="tab-panel active" data-tab-panel="sports">

  <div class="scoreboard">
    <div class="stat">
      <div class="stat-label">Trades registrados</div>
      <div class="stat-value">{len(log_rows)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Win rate (resueltos)</div>
      <div class="stat-value accent">{win_rate:.1f}%</div>
    </div>
    <div class="stat">
      <div class="stat-label">Retorno promedio</div>
      <div class="stat-value {'pos' if avg_return >= 0 else 'neg'}">{avg_return:+.1f}%</div>
    </div>
    <div class="stat">
      <div class="stat-label">Posiciones abiertas</div>
      <div class="stat-value">{len(open_trades)}</div>
    </div>
  </div>

  <section>
    <div class="section-head">
      <h2>Rendimiento</h2>
      <span class="section-note">Realizado = trades cerrados · No realizado = abiertos, marca en vivo</span>
    </div>
    <div class="scoreboard" style="grid-template-columns: repeat(2, 1fr); margin-bottom: 20px;">
      <div class="stat">
        <div class="stat-label">Retorno realizado (promedio)</div>
        <div class="stat-value {'pos' if realized_avg >= 0 else 'neg'}">{realized_avg:+.1f}%</div>
        <div class="section-note">{len(realized_pct)} trades cerrados</div>
      </div>
      <div class="stat">
        <div class="stat-label">Retorno no realizado (promedio)</div>
        <div class="stat-value {'pos' if unrealized_avg >= 0 else 'neg'}">{unrealized_avg:+.1f}%</div>
        <div class="section-note">{len(unrealized_pct)} trades abiertos</div>
      </div>
    </div>
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 16px;">
      <div class="table-scroll">
        <table>
          <thead><tr><th>Día (Zúrich)</th><th>Trades</th><th>Promedio %</th></tr></thead>
          <tbody>{day_rows(realized_by_day)}</tbody>
        </table>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Día detectado (Zúrich)</th><th>Trades</th><th>Promedio %</th></tr></thead>
          <tbody>{day_rows(unrealized_by_day)}</tbody>
        </table>
      </div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Señales activas ahora</h2>
      <span class="section-note">{len(copy_signals)} COPY · actualiza cada 2 min</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Acción</th><th>Mercado (clic para abrir)</th><th>Apuesta</th><th>Precio</th>
            <th>Traders</th><th>Convicción</th><th>Stake</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_signal_row(r) for r in copy_signals + other_signals) or '<tr><td colspan="7" class="empty">Sin señales activas en este momento</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Historial de trades</h2>
      <span class="section-note">{len(open_trades)} abiertos + últimos 10 cerrados (de {len(resolved)} resueltos) · ordenado por fecha · * = retorno no realizado, mercado sigue abierto</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Estado</th><th>Mercado (clic para abrir)</th><th>Apuesta</th><th>Entrada</th>
            <th>Salida</th><th>Retorno</th><th>Detectado</th><th>Traders</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_log_row(r) for r in history_rows) or '<tr><td colspan="8" class="empty">Todavía no hay trades registrados</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Rendimiento por trader</h2>
      <span class="section-note">PnL realizado y win rate en ventanas móviles de 7 y 30 días · ordenado peor a mejor (7d) · se actualiza cada ~2h</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Label</th><th>Flag</th>
            <th>PnL 7d</th><th>WR 7d</th><th>Trades 7d</th>
            <th>PnL 30d</th><th>WR 30d</th><th>Trades 30d</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_performance_row(r) for r in performance_rows) or '<tr><td colspan="8" class="empty">Sin datos de rendimiento todavia</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Traders</h2>
      <span class="section-note">{len(trader_rows)} wallets · clic en el usuario para ver su perfil en Polymarket</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Label</th><th>Usuario</th><th>Wallet</th><th>Verdict</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_trader_row(r) for r in trader_rows) or '<tr><td colspan="5" class="empty">Sin datos de traders todavia</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  </div>

  <div class="tab-panel" data-tab-panel="elon">

  <section>
    <div class="section-head">
      <h2>Candidatos — Elon Musk mentions</h2>
      <span class="section-note">{len(elon_rows)} wallets candidatas · fase de exploración, sin señales de consenso todavía</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Usuario</th><th>Wallet</th><th>Verdict</th>
            <th>Copy score</th><th>Volumen Elon</th><th>Trades Elon</th><th>Risk score</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_elon_candidate_row(r) for r in elon_rows) or '<tr><td colspan="7" class="empty">Sin candidatos todavía — corré discover-elon-traders o evaluate-elon-wallet</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  </div>

  <footer>
    <span>Generado automáticamente por un cron en VPS cada 2 min.</span>
    <a href="https://github.com/giuseppemineo685-beep/atlantis-polymarket-screening" target="_blank">Ver repositorio</a>
  </footer>

</div>
<script>
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.tabPanel === tab));
  }});
}});
</script>
</body>
</html>
"""

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_out)
    print(f"Dashboard generado: {OUT_PATH}")


if __name__ == "__main__":
    main()
