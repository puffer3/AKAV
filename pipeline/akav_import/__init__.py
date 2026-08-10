"""AKAV job-workbook import pipeline.

Parses per-show .xlsx workbooks (crew, positions, rates, grades) into
normalized JSON and uploads it to the AKAV Apps Script endpoint, which
maintains the master one-row-per-person Google Sheet.
"""

__version__ = "0.1.0"
