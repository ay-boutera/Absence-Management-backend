from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import Response
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.pdfgen.canvas import Canvas


class NumberedCanvas(Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(page_count)
            super().showPage()
        super().save()

    def _draw_page_number(self, page_count: int):
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.grey)
        self.drawRightString(285 * mm, 10 * mm, f"Page {self.getPageNumber()} of {page_count}")


def build_csv_response(headers: list[str], rows: list[list], filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


def build_pdf_response(
    title: str,
    headers: list[str],
    rows: list[list],
    filename: str,
    metadata: dict | None = None,
) -> Response:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    body_style = styles["BodyText"]
    body_style.fontName = "Helvetica"
    body_style.fontSize = 9
    body_style.leading = 11

    story = []

    title_style = styles["Title"]
    title_style.fontName = "Helvetica-Bold"
    title_style.fontSize = 18
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 4 * mm))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    story.append(Paragraph(f"Generated at: {generated_at}", body_style))

    if metadata:
        for key, value in metadata.items():
            story.append(Paragraph(f"{key}: {value}", body_style))

    story.append(Spacer(1, 5 * mm))

    table_data: list[list] = [headers]
    for row in rows:
        table_data.append([Paragraph(str(cell) if cell is not None else "", body_style) for cell in row])

    table = Table(table_data, repeatRows=1)
    table_style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )
    table.setStyle(table_style)

    story.append(table)

    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )
