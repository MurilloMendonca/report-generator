// Sign-in, sign-up, password recovery and the links sent by email.
// One page serves every view; the path picks which form is shown.
const Auth = {
    config: { signup_open: false, turnstile_site_key: '', password_min_length: 12 },
    turnstileWidgets: new Map(),
    turnstileTokens: new Map(),
    linkToken: '',

    views: {
        '/login': 'login',
        '/signup': 'signup',
        '/forgot-password': 'forgot-password',
        '/reset-password': 'reset-password',
        '/verify-email': 'verify-email',
        '/confirm-email': 'confirm-email',
    },

    async init() {
        // Links from emails carry the token in the fragment, which never reaches server logs.
        const fragment = new URLSearchParams(window.location.hash.slice(1));
        this.linkToken = fragment.get('token') || '';
        if (this.linkToken) window.history.replaceState(null, '', window.location.pathname + window.location.search);

        try {
            const res = await fetch('/api/auth/config');
            if (res.ok) this.config = await res.json();
        } catch (error) {
            // Keep defaults; the forms still work without Turnstile.
        }
        document.querySelectorAll('.password-min').forEach(el => { el.textContent = String(this.config.password_min_length); });
        document.querySelectorAll('.signup-only').forEach(el => { el.hidden = !this.config.signup_open; });

        document.getElementById('login-form').addEventListener('submit', event => this.submitLogin(event));
        document.getElementById('signup-form').addEventListener('submit', event => this.submitSignup(event));
        document.getElementById('forgot-form').addEventListener('submit', event => this.submitForgot(event));
        document.getElementById('reset-form').addEventListener('submit', event => this.submitReset(event));

        const view = this.views[window.location.pathname] || 'login';
        if (view === 'signup' && !this.config.signup_open) {
            this.show('login');
            this.message('Novos cadastros estão fechados no momento.');
            return;
        }
        this.show(view);
        if (view === 'verify-email') await this.confirmLink('/api/auth/verify-email', 'Email confirmado! Você já pode criar relatórios.');
        if (view === 'confirm-email') await this.confirmLink('/api/auth/confirm-email', 'Novo email confirmado.');
        if (view === 'reset-password' && !this.linkToken) this.error('Link inválido. Peça um novo em "Esqueci minha senha".');
    },

    show(view) {
        document.querySelectorAll('[data-view]').forEach(el => { el.hidden = el.dataset.view !== view; });
        const form = document.querySelector(`[data-view="${view}"]`);
        const slot = form?.querySelector('.turnstile-slot');
        if (slot) this.renderTurnstile(view, slot);
        form?.querySelector('input')?.focus();
    },

    message(text) {
        const el = document.getElementById('auth-message');
        el.textContent = text;
        el.hidden = !text;
    },

    error(text) {
        document.getElementById('auth-error').textContent = text || '';
    },

    // --- Turnstile ---------------------------------------------------------------

    loadTurnstileScript() {
        if (this.turnstileReady) return this.turnstileReady;
        this.turnstileReady = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
            script.async = true;
            script.onload = () => resolve(window.turnstile);
            script.onerror = () => reject(new Error('Turnstile indisponível'));
            document.head.appendChild(script);
        });
        return this.turnstileReady;
    },

    async renderTurnstile(view, slot) {
        if (!this.config.turnstile_site_key || this.turnstileWidgets.has(view)) return;
        try {
            const turnstile = await this.loadTurnstileScript();
            const widgetId = turnstile.render(slot, {
                sitekey: this.config.turnstile_site_key,
                callback: token => this.turnstileTokens.set(view, token),
                'expired-callback': () => this.turnstileTokens.delete(view),
            });
            this.turnstileWidgets.set(view, widgetId);
        } catch (error) {
            this.error('Não foi possível carregar a verificação anti-robô. Recarregue a página.');
        }
    },

    turnstileToken(view) {
        return this.turnstileTokens.get(view) || null;
    },

    resetTurnstile(view) {
        const widgetId = this.turnstileWidgets.get(view);
        this.turnstileTokens.delete(view);
        if (widgetId !== undefined && window.turnstile) window.turnstile.reset(widgetId);
    },

    // --- Requests ------------------------------------------------------------------

    async post(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: API.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        let payload = {};
        try { payload = await res.json(); } catch (error) { /* empty body */ }
        if (!res.ok) {
            const detail = payload.detail;
            const message = typeof detail === 'string' ? detail : detail?.message;
            throw new Error(message || (res.status === 429 ? 'Muitas tentativas. Aguarde alguns minutos.' : 'Não foi possível concluir. Tente novamente.'));
        }
        return payload;
    },

    async submitting(form, view, action) {
        const button = form.querySelector('button[type="submit"]');
        this.error('');
        this.message('');
        button.disabled = true;
        try {
            await action();
        } catch (error) {
            this.error(error.message);
            this.resetTurnstile(view);
        } finally {
            button.disabled = false;
        }
    },

    nextPath() {
        const next = new URLSearchParams(window.location.search).get('next') || '/';
        // Only same-site paths: "/x" but not "//evil.example" or "/\evil".
        return /^\/(?![\/\\])/.test(next) ? next : '/';
    },

    submitLogin(event) {
        event.preventDefault();
        return this.submitting(event.target, 'login', async () => {
            await this.post('/api/auth/login', {
                email: document.getElementById('login-email').value,
                password: document.getElementById('login-password').value,
                turnstile_token: this.turnstileToken('login'),
            });
            window.location.assign(this.nextPath());
        });
    },

    submitSignup(event) {
        event.preventDefault();
        return this.submitting(event.target, 'signup', async () => {
            const result = await this.post('/api/auth/signup', {
                display_name: document.getElementById('signup-name').value,
                email: document.getElementById('signup-email').value,
                password: document.getElementById('signup-password').value,
                turnstile_token: this.turnstileToken('signup'),
            });
            event.target.reset();
            this.show('login');
            this.message(result.message);
        });
    },

    submitForgot(event) {
        event.preventDefault();
        return this.submitting(event.target, 'forgot-password', async () => {
            const result = await this.post('/api/auth/forgot-password', {
                email: document.getElementById('forgot-email').value,
                turnstile_token: this.turnstileToken('forgot-password'),
            });
            event.target.reset();
            this.resetTurnstile('forgot-password');
            this.message(result.message);
        });
    },

    submitReset(event) {
        event.preventDefault();
        const password = document.getElementById('reset-password').value;
        if (password !== document.getElementById('reset-confirm').value) {
            this.error('As senhas não conferem.');
            return;
        }
        return this.submitting(event.target, 'reset-password', async () => {
            const result = await this.post('/api/auth/reset-password', { token: this.linkToken, password });
            this.linkToken = '';
            this.show('login');
            this.message(result.message);
        });
    },

    async confirmLink(url, successText) {
        if (!this.linkToken) {
            this.error('Link inválido ou incompleto.');
            return;
        }
        try {
            await this.post(url, { token: this.linkToken });
            this.linkToken = '';
            document.querySelectorAll('[data-view]').forEach(el => { el.hidden = true; });
            this.message(successText);
            const link = document.createElement('a');
            link.href = '/';
            link.className = 'btn auth-submit';
            link.textContent = 'Ir para o estúdio';
            document.getElementById('auth-message').appendChild(document.createElement('br'));
            document.getElementById('auth-message').appendChild(link);
        } catch (error) {
            this.error(error.message);
        }
    },
};

window.addEventListener('DOMContentLoaded', () => Auth.init());
