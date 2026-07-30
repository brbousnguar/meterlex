# Security policy

## Supported version

Security fixes are applied to the current `main` branch.

## Reporting

Please report vulnerabilities privately through GitHub's **Security advisories**
tab instead of opening a public issue. Include reproduction steps and affected
versions, but do not attach real coding-agent logs or credentials. You should
receive an acknowledgement within seven days. Please allow a reasonable period
for investigation and remediation before public disclosure.

## Local data boundary

Meterlex reads local coding-tool logs through read-only mounts and stores
derived usage metrics in local SQLite. It is not designed for direct exposure
to the public internet: the API has no authentication and permissive local CORS.
Bind it to a trusted host/network only, and treat the database as private
because project names and local file paths may be present.

## Secrets and test data

If a credential is accidentally committed, revoke or rotate it immediately;
removing it from Git history is not sufficient. Use only synthetic data in
issues, fixtures, screenshots, and pull requests.

## Out of scope

The following are not security vulnerabilities by themselves:

- inaccurate estimates caused by changed vendor pricing or log formats;
- exposure caused by intentionally publishing the unauthenticated API;
- attacks requiring prior write access to the local session directories or
  database.
