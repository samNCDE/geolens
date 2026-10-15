"""Suite-wide settings.

Section plots are on by default in the stream types (they are how a user
checks a call), but the landscape fixtures carry thousands of sections, and
rendering them in every test that runs the pipeline would multiply the
suite's time for nothing. tests/test_section_plots.py turns them on
explicitly.
"""
import os

os.environ.setdefault("GEOLENS_SECTION_PLOTS", "0")
