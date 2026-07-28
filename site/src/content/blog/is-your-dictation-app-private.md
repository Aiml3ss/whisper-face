---
title: How to tell whether a dictation app is actually private
description: A checklist you can run yourself, without taking anyone's privacy page at face value — including ours.
date: '2026-07-28'
---

Every dictation tool has a privacy page. They are mostly true and mostly beside the point, because they describe policy rather than architecture. Policy is a promise about what a company will choose to do. Architecture is what the software is able to do.

You dictate email, medical notes, client names, unreleased work, and occasionally a password you meant to type. It is worth ten minutes to check which one you are relying on.

Here is how to check, on any tool, without trusting the marketing.

## 1. Pull the plug

Install it, dictate one sentence to warm it up, then turn Wi-Fi fully off and dictate again.

If text still appears, recognition is happening on your machine. If it does not, your audio was going somewhere — and that is fine if you know it, and a problem if you assumed otherwise.

Repeat with a VPN on and on a captive-portal network. Those catch tools that work offline but break when a network exists and does not route.

## 2. Watch the traffic

You do not need special tools. On a Mac, open Activity Monitor → Network and watch the process while you dictate. Sustained upload during speech is a strong signal.

For a closer look, Little Snitch, LuLu, or `lsof -i` will show you which processes hold outbound connections and to where. A local model talking to `127.0.0.1` is fine. A steady stream to a hostname you do not recognize is worth understanding.

## 3. Find out whether an account is required

If you must sign in, your dictation is associated with an identity by construction, whatever the retention policy says. That may be a fair trade for sync across devices. It is a different product from one that cannot associate anything because it never knew who you were.

Ask the simpler question: does it work with no account at all?

## 4. Ask what is kept, and where

Local does not mean nothing is stored. It means what is stored is on your disk. That is better, and it is not nothing.

The questions worth asking:

- Is audio written to disk, ever? Buffered in memory is very different from a file in a cache directory.
- What files does it create, and are they listed anywhere you can read?
- What permissions do those files have? On a shared or managed machine this matters more than people expect.
- Can you open, inspect, and delete what it learned about you?
- If it exports diagnostics for support, what exactly is in that export?

"We do not store your voice" is a good answer. "Here is the list of files we create, here is what is in each one, and here is the button that deletes them" is a better one.

## 5. Read what the cleanup step does

This is the one people miss. A tool can run recognition locally and still send text to a cloud language model to fix grammar. Your speech never left as audio — it left as a transcript.

That is a *more* legible form of your data, not less. Ask specifically whether the tidy-up pass is local.

## 6. Check whether you can read the source

Open source does not automatically mean private. It means the claims are checkable, by you or by anyone else, instead of asserted. For a tool that hears everything you say, that is a meaningful difference in kind.

If it is open, you can also check something stronger than the README: does the codebase have tests that would *fail* if the privacy promise broke? A promise nobody tests is a promise waiting to regress.

## Applying this to Whisper Face

We would rather hand you the checklist than the conclusion, so:

Recognition and cleanup both run on your Mac — pull the plug and see. There is no account. Audio lives in memory and is never written to disk; the optional Flight Recorder buffer is RAM-only and clears on use, pause, disable, and quit. The private files it creates are documented as a list, written with restrictive permissions, and each is inspectable and deletable from the app. The support bundle is built from an allowlist and excludes transcripts, paths, usernames, hostnames, and timestamps — and it is never uploaded; you choose where it is saved.

On the last point: the repository contains tests that fail if a module on the dictation path gains the ability to reach the network, and that drive a synthetic sentence through the real pipeline and fail on any outbound connection to anything but the local model. They exist so the promise is enforced rather than merely stated.

None of that makes local the same as anonymous, and it would be dishonest to imply otherwise. A file on your machine is still a file, and anyone with access to the machine has access to it. What we can say is which properties hold, and how you can check each one for yourself.

[See what Whisper Face actually does today](/docs/comparison), or [get started](/docs/getting-started).
