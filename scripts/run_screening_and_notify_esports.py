"""Esports counterpart of scripts/run_screening_and_notify.py - paper trading
only. Deliberately does NOT import or call anything from the real-money
order-queue module the sports script uses to feed the Finland VPS execution
pipeline - this vertical has no roster review/approval for real money yet.
Every other mechanic (consensus signals, trade log, early-exit/resolution
detection, Telegram) is a straight mirror of the sports script, just pointed
at esports-specific CLI commands, CSVs, and state files so nothing here can
collide with the sports/live-money paper trail.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from atlantis.config import load_settings  # noqa: E402
from atlantis.polymarket.client import build_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "notified_signals_esports.json"
TRADE_LOG_PATH = ROOT / "outputs" / "trade_log_esports.csv"
TRADE_LOG_FIELDS = [
    "condition_id",
    "asset",
    "title",
    "slug",
    "outcome",
    "traders",
    "entry_supporting_traders",
    "supporting_traders",
    "date_first_seen",
    "entry_price",
    "current_price",
    "consensus_active",
    "status",
    "exit_price",
    "pct_return",
    "last_updated",
]

VERDICT_MAP = {
    "WATCHLIST_STRONG": "A",
    "WATCHLIST": "B",
    "PAPER_ONLY": "C",
    "REJECT": "REJECT",
}


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def convert_to_portfolio_format() -> None:
    with open(ROOT / "outputs" / "watchlist_evaluation_esports.csv") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    for r in rows:
        verdict = VERDICT_MAP.get(r["verdict"], "REJECT")
        esports_trades = int(r["esports_trades"] or 0)
        esports_volume = float(r["esports_volume"] or 0)
        recent_esports_14d = int(r["recent_esports_14d"] or 0)
        bot_score = float(r["bot_score"] or 0)

        confidence_score = min(100, esports_trades / 5 + recent_esports_14d)
        copy_score = min(100, (esports_volume / 20000) + confidence_score * 0.3)
        risk_score = bot_score

        out_rows.append(
            {
                "username": r["label"],
                "wallet_address": r["wallet"],
                "verdict": verdict,
                "copy_score": round(copy_score, 2),
                "confidence_score": round(confidence_score, 2),
                "risk_score": round(risk_score, 2),
            }
        )

    out_path = ROOT / "outputs" / "portfolio_traders_esports.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["username", "wallet_address", "verdict", "copy_score", "confidence_score", "risk_score"]
        )
        writer.writeheader()
        writer.writerows(out_rows)


def fetch_and_save_traders_info(client) -> None:
    """Look up each wallet's real Polymarket username (not our internal
    label) so the dashboard can link straight to their profile."""
    with open(ROOT / "outputs" / "watchlist_evaluation_esports.csv") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    for r in rows:
        username = ""
        try:
            sample = client.get_user_trades(wallet_address=r["wallet"], limit=1, offset=0)
            if sample:
                username = sample[0].get("name") or sample[0].get("pseudonym") or ""
        except Exception as exc:
            print(f"  aviso: no se pudo obtener username de {r['label']}: {exc}", file=sys.stderr)
        out_rows.append(
            {
                "label": r["label"],
                "wallet": r["wallet"],
                "username": username,
                "status": r["status"],
                "verdict": r["verdict"],
                "notes": r.get("input_notes", ""),
            }
        )

    out_path = ROOT / "outputs" / "traders_esports.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["label", "wallet", "username", "status", "verdict", "notes"])
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"  traders_esports.csv actualizado ({len(out_rows)} wallets)")


def load_state_path(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()))


def save_state_path(path: Path, keys: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(keys), indent=2))


def load_state() -> set[str]:
    return load_state_path(STATE_PATH)


def save_state(keys: set[str]) -> None:
    save_state_path(STATE_PATH, keys)


def signal_key(row: dict) -> str:
    return f"{row['condition_id']}|{row['asset']}"


def load_trade_log() -> dict[str, dict]:
    if not TRADE_LOG_PATH.exists():
        return {}
    with TRADE_LOG_PATH.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for field in TRADE_LOG_FIELDS:
            r.setdefault(field, "")
    return {f"{r['condition_id']}|{r['asset']}": r for r in rows}


def save_trade_log(log: dict[str, dict]) -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        log.values(),
        key=lambda r: (r["status"] != "OPEN", r.get("last_updated", "")),
        reverse=False,
    )
    with TRADE_LOG_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def get_market_price_info(condition_id: str, asset: str) -> tuple[Decimal | None, bool]:
    """Return (current_price, is_closed) for `asset` in this market. Same
    CLOB-not-gamma-api reasoning as the sports script: gamma-api archives
    markets after a couple days and starts returning nothing for them."""
    url = f"https://clob.polymarket.com/markets/{condition_id}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "atlantis-screening-esports/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            market = json.loads(resp.read())
    except Exception as exc:
        print(f"  aviso: no se pudo consultar {condition_id}: {exc}", file=sys.stderr)
        return None, False

    if not market:
        return None, False
    closed = bool(market.get("closed"))

    for token in market.get("tokens", []):
        if token.get("token_id") == asset:
            try:
                return Decimal(str(token.get("price"))), closed
            except InvalidOperation:
                return None, closed
    return None, closed


def load_label_to_wallet() -> dict[str, str]:
    with open(ROOT / "outputs" / "portfolio_traders_esports.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    return {r["username"]: r["wallet_address"] for r in rows}


def count_traders_still_holding(condition_id: str, asset: str, trader_labels: list[str], label_to_wallet: dict[str, str], client) -> int:
    held = 0
    for label in trader_labels:
        wallet = label_to_wallet.get(label)
        if not wallet:
            continue
        try:
            positions = client.get_user_positions(wallet_address=wallet, limit=500)
        except Exception as exc:
            print(f"    aviso: no se pudo chequear posicion de {label}: {exc}", file=sys.stderr)
            continue
        match = next(
            (p for p in positions if p.get("conditionId") == condition_id and p.get("asset") == asset),
            None,
        )
        if match and float(match.get("size") or 0) > 0:
            held += 1
    return held


MIN_CONSENSUS = 2


def apply_trader_change(row: dict, live_supporting: int, live_price: Decimal | None, now: str) -> str | None:
    trader_count = len([t for t in row["traders"].split(",") if t.strip()])
    entry_supporting = int(row.get("entry_supporting_traders") or trader_count or live_supporting)
    row["entry_supporting_traders"] = entry_supporting

    if live_price is not None:
        row["current_price"] = str(live_price)
    row["supporting_traders"] = live_supporting
    row["last_updated"] = now

    if live_supporting < entry_supporting and live_price is not None:
        entry_price = Decimal(row["entry_price"])
        pct_return = ((live_price / entry_price) - 1) * Decimal("100") if entry_price > 0 else Decimal("0")
        row["status"] = "CLOSED"
        row["exit_price"] = str(live_price)
        row["pct_return"] = f"{pct_return:.2f}"
        row["consensus_active"] = "no"
        return "closed"

    row["consensus_active"] = "yes" if live_supporting >= MIN_CONSENSUS else row.get("consensus_active", "yes")
    return None


def update_trade_log(
    log: dict[str, dict], copy_signals: list[dict], label_to_wallet: dict[str, str], client
) -> tuple[dict[str, dict], list[dict]]:
    """Same rules as the sports script's update_trade_log, minus the
    real-money order-queue calls - this is paper-only, nothing here ever
    touches the live execution pipeline."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    current_keys = set()
    closed_alerts = []

    for signal in copy_signals:
        key = signal_key(signal)
        current_keys.add(key)
        if key not in log:
            if int(signal["supporting_traders"]) < MIN_CONSENSUS:
                print(f"  (omitido, {signal['supporting_traders']} < minimo de consenso: {signal['title']})")
                continue
            log[key] = {
                "condition_id": signal["condition_id"],
                "asset": signal["asset"],
                "title": signal["title"],
                "slug": signal.get("slug", ""),
                "outcome": signal["outcome"],
                "traders": signal["traders"],
                "entry_supporting_traders": signal["supporting_traders"],
                "supporting_traders": signal["supporting_traders"],
                "date_first_seen": now,
                "entry_price": signal["avg_entry_price"],
                "current_price": signal["current_price"],
                "consensus_active": "yes",
                "status": "OPEN",
                "exit_price": "",
                "pct_return": "",
                "last_updated": now,
            }
            print(f"  + nuevo trade en log: {signal['title']} -> {signal['outcome']}")
        else:
            row = log[key]
            if row["status"] == "OPEN":
                tier = apply_trader_change(row, int(signal["supporting_traders"]), Decimal(signal["current_price"]), now)
                if tier == "closed":
                    closed_alerts.append(row)
                    print(f"  = cerrado (un trader salio): {row['title']} ({row['pct_return']}%)")

    for key, row in log.items():
        if row["status"] != "OPEN" or key in current_keys:
            continue

        price, closed = get_market_price_info(row["condition_id"], row["asset"])

        if closed and price is not None and price in (Decimal("0"), Decimal("1")):
            entry_price = Decimal(row["entry_price"])
            pct_return = ((price / entry_price) - 1) * Decimal("100") if entry_price > 0 else Decimal("0")
            row["status"] = "WIN" if price == Decimal("1") else "LOSS"
            row["exit_price"] = str(price)
            row["current_price"] = str(price)
            row["pct_return"] = f"{pct_return:.2f}"
            row["consensus_active"] = "no"
            row["last_updated"] = now
            print(f"  = resuelto: {row['title']} -> {row['status']} ({pct_return:.2f}%)")
            continue

        trader_labels = [t.strip() for t in row["traders"].split(",") if t.strip()]
        still_holding = count_traders_still_holding(row["condition_id"], row["asset"], trader_labels, label_to_wallet, client)

        tier = apply_trader_change(row, still_holding, price, now)
        if tier == "closed":
            closed_alerts.append(row)
            print(f"  = cerrado (un trader salio): {row['title']} ({row['pct_return']}%)")
        else:
            print(f"  ~ fuera del filtro de precio pero {still_holding}/{len(trader_labels)} siguen dentro: {row['title']}")

    return log, closed_alerts


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


