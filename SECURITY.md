# Security policy

This is a local-first desktop application and is not designed as a public web service. Keep it bound to loopback and do not forward or expose its port.

## Reporting

Until a project contact is published, report vulnerabilities privately to the repository owner rather than opening a public issue containing exploit details or meeting data.

## Sensitive data

Never include real recordings, transcripts, generated meeting analysis, credentials, cookies, private model files, or raw logs in bug reports, commits, CI artifacts, or public issue trackers. Use synthetic examples and redact paths/usernames.

## Supported security assumptions

- Single local user on a trusted workstation.
- Local Ollama endpoints only (`127.0.0.1`, ports `11434` and `11435`).
- Local disk protected by the operating-system user account.
- Not hardened for untrusted browser extensions, shared-host multi-user deployment, or network exposure.

If any assumption changes, perform a security review before release.
