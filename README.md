# Wealth Advisor

AI-powered stock portfolio analysis using live market data and Claude AI. Upload your portfolio, get buy/sell/hold recommendations, technical analysis, alternative suggestions, and a beautiful HTML report — all automatically.

![Python](https://img.shields.io/badge/python-3.8+-blue) ![Flask](https://img.shields.io/badge/flask-3.0-green) ![Claude AI](https://img.shields.io/badge/Claude-Sonnet-orange)

---

## What It Does

- **Reads your portfolio** from a CSV or text file (or type it directly in the web UI)
- **Fetches live market data** from Yahoo Finance for each holding
- **Computes technical indicators** — 50/200-day moving averages, RSI, MACD, momentum
- **AI-powered analysis** via Claude — investing style detection, buy/sell/hold per stock, alternative suggestions, day trading candidates
- **Generates a visual HTML report** you can view in browser or download
- **Runs on a schedule** — automatically every Monday, Wednesday, and Friday at 8 AM, or whenever you update your portfolio file

---

## Prerequisites

- Python 3.8 or higher
- An Anthropic API key — get one free at [console.anthropic.com](https://console.anthropic.com)

---

## Installation

**1. Clone the repository**
```bash
git clone https://github.com/singhalokk/WealthAdvisor.git
cd WealthAdvisor
```

**2. Install dependencies**
```bash
pip3 install -r requirements.txt
```

**3. Set your Anthropic API key**
```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-your-key-here' >> ~/.zshrc
source ~/.zshrc
```
> On Linux or if using bash: replace `~/.zshrc` with `~/.bashrc`

---

## Usage

### Web Interface (recommended)

Start the server:
```bash
python3 web_app.py
```

Open **http://localhost:8080** in your browser. Create an account, upload your portfolio, and click **Run Analysis**.

---

### Command Line

```bash
python3 main.py your_portfolio.csv
```

With JSON output saved:
```bash
python3 main.py your_portfolio.csv --output output/results.json
```

Generate an HTML report from a saved JSON:
```bash
python3 generate_report.py output/results.json
```

---

## Portfolio File Format

Only the **Ticker** column is required. All other columns are optional.

| Column | Required | Example |
|--------|----------|---------|
| Ticker | ✅ Yes | AAPL |
| Shares | Optional | 50 |
| Current Value | Optional | 9250.00 |
| Cost Basis | Optional | 6000.00 |
| Gain/Loss | Optional | 3250.00 |

**CSV example:**
```csv
Ticker,Shares,Current Value,Cost Basis,Gain/Loss
AAPL,50,9250,6000,3250
MSFT,30,12300,9000,3300
NVDA,15,19500,8000,11500
SPY,20,11200,8500,2700
JNJ,25,3700,3900,-200
```

**Plain text (tickers only):**
```
AAPL
MSFT
NVDA
SPY
```

Column names are flexible — *Symbol*, *Stock*, *Quantity*, *Market Value* etc. are all recognized automatically.

---

## Scheduled Daily Analysis

The included `run_daily.sh` script runs automatically via cron and skips days when it isn't needed.

**Set up the schedule (runs Mon / Wed / Fri at 8 AM):**
```bash
crontab -e
```
Add this line:
```
0 8 * * * /bin/zsh -l /path/to/WealthAdvisor/run_daily.sh
```

**The script runs if:**
- Today is Monday, Wednesday, or Friday, **OR**
- `portfolio.csv` was updated since the last run

Results are saved to `output/YYYY-MM-DD.json` and `output/YYYY-MM-DD.html`.

---

## Sharing Over the Internet (ngrok)

To let others access your locally running instance:

```bash
# Install ngrok
brew install ngrok        # macOS
# or download from https://ngrok.com

# Start tunnel (app must already be running on port 8080)
ngrok http 8080
```

Share the `https://xxx.ngrok-free.app` URL with anyone. Requires a free ngrok account.

---

## Project Structure

```
WealthAdvisor/
├── web_app.py           # Flask web application (auth, upload, job management)
├── main.py              # CLI entry point
├── portfolio_reader.py  # Parses CSV and text portfolio files
├── market_data.py       # Live Yahoo Finance data + technical indicators
├── advisor.py           # Claude AI portfolio analysis
├── generate_report.py   # HTML report generator
├── run_daily.sh         # Scheduled analysis runner
├── requirements.txt     # Python dependencies
├── sample_portfolio.csv # Example portfolio to try
└── templates/           # Web UI HTML templates
    ├── base.html
    ├── login.html
    ├── register.html
    ├── dashboard.html
    └── status.html
```

---

## Analysis Features

| Feature | Description |
|---------|-------------|
| 50 / 200-day MA | Trend direction and golden/death cross signals |
| RSI (14-day) | Overbought / oversold momentum |
| MACD | Bullish / bearish momentum confirmation |
| 1 / 3 / 6-month momentum | Short and medium-term price performance |
| Buy / Sell / Hold | Per-stock recommendation with confidence level |
| Investing style | Growth, Value, Dividend, Index, Balanced, etc. |
| Alternatives | Suggested replacement stocks or ETFs per holding |
| Day trading | Entry zone, exit target, and stop loss for candidates |

---

## Important Disclaimer

This tool is for **educational purposes only**. It does not constitute financial advice. Always consult a licensed financial advisor before making investment decisions.

---

## License

MIT