PAPER_PREFIX = "📄 <b>PAPER TRADE · ESPORTS</b>\n"


def format_closed_message(row: dict) -> str:
    pct = float(row["pct_return"])
    if pct >= 0:
        emoji, headline = "✅", "CERRADO — ganancia asegurada"
    else:
        emoji, headline = "🔻", "CERRADO — salimos con pérdida"
    return (
        f"{PAPER_PREFIX}"
        f"{emoji} <b>{headline}</b>\n"
        f"{row['title']}\n"
        f"Resultado: <b>{row['outcome']}</b>\n"
        f"Entrada: {row['entry_price']}  →  Salida: {row['exit_price']}  ({row['pct_return']}%)\n"
        f"Uno de los traders que respaldaba esto salió, así que seguimos su salida en vez de esperar "
        f"a que resuelva el mercado."
    )


def format_signal_message(row: dict) -> str:
    return (
        f"{PAPER_PREFIX}"
        f"🟢 <b>NUEVA SEÑAL COPY</b>\n"
        f"{row['title']}\n"
        f"Resultado: <b>{row['outcome']}</b>\n"
        f"Precio: {row['current_price']}  |  Traders de acuerdo: {row['supporting_traders']}\n"
        f"Convicción: {row['conviction']}  |  Stake sugerido: ${row['stake']}\n"
        f"Traders: {row['traders']}"
    )


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en el entorno", file=sys.stderr)
        return 1

    skip_evaluate = "--skip-evaluate" in sys.argv

    try:
        return run_pipeline(token, chat_id, skip_evaluate=skip_evaluate)
    except Exception as exc:
        print(f"Error fatal en el pipeline de esports: {exc}", file=sys.stderr)
        try:
            send_telegram(
                token,
                chat_id,
                f"🔴 <b>El screening de esports (paper) falló esta corrida</b>\n{type(exc).__name__}: {exc}\n"
                f"La próxima corrida programada lo va a reintentar solo.",
            )
        except Exception as notify_exc:
            print(f"Ademas fallo el aviso por Telegram: {notify_exc}", file=sys.stderr)
        return 1


