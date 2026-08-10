"""Dataclasses for the import pipeline."""

from dataclasses import dataclass, field, asdict


@dataclass
class ShowMeta:
    showId: str = ""
    showLabel: str = ""
    venue: str = ""
    client: str = ""
    po: str = ""
    pm: str = ""
    firstDate: str = ""
    lastDate: str = ""


@dataclass
class WorkRecord:
    date: str = ""            # ISO YYYY-MM-DD
    position: str = ""
    callStart: str = ""       # HH:MM
    callEnd: str = ""
    rate: float = None        # day rate; None when the workbook has no rates
    area: str = ""
    otNote: str = ""
    name: str = ""            # raw name from the sheet
    email: str = ""
    phoneDigits: str = ""
    sourceSheet: str = ""
    sourceRow: int = 0
    personKey: str = ""       # set by identity resolution
    recordHash: str = ""      # set once personKey is known


@dataclass
class StatusRow:
    name: str = ""
    email: str = ""
    phoneDigits: str = ""
    amount: float = None      # $ due / amount paid total
    grade: str = ""
    notes: str = ""
    sourceSheet: str = ""
    sourceRow: int = 0


@dataclass
class Person:
    personKey: str = ""
    name: str = ""
    email: str = ""
    phoneDigits: str = ""
    grade: str = ""
    notes: str = ""
    total: float = None       # fallback total (Crew Status $ due) when no rates
    # preview-only aggregates
    days: int = 0
    positions: list = field(default_factory=list)
    rateMin: float = None
    rateMax: float = None
    rateSum: float = None


def to_dict(obj):
    return asdict(obj)
