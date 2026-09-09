# Security Policy

## Supported Version

Security fixes are applied to the latest version on the default branch.

## Reporting

Do not open a public issue containing credentials, tokens, health data, or a reproducible private-data leak. Use GitHub's private vulnerability reporting when enabled on the repository.

## Immediate Response to Credential Exposure

1. Revoke or rotate the Oura OAuth application secret.
2. Revoke the affected Oura authorization.
3. Remove the secret from Git history, not only from the latest commit.
4. Review local and GitHub access logs where available.

The application is designed for localhost use. Network deployment is unsupported until authentication, transport security, and multi-user data isolation are implemented.
