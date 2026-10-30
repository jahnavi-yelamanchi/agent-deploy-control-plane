# ADR-014: Terraform validated with mock-provider plans

Context: No cloud credentials; modules must still be checked beyond syntax.
Decision: `terraform validate` + `terraform test` with `mock_provider` blocks running `command = plan`; never apply.
Alternative rejected: `plan` with fake credentials and skip flags.
Why: azurerm authenticates at plan time; mock providers need no network or identity.
Consequence: Plan values are mocked; real plan requires `make bootstrap` credentials.
