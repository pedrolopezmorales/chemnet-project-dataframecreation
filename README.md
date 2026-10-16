# ChemNet Data Pipeline

This repository contains ChemNet's offline dataframe generation, normalization, filtering, classification, and association-table utilities. The live Django app remains responsible for serving data; this project owns the refresh/build workflow.

## Install

```powershell
python -m pip install -e .
```

## Build dataframes

Run the CLI with the data directory that contains the yearly source CSVs:

```powershell
chemnet-data-pipeline main --data-dir C:\path\to\chemnet_project\data
chemnet-data-pipeline filtered --data-dir C:\path\to\chemnet_project\data --input https://example.org/studies.csv
```

`main` combines the yearly `esandt_papers_<year>_with_inchikeys_affil_fixed.csv` inputs, normalizes text, and writes `esandt_papers_all.csv`. `filtered` applies the study/chemical filters and writes `esandt_papers_filtered_all.csv`.

Classification and company-association operations use the existing ChemNet domain matching and classification functions. Point them at the Django checkout and its data directory:

```powershell
chemnet-data-pipeline affiliations --project-root C:\path\to\chemnet_project --data-dir C:\path\to\chemnet_project\data
chemnet-data-pipeline companies --project-root C:\path\to\chemnet_project --data-dir C:\path\to\chemnet_project\data
chemnet-data-pipeline company-associations --project-root C:\path\to\chemnet_project --data-dir C:\path\to\chemnet_project\data
chemnet-data-pipeline funding-tables --project-root C:\path\to\chemnet_project --data-dir C:\path\to\chemnet_project\data
```

`funding-tables` builds the all-sources table plus the government, university, foundation, company, and unknown category CSVs. These domain-aware operations require the Django project's Python dependencies. The yearly CSV generation and filtering commands only require pandas.
