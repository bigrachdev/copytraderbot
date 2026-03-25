/**
 * Main application with routing and components
 */

class App {
    constructor() {
        this.currentRoute = 'dashboard';
        this.root = document.getElementById('root');
    }

    async init() {
        // Initialize auth
        const authenticated = await window.auth.init();

        if (!authenticated) {
            this.showLoginPage();
            return;
        }

        // Build UI
        this.buildUI();
        this.setupNavigation();
        this.loadDashboard();
    }

    showLoginPage() {
        this.root.innerHTML = `
            <div class="login-container">
                <div class="login-card">
                    <div class="logo">
                        <i class="fas fa-robot"></i>
                    </div>
                    <h1>DEX Copy Trading Bot</h1>
                    <p>Advanced Solana trading with AI-powered whale tracking</p>
                    
                    ${window.auth.isTelegramMiniApp() 
                        ? '<p class="loading">Authenticating with Telegram...</p>'
                        : `
                            <div class="auth-methods">
                                <button class="btn btn-primary" onclick="window.location.href='https://t.me/your_bot_username'">
                                    <i class="fab fa-telegram"></i> Open in Telegram
                                </button>
                                <p class="note">This app works best as a Telegram Mini App</p>
                            </div>
                        `
                    }
                </div>
            </div>
        `;
    }

    buildUI() {
        this.root.innerHTML = `
            <div class="app-container">
                <header class="app-header">
                    <div class="header-content">
                        <div class="logo-section">
                            <h1>DEX Bot</h1>
                        </div>
                        <div class="header-actions">
                            <button class="btn-icon" onclick="app.toggleSettings()" title="Settings">
                                <i class="fas fa-cog"></i>
                            </button>
                            <button class="btn-icon" onclick="app.showProfile()" title="Profile">
                                <i class="fas fa-user"></i>
                            </button>
                        </div>
                    </div>
                </header>

                <nav class="bottom-nav">
                    <button class="nav-item active" data-route="dashboard" onclick="app.navigate('dashboard')">
                        <i class="fas fa-chart-line"></i>
                        <span>Dashboard</span>
                    </button>
                    <button class="nav-item" data-route="trading" onclick="app.navigate('trading')">
                        <i class="fas fa-swap-alt"></i>
                        <span>Trading</span>
                    </button>
                    <button class="nav-item" data-route="copy-trading" onclick="app.navigate('copy-trading')">
                        <i class="fas fa-copy"></i>
                        <span>Copy</span>
                    </button>
                    <button class="nav-item" data-route="wallets" onclick="app.navigate('wallets')">
                        <i class="fas fa-wallet"></i>
                        <span>Wallets</span>
                    </button>
                </nav>

                <main class="app-main">
                    <div id="content" class="content"></div>
                </main>

                <div id="modal" class="modal"></div>
            </div>
        `;
    }

