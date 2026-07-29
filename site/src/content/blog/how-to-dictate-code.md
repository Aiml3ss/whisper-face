---
title: How to dictate code without fighting your editor
description: Ordinary dictation turns code into prose. What it takes to speak an identifier, a path, or a command and get the characters you meant.
date: '2026-07-28'
---

Say `handleSubmit` out loud to a normal dictation tool and you get "handle submit". Say `src/api/v2/users.py` and you get "sources API V two users dot pie". Say "pipe to grep dash i" and you get a sentence about plumbing.

This is not the recognizer failing. It is doing precisely what it was built for — turning speech into readable prose — and code is not prose.

## Why prose rules break code

Everything that makes dictation pleasant for writing is wrong for code:

- **Sentence casing.** Prose capitalizes after a full stop. Code does not, and `Handlesubmit` will not compile.
- **Spoken punctuation becomes literal punctuation.** "Dot" should become `.` in an identifier and stay the word "dot" in a sentence.
- **Filler removal.** "So, uh, then we return" — in prose, drop the fillers. In a comment being dictated verbatim, maybe not.
- **Spacing.** Prose puts spaces between words. `camelCase` and `snake_case` very much do not.
- **Autocorrect toward real words.** `usr` becomes "user", `elif` becomes "else if", `stdin` becomes "standing".

Applying prose rules to code produces something that reads fine and does not run.

## Modes, not modes-you-have-to-remember

The usual answer is a separate "code mode" you switch into. That works until you forget you are in it and dictate an email that arrives with no capital letters.

A better shape is a mode that lasts exactly one dictation. Hold a modifier along with your usual key, speak, release — that utterance is compiled as code, and the next one is not. Nothing to remember, nothing to switch back.

In Whisper Face this is one of six modes on the same gesture. The modifier chooses; the mode never sticks.

## What "compiled as code" actually has to do

**Glyph names become glyphs.** A fixed table of spoken names for ASCII characters — "open paren", "close brace", "underscore", "arrow" — resolves to the characters, and the spacing rules around them stop the result being pulled apart into prose.

Be concrete about the boundary, because it matters if you are deciding whether this is useful: Whisper Face's code mode today is that glyph table plus spacing. It does **not** convert spoken casing conventions — saying "camel case handle submit" gives you those words, not `handleSubmit`. If you want an identifier, spell the shape you want with glyph names, or type it.

**Paths and identifiers are protected where they are recognizable.** Anchor detection keys off shape: `./src/api/handlers.py` or `/src/api/handlers.py` is protected whole, and cleanup may not rewrite it. A bare `src/api/handlers.py` is not — only segments that look like identifiers on their own survive. Say the leading slash if the path matters.

**Terminals are treated differently from editors.** Text going into a terminal should be verbatim. Text going into a code comment can be tidied. Per-app tone handles that — technical in editors, verbatim in terminals — so the same spoken sentence lands appropriately in each.

## What it will not do

Dictation will not write your code. It transcribes what you say, and saying a nested generic type out loud is slower than typing it.

Where it earns its place is the surrounding volume: commit messages, code comments, pull request descriptions, issue write-ups, docstrings, the paragraph of context in a Slack thread explaining what you just did. That is a large fraction of a developer's typing and almost none of it is syntax.

For actual code, dictation works best on the shapes you say more easily than you type: a long descriptive identifier, a file path you would otherwise tab-complete through, a shell incantation with flags.

One safety note worth stating plainly. Whisper Face's command mode operates on a small allowlist of reversible editing actions. It cannot run shell commands. Dictating "delete the src directory" edits text — it does not execute anything. A voice pipeline that could run commands from a recognizer's best guess would be a bad idea, and this one deliberately cannot.

## A realistic setup

1. Put the names your codebase uses — services, models, unusual spellings — into your vocabulary, so the recognizer stops losing them to common words.
2. Set your editor and terminal tones once. Technical for the editor, verbatim for the terminal.
3. Use code mode for identifiers and paths; use ordinary dictation for comments and messages.
4. When it gets a term wrong, fix it. The same correction twice in one app, or three times overall, makes it a candidate; it becomes a scoped local rule only after passing a regression check against your own past corrections. One fix on its own will not stick, by design.

Then dictate the commit message instead of typing it, and see whether the loop is worth keeping.

[Get started](/docs/getting-started), or read [why dictation gets names and numbers wrong](/blog/why-dictation-gets-names-and-numbers-wrong).
