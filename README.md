# Absence-Management Backend

## Render Deployment (Internal Postgres)

Use Render internal networking for both runtime and migrations.

Environment variables:

```env
ENVIRONMENT=production
DATABASE_URL=postgresql://ams_user:4Y6I6Nscz9jJHu0hY0XTZMqqstFiZgz3@dpg-d7apian5r7bs738fdq9g-a/ams_database_y1q6
ALEMBIC_DATABASE_URL=postgresql+psycopg2://ams_user:4Y6I6Nscz9jJHu0hY0XTZMqqstFiZgz3@dpg-d7apian5r7bs738fdq9g-a/ams_database_y1q6
USE_REDIS=False
```

Do not set `DATABASE_PRODUCTION_URL` in Render to avoid mixed database sources.

Render service commands:

- Build command: `pip install -r requirements-prod.txt`
- Pre-deploy command: `alembic -c alembic.ini upgrade head`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Verification:

- Deploy should pass pre-deploy without `Network is unreachable`.
- Open `/docs` and test one DB endpoint (for example, students list).
