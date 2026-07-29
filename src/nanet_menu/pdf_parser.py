import io
import re
from collections.abc import Iterable, Sequence
from datetime import date

import pdfplumber

from nanet_menu.date_range import normalize_text
from nanet_menu.errors import MenuParseError
from nanet_menu.models import MenuSection

_DAY_RE = re.compile(r"(?:(?P<month>\d{1,2})\s*[./월]\s*)?(?P<day>\d{1,2})\s*(?:[일.]|\()")
_MEALS = ("조식", "중식", "석식")
_EXCLUDED = ("원산지", "알레르기", "열량", "kcal", "Kcal")


def extract_menu(pdf_bytes: bytes, target: date) -> tuple[MenuSection, ...]:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes), unicode_norm="NFKC") as pdf:
            if not pdf.pages:
                raise MenuParseError("PDF에 페이지가 없습니다.")
            if sum(len(page.chars) for page in pdf.pages) < 30:
                raise MenuParseError(
                    "PDF에서 텍스트를 찾지 못했습니다. 이미지형 PDF일 수 있습니다."
                )
            tables = [table for page in pdf.pages for table in page.extract_tables()]
            sections = extract_menu_from_tables(tables, target)
    except MenuParseError:
        raise
    except Exception as exc:
        raise MenuParseError("PDF를 열거나 표를 추출하지 못했습니다.") from exc
    if not sections:
        raise MenuParseError(f"PDF에서 {target.isoformat()}의 식단을 신뢰성 있게 찾지 못했습니다.")
    return sections


def extract_menu_from_tables(
    tables: Iterable[Sequence[Sequence[str | None]]],
    target: date,
) -> tuple[MenuSection, ...]:
    sections: list[MenuSection] = []
    for table in tables:
        normalized = [[_normalize_cell(cell or "") for cell in row] for row in table]
        if not normalized:
            continue
        target_row = _find_date_row(normalized, target)
        if target_row is not None:
            sections.extend(_sections_from_date_block(normalized, target_row))
    return _deduplicate_sections(sections)


def _find_date_row(rows: Sequence[Sequence[str]], target: date) -> int | None:
    for index, row in enumerate(rows):
        if row and _date_cell_matches(row[0], target):
            return index
    return None


def _date_cell_matches(value: str, target: date) -> bool:
    match = _DAY_RE.search(value)
    if not match:
        return False
    month = int(match["month"]) if match["month"] else target.month
    return month == target.month and int(match["day"]) == target.day


def _sections_from_date_block(
    rows: Sequence[Sequence[str]],
    target_row: int,
) -> list[MenuSection]:
    sections: list[MenuSection] = []
    end_row = next(
        (
            index
            for index in range(target_row + 1, len(rows))
            if rows[index] and _DAY_RE.search(rows[index][0])
        ),
        len(rows),
    )
    block = rows[target_row:end_row]
    dinner_offset = next(
        (
            index
            for index, row in enumerate(block)
            if any("<저녁>" in cell.replace(" ", "") for cell in row)
        ),
        None,
    )
    header_rows = rows[:target_row]
    restaurants = _restaurant_columns(header_rows)

    for column, (restaurant, subgroup) in restaurants.items():
        pre_dinner_rows = block[:dinner_offset] if dinner_offset is not None else block
        pre_dinner_cells = [
            row[column] for row in pre_dinner_rows if column < len(row) and row[column]
        ]
        if len(pre_dinner_cells) >= 2:
            _append_section(
                sections,
                restaurant,
                _meal_name("조식", subgroup),
                [pre_dinner_cells[0]],
            )
            _append_section(
                sections,
                restaurant,
                _meal_name("중식", subgroup),
                pre_dinner_cells[1:],
            )
        elif pre_dinner_cells:
            _append_section(
                sections,
                restaurant,
                _meal_name("중식", subgroup),
                pre_dinner_cells,
            )

        if dinner_offset is not None:
            dinner_row = block[dinner_offset]
            if column < len(dinner_row) and dinner_row[column]:
                _append_section(
                    sections,
                    restaurant,
                    _meal_name("석식", subgroup),
                    [dinner_row[column]],
                )
    return sections


def _restaurant_columns(
    header_rows: Sequence[Sequence[str]],
) -> dict[int, tuple[str, str]]:
    width = max((len(row) for row in header_rows), default=0)
    restaurants: dict[int, tuple[str, str]] = {}
    previous = ""
    for column in range(1, width):
        candidates = [row[column] for row in header_rows[:3] if column < len(row) and row[column]]
        restaurant = next(
            (_clean_restaurant(value) for value in candidates if "식당" in value),
            "",
        )
        if restaurant:
            previous = restaurant
        elif previous:
            restaurant = previous
        if not restaurant:
            continue
        subgroup = ""
        if len(header_rows) >= 3 and column < len(header_rows[2]):
            label = header_rows[2][column]
            if label and any(marker in label for marker in ("점심", "한식", "분식")):
                subgroup = label.replace("점심", "").strip()
        restaurants[column] = (restaurant, subgroup)
    return restaurants


def _clean_restaurant(value: str) -> str:
    first_line = value.splitlines()[0]
    match = re.search(r".*?식당", first_line)
    return normalize_text(match.group(0) if match else first_line)


def _meal_name(meal: str, subgroup: str) -> str:
    return f"{meal} {subgroup}".strip()


def _append_section(
    sections: list[MenuSection],
    restaurant: str,
    meal: str,
    values: Sequence[str],
) -> None:
    items = tuple(item for value in values for item in _split_items(value) if _is_menu_item(item))
    if items:
        sections.append(MenuSection(restaurant, meal, items))


def _split_items(value: str) -> list[str]:
    value = value.replace("ㆍ", "\n").replace("·", "\n")
    parts: list[str] = []
    for part in re.split(r"[/\n\r]+", value):
        cleaned = normalize_text(part).lstrip("-• ")
        cleaned = re.sub(r"<\s*저\s*녁\s*>", "", cleaned).strip()
        if cleaned:
            tokens = cleaned.split()
            if len(tokens) > 1 and all(len(token) == 1 for token in tokens):
                cleaned = "".join(tokens)
            parts.append(cleaned)
    return parts


def _normalize_cell(value: str) -> str:
    return "\n".join(
        normalized for line in value.splitlines() if (normalized := normalize_text(line))
    )


def _is_menu_item(value: str) -> bool:
    if len(value) < 2 or any(marker.lower() in value.lower() for marker in _EXCLUDED):
        return False
    if "영업을하지않습니다" in value.replace(" ", ""):
        return False
    if value in _MEALS or _DAY_RE.fullmatch(value) or value.startswith(("[", "<")):
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", value):
        return False
    return re.fullmatch(r"[\d,.]+\s*(?:kcal)?", value, re.IGNORECASE) is None


def _deduplicate_sections(sections: list[MenuSection]) -> tuple[MenuSection, ...]:
    unique: list[MenuSection] = []
    for section in sections:
        if section not in unique:
            unique.append(section)
    return tuple(unique)