def run_pipeline(token: str, chat_id: str, skip_evaluate: bool = False) -> int:
    if skip_evaluate:
        print("(ciclo rapido: usando outputs/portfolio_traders_esports.csv ya existente, sin re-evaluar wallets)")
    else:
        run(
            [
                sys.executable,
                "-B",
                "-m",
                "atlantis.cli",
                "evaluate-watchlist-esports",
                "--wallets-csv",
                "inputs/approved_wallets_esports.csv",
                "--statuses",
                "approved,paper_only",
                "--since-days",
                "30",
                "--csv",
                "outputs/watchlist_evaluation_esports.csv",
            ]
        )

        convert_to_portfolio_format()
        fetch_and_save_traders_info(build_client(load_settings()))

    run(
        [
            sys.executable,
            "-B",
            "-m",
            "atlantis.cli",
            "active-portfolio-esports",
            "--traders-csv",
            "outputs/portfolio_traders_esports.csv",
            "--min-verdict",
            "B",
            "--bankroll",
            "1000",
            "--csv",
            "outputs/active_portfolio_signals_esports.csv",
        ]
    )

    with open(ROOT / "outputs" / "active_portfolio_signals_esports.csv") as f:
        signals = list(csv.DictReader(f))

    copy_signals = [s for s in signals if s.get("action") == "COPY"]

    seen = load_state()
    new_signals = [s for s in copy_signals if signal_key(s) not in seen]

    print(f"Señales COPY totales (esports): {len(copy_signals)}  |  Nuevas: {len(new_signals)}")

    for signal in new_signals:
        try:
            send_telegram(token, chat_id, format_signal_message(signal))
        except Exception as exc:
            print(f"Error enviando notificacion: {exc}", file=sys.stderr)

    all_current_keys = {signal_key(s) for s in copy_signals}
    save_state(seen | all_current_keys)

    settings = load_settings()
    client = build_client(settings)
    label_to_wallet = load_label_to_wallet()

    trade_log = load_trade_log()
    trade_log, closed_alerts = update_trade_log(trade_log, copy_signals, label_to_wallet, client)

    print(f"Cierres por salida de trader (esports): {len(closed_alerts)}")
    for row in closed_alerts:
        try:
            send_telegram(token, chat_id, format_closed_message(row))
        except Exception as exc:
            print(f"Error enviando alerta de cierre: {exc}", file=sys.stderr)

    save_trade_log(trade_log)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
