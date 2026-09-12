#!/usr/bin/env python3
"""
Genera docs/replication_market.html - dashboard interno "The Replication
Market" para trackear a la wallet 0xeebde7a0e019a63e6b476eb425505b7b3e6eba30
("Bonereaper") y, mas adelante, la replicacion (primero en papel) de sus
operaciones.

NO es el SaaS publico (thereplicationmarket.com) - ver
atlantis/replication_market/README.md.

Lee atlantis/replication_market/bonereaper_monitor.jsonl (generado por
monitor_bonereaper.py) y renderiza dos tabs: Analitica y Replica en vivo.

Pensado para correr en loop (igual que publish_dashboard_loop.sh) una vez
el monitor este desplegado en el VPS de Alemania.
"""
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSONL_PATH = ROOT / "atlantis" / "replication_market" / "bonereaper_monitor.jsonl"
OUT_PATH = ROOT / "docs" / "replication_market.html"

WALLET = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
COIN_ORDER = ["Bitcoin", "Solana", "XRP", "Ethereum"]


def load_records():
    """Read the monitor's JSONL, de-duping defensively: a monitor restart
    re-polls the last ~100 trades and can re-log ones already on disk before
    its in-memory seen-set is warm again, so the same (tx, timestamp, size)
    can appear more than once."""
    if not JSONL_PATH.exists():
        return []
    seen = set()
    out = []
    for line in JSONL_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        key = (rec.get("transactionHash"), rec.get("trade_timestamp"), rec.get("size"))
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_usd(v, decimals=2):
    return f"${v:,.{decimals}f}"


def market_label(rec):
    """Nombre del mercado. Los registros nuevos ya traen el titulo real de
    Polymarket; para los mas viejos (loggeados antes de guardar ese campo)
    lo reconstruimos a partir de la ventana de 5 min."""
    title = rec.get("market_title")
    if title:
        # "Bitcoin Up or Down - September 12, 5:55AM-6:00AM ET" -> recortar el "Up or Down - "
        return re.sub(r"\s+Up or Down\s*-\s*", " ", title)
    ws = rec.get("window_start")
    if ws:
        start = time.strftime("%H:%M", time.gmtime(ws))
        end = time.strftime("%H:%M", time.gmtime(ws + 300))
        return f"{rec.get('coin','?')} {start}–{end} UTC"
    return rec.get("coin", "?")


def fmt_trade_time(rec):
    ts = rec.get("trade_timestamp")
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) + " UTC"


