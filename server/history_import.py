"""Reads a blood bank's own file of past daily stock (.csv or .xlsx) into rows ready for inventory_snapshots. Rules only, no AI and no
outside service. Pure: no database, no web framework.

ONE function, `parse_history_upload`, is what both the preview (POST /forecast/historical-upload/preview) and the save
(POST /forecast/historical-upload) call, so what the user was shown is exactly what is saved.

Two layouts are recognised, in any column order and with extra columns ignored:
  long  one row per date + blood type + units          (the original snapshot_date, blood_type, units file)
  wide  one date column and one column per blood type  (A+, A-, B+, B-, AB+, AB-, O+, O-); turned into long rows
The header may sit under up to HEADER_SCAN_ROWS title rows. A row that cannot be used is skipped with a plain reason and never
guessed at; the rest of the file is still read.

Dates: ISO (2026-11-10), year first with / or ., day/month/year or month/day/year with / . - and a 4-digit year, written months
("10 Nov 2026", "Nov 10, 2026"), and real Excel dates. A numeric date such as 03/04/2026 can be read two ways. If any date in the file
has a part over 12 the file's order is proved and used; if the file proves both orders or none, `ambiguous` is true, nothing is
returned as usable and the caller must pass date_order ("dmy" or "mdy"); under a choice a row that cannot be read that way is skipped.
"""
import csv
import io
import re
from datetime import date, datetime
from typing import Iterator, Optional

MAX_ENTRIES = 15_000        # date + blood type figures in one file (a wide row is up to 8): about 5 years of 8 types. Bounds the request's time on the slow instance
MAX_COLS = 60
HEADER_SCAN_ROWS = 10
MAX_BLANK_RUN = 1000         # a sheet whose "used range" runs on over empty rows stops being read after this many in a row
MAX_UNITS = 1_000_000        # beyond any real stock count; also keeps the value inside the database's int range
SAMPLE_ROWS = 10
PROBLEMS_SHOWN = 20

BLOOD_TYPES = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")

# Header words, compared after lower-casing and dropping everything but letters and digits; whole-word match only.
_DATE_HEADERS = {"date", "snapshotdate", "day", "asof", "asofdate", "stockdate", "reportdate", "recorddate", "inventorydate", "dateofstock"}
_TYPE_HEADERS = {"bloodtype", "bloodtypes", "bloodgroup", "bloodgroups", "group", "type", "abo", "aborh", "abogroup", "bg"}
_UNITS_HEADERS = {"units", "unit", "qty", "quantity", "stock", "count", "onhand", "balance", "unitsonhand", "stockonhand", "unitsinstock",
                  "quantityonhand", "unitsavailable", "unitsavail", "noofunits", "numberofunits"}
_ROLE_LABEL = {"date": "date", "type": "blood type", "units": "units"}


class HistoryFileError(Exception):
    """The whole file is refused (too big once opened, not really a workbook): the message is for the user."""


EXCEL_UNAVAILABLE = "Excel files can't be read right now; save the file as .csv and try again."


class ExcelUnavailable(HistoryFileError):
    """openpyxl or defusedxml is not installed. Only an .xlsx is affected (the endpoints answer 503); nothing else needs either package, so
    neither is imported until a workbook is read, and a missing one can never stop the server from starting."""


def _excel_libraries():
    """(load_workbook, DefusedXmlException), imported here and nowhere else. Both are required: openpyxl alone would read a workbook's XML
    without defusedxml's protection against entity payloads, so a missing defusedxml also means no Excel."""
    try:
        from defusedxml.common import DefusedXmlException
        from openpyxl import load_workbook
    except ImportError:
        raise ExcelUnavailable(EXCEL_UNAVAILABLE)
    return load_workbook, DefusedXmlException


# ── blood types ─────────────────────────────────────────────────────────────

_BT = re.compile(r"(ab|a|b|o)-?(\+|pos(?:itive)?|plus|-|neg(?:ative)?|minus)(?:ve)?", re.ASCII)


