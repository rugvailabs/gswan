import datetime
import decimal
from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    date: datetime.date
    description: str
    amount: decimal.Decimal
    category: str