def build_stats(recs):
    resolved = [r for r in recs if r.get("winner")]
    wins = sum(1 for r in resolved if r.get("correct"))
    n_resolved = len(resolved)
    win_rate = (wins / n_resolved * 100) if n_resolved else None

    total_cost = sum(r["size"] * r["price_paid"] for r in resolved)
    total_pnl = sum(
        (r["size"] if r.get("correct") else 0) - r["size"] * r["price_paid"]
        for r in resolved
    )

    by_coin = {c: {"n": 0, "wins": 0, "resolved": 0} for c in COIN_ORDER}
    for r in recs:
        c = r.get("coin")
        if c not in by_coin:
            by_coin.setdefault(c, {"n": 0, "wins": 0, "resolved": 0})
        by_coin[c]["n"] += 1
        if r.get("winner"):
            by_coin[c]["resolved"] += 1
            if r.get("correct"):
                by_coin[c]["wins"] += 1

    # price-paid calibration buckets (only where we have a resolution)
    buckets = {}
    for r in resolved:
        b = round(r["price_paid"], 1)
        d = buckets.setdefault(b, {"n": 0, "wins": 0})
        d["n"] += 1
        d["wins"] += int(bool(r.get("correct")))

    # elapsed-in-window histogram (7 buckets of 50s)
    timing = [0] * 7
    for r in recs:
        e = r.get("elapsed_in_window_s")
        if e is None:
            continue
        idx = min(max(int(e // 50), 0), 6)
        timing[idx] += 1

    with_chainlink = [r for r in recs if r.get("chainlink_price_at_trade") is not None]

    timestamps = [r["trade_timestamp"] for r in recs if r.get("trade_timestamp")]
    span_hours = (max(timestamps) - min(timestamps)) / 3600 if len(timestamps) > 1 else 0

    return {
        "total": len(recs),
        "resolved": n_resolved,
        "wins": wins,
        "win_rate": win_rate,
        "total_cost": total_cost,
        "total_pnl": total_pnl,
        "by_coin": by_coin,
        "buckets": buckets,
        "timing": timing,
        "with_chainlink": len(with_chainlink),
        "span_hours": span_hours,
        "recent": list(reversed(recs))[:30],
    }


def render_coin_rows(by_coin):
    rows = []
    for c in COIN_ORDER:
        d = by_coin.get(c)
        if not d or d["n"] == 0:
            continue
        wr = f"{d['wins'] / d['resolved'] * 100:.1f}%" if d["resolved"] else "—"
        rows.append(
            f"""<tr><td>{esc(c)}</td><td class="num">{d['n']}</td>
            <td class="num">{d['resolved']}</td><td class="num">{wr}</td></tr>"""
        )
    return "\n".join(rows)


def render_calibration_bars(buckets):
    if not buckets:
        return '<p class="muted">Todavía no hay suficientes mercados resueltos para calibrar.</p>'
    rows = []
    for b in sorted(buckets):
        d = buckets[b]
        wr = d["wins"] / d["n"] * 100
        edge = wr - b * 100
        edge_class = "good" if edge >= 0 else "bad"
        rows.append(
            f"""
        <div class="cal-row">
          <div class="cal-label">{b:.1f}¢</div>
          <div class="cal-track">
            <div class="cal-fill fair" style="width:{b*100:.1f}%"></div>
            <div class="cal-fill real" style="width:{wr:.1f}%"></div>
          </div>
          <div class="cal-n">n={d['n']}</div>
          <div class="cal-edge {edge_class}">{edge:+.1f}pp</div>
        </div>"""
        )
    return "\n".join(rows)


def render_timing_bars(timing):
    labels = ["0-50s", "50-100s", "100-150s", "150-200s", "200-250s", "250-300s", "300s+"]
    maxv = max(timing) if any(timing) else 1
    rows = []
    for lab, v in zip(labels, timing):
        pct = v / maxv * 100 if maxv else 0
        rows.append(
            f"""
        <div class="tm-row">
          <div class="tm-label">{lab}</div>
          <div class="tm-track"><div class="tm-fill" style="width:{pct:.1f}%"></div></div>
          <div class="tm-n">{v}</div>
        </div>"""
        )
    return "\n".join(rows)


def render_recent_table(recent):
    rows = []
    for r in recent:
        state = "?"
        state_class = "pending"
        if r.get("winner"):
            state = "✓ correcto" if r.get("correct") else "✗ incorrecto"
            state_class = "good" if r.get("correct") else "bad"
        cl = (
            f"{r['chainlink_price_at_trade']:,.2f}"
            if r.get("chainlink_price_at_trade") is not None
            else "—"
        )
        elapsed = r.get("elapsed_in_window_s")
        elapsed_s = f"{elapsed}s" if elapsed is not None else "—"
        rows.append(
            f"""<tr>
            <td>
              <div class="mkt-name">{esc(market_label(r))}</div>
              <div class="mkt-time mono-sm">{esc(fmt_trade_time(r))}</div>
            </td>
            <td>{esc(r.get('side_bought',''))}</td>
            <td class="num">{r.get('price_paid',0):.3f}</td>
            <td class="num">{r.get('size',0):.1f}</td>
            <td class="num">{elapsed_s}</td>
            <td class="num mono-sm">{cl}</td>
            <td class="pill {state_class}">{state}</td>
            </tr>"""
        )
    return "\n".join(rows)


def render(stats):
    win_rate_txt = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "—"
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Replication Market</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {{
  --paper: #f6f3ec;
  --ink: #1b1d22;
  --ash: #6b6455;
  --line: #e2ddd0;
  --surface: #ffffff;
  --accent: #c8912f;
  --accent-soft: rgba(200,145,47,0.12);
  --good: #2f8a5b;
  --good-soft: rgba(47,138,91,0.12);
  --bad: #b8404f;
  --bad-soft: rgba(184,64,79,0.12);
  --pending: #8a8272;
  --pending-soft: rgba(138,130,114,0.14);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper: #15161a;
    --ink: #ece7db;
    --ash: #a39a86;
    --line: #2c2d33;
    --surface: #1d1e24;
    --accent: #e0a83f;
    --accent-soft: rgba(224,168,63,0.14);
    --good: #4cb583;
    --good-soft: rgba(76,181,131,0.14);
    --bad: #e0687a;
    --bad-soft: rgba(224,104,122,0.14);
    --pending: #9a9282;
    --pending-soft: rgba(154,146,130,0.16);
  }}
}}
:root[data-theme="dark"] {{
  --paper: #15161a;
  --ink: #ece7db;
  --ash: #a39a86;
  --line: #2c2d33;
  --surface: #1d1e24;
  --accent: #e0a83f;
  --accent-soft: rgba(224,168,63,0.14);
  --good: #4cb583;
  --good-soft: rgba(76,181,131,0.14);
  --bad: #e0687a;
  --bad-soft: rgba(224,104,122,0.14);
  --pending: #9a9282;
  --pending-soft: rgba(154,146,130,0.16);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
  font-size: 15px; line-height: 1.5;
}}
.mono {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }}
.mono-sm {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; font-size: 0.85em; }}
h1, h2, h3 {{ font-family: 'Fraunces', Georgia, serif; text-wrap: balance; margin: 0; }}
.wrap {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 60px; }}

