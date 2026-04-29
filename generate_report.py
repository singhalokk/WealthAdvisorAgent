#!/usr/bin/env python3
"""
Generate a readable HTML report from a WealthAdvisor JSON output file.
Usage: python3 generate_report.py output/2025-04-24.json
"""

import json
import sys
import webbrowser
from pathlib import Path
from datetime import datetime


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def signal_badge(signal: str) -> str:
    signal = (signal or "").upper()
    colors = {"BUY": ("#22c55e", "#fff"), "SELL": ("#ef4444", "#fff"), "HOLD": ("#f59e0b", "#fff")}
    bg, fg = colors.get(signal, ("#6b7280", "#fff"))
    return f'<span style="background:{bg};color:{fg};padding:3px 10px;border-radius:12px;font-weight:700;font-size:0.85em">{signal}</span>'


def trend_badge(trend: str) -> str:
    trend = trend or ""
    icons = {
        "strong_uptrend":   ("↑↑", "#16a34a"),
        "uptrend":          ("↑",  "#22c55e"),
        "sideways":         ("→",  "#f59e0b"),
        "downtrend":        ("↓",  "#ef4444"),
        "strong_downtrend": ("↓↓", "#b91c1c"),
    }
    icon, color = icons.get(trend, ("?", "#6b7280"))
    label = trend.replace("_", " ").title()
    return f'<span style="color:{color};font-weight:600">{icon} {label}</span>'


def pct_cell(val) -> str:
    if val is None:
        return "<td>—</td>"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "<td>—</td>"
    color = "#16a34a" if v > 0 else "#ef4444" if v < 0 else "#374151"
    prefix = "+" if v > 0 else ""
    return f'<td style="color:{color};font-weight:600">{prefix}{v:.1f}%</td>'


def rsi_cell(val) -> str:
    if val is None:
        return "<td>—</td>"
    v = float(val)
    if v >= 70:
        color, note = "#ef4444", " ⚠"
    elif v <= 30:
        color, note = "#22c55e", " ⚠"
    else:
        color, note = "#374151", ""
    return f'<td style="color:{color};font-weight:600">{v:.1f}{note}</td>'


