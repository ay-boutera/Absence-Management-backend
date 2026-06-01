# AMS — Absence Management System

## Stack
- FastAPI, SQLAlchemy, PostgreSQL, Alembic, Pydantic v2
- Hosted on Render

## Rules
- Always read existing files before writing new ones
- Follow existing router/schema/model patterns
- Never modify alembic_version table
- Use Pydantic v2 syntax (model_config, not class Config)
- 5CS: S2 is internship — block planning imports for that semester


After that, the full notification flow is live:
  - Student submits justification → all admins get justification_submitted notification via WebSocket + inbox
  - Admin approves → student gets justification_approved + absences marked as justified (no longer count)
  - Admin rejects → student gets justification_rejected
  - Teacher records 3rd unexcused absence in a module → student gets absence_warning
  - Teacher records 5th unexcused absence in a module → student gets absence_exclusion
  - Flutter app connects via ws://<host>/api/v1/ws/notifications?token=<jwt> to receive all of the above in real time


    How Notifications Work                                                                                                                                         

    The two layers                                                                                                                                                                                                                                      
    Every notification does two things at once:

  1. Persists a row in the notifications table (inbox, permanent)
  2. Pushes a JSON message over WebSocket to any live connection for that user (real-time)

    This is done by the single function create_and_push() in notification_service.py.


    The WebSocket connection

    Both the Flutter app (students) and the web app (admins) connect the same way:

  ws://<host>/api/v1/ws/notifications?token=<access_jwt>

    - The server validates the JWT from the query string
    - Checks the user exists and is active in the DB
    - Registers the connection in a in-memory dict: { user_id → [WebSocket, ...] }
    - Keeps the socket open — client sends any message (e.g. "ping") to stay alive
    - On disconnect, the socket is removed from the dict

    One user can have multiple open connections (e.g. two browser tabs). All of them get the push.


    Who triggers notifications and when

    ┌───────────────────────────────────────────────────┬────────────────────────────────────┬───────────────────────┬─────────────────────────┐
    │                      Trigger                      │            Who fires it            │    Who receives it    │          Type           │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Student submits a justification                   │ POST /justifications               │ Every active admin    │ justification_submitted │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Admin approves a justification                    │ PATCH /justifications/{id}/approve │ That student          │ justification_approved  │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Admin rejects a justification                     │ PATCH /justifications/{id}/reject  │ That student          │ justification_rejected  │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Bulk approve                                      │ PATCH /justifications/approve-all  │ Each affected student │ justification_approved  │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Bulk reject                                       │ PATCH /justifications/reject-all   │ Each affected student │ justification_rejected  │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Teacher records 3rd unexcused absence in a module │ POST /absences                     │ That student          │ absence_warning         │
    ├───────────────────────────────────────────────────┼────────────────────────────────────┼───────────────────────┼─────────────────────────┤
    │ Teacher records 5th unexcused absence in a module │ POST /absences                     │ That student          │ absence_exclusion       │
    └───────────────────────────────────────────────────┴────────────────────────────────────┴───────────────────────┴─────────────────────────┘

  ---
  The two helper functions

  create_and_push(db, recipient_id, recipient_role, type, title, body, ...)
  - Saves one Notification row
  - Immediately pushes to that user's WebSocket if connected
  - Used for targeted notifications (one specific student or admin)

  notify_admins(db, type, title, body, ...)
  - Queries SELECT id FROM admins WHERE is_active = true
  - Calls create_and_push for each admin
  - Used when a student submits a justification (all admins need to know)

  ---
  REST inbox (for when the user was offline)

  If the user wasn't connected when the notification was sent, the row is still in the DB. The client can fetch it later:

    ┌─────────────────────────────────┬─────────────────────────────────────────────────────────────┐
    │            Endpoint             │                           Purpose                           │
    ├─────────────────────────────────┼─────────────────────────────────────────────────────────────┤
    │ GET /notifications              │ Paginated inbox (newest first), optional unread_only filter │
    ├─────────────────────────────────┼─────────────────────────────────────────────────────────────┤
    │ GET /notifications/unread-count │ Returns {"count": N} — for badge on app icon                │
    ├─────────────────────────────────┼─────────────────────────────────────────────────────────────┤
    │ PATCH /notifications/{id}/read  │ Mark one as read                                            │
    ├─────────────────────────────────┼─────────────────────────────────────────────────────────────┤
    │ POST /notifications/read-all    │ Mark all as read                                            │
    └─────────────────────────────────┴─────────────────────────────────────────────────────────────┘

  ---
  Important limitation

  The ConnectionManager stores connections in memory on the running process. This means:

  - Works perfectly on a single-process deployment (Render with one worker)
  - If you ever scale to multiple workers or multiple instances, a user connected to worker A will not receive a push triggered on worker B — you would need a message broker (Redis Pub/Sub,
  etc.) to bridge them

✻ Worked for 33s   
