"""Small generated PDFs for parser tests."""

from pathlib import Path

import pymupdf


def write_text_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def write_table_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    rows, columns = 2, 2
    origin_x, origin_y, width, height = 72, 72, 160, 36
    values = [["Item", "Qty"], ["Widget", "2"]]
    shape = page.new_shape()
    for row in range(rows + 1):
        shape.draw_line(
            (origin_x, origin_y + row * height),
            (origin_x + columns * width, origin_y + row * height),
        )
    for column in range(columns + 1):
        shape.draw_line(
            (origin_x + column * width, origin_y),
            (origin_x + column * width, origin_y + rows * height),
        )
    shape.finish(color=(0, 0, 0), width=0.6)
    shape.commit()
    for row in range(rows):
        for column in range(columns):
            rect = pymupdf.Rect(
                origin_x + column * width + 4,
                origin_y + row * height + 8,
                origin_x + (column + 1) * width - 4,
                origin_y + (row + 1) * height - 4,
            )
            page.insert_textbox(rect, values[row][column], fontsize=11)
    document.save(path)
    document.close()


def write_image_pdf(path: Path, *, caption: str | None = None) -> None:
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8))
    pixmap.clear_with(180)
    page.insert_image(pymupdf.Rect(72, 72, 144, 144), pixmap=pixmap)
    if caption:
        page.insert_text((72, 180), caption)
    document.save(path)
    document.close()


def write_encrypted_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Secret page")
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="owner-secret",
    )
    document.close()