    setupNavigation() {
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => {
                document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
                item.classList.add('active');
            });
        });
    }

    navigate(route) {
        this.currentRoute = route;
        
        switch (route) {
            case 'dashboard':
                this.loadDashboard();
                break;
            case 'trading':
                this.loadTrading();
                break;
            case 'copy-trading':
                this.loadCopyTrading();
                break;
            case 'wallets':
                this.loadWallets();
                break;
        }
    }

    async loadDashboard() {
        const content = document.getElementById('content');
        content.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Loading...</div>';

        try {
            const data = await window.api.getDashboard();

            content.innerHTML = `
                <div class="dashboard-view">
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-label">Balance</div>
                            <div class="stat-value">${data.user.balance.toFixed(2)} SOL</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Total Trades</div>
                            <div class="stat-value">${data.stats.total_trades}</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Win Rate</div>
                            <div class="stat-value">${(data.stats.win_rate * 100).toFixed(1)}%</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Total Profit</div>
                            <div class="stat-value ${data.stats.total_profit >= 0 ? 'positive' : 'negative'}">
                                ${data.stats.total_profit.toFixed(2)} SOL
                            </div>
                        </div>
                    </div>

                    <div class="section">
                        <h2>Recent Trades</h2>
                        <div id="recent-trades" class="trades-list">
                            <div class="loading"><i class="fas fa-spinner fa-spin"></i></div>
                        </div>
                    </div>

                    <div class="section">
                        <h2>Quick Actions</h2>
                        <div class="action-buttons">
                            <button class="btn btn-primary" onclick="app.navigate('trading')">
                                <i class="fas fa-swap-alt"></i> Swap Tokens
                            </button>
                            <button class="btn btn-secondary" onclick="app.navigate('copy-trading')">
                                <i class="fas fa-copy"></i> Copy Traders
                            </button>
                        </div>
                    </div>
                </div>
            `;

            // Load recent trades
            this.loadRecentTrades();
        } catch (error) {
            content.innerHTML = `<div class="error">Failed to load dashboard: ${error.message}</div>`;
        }
    }

    async loadRecentTrades() {
        try {
            const data = await window.api.getTrades(5);
            const container = document.getElementById('recent-trades');

            if (data.trades.length === 0) {
                container.innerHTML = '<p class="empty">No trades yet</p>';
                return;
            }

            container.innerHTML = data.trades.map(trade => `
                <div class="trade-item">
                    <div class="trade-info">
                        <div class="trade-tokens">${trade.input_symbol} → ${trade.output_symbol}</div>
                        <div class="trade-time">${new Date(trade.created_at).toLocaleDateString()}</div>
                    </div>
                    <div class="trade-status ${trade.status}">
                        <span>${trade.status}</span>
                    </div>
                    <div class="trade-amount">${trade.profit_loss.toFixed(2)} SOL</div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Load trades error:', error);
        }
    }

    async loadTrading() {
        const content = document.getElementById('content');
        content.innerHTML = `
            <div class="trading-view">
                <div class="section">
                    <h2>Token Analysis</h2>
                    <div class="search-box">
                        <input type="text" id="token-input" placeholder="Enter token address or symbol..." />
                        <button onclick="app.analyzeToken()" class="btn btn-primary">Analyze</button>
                    </div>
                    <div id="analysis-result"></div>
                </div>

                <div class="section">
                    <h2>Execute Swap</h2>
                    <form id="swap-form" onsubmit="app.executeSwap(event)">
                        <div class="form-group">
                            <label>From Token</label>
                            <select id="input-token" required>
                                <option value="">Select token...</option>
                                <option value="11111111111111111111111111111111">SOL</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>To Token</label>
                            <input type="text" id="output-token" placeholder="Token address" required />
                        </div>
                        <div class="form-group">
                            <label>Amount</label>
                            <input type="number" id="swap-amount" placeholder="0.0" step="0.001" required />
                        </div>
                        <div class="form-group">
                            <label>Slippage %</label>
                            <input type="number" id="slippage" value="2.0" min="0.1" max="10" step="0.1" />
                        </div>
                        <button type="submit" class="btn btn-primary btn-block">Execute Swap</button>
                    </form>
                    <div id="swap-result"></div>
                </div>
            </div>
        `;
    }

    async analyzeToken() {
        const tokenInput = document.getElementById('token-input');
        const resultDiv = document.getElementById('analysis-result');
        const token = tokenInput.value.trim();

        if (!token) {
            resultDiv.innerHTML = '<p class="error">Please enter a token address</p>';
            return;
        }

        resultDiv.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Analyzing...</div>';

        try {
            const result = await window.api.analyzeToken(token);
            resultDiv.innerHTML = `
                <div class="analysis-card">
                    <h3>${result.name} (${result.symbol})</h3>
                    <div class="analysis-grid">
                        <div><strong>Price:</strong> $${result.price}</div>
                        <div><strong>Market Cap:</strong> $${(result.market_cap / 1e6).toFixed(2)}M</div>
                        <div><strong>Volume:</strong> $${(result.volume / 1e6).toFixed(2)}M</div>
                        <div><strong>Risk Score:</strong> ${result.risk_score}/10</div>
                    </div>
                    <p>${result.recommendation}</p>
                </div>
            `;
        } catch (error) {
            resultDiv.innerHTML = `<p class="error">Analysis failed: ${error.message}</p>`;
        }
    }

    async executeSwap(event) {
        event.preventDefault();

        const inputMint = document.getElementById('input-token').value;
        const outputMint = document.getElementById('output-token').value;
        const amount = parseFloat(document.getElementById('swap-amount').value);
        const slippage = parseFloat(document.getElementById('slippage').value);

        const resultDiv = document.getElementById('swap-result');
        resultDiv.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Processing swap...</div>';

        try {
            const result = await window.api.createSwap(inputMint, outputMint, amount, slippage);
            resultDiv.innerHTML = `
                <div class="success">
                    <h4>Swap Executed!</h4>
                    <p>Transaction: ${result.tx_hash}</p>
                    <p>Received: ${result.output_amount.toFixed(6)}</p>
                </div>
            `;
            document.getElementById('swap-form').reset();
        } catch (error) {
            resultDiv.innerHTML = `<div class="error">Swap failed: ${error.message}</div>`;
        }
    }

    async loadCopyTrading() {
        const content = document.getElementById('content');
        content.innerHTML = `
            <div class="copy-trading-view">
                <div class="section">
                    <h2>Top Whales</h2>
                    <div id="whales-list" class="loading">
                        <i class="fas fa-spinner fa-spin"></i> Loading whales...
                    </div>
                </div>

                <div class="section">
                    <h2>Your Watched Traders</h2>
                    <div id="watched-list" class="loading">
                        <i class="fas fa-spinner fa-spin"></i> Loading...
                    </div>
                </div>
            </div>
        `;

        this.loadWhales();
        this.loadWatchedWallets();
    }

    async loadWhales() {
        try {
            const data = await window.api.getWhales();
            const container = document.getElementById('whales-list');

            container.innerHTML = data.whales.map(whale => `
                <div class="whale-card">
                    <div class="whale-info">
                        <div class="whale-address">${whale.wallet.substring(0, 8)}...${whale.wallet.substring(-8)}</div>
                        <div class="whale-stats">
                            <span>Trades: ${whale.total_trades}</span>
                            <span>Win Rate: ${(whale.win_rate * 100).toFixed(1)}%</span>
                            <span>Avg Profit: ${whale.avg_profit.toFixed(2)}%</span>
                        </div>
                    </div>
                    <button class="btn btn-primary" onclick="app.watchWhale('${whale.wallet}')">
                        <i class="fas fa-star"></i> Watch
                    </button>
                </div>
            `).join('');
        } catch (error) {
            document.getElementById('whales-list').innerHTML = `<p class="error">Failed to load whales: ${error.message}</p>`;
        }
    }

    async loadWatchedWallets() {
        try {
            const data = await window.api.getWatchedWallets();
            const container = document.getElementById('watched-list');

            if (data.watched.length === 0) {
                container.innerHTML = '<p class="empty">Not watching any traders yet</p>';
                return;
            }

            container.innerHTML = data.watched.map(wallet => `
                <div class="watched-card">
                    <div class="watched-info">
                        <div class="watched-address">${wallet.wallet.substring(0, 8)}...${wallet.wallet.substring(-8)}</div>
                        <div class="watched-scale">Copy Scale: ${wallet.copy_scale}x</div>
                    </div>
                    <button class="btn btn-danger" onclick="app.unwatchWallet('${wallet.id}')">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            `).join('');
        } catch (error) {
            document.getElementById('watched-list').innerHTML = `<p class="error">Failed to load watched wallets: ${error.message}</p>`;
        }
    }

    async watchWhale(walletAddress) {
        try {
            await window.api.watchWhale(walletAddress);
            this.loadWatchedWallets();
            this.showToast('Trader added to watch list');
        } catch (error) {
            this.showToast('Failed to watch trader: ' + error.message, 'error');
        }
    }

    async unwatchWallet(walletId) {
        try {
            await window.api.unwatchWallet(walletId);
            this.loadWatchedWallets();
            this.showToast('Trader removed from watch list');
        } catch (error) {
            this.showToast('Failed to unwatch trader: ' + error.message, 'error');
        }
    }

    async loadWallets() {
        const content = document.getElementById('content');
        content.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Loading...</div>';

        try {
            const wallet = await window.api.getWallet();
            const tokens = await window.api.getWalletTokens();

            content.innerHTML = `
                <div class="wallets-view">
                    <div class="section">
                        <h2>Main Wallet</h2>
                        <div class="wallet-card">
                            <div class="wallet-address">${wallet.address}</div>
                            <div class="wallet-balance">
                                <span class="label">SOL Balance</span>
                                <span class="value">${wallet.balance.toFixed(4)} SOL</span>
                            </div>
                        </div>
                    </div>

                    <div class="section">
                        <h2>Token Holdings</h2>
                        <div id="tokens-list" class="tokens-grid">
                            ${tokens.tokens.map(token => `
                                <div class="token-item">
                                    <div class="token-name">${token.symbol}</div>
                                    <div class="token-amount">${token.amount.toFixed(2)}</div>
                                    <div class="token-value">$${token.usd_value.toFixed(2)}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                </div>
            `;
        } catch (error) {
            content.innerHTML = `<div class="error">Failed to load wallets: ${error.message}</div>`;
        }
    }

    toggleSettings() {
        const modal = document.getElementById('modal');
        modal.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-header">
                    <h2>Settings</h2>
                    <button class="btn-close" onclick="app.closeModal()">×</button>
                </div>
                <div class="modal-body">
                    <div class="settings-group">
                        <label>
                            <input type="checkbox" id="notifications" />
                            Enable Notifications
                        </label>
                        <label>
                            <input type="checkbox" id="dark-mode" />
                            Dark Mode
                        </label>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-primary" onclick="app.saveSettings()">Save</button>
                    <button class="btn btn-secondary" onclick="app.closeModal()">Close</button>
                </div>
            </div>
        `;
        modal.classList.add('open');
    }

    showProfile() {
        const user = window.auth.getUser();
        const modal = document.getElementById('modal');
        modal.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-header">
                    <h2>Profile</h2>
                    <button class="btn-close" onclick="app.closeModal()">×</button>
                </div>
                <div class="modal-body">
                    <div class="profile-info">
                        <p><strong>User ID:</strong> ${user.user_id}</p>
                        <p><strong>Username:</strong> ${user.username || 'Not set'}</p>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-danger" onclick="app.handleLogout()">Logout</button>
                    <button class="btn btn-secondary" onclick="app.closeModal()">Close</button>
                </div>
            </div>
        `;
        modal.classList.add('open');
    }

    async handleLogout() {
        if (confirm('Are you sure you want to logout?')) {
            await window.auth.logout();
            location.reload();
        }
    }

    closeModal() {
        const modal = document.getElementById('modal');
        modal.classList.remove('open');
    }

    saveSettings() {
        this.showToast('Settings saved');
        this.closeModal();
    }

    showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = message;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('show');
        }, 10);

        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
    window.app.init();
});
