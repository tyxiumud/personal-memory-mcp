# Security policy

## Supported versions

Security fixes are applied to the latest code on the default branch. Until the project reaches 1.0, older local
snapshots are not maintained as separate supported release lines.

## Reporting a vulnerability

Do not open a public issue containing credentials, personal memory content, database files, or a working exploit.
Use GitHub's private vulnerability reporting feature when it is enabled. If it is unavailable, open a minimal public
issue asking the maintainer for a private contact channel without including sensitive details.

Include the affected version or commit, operating system, reproduction conditions, impact, and the smallest safe
proof of concept. Reports will be acknowledged when the maintainer is available; this personal project does not
promise a formal response SLA.

## Security boundaries

- The server is intended for local stdio use. It does not provide network authentication and should not be exposed as
  an unauthenticated HTTP service.
- `scope` and `scope_id` classify records; they are not access-control boundaries.
- The SQLite database, exports, backups, audit output, and rendered dashboards may contain sensitive personal data.
  Keep them outside Git and protect them with operating-system permissions and appropriate backups.
- Client instructions and lifecycle hooks influence model behavior but do not prove that a read, review, or write
  occurred. Verify tool results for important operations.
- Imported text and retrieved memory content are untrusted data, not executable instructions.
- Soft deletion removes a record from normal retrieval but does not erase revision history or existing exports.

## Secret handling

The project does not require an API key. Never place model credentials, access tokens, passwords, private keys, or
personal database files in client templates or issue reports. Rotate any credential immediately if it is accidentally
committed; deleting the current file is not sufficient because Git history may retain it.