.masthead {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; padding-bottom: 20px; border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}}
.masthead-id {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }}
.masthead h1 {{ font-size: 1.65rem; font-weight: 600; }}
.wallet-addr {{ color: var(--ash); font-size: 0.8rem; }}
.status-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.pill {{
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
  border-radius: 999px; font-size: 0.78rem; font-weight: 500; white-space: nowrap;
}}
.pill.good {{ background: var(--good-soft); color: var(--good); }}
.pill.bad {{ background: var(--bad-soft); color: var(--bad); }}
.pill.pending {{ background: var(--pending-soft); color: var(--pending); }}
.pill.accent {{ background: var(--accent-soft); color: var(--accent); }}
.pill-dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
.updated {{ color: var(--ash); font-size: 0.78rem; }}

.stat-strip {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 1px; background: var(--line); border: 1px solid var(--line);
  border-radius: 10px; overflow: hidden; margin-bottom: 28px;
}}
.stat-tile {{ background: var(--surface); padding: 16px 18px; }}
.stat-tile .label {{ color: var(--ash); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; }}
.stat-tile .value {{ font-family: 'IBM Plex Mono', monospace; font-size: 1.5rem; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }}
.stat-tile .value.good {{ color: var(--good); }}
.stat-tile .value.bad {{ color: var(--bad); }}

.tabs {{ display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 24px; }}
.tab-btn {{
  font-family: inherit; font-size: 0.88rem; font-weight: 500; color: var(--ash);
  background: none; border: none; border-bottom: 2px solid transparent;
  padding: 10px 4px; margin-right: 20px; cursor: pointer;
}}
.tab-btn.active {{ color: var(--ink); border-color: var(--accent); }}
.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; }}

.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 20px 22px; margin-bottom: 20px; }}
.card h2 {{ font-size: 1.05rem; font-weight: 600; margin-bottom: 4px; }}
.card .sub {{ color: var(--ash); font-size: 0.82rem; margin-bottom: 16px; }}
.muted {{ color: var(--ash); font-size: 0.85rem; }}

.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
@media (max-width: 720px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}

