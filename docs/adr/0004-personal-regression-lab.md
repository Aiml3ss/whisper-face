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
passes. Apply promoted mappings in one non-recursive substitution pass.
Revalidate serialized promotions during load, quarantine conflicts, reject
future schemas safely, and cap cases, mappings, quarantine records, span sizes,
and application identifiers.

No audio, timestamp, or surrounding document text belongs in this state.

## Consequences

- One observation cannot disable or replace an established mapping.
- App-specific vocabulary cannot escape into unrelated applications.
- Conflicting evidence demotes and explains a previously promoted prior.
- Malformed or stale persisted state cannot silently reactivate an unsafe rule.
- Personalization work and state size remain bounded on the dictation hot path.
