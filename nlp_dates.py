"""Parses relative date phrases out of a free-text question ("last 3 months",
"this quarter", "Jan to March", etc.) so the chat box can override the locked
timeframe when the user's wording implies a different range. Extracted as its
own module because it's a genuinely distinct concern from query generation or
UI rendering — testable in isolation, reusable elsewhere (e.g. if a second
entry point for free-text questions is ever added)."""
import re
import calendar
from datetime import date, timedelta

MONTH_MAP = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
             "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}


def parse_relative_dates(question: str, fallback_from: str, fallback_to: str) -> tuple:
    """Returns (date_from, date_to) as YYYY-MM-DD strings. Falls back to the
    given (fallback_from, fallback_to) — typically the locked context's
    timeframe — if nothing relative is detected in the question."""
    today = date.today()
    q = question.lower()

    m = re.search(r'last\s+(\d+)\s+month', q)
    if m:
        n = int(m.group(1))
        fm_year, fm_month = today.year, today.month - n
        while fm_month <= 0:
            fm_month += 12
            fm_year -= 1
        return f"{fm_year}-{str(fm_month).zfill(2)}-01", today.strftime("%Y-%m-%d")

    if "this month" in q:
        return today.replace(day=1).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    if "last month" in q:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1).strftime("%Y-%m-%d"), last_prev.strftime("%Y-%m-%d")

    if "quarter" in q:
        qm = (today.month - 1) // 3 * 3 + 1
        if "last" in q:
            qm -= 3
            if qm <= 0:
                qm += 12
        qend = qm + 2
        return f"{today.year}-{str(qm).zfill(2)}-01", f"{today.year}-{str(qend).zfill(2)}-{calendar.monthrange(today.year, qend)[1]}"

    if "this year" in q:
        return f"{today.year}-01-01", today.strftime("%Y-%m-%d")
    if "last year" in q:
        return f"{today.year-1}-01-01", f"{today.year-1}-12-31"

    if "today" in q:
        return today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")
    if "yesterday" in q:
        y = today - timedelta(days=1)
        return y.strftime("%Y-%m-%d"), y.strftime("%Y-%m-%d")

    # Explicit month names, with or without a year: "Jan 2026", "Jan to March"
    cy = str(today.year)
    year_months = re.findall(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})", question, re.IGNORECASE)
    if not year_months:
        months_only = re.findall(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*", question, re.IGNORECASE)
        if len(months_only) >= 2:
            year_months = [(months_only[0], cy), (months_only[-1], cy)]
        elif len(months_only) == 1:
            year_months = [(months_only[0], cy)]

    if len(year_months) >= 2:
        m1, y1 = year_months[0]
        m2, y2 = year_months[-1]
        d_from = f"{y1}-{MONTH_MAP[m1.lower()[:3]]}-01"
        last_day = calendar.monthrange(int(y2), int(MONTH_MAP[m2.lower()[:3]]))[1]
        d_to = f"{y2}-{MONTH_MAP[m2.lower()[:3]]}-{last_day}"
        return d_from, d_to
    if len(year_months) == 1:
        m1, y1 = year_months[0]
        d_from = f"{y1}-{MONTH_MAP[m1.lower()[:3]]}-01"
        last_day = calendar.monthrange(int(y1), int(MONTH_MAP[m1.lower()[:3]]))[1]
        return d_from, f"{y1}-{MONTH_MAP[m1.lower()[:3]]}-{last_day}"

    return fallback_from, fallback_to
