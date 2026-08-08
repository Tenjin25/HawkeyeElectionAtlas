# Iowa 2024 General Precinct Notes

These 2024 precinct files were built primarily from county-level `detailxls.zip` workbooks on the Iowa statewide results site and then combined into:

- [20241105__ia__general__precinct.csv](C:/Users/Shama/OneDrive/Documents/Side_Projects/Side_Projects/IAPrecinctMap/data/2024/20241105__ia__general__precinct.csv)

After fixing contest-level validation for duplicate office titles, the only remaining known workbook-versus-summary discrepancy is in Johnson County.

## Johnson County

Contest:

`Cedar Township Township Trustee`

Johnson County's November 5, 2024 general-election page says:

- `Cedar Trustee: No candidate filed.`

That means this contest was expected to be resolved entirely through write-in votes.

State `sum.json` totals:

- `Write-In`: `28`
- `Jack Johnson`: `3`
- `Joseph Deeney`: `3`

County precinct workbook totals:

- `Write-In`: `31`
- `Jack Johnson`: `0`
- `Joseph Deeney`: `3`

Local official winner resolution:

- Johnson County's official `Township Winners (including write-in winners)` PDF lists `Jack Johnson` and `Joseph Deeney` as the winners for `Cedar Township Township Trustee`.

Sources:

- [Johnson County November 5, 2024 general election page](https://www.johnsoncountyiowa.gov/november-5-2024-general-election)
- [Johnson County Township Winners PDF](https://johnsoncountyiowa.gov/sites/default/files/Elections/2024TownshipWinners.pdf)

CSV handling:

- The precinct CSVs keep the county workbook figures for this contest.
- The county workbook is still the precinct-level source and remains internally consistent with `votes = absentee + election_day`.
- The local winner PDF resolves the winning names, but it does not provide precinct-level `absentee` and `election_day` splits for the `3` votes attributed to `Jack Johnson` in the state summary.
- Because that split is not available, the CSV preserves the workbook's original precinct split instead of reallocating those `3` votes between `Write-In` and `Jack Johnson`.
