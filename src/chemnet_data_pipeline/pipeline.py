"""ChemNet dataframe generation and cleanup operations.

The core CSV builders only require pandas. Classification and association
builders lazily connect to the Django project's existing domain helpers.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_FILTERED_INPUT = "https://ucsf.box.com/shared/static/9opirilxmi2d1spger8geg785810743z.csv"
PLACEHOLDER_CHEMICALS = {"graphical abstract", "no chemicals found"}
MAIN_TEXT_COLUMNS = [
    "Title",
    "Authors",
    "Affiliations",
    "Funding Sources",
    "Chemicals with InChIKey",
    "Chemicals Mentioned",
]
FILTERED_TEXT_COLUMNS = MAIN_TEXT_COLUMNS + ["Abstract"]


def _data_directory(data_dir: str | Path | None = None) -> Path:
    configured_dir = data_dir or os.environ.get("CHEMNET_DATA_DIR")
    return Path(configured_dir) if configured_dir else Path.cwd() / "data"


def _network_helpers():
    project_root = os.environ.get("CHEMNET_PROJECT_ROOT")
    if project_root:
        root = str(Path(project_root).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chemnet_site.settings")
    os.environ.setdefault("CHEMNET_DATA_PIPELINE_SKIP_STARTUP_TABLES", "1")
    try:
        import django

        django.setup()
        return importlib.import_module("networkviewer.network_functions")
    except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
        raise RuntimeError(
            "This operation needs the ChemNet Django project. Set "
            "CHEMNET_PROJECT_ROOT to its checkout and install its requirements."
        ) from exc


def _classification_path(data_dir: str | Path | None, filename: str) -> Path:
    return _data_directory(data_dir) / filename


def load_affiliation_classifications(data_dir: str | Path | None = None) -> dict[str, Any]:
    path = _classification_path(data_dir, "affiliation_classifications.json")
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as source:
            data = json.load(source)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error loading classifications from {path}: {exc}")
        return {}


def save_affiliation_classifications(
    classification_dict: dict[str, Any], data_dir: str | Path | None = None
) -> None:
    path = _classification_path(data_dir, "affiliation_classifications.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as destination:
        json.dump(classification_dict, destination, indent=2, ensure_ascii=False)


def create_affiliation_classifications(
    force_reclassify: bool = False, data_dir: str | Path | None = None
) -> dict[str, Any]:
    """Build or update affiliation classifications using the app's classifier."""
    helpers = _network_helpers()
    classification_dict = load_affiliation_classifications(data_dir)
    affiliations = (
        helpers.main["Affiliations"]
        .dropna()
        .astype(str)
        .apply(lambda text: [item.strip() for item in text.split("|") if item.strip()])
    )
    affiliations = sorted({affiliation for row in affiliations for affiliation in row})

    pending = affiliations if force_reclassify else [
        affiliation for affiliation in affiliations if affiliation not in classification_dict
    ]
    if not pending:
        print("All affiliations already classified")
        return classification_dict

    for index, affiliation in enumerate(pending, start=1):
        classification_dict[affiliation] = helpers.categorize_affiliation_text(affiliation)
        if index % 100 == 0 or index == len(pending):
            print(f"Classified {index}/{len(pending)} affiliations ({index / len(pending):.1%})")

    save_affiliation_classifications(classification_dict, data_dir)
    print(f"Saved {len(classification_dict)} classifications")
    return classification_dict


