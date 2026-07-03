"""Read-only research dashboard on localhost:5000. All queries open SQLite in
read-only mode; the page auto-refreshes every ten seconds."""

import sqlite3
import time

from flask import Flask, render_template_string

from bot.config import load_config

config = load_config()
app = Flask(__name__)

PAGE = """<!doctype html><html><head><title>Polymarket Research Bot</title>
<meta http-equiv="refresh" content="10">
<style>
 body{font-family:monospace;background:#111;color:#ddd;margin:2em}
 table{border-collapse:collapse;margin:1em 0}td,th{border:1px solid #444;padding:4px 10px}
 h2{color:#8cf} .green{color:#5f5}.red{color:#f55} .num{text-align:right}
</style></head><body>
<h1>Polymarket Copy-Trading Research Bot v3 {{ '&mdash; DRY RUN' if dry_run else '&mdash; LIVE' }}</h1>
<h2>Bot status</h2>
<p>Alive (&lt;60s heartbeat): <span class="green">{{ alive }}</span> /
 stale: <span class="red">{{ stale }}</span> of {{ total_bots }} bots
 &mdash; halted by circuit breaker: <span class="red">{{ halted }}</span></p>
<h2>Layer volumes &amp; P&amp;L</h2>
<table><tr><th></th><th>last hour</th><th>total</th><th>cumulative P&amp;L</th><th>win rate</th></tr>
<tr><td>Layer 1 signals</td><td class="num">{{ l1_hour }}</td><td class="num">{{ l1_total }}</td><td>&mdash;</td><td>&mdash;</td></tr>
{% for l in (2, 3) %}<tr><td>Layer {{ l }} executions</td>
<td class="num">{{ stats[l].hour }}</td><td class="num">{{ stats[l].total }}</td>
<td class="num">{{ '%+.2f' % stats[l].pnl }}</td>
<td class="num">{{ '%.1f%%' % (stats[l].wr * 100) }}</td></tr>{% endfor %}</table>
<h2>Edge decay instruments</h2>
<table><tr><th>metric</th><th>value</th></tr>
<tr><td>median signal detection latency</td><td class="num">{{ '%.0f ms' % det_ms }}</td></tr>
<tr><td>avg slippage vs source fill</td><td class="num">{{ '%+.4f' % slip }}</td></tr>
<tr><td>total modeled taker fees</td><td class="num">{{ '%.2f USDC' % fees }}</td></tr></table>
<h2>Layer 2 vs Layer 3 win rate by category</h2>
<table><tr><th>category</th><th>L2 win rate</th><th>L2 trades</th><th>L3 win rate</th><th>L3 trades</th></tr>
{% for row in comparison %}<tr><td>{{ row.category }}</td>
<td class="num">{{ '%.1f%%' % (row.wr2 * 100) }}</td><td class="num">{{ row.n2 }}</td>
<td class="num">{{ '%.1f%%' % (row.wr3 * 100) }}</td><td class="num">{{ row.n3 }}</td></tr>{% endfor %}</table>
<h2>Top five bots</h2>
<table><tr><th>bot</th><th>layer</th><th>category</th><th>conviction</th><th>news</th><th>trades</th><th>win rate</th><th>P&amp;L</th></tr>
{% for b in top %}<tr><td>{{ b['bot_id'] }}</td><td>{{ b['layer'] }}</td><td>{{ b['category'] }}</td>
<td class="num">{{ b['conviction_bin'] }}</td><td>{{ 'on' if b['news_filter'] else 'off' }}</td>
<td class="num">{{ b['trade_count'] }}</td><td class="num">{{ '%.1f%%' % (b['win_rate'] * 100) }}</td>
<td class="num">{{ '%+.2f' % b['cumulative_pnl'] }}</td></tr>{% endfor %}</table>
<h2>Recent trades (last 30, all layers)</h2>
<table><tr><th>time (UTC)</th><th>layer</th><th>bot</th><th>outcome</th><th>price</th><th>size</th><th>status</th><th>P&amp;L</th></tr>
{% for t in trades %}<tr><td>{{ t.when }}</td><td>{{ t['layer'] }}</td><td>{{ t['bot_id'] }}</td>
<td>{{ t['outcome'] }}</td><td class="num">{{ '%.3f' % (t['exec_price'] or 0) }}</td>
<td class="num">{{ '%.2f' % t['size_usdc'] }}</td><td>{{ t['status'] }}</td>
<td class="num">{{ '%+.2f' % t['current_pnl'] }}</td></tr>{% endfor %}</table>
</body></html>"""


def ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    db = ro()
    now = time.time()
    hb = db.execute("SELECT last_alive FROM heartbeats").fetchall()
    alive = sum(1 for r in hb if now - r["last_alive"] < 60)
    halted = db.execute("SELECT COUNT(*) n FROM bot_metadata WHERE halted=1").fetchone()["n"]

    l1_hour = db.execute("SELECT COUNT(*) n FROM layer_one_signals WHERE ts > ?",
                         (now - 3600,)).fetchone()["n"]
    l1_total = db.execute("SELECT COUNT(*) n FROM layer_one_signals").fetchone()["n"]

    stats = {}
    for layer in (2, 3):
        row = db.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN ts > ? THEN 1 ELSE 0 END) hour, "
            "COALESCE(SUM(current_pnl), 0) pnl, "
            "COALESCE(AVG(CASE WHEN status='resolved' THEN "
            "  (current_pnl > 0) END), 0) wr "
            "FROM layer_two_executions WHERE layer = ? AND is_paper = 0 "
            "AND status != 'failed'", (now - 3600, layer)).fetchone()
        stats[layer] = type("S", (), {"total": row["total"], "hour": row["hour"] or 0,
                                      "pnl": row["pnl"], "wr": row["wr"]})

    lat = [r["detection_latency_ms"] for r in db.execute(
        "SELECT detection_latency_ms FROM layer_one_signals WHERE "
        "detection_latency_ms IS NOT NULL ORDER BY ts DESC LIMIT 200")]
    det_ms = sorted(lat)[len(lat) // 2] if lat else 0
    row = db.execute("SELECT COALESCE(AVG(slippage),0) s, COALESCE(SUM(fee_usdc),0) f "
                     "FROM layer_two_executions WHERE is_paper=0 "
                     "AND status NOT IN ('failed','skipped')").fetchone()
    slip, fees = row["s"], row["f"]

    comparison = []
    for cat_row in db.execute("SELECT DISTINCT category FROM bot_metadata "
                              "WHERE category IS NOT NULL ORDER BY category"):
        cat = cat_row["category"]
        vals = {}
        for layer in (2, 3):
            r = db.execute(
                "SELECT COALESCE(AVG(e.current_pnl > 0), 0) wr, COUNT(*) n "
                "FROM layer_two_executions e JOIN bot_metadata b ON b.bot_id=e.bot_id "
                "WHERE e.layer=? AND b.category=? AND e.status='resolved' "
                "AND e.is_paper=0", (layer, cat)).fetchone()
            vals[layer] = r
        comparison.append(type("C", (), {"category": cat,
                                         "wr2": vals[2]["wr"], "n2": vals[2]["n"],
                                         "wr3": vals[3]["wr"], "n3": vals[3]["n"]}))

    top = db.execute("SELECT * FROM bot_metadata WHERE trade_count > 0 "
                     "ORDER BY cumulative_pnl DESC LIMIT 5").fetchall()
    trades = []
    for t in db.execute("SELECT * FROM layer_two_executions WHERE is_paper=0 "
                        "ORDER BY ts DESC LIMIT 30"):
        trades.append(type("T", (), {**dict(t), "when": time.strftime(
            "%m-%d %H:%M:%S", time.gmtime(t["ts"]))})())
    db.close()

    return render_template_string(
        PAGE, dry_run=config.dry_run, alive=alive, stale=len(hb) - alive,
        total_bots=len(hb), halted=halted, l1_hour=l1_hour, l1_total=l1_total,
        stats=stats, comparison=comparison, top=top, trades=trades,
        det_ms=det_ms, slip=slip, fees=fees)


if __name__ == "__main__":
    app.run(host=config["runtime"]["dashboard_host"],
            port=config["runtime"]["dashboard_port"])