def blood_type(value) -> Optional[str]:
    """'O+', 'o pos', 'O positive', 'Opositive', 'O POS', 'O -ve' -> the canonical form; anything else None."""
    if not isinstance(value, str):
        return None
    s = value.strip().lower().replace("−", "-").replace("–", "-").replace("‐", "-")
    m = _BT.fullmatch(re.sub(r"[\s_.()]", "", s))
    if not m:
        return None
    return m.group(1).upper() + ("+" if m.group(2) in ("+", "plus") or m.group(2).startswith("pos") else "-")


# ── dates ───────────────────────────────────────────────────────────────────

_TIME = r"(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
_YMD = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})" + _TIME, re.ASCII)
_NUM = re.compile(r"(\d{1,2})([/.\-])(\d{1,2})\2(\d{4})", re.ASCII)
_DMY_WORDS = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-./]*([A-Za-z]{3,9})\.?,?[\s\-./]*(\d{4})")
_MDY_WORDS = re.compile(r"([A-Za-z]{3,9})\.?[\s\-./]*(\d{1,2})(?:st|nd|rd|th)?,?[\s\-./]*(\d{4})")
_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_MONTHS = {}
for _i, _name in enumerate(_MONTH_NAMES, start=1):
    _MONTHS[_name.lower()] = _MONTHS[_name[:3].lower()] = _i
_MONTHS["sept"] = 9


def _real_date(y: int, m: int, d: int) -> date:
    try:
        return date(y, m, d)
    except ValueError:
        raise ValueError(f"{d}/{m}/{y} is not a real date")


def _read_date(v):
    """-> (value, kind). value is a date, or for a numeric day/month date the tuple (a, b, year, raw) left unresolved until the
    file's order is known. Raises ValueError(reason)."""
    if isinstance(v, datetime):
        return v.date(), "excel"
    if isinstance(v, date):
        return v, "excel"
    if not isinstance(v, str):
        raise ValueError(f"{v!r} is not a date we can read")
    s = v.strip()
    if m := _YMD.fullmatch(s):
        return _real_date(int(m[1]), int(m[2]), int(m[3])), "iso"
    if m := _NUM.fullmatch(s):
        return (int(m[1]), int(m[3]), int(m[4]), s), "num"
    if m := _DMY_WORDS.fullmatch(s):
        mon = _MONTHS.get(m[2].lower())
        if mon:
            return _real_date(int(m[3]), mon, int(m[1])), "written"
    if m := _MDY_WORDS.fullmatch(s):
        mon = _MONTHS.get(m[1].lower())
        if mon:
            return _real_date(int(m[3]), mon, int(m[2])), "written"
    raise ValueError(f"'{s}' is not a date we can read")


def _say(d: date) -> str:
    return f"{d.day} {_MONTH_NAMES[d.month - 1]} {d.year}"


# ── units ───────────────────────────────────────────────────────────────────

_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+", re.ASCII)
_WHOLE = re.compile(r"\d+(?:\.0+)?", re.ASCII)
_DECIMAL = re.compile(r"-?\d*\.\d+", re.ASCII)


def _read_units(v) -> int:
    """A whole number 0 or more. Raises ValueError(reason): a blank is not 0, a decimal is not rounded, a negative is not accepted."""
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError("units is blank (a blank is not counted as 0)")
    if isinstance(v, bool):
        raise ValueError(f"units {v!r} is not a number")
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("units is not a number")
        if v != int(v):
            raise ValueError(f"units {v} is not a whole number")
        n = int(v)
    elif not isinstance(v, str):  # a date, a time or anything else Excel can hold in a cell
        raise ValueError("units is not a number")
    else:
        s = v.strip()
        if _THOUSANDS.fullmatch(s):
            s = s.replace(",", "")
        if _WHOLE.fullmatch(s):
            n = int(s.split(".")[0])
        elif re.fullmatch(r"-\d+(?:\.0+)?", s, re.ASCII):
            raise ValueError(f"units {s} is negative; it must be 0 or more")
        elif _DECIMAL.fullmatch(s):
            raise ValueError(f"units {s} is not a whole number")
        else:
            raise ValueError(f"units '{s}' is not a number")
    if n < 0:
        raise ValueError(f"units {n} is negative; it must be 0 or more")
    if n > MAX_UNITS:
        raise ValueError(f"units {n:,} is too large to be a stock count")
    return n