def fmt_cap(val) -> str:
    if not val:
        return "—"
    v = float(val)
    if v >= 1e12:
        return f"${v/1e12:.1f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def build_html(data: dict, source_file: str) -> str:
    advice = data.get("advice", {})
    analyses = data.get("analyses", {})

    style_info = advice.get("investing_style", {})
    summary = advice.get("portfolio_summary", {})
    recs = advice.get("stock_recommendations", [])
    alts = advice.get("alternatives", [])
    actions = advice.get("top_actions", [])
    day_trades = advice.get("day_trading_candidates", [])
    market_ctx = advice.get("market_context", "")
    disclaimer = advice.get("disclaimer", "")

    run_date = Path(source_file).stem
    try:
        run_date = datetime.strptime(run_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        pass

    style_colors = {
        "growth": "#16a34a", "value": "#2563eb", "dividend": "#d97706",
        "index": "#0891b2", "balanced": "#7c3aed", "aggressive": "#dc2626",
        "conservative": "#059669",
    }
    primary_style = style_info.get("primary_style", "balanced")
    style_color = style_colors.get(primary_style, "#374151")

    health_colors = {"excellent": "#16a34a", "good": "#2563eb", "fair": "#f59e0b", "poor": "#ef4444"}
    health = summary.get("overall_health", "")
    health_color = health_colors.get(health, "#374151")

    # ── Technical rows ────────────────────────────────────────────────────────
    tech_rows = ""
    for ticker, a in sorted(analyses.items()):
        if a.get("error"):
            tech_rows += f"<tr><td><strong>{ticker}</strong></td><td colspan='9' style='color:#ef4444'>Error: {a['error']}</td></tr>"
            continue
        sig = (a.get("signal") or "hold").upper()
        tech_rows += f"""
        <tr>
          <td><strong>{ticker}</strong><br><small style="color:#6b7280">{a.get('company_name','')}</small></td>
          <td><strong>${float(a.get('current_price',0)):.2f}</strong></td>
          <td>${float(a.get('ma_50',0)):.2f}</td>
          <td>${float(a.get('ma_200',0)):.2f}</td>
          {pct_cell(a.get('price_vs_ma200_pct'))}
          {rsi_cell(a.get('rsi_14'))}
          {pct_cell(a.get('momentum_1m_pct'))}
          {pct_cell(a.get('momentum_3m_pct'))}
          <td>{trend_badge(a.get('trend',''))}</td>
          <td>{signal_badge(sig)}</td>
        </tr>"""

    # ── Recommendation rows ───────────────────────────────────────────────────
    rec_rows = ""
    for r in recs:
        conf = r.get("confidence", "medium")
        conf_color = {"high": "#16a34a", "medium": "#f59e0b", "low": "#ef4444"}.get(conf, "#6b7280")
        signals_html = "".join(f'<li>{s}</li>' for s in r.get("key_signals", []))
        risks_html   = "".join(f'<li style="color:#ef4444">{s}</li>' for s in r.get("risk_factors", []))
        rec_rows += f"""
        <tr>
          <td><strong>{r.get('ticker','')}</strong><br><small style="color:#6b7280">{r.get('company_name','')}</small></td>
          <td style="text-align:center">{signal_badge(r.get('action','HOLD'))}</td>
          <td style="color:{conf_color};font-weight:600;text-align:center">{conf.upper()}</td>
          <td style="font-size:0.9em">{r.get('reasoning','')}</td>
          <td><ul style="margin:0;padding-left:16px;font-size:0.85em">{signals_html}</ul></td>
          <td style="font-size:0.85em">{r.get('price_target','N/A')}</td>
          <td><ul style="margin:0;padding-left:16px;font-size:0.85em">{risks_html}</ul></td>
        </tr>"""

    # ── Alternatives rows ─────────────────────────────────────────────────────
    alt_rows = ""
    for a in alts:
        alt_rows += f"""
        <tr>
          <td><strong style="color:#ef4444">{a.get('replaces','')}</strong></td>
          <td>→</td>
          <td><strong style="color:#16a34a">{a.get('alternative_ticker','')}</strong></td>
          <td>{a.get('alternative_name','')}</td>
          <td><span style="background:#e0f2fe;color:#0369a1;padding:2px 8px;border-radius:8px;font-size:0.8em">{a.get('type','').replace('_',' ')}</span></td>
          <td style="font-size:0.9em">{a.get('reason','')}</td>
        </tr>"""

    # ── Top actions ───────────────────────────────────────────────────────────
    action_items = ""
    for a in sorted(actions, key=lambda x: x.get("priority", 99)):
        action_items += f"""
        <div style="display:flex;gap:16px;padding:14px 0;border-bottom:1px solid #e5e7eb">
          <div style="background:#2563eb;color:#fff;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0">{a.get('priority','·')}</div>
          <div>
            <div style="font-weight:600;color:#111827">{a.get('action','')}</div>
            <div style="color:#6b7280;font-size:0.9em;margin-top:4px">{a.get('rationale','')}</div>
          </div>
        </div>"""

    # ── Day trading rows ──────────────────────────────────────────────────────
    day_trade_section = ""
    if day_trades:
        dt_rows = ""
        for c in day_trades:
            d = (c.get("direction") or "long").lower()
            d_color = "#16a34a" if d == "long" else "#ef4444"
            dt_rows += f"""
            <tr>
              <td><strong>{c.get('ticker','')}</strong></td>
              <td style="color:{d_color};font-weight:700">{d.upper()}</td>
              <td>{c.get('entry_zone','N/A')}</td>
              <td style="color:#16a34a;font-weight:600">{c.get('exit_target','N/A')}</td>
              <td style="color:#ef4444;font-weight:600">{c.get('stop_loss','N/A')}</td>
              <td style="font-size:0.9em">{c.get('rationale','')}</td>
            </tr>"""
        day_trade_section = f"""
        <div class="card">
          <h2>⚡ Day Trading Candidates</h2>
          <p style="color:#ef4444;font-weight:600">⚠ Day trading carries significant risk. Always use strict stop losses.</p>
          <table>
            <thead><tr><th>Ticker</th><th>Direction</th><th>Entry Zone</th><th>Exit Target</th><th>Stop Loss</th><th>Rationale</th></tr></thead>
            <tbody>{dt_rows}</tbody>
          </table>
        </div>"""

    sectors_html = " ".join(
        f'<span style="background:#f3f4f6;padding:4px 10px;border-radius:12px;font-size:0.85em">{s}</span>'
        for s in summary.get("sectors", [])
    )
    strengths_html = "".join(f'<li style="color:#16a34a">✓ {s}</li>' for s in summary.get("strengths", []))
    weaknesses_html = "".join(f'<li style="color:#ef4444">✗ {s}</li>' for s in summary.get("weaknesses", []))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wealth Advisor Report — {run_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f9fafb; color: #111827; }}
  .header {{ background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%); color: white; padding: 32px 40px; }}
  .header h1 {{ font-size: 1.8em; font-weight: 700; }}
  .header p {{ opacity: 0.8; margin-top: 6px; }}
  .container {{ max-width: 1300px; margin: 0 auto; padding: 32px 24px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; margin-bottom: 24px; }}
  .card {{ background: white; border-radius: 12px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 24px; }}
  .card h2 {{ font-size: 1.1em; font-weight: 700; color: #111827; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #f3f4f6; }}
  .stat-label {{ font-size: 0.8em; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }}
  .stat-value {{ font-size: 1.6em; font-weight: 700; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th {{ background: #f9fafb; padding: 10px 12px; text-align: left; font-size: 0.8em; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 2px solid #e5e7eb; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: middle; }}
  tr:hover td {{ background: #f9fafb; }}
  .disclaimer {{ background: #fef3c7; border: 1px solid #fcd34d; border-radius: 8px; padding: 14px 18px; font-size: 0.85em; color: #92400e; margin-top: 24px; }}
  @media (max-width: 768px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} table {{ font-size: 0.8em; }} }}
</style>
</head>
<body>

<div class="header">
  <h1>📈 Wealth Advisor Report</h1>
  <p>Generated on {run_date} · {len(analyses)} holdings analyzed</p>
</div>

<div class="container">

  <!-- Style + Summary -->
  <div class="grid-2">
    <div class="card">
      <h2>Your Investing Style</h2>
      <div style="font-size:1.8em;font-weight:800;color:{style_color};text-transform:uppercase">{primary_style}</div>
      <div style="margin:10px 0;color:#374151">{style_info.get('description','')}</div>
      <div class="stat-label" style="margin-top:12px">Risk Level</div>
      <div style="font-weight:700;color:{style_color}">{(style_info.get('risk_level') or '').upper()}</div>
    </div>
    <div class="card">
      <h2>Portfolio Health</h2>
      <div style="display:flex;gap:32px;margin-bottom:16px">
        <div>
          <div class="stat-label">Overall Health</div>
          <div class="stat-value" style="color:{health_color}">{health.upper()}</div>
        </div>
        <div>
          <div class="stat-label">Diversification</div>
          <div class="stat-value">{summary.get('diversification_score','?')}<span style="font-size:0.5em;color:#6b7280">/10</span></div>
        </div>
        <div>
          <div class="stat-label">Holdings</div>
          <div class="stat-value">{summary.get('total_holdings', len(analyses))}</div>
        </div>
      </div>
      <div style="margin-bottom:10px">{sectors_html}</div>
      <ul style="padding-left:18px;font-size:0.9em;line-height:1.8">{strengths_html}{weaknesses_html}</ul>
    </div>
  </div>

  <!-- Technical Analysis -->
  <div class="card">
    <h2>📊 Technical Analysis</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Ticker</th><th>Price</th><th>50-MA</th><th>200-MA</th>
            <th>vs 200-MA</th><th>RSI-14</th><th>Mom 1M</th><th>Mom 3M</th>
            <th>Trend</th><th>Signal</th>
          </tr>
        </thead>
        <tbody>{tech_rows}</tbody>
      </table>
    </div>
    <div style="margin-top:12px;font-size:0.8em;color:#6b7280">
      RSI &gt; 70 = overbought ⚠ &nbsp;|&nbsp; RSI &lt; 30 = oversold ⚠ &nbsp;|&nbsp; Mom = price momentum vs N months ago
    </div>
  </div>

  <!-- Recommendations -->
  <div class="card">
    <h2>🤖 AI Recommendations</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr><th>Ticker</th><th>Action</th><th>Confidence</th><th>Reasoning</th><th>Key Signals</th><th>Price Target</th><th>Risk Factors</th></tr>
        </thead>
        <tbody>{rec_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Alternatives -->
  {'<div class="card"><h2>🔄 Alternative Suggestions</h2><div style="overflow-x:auto"><table><thead><tr><th>Replace</th><th></th><th>With</th><th>Name</th><th>Type</th><th>Reason</th></tr></thead><tbody>' + alt_rows + '</tbody></table></div></div>' if alts else ''}

  <!-- Top Actions + Market Context -->
  <div class="grid-2">
    <div class="card">
      <h2>✅ Priority Action Plan</h2>
      {action_items}
    </div>
    <div class="card">
      <h2>🌐 Market Context</h2>
      <p style="color:#374151;line-height:1.7">{market_ctx}</p>
    </div>
  </div>

  <!-- Day Trading -->
  {day_trade_section}

  <!-- Disclaimer -->
  <div class="disclaimer">⚠ {disclaimer}</div>

</div>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_report.py output/2025-04-24.json")
        sys.exit(1)

    source = sys.argv[1]
    data = load_json(source)

    out_path = Path(source).with_suffix(".html")
    out_path.write_text(build_html(data, source), encoding="utf-8")

    print(f"Report saved to: {out_path}")
    webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
