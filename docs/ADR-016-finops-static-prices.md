# ADR-016: Static price tables with sources, Infracost when available

Context: Cost per task and cluster cost must be reported without billing access.
Decision: Token cost = usage x sourced price table; AKS-vs-EKS table from Terraform node pools x static on-demand prices; Infracost used if installed.
Alternative rejected: Infracost only; cloud billing exports.
Why: Works offline and in CI; sources and as-of date are in the file.
Consequence: Prices drift; table must be refreshed.