# ── reading the file ────────────────────────────────────────────────────────

def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _csv_rows(text: str) -> Iterator[tuple[int, list]]:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delimiter = max(",;\t", key=lambda d: first.count(d))  # a spreadsheet saved in some regional settings separates with ; or a tab
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    for row in reader:
        if any(not _blank(c) for c in row):
            yield reader.line_num, row[:MAX_COLS]


# An XML entity payload ("billion laughs") or an external reference in the workbook: defusedxml refuses it, we say so plainly.
_UNSAFE_XML = ("That workbook contains something unsafe in its XML (a DOCTYPE or entity definition), so it was refused. "
               "Open the data in Excel and save it again as .xlsx or .csv.")


def _is_unsafe_xml(error: BaseException) -> bool:
    """openpyxl wraps an XML error in its own ValueError; defusedxml's refusal is the cause, so look down the chain."""
    DefusedXmlException = _excel_libraries()[1]
    while error is not None:
        if isinstance(error, DefusedXmlException):
            return True
        error = error.__cause__ or error.__context__
    return False


def _xlsx_rows(content: bytes, info: dict) -> Iterator[tuple[int, tuple]]:
    load_workbook = _excel_libraries()[0]  # imported here: only an .xlsx pays for it
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)  # data_only: a formula's last saved value, not "=SUM(...)"
    except Exception as error:
        if _is_unsafe_xml(error):
            raise HistoryFileError(_UNSAFE_XML)
        raise HistoryFileError("That file could not be opened as an Excel workbook. Open it in Excel, save it again as .xlsx or .csv, and try again.")
    try:
        sheets = wb.worksheets
        if not sheets:
            raise HistoryFileError("That workbook has no sheets.")
        ws = sheets[0]
        info["sheet"] = ws.title
        info["other_sheets"] = [
            o.title for o in sheets[1:]
            if any(not _blank(c) for r in o.iter_rows(max_row=50, max_col=MAX_COLS, values_only=True) for c in r)
        ]
        blanks = 0
        for n, row in enumerate(ws.iter_rows(max_col=MAX_COLS, values_only=True), start=1):
            if all(_blank(c) for c in row):
                blanks += 1
                if blanks > MAX_BLANK_RUN:
                    break
                continue
            blanks = 0
            yield n, row
    except HistoryFileError:
        raise
    except Exception as error:
        if _is_unsafe_xml(error):
            raise HistoryFileError(_UNSAFE_XML)
        raise HistoryFileError("That workbook could not be read. Open it in Excel, save it again as .xlsx or .csv, and try again.")
    finally:
        wb.close()


# ── headers ─────────────────────────────────────────────────────────────────

def _key(c) -> str:
    return re.sub(r"[^a-z0-9]", "", c.lower()) if isinstance(c, str) else ""


