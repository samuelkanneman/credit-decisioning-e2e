"""Modelling decisions that are fixed by ADR-002."""

TARGET = "bad_12m"
TARGET_WINDOW_MONTHS = 12

# Issue-year ranges, inclusive.
SPLITS: dict[str, tuple[int, int]] = {
    "dev": (2013, 2015),
    "oot": (2016, 2016),
    "prod": (2017, 2017),
}
