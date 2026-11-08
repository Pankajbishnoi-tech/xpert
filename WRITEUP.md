# Write-up

**Model:** `deepseek/deepseek-v3.2` on OpenRouter. **Dev score:** 27.0 / 56 on the parts the checker can
score (no model at all: 9.5). **Cost:** about $0.012 per 1,000 messages on dev; one run takes about 2 minutes.

## What I focused on, and why

Open loops carry 38 of the 100 points (found, closed, help), so most of the effort went there. Two
facts shaped everything else: loops are scored only on the last two weeks, and a false "done" or a
wrong assist/remind costs more than a miss. So the system reads only the scored window for loops, and
it leans towards "open" whenever there is no message showing the task was done. The second focus was
running unattended: one command, standard library only, a valid file even if every model call fails,
and a cost that stays low enough to keep most of the cost points.

## My approach

Five stages (`solution.py`), 16 model calls on dev:

1. **Organisations** (1 call). Every group chat goes in as one line (name, size, how often Arjun writes,
   three sample messages). The model returns the organisations and which group belongs to which. Seed
   organisations are always kept. A rule adds back business groups the model drops or misfiles
   ("Radiant operations": a company of its own, role helper if Arjun never writes there).
2. **Circles** (5 calls, 70 people each). Each person is a short profile: saved names, 1:1 message
   counts, a few lines they wrote and Arjun's replies, and their groups tagged with organisation. Seed
   circles override. Code enforces one spouse, and anyone saved under Arjun's surname stays in the family circles.
3. **Links and chat ties** (code only). Colleagues and partners "run" the organisation the model names
   (or the one whose groups they share most), staff "support" home, office or farmhouse. Links are kept only
   for people Arjun talks to one to one, because the answers link few people. Every 1:1 chat is tied to
   the person, and a group is tied to its organisation if it has one, otherwise to its main sender.
4. **Open loops** (8 calls plus 1 merge). Only chats with activity in the last ~15 days where a loop for
   Arjun can arise are read: his 1:1 chats, small groups, and groups he writes in. Chats are packed
   about 500 messages per call, each with earlier context and later messages that can show whether the task got closed.
   One prompt returns type, owner, the message that makes the ask concrete, status with the message that
   closed it, and the help decision. Code then drops trivial items, turns any "done" without a visible
   closing message back to "open", and one small call merges repeats of the same task.
5. **Goals** (1 call). Only Arjun's own messages are read. At most 6 goals, each needing at least two
   evidence ids that really exist (one passing remark is not a goal).

Seven models were tried on dev at the same settings: DeepSeek V3.2 scored highest (26.7 before the last round of fixes) and was
among the cheapest. Gemini 2.5/3.1 Flash-Lite scored 20.7 and 21.3, Qwen3-235B 23.0, Nemotron Ultra 25.1 (at
four times the cost).

## What it costs

Dev: 4,502 messages, 16 calls, 172k tokens in and 27k out, **$0.055 per run, or $0.012 per 1,000
messages**. Where it goes: open loops 70%, circles 17%, goals 8%, organisations 3%, the merge 2%. The
hidden set is three times larger, but loops (the biggest share) still read only two weeks, so I expect
about $0.10–0.15 per run, **roughly $0.007–0.010 per 1,000 messages**. Reasoning is switched off (it cut
a call from 198s to 10s on a reasoning model and multiplied output tokens).

## Where it's wrong

Rates are from dev. The hidden set is labelled by hand and has new names, so treat these as the centre of a range.

- **Circles: about 25% wrong** (dev: 10 of 41). The model mixes up neighbouring circles rather than
  far-apart ones: relatives not saved under his surname called friend or colleague, staff called vendor or colleague (2),
  client vs vendor vs partner (2), sibling called relative. People with few messages fall back to rules
  and are wrong more often.
- **Organisations: score about 0.3–0.4.** The main companies are found reliably (NEXUS 0.86, PostMyAd
  0.47 overlap, Radiant 0.67). Expected misses: a second product line run by the same team (Nexus DaaS),
  places with no group chat of their own (office, farmhouse) and too few or too many people attached
  to Home. On the hidden set I expect 2–3 of the real organisations to be missing.
- **Chats: about 10% wrong** (dev 0.90). Mostly 1:1 chats with colleagues that the answers tie to the
  company instead of the person, and groups with no organisation tied to the wrong main sender.
- **Goals: about half of mine will match.** The 5 dev goals are plausible aims (stock portfolio, flat interiors,
  growing the ad product, hiring an AI team, home theatre), but goals are unlabelled in dev. I expect to miss
  family, health and experience goals that are rarely stated outright, and some goals will match the right
  aim but fall under a different area than ours.
- **Open loops found: precision about 25–30%, recall about 45%.** On dev, about 30 of 121 loops match. Most
  "extras" are real tasks the answers do not list (team work in client groups, small household errands).
  Most misses are the right task tied to a different message in the same thread. Very few are tasks never found.
- **Status: about 75% right on matched loops; a false "done" on about 7%** (2 of 29 on dev). The
  usual mistake is "open" where the answers say "done" with no closing message in view.
- **Where to help: my weakest guess.** A wrong assist or remind on a "leave" costs a point, while a
  wrong "leave" costs nothing, so the system leans towards leave: any loop owned by someone else is
  left (as in the kit's example), and the prompt makes leave the choice when unsure. On dev, open loops
  get 41% remind, 37% leave and 23% assist. The mistakes I still expect: remind or assist on Arjun's own
  tasks that he would rather handle alone, mostly his wife's personal requests and staff matters.
  Maybe 1 in 4 of my remind/assist calls is one he would have left. Dev has no labels to measure it.
- **Failures outside the model:** the provider returning errors or rate limits. Each call is retried
  six times with backoff. A stage that still fails falls back to rules (organisations, circles) or
  to an empty list (loops, goals), so the file stays valid but that part scores low.
