/**
 * Telegram Mini App specific initialization and features
 */

class MiniApp {
    constructor() {
        this.tg = window.Telegram?.WebApp;
        this.isReady = false;
    }

    async init() {
        if (!this.tg) {
            console.warn('Telegram WebApp not available');
            return;
        }

        try {
            // Ready the app
            this.tg.ready();
            this.isReady = true;

            // Expand the app
            this.expand();

            // Apply Telegram theme
            this.applyTheme();

            // Setup event handlers
            this.setupEventHandlers();

            // Setup main button
            this.setupMainButton();

            // Setup back button if history
            this.setupBackButton();

            // Initialize auth
            window.auth.isMiniApp = true;
            await window.auth.init();

            // Build UI
            if (window.auth.isLoggedIn()) {
                this.buildMiniAppUI();
            }
        } catch (error) {
            console.error('Mini app init error:', error);
        }
    }

    expand() {
        if (this.tg?.expand) {
            this.tg.expand();
        }
    }

    applyTheme() {
        const colors = this.tg?.themeParams || {};

        // Set CSS variables based on Telegram theme
        if (colors.bg_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-bg-color',
                colors.bg_color
            );
        }

        if (colors.text_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-text-color',
                colors.text_color
            );
        }

        if (colors.hint_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-hint-color',
                colors.hint_color
            );
        }

        if (colors.link_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-link-color',
                colors.link_color
            );
        }

        if (colors.button_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-button-color',
                colors.button_color
            );
        }

        if (colors.button_text_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-button-text-color',
                colors.button_text_color
            );
        }

        if (colors.secondary_bg_color) {
            document.documentElement.style.setProperty(
                '--tg-theme-secondary-bg-color',
                colors.secondary_bg_color
            );
        }
    }

    setupEventHandlers() {
        // Handle visibility change
        if (this.tg?.onEvent) {
            this.tg.onEvent('visibilityStateChanged', () => {
                if (this.tg.isVisible) {
                    console.log('App became visible');
                } else {
                    console.log('App became hidden');
                }
            });

            // Handle viewport changed
            this.tg.onEvent('viewportChanged', () => {
                console.log('Viewport changed');
            });

            // Handle theme changed
            this.tg.onEvent('themeChanged', () => {
                console.log('Theme changed');
                this.applyTheme();
            });

            // Handle main button clicked
            this.tg.onEvent('mainButtonClicked', () => {
                console.log('Main button clicked');
                this.handleMainButtonClick();
            });

            // Handle back button clicked
            this.tg.onEvent('backButtonClicked', () => {
                console.log('Back button clicked');
                this.handleBackButton();
            });
        }

        // Handle beforeunload
        window.addEventListener('beforeunload', () => {
            if (this.tg?.close) {
                // Send data back if needed
                this.tg.sendData(JSON.stringify({
                    timestamp: Date.now(),
                }));
            }
        });
    }

    setupMainButton() {
        if (!this.tg?.MainButton) return;

        // Initially hide main button
        this.tg.MainButton.hide();
    }

    setupBackButton() {
        if (!this.tg?.BackButton) return;

        // Hide back button initially
        this.tg.BackButton.hide();
    }

    showMainButton(text = 'Confirm', callback = null) {
        if (!this.tg?.MainButton) return;

        this.tg.MainButton.setText(text);
        this.tg.MainButton.show();

        if (callback) {
            this.tg.MainButton.onClick(callback);
        }
    }

    hideMainButton() {
        if (this.tg?.MainButton) {
            this.tg.MainButton.hide();
        }
    }

    showBackButton(callback = null) {
        if (!this.tg?.BackButton) return;

        this.tg.BackButton.show();

        if (callback) {
            this.tg.BackButton.onClick(callback);
        }
    }

    hideBackButton() {
        if (this.tg?.BackButton) {
            this.tg.BackButton.hide();
        }
    }

    handleMainButtonClick() {
        console.log('Main button clicked');
        // This will be overridden by the app logic
    }

    handleBackButton() {
        // Default: go back
        if (window.app) {
            window.app.navigate('dashboard');
        }
    }

    buildMiniAppUI() {
        const root = document.getElementById('root');

        root.innerHTML = `
            <div class="app-container mini-app">
                <header class="app-header">
                    <div class="header-content">
                        <h1>DEX Bot</h1>
                        <button class="btn-icon" onclick="miniApp.openSettings()" title="Settings">
                            <i class="fas fa-cog"></i>
                        </button>
                    </div>
                </header>

                <nav class="bottom-nav">
                    <button class="nav-item active" data-route="dashboard" onclick="miniApp.navigate('dashboard')">
                        <i class="fas fa-chart-line"></i>
                        <span>Dashboard</span>
                    </button>
                    <button class="nav-item" data-route="quick-trade" onclick="miniApp.navigate('quick-trade')">
                        <i class="fas fa-bolt"></i>
                        <span>Quick</span>
                    </button>
                    <button class="nav-item" data-route="copy" onclick="miniApp.navigate('copy')">
                        <i class="fas fa-copy"></i>
                        <span>Copy</span>
                    </button>
                    <button class="nav-item" data-route="wallet" onclick="miniApp.navigate('wallet')">
                        <i class="fas fa-wallet"></i>
                        <span>Wallet</span>
                    </button>
                </nav>

                <main class="app-main">
                    <div id="content" class="content"></div>
                </main>

                <div id="modal" class="modal"></div>
            </div>
        `;

        // Initialize mini app version of app
        this.setupNavigation();
        this.loadDashboard();
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
        switch (route) {
            case 'dashboard':
                this.loadDashboard();
                break;
            case 'quick-trade':
                this.loadQuickTrade();
                break;
            case 'copy':
                this.loadCopyTrading();
                break;
            case 'wallet':
                this.loadWallet();
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
                            <div class="stat-label">Win Rate</div>
                            <div class="stat-value">${(data.stats.win_rate * 100).toFixed(1)}%</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Today's P/L</div>
                            <div class="stat-value ${data.stats.total_profit >= 0 ? 'positive' : 'negative'}">
                                ${data.stats.total_profit.toFixed(2)} SOL
                            </div>
                        </div>
                    </div>

                    <div class="section">
                        <h2>Recent Trades</h2>
                        <div id="recent-trades" class="trades-list"></div>
                    </div>
                </div>
            `;

            this.loadRecentTrades();
        } catch (error) {
            content.innerHTML = `<div class="error">Failed to load dashboard: ${error.message}</div>`;
        }
    }

    async loadRecentTrades() {
        try {
            const data = await window.api.getTrades(3);
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
                    <div class="trade-amount">${trade.profit_loss.toFixed(2)} SOL</div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Load trades error:', error);
        }
    }

    async loadQuickTrade() {
        const content = document.getElementById('content');

        content.innerHTML = `
            <div class="section">
                <h2>Quick Swap</h2>
                <form id="quick-swap-form" onsubmit="miniApp.executeQuickSwap(event)">
                    <div class="form-group">
                        <label>Amount (SOL)</label>
                        <input type="number" id="quick-amount" placeholder="0.1" step="0.001" required />
                    </div>
                    <div class="form-group">
                        <label>Token Address</label>
                        <input type="text" id="quick-token" placeholder="Token mint address" required />
                    </div>
                    <button type="submit" class="btn btn-primary btn-block">Swap Now</button>
                </form>
                <div id="quick-result"></div>
            </div>
        `;

        this.showMainButton('Execute Swap', () => {
            document.getElementById('quick-swap-form').dispatchEvent(new Event('submit'));
        });
    }

    async executeQuickSwap(event) {
        event.preventDefault();

        const amount = parseFloat(document.getElementById('quick-amount').value);
        const tokenAddress = document.getElementById('quick-token').value;

        const resultDiv = document.getElementById('quick-result');
        resultDiv.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Processing...</div>';

        try {
            const result = await window.api.createSwap(
                '11111111111111111111111111111111',
                tokenAddress,
                amount,
                2.0
            );

            resultDiv.innerHTML = `
                <div class="success">
                    <p>Swap executed successfully!</p>
                    <p>Received: ${result.output_amount.toFixed(4)}</p>
                </div>
            `;

            document.getElementById('quick-swap-form').reset();
        } catch (error) {
            resultDiv.innerHTML = `<div class="error">Swap failed: ${error.message}</div>`;
        }
    }

    async loadCopyTrading() {
        const content = document.getElementById('content');

        content.innerHTML = `
            <div class="section">
                <h2>Top Traders</h2>
                <div id="whales-list" class="loading">
                    <i class="fas fa-spinner fa-spin"></i> Loading...
                </div>
            </div>
        `;

        try {
            const data = await window.api.getWhales();
            const container = document.getElementById('whales-list');

            container.innerHTML = data.whales.slice(0, 5).map(whale => `
                <div class="whale-card">
                    <div class="whale-info">
                        <div class="whale-address">${whale.wallet.substring(0, 8)}...</div>
                        <div class="whale-stats">
                            <span>${(whale.win_rate * 100).toFixed(0)}% | ${whale.avg_profit.toFixed(1)}%</span>
                        </div>
                    </div>
                    <button class="btn btn-primary" onclick="miniApp.watchWhale('${whale.wallet}')">
                        Watch
                    </button>
                </div>
            `).join('');
        } catch (error) {
            document.getElementById('whales-list').innerHTML = `<p class="error">${error.message}</p>`;
        }
    }

    async watchWhale(walletAddress) {
        try {
            await window.api.watchWhale(walletAddress);
            this.showNotification('Trader added to watch list');
            this.loadCopyTrading();
        } catch (error) {
            this.showNotification('Failed: ' + error.message);
        }
    }

    async loadWallet() {
        const content = document.getElementById('content');

        try {
            const wallet = await window.api.getWallet();

            content.innerHTML = `
                <div class="section">
                    <div class="wallet-card">
                        <div class="wallet-address">${wallet.address}</div>
                        <div class="wallet-balance">
                            <span class="label">Balance</span>
                            <span class="value">${wallet.balance.toFixed(4)} SOL</span>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <h2>Tokens</h2>
                    <div id="tokens-mini"></div>
                </div>
            `;

            // Load tokens
            const tokens = await window.api.getWalletTokens();
            const tokensDiv = document.getElementById('tokens-mini');

            tokensDiv.innerHTML = tokens.tokens.map(token => `
                <div style="display: flex; justify-content: space-between; padding: var(--spacing-md) 0; border-bottom: 1px solid var(--border-color);">
                    <div>
                        <div style="font-weight: 600;">${token.symbol}</div>
                        <div style="font-size: 0.85rem; color: var(--text-tertiary);">${token.amount.toFixed(2)}</div>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-weight: 600;">$${token.usd_value.toFixed(2)}</div>
                    </div>
                </div>
            `).join('');
        } catch (error) {
            content.innerHTML = `<div class="error">Failed to load wallet: ${error.message}</div>`;
        }
    }

    openSettings() {
        const modal = document.getElementById('modal');

        modal.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-header">
                    <h2>Settings</h2>
                    <button class="btn-close" onclick="miniApp.closeModal()">×</button>
                </div>
                <div class="modal-body">
                    <div class="settings-group">
                        <label>
                            <input type="checkbox" id="notif-toggle" checked />
                            <span>Notifications</span>
                        </label>
                        <label>
                            <input type="checkbox" id="dark-toggle" checked />
                            <span>Dark Mode</span>
                        </label>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-danger" onclick="miniApp.handleLogout()">Logout</button>
                    <button class="btn btn-secondary" onclick="miniApp.closeModal()">Back</button>
                </div>
            </div>
        `;

        modal.classList.add('open');
    }

    closeModal() {
        const modal = document.getElementById('modal');
        modal.classList.remove('open');
    }

    async handleLogout() {
        if (confirm('Logout?')) {
            await window.auth.logout();
            window.tg?.close?.();
        }
    }

    showNotification(message) {
        if (this.tg?.showPopup) {
            this.tg.showPopup({
                title: 'DEX Bot',
                message: message,
                buttons: [{ id: 1, text: 'OK', type: 'ok' }]
            });
        } else {
            alert(message);
        }
    }

    hapticFeedback(style = 'light') {
        if (this.tg?.HapticFeedback) {
            switch (style) {
                case 'light':
                    this.tg.HapticFeedback.impactOccurred('light');
                    break;
                case 'medium':
                    this.tg.HapticFeedback.impactOccurred('medium');
                    break;
                case 'heavy':
                    this.tg.HapticFeedback.impactOccurred('heavy');
                    break;
            }
        }
    }
}

// Initialize mini app
document.addEventListener('DOMContentLoaded', () => {
    window.miniApp = new MiniApp();
    window.miniApp.init();
});
