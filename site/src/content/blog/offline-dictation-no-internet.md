---
title: Offline dictation, or how to talk to your computer on a plane
description: Most voice-to-text stops working without Wi-Fi. Here is why, and what has to be true for dictation to keep going when the connection does not.
date: '2026-07-28'
---

Open a laptop at 30,000 feet, put the cursor in a document, and start dictating. With most voice-to-text tools, nothing happens. The microphone light may come on. The words never arrive.

This is not a bug. It is the architecture showing through.

## Why most dictation needs a connection

Speech recognition is expensive to run. The straightforward way to build a dictation product is to record a few seconds of audio on the device, send it to a server, run a large model there, and send the text back. The model can be enormous because it lives in a data centre. The device only has to record and wait.

That design has real advantages — and one unavoidable consequence. If the round trip cannot happen, there is no text. No Wi-Fi, hotel captive portal, spotty train signal, VPN blocking the endpoint, the vendor having a bad afternoon: same result. The feature is not slow, it is absent.

It also means every sentence you dictate is, at some point, audio on someone else's computer. What happens to it there is a policy question, and policies change.

## What offline dictation actually requires

"Works offline" is easy to put on a landing page and harder to build, because four separate things all have to be local:

**The recognizer has to be on the device.** Not a small fallback model for emergencies — the actual model doing the actual work, resident on your disk, loaded into memory.

**The models have to already be downloaded.** A tool that streams weights on first use is online-only wearing a disguise. Models need to be fetched during installation and resolved from local paths afterwards.

**Cleanup has to be local too.** Raw speech recognition output is messy: no punctuation, filler words, false starts. Many tools tidy this with a second, cloud-hosted language model. If recognition is local but cleanup is not, the feature still breaks on a plane — it just breaks later in the pipeline.

**Nothing may block on the network.** A single "check for updates" or telemetry call on the hot path, waiting on a timeout, is enough to make dictation feel broken even though the recognition itself would have worked.

Miss any one of those and you get a tool that is *mostly* offline, which in practice means a tool that fails at the worst moment.

## The tradeoff nobody mentions

Local models are smaller than cloud models. That is a real constraint and it would be dishonest to pretend otherwise.

What is less obvious is how much of the perceived quality gap comes from the model versus from everything around it. A smaller recognizer with good audio handling, sensible prompting from your own vocabulary, and cleanup that refuses to invent words can beat a larger one that has been left to guess. Quiet speech normalized before recognition, a glossary of the names you actually use, recognition that starts while you are still talking rather than after you stop — none of that needs a data centre.

There is also a latency argument that runs the other way. A local model does not pay for a network round trip. When the model is warm in memory, the gap between releasing the key and seeing text is dominated by compute you control, not by a connection you do not.

## How to tell before you install

You can test this in about a minute, and it is worth doing:

1. Install the tool and dictate one sentence so it is warmed up.
2. Turn off Wi-Fi. Fully off, not "forget this network".
3. Dictate again.

If text appears, it is local. If nothing appears, or you get an error, or it silently falls back to something noticeably worse, you now know what you have.

Do the same test with your VPN on, and again on a captive-portal network like a hotel or airport. Those catch tools that technically work offline but break on a network that exists yet does not route.

## Where Whisper Face sits

Whisper Face runs the whole path on your machine: recognition, cleanup, and insertion. Models are downloaded during installation and resolved from local paths afterwards. The optional language model that tidies grammar runs locally too. There is no account, so there is nothing to sign in to when the connection drops.

You can run the aeroplane test on it yourself, and we would rather you did than take our word for it.

It is also worth saying what offline does *not* mean. Local is not the same as anonymous — a local file is still a file on a machine someone could get access to. That is why the private files Whisper Face writes are kept to a documented list, stored with restrictive permissions, and individually inspectable and deletable. Offline is one property. Handling what stays behind carefully is a different one, and both have to be true.

Read more about [where your voice goes](/blog/your-voice-stays-local), or [get started](/docs/getting-started).