table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th {{ text-align: left; color: var(--ash); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); }}
td.num {{ font-family: 'IBM Plex Mono', monospace; text-align: right; font-variant-numeric: tabular-nums; }}
.table-scroll {{ overflow-x: auto; }}
td.pill {{ text-align: center; }}
.mkt-name {{ font-weight: 500; }}
.mkt-time {{ color: var(--ash); margin-top: 1px; }}

.cal-row {{ display: grid; grid-template-columns: 46px 1fr 44px 56px; align-items: center; gap: 10px; padding: 5px 0; }}
.cal-label {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; color: var(--ash); }}
.cal-track {{ position: relative; height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; }}
.cal-fill {{ position: absolute; top: 0; left: 0; height: 100%; border-radius: 4px; }}
.cal-fill.fair {{ background: var(--pending-soft); border-right: 2px dashed var(--ash); }}
.cal-fill.real {{ background: var(--accent); opacity: 0.75; height: 4px; top: 2px; }}
.cal-n {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; color: var(--ash); text-align: right; }}
.cal-edge {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; text-align: right; font-weight: 600; }}
.cal-edge.good {{ color: var(--good); }}
.cal-edge.bad {{ color: var(--bad); }}

.tm-row {{ display: grid; grid-template-columns: 90px 1fr 34px; align-items: center; gap: 10px; padding: 4px 0; }}
.tm-label {{ font-size: 0.76rem; color: var(--ash); }}
.tm-track {{ height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; }}
.tm-fill {{ height: 100%; background: var(--accent); border-radius: 4px; }}
.tm-n {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem; text-align: right; color: var(--ash); }}

.banner {{
  display: flex; gap: 12px; align-items: flex-start; padding: 14px 16px;
  border-radius: 10px; background: var(--accent-soft); border: 1px solid var(--accent);
  margin-bottom: 20px; font-size: 0.86rem;
}}
.banner b {{ color: var(--accent); }}

.replica-tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap: 14px; margin-bottom: 20px; }}
.replica-tile {{ border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; background: var(--surface); }}
.replica-tile .n {{ font-family: 'IBM Plex Mono', monospace; font-size: 1.9rem; font-weight: 600; }}
.replica-tile .l {{ color: var(--ash); font-size: 0.76rem; margin-top: 2px; }}