def create_company_classifications(
    force_reclassify: bool = False, data_dir: str | Path | None = None
) -> dict[str, Any]:
    """Build or update company classifications using the app's classifier."""
    helpers = _network_helpers()
    path = _classification_path(data_dir, "company_classifications.json")
    try:
        with path.open("r", encoding="utf-8") as source:
            classifications = json.load(source)
        if not isinstance(classifications, dict):
            classifications = {}
    except FileNotFoundError:
        classifications = {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error loading classifications from {path}: {exc}")
        classifications = {}

    companies = sorted(
        {company.strip() for company in helpers.no_dup_comp if isinstance(company, str) and company.strip()}
    )
    pending = companies if force_reclassify else [
        company for company in companies if company not in classifications
    ]
    if not pending:
        print("All companies already classified")
        return classifications

    for index, company in enumerate(pending, start=1):
        classifications[company] = helpers.categorize_funding_source(company)
        if index % 50 == 0 or index == len(pending):
            print(f"Classified {index}/{len(pending)} companies ({index / len(pending):.1%})")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as destination:
        json.dump(classifications, destination, indent=2, ensure_ascii=False)
    print(f"Saved {len(classifications)} classifications")
    return classifications


def create_main_dataframe(
    data_dir: str | Path | None = None,
    data_years: range = range(2002, 2025),
) -> pd.DataFrame:
    """Combine yearly source CSVs, normalize text, and write the display CSV."""
    data_path = _data_directory(data_dir)
    frames = []
    missing_years = []

    for year in data_years:
        filename = f"esandt_papers_{year}_with_inchikeys_affil_fixed.csv"
        file_path = data_path / filename
        if not file_path.exists():
            missing_years.append(year)
            print(f"Skipping missing dataset file for year {year}: {filename}")
            continue
        frames.append(pd.read_csv(file_path))

    if not frames:
        raise FileNotFoundError(
            "No yearly dataset files were found. Expected files like "
            "esandt_papers_2002_with_inchikeys_affil_fixed.csv."
        )

    combined = pd.concat(frames, ignore_index=True)
    combined = normalize_dataframe_text_columns(combined, MAIN_TEXT_COLUMNS)
    display_df = format_dataframe_for_display(combined, MAIN_TEXT_COLUMNS)
    data_path.mkdir(parents=True, exist_ok=True)
    display_df.to_csv(data_path / "esandt_papers_all.csv", index=False)
    print(f"Combined dataset created! Total rows: {len(combined)}")
    if missing_years:
        print(f"Skipped missing year files: {missing_years}")
    return combined


def normalize_dataframe_string_value(value: Any) -> Any:
    if pd.isna(value) or value is None or not isinstance(value, str):
        return value
    cleaned = re.sub(r"\s+", " ", value.strip())
    return cleaned.casefold() if cleaned else cleaned


def normalize_dataframe_text_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = df[column].map(normalize_dataframe_string_value)
    return df


def display_case_string(value: Any) -> Any:
    if pd.isna(value) or value is None or not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value.strip())
    if not text:
        return text
    return " ".join(
        token if token.isupper() and len(token) <= 3 else token.capitalize()
        for token in text.split()
    )


