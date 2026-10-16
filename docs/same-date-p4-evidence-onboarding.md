# Same-Date P4 evidence onboarding

## Scope and stop condition

P4 inventories local bytes before any evidence promotion. It does not call a
provider, generate a candidate registry, request approval, create a v1.1
snapshot, or create an investment decision. A provider parser may be added only
after the original provider response bytes and a hash-linked rights record are
both preserved.

The current local review found provider-derived observations, but no qualifying
provider-native response for the required Same-Date targets. Alpha Vantage CSV
files are real local observations but are normalized StockLens caches. The
Nasdaq Top10 JSON files are normalized or derived cross-project artifacts. They
must not be relabelled as provider-native evidence.

Run the read-only inventory locally:

```powershell
.\.venv-personal\Scripts\python.exe scripts\inventory_same_date_evidence.py `
  --private-data-root private_data `
  --top10-data-root "<local Top10 data directory>" `
  --inspected-at "<timezone-aware ISO timestamp>"
```

The output path is fixed to
`private_data/decision/evidence-inventory-p4.json`; the command has no output
path option. The writer rejects absolute or relative path escapes, resolved
symlink/junction escapes, and collisions with inspected inputs. The output
stays below `private_data/`, which is ignored by Git. It records the
exact current file hash, source and time metadata, evidence class, point-in-time
status, rights status, and the reason parsing or promotion remains blocked.

The tracked `config/evidence-inventory-manifest-v1.json` declares every expected
artifact and every known local Top10 exclusion. Any undeclared file is reported
as `unclassified`; a missing, malformed, unreadable, future-dated, hash-mismatched,
or unclassified artifact keeps `inventory_complete=false`.

## Evidence classes

- `normalized_only`: real observations remain, but provider bytes cannot be
  replayed byte-for-byte.
- `derived_only`: values were computed by another application from upstream
  inputs whose complete lineage is not locally replayable.
- `supporting_only`: hash-linked issuer material exists but does not satisfy a
  Same-Date price, consensus, peer-multiple, acceptance-time, regime, or rights
  role. NVIDIA is identified as official investor-relations evidence. The two
  AVGO files are identified more narrowly as issuer-release supporting evidence
  distributed by PR Newswire; independent official-issuer-origin verification
  remains false.
- `missing`: no usable local evidence was found for the target.
- `invalid_integrity`, `malformed`, and `unreadable`: bytes exist but cannot be
  accepted as a normal artifact. These states are never treated as missing or
  promoted.

No provider parser or candidate registry is implemented by P4.1. Existing
generic synthetic-fixture parsing is not evidence that an actual provider
format is supported.

## Current P4 acquisition gaps

1. Preserve provider-native price and consensus responses at collection time,
   together with capture time, provider timestamp, revision/vintage metadata,
   and the applicable rights record.
2. Obtain an independent, same-basis QQQ/SOXX/peer Forward-multiple ledger.
3. Preserve target filing acceptance timestamps or apply the documented
   conservative next-session rule; the current local SEC ledger has no AVGO or
   NVDA target record.
4. Preserve the applicable provider terms document bytes and a dated review
   record. A URL or local manifest assertion alone cannot pass the rights gate.

## QA public-key management plan

This is a plan only. No production QA key is generated or registered by P4.

- **Owner and approval authority:** an independent QA owner controls approval.
  The evidence developer cannot approve their own candidate.
- **Private-key custody:** store the key outside the repository and outside the
  developer account, in an OS-backed credential store or hardware-backed vault.
  The application and developer CI receive only the public key.
- **Algorithm:** RSA 3072-bit minimum for a production key, exponent 65537,
  SHA-256, and RSA-PSS padding. RSA 2048 is the lower acceptance boundary, not
  the preferred new-key size.
- **Fingerprint:** publish the SHA-256 fingerprint of the DER-encoded public key
  with the key ID. Pin the expected key ID and fingerprint in the reviewed
  public-key registry.
- **Validity:** every key has timezone-aware `valid_from` and `expires_at`.
  Receipts outside that interval fail closed.
- **Rotation:** create and review the replacement public key before expiry,
  allow a short documented verification overlap, and stop issuing receipts with
  the retiring key before removal.
- **Revocation and incident response:** maintain a signed or independently
  reviewed revocation record. Suspected private-key exposure immediately blocks
  the key and all receipts issued after the incident boundary.
- **Approval receipt:** QA independently recomputes candidate and source hashes,
  signs the canonical receipt, records decision time and target tickers, and
  returns only the receipt and public verification material. The developer
  cannot access or invoke the private signing key.
- **Implementation review:** before a production key is registered, replace the
  custom RSA path with a standard maintained cryptography library, or
  independently cross-check every generated and rejected receipt against one.

## Promotion order after the missing evidence exists

Inventory the new bytes, verify rights, implement only the matching actual
provider parser, replay without network access, and compare the canonical hash.
Then generate a candidate hash and stop. Independent QA must verify and sign the
candidate before a signed audit or v1.1 snapshot can be created. Decision
calculation remains downstream of that complete chain.
