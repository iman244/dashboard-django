"""Folding a national id to one canonical spelling.

An operator types on a Persian keyboard, which produces ۰۱۲; some source data
carries Arabic-Indic ٠١٢. As a *display* concern the dashboard already handles
this with `digitsFaToEn`. Here it is an *identity* concern: `national_id` is
half of a unique constraint, and '۰۰۱۲۳۴۵۶۷۸' and '0012345678' are different
strings. Without folding, the constraint would accept both and split one
patient's files across two entries without raising anything.

The client normalizes too. This is the guarantee; that is the courtesy.
"""
import re

# Persian (U+06F0..) and Arabic-Indic (U+0660..) digits, in order.
_DIGIT_MAP = str.maketrans(
    '۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩',
    '01234567890123456789',
)


def normalize_national_id(value):
    """`value` with Persian/Arabic digits folded to ASCII and ends trimmed."""
    if not isinstance(value, str):
        return value
    return value.translate(_DIGIT_MAP).strip()


# The columns an Excel report keeps a national id in: step_1 and step_2.
EXCEL_NATIONAL_ID_COLUMNS = ('personel.کد ملی', 'تجمیع نتایج.کد ملی', 'کد ملی')


def canonical_national_id(value):
    """The ten-digit text form of a national id read from a spreadsheet cell.

    Excel stores 0012345678 typed into a number cell as 12345678, and pandas
    may hand it back as 12345678.0. Reading the column as text cannot bring
    the zeros back, so they are restored here: 8 or 9 digits are left-padded
    to 10, the same rule the dashboard applies when it looks a patient up.
    Anything that is not such a number is returned folded but otherwise as is.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        value = str(value)
    value = normalize_national_id(value)
    if isinstance(value, str) and re.fullmatch(r'[0-9]{8,9}', value):
        return value.zfill(10)
    return value


def is_national_id(value):
    """Whether `value` is exactly ten ASCII digits.

    Not `str.isdigit()`: it also accepts superscripts such as '²', which the
    database lookups would never match and `int()` refuses.
    """
    return isinstance(value, str) and re.fullmatch(r'[0-9]{10}', value) is not None
