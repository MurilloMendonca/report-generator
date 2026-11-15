// Swagger UI for the signed-in user. "Try it out" uses the browser session, so state-changing
// requests carry the CSRF header; a token entered in "Authorize" is sent as a bearer token.
window.addEventListener('DOMContentLoaded', () => {
    const csrf = () => document.cookie.split('; ').find(item => item.startsWith('csrf_token='))?.split('=')[1] || '';
    window.SwaggerUIBundle({
        url: '/api/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        docExpansion: 'none',
        tryItOutEnabled: false,
        requestInterceptor: request => {
            if (!['GET', 'HEAD'].includes((request.method || 'GET').toUpperCase())) request.headers['X-CSRF-Token'] = decodeURIComponent(csrf());
            return request;
        },
    });
});
