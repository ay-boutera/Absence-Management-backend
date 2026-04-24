"""
services/pdf_service.py — ESI-SBA branded PDF generation (Sprint 6 US-49).

Generates professional PDF reports from dashboard stats data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fpdf import FPDF

# ESI-SBA brand colors (RGB)
_DARK_BLUE = (13, 40, 80)
_MID_BLUE = (26, 83, 160)
_LIGHT_BLUE = (219, 234, 254)
_WHITE = (255, 255, 255)
_GREY = (245, 245, 245)
_DARK_GREY = (80, 80, 80)


class _ESISBAPDF(FPDF):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self._report_title = title
        self._report_subtitle = subtitle
        self.alias_nb_pages()
        self.set_auto_page_break(auto=True, margin=18)

    def header(self):
        # Institution bar
        self.set_fill_color(*_DARK_BLUE)
        self.rect(0, 0, 210, 14, "F")
        self.set_y(2)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*_WHITE)
        self.cell(0, 10, "ESI-SBA  —  École Supérieure en Informatique", align="C")

        # Title bar
        self.set_fill_color(*_MID_BLUE)
        self.rect(0, 14, 210, 10, "F")
        self.set_y(15)
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 8, self._report_title, align="C")

        if self._report_subtitle:
            self.ln(8)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*_WHITE)
            self.set_y(22)
            self.cell(0, 6, self._report_subtitle, align="C")
            self.ln(6)

        self.set_text_color(0, 0, 0)
        self.set_y(30)

    def footer(self):
        self.set_y(-12)
        self.set_fill_color(*_DARK_BLUE)
        self.rect(0, self.get_y(), 210, 12, "F")
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*_WHITE)
        generated = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
        self.cell(130, 10, f"Généré le {generated}  —  Système de Gestion des Absences", align="L")
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="R")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def section_title(self, text: str):
        self.ln(4)
        self.set_fill_color(*_MID_BLUE)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*_WHITE)
        self.cell(0, 8, f"  {text}", fill=True, ln=True)
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def kpi_row(self, items: list[tuple[str, str]]):
        """Render a row of KPI boxes: [(label, value), ...]"""
        w = 190 / len(items)
        self.set_font("Helvetica", "B", 14)
        for label, value in items:
            x = self.get_x()
            y = self.get_y()
            self.set_fill_color(*_LIGHT_BLUE)
            self.rect(x, y, w - 2, 20, "F")
            self.set_font("Helvetica", "B", 13)
            self.set_text_color(*_DARK_BLUE)
            self.set_xy(x, y + 2)
            self.cell(w - 2, 8, value, align="C")
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*_DARK_GREY)
            self.set_xy(x, y + 11)
            self.cell(w - 2, 6, label, align="C")
            self.set_xy(x + w, y)
        self.set_text_color(0, 0, 0)
        self.ln(22)

    def table(self, headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None):
        """Render a data table."""
        n = len(headers)
        if col_widths is None:
            col_widths = [190 / n] * n

        # Header row
        self.set_fill_color(*_DARK_BLUE)
        self.set_text_color(*_WHITE)
        self.set_font("Helvetica", "B", 8)
        for h, w in zip(headers, col_widths):
            self.cell(w, 7, h, border=0, fill=True, align="C")
        self.ln()

        # Data rows
        self.set_text_color(0, 0, 0)
        self.set_font("Helvetica", "", 8)
        for i, row in enumerate(rows):
            fill = i % 2 == 0
            self.set_fill_color(*(_GREY if fill else _WHITE))
            for val, w in zip(row, col_widths):
                self.cell(w, 6, str(val), border=0, fill=fill, align="C")
            self.ln()
        self.ln(2)

    def info_line(self, label: str, value: str):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*_DARK_GREY)
        self.cell(50, 6, label + " :", align="R")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(0, 0, 0)
        self.cell(0, 6, value, ln=True)


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_admin_dashboard_pdf(data: dict) -> bytes:
    pdf = _ESISBAPDF(
        title="Tableau de Bord Administrateur — Statistiques d'Absences",
        subtitle=_subtitle_from_filters(data),
    )
    pdf.add_page()

    # Filters
    pdf.section_title("Filtres appliqués")
    pdf.info_line("Période", f"{data.get('date_from') or '—'}  →  {data.get('date_to') or '—'}")
    pdf.info_line("Filière", data.get("filiere_filter") or "Toutes")
    pdf.info_line("Type de séance", data.get("session_type_filter") or "Tous")
    pdf.ln(2)

    # KPIs
    pdf.section_title("Indicateurs Clés")
    pdf.kpi_row([
        ("Séances", str(data["total_sessions"])),
        ("Enregistrements", str(data["total_records"])),
        ("Absences", str(data["total_absences"])),
        ("Taux Global", f"{data['overall_rate']}%"),
    ])

    # By filière
    if data.get("by_filiere"):
        pdf.section_title("Répartition par Filière")
        pdf.table(
            ["Filière", "Enregistrements", "Absences", "Taux (%)"],
            [[r["filiere"], r["total_records"], r["absences"], f"{r['rate']}%"] for r in data["by_filiere"]],
            [60, 45, 45, 40],
        )

    # By module
    if data.get("by_module"):
        pdf.section_title("Répartition par Module")
        pdf.table(
            ["Module", "Enregistrements", "Absences", "Taux (%)"],
            [[r["module_nom"], r["total_records"], r["absences"], f"{r['rate']}%"] for r in data["by_module"][:20]],
            [80, 40, 40, 30],
        )

    # By session type
    if data.get("by_session_type"):
        pdf.section_title("Répartition par Type de Séance")
        pdf.table(
            ["Type", "Enregistrements", "Absences", "Taux (%)"],
            [[r["session_type"], r["total_records"], r["absences"], f"{r['rate']}%"] for r in data["by_session_type"]],
            [70, 45, 45, 30],
        )

    return pdf.output()


def generate_teacher_dashboard_pdf(data: dict) -> bytes:
    pdf = _ESISBAPDF(
        title="Tableau de Bord Enseignant — Statistiques par Module",
        subtitle=f"Enseignant ID: {data.get('teacher_id')}  |  Ratio justifiés: {data.get('overall_justified_ratio')}%",
    )
    pdf.add_page()

    pdf.section_title("Filtres appliqués")
    pdf.info_line("Période", f"{data.get('date_from') or '—'}  →  {data.get('date_to') or '—'}")
    pdf.ln(2)

    pdf.section_title("Statistiques par Module")
    if data.get("by_module"):
        pdf.table(
            ["Module", "Séances", "Total", "Absences", "Taux", "Justifiés", "Ratio just."],
            [
                [
                    r["module_nom"],
                    r["total_sessions"],
                    r["total_records"],
                    r["total_absences"],
                    f"{r['absence_rate']}%",
                    r["justified_count"],
                    f"{r['justified_ratio']}%",
                ]
                for r in data["by_module"]
            ],
            [50, 20, 20, 22, 22, 22, 34],
        )
    else:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 8, "Aucune donnée disponible.", ln=True)

    return pdf.output()


def generate_student_stats_pdf(data: dict) -> bytes:
    pdf = _ESISBAPDF(
        title="Mes Statistiques d'Absences",
        subtitle=f"Matricule: {data.get('student_matricule')}",
    )
    pdf.add_page()

    pdf.section_title("Bilan Global")
    pdf.kpi_row([
        ("Séances", str(data["total_sessions"])),
        ("Absences", str(data["total_absences"])),
        ("Taux global", f"{data['overall_rate']}%"),
        ("Justifiées", str(data["justified_count"])),
    ])

    pdf.section_title("Détail par Module")
    if data.get("by_module"):
        pdf.table(
            ["Module", "Séances", "Absences", "Taux", "Justifiées", "À risque"],
            [
                [
                    r["module_nom"],
                    r["total_sessions"],
                    r["total_absences"],
                    f"{r['absence_rate']}%",
                    r["justified_count"],
                    "⚠ OUI" if r["is_at_risk"] else "NON",
                ]
                for r in data["by_module"]
            ],
            [65, 22, 22, 22, 22, 37],
        )

    return pdf.output()


def generate_motif_breakdown_pdf(data: dict) -> bytes:
    pdf = _ESISBAPDF(title="Répartition des Motifs d'Absence")
    pdf.add_page()

    pdf.section_title("Répartition des Motifs d'Absence")
    pdf.info_line("Total absences (avec motif)", str(data["total_absences_with_motif"]))
    pdf.ln(3)

    if data.get("breakdown"):
        pdf.table(
            ["Motif", "Nombre", "Pourcentage"],
            [[r["label"], r["count"], f"{r['percentage']}%"] for r in data["breakdown"]],
            [100, 45, 45],
        )

    return pdf.output()


# ── Internal helpers ───────────────────────────────────────────────────────────

def _subtitle_from_filters(data: dict) -> str:
    parts = []
    if data.get("date_from") or data.get("date_to"):
        parts.append(f"{data.get('date_from', '?')} → {data.get('date_to', '?')}")
    if data.get("filiere_filter"):
        parts.append(f"Filière : {data['filiere_filter']}")
    return "  |  ".join(parts) if parts else "Toutes données"
