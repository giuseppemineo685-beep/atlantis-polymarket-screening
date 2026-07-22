from __future__ import annotations

import base64
import csv
import html
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = Path(__file__).resolve().parent / "fonts"
TRADE_LOG = ROOT / "outputs" / "trade_log.csv"
SIGNALS = ROOT / "outputs" / "active_portfolio_signals.csv"
OUT_PATH = ROOT / "docs" / "index.html"

ACTION_ORDER = {"COPY": 0, "WAIT": 1, "CONFLICT": 2, "IGNORE": 3}
STATUS_ORDER = {"OPEN": 0, "WIN": 1, "LOSS": 2}


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


def render_signal_row(row: dict) -> str:
    action = row.get("action", "IGNORE")
    return f"""
    <tr>
      <td><span class="pill pill-{action.lower()}">{esc(action)}</span></td>
      <td class="title-cell">{esc(row.get('title'))}</td>
      <td>{esc(row.get('outcome'))}</td>
      <td class="num">{fmt_price(row.get('current_price'))}</td>
      <td class="num">{esc(row.get('supporting_traders'))}</td>
      <td class="num">{esc(row.get('conviction'))}</td>
      <td class="num">${esc(row.get('stake'))}</td>
    </tr>"""


def render_log_row(row: dict) -> str:
    status = row.get("status", "OPEN")
    consensus_active = row.get("consensus_active", "yes") == "yes"
    pct_return = row.get("pct_return", "")
    return_class = ""
    if pct_return not in (None, ""):
        try:
            return_class = "num-pos" if float(pct_return) >= 0 else "num-neg"
        except ValueError:
            return_class = ""

    if status == "OPEN" and not consensus_active:
        status_badge = '<span class="pill pill-warn">SIN CONSENSO</span>'
    else:
        status_badge = f'<span class="pill pill-{status.lower()}">{esc(status)}</span>'

    return f"""
    <tr>
      <td>{status_badge}</td>
      <td class="title-cell">{esc(row.get('title'))}</td>
      <td>{esc(row.get('outcome'))}</td>
      <td class="num">{fmt_price(row.get('entry_price'))}</td>
      <td class="num">{fmt_price(row.get('exit_price') or row.get('current_price'))}</td>
      <td class="num {return_class}">{fmt_pct(pct_return)}</td>
      <td class="dim">{esc(row.get('date_first_seen'))}</td>
      <td class="dim">{esc(row.get('traders'))}</td>
    </tr>"""


def main() -> None:
    log_rows = read_csv(TRADE_LOG)
    signal_rows = read_csv(SIGNALS)

    log_rows.sort(key=lambda r: (STATUS_ORDER.get(r.get("status", "OPEN"), 9), r.get("last_updated", "")), reverse=False)
    signal_rows.sort(key=lambda r: ACTION_ORDER.get(r.get("action", "IGNORE"), 9))

    resolved = [r for r in log_rows if r.get("status") in ("WIN", "LOSS")]
    wins = [r for r in resolved if r["status"] == "WIN"]
    open_trades = [r for r in log_rows if r.get("status") == "OPEN"]
    win_rate = (len(wins) / len(resolved) * 100) if resolved else 0.0
    avg_return = 0.0
    if resolved:
        returns = [float(r["pct_return"]) for r in resolved if r.get("pct_return") not in (None, "")]
        avg_return = sum(returns) / len(returns) if returns else 0.0

    copy_signals = [r for r in signal_rows if r.get("action") == "COPY"]
    other_signals = [r for r in signal_rows if r.get("action") != "COPY"][:20]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
    <div class="updated">Última corrida<br><b>{esc(now)}</b></div>
  </header>

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
      <h2>Señales activas ahora</h2>
      <span class="section-note">{len(copy_signals)} COPY · actualiza cada 30 min</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Acción</th><th>Mercado</th><th>Resultado</th><th>Precio</th>
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
      <span class="section-note">Los cerrados quedan siempre visibles — {len(resolved)} resueltos, {len(open_trades)} abiertos</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Estado</th><th>Mercado</th><th>Resultado</th><th>Entrada</th>
            <th>Salida</th><th>Retorno</th><th>Detectado</th><th>Traders</th>
          </tr>
        </thead>
        <tbody>
          {"".join(render_log_row(r) for r in log_rows) or '<tr><td colspan="8" class="empty">Todavía no hay trades registrados</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  <footer>
    <span>Generado automáticamente por GitHub Actions cada 30 min.</span>
    <a href="https://github.com/giuseppemineo685-beep/atlantis-polymarket-screening" target="_blank">Ver repositorio</a>
  </footer>

</div>
</body>
</html>
"""

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_out)
    print(f"Dashboard generado: {OUT_PATH}")


if __name__ == "__main__":
    main()
