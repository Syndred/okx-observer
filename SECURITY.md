# Security

This project is a local market screener. It does not need exchange API keys and does not place orders.

Please **do not** open public issues that include secrets, `.env` files, or personal machine paths. If you believe you found a vulnerability in the dashboard (for example log leakage of credentials you accidentally mounted), email the maintainer through GitHub or open a private security advisory.

Expected behavior:

- The dashboard binds to `127.0.0.1:8787` only.
- Scan logs are redacted for common `api_key` / `secret` / `passphrase` patterns.
- Stopping the app may quit Docker Desktop on your machine; that is intentional power-saving, not remote control.