def _find_header(row, columns: Optional[dict] = None):
    """None if this row is not a header; a str (a reason) if it is one but unusable; else the layout and its columns.
    A role (date, blood type, units) that more than one column could fill is not an error: unless `columns` names one
    ({"units": "UNITS"}, the header text as the preview showed it), the role goes to "ask" with its candidates and its column is None.
    A name that is not a header of this row is a HistoryFileError (the endpoints answer 400)."""
    roles = {"date": [], "type": [], "units": []}
    wide = []  # (column, canonical type)
    names = {"date": _DATE_HEADERS, "type": _TYPE_HEADERS, "units": _UNITS_HEADERS}
    for i, c in enumerate(row):
        canon = blood_type(c)  # before the generic key: dropping punctuation would make "A+" and "A-" the same word
        if canon:
            wide.append((i, canon))
            continue
        for role, words in names.items():
            if _key(c) in words:
                roles[role].append(i)
    if not (roles["date"] and ((roles["type"] and roles["units"]) or wide)):
        return None
    text = lambda i: str(row[i]).strip().lstrip("﻿")  # a BOM can sit on the first header
    if wide and (roles["type"] or roles["units"]):
        return "The columns look like two layouts at once: a blood type column and also one column per blood type. Keep only one of them."
    ask: dict[str, list] = {}
    chosen = columns or {}
    for role in (("date",) if wide else ("date", "type", "units")):
        want = chosen.get(role)
        if want is not None:
            same = [i for i in range(len(row)) if not _blank(row[i]) and text(i) == want.strip().lstrip("\ufeff")]
            if not same:
                raise HistoryFileError(f"The column you chose, '{want}', is not in this file.")
            roles[role] = same[:1]  # the user's column; the other candidates are ignored
        elif len(roles[role]) > 1:
            ask[role] = [(i, text(i)) for i in roles[role]]
    seen = {}
    for i, canon in wide:
        if canon in seen:
            return f"Two columns are both {canon} ({text(seen[canon])} and {text(i)}). Remove one of them."
        seen[canon] = i
    col = lambda role: None if role in ask else roles[role][0]
    name = lambda role: None if role in ask else text(roles[role][0])
    if wide:
        return {"layout": "wide", "date": col("date"), "cols": wide, "names": {text(i): c for i, c in wide}, "date_name": name("date"), "ask": ask}
    return {"layout": "long", "date": col("date"), "type": col("type"), "units": col("units"),
            "date_name": name("date"), "type_name": name("type"), "units_name": name("units"), "ask": ask}


# ── the result ──────────────────────────────────────────────────────────────

class Parsed:
    def __init__(self, kind: str):
        self.kind = kind
        self.fatal: Optional[str] = None
        self.layout: Optional[str] = None
        self.mapping: dict = {}
        self.date_format: Optional[str] = None
        self.date_order: Optional[str] = None      # the order applied to numeric day/month dates, if the file has any
        self.ambiguous = False
        self.ambiguous_example: Optional[dict] = None
        self.rows: list[dict] = []                 # {"row", "snapshot_date": date, "blood_type", "units"}
        self.problems: list[dict] = []             # {"row", "reason"}; one per skipped entry
        self.header_row: Optional[int] = None
        self.kinds: set = set()                    # which kinds of date the file used
        self.sheet: Optional[str] = None
        self.other_sheets: list[str] = []
        self.column_choices: dict = {}             # role -> [{"name", "samples"}]: columns the user must pick between; then nothing is read

    @property
    def errors(self) -> list[dict]:
        """What the upload endpoint records and returns as `errors`: every problem, or the one reason the whole file was unusable."""
        return [{"row": 0, "reason": self.fatal}] if self.fatal else self.problems

    def preview(self) -> dict:
        usable, skipped = len(self.rows), len(self.problems)
        pairs = {(r["snapshot_date"], r["blood_type"]) for r in self.rows}
        return {
            "file_type": self.kind, "sheet": self.sheet, "other_sheets": self.other_sheets,
            "layout": self.layout, "mapping": self.mapping, "header_row": self.header_row,
            "date_format": self.date_format, "date_order": self.date_order,
            "ambiguous_dates": self.ambiguous, "ambiguous_example": self.ambiguous_example,
            "row_counts": {"total": usable + skipped, "usable": usable, "skipped": skipped},
            "repeated": usable - len(pairs),  # entries that share a date and blood type with a later one: only the last is kept
            "sample_rows": [{"row": r["row"], "snapshot_date": r["snapshot_date"].isoformat(), "blood_type": r["blood_type"], "units": r["units"]}
                            for r in self.rows[:SAMPLE_ROWS]],
            "problems": self.errors[:PROBLEMS_SHOWN], "more_problems": max(0, len(self.errors) - PROBLEMS_SHOWN),
            "fatal": self.fatal,
            "needs_column_choice": bool(self.column_choices),
            "column_choices": self.column_choices,  # {"units": [{"name": "UNITS", "samples": ["70", "19", "21"]}, ...]} while rows are withheld
        }


