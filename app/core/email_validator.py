import re
from fastapi import HTTPException, status

# Compile once — reused on every request
_ESI_EMAIL_RE = re.compile(r"^[a-z]{1,2}\.[a-z]+(-[a-z]+)*@esi-sba\.dz$")


def validate_esi_email(email: str) -> str:
    """ Validate that 'email' matches the ESI-SBA institutional format. """
    normalised = email.strip().lower()

    if not _ESI_EMAIL_RE.match(normalised):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access is restricted to ESI-SBA institutional accounts. "
                "Your email must follow the format: "
                "firstletter.lastname@esi-sba.dz or twoletters.lastname@esi-sba.dz "
                "(e.g. i.brahmi@esi-sba.dz or am.brahmi@esi-sba.dz)."
            ),
        )

    return normalised


def extract_name_hint_from_email(email: str) -> dict:
    """
    Parse the local part of an ESI email to get name hints.
    Used as a fallback if Google doesn't return a display name.

    'i.brahmi@esi-sba.dz' → {'first_initial': 'i', 'last_name': 'brahmi'}
    'n.el-fouad@esi-sba.dz' → {'first_initial': 'n', 'last_name': 'el-fouad'}
    """
    local = email.split("@")[0]  # 'i.brahmi'
    parts = local.split(".", 1)  # ['i', 'brahmi']
    if len(parts) == 2:
        return {
            "first_initial": parts[0].upper(),  # 'I'
            "last_name": parts[1].replace("-", " ").title(),  # 'Brahmi' or 'El Fouad'
        }
    return {"first_initial": "", "last_name": local.title()}
