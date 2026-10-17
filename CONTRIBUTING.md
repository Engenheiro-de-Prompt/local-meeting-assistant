# Contributing

Thanks for helping improve the local meeting assistant.

## Before opening a change

- Keep processing local-only. Never add an external speech/LLM API, telemetry, relay, or network bind beyond loopback without explicit design review.
- Never add meeting recordings, transcripts, analyses, personal credentials, runtime logs, cache/model files, or private evaluation fixtures to Git.
- Keep the original recorder and unrelated services out of scope.
- Add a regression test before changing behavior; test with synthetic fixtures where possible.
- Run the complete unittest suite, Python compile check, JavaScript syntax check, and shell syntax check.
- Include the exact test commands and distinguish unit tests from real device/model validation.

## Code organization

- Keep HTTP handlers thin and put validation/processing behavior in testable functions.
- Keep prompts and analysis response validation explicit; do not expose meeting content in error messages or logs.
- Configure paths and endpoints through documented environment variables. Never commit machine-specific values.
- Preserve least privilege: loopback binding, bounded uploads, private local-file permissions, and path containment checks.

A license has not yet been selected. Contributions and redistribution terms remain subject to the project owner's later license decision.
