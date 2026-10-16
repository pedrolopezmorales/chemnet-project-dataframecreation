"""Command-line entry point for ChemNet dataframe refreshes."""

import argparse
import os
from pathlib import Path

from .pipeline import (
    DEFAULT_FILTERED_INPUT,
    create_affiliation_classifications,
    create_company_assoc_dataframe,
    create_company_classifications,
    create_filtered_main_dataframe,
    create_main_dataframe,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and clean ChemNet dataframes.")
    parser.add_argument(
        "operation",
        choices=["main", "filtered", "affiliations", "companies", "company-associations"],
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("CHEMNET_DATA_DIR", Path.cwd() / "data")),
    )
    parser.add_argument("--input", help="CSV path or URL for the filtered operation")
    parser.add_argument(
        "--project-root",
        type=Path,
        help="ChemNet Django checkout, required by classification and association operations",
    )
    args = parser.parse_args()

    if args.project_root:
        os.environ["CHEMNET_PROJECT_ROOT"] = str(args.project_root.resolve())

    if args.operation == "main":
        create_main_dataframe(data_dir=args.data_dir)
    elif args.operation == "filtered":
        create_filtered_main_dataframe(
            all_studies_csv_url=args.input or DEFAULT_FILTERED_INPUT,
            data_dir=args.data_dir,
        )
    elif args.operation == "affiliations":
        create_affiliation_classifications(data_dir=args.data_dir)
    elif args.operation == "companies":
        create_company_classifications(data_dir=args.data_dir)
    else:
        create_company_assoc_dataframe(data_dir=args.data_dir)
