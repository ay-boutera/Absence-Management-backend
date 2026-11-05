<h1 align="center">AMS: Absence Management System (backend)</h1>

<p align="center">
  The API behind ESI-SBA's attendance platform: QR-code attendance with rotating codes,
  absence rules with warning and exclusion thresholds, justifications, make-up sessions,
  live notifications, and CSV/PDF reports.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-asyncpg-4169E1?logo=postgresql&logoColor=white">
  <img alt="SQLAlchemy 2.0" src="https://img.shields.io/badge/SQLAlchemy-2.0_async-D71F00?logo=sqlalchemy&logoColor=white">
  <img alt="Tests: pytest" src="https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white">
</p>

## Overview

AMS replaces paper attendance sheets at the Higher School of Computer Science of Sidi Bel Abbès (ESI-SBA). A teacher opens a session and shows a QR code that changes every 30 seconds; students scan it with the mobile app to be marked present. The backend records every absence, applies the school's rules, and gives each role what it needs:

| Role        | What they do                                                                                                                                       |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Admin**   | Import students, teachers and the timetable; manage accounts; review justifications, corrections and make-up requests; dashboards, exports, settings |
| **Teacher** | See today's sessions, show the QR code, mark and correct absences, follow their groups and the students close to a threshold                      |
| **Student** | Scan the QR code, follow absences per module, submit justifications, request make-up sessions, receive notifications                               |

Year-end project (PFA), 2025–2026, at ESI-SBA. The system has three parts:

