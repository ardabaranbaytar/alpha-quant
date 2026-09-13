class RiskConfig:
    # Portfolio limits
    MAX_OPEN_POSITIONS = 5

    # Maximum loss limits
    MAX_DAILY_LOSS_PCT = 0.02
    MAX_PORTFOLIO_DRAWDOWN_PCT = 0.15

    # Position sizing
    MAX_POSITION_WEIGHT = 0.10
    MAX_RISK_PER_TRADE = 0.01

    # Exposure limits
    MAX_ASSET_EXPOSURE = 0.20
    MAX_STRATEGY_EXPOSURE = 0.40

    # Execution
    TRANSACTION_COST_RATE = 0.0015

    # Trading controls
    COOLDOWN_HOURS = 4


risk_config = RiskConfig()