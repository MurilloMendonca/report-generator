// Account settings: profile, password, email and signed-in devices.
const Account = {
    user: null,

    async init() {
        try {
            this.user = await API.me();
        } catch (error) {
            API.redirectToLogin();
            return;
        }
        document.getElementById('app-shell').style.display = 'block';
        this.renderProfile();
        document.getElementById('admin-link').hidden = this.user.role !== 'admin';
        document.getElementById('profile-form').addEventListener('submit', event => this.saveProfile(event));
        document.getElementById('password-form').addEventListener('submit', event => this.changePassword(event));
        document.getElementById('email-form').addEventListener('submit', event => this.changeEmail(event));
        document.getElementById('resend-btn').addEventListener('click', () => this.run(async () => (await API.resendVerification()).message));
        document.getElementById('logout-all-btn').addEventListener('click', () => this.logoutAll());
        document.getElementById('logout-btn').addEventListener('click', async () => {
            try { await API.logout(); } catch (error) { /* already signed out */ }
            window.location.assign('/login');
        });
        document.getElementById('delete-account-form').addEventListener('submit', event => this.deleteAccount(event));
        await Promise.all([this.loadSessions(), this.loadUsage()]);
    },

    formatBytes(bytes) {
        if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
        if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
        return `${Math.round(bytes / 1024)} KB`;
    },

    async loadUsage() {
        try {
            const usage = await this.request('GET', '/api/usage');
            const share = usage.storage_limit_bytes ? Math.min(100, (usage.storage_bytes / usage.storage_limit_bytes) * 100) : 0;
            document.getElementById('usage-storage').textContent =
                `Armazenamento: ${this.formatBytes(usage.storage_bytes)} de ${this.formatBytes(usage.storage_limit_bytes)}`;
            document.getElementById('usage-storage-bar').style.width = `${share.toFixed(1)}%`;
            document.getElementById('usage-reports').textContent =
                `Relatórios criados nas últimas 24 horas: ${usage.reports_last_24h} de ${usage.reports_per_day_limit}.`;
        } catch (error) {
            document.getElementById('usage-storage').textContent = error.message;
        }
    },

    async deleteAccount(event) {
        event.preventDefault();
        const ok = await this.run(async () => {
            await this.request('DELETE', '/api/auth/account', { password: document.getElementById('delete-password').value });
        });
        if (ok) window.location.assign('/login');
    },

    notice(text, isError = false) {
        const el = document.getElementById('account-notice');
        el.textContent = text || '';
        el.hidden = !text;
        el.classList.toggle('is-error', isError);
    },

    async run(action) {
        this.notice('');
        try {
            const message = await action();
            if (message) this.notice(message);
            return true;
        } catch (error) {
            this.notice(error.message, true);
            return false;
        }
    },

    async request(method, url, body) {
        const res = await fetch(url, {
            method,
            headers: API.authHeaders(body ? { 'Content-Type': 'application/json' } : {}),
            body: body ? JSON.stringify(body) : undefined,
        });
        return await API.parseResponse(res, 'Não foi possível concluir. Tente novamente.');
    },

    renderProfile() {
        document.getElementById('profile-email').textContent = this.user.email;
        document.getElementById('profile-name').value = this.user.display_name || '';
        const verified = document.getElementById('profile-verified');
        verified.textContent = this.user.email_verified ? 'confirmado' : 'não confirmado';
        verified.classList.toggle('badge-warning', !this.user.email_verified);
        document.getElementById('profile-unverified').hidden = this.user.email_verified;
    },

    saveProfile(event) {
        event.preventDefault();
        return this.run(async () => {
            const result = await this.request('PATCH', '/api/auth/me', { display_name: document.getElementById('profile-name').value });
            this.user = result.user;
            this.renderProfile();
            return 'Perfil salvo.';
        });
    },

    changePassword(event) {
        event.preventDefault();
        const next = document.getElementById('new-password').value;
        if (next !== document.getElementById('confirm-password').value) {
            this.notice('As senhas não conferem.', true);
            return;
        }
        return this.run(async () => {
            const result = await this.request('POST', '/api/auth/change-password', {
                current_password: document.getElementById('current-password').value,
                new_password: next,
            });
            event.target.reset();
            await this.loadSessions();
            return result.message;
        });
    },

    changeEmail(event) {
        event.preventDefault();
        return this.run(async () => {
            const result = await this.request('POST', '/api/auth/change-email', {
                new_email: document.getElementById('new-email').value,
                password: document.getElementById('email-password').value,
            });
            event.target.reset();
            return result.message;
        });
    },

    describeDevice(userAgent) {
        const ua = userAgent || '';
        const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Navegador';
        const system = /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : '';
        return system ? `${browser} · ${system}` : browser;
    },

    async loadSessions() {
        const list = document.getElementById('session-list');
        let sessions = [];
        try {
            sessions = (await this.request('GET', '/api/auth/sessions')).sessions;
        } catch (error) {
            this.notice(error.message, true);
            return;
        }
        list.replaceChildren(...sessions.map(session => {
            const item = document.createElement('li');
            const label = document.createElement('span');
            const seen = new Date(session.last_seen_at).toLocaleString('pt-BR');
            label.textContent = `${this.describeDevice(session.user_agent)} — ${session.ip || 'IP desconhecido'} — último uso ${seen}`;
            item.appendChild(label);
            if (session.current) {
                const badge = document.createElement('span');
                badge.className = 'badge';
                badge.textContent = 'este dispositivo';
                item.appendChild(badge);
            } else {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-outline btn-small';
                button.textContent = 'Encerrar';
                button.addEventListener('click', () => this.run(async () => {
                    await this.request('DELETE', `/api/auth/sessions/${encodeURIComponent(session.id)}`);
                    await this.loadSessions();
                    return 'Sessão encerrada.';
                }));
                item.appendChild(button);
            }
            return item;
        }));
    },

    async logoutAll() {
        const ok = await this.run(async () => {
            await this.request('POST', '/api/auth/logout-all');
        });
        if (ok) window.location.assign('/login');
    },
};

window.addEventListener('DOMContentLoaded', () => Account.init());
