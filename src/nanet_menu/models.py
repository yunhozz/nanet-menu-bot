from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Notice:
    sequence: str
    display_number: str
    title: str
    registered_on: date
    detail_url: str


@dataclass(frozen=True)
class Attachment:
    filename: str
    system_filename: str
    download_url: str


@dataclass(frozen=True)
class MenuSection:
    restaurant: str
    meal: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class DailyMenu:
    menu_date: date
    sections: tuple[MenuSection, ...]
    notice_title: str
    source_url: str
