# MIT License

# Copyright (c) 2020 Dr. Jan-Philip Gehrcke

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
This program is part of https://github.com/jgehrcke/covid-19-germany-gae
"""


import argparse
import logging
import sys
import json
import fnmatch

import pandas as pd
import pandas.testing


log = logging.getLogger()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
    datefmt="%y%m%d-%H:%M:%S",
)


def main():

    args = parse_args()

    COLUMN_NAME_IGNORE_LIST = args.ignore_column
    COLUMN_NAME_ALLOW_PATTERN = args.column_allowlist_pattern

    df_base, df_ext = parse_files_and_check_sanity(args)

    # Build four data frames (general case):
    #   only_in_base: not covered by ext
    #   overlap_base: the overlap timeframe, in the base data set
    #   overlap_ext: the overlap timeframe, in the extension data set
    #   only_in_ext: not covered by base

    df_only_in_base = df_base[df_base.index < df_ext.index.min()]
    df_only_in_ext = df_ext[df_ext.index > df_base.index.max()]
    df_overlap_base = df_base[df_base.index >= df_ext.index.min()]
    df_overlap_ext = df_ext[df_ext.index <= df_base.index.max()]

    # print(df_only_in_ext)

    log.info("deep-compare (only) the overlap between the two data sets")

    # I did not really have success with the `atol` and `rtol` values of this
    # helper:
    # pandas.testing.assert_frame_equal(df_overlap_base, df_overlap_ext, check_exact=True)
    # Using df.compare() instead, which is more work but also a more robust,
    # transparent, version-stable solution.

    # Using `base` as the base of this comparison means that `base` (old) data
    # will appear as `self` in the output, and `ext` (new) data will appear as
    # `other`.
    df_diff = df_overlap_base.compare(df_overlap_ext)
    log.info("df_diff:\n%s", df_diff)

    # Iterate over `base` columns, iteration over `df_diff` columns would yield
    # the multi-index result, e.g.  ('DE-BB', 'self')
    column_etadgets = {}
    for column in df_base:
        # `df.compare()`: only the rows and columns with different values are
        # kept!
        if column not in df_diff:
            log.info(
                "df_diff does not contain column (data sets are equal here): %s", column
            )
            continue
        log.info("column has differences: %s", column)
        old = df_diff[column]["self"]
        new = df_diff[column]["other"]
        cdiff = new - old
        # print(cdiff)

        # Common convention: ">=" -> "greater than or equal to" -> "GE".
        # Find Earliest Timestamp for Abs(Diff) to be Greater than or Equal to
        # the Threshold and call that ETADGET -- naming is hard.
        etadget = (cdiff.apply(abs) - args.threshold).apply(abs).idxmin()
        log.info(
            "%s earliest timestamp for abs(diff) >= threshold %s: %s",
            column,
            args.threshold,
            etadget,
        )

        if COLUMN_NAME_IGNORE_LIST and column in COLUMN_NAME_IGNORE_LIST:
            log.info("ignore column %s: do not consider its etadget", column)
            continue

        if COLUMN_NAME_ALLOW_PATTERN:
            if not fnmatch.fnmatchcase(column, COLUMN_NAME_ALLOW_PATTERN):
                log.info(
                    "ignore column %s: does not match %s",
                    column,
                    COLUMN_NAME_ALLOW_PATTERN,
                )
                continue
            log.info(
                "column `%s`: matches pattern `%s`",
                column,
                COLUMN_NAME_ALLOW_PATTERN,
            )

        log.info("consider column %s for minimal ETADGET determination", column)
        column_etadgets[column] = etadget
        # etadgets.append(etadget)

    log.info("all columns analyzed")
    min_etadget_column = min(column_etadgets, key=column_etadgets.get)
    min_etadget = column_etadgets[min_etadget_column]
    log.info(
        "candidate columns and their corresponding ETADGET:\n%s",
        json.dumps(column_etadgets, indent=2),
    )
    log.info(
        "the minimal ETADGET across considered columns: %s (column: %s)",
        min_etadget,
        min_etadget_column,
    )

    log.info("assemble output dataframe")

    df_overlap_use_from_base = df_overlap_base[df_overlap_base.index < min_etadget]
    df_overlap_use_from_ext = df_overlap_ext[df_overlap_ext.index >= min_etadget]

    # Now stich together the four pieces
    df_result = df_only_in_base.append(df_overlap_use_from_base)
    df_result = df_result.append(df_overlap_use_from_ext)
    df_result = df_result.append(df_only_in_ext)

    # print(df_result)

    result_csv_bytes = df_result.to_csv().encode("utf-8")

    sys.stdout.buffer.write(result_csv_bytes)


def parse_files_and_check_sanity(args):

    log.info("read: %s", args.path_base)
    df_base = pd.read_csv(args.path_base, index_col=["time_iso8601"])

    log.info("read: %s", args.path_extension)
    df_ext = pd.read_csv(args.path_extension, index_col=["time_iso8601"])

    columns_diff = set(df_base.columns) - set(df_ext.columns)
    if columns_diff:
        log.error("these columns do not appear in both: %s", columns_diff)
        sys.exit(1)

    log.info("good: set of columns: equal")

    assert df_base.index.is_monotonic_increasing
    assert df_ext.index.is_monotonic_increasing
    log.info("good: both time series have monotonically increasing indices")

    if df_base.index.max() == df_ext.index.max():
        log.info(
            "exit early: the newest data point is equal in both data sets, emit base"
        )
        with open(args.path_base, "rb") as fin:
            sys.stdout.buffer.write(fin.read())
        sys.exit(0)

    log.info("good: newest timestamp differs across base and extension")

    if df_base.index.max() > df_ext.index.max():
        log.error("base contains newest data point")
        sys.exit(1)
    log.info("good: extension contains newest data point")

    if not df_ext.index.min() in df_base.index:
        log.error(
            "timestamp of first data point in extension is not in base (data set do not overlap or use different timestamps)"
        )
        sys.exit(1)

    log.info("good: first timestamp in extension data set also in base data set")

    if df_base.index.min() == df_ext.index.min():
        log.info("ok (special case): data sets start at the same time")
    else:
        log.info("data sets do not start at the same time")
        if df_base.index.min() < df_ext.index.min():
            log.info(
                "ok (common case): first data point in extension is newer than first data point in base"
            )

        else:
            log.error(
                "first data point in extension is older than first data point in base"
            )
            sys.exit(1)

    return df_base, df_ext


def parse_args():

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Merges two CSV data sets: base and extension",
        epilog="ETADGET, yo (TODO).",
    )

    parser.add_argument(
        "path_base",
        type=str,
        metavar="PATH_CSV_BASE",
        help="path to base data set (CSV file)",
    )

    parser.add_argument(
        "path_extension",
        type=str,
        metavar="PATH_CSV_EXT",
        help="path to extension data set (CSV file)",
    )

    parser.add_argument(
        "--threshold",
        type=float,
        help="Use extension rows from the earliest row onwards that "
        + "contains a value that differs from the corresponding base value "
        + "by more than this threshold (ignore extension rows before that: use "
        + "the ones from the base data set.",
    )

    parser.add_argument(
        "--ignore-column",
        type=str,
        action="append",
        help="When looking for the earliest point in time to use from the "
        + "extension data set, ignore this column. Takes precedence over "
        + "the allowlist pattern.",
    )

    parser.add_argument(
        "--column-allowlist-pattern",
        type=str,
        help="When looking for the earliest point in time to use from the "
        + "extension data set, ignore all columns but those that have a "
        + "name that match this pattern. "
        + "See https://docs.python.org/3/library/fnmatch.html for pattern "
        + "documentation. The method fnmatchcase() is used.",
    )

    args = parser.parse_args()

    return args


if __name__ == "__main__":
    main()