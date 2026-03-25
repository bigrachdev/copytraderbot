/**
 * API Client for communicating with the backend
 */

class APIClient {
    constructor() {
        this.baseURL = window.location.origin;
        this.timeout = 10000;
        // Check if in dev mode from URL or localStorage
        const params = new URLSearchParams(window.location.search);
        this.devMode = params.get('dev') === 'true' || localStorage.getItem('devMode') === 'true';
    }

    async request(method, endpoint, data = null) {
        const url = `${this.baseURL}${endpoint}`;
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
            },
        };

        // Add dev mode header if in development mode
        if (this.devMode) {
            options.headers['X-Dev-Mode'] = 'true';
        }

        // Add Telegram auth header if available
        if (window.Telegram?.WebApp?.initData) {
            options.headers['X-Telegram-Auth-Data'] = JSON.stringify(
                this.parseTelegramData()
            );
        }

        if (data) {
            options.body = JSON.stringify(data);
        }

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), this.timeout);

            const response = await fetch(url, {
                ...options,
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || `HTTP ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error(`API Error [${method} ${endpoint}]:`, error);
            throw error;
        }
    }

    parseTelegramData() {
        if (!window.Telegram?.WebApp?.initData) return {};
        
        const params = new URLSearchParams(window.Telegram.WebApp.initData);
        const data = {};
        
        for (const [key, value] of params) {
            try {
                data[key] = JSON.parse(value);
            } catch {
                data[key] = value;
            }
        }
        
        return data;
    }

    // ────── AUTH ──────────────────────────────────────────────────────────────
    
    async authTelegram(initData) {
        return this.request('POST', '/api/auth/telegram', initData);
    }

    async logout() {
        return this.request('POST', '/api/auth/logout');
    }

    async getAuthStatus() {
        return this.request('GET', '/api/auth/status');
    }

    // ────── DASHBOARD ──────────────────────────────────────────────────────────
    
    async getDashboard() {
        return this.request('GET', '/api/dashboard');
    }

    // ────── TRADES ──────────────────────────────────────────────────────────
    
    async getTrades(limit = 20, offset = 0) {
        return this.request('GET', `/api/trades?limit=${limit}&offset=${offset}`);
    }

    async getTradeDetail(tradeId) {
        return this.request('GET', `/api/trades/${tradeId}`);
    }

    // ────── WALLET ──────────────────────────────────────────────────────────
    
    async getWallet() {
        return this.request('GET', '/api/wallet');
    }

    async getWalletTokens() {
        return this.request('GET', '/api/wallet/tokens');
    }

    // ────── COPY TRADING ──────────────────────────────────────────────────────
    
    async getWhales() {
        return this.request('GET', '/api/copy-trading/whales');
    }

    async watchWhale(walletAddress, copyScale = 1.0) {
        return this.request('POST', '/api/copy-trading/watch', {
            wallet_address: walletAddress,
            copy_scale: copyScale,
        });
    }

    async getWatchedWallets() {
        return this.request('GET', '/api/copy-trading/watched');
    }

    async unwatchWallet(walletId) {
        return this.request('DELETE', `/api/copy-trading/unwatch/${walletId}`);
    }

    // ────── TRADING ──────────────────────────────────────────────────────────
    
    async analyzeToken(tokenAddress) {
        return this.request('POST', '/api/trading/analyze', {
            token_address: tokenAddress,
        });
    }

    async createSwap(inputMint, outputMint, amount, slippage = 2.0) {
        return this.request('POST', '/api/trading/swap', {
            input_mint: inputMint,
            output_mint: outputMint,
            amount,
            slippage,
        });
    }

    // ────── SETTINGS ──────────────────────────────────────────────────────────
    
    async getSettings() {
        return this.request('GET', '/api/settings');
    }

    async updateSettings(settings) {
        return this.request('PUT', '/api/settings', settings);
    }
}

// Export globally
window.api = new APIClient();
