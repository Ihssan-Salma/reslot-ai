# Architecture

ReSlot AI is a cancellation-recovery automation foundation using existing Calendar, Gmail and
Sheets systems. It does not provide booking management. Agents, LangGraph workflows and MCP
adapters are future work.

- API routes derive authorized session/tenant context and enforce owner/admin access and CSRF.
- Services implement OAuth lifecycle, Sheets validation/read/status, and read-only Calendar normalization.
- Repositories apply explicit tenant predicates; PostgreSQL enforces forced RLS and membership checks.
- Models persist auth, tenant/Google configuration and minimal integration state only.
- Google transport handles real HTTP; refresh credentials are encrypted and never enter JavaScript.
- React provides authentication, business selection and integration settings, without management pages.
- Celery is idle infrastructure without scheduled/application jobs.

See [the pivot inventory](architecture-pivot.md), [Sheets](google-sheets.md),
[Calendar format](calendar-reservation-format.md), [authentication](authentication.md),
and [Google security](google-security.md). Historical booking schema is retained only for
non-destructive upgrades and Alembic drift checks; it is not the active domain model.
