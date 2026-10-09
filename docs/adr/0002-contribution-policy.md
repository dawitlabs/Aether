# ADR-0002: Claim contribution and review policy

Status: Accepted; implemented

Date: 2026-10-09

## Context

The supplied documents let agents propose freely, require evidence, attribute
every write to a Contributor, and leave verification to "governance policy".
They also describe a generic Contribution record wrapping every proposal type
(claims, entity updates, relationships, disputes, annotations). Phase 3 needs
only claims and disputes. The API has no authentication yet and is bound to
loopback.

## Decision

- Identity: Contributor records (`human` or `agent`) with permissions. API keys
  are random, shown once, and stored only as SHA-256 hashes; clients send
  `Authorization: Bearer <key>`. The first admin is created by a local script;
  there is no open self-registration.
- Auth scope: every write endpoint requires a key, including document upload,
  extraction, and community rebuild. Reads stay open, still loopback-only.
- Review: one approval verifies a claim. The reviewer must be a human with the
  `review` permission and must not be the claim's author. Agents may propose
  and dispute, never review.
- Model: a claim carries its own lifecycle (`proposed` → `verified` or
  `rejected`). Each accept, reject, or dispute is recorded as a Review node.
  A dispute moves a claim to `under_review` with polarity `disputed`, reusing
  existing values rather than adding a status.
- Evidence: at least one supporting reference; every excerpt must appear
  verbatim in its cited text unit.
- Reputation: per-contributor counts of accepted and rejected claims, no score.

## Consequences

- The store's blanket refusal of verified claims is replaced by this rule.
- Raising approvals to two, or letting agents review, changes only the review
  check, not the data model.
- The generic Contribution envelope is deferred until entity or relationship
  edits become proposable (Phase 4).
- Authentication alone does not make the API safe to expose: rate limiting and
  a security review remain Phase 4 work, so loopback binding stays required.
