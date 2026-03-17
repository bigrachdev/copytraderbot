"""
Smart Token Analyzer - Analyzes token safety and risk factors
Checks contract security, liquidity, holder distribution, honeypot detection, etc.
"""
import logging
import requests
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)


class TokenAnalyzer:
    """Analyze token safety and risk metrics"""
    
    def __init__(self):
        """Initialize token analyzer"""
        # API endpoints for token analysis
        self.magic_eden_api = "https://api-mainnet.magiceden.dev/rpc"
        self.birdeye_api = "https://public-api.birdeye.so"
        self.solscan_api = "https://api.solscan.io"
        self.dex_screener_api = "https://api.dexscreener.com"
        
        logger.info("✅ Token analyzer initialized")
    
    def analyze_token(self, token_address: str) -> Dict:
        """
        Comprehensive token analysis
        Returns risk score and detailed metrics
        """
        results = {
            'token_address': token_address,
            'risk_score': 0,  # 0-100, higher = riskier
            'safety_metrics': {},
            'trade_recommendation': 'ANALYZE',
            'suggested_trade_percent': 5.0,
            'warnings': [],
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Run all checks
            results['safety_metrics']['contract_security'] = self.check_contract_security(token_address)
            results['safety_metrics']['liquidity'] = self.check_liquidity(token_address)
            results['safety_metrics']['holder_distribution'] = self.check_holder_distribution(token_address)
            results['safety_metrics']['mint_freeze'] = self.check_mint_freeze(token_address)
            results['safety_metrics']['volume_market_cap'] = self.check_volume_ratio(token_address)
            results['safety_metrics']['social_presence'] = self.check_social_presence(token_address)
            results['safety_metrics']['dev_activity'] = self.check_dev_activity(token_address)
            results['safety_metrics']['honeypot'] = self.check_honeypot(token_address)
            results['safety_metrics']['sell_restrictions'] = self.check_sell_restrictions(token_address)
            
            # Calculate overall risk score
            results['risk_score'] = self._calculate_risk_score(results['safety_metrics'])
            
            # Generate recommendation
            results['trade_recommendation'], results['suggested_trade_percent'] = \
                self._generate_recommendation(results['risk_score'], results['safety_metrics'])
            
            logger.info(f"✅ Token analysis complete: {token_address[:10]}... Risk: {results['risk_score']}")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error analyzing token: {e}")
            results['warnings'].append(f"Analysis error: {str(e)}")
            return results
    
    def check_contract_security(self, token_address: str) -> Dict:
        """
        Check contract security:
        - Verified contract
        - Open source
        - Audit status
        - Recent updates
        """
        try:
            metrics = {
                'is_verified': False,
                'is_open_source': False,
                'audit_status': 'UNKNOWN',
                'recent_updates': False,
                'score': 50  # Default neutral
            }
            
            # Check if contract is verified on Solscan
            try:
                response = requests.get(
                    f"{self.solscan_api}/token/meta",
                    params={'tokenAddress': token_address},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data'):
                        metrics['is_verified'] = data['data'].get('verified', False)
                        metrics['is_open_source'] = bool(data['data'].get('source_code'))
                        if metrics['is_verified']:
                            metrics['score'] = 75
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking contract security: {e}")
            return {'score': 50, 'error': str(e)}
    
    def check_liquidity(self, token_address: str) -> Dict:
        """
        Check token liquidity:
        - Pool size
        - Liquidity locked %
        - Price impact for $1000 trade
        - Swap fees
        """
        try:
            metrics = {
                'pool_size_sol': 0,
                'liquidity_locked_percent': 0,
                'price_impact_1k': 0,
                'is_liquid': False,
                'score': 50
            }
            
            # Check liquidity from Dex Screener
            try:
                response = requests.get(
                    f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('pairs'):
                        pair = data['pairs'][0]
                        liquidity = pair.get('liquidity', {})
                        
                        # Check minimum liquidity (50 SOL = decent)
                        if liquidity.get('usd', 0) > 5000:  # ~5k USD
                            metrics['pool_size_sol'] = liquidity['usd'] / 100  # Rough conversion
                            metrics['is_liquid'] = True
                            metrics['score'] = 80
                        elif liquidity.get('usd', 0) > 1000:
                            metrics['score'] = 60
                        else:
                            metrics['warnings'] = ["Low liquidity pool"]
                            metrics['score'] = 30
                        
                        # Price impact analysis
                        price_impact = pair.get('priceImpact', {})
                        if price_impact:
                            impact_1k = price_impact.get('impact0', 0)
                            metrics['price_impact_1k'] = impact_1k
                            if impact_1k > 20:  # >20% impact = rug risk
                                metrics['score'] -= 30
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking liquidity: {e}")
            return {'score': 30, 'error': str(e)}
    
    def check_holder_distribution(self, token_address: str) -> Dict:
        """
        Check token holder distribution:
        - Top 10 holders %
        - Holder concentration
        - Whale risk
        """
        try:
            metrics = {
                'top_10_percent': 0,
                'top_holder_percent': 0,
                'holder_count': 0,
                'is_concentrated': False,
                'score': 50
            }
            
            # Check Solscan for holder data
            try:
                response = requests.get(
                    f"{self.solscan_api}/token/holder",
                    params={'tokenAddress': token_address, 'limit': 10},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data'):
                        holders = data['data']
                        total_owned_by_top10 = sum([float(h.get('amount', 0)) for h in holders[:10]])
                        metrics['top_10_percent'] = total_owned_by_top10
                        
                        if total_owned_by_top10 > 50:
                            metrics['score'] = 20  # High concentration = rug risk
                            metrics['is_concentrated'] = True
                        elif total_owned_by_top10 > 30:
                            metrics['score'] = 40
                        else:
                            metrics['score'] = 75  # Well distributed
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking holder distribution: {e}")
            return {'score': 50, 'error': str(e)}
    
    def check_mint_freeze(self, token_address: str) -> Dict:
        """
        Check if token mint/freeze authority is disabled
        - Mint disabled = good (can't create infinite supply)
        - Freeze disabled = good (can't freeze accounts)
        """
        try:
            metrics = {
                'mint_disabled': False,
                'freeze_disabled': False,
                'score': 50
            }
            
            try:
                # Check Solscan for token supply info
                response = requests.get(
                    f"{self.solscan_api}/token/meta",
                    params={'tokenAddress': token_address},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data'):
                        token_data = data['data']
                        # Both disabled = safe
                        if not token_data.get('mint_authority') and not token_data.get('freeze_authority'):
                            metrics['mint_disabled'] = True
                            metrics['freeze_disabled'] = True
                            metrics['score'] = 100
                        elif not token_data.get('mint_authority'):
                            metrics['mint_disabled'] = True
                            metrics['score'] = 80
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking mint/freeze: {e}")
            return {'score': 40, 'error': str(e)}
    
    def check_volume_ratio(self, token_address: str) -> Dict:
        """
        Check 24h volume vs market cap ratio
        - Healthy ratio: 10-50% daily volume/market cap
        - Low ratio: pump & dump risk
        - High ratio: potential volatility
        """
        try:
            metrics = {
                'volume_24h': 0,
                'market_cap': 0,
                'ratio': 0,
                'score': 50
            }
            
            try:
                response = requests.get(
                    f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('pairs'):
                        pair = data['pairs'][0]
                        volume = pair.get('volume', {}).get('h24', 0)
                        market_cap = pair.get('marketCap', 0)
                        
                        metrics['volume_24h'] = volume
                        metrics['market_cap'] = market_cap
                        
                        if market_cap > 0:
                            metrics['ratio'] = (volume / market_cap) * 100
                            
                            # Evaluate ratio
                            if 10 <= metrics['ratio'] <= 50:
                                metrics['score'] = 85  # Healthy
                            elif metrics['ratio'] < 5:
                                metrics['score'] = 30  # Possible pump & dump
                            elif metrics['ratio'] > 100:
                                metrics['score'] = 40  # High volatility
                            else:
                                metrics['score'] = 60
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking volume ratio: {e}")
            return {'score': 50, 'error': str(e)}
    
    def check_social_presence(self, token_address: str) -> Dict:
        """
        Check social presence:
        - Has Twitter/X
        - Has website
        - Has Discord
        - Community size
        """
        try:
            metrics = {
                'has_twitter': False,
                'has_website': False,
                'has_discord': False,
                'twitter_followers': 0,
                'score': 50
            }
            
            try:
                response = requests.get(
                    f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('pairs'):
                        pair = data['pairs'][0]
                        
                        # Check social links
                        if pair.get('twitter'):
                            metrics['has_twitter'] = True
                        if pair.get('website'):
                            metrics['has_website'] = True
                        
                        # Scoring based on presence
                        social_count = sum([
                            metrics['has_twitter'],
                            metrics['has_website'],
                            metrics['has_discord']
                        ])
                        
                        metrics['score'] = 40 + (social_count * 20)
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking social presence: {e}")
            return {'score': 40, 'error': str(e)}
    
    def check_dev_activity(self, token_address: str) -> Dict:
        """
        Check developer activity:
        - Recent contract updates
        - Developer wallet holdings
        - Developer wallet activity
        """
        try:
            metrics = {
                'recent_activities': 0,
                'dev_holding_percent': 0,
                'is_active': False,
                'score': 50
            }
            
            try:
                # Check recent transactions on Solscan
                response = requests.get(
                    f"{self.solscan_api}/token/transfer",
                    params={'tokenAddress': token_address, 'limit': 5},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data'):
                        recent = len(data['data'])
                        metrics['recent_activities'] = recent
                        if recent > 2:
                            metrics['is_active'] = True
                            metrics['score'] = 75
                        else:
                            metrics['score'] = 40
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking dev activity: {e}")
            return {'score': 40, 'error': str(e)}
    
    def check_honeypot(self, token_address: str) -> Dict:
        """
        Check if token is a honeypot:
        - Can you sell after buying?
        - Transfer tax
        - Unusual restrictions
        """
        try:
            metrics = {
                'is_honeypot': False,
                'transfer_tax': 0,
                'can_sell': True,
                'score': 80
            }
            
            try:
                # Check for known honeypot patterns
                # This is a simplified check - in production use Honeypot.is API
                response = requests.get(
                    f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('pairs'):
                        pair = data['pairs'][0]
                        
                        # Check if there's a buy/sell fee
                        if pair.get('fees'):
                            fees = pair['fees']
                            # If sell tax > 30% = honeypot
                            if fees.get('sellTax', 0) > 30:
                                metrics['is_honeypot'] = True
                                metrics['can_sell'] = False
                                metrics['score'] = 5
                            elif fees.get('sellTax', 0) > 5:
                                metrics['transfer_tax'] = fees.get('sellTax', 0)
                                metrics['score'] = 40
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking honeypot: {e}")
            return {'is_honeypot': False, 'score': 50, 'error': str(e)}
    
    def check_sell_restrictions(self, token_address: str) -> Dict:
        """
        Check for sell restrictions:
        - Vesting schedules
        - Locked liquidity
        - Cooldown periods
        """
        try:
            metrics = {
                'has_vesting': False,
                'has_cooldown': False,
                'liquidity_locked': False,
                'score': 80
            }
            
            try:
                response = requests.get(
                    f"{self.solscan_api}/token/meta",
                    params={'tokenAddress': token_address},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data'):
                        token_data = data['data']
                        
                        # Check for restrictions
                        if token_data.get('locked'):
                            metrics['liquidity_locked'] = True
                        
                        # If has freeze authority = can be restricted
                        if token_data.get('freeze_authority'):
                            metrics['has_cooldown'] = True
                            metrics['score'] = 50
            except:
                pass
            
            return metrics
        except Exception as e:
            logger.error(f"Error checking sell restrictions: {e}")
            return {'score': 60, 'error': str(e)}
    
    def _calculate_risk_score(self, metrics: Dict) -> float:
        """Calculate weighted risk score from all metrics"""
        try:
            scores = []
            weights = [
                ('contract_security', 0.15),
                ('liquidity', 0.20),
                ('holder_distribution', 0.15),
                ('mint_freeze', 0.10),
                ('volume_market_cap', 0.10),
                ('social_presence', 0.10),
                ('dev_activity', 0.05),
                ('honeypot', 0.10),
                ('sell_restrictions', 0.05),
            ]
            
            for metric_name, weight in weights:
                if metric_name in metrics:
                    score = metrics[metric_name].get('score', 50)
                    # Convert to risk score (100 = safe, 0 = risky)
                    risk_score = 100 - score
                    scores.append(risk_score * weight)
            
            # Return average risk score (0-100)
            return sum(scores) / sum([w for _, w in weights]) if scores else 50
        except Exception as e:
            logger.error(f"Error calculating risk score: {e}")
            return 50
    
    def _generate_recommendation(self, risk_score: float, metrics: Dict) -> Tuple[str, float]:
        """
        Generate trading recommendation and suggested trade percentage
        
        Risk Score: 0-100 (0=safe, 100=risky)
        """
        honeypot = metrics.get('honeypot', {}).get('is_honeypot', False)
        holder_conc = metrics.get('holder_distribution', {}).get('is_concentrated', False)
        
        # Auto-reject if honeypot
        if honeypot:
            return ('REJECT_HONEYPOT', 0)
        
        # Auto-reject if highly concentrated
        if holder_conc:
            return ('REJECT_CONCENTRATED', 0)
        
        # Generate recommendation based on risk
        if risk_score < 20:
            return ('BUY_SAFE', 50.0)  # Very safe - use up to 50%
        elif risk_score < 35:
            return ('BUY_NORMAL', 30.0)  # Normal - use up to 30%
        elif risk_score < 50:
            return ('BUY_CAUTION', 15.0)  # Caution - use up to 15%
        elif risk_score < 65:
            return ('BUY_HIGH_RISK', 10.0)  # High risk - use up to 10%
        elif risk_score < 80:
            return ('BUY_VERY_HIGH_RISK', 5.0)  # Very high - use only 5%
        else:
            return ('REJECT_TOO_RISKY', 0)  # Too risky - reject


# Global token analyzer
token_analyzer = TokenAnalyzer()
