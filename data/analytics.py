"""
Advanced analytics and trading statistics
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from data.database import db
import statistics

logger = logging.getLogger(__name__)


class TradingAnalytics:
    """Analyze trading performance and statistics"""
    
    def __init__(self):
        pass
    
    def calculate_performance_metrics(self, user_id: int) -> Dict:
        """Calculate comprehensive performance metrics"""
        trades = db.get_user_trades(user_id, limit=1000)
        
        if not trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'profit_factor': 0,
                'avg_profit': 0,
                'max_drawdown': 0
            }
        
        # Calculate metrics
        winning_trades = [t for t in trades if t['output_amount'] > t['input_amount']]
        losing_trades = [t for t in trades if t['output_amount'] < t['input_amount']]
        
        profits = []
        for trade in trades:
            pnl = trade['output_amount'] - trade['input_amount']
            profits.append(pnl)
        
        total_profit = sum(profits)
        total_loss = sum([p for p in profits if p < 0])
        
        metrics = {
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': (len(winning_trades) / len(trades) * 100) if trades else 0,
            'avg_profit_per_trade': statistics.mean(profits) if profits else 0,
            'total_profit': total_profit,
            'total_loss': abs(total_loss),
            'profit_factor': (total_profit / abs(total_loss)) if total_loss != 0 else 0,
            'max_drawdown': self._calculate_max_drawdown(profits)
        }
        
        logger.info(f"📊 Performance: {len(winning_trades)}/{len(trades)} wins ({metrics['win_rate']:.1f}%)")
        return metrics
    
    def _calculate_max_drawdown(self, profits: List[float]) -> float:
        """Calculate maximum drawdown"""
        if not profits:
            return 0
        
        cumulative = 0
        peak = 0
        max_dd = 0
        
        for profit in profits:
            cumulative += profit
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        
        return max_dd
    
    def get_trading_stats_by_dex(self, user_id: int) -> Dict:
        """Get performance by DEX"""
        trades = db.get_user_trades(user_id, limit=1000)
        
        dex_stats = {}
        for trade in trades:
            dex = trade['dex']
            if dex not in dex_stats:
                dex_stats[dex] = {
                    'trades': 0,
                    'total_volume': 0,
                    'wins': 0,
                    'losses': 0
                }
            
            dex_stats[dex]['trades'] += 1
            dex_stats[dex]['total_volume'] += trade['input_amount']
            
            if trade['output_amount'] > trade['input_amount']:
                dex_stats[dex]['wins'] += 1
            else:
                dex_stats[dex]['losses'] += 1
        
        return dex_stats
    
    def get_hourly_volume(self, user_id: int, hours: int = 24) -> List[Dict]:
        """Get hourly trading volume"""
        trades = db.get_user_trades(user_id, limit=1000)
        now = datetime.now()
        cutoff = now - timedelta(hours=hours)
        
        hourly_stats = {}
        for trade in trades:
            # Assuming trade has created_at timestamp
            trade_time = trade['created_at']
            hour_key = trade_time.strftime('%Y-%m-%d %H:00')
            
            if hour_key not in hourly_stats:
                hourly_stats[hour_key] = {'volume': 0, 'trades': 0}
            
            hourly_stats[hour_key]['volume'] += trade['input_amount']
            hourly_stats[hour_key]['trades'] += 1
        
        return hourly_stats
    
    def get_top_tokens_traded(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Get most traded tokens"""
        trades = db.get_user_trades(user_id, limit=1000)
        
        token_stats = {}
        for trade in trades:
            token = trade['output_mint']
            if token not in token_stats:
                token_stats[token] = {
                    'trades': 0,
                    'total_amount': 0,
                    'wins': 0
                }
            
            token_stats[token]['trades'] += 1
            token_stats[token]['total_amount'] += trade['output_amount']
            
            if trade['output_amount'] > trade['input_amount']:
                token_stats[token]['wins'] += 1
        
        # Sort by number of trades
        sorted_tokens = sorted(token_stats.items(), 
                              key=lambda x: x[1]['trades'], 
                              reverse=True)[:limit]
        
        return [{'token': t[0], **t[1]} for t in sorted_tokens]
    
    def get_copy_trading_stats(self, user_id: int) -> Dict:
        """Get copy trading specific statistics"""
        trades = db.get_user_trades(user_id, limit=1000)
        copy_trades = [t for t in trades if t['is_copy']]
        
        if not copy_trades:
            return {'copy_trades': 0, 'success_rate': 0}
        
        successful = [t for t in copy_trades if t['output_amount'] > t['input_amount']]
        
        return {
            'total_copy_trades': len(copy_trades),
            'successful_copies': len(successful),
            'success_rate': (len(successful) / len(copy_trades) * 100) if copy_trades else 0,
            'total_copied_volume': sum([t['input_amount'] for t in copy_trades])
        }
    
    def generate_daily_report(self, user_id: int) -> str:
        """Generate daily trading report"""
        metrics = self.calculate_performance_metrics(user_id)
        dex_stats = self.get_trading_stats_by_dex(user_id)
        top_tokens = self.get_top_tokens_traded(user_id, limit=5)
        copy_stats = self.get_copy_trading_stats(user_id)
        
        report = (
            f"📊 **Daily Trading Report**\n\n"
            f"**Overall Performance:**\n"
            f"• Total Trades: {metrics['total_trades']}\n"
            f"• Win Rate: {metrics['win_rate']:.1f}%\n"
            f"• Total Profit: ${metrics['total_profit']:.2f}\n"
            f"• Profit Factor: {metrics['profit_factor']:.2f}\n\n"
            f"**Copy Trading:**\n"
            f"• Copied Trades: {copy_stats['total_copy_trades']}\n"
            f"• Success Rate: {copy_stats['success_rate']:.1f}%\n\n"
            f"**Top DEXs:**\n"
        )
        
        for dex, stats in dex_stats.items():
            dex_wr = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
            report += f"• {dex.upper()}: {stats['trades']} trades, {dex_wr:.1f}% WR\n"
        
        return report


analytics = TradingAnalytics()
