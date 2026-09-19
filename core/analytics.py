import logging

import pandas as pd
from sqlalchemy import text

from config.database import db

logger = logging.getLogger(__name__)

class PerformanceAnalytics:
    def __init__(self):
        pass

    def _get_closed_positions(self) -> pd.DataFrame:
        """Fetch closed pair histories from the one-symbol-per-row positions schema."""
        query = """
            SELECT
                COALESCE(strategy_name, MIN(symbol)) AS pair,
                status,
                COALESCE(SUM(unrealized_pnl), 0) AS pnl,
                MIN(opened_at) AS entry_time,
                MAX(closed_at) AS exit_time
            FROM positions
            WHERE status = 'CLOSED'
            GROUP BY strategy_name, status
            ORDER BY MAX(closed_at) ASC;
        """
        try:
            with db.engine.connect() as conn:
                df = pd.read_sql(text(query), conn)
            
            if not df.empty:
                df['entry_time'] = pd.to_datetime(df['entry_time'])
                df['exit_time'] = pd.to_datetime(df['exit_time'])
            return df
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to read position history: %s", e)
            return pd.DataFrame()

    def generate_report(self, save_csv: bool = True) -> dict:
        """Calculates all quant metrics and returns a summary report dictionary."""
        df = self._get_closed_positions()
        
        if df.empty:
            logger.info("No closed positions found. Returning empty report.")
            return {
                "Total PnL": "$0.00", 
                "Win Rate": "%0.00", 
                "Profit Factor": "0.00", 
                "Max Drawdown": "$0.00"
            }

        total_trades = len(df)
        total_net_pnl = df['pnl'].sum()  
        
        winners = df[df['pnl'] > 0]
        losers = df[df['pnl'] <= 0]
        
        winner_count = len(winners)
        win_rate = (winner_count / total_trades) * 100 if total_trades > 0 else 0
        
        avg_win = winners['pnl'].mean() if not winners.empty else 0.0
        avg_loss = losers['pnl'].mean() if not losers.empty else 0.0
        
        total_profit = winners['pnl'].sum()
        total_loss = abs(losers['pnl'].sum())
        profit_factor = total_profit / total_loss if total_loss > 0 else total_profit

        df['holding_time'] = df['exit_time'] - df['entry_time']
        avg_holding_time = df['holding_time'].mean()
        
        pair_groups = df.groupby('pair')['pnl'].sum()
        best_pair = pair_groups.idxmax() if not pair_groups.empty else "N/A"
        worst_pair = pair_groups.idxmin() if not pair_groups.empty else "N/A"

        equity_curve = 100000 + df['pnl'].cumsum()
        cumulative_peak = equity_curve.cummax()
        drawdowns = equity_curve - cumulative_peak
        max_drawdown = drawdowns.min() if not drawdowns.empty else 0.0

        report = {
            "Status": "OPERATIONAL",
            "Total PnL": f"${total_net_pnl:,.2f}",
            "Win Rate": f"%{win_rate:.2f}",
            "Profit Factor": f"{profit_factor:.2f}",
            "Max Drawdown": f"${max_drawdown:,.2f}",
            "Average Win": f"${avg_win:,.2f}",
            "Average Loss": f"${avg_loss:,.2f}",
            "Number of Trades": total_trades,
            "Avg Holding Time": str(avg_holding_time).split('.')[0],
            "Best Pair": best_pair,
            "Worst Pair": worst_pair,
            "Timestamp": pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        if save_csv:
            try:
                report_df = pd.DataFrame(list(report.items()), columns=['Metric', 'Value'])
                report_df.to_csv("logs/performance_report.csv", index=False, encoding='utf-8')
                logger.info("Performance report saved to logs/performance_report.csv")
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not save CSV report: %s", e)

        return report

analytics_manager = PerformanceAnalytics()