_FORMAT_WORDS = {"iso": "year-month-day, like 2026-11-10", "excel": "Excel date cells", "written": "the month written out, like 10 Nov 2026"}


def parse_history_upload(kind: str, content, today: date, date_order: Optional[str] = None, columns: Optional[dict] = None) -> Parsed:
    """kind "csv" (content: the text) or "xlsx" (content: the bytes). `columns` is the user's answer when the preview listed column_choices:
    {"units": header text, "date": ..., "type": ...}. Raises HistoryFileError for a file refused as a whole (ExcelUnavailable when the Excel
    libraries are not installed)."""
    out = Parsed(kind)
    info: dict = {}
    if kind == "xlsx":
        _excel_libraries()  # fail before anything is opened
    rows_iter = _csv_rows(content) if kind == "csv" else _xlsx_rows(content, info)
    try:
        _read(rows_iter, out, today, date_order if date_order in ("dmy", "mdy") else None, columns)
    finally:
        rows_iter.close()
    out.sheet, out.other_sheets = info.get("sheet"), info.get("other_sheets", [])
    return out


def _sample_text(v) -> str:
    return (v.date().isoformat() if isinstance(v, datetime) else str(v).strip())[:40]


def _read(rows_iter, out: Parsed, today: date, order: Optional[str], columns: Optional[dict] = None) -> None:
    header, scanned, first_names = None, 0, []
    for n, row in rows_iter:
        if header is None:
            scanned += 1
            if scanned == 1:
                first_names = [str(c).strip() for c in row if not _blank(c)][:8]
            found = _find_header(row, columns)
            if isinstance(found, str):
                out.fatal = found
                return
            if found:
                header, out.header_row = found, n
                out.column_choices = {role: [{"name": name, "samples": []} for _, name in cands] for role, cands in found["ask"].items()}
            elif scanned >= HEADER_SCAN_ROWS:
                break
            continue
        if header["ask"]:
            # Several columns could fill a role: the first values of each candidate are all the user needs to tell them apart.
            for role, cands in header["ask"].items():
                for (i, _), shown in zip(cands, out.column_choices[role]):
                    if len(shown["samples"]) < 3 and i < len(row) and not _blank(row[i]):
                        shown["samples"].append(_sample_text(row[i]))
            if n - out.header_row >= 200 or all(len(s["samples"]) == 3 for c in out.column_choices.values() for s in c):
                break
            continue
        _data_row(n, row, header, out)
    if header is not None and header["ask"]:
        out.layout = header["layout"]
        return
    if header is None:
        out.fatal = ("The file is empty." if not scanned else
                     "We could not find the columns we need. The first row has: " + ", ".join(first_names) + ". We need a date column and either "
                     "a blood type column and a units column, or one column per blood type (A+, A-, B+, B-, AB+, AB-, O+, O-).")
        return
    out.layout = header["layout"]
    out.mapping = ({"date": header["date_name"], "blood_type": header["type_name"], "units": header["units_name"]} if header["layout"] == "long"
                   else {"date": header["date_name"], "blood_types": header["names"]})
    _resolve_dates(out, today, order)