def format_dataframe_for_display(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    display_df = df.copy()
    for column in columns:
        if column in display_df.columns:
            display_df[column] = display_df[column].map(display_case_string)
    return display_df


def clean_chemicals_cell(value: Any) -> str:
    if pd.isna(value) or value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    cleaned = []
    for token in text.split(";"):
        token_clean = token.strip()
        if not token_clean:
            continue
        token_for_match = re.sub(r"\([^)]*\)", "", token_clean).strip().lower()
        if token_for_match not in PLACEHOLDER_CHEMICALS:
            cleaned.append(token_clean)
    return "; ".join(cleaned)


def create_filtered_main_dataframe(
    all_studies_csv_url: str = DEFAULT_FILTERED_INPUT,
    data_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Filter the study source and write the normalized filtered dataset."""
    all_studies = pd.read_csv(all_studies_csv_url)
    if "DOI" not in all_studies.columns:
        raise KeyError("Input dataframe does not contain a 'DOI' column")
    if "Chemicals with InChIKey" not in all_studies.columns:
        raise KeyError("Input dataframe does not contain a 'Chemicals with InChIKey' column")

    if "Year" in all_studies.columns:
        year_values = pd.to_numeric(all_studies["Year"], errors="coerce")
        exempt_years = year_values.isin([2012, 2013, 2014])
    else:
        exempt_years = pd.Series(False, index=all_studies.index)

    doi_matches = all_studies["DOI"].astype(str).str.contains(
        "acs.est.", case=False, na=False, regex=False
    )
    all_studies = all_studies[doi_matches | exempt_years].reset_index(drop=True)
    all_studies["Chemicals with InChIKey"] = all_studies["Chemicals with InChIKey"].apply(
        clean_chemicals_cell
    )
    all_studies = normalize_dataframe_text_columns(all_studies, FILTERED_TEXT_COLUMNS)
    all_studies = all_studies[
        all_studies["Chemicals with InChIKey"].str.strip() != ""
    ].reset_index(drop=True)

    display_df = format_dataframe_for_display(all_studies, FILTERED_TEXT_COLUMNS)
    data_path = _data_directory(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    output_path = data_path / "esandt_papers_filtered_all.csv"
    display_df.to_csv(output_path, index=False)
    print(f"Filtered main dataframe saved to {output_path}")
    print(f"Total rows: {len(all_studies)}")
    return all_studies


_affiliation_classification_dict: dict[str, Any] = {}


def categorize_collaborator_affiliation(affiliation: Any) -> str:
    if not isinstance(affiliation, str) or not affiliation.strip():
        return "Unknown"
    key = affiliation.strip()
    if key not in _affiliation_classification_dict:
        helpers = _network_helpers()
        _affiliation_classification_dict[key] = helpers.categorize_affiliation_text(key)
    return _affiliation_classification_dict[key]


def create_company_assoc_dataframe(data_dir: str | Path | None = None) -> pd.DataFrame:
    """Create the company association table using the app's matching utilities."""
    helpers = _network_helpers()
    comparing_companies = helpers.main.drop(
        ["DOI", "URL", "Year", "Title", "Chemicals Mentioned", "Abstract"], axis=1
    )
    comparing_companies["Matched Companies"] = helpers.match_items_against_master(
        comparing_companies, "Funding Sources", helpers.no_dup_comp
    )
    comparing_companies["Matched Chemicals"] = comparing_companies[
        "Chemicals with InChIKey"
    ].str.split(";").apply(lambda values: [value.strip() for value in values])
    comparing_companies["Matched Affiliations"] = helpers.match_items_against_master_aff(
        comparing_companies, "Affiliations", helpers.new_no_dup_aff
    )
    comparing_companies["Researchers"] = comparing_companies["Authors"].apply(
        helpers.split_researchers
    )
    comparing_companies["Aff"] = comparing_companies["Affiliations"].apply(
        lambda value: [item.strip() for item in value.split("|")]
        if isinstance(value, str) and value.strip()
        else []
    )
    comparing_companies = comparing_companies.drop(
        ["Affiliations", "Funding Sources", "Chemicals with InChIKey", "Authors"], axis=1
    )

    match_chem = []
    for companies, chemicals in comparing_companies[
        ["Matched Companies", "Matched Chemicals"]
    ].itertuples(index=False, name=None):
        for company in companies:
            for chemical in chemicals:
                match_chem.append({"Company": company, "Chemical": chemical})
    chemicals_per_company = (
        pd.DataFrame(match_chem)
        .groupby("Company")["Chemical"]
        .agg(lambda values: list(dict.fromkeys(values)))
        .reset_index()
        .rename(columns={"Chemical": "Chemicals"})
    )

    matched_affiliations = []
    for companies, affiliations in comparing_companies[
        ["Matched Companies", "Matched Affiliations"]
    ].itertuples(index=False, name=None):
        for company in companies:
            for affiliation in affiliations:
                matched_affiliations.append({"Company": company, "Affiliations": affiliation})
    aff_per_company = (
        pd.DataFrame(matched_affiliations)
        .groupby("Company")["Affiliations"]
        .agg(lambda values: list(dict.fromkeys(values)))
        .reset_index()
    )

    comparing_companies["Names"] = comparing_companies["Researchers"].apply(
        lambda names: [helpers.normalize_name(name) for name in names]
    )
    comparing_companies["ResearcherAffPairs"] = comparing_companies.apply(
        helpers.create_researcher_affiliation_pairs_components, axis=1
    )
    researcher_rows = comparing_companies.explode("ResearcherAffPairs")
    researcher_rows[["Researcher", "Aff"]] = pd.DataFrame(
        researcher_rows["ResearcherAffPairs"].tolist(), index=researcher_rows.index
    )
    researcher_rows = researcher_rows.explode("Matched Companies").rename(
        columns={"Matched Companies": "Company"}
    )
    researcher_rows = researcher_rows[["Company", "Researcher", "Aff"]].reset_index(drop=True)

    def aggregate_researchers(rows: pd.DataFrame) -> pd.DataFrame:
        grouped = (
            rows.groupby("Company")
            .agg({"Researcher": list, "Aff": list})
            .reset_index()
            .rename(columns={"Researcher": "Researchers", "Aff": "Affs"})
        )

        def researcher_details(row: pd.Series) -> list[dict[str, str]]:
            details = []
            seen = set()
            for name, affiliation in zip(row.get("Researchers", []), row.get("Affs", [])):
                researcher_name = str(name).strip() if isinstance(name, str) else ""
                affiliation_text = str(affiliation).strip() if isinstance(affiliation, str) else ""
                if not researcher_name:
                    continue
                key = (helpers.normalize_name(researcher_name), affiliation_text)
                if key in seen:
                    continue
                seen.add(key)
                details.append({
                    "Researcher": researcher_name,
                    "Affiliation": affiliation_text,
                    "Category": categorize_collaborator_affiliation(affiliation_text),
                })
            return details

        grouped["ResearcherDetails"] = grouped.apply(researcher_details, axis=1)
        return grouped

    researcher_rows["Researcher"] = researcher_rows["Researcher"].apply(
        helpers.normalize_comma_name
    )
    researchers_per_company = aggregate_researchers(researcher_rows)
    company_assoc = pd.merge(aff_per_company, chemicals_per_company, on="Company")
    company_assoc = pd.merge(company_assoc, researchers_per_company, on="Company")
    company_assoc["Universities"] = company_assoc["Affiliations"].apply(
        lambda affiliations: helpers.extract_uni_affil(affiliations, helpers.university_keys)
    )
    company_assoc["Countries"] = company_assoc["Affiliations"].apply(
        helpers.extract_country_list
    )

    data_path = _data_directory(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    output_path = data_path / "comparing_fundingsources.csv"
    company_assoc.to_csv(output_path, index=False)
    print(f"Saved comparing_fundingsources.csv ({len(company_assoc)} rows)")
    return company_assoc


FUNDING_TABLE_CATEGORIES = ("all", "government", "university", "foundation", "company", "unknown")


def _normalize_funding_table_category(category: str | None) -> str:
    normalized = str(category or "all").strip().lower()
    return normalized if normalized in FUNDING_TABLE_CATEGORIES else "all"


def _is_placeholder_funding_source(value: Any) -> bool:
    return str(value or "").strip().lower() in {"not found", "not found."}


def _compute_funding_source_counts(helpers: Any) -> dict[str, int]:
    if "Funding Sources" not in helpers.main.columns:
        return {}
    counts = (
        helpers.main["Funding Sources"]
        .dropna()
        .astype(str)
        .str.split(r"[;]")
        .explode()
        .astype(str)
        .str.strip()
    )
    counts = counts[(counts != "") & ~counts.map(_is_placeholder_funding_source)]
    return counts.value_counts().to_dict()


def create_funding_source_dataframe(
    chem_limit: int = 5,
    top_n: int | None = 50,
    category: str = "all",
    counts_override: dict[str, int] | None = None,
    auto_build_category_csvs: bool = True,
    data_dir: str | Path | None = None,
    helpers: Any = None,
) -> pd.DataFrame:
    """Build one funding-source table using the existing ChemNet domain helpers."""
    helpers = helpers or _network_helpers()
    category_key = _normalize_funding_table_category(category)
    company_assoc_names = set(helpers.company_assoc["Company"].dropna().astype(str).str.strip())
    source_counts = counts_override
    if source_counts is None:
        source_counts = helpers.company_counts or _compute_funding_source_counts(helpers)

    sorted_companies = sorted(source_counts.items(), key=lambda item: item[1], reverse=True)
    top_companies = []
    for name, count in sorted_companies:
        if _is_placeholder_funding_source(name) or name not in company_assoc_names:
            continue
        if category_key != "all":
            classification = str(helpers.company_classification_dict.get(name, "Unknown")).strip().lower()
            if (classification or "unknown") != category_key:
                continue
        top_companies.append((name, count))
        if top_n is not None and len(top_companies) >= max(1, int(top_n)):
            break

    rows = []
    for index, (company, study_count) in enumerate(top_companies, start=1):
        print(f"Processing {index}/{len(top_companies)}: {company}")
        try:
            classification = helpers.company_classification_dict.get(company, "Unknown")
            connections = helpers.show_company_connections(company)
            chemicals = connections.get("Chemicals", []) if isinstance(connections, dict) else []
            chemicals = sorted(chemicals or [], key=helpers.count_key, reverse=True)
            wiki = helpers.get_wikipedia_description_fundingsource(company)
            rows.append({
                "company": company,
                "study_count": int(study_count),
                "classification": classification,
                "top_chemicals": ";".join(chemicals[:chem_limit]),
                "description": wiki.get("description", "") if isinstance(wiki, dict) else "",
                "wiki_url": wiki.get("url", "") if isinstance(wiki, dict) else "",
            })
        except Exception as exc:
            print(f"Error processing {company}: {exc}")
            rows.append({
                "company": company,
                "study_count": int(study_count),
                "classification": helpers.company_classification_dict.get(company, "Unknown"),
                "top_chemicals": "",
                "description": "",
                "wiki_url": "",
            })

    result = pd.DataFrame(rows)
    if auto_build_category_csvs and category_key == "all" and counts_override is None:
        create_funding_source_category_dataframes(
            top_n=top_n,
            source_counts=source_counts,
            data_dir=data_dir,
            helpers=helpers,
        )
    return result


def create_funding_source_category_dataframes(
    top_n: int | None = 50,
    source_counts: dict[str, int] | None = None,
    data_dir: str | Path | None = None,
    helpers: Any = None,
) -> dict[str, str]:
    """Create all funding-source category CSVs from raw study-level counts."""
    helpers = helpers or _network_helpers()
    source_counts = source_counts if source_counts is not None else _compute_funding_source_counts(helpers)
    output_dir = Path(data_dir) if data_dir is not None else Path(helpers.settings.BASE_DIR) / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    created_files = {}
    for category in FUNDING_TABLE_CATEGORIES:
        rows = create_funding_source_dataframe(
            top_n=top_n,
            category=category,
            counts_override=source_counts,
            auto_build_category_csvs=False,
            data_dir=output_dir,
            helpers=helpers,
        )
        output_path = output_dir / f"funding_source_table_{category}.csv"
        rows.to_csv(output_path, index=False)
        created_files[category] = str(output_path)
    return created_files