| Repository                                                                                  | Role                                       |
| ------------------------------------------------------------------------------------------- | ------------------------------------------ |
| **Absence-Management-backend** (this one)                                                   | REST and WebSocket API (FastAPI)           |
| [Absence-Management-frontend](https://github.com/ay-boutera/Absence-Management-frontend)    | Web app for admins and teachers (Next.js)  |
| [Absence-Management-mobile](https://github.com/ay-boutera/Absence-Management-mobile)        | Mobile app for students (Flutter)          |
| [Absence-Management](https://github.com/ay-boutera/Absence-Management)                      | All three as Git submodules                |

## Features

- **QR attendance that cannot be shared.** For each session, the API gives the teacher's screen a code valid for 30 seconds, meant to be refreshed about every 25 seconds and shown as a QR code. The student app sends the scanned code to mark the student present, so a screenshot sent to a friend expires before it is useful.
- **Absence rules.** Per-module warning and exclusion thresholds (3 and 5 absences by default), a justification deadline, the academic year and the current semester, all editable by admins. Students are notified when they reach a threshold.
- **Corrections.** A teacher can correct an absence freely up to 15 minutes after the session ends; after that, the correction becomes a request that an admin approves or rejects.
- **Justifications.** Students upload a document (stored on Cloudinary); admins approve or reject it, one by one or in bulk. Approved absences stop counting toward the thresholds.
- **Make-up sessions.** Students request to attend another session of a module to compensate; admins approve or reject.
- **Live notifications.** Every notification is stored (inbox, unread count, mark as read) and pushed at the same time over a WebSocket (`/api/v1/ws/notifications?token=<jwt>`), which the mobile app listens to.
- **Imports and exports.** CSV imports of students, teachers and the timetable, validated row by row with an import history; CSV and PDF exports of absences, students, teachers and the timetable.
- **Dashboards.** For admins: totals, absences by level and monthly trends. For teachers: absence rate per module and threshold alerts.
- **Authentication and security.** Email and password, or Google sign-in limited to `@esi-sba.dz` accounts. Short-lived JWT access tokens (15 min) and refresh tokens (7 days) in HttpOnly cookies for the web app, or as a Bearer header for the mobile app; CSRF token, password reset by email, logout after 30 minutes of inactivity, security headers, role-based permissions.
- **Health checks.** `/health`, `/health/db` and `/health/db/tables` report whether the database is reachable, migrated to the latest Alembic revision, and has every critical table.

## Tech stack

| Area           | Tools                                                                   |
| -------------- | ----------------------------------------------------------------------- |
| API            | Python 3.12, FastAPI, Pydantic v2, pydantic-settings, Uvicorn            |
| Database       | PostgreSQL, SQLAlchemy 2.0 (async, asyncpg), Alembic migrations          |
| Authentication | python-jose (JWT), passlib + bcrypt, Authlib (Google OAuth 2.0)          |
| Services       | fastapi-mail (SMTP), Cloudinary (documents), ReportLab (PDF)             |
| Tests          | pytest, pytest-asyncio, HTTPX                                            |
| Hosting        | Render                                                                   |

## Project structure

```
app/
├── main.py         # the FastAPI app: middleware, routers, health checks
├── config/         # settings (read from .env), enums, constants
├── db/             # async engine and sessions
├── models/         # SQLAlchemy models: users, sessions, absences, justifications...
├── schemas/        # Pydantic request and response models
├── routers/        # one router per area: auth, accounts, imports, sessions, attendance...
├── services/       # auth, Google OAuth, email, notifications, Cloudinary, sessions
├── helpers/        # permissions, JWT / cookies / CSRF, absence rules
└── utils/          # CSV and PDF export helpers
alembic/            # database migrations
tests/              # pytest suite
```

## Getting started

**Requirements:** Python 3.12 and a PostgreSQL database.

```bash
git clone https://github.com/ay-boutera/Absence-Management-backend.git
cd Absence-Management-backend

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# create .env (see the table below), then:
alembic upgrade head
uvicorn app.main:app --reload
```

The interactive API documentation is at http://localhost:8000/api/v1/docs.

### Configuration

Settings are read from a `.env` file at the project root.

| Variable                                                                                        | Purpose                                                       | Default             |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ------------------- |
| `DATABASE_URL`                                                                                  | `postgresql+asyncpg://user:password@localhost:5432/ams`       | required            |
| `ALEMBIC_DATABASE_URL`                                                                          | Optional separate URL for migrations                          | `DATABASE_URL`      |
| `SECRET_KEY`, `CSRF_SECRET_KEY`                                                                 | Sign the JWTs and the CSRF tokens                             | required            |
| `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`                         | Token settings                                                | `HS256`, `15`, `7`  |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`                               | Google sign-in                                                | required            |
| `ALLOWED_EMAIL_DOMAIN`                                                                          | Only these Google accounts can sign in                        | `esi-sba.dz`        |
| `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_SERVER`, `MAIL_PORT`, `MAIL_STARTTLS`, `MAIL_SSL_TLS` | SMTP for password-reset emails                      | port `587`, STARTTLS |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`                          | Storage of justification documents                            | required            |
| `FRONTEND_URL`, `CORS_ALLOW_ALL`                                                                | Web app address (CORS and redirects)                          | `false` for CORS    |
| `RESET_TOKEN_EXPIRE_MINUTES`, `SESSION_INACTIVITY_MINUTES`                                      | Password-reset link lifetime, inactivity logout               | `30`, `30`          |
| `ENVIRONMENT`                                                                                   | `development` or `production`                                 | `production`        |

## API

Every route is under `/api/v1`. The full, interactive reference is generated by FastAPI at `/api/v1/docs`.

| Area                  | Routes                                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| Authentication        | `POST /auth/login`, `/auth/logout`, `/auth/refresh`, `/auth/reset-password`, `/auth/change-password`; `GET /auth/me`, `/auth/google` |
| Accounts              | `/accounts/admins`, `/accounts/teachers`, `/accounts/students`: create, list, update, activate or deactivate |
| Imports and exports   | `POST /import/students`, `/import/teachers`, `/import/planning`; `GET /export/{absences,students,teachers,planning}/{csv,pdf}` |
| Timetable and sessions | `GET /planning/my-schedule`, `/sessions/today`, `/sessions/my-sessions`; attendance and summary per session |
| Attendance            | `GET /sessions/{id}/qr-code` (teacher), `POST /attendance/mark-present` (student)                         |
| Absences              | `POST /absences`, `GET /absences`, correction requests                                                    |
| Justifications        | Submit, follow and edit (student); approve or reject, one or all (admin); download the document           |
| Make-up sessions      | `/compensation-requests`: available sessions, request, approve or reject                                  |
| Notifications         | `GET /notifications`, unread count, mark as read; WebSocket `/ws/notifications`                           |
| Dashboards            | `/dashboard/stats`, `/dashboard/absences-by-level`, `/dashboard/monthly-trends`, `/teacher/dashboard/*`   |
| Settings              | `GET` and `PUT /settings/administration`                                                                  |

## Tests

```bash
pytest
```

The suite covers authentication, accounts, imports and exports, the teachers import, the student profile, and the generated API documentation.

## Team

- [Boutera Ahmed Yassine](https://github.com/ay-boutera): backend, main developer
- [trarifouad](https://github.com/trarifouad): backend contributions and the web app
- [i-brahmi](https://github.com/i-brahmi): the mobile app
