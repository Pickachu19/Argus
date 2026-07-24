# Changelog

## Production-grade upgrade

### Phase 1: infrastructure

- Routed the public catalog through Nginx, isolated MongoDB on an internal network, pinned container images, and added health-based startup ordering, restart policies, and resource limits.
- Moved runtime secrets to `.env`, documented placeholders in `.env.example`, separated service databases, and disabled Flask development mode.

### Phase 2: transport and gateway

- Added local TLS generation, HTTP-to-HTTPS redirects, HSTS, CSP, browser hardening headers, rate limits, JSON gateway logs, and request-ID forwarding.
- Configured secure, HTTP-only, SameSite cookies and CSRF checks.

### Phase 3: authentication and authorization

- Added short access tokens, rotating server-tracked refresh tokens, TTL-backed revocation, POST logout, current-state role checks, disabled-account enforcement, and forced password reset.
- Protected product mutations with administrator checks and CSRF-protected POST forms. Added administrator user management.

### Phase 4: interface

- Added auth-aware navigation, inline form errors, searchable pagination, dismissible alerts, empty states, an accessible delete modal, product image uploads, and keyboard focus styling.

### Phase 5: operations

- Added liveness, readiness, Prometheus metrics, structured request logs, Mongo audit records, database indexes, upload storage, and a starter Prometheus and Grafana profile.

### Phase 6: delivery

- Switched both services to non-root multi-stage images with Gunicorn. Added unit and Compose integration tests, coverage reporting, image vulnerability scans, Dependabot, setup documentation, and security limitations.
