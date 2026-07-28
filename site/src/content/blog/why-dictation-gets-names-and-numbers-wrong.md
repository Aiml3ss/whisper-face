---
title: Why dictation gets names and numbers wrong
description: Speech recognition guesses. When it guesses about an invoice figure or a surname, the fix is not a better guess — it is refusing to guess at all.
date: '2026-07-28'
---

Dictate a paragraph of ordinary prose and most tools do well. Dictate an invoice number, a client's surname, or a file path, and the same tool will quietly hand you something that is nearly right.

Nearly right is the problem. "$1,450" becomes "$1,415". "Siobhán" becomes "Shivon". `src/api/handlers.py` becomes "sources API handlers dot pie". The sentence still reads fluently, which is exactly why you will not catch it.

## Recognition is a probability, not a transcription

A speech model does not hear words. It scores possibilities. Given the audio and everything it has seen before, it picks the sequence it considers most likely.

That works because language is predictable. "I'll see you next" is very probably followed by a day of the week. The model uses that to fix genuinely ambiguous audio, and most of the time it is right.

Names, numbers, and identifiers break the assumption. There is no linguistic pattern that makes 1450 more likely than 1415. A surname the model has rarely seen loses to a common word that sounds similar. A file path is not a sentence at all, so sentence-shaped priors actively work against it.

So the model does what it always does: it picks the likelier option. For ordinary words that is helpful. For a number, it is a coin flip presented with confidence.

## Cleanup makes it worse

Most dictation tools run a second pass to make raw output readable — adding punctuation, removing filler, fixing grammar. Increasingly that pass is a language model.

Language models are very good at making text sound right. That is the danger. Asked to tidy a sentence containing an unusual name, a model may "correct" it to a common one. Asked to tidy a sentence with an odd-looking number, it may round it. It is not malfunctioning; it is doing what it was asked, and it has no way to know which parts of your sentence were load-bearing.

You now have two independent chances to lose the fact, and the output reads more fluently after each one.

## The fix: decide what may not change

The alternative is to stop treating every word as equally editable.

Before cleanup runs, identify the spans that carry facts — names, numbers, currency, dates, URLs, file paths, identifiers, commands — and mark them as protected. Cleanup may then reshape the sentence around them but may not touch them.

Whisper Face goes a step further with the language model. It is not allowed to hand back a rewritten sentence. It has to return a list of specific edits, each naming the exact span of the original it applies to. Those edits are replayed against the original text, and if the result does not reconstruct what the model itself produced, the entire response is discarded and deterministic cleanup is used instead.

That sounds fussy. It exists because a model that is 99% reliable at preserving numbers is not good enough when the 1% is your bank details, and because "trust the model" and "verify the model" are different engineering positions.

## Getting the recognizer to help

Protection prevents corruption. It does not, on its own, make the first guess better. Two things do:

**Give it your vocabulary.** Recognizers accept a prompt that biases them toward expected terms. Names of colleagues, product names, jargon your field uses and nobody else does — supplied up front, these stop losing to common words that merely sound similar.

**Let corrections stick.** When you fix a word, that is a signal. In Whisper Face a correction becomes a scoped local rule — but only if it passes a regression suite of your own past corrections with zero regressions, and only for the app you were in. Corrections that would fix one thing and break two are rejected. Contradicting evidence later demotes a rule. Every rule is inspectable and removable.

## What to check in any dictation tool

- Dictate a long number and a real surname. Do they survive?
- Dictate a file path. Does it come back as a path or as prose?
- Fix a word it got wrong. Does it learn, and can you see and undo what it learned?
- If it uses a language model to clean up, can it show you what that model changed?

The last one matters most. A tool that cannot tell you what was altered is asking you to proofread every sentence, which is most of the time dictation was supposed to save.

Whisper Face keeps a per-result view of which anchors were protected, which proposed edits were accepted or rejected, and whether surrounding context influenced the outcome — without storing a transcript of what you said.

[Get started](/docs/getting-started), or read about [how corrections are learned](/blog/your-voice-stays-local).
