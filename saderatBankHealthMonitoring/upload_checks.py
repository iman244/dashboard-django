"""Turn a parsed sheet into warnings a person can act on.

Nothing here stops an upload: a readable sheet is always saved. Row
numbers are Excel's: row 1 holds the headers, so the first data row is 2.
"""
from collections import defaultdict

from .layouts import LAYOUTS
from .national_id import EXCEL_NATIONAL_ID_COLUMNS, canonical_national_id, is_national_id

LISTED = 20
FIRST_DATA_ROW = 2


def id_column_for(slug, columns):
    layout = LAYOUTS.get(slug)
    if layout:
        return layout['id_column'] if layout['id_column'] in columns else None
    # Only the known columns: person reports search nothing else.
    return next((c for c in EXCEL_NATIONAL_ID_COLUMNS if c in columns), None)


def looks_like(slug, columns):
    """Another layout whose id column this sheet has, if any."""
    return next((other for other, layout in LAYOUTS.items()
                 if other != slug and layout['id_column'] in columns), None)


def check_sheet(slug, rows, columns):
    """Warnings for a parsed sheet going into monitoring `slug`."""
    columns = [str(c) for c in columns]
    if not rows:
        return [{'level': 'warning', 'code': 'no_rows'}]

    layout = LAYOUTS.get(slug)
    id_column = id_column_for(slug, columns)
    warnings = []
    if layout and id_column is None:
        warning = {'level': 'warning', 'code': 'missing_id_column',
                   'column': layout['id_column'], 'found': columns[:LISTED]}
        other = looks_like(slug, columns)
        if other:
            warning['looks_like'] = other
        warnings.append(warning)
    elif id_column is None:
        warnings.append({'level': 'warning', 'code': 'no_id_column'})
    else:
        blank, invalid, seen = [], [], defaultdict(list)
        for index, row in enumerate(rows):
            number = index + FIRST_DATA_ROW
            value = canonical_national_id(row.get(id_column))
            if value in (None, ''):
                blank.append(number)
            elif not is_national_id(value):
                invalid.append({'row': number, 'value': str(value)})
            else:
                seen[value].append(number)
        duplicates = [{'value': v, 'rows': r} for v, r in seen.items() if len(r) > 1]
        if blank:
            warnings.append({'level': 'warning', 'code': 'blank_ids',
                             'count': len(blank), 'rows': blank[:LISTED]})
        if invalid:
            warnings.append({'level': 'warning', 'code': 'invalid_ids',
                             'count': len(invalid), 'rows': invalid[:LISTED]})
        if duplicates:
            warnings.append({'level': 'warning', 'code': 'duplicate_ids',
                             'count': len(duplicates), 'groups': duplicates[:LISTED]})

    if layout:
        missing = [c for c in layout['chart_columns'] if c not in columns]
        if missing:
            warnings.append({'level': 'warning', 'code': 'missing_columns', 'columns': missing})
    return warnings