def _data_row(n: int, row, header: dict, out: Parsed) -> None:
    """Adds this row's entries to out.rows / out.problems. A numeric day/month date is left as the tuple from _read_date."""
    cell = lambda i: row[i] if i < len(row) else None
    wide = header["layout"] == "wide"
    cols = header["cols"] if wide else [(header["type"], None)]
    if _blank(cell(header["date"])) and all(_blank(cell(i)) for i, _ in cols) and (wide or _blank(cell(header["units"]))):
        return
    if len(out.rows) + len(out.problems) >= MAX_ENTRIES:
        raise HistoryFileError(f"That file has more than {MAX_ENTRIES:,} figures (one per date and blood type). Upload it in smaller pieces, for example one year at a time.")
    try:
        d, kind = _read_date(cell(header["date"]))
    except ValueError as e:
        out.problems.append({"row": n, "reason": str(e) if not _blank(cell(header["date"])) else "the date is blank"})
        return
    out.kinds.add(kind)
    for i, canon in cols:
        if wide:
            bt = canon
        else:
            bt = blood_type(cell(i))
            if bt is None:
                out.problems.append({"row": n, "reason": "the blood type is blank" if _blank(cell(i)) else f"blood type '{str(cell(i)).strip()}' is not one we recognise"})
                continue
        try:
            units = _read_units(cell(header["units"]) if not wide else cell(i))
        except ValueError as e:
            # The type and the day are known, so say what happens to them: the day is not zeroed, the previous value stands
            # (an existing row is left alone; otherwise the forecast carries the last value forward, see _resample_daily_series).
            out.problems.append({"row": n, "reason": f"{bt}: {e}; we keep the previous value for that day."})
            continue
        out.rows.append({"row": n, "snapshot_date": d, "blood_type": bt, "units": units})


def _resolve_dates(out: Parsed, today: date, order: Optional[str]) -> None:
    kinds = out.kinds
    dmy = mdy = either = 0
    example = None
    for r in out.rows:
        d = r["snapshot_date"]
        if isinstance(d, tuple):
            a, b = d[0], d[1]
            if a > 12 and b <= 12:
                dmy += 1
            elif b > 12 and a <= 12:
                mdy += 1
            elif a <= 12 and b <= 12 and a != b:
                either += 1
                example = example or d
    if "num" in kinds and order is None:
        if dmy and not mdy:
            order = "dmy"
        elif mdy and not dmy:
            order = "mdy"
        elif either or (dmy and mdy):
            out.ambiguous = True
            if example:
                a, b, y, raw = example
                out.ambiguous_example = {"text": raw, "day_first": _say(date(y, b, a)), "month_first": _say(date(y, a, b))}
            else:  # contradicting rows only: show one of each kind
                out.ambiguous_example = None
            out.rows, out.problems = [], []   # nothing is usable until the user chooses
            return
        else:
            order = "dmy"  # every numeric date reads the same either way (03/03/2026)
    out.date_order = order if "num" in kinds else None

    keep: list[dict] = []
    for r in out.rows:
        d = r["snapshot_date"]
        if isinstance(d, tuple):
            a, b, y, raw = d
            day, month = (a, b) if order == "dmy" else (b, a)
            try:
                d = date(y, month, day)
            except ValueError:
                out.problems.append({"row": r["row"], "reason": f"{raw} cannot be read as {'day/month/year' if order == 'dmy' else 'month/day/year'}"})
                continue
        if d >= today:
            out.problems.append({"row": r["row"], "reason": f"the date {_say(d)} must be before today ({_say(today)}); today's stock is recorded automatically, not backfilled"})
            continue
        r["snapshot_date"] = d
        keep.append(r)
    out.rows = keep
    out.problems.sort(key=lambda p: p["row"])

    parts = [_FORMAT_WORDS[k] for k in ("iso", "written", "excel") if k in kinds]
    if "num" in kinds:
        parts.append({"dmy": "day first, like 10/11/2026 for 10 November", "mdy": "month first, like 11/10/2026 for 10 November"}[order])
    out.date_format = " and ".join(parts) or None
