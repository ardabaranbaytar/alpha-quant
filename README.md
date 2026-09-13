# Alpha Quant — Statistical Pairs Trading Bot

A modular quantitative trading research and simulation platform built for statistical arbitrage experimentation, portfolio presentation, and risk-engine design.

The system screens a configurable NASDAQ-100-based equity universe to detect potential statistical arbitrage opportunities between cointegrated stock pairs using rolling Z-score thresholds. It evaluates signals through a rule-based risk management engine and tracks simulated long/short positions through a responsive real-time web dashboard.

> ⚠️ **Disclaimer:** This project is developed strictly for educational, research, and portfolio presentation purposes. It does not constitute financial or investment advice. All trading operations, margin calculations, and equity curves are simulated. No real capital is deployed.

---

## Key Features

- Cointegration-based statistical pairs trading signal generation
- Rolling Z-score entry, exit, and hard-stop logic
- PostgreSQL-backed market data and position storage
- Simulated long/short trade execution
- Transaction cost and slippage modeling
- Rule-based portfolio risk controls
- Fundamental health-score filter for additional asset screening
- FastAPI + Jinja2 web dashboard for signal and position monitoring
- Environment-based configuration using `.env`
- Signed-session authentication and basic rate limiting for the dashboard

---

## Technical Architecture & Core Modules

The platform is built on a decoupled, modular architecture split into five operational layers:

- **Data Pipeline (`data_pipeline/`)**  
  Fetches hourly OHLCV market data with `yfinance`. The scheduler supports lightweight intraday updates and deeper historical synchronization jobs to preserve database consistency.

- **Signal Engine (`core/signal_generator.py`)**  
  Screens the asset matrix for potential mean-reversion opportunities. It evaluates pair relationships using the two-step Engle-Granger cointegration test and computes rolling spread statistics.

- **Fundamental Filter (`core/health_score.py`)**  
  Acts as a secondary risk overlay by assigning a corporate health score between 0 and 100 based on profitability, cash-to-debt structure, and operating margin indicators.

- **Risk & Execution Engine (`execution/execution_engine.py`)**  
  Processes active signals against portfolio-level constraints such as open-position limits, stock exposure limits, trailing stop-losses, daily drawdown limits, and cooldown windows. It also applies fixed transaction cost and slippage assumptions to simulated trades.

- **Web Dashboard (`web_app/`)**  
  Provides a FastAPI and Jinja2-based dashboard for monitoring active pair signals, open/closed simulated positions, and performance metrics. The interface includes a lightweight responsive frontend using Tailwind CSS and Three.js visual effects.

---

## Quantitative Strategy Methodology

The core strategy is based on **mean reversion**. For two stocks, `A` and `B`, the system estimates whether a linear combination of their prices forms a stationary spread.

The spread is modeled as:

$$
Spread_t = Price_{A,t} - \beta \cdot Price_{B,t}
$$

Where:

- $\beta$ is the hedge ratio.
- $Spread_t$ is the estimated stationary relationship between the two assets.

If the Engle-Granger cointegration test returns a p-value below the configured significance threshold, the pair becomes eligible for signal evaluation.

The rolling Z-score is computed as:

$$
Z_t = \frac{Ratio_t - \mu_W}{\sigma_W}
$$

Where:

- $Ratio_t$ is the current price ratio or spread-ratio value.
- $\mu_W$ is the rolling mean over the lookback window.
- $\sigma_W$ is the rolling standard deviation over the lookback window.
- $W$ is the configurable historical lookback window.

### Signal Logic

- **Short Spread** — When `Z > Z_entry`  
  Sell Stock A and buy Stock B, expecting the ratio to revert downward toward its historical mean.

- **Long Spread** — When `Z < -Z_entry`  
  Buy Stock A and sell Stock B, expecting the ratio to revert upward toward its historical mean.

- **Hard Stop** — When `|Z| >= Z_stop`  
  Exit or block the trade to reduce exposure to potential structural breaks or cointegration breakdown.

---

## Risk Management Logic

The simulation engine applies multiple controls before opening or closing positions:

- Maximum number of concurrent open positions
- Maximum overlapping exposure per stock
- Daily portfolio drawdown limit
- Position-level trailing stop-loss logic
- Cooldown period after position closure
- Transaction cost and slippage penalty
- Fundamental health-score filter
- Signal disappearance-based exit logic

