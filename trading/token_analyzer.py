"""
Smart Token Analyzer - Analyzes Solana token safety and risk factors.
Uses Solscan + Birdeye + DexScreener (all free/no-key endpoints).
"""
import logging
import requests
from typing import Dict, Tuple
from datetime import datetime
from config import (
    BIRDEYE_API_URL, SOLSCAN_API_URL, DEXSCREENER_API_URL,
    TOKEN_MIN_LIQUIDITY_SAFE, TOKEN_MIN_LIQUIDITY_MEDIUM, TOKEN_MIN_LIQUIDITY_RISKY,
    TOKEN_HOLDER_CONCENTRATION_DANGER, TOKEN_HOLDER_CONCENTRATION_WARNING,
    TOKEN_HONEYPOT_TAX_THRESHOLD, TOKEN_HIGH_TAX_THRESHOLD,
    TOKEN_RISK_SAFE, TOKEN_RISK_NORMAL, TOKEN_RISK_CAUTION,
    TOKEN_RISK_HIGH, TOKEN_RISK_VERY_HIGH,
    RPC_TIMEOUT,
)

logger = logging.getLogger(__name__)


class TokenAnalyzer:
    """Analyze token safety and risk metrics for Solana."""

    def __init__(self):
        self.birdeye_api      = BIRDEYE_API_URL
        self.solscan_api      = SOLSCAN_API_URL
        self.dex_screener_api = DEXSCREENER_API_URL
        logger.info("✅ Token analyzer initialized")

    # =========================================================================
    # Public entry point
    # =========================================================================

    def analyze_token(self, token_address: str, chain: str = 'solana') -> Dict:
        """
        Comprehensive Solana token analysis.
        Returns risk score (0=safe, 100=risky) and trade recommendation.
        """
        results = {
            'token_address': token_address,
            'chain': 'solana',
            'risk_score': 0,
            'safety_metrics': {},
            'trade_recommendation': 'ANALYZE',
            'suggested_trade_percent': 5.0,
            'warnings': [],
            'timestamp': datetime.now().isoformat()
        }

        try:
            m = results['safety_metrics']
            m['liquidity']           = self.check_liquidity(token_address)
            m['volume_market_cap']   = self.check_volume_ratio(token_address)
            m['social_presence']     = self.check_social_presence(token_address)
            m['contract_security']   = self.check_contract_security(token_address)
            m['holder_distribution'] = self.check_holder_distribution(token_address)
            m['mint_freeze']         = self.check_mint_freeze(token_address)
            m['dev_activity']        = self.check_dev_activity(token_address)
            m['honeypot']            = self.check_honeypot_solana(token_address)
            m['sell_restrictions']   = self.check_sell_restrictions(token_address)

            results['risk_score'] = self._calculate_risk_score(m)
            results['trade_recommendation'], results['suggested_trade_percent'] = \
                self._generate_recommendation(results['risk_score'], m)

            logger.info(f"Token analysis: {token_address[:10]}… risk={results['risk_score']:.0f}")
            return results

        except Exception as e:
            logger.error(f"analyze_token error: {e}")
            results['warnings'].append(f"Analysis error: {str(e)}")
            return results

    # =========================================================================
    # Solana checks
    # =========================================================================

    def check_contract_security(self, token_address: str) -> Dict:
        metrics = {'is_verified': False, 'is_open_source': False,
                   'audit_status': 'UNKNOWN', 'score': 50}
        try:
            r = requests.get(f"{self.solscan_api}/token/meta",
                             params={'tokenAddress': token_address}, timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                d = r.json().get('data', {})
                if d:
                    metrics['is_verified']   = d.get('verified', False)
                    metrics['is_open_source']= bool(d.get('source_code'))
                    metrics['score'] = 75 if metrics['is_verified'] else 50
        except Exception:
            pass
        return metrics

    def check_liquidity(self, token_address: str) -> Dict:
        metrics = {'pool_size_usd': 0, 'is_liquid': False, 'score': 50}
        try:
            r = requests.get(
                f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                pairs = r.json().get('pairs') or []
                if pairs:
                    liq_usd = float(pairs[0].get('liquidity', {}).get('usd', 0) or 0)
                    metrics['pool_size_usd'] = liq_usd
                    if liq_usd > TOKEN_MIN_LIQUIDITY_SAFE:
                        metrics['is_liquid'] = True
                        metrics['score'] = 90
                    elif liq_usd > TOKEN_MIN_LIQUIDITY_MEDIUM:
                        metrics['is_liquid'] = True
                        metrics['score'] = 70
                    elif liq_usd > TOKEN_MIN_LIQUIDITY_RISKY:
                        metrics['score'] = 45
                    else:
                        metrics['score'] = 20
        except Exception:
            pass
        return metrics

    def check_holder_distribution(self, token_address: str) -> Dict:
        metrics = {'top_10_percent': 0, 'is_concentrated': False, 'score': 50}
        try:
            r = requests.get(f"{self.solscan_api}/token/holder",
                             params={'tokenAddress': token_address, 'limit': 10},
                             timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                holders = r.json().get('data', [])
                top10 = sum(float(h.get('amount', 0)) for h in holders[:10])
                metrics['top_10_percent'] = top10
                if top10 > TOKEN_HOLDER_CONCENTRATION_DANGER:
                    metrics['is_concentrated'] = True
                    metrics['score'] = 20
                elif top10 > TOKEN_HOLDER_CONCENTRATION_WARNING:
                    metrics['score'] = 40
                else:
                    metrics['score'] = 75
        except Exception:
            pass
        return metrics

    def check_mint_freeze(self, token_address: str) -> Dict:
        metrics = {'mint_disabled': False, 'freeze_disabled': False, 'score': 50}
        try:
            r = requests.get(f"{self.solscan_api}/token/meta",
                             params={'tokenAddress': token_address}, timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                d = r.json().get('data', {})
                if d:
                    no_mint   = not d.get('mint_authority')
                    no_freeze = not d.get('freeze_authority')
                    metrics['mint_disabled']   = no_mint
                    metrics['freeze_disabled'] = no_freeze
                    metrics['score'] = 100 if (no_mint and no_freeze) else (80 if no_mint else 50)
        except Exception:
            pass
        return metrics

    def check_volume_ratio(self, token_address: str) -> Dict:
        metrics = {'volume_24h': 0, 'market_cap': 0, 'ratio': 0, 'score': 50}
        try:
            r = requests.get(
                f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                pairs = r.json().get('pairs') or []
                if pairs:
                    vol = float(pairs[0].get('volume', {}).get('h24', 0) or 0)
                    mc  = float(pairs[0].get('marketCap', 0) or 0)
                    metrics['volume_24h']  = vol
                    metrics['market_cap']  = mc
                    if mc > 0:
                        ratio = (vol / mc) * 100
                        metrics['ratio'] = ratio
                        if 10 <= ratio <= 50:
                            metrics['score'] = 85
                        elif ratio < 5:
                            metrics['score'] = 30
                        elif ratio > 100:
                            metrics['score'] = 40
                        else:
                            metrics['score'] = 60
        except Exception:
            pass
        return metrics

    def check_social_presence(self, token_address: str) -> Dict:
        metrics = {'has_twitter': False, 'has_website': False, 'score': 50}
        try:
            r = requests.get(
                f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                pairs = r.json().get('pairs') or []
                if pairs:
                    info = pairs[0].get('info', {})
                    metrics['has_twitter'] = bool(info.get('socials'))
                    metrics['has_website'] = bool(info.get('websites'))
                    count = sum([metrics['has_twitter'], metrics['has_website']])
                    metrics['score'] = 40 + count * 20
        except Exception:
            pass
        return metrics

    def check_dev_activity(self, token_address: str) -> Dict:
        metrics = {'recent_activities': 0, 'is_active': False, 'score': 50}
        try:
            r = requests.get(f"{self.solscan_api}/token/transfer",
                             params={'tokenAddress': token_address, 'limit': 5},
                             timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                recent = len(r.json().get('data', []))
                metrics['recent_activities'] = recent
                metrics['is_active'] = recent > 2
                metrics['score'] = 75 if recent > 2 else 40
        except Exception:
            pass
        return metrics

    def check_honeypot_solana(self, token_address: str) -> Dict:
        """Solana honeypot check via DexScreener fees field."""
        metrics = {'is_honeypot': False, 'transfer_tax': 0,
                   'can_sell': True, 'score': 80}
        try:
            r = requests.get(
                f"{self.dex_screener_api}/latest/dex/tokens/{token_address}",
                timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                pairs = r.json().get('pairs') or []
                if pairs:
                    fees = pairs[0].get('fees', {})
                    sell_tax = float(fees.get('sellTax', 0) or 0)
                    if sell_tax > TOKEN_HONEYPOT_TAX_THRESHOLD:
                        metrics['is_honeypot'] = True
                        metrics['can_sell'] = False
                        metrics['score'] = 5
                    elif sell_tax > TOKEN_HIGH_TAX_THRESHOLD:
                        metrics['transfer_tax'] = sell_tax
                        metrics['score'] = 40
        except Exception:
            pass
        return metrics

    # Keep old name as alias for backwards compat
    def check_honeypot(self, token_address: str) -> Dict:
        return self.check_honeypot_solana(token_address)

    def check_sell_restrictions(self, token_address: str) -> Dict:
        metrics = {'has_vesting': False, 'has_cooldown': False,
                   'liquidity_locked': False, 'score': 80}
        try:
            r = requests.get(f"{self.solscan_api}/token/meta",
                             params={'tokenAddress': token_address}, timeout=RPC_TIMEOUT)
            if r.status_code == 200:
                d = r.json().get('data', {})
                if d:
                    if d.get('locked'):
                        metrics['liquidity_locked'] = True
                    if d.get('freeze_authority'):
                        metrics['has_cooldown'] = True
                        metrics['score'] = 50
        except Exception:
            pass
        return metrics

    # =========================================================================
    # Scoring
    # =========================================================================

    def _calculate_risk_score(self, metrics: Dict) -> float:
        weights = [
            ('contract_security',   0.15),
            ('liquidity',           0.20),
            ('holder_distribution', 0.15),
            ('mint_freeze',         0.10),
            ('volume_market_cap',   0.10),
            ('social_presence',     0.10),
            ('dev_activity',        0.05),
            ('honeypot',            0.10),
            ('sell_restrictions',   0.05),
        ]
        total_w, total_risk = 0.0, 0.0
        for name, w in weights:
            if name in metrics:
                score = metrics[name].get('score', 50)
                total_risk += (100 - score) * w
                total_w    += w
        return total_risk / total_w if total_w else 50.0

    def _generate_recommendation(self, risk_score: float, metrics: Dict) -> Tuple[str, float]:
        if metrics.get('honeypot', {}).get('is_honeypot'):
            return ('REJECT_HONEYPOT', 0)
        if metrics.get('holder_distribution', {}).get('is_concentrated'):
            return ('REJECT_CONCENTRATED', 0)
        if risk_score < TOKEN_RISK_SAFE:      return ('BUY_SAFE',          50.0)
        if risk_score < TOKEN_RISK_NORMAL:    return ('BUY_NORMAL',         30.0)
        if risk_score < TOKEN_RISK_CAUTION:   return ('BUY_CAUTION',        15.0)
        if risk_score < TOKEN_RISK_HIGH:      return ('BUY_HIGH_RISK',      10.0)
        if risk_score < TOKEN_RISK_VERY_HIGH: return ('BUY_VERY_HIGH_RISK',  5.0)
        return ('REJECT_TOO_RISKY', 0)


# Global instance
token_analyzer = TokenAnalyzer()
