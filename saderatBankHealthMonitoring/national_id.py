"""Folding a national id to one canonical spelling.

An operator types on a Persian keyboard, which produces ۰۱۲; some source data
carries Arabic-Indic ٠١٢. As a *display* concern the dashboard already handles
this with `digitsFaToEn`. Here it is an *identity* concern: `national_id` is
half of a unique constraint, and '۰۰۱۲۳۴۵۶۷۸' and '0012345678' are different
strings. Without folding, the constraint would accept both and split one
patient's files across two entries without raising anything.

The client normalizes too. This is the guarantee; that is the courtesy.
"""

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
