from enum import Enum as PyEnum


# ── Role Enum ──────────────────────────────────────────────────────────────────
class UserRole(str, PyEnum):
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"


class SessionType(str, PyEnum):
    COURS = "Cours"
    TD = "TD"
    TP = "TP"
    EXAMEN = "Examen"
    TD_TP = "TD/TP"
    COURS_TP = "Cours/TP"
    COURS_TD_TP = "Cours/TD/TP"
    ENCADREMENTS = "Encadrements"
    ENCADREMENTS_PP = "Encadrements projets pluridisciplinaires"
    COURS_TD_COLLECTIF = "Cours/TD Collectif"
    


class ImportType(str, PyEnum):
    STUDENTS = "students"
    PLANNING = "planning"
    TEACHERS = "teachers"


class ImportExportAction(str, PyEnum):
    IMPORT = "import"
    EXPORT = "export"


class ImportExportFileType(str, PyEnum):
    CSV = "csv"
    PDF = "pdf"
    EXCEL = "excel"


class ImportExportDataType(str, PyEnum):
    STUDENTS = "students"
    ATTENDANCE = "attendance"
    SCHEDULE = "schedule"
    JUSTIFICATIONS = "justifications"


class AcademicYear(str, PyEnum):
    CP_1 = "1CP"
    CP_2 = "2CP"
    CS_1 = "1CS"
    CS_2 = "2CS"
    CS_3 = "3CS"


class SectionEnum(str, PyEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"
    I = "I"
    J = "J"
    K = "K"
    L = "L"
    M = "M"
    N = "N"
    O = "O"
    P = "P"
    Q = "Q"
    R = "R"
    S = "S"
    T = "T"
    U = "U"
    V = "V"
    W = "W"
    X = "X"
    Y = "Y"
    Z = "Z"


class SpecialityEnum(str, PyEnum):
    ISI = "ISI"
    SIW = "SIW"
    IASD = "IASD"
    CYS = "CyS"


class SessionStatusEnum(str, PyEnum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AbsenceSourceEnum(str, PyEnum):
    PWA = "PWA"
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"


class CorrectionStatusEnum(str, PyEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class JustificationStatusEnum(str, PyEnum):
    NON_JUSTIFIEE = "non_justifiee"
    EN_ATTENTE = "en_attente"
    JUSTIFIEE = "justifiee"
    REJETEE = "rejetee"
