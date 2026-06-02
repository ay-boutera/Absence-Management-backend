"""
helpers/absence.py — Shared SQLAlchemy filter for absence threshold queries
============================================================================

Use UNEXCUSED_ABSENCE everywhere you use `Absence.is_absent == True`
when computing absence counts for thresholds (dashboard, teacher dashboard,
threshold notifications, groups, etc.).

Policy: every absence counts toward the warning / exclusion thresholds
regardless of whether a justification was submitted, accepted, or rejected.
Justifications are purely informational.
"""

from app.models.absence import Absence

# Every recorded absence counts — justification status has no effect on
# the warning / exclusion threshold count.
UNEXCUSED_ABSENCE = Absence.is_absent.is_(True)
