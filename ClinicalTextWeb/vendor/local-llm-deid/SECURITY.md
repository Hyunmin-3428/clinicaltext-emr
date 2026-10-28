# Security and sensitive-data reporting

The current `1.0.x` line receives security fixes.

Do not include clinical text, identifiers, raw model responses, credentials, or
other sensitive material in a public GitHub issue, pull request, discussion,
test fixture, screenshot, or log. Use GitHub private vulnerability reporting
when it is enabled for this repository. Otherwise, contact the maintainers
through an institutionally approved private channel and provide only the
minimum synthetic reproduction needed to describe the issue.

For real clinical data, follow the governing IRB and institutional security
requirements. The software's local-first design and safe output defaults do not
make an unapproved environment suitable for PHI.
