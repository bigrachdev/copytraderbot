/**
 * Authentication module for both web and Telegram Mini App
 */

class Auth {
    constructor() {
        this.user = null;
        this.isAuthenticated = false;
        this.isMiniApp = !!window.Telegram?.WebApp;
        // Development mode - set ?dev=true in URL to bypass auth
        const params = new URLSearchParams(window.location.search);
        this.devMode = params.get('dev') === 'true' || localStorage.getItem('devMode') === 'true';
    }

    async init() {
        // Development mode - skip real authentication
        if (this.devMode) {
            console.warn('⚙️ Development mode: Skipping authentication');
            this.isAuthenticated = true;
            this.user = { 
                id: 'DEV_USER',
                username: 'Developer',
                authenticated: true
            };
            localStorage.setItem('devMode', 'true');
            return true;
        }

        // If Telegram Mini App, authenticate with Telegram
        if (this.isMiniApp) {
            await this.initTelegramAuth();
        }

        // Check existing session
        try {
            const status = await window.api.getAuthStatus();
            this.isAuthenticated = status.authenticated;
            this.user = status;
        } catch (error) {
            console.error('Auth status check failed:', error);
            this.isAuthenticated = false;
        }

        return this.isAuthenticated;
    }

    async initTelegramAuth() {
        const tg = window.Telegram.WebApp;

        // Ensure the WebApp is ready
        if (!tg.initData) {
            console.warn('No Telegram init data available');
            return false;
        }

        try {
            // Parse Telegram data
            const initData = {};
            const params = new URLSearchParams(tg.initData);

            for (const [key, value] of params) {
                try {
                    initData[key] = JSON.parse(value);
                } catch {
                    initData[key] = value;
                }
            }

            // Authenticate with backend
            const result = await window.api.authTelegram(initData);

            if (result.success) {
                this.user = {
                    user_id: result.user_id,
                    telegram_id: result.user_id,
                    is_new: result.is_new,
                };
                this.isAuthenticated = true;

                // Expand the app if mobile
                tg.expand?.();

                return true;
            }
        } catch (error) {
            console.error('Telegram authentication failed:', error);
        }

        return false;
    }

    async logout() {
        try {
            await window.api.logout();
            this.user = null;
            this.isAuthenticated = false;
            return true;
        } catch (error) {
            console.error('Logout failed:', error);
            return false;
        }
    }

    isLoggedIn() {
        return this.isAuthenticated && this.user;
    }

    getUser() {
        return this.user;
    }

    isTelegramMiniApp() {
        return this.isMiniApp;
    }
}

// Export globally
window.auth = new Auth();
