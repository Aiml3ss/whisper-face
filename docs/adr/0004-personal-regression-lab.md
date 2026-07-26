# ADR-0004: Personal Priors must pass a private regression suite

Status: accepted

## Context

A correction that is useful in one application can be wrong elsewhere, and a
new correction can conflict with a previously learned mapping. Sequential text
replacement can also create accidental mapping chains. Persisted learned state
must not bypass current safety rules after an upgrade.

## Decision

Store only the exact heard span, preferred span, and optional application scope
for each regression case. Activate a Personal Prior after two matching
observations in one app or three globally, provided every applicable case
passes. Before activation, shadow the candidate against the entire private
suite and require at least one corrected result with zero newly incorrect
results or evaluation errors. Model, prompt, dictionary, and Personal Prior
candidates share this content-free gate; only Personal Priors currently have a
live candidate producer. The activation callback is never invoked for a
quarantined or no-gain candidate.

Apply promoted mappings in one non-recursive substitution pass. Revalidate
serialized promotions during load, quarantine conflicts, reject future schemas
safely, and cap cases, mappings, quarantine records, span sizes, and
application identifiers. Shadow receipts contain only an opaque candidate ID,
candidate kind, disposition, and bounded counts.

No audio, timestamp, or surrounding document text belongs in this state.

## Consequences

- One observation cannot disable or replace an established mapping.
- App-specific vocabulary cannot escape into unrelated applications.
- Conflicting evidence demotes and explains a previously promoted prior.
- A candidate that fixes one case but changes another incorrectly cannot
  activate.
- Models, prompts, and dictionaries can use the same promotion contract
  without giving the gate private-text persistence or model-routing authority.
- Malformed or stale persisted state cannot silently reactivate an unsafe rule.
- Personalization work and state size remain bounded on the dictation hot path.
