#!/usr/bin/env python3
"""
Wealth Advisor CLI
Usage: python main.py <portfolio_file> [--output results.json]
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict

import click
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.text import Text

from advisor import get_portfolio_advice
from market_data import StockAnalysis, analyze_portfolio
from portfolio_reader import read_portfolio

console = Console()

_SIGNAL_COLOR = {'buy': 'green', 'sell': 'red', 'hold': 'yellow',
                 'BUY': 'green', 'SELL': 'red', 'HOLD': 'yellow'}
_TREND_LABEL = {
    'strong_uptrend':   '[bold green]↑↑ Strong Up[/bold green]',
    'uptrend':          '[green]↑  Uptrend[/green]',
    'sideways':         '[yellow]→  Sideways[/yellow]',
    'downtrend':        '[red]↓  Downtrend[/red]',
    'strong_downtrend': '[bold red]↓↓ Strong Down[/bold red]',
}
_HEALTH_COLOR = {'excellent': 'green', 'good': 'cyan', 'fair': 'yellow', 'poor': 'red'}
_STYLE_COLOR  = {
    'growth': 'green', 'value': 'blue', 'dividend': 'yellow',
    'index': 'cyan', 'balanced': 'magenta', 'aggressive': 'red', 'conservative': 'dark_green',
}


# ── Display helpers ───────────────────────────────────────────────────────────

def _signed(val: float, decimals: int = 1) -> str:
    prefix = '+' if val > 0 else ''
    return f"{prefix}{val:.{decimals}f}%"


def _colored_pct(val: float, decimals: int = 1) -> str:
    s = _signed(val, decimals)
    color = 'green' if val > 0 else 'red' if val < 0 else 'white'
    return f"[{color}]{s}[/{color}]"


def _section(title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]{title}[/bold cyan]")
    console.print()


def display_investing_style(advice: dict) -> None:
    style = advice.get('investing_style', {})
    primary = style.get('primary_style', 'unknown')
    color = _STYLE_COLOR.get(primary, 'white')
    risk = style.get('risk_level', 'unknown')

    console.print(Panel(
        f"[bold {color}]{primary.upper()} INVESTOR[/bold {color}]\n\n"
        f"{style.get('description', '')}\n\n"
        f"Risk Level: [bold]{risk.upper()}[/bold]",
        title="[bold]Your Investing Style[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))


def display_portfolio_summary(advice: dict) -> None:
    s = advice.get('portfolio_summary', {})
    health = s.get('overall_health', 'unknown')
    hcolor = _HEALTH_COLOR.get(health, 'white')
    div_score = s.get('diversification_score', 'N/A')

    strengths = '\n'.join(f"  [green]✓[/green] {x}" for x in s.get('strengths', []))
    weaknesses = '\n'.join(f"  [red]✗[/red] {x}" for x in s.get('weaknesses', []))
    sectors = ', '.join(s.get('sectors', []))

    console.print(Panel(
        f"Overall Health: [{hcolor}][bold]{health.upper()}[/bold][/{hcolor}]   "
        f"Diversification: [bold]{div_score}/10[/bold]   "
        f"Holdings: [bold]{s.get('total_holdings', '?')}[/bold]\n\n"
        f"[dim]Sectors:[/dim] {sectors}\n\n"
        f"{strengths}\n{weaknesses}",
        title="[bold]Portfolio Summary[/bold]",
        border_style="blue",
        padding=(1, 2),
    ))


def display_technical_table(analyses: dict[str, StockAnalysis]) -> None:
    t = Table(title="Technical Analysis", show_header=True, header_style="bold cyan",
              show_lines=False, border_style="dim")

    t.add_column("Ticker",    style="bold", width=7)
    t.add_column("Price",     width=9,  justify="right")
    t.add_column("50-MA",     width=9,  justify="right")
    t.add_column("200-MA",    width=9,  justify="right")
    t.add_column("vs 200-MA", width=9,  justify="right")
    t.add_column("RSI",       width=7,  justify="right")
    t.add_column("Mom 3M",    width=9,  justify="right")
    t.add_column("Trend",     width=18)
    t.add_column("Signal",    width=8,  justify="center")

    for ticker, a in sorted(analyses.items()):
        if a.error:
            t.add_row(ticker, "[dim]error[/dim]", "-", "-", "-", "-", "-",
                      "[dim]N/A[/dim]", "[dim]ERR[/dim]")
            continue

        rsi_color = 'red' if a.rsi_14 > 70 else 'green' if a.rsi_14 < 30 else 'white'
        sig_color = _SIGNAL_COLOR.get(a.signal, 'white')
        trend_label = _TREND_LABEL.get(a.trend, a.trend)

        t.add_row(
            ticker,
            f"${a.current_price:.2f}",
            f"${a.ma_50:.2f}",
            f"${a.ma_200:.2f}",
            _colored_pct(a.price_vs_ma200_pct),
            f"[{rsi_color}]{a.rsi_14:.1f}[/{rsi_color}]",
            _colored_pct(a.momentum_3m_pct),
            trend_label,
            f"[bold {sig_color}]{a.signal.upper()}[/bold {sig_color}]",
        )

    console.print(t)


def display_recommendations(advice: dict) -> None:
    recs = advice.get('stock_recommendations', [])
    if not recs:
        return

    t = Table(title="AI Recommendations", show_header=True, header_style="bold magenta",
              show_lines=True, border_style="dim")
    t.add_column("Ticker",      width=7,  style="bold")
    t.add_column("Action",      width=7,  justify="center")
    t.add_column("Confidence",  width=10, justify="center")
    t.add_column("Key Signals", width=32)
    t.add_column("Reasoning",   width=58)

    conf_color = {'high': 'green', 'medium': 'yellow', 'low': 'red'}

    for rec in recs:
        action = rec.get('action', 'HOLD').upper()
        conf   = rec.get('confidence', 'medium').lower()
        ac     = _SIGNAL_COLOR.get(action, 'yellow')
        cc     = conf_color.get(conf, 'white')
        signals = '\n'.join(f"• {s}" for s in rec.get('key_signals', [])[:3])

        t.add_row(
            rec.get('ticker', ''),
            f"[bold {ac}]{action}[/bold {ac}]",
            f"[{cc}]{conf.upper()}[/{cc}]",
            signals,
            rec.get('reasoning', ''),
        )

    console.print(t)


def display_alternatives(advice: dict) -> None:
    alts = advice.get('alternatives', [])
    if not alts:
        return

    t = Table(title="Alternative Suggestions", show_header=True,
              header_style="bold yellow", show_lines=True, border_style="dim")
    t.add_column("Replace",   width=9,  style="bold red")
    t.add_column("→ With",    width=9,  style="bold green")
    t.add_column("Name",      width=28)
    t.add_column("Type",      width=18)
    t.add_column("Reason",    width=55)

    for alt in alts:
        t.add_row(
            alt.get('replaces', ''),
            alt.get('alternative_ticker', ''),
            alt.get('alternative_name', ''),
            alt.get('type', '').replace('_', ' '),
            alt.get('reason', ''),
        )

    console.print(t)


def display_top_actions(advice: dict) -> None:
    actions = advice.get('top_actions', [])
    if not actions:
        return

    lines = []
    for a in sorted(actions, key=lambda x: x.get('priority', 99)):
        lines.append(f"[bold cyan]{a.get('priority', '·')}.[/bold cyan] {a.get('action', '')}")
        if a.get('rationale'):
            lines.append(f"   [dim]{a['rationale']}[/dim]")
        lines.append("")

    console.print(Panel(
        '\n'.join(lines).strip(),
        title="[bold]Priority Actions[/bold]",
        border_style="green",
        padding=(1, 2),
    ))


def display_day_trading(advice: dict) -> None:
    candidates = advice.get('day_trading_candidates', [])
    if not candidates:
        return

    _section("Day Trading Candidates")

    t = Table(show_header=True, header_style="bold red", show_lines=True, border_style="dim")
    t.add_column("Ticker",     width=8,  style="bold")
    t.add_column("Direction",  width=8,  justify="center")
    t.add_column("Entry Zone", width=14, justify="right")
    t.add_column("Exit Target",width=12, justify="right")
    t.add_column("Stop Loss",  width=10, justify="right")
    t.add_column("Rationale",  width=50)

    dir_color = {'long': 'green', 'short': 'red'}
    for c in candidates:
        d = c.get('direction', 'long').lower()
        t.add_row(
            c.get('ticker', ''),
            f"[bold {dir_color.get(d, 'white')}]{d.upper()}[/bold {dir_color.get(d, 'white')}]",
            c.get('entry_zone', 'N/A'),
            c.get('exit_target', 'N/A'),
            c.get('stop_loss', 'N/A'),
            c.get('rationale', ''),
        )

    console.print(t)
    console.print("[bold red]⚠  Day trading carries significant risk. Use strict position sizing and stop losses.[/bold red]")


def display_results(analyses: dict[str, StockAnalysis], advice: dict) -> None:
    _section("Investing Style")
    display_investing_style(advice)

    _section("Portfolio Health")
    display_portfolio_summary(advice)

    _section("Technical Analysis")
    display_technical_table(analyses)

    _section("AI Recommendations")
    display_recommendations(advice)

    _section("Alternatives")
    display_alternatives(advice)

    _section("Action Plan")
    display_top_actions(advice)

    display_day_trading(advice)

    if advice.get('market_context'):
        _section("Market Context")
        console.print(Panel(advice['market_context'], border_style="dim", padding=(1, 2)))

    console.print()
    console.print(f"[dim italic]{advice.get('disclaimer', '')}[/dim italic]")
    console.print()


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.argument('portfolio_file', type=click.Path(exists=True))
@click.option('--output', '-o', type=click.Path(), default=None,
              help='Save full results to a JSON file')
def main(portfolio_file: str, output: str | None) -> None:
    """
    Wealth Advisor — AI-powered portfolio analysis.

    Provide a CSV or plain-text file listing your holdings (ticker required;
    shares, current value, cost basis, and gain/loss are optional).

    Example:  python main.py my_portfolio.csv
    """
    import os
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Wealth Advisor[/bold cyan]\n"
        "[dim]AI-powered portfolio analysis · technical indicators + Claude AI[/dim]",
        border_style="cyan",
    ))
    console.print()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]Error:[/red] ANTHROPIC_API_KEY environment variable is not set.")
        console.print("[dim]Get your key at https://console.anthropic.com and run:[/dim]")
        console.print("[bold]  export ANTHROPIC_API_KEY=sk-ant-...[/bold]")
        sys.exit(1)

    # ── Step 1: Read portfolio ────────────────────────────────────────────────
    try:
        portfolio_df = read_portfolio(portfolio_file)
    except Exception as exc:
        console.print(f"[red]Error reading portfolio:[/red] {exc}")
        sys.exit(1)

    tickers = portfolio_df['ticker'].tolist()
    console.print(f"[green]✓[/green] Loaded [bold]{len(tickers)}[/bold] holdings: "
                  f"[dim]{', '.join(tickers)}[/dim]")
    console.print()

    # ── Step 2: Fetch market data ─────────────────────────────────────────────
    analyses: dict[str, StockAnalysis] = {}
    done: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching market data...", total=len(tickers))

        def on_done(ticker: str) -> None:
            done.append(ticker)
            progress.update(task, advance=1,
                            description=f"Fetched [bold]{ticker}[/bold]")

        analyses = analyze_portfolio(tickers, on_progress=on_done)

    errors = [t for t, a in analyses.items() if a.error]
    ok_count = len(tickers) - len(errors)
    console.print(f"[green]✓[/green] Market data fetched for [bold]{ok_count}[/bold] holdings"
                  + (f" ([red]{len(errors)} failed: {', '.join(errors)}[/red])" if errors else ""))
    console.print()

    # ── Step 3: AI analysis ───────────────────────────────────────────────────
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        progress.add_task("Consulting Claude AI for portfolio advice...", total=None)
        try:
            advice = get_portfolio_advice(analyses, portfolio_df)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Claude returned invalid JSON:[/red] {exc}")
            sys.exit(1)
        except Exception as exc:
            console.print(f"[red]Error getting AI advice:[/red] {exc}")
            sys.exit(1)

    console.print("[green]✓[/green] AI analysis complete")

    # ── Step 4: Display ───────────────────────────────────────────────────────
    display_results(analyses, advice)

    # ── Step 5: Optionally save JSON ──────────────────────────────────────────
    if output:
        import numpy as np

        def _safe(obj):
            if isinstance(obj, dict):
                return {k: _safe(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_safe(v) for v in obj]
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return None if math.isnan(obj) else float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
            return obj

        payload = {
            "portfolio": portfolio_df.to_dict(orient='records'),
            "analyses": {k: _safe(asdict(v)) for k, v in analyses.items()},
            "advice": advice,
        }
        with open(output, 'w') as f:
            json.dump(payload, f, indent=2)
        console.print(f"[green]✓[/green] Results saved to [bold]{output}[/bold]")
        console.print()


if __name__ == '__main__':
    main()