footer {{ margin-top: 32px; color: var(--ash); font-size: 0.78rem; border-top: 1px solid var(--line); padding-top: 16px; }}
footer a {{ color: var(--ash); }}
</style>
</head>
<body>
<div class="wrap">

  <div class="masthead">
    <div class="masthead-id">
      <h1>The Replication Market</h1>
      <span class="wallet-addr mono-sm">wallet {WALLET[:8]}&hellip;{WALLET[-6:]} &middot; alias &ldquo;Bonereaper&rdquo;</span>
    </div>
    <div class="status-row">
      <span class="pill accent"><span class="pill-dot"></span> tracking</span>
      <span class="pill pending"><span class="pill-dot"></span> modo papel</span>
      <span class="updated">actualizado {generated_at}</span>
    </div>
  </div>

  <div class="stat-strip">
    <div class="stat-tile"><div class="label">Trades registrados</div><div class="value">{stats['total']}</div></div>
    <div class="stat-tile"><div class="label">Resueltos</div><div class="value">{stats['resolved']}</div></div>
    <div class="stat-tile"><div class="label">Win rate (real)</div><div class="value {'good' if (stats['win_rate'] or 0) >= 50 else 'bad'}">{win_rate_txt}</div></div>
    <div class="stat-tile"><div class="label">PnL de la muestra</div><div class="value {'good' if stats['total_pnl'] >= 0 else 'bad'}">{fmt_usd(stats['total_pnl'])}</div></div>
    <div class="stat-tile"><div class="label">Horas cubiertas</div><div class="value">{stats['span_hours']:.1f}h</div></div>
  </div>

  <div class="tabs">
    <button class="tab-btn active" data-tab="analytics">Analítica</button>
    <button class="tab-btn" data-tab="replica">Réplica en vivo</button>
  </div>

  <div class="tab-panel active" data-tab-panel="analytics">

    <div class="card">
      <h2>Calibración: precio pagado vs. win rate real</h2>
      <div class="sub">Barra tenue = probabilidad implícita por el precio pagado &middot; barra sólida = win rate real observado. Edge positivo = gana más de lo que el precio implicaba.</div>
      {render_calibration_bars(stats['buckets'])}
    </div>

    <div class="grid-2">
      <div class="card">
        <h2>Momento de compra dentro de la ventana de 5 min</h2>
        <div class="sub">Segundos transcurridos desde la apertura del mercado hasta la compra.</div>
        {render_timing_bars(stats['timing'])}
      </div>
      <div class="card">
        <h2>Cobertura por moneda</h2>
        <div class="sub">Mercados &ldquo;Up or Down&rdquo; de 5 min que opera esta wallet.</div>
        <div class="table-scroll">
        <table>
          <tr><th>Moneda</th><th>Trades</th><th>Resueltos</th><th>Win%</th></tr>
          {render_coin_rows(stats['by_coin'])}
        </table>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Metodología</h2>
      <div class="sub" style="margin-bottom:0">
        Precio real: WebSocket público <code class="mono-sm">wss://ws-live-data.polymarket.com</code>,
        topic <code class="mono-sm">crypto_prices_chainlink</code> &mdash; el mismo feed de Chainlink que
        Polymarket usa para resolver estos mercados, capturado en el instante exacto de cada compra
        ({stats['with_chainlink']} de {stats['total']} trades con precio capturado hasta ahora).
        Resolución real por mercado vía <code class="mono-sm">clob.polymarket.com/markets/&#123;conditionId&#125;</code>.
        Corriendo desde {stats['span_hours']:.1f}h &mdash; objetivo mínimo 24h antes de sacar conclusiones firmes.
      </div>
    </div>

  </div>

  <div class="tab-panel" data-tab-panel="replica">

    <div class="banner">
      <span>&#9888;</span>
      <div><b>Modo papel solamente.</b> Todavía no se ejecuta ninguna orden real con dinero propio.
      Este contador mide cuántos trades de la wallet fueron <i>detectados</i> a tiempo como para,
      en teoría, haber sido replicados &mdash; no hay ejecución real hasta que se confirme
      explícitamente pasar a dinero real (siguiendo la misma convención que el resto de ATLANTIS:
      papel primero, Finland VPS después).</div>
    </div>

    <div class="replica-tiles">
      <div class="replica-tile"><div class="n">{stats['total']}</div><div class="l">detectados</div></div>
      <div class="replica-tile"><div class="n">0</div><div class="l">replicados en papel</div></div>
      <div class="replica-tile"><div class="n">0</div><div class="l">replicados con dinero real</div></div>
      <div class="replica-tile"><div class="n">&mdash;</div><div class="l">latencia media de detección</div></div>
    </div>

    <div class="card">
      <h2>Detecciones recientes</h2>
      <div class="sub">Mostrando los últimos {len(stats['recent'])} de {stats['total']} trades guardados en total (no se borra nada, esto es solo una ventana).</div>
      <div class="table-scroll">
      <table>
        <tr><th>Mercado</th><th>Lado</th><th>Precio</th><th>Tamaño</th><th>Elapsed</th><th>Chainlink @ compra</th><th>Resultado</th></tr>
        {render_recent_table(stats['recent'])}
      </table>
      </div>
    </div>

  </div>

  <footer>
    Dashboard interno de análisis &mdash; no confundir con
    <a href="https://thereplicationmarket.com" target="_blank" rel="noopener">thereplicationmarket.com</a>
    (el SaaS público de copy-trading). Generado por
    <code class="mono-sm">scripts/generate_replication_market_dashboard.py</code> en
    atlantis-polymarket-screening.
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
</script>
</body>
</html>
"""


def main():
    recs = load_records()
    stats = build_stats(recs)
    html = render(stats)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html)
    print(f"wrote {OUT_PATH} ({len(recs)} records, {stats['resolved']} resolved)")


if __name__ == "__main__":
    main()