These controls are designed to make the system more realistic than a pure signal generator by forcing every trade idea through a portfolio-risk layer.

---

## Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend & API Layer** | Python 3.13, FastAPI, SQLAlchemy |
| **Data Architecture** | PostgreSQL, pandas, numpy, statsmodels |
| **Market Data Ingestion** | yfinance, requests |
| **Automation** | APScheduler |
| **Frontend UI** | Jinja2 Templates, Tailwind CSS, Three.js |
| **Security Layer** | itsdangerous signed cookies, slowapi rate limiting |
| **Server Gateway** | Uvicorn |

---

## Project Directory Tree

```text
alpha_quant_bot/
├── config/
│   ├── settings.py          # Environment-based configuration
│   ├── database.py          # SQLAlchemy engine and session management
│   ├── risk_config.py       # Portfolio and trade-level risk parameters
│   └── logging_config.py    # Centralized logging configuration
├── core/
│   ├── signal_generator.py  # Cointegration screening and Z-score signal engine
│   ├── health_score.py      # Fundamental corporate scoring filter
│   └── analytics.py         # Quant performance metrics
├── data_pipeline/
│   ├── yfinance_fetcher.py  # OHLCV ingestion and market data updates
│   └── scheduler.py         # APScheduler-based data synchronization jobs
├── execution/
│   └── execution_engine.py  # Simulated position management and PnL logic
├── logs/                    # Runtime logs, auto-generated and ignored by Git
├── tests/                   # Future unit and integration tests
├── web_app/
│   ├── templates/
│   │   ├── dashboard.html   # Signal monitoring dashboard
│   │   ├── login.html       # Authenticated dashboard login page
│   │   └── positions.html   # Position ledger page
│   └── app.py               # FastAPI app, middleware, auth, and routes
├── .env.example             # Environment variable template
├── .gitignore               # Git ignore rules for secrets, cache, logs, and outputs
├── requirements.txt         # Python dependencies
└── run_bot.py               # Main application entry point
```

---

## Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/ardabaranbaytar/alpha-quant-bot.git
cd alpha-quant-bot
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate it:

```bash
# Windows PowerShell
venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Open `.env` and configure your local credentials:

```env
DB_USER=postgres
DB_PASS=your_secure_db_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=financial_analysis

APP_USERNAME=your_admin_username_here
APP_PASSWORD=your_super_strong_dashboard_password
SESSION_SECRET=your_cryptographic_random_hex_secret

ALLOWED_ORIGINS=http://localhost:8000
LOG_LEVEL=INFO
```

## Database Setup

The system expects a local PostgreSQL database with the required market-data and position-tracking tables.

Core database responsibilities:

- `stock_prices` — Stores historical OHLCV market data.
- `company_balance_sheets` — Stores fundamental financial indicators.
- `positions` — Stores open and closed simulated long/short trades.

Make sure your PostgreSQL instance is running and your `.env` credentials match your local database configuration.

---

## Running the Platform

Start the bot and web dashboard:

```bash
python run_bot.py
```

Then open your browser:

```text
http://localhost:8000
```

---

## Project Status

This project is currently designed as a **local research and simulation system**. It does not send real market orders and does not connect to a live brokerage account.

Current focus areas:

- Statistical arbitrage research
- Pair selection and signal validation
- Simulated portfolio-risk management
- Dashboard-based monitoring
- Local PostgreSQL-backed trade tracking

---

## Quantitative Research Roadmap

- [ ] Add a broker-agnostic paper-trading execution adapter with strict risk controls.
- [ ] Implement an event-driven backtesting framework.
- [ ] Add walk-forward optimization for pair and parameter validation.
- [ ] Add Monte Carlo simulation for robustness testing.
- [ ] Improve portfolio-level analytics, including exposure attribution and capital utilization.
- [ ] Add multi-factor alpha filters such as momentum, volatility, and sentiment features.
- [ ] Expand test coverage with unit and integration tests.
- [ ] Add optional professional data-feed support as a replacement for public data sources.

---

## Security Notes

This project uses environment variables for local secrets and credentials. The repository should only include `.env.example`, never a real `.env` file.

Before deploying the dashboard outside a local environment:

- Require a strong `APP_PASSWORD`.
- Use a strong random `SESSION_SECRET`.
- Enable secure cookies in production.
- Restrict allowed origins.
- Avoid exposing the dashboard publicly without proper authentication.

---

## License

Distributed under the MIT License. See `LICENSE` for more information.

