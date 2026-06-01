"""
helpers/absence.py — Shared SQLAlchemy filter for unexcused absences
=====================================================================

Use UNEXCUSED_ABSENCE everywhere you currently use `Absence.is_absent == True`
when computing absence counts that matter for thresholds (dashboard, teacher
dashboard, groups, etc.).

An absence is "excused" when its justification was approved by an admin, which
sets `statut_justificatif = 'justified'`.  Such absences must NOT count toward
the warning / exclusion thresholds.
"""

from sqlalchemy import and_, or_

from app.models.absence import Absence

# Drop-in replacement for `Absence.is_absent == True` in threshold queries.
# Keeps absences that are present in the session but excludes those that have
# been excused by an approved justification.
UNEXCUSED_ABSENCE = and_(
    Absence.is_absent.is_(True),
    or_(
        Absence.statut_justificatif.is_(None),
        Absence.statut_justificatif != "justified",
    ),
)
