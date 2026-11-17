# AI Coding Harness: Capability Limits

For maintainers and auditors. It says what the harness enforces, what it can't, and
how each guardrail can fail. The question underneath everything: if someone genuinely
wanted to beat it, where could they start?

The design assumes a model that mostly follows written rules but occasionally cuts
corners, treats a run as a result, or verifies the wrong layer. For every constraint,
the real safety net is auditability after the fact.

## The point in one sentence

Evidence rests on two things: program self-assertion and a user passphrase. The gap
the harness can't close is whether what got verified is what the user saw. The best
it can do is make the model write down which layer it claims to cover, and leave
that statement unchecked.

## 1. Shell allowlist

Read commands get through; write commands need terminal approval. The allowlist is a
policy table of known write patterns. A write alias or argument outside the deny
rules slips through. Approval assumes the user is watching; in an unattended task the
user may approve everything. The allowlist can't tell apart side effects of the same
command name. `python`, `bash`, and `sh` are often allowed because the model needs
them for distribution, and those can run anything.

## 2. Only harness publish writes files

Direct Write, Edit, or apply_patch on project files is blocked; publish is the only
path. But it only blocks project files. `.harness-tmp/`, the scratch area, allows
writes and is not reconciled. The harness never looks at what publish writes; it
blocks bypassing the mechanism, not wrong output. A bad file publishes as easily as
a good one. If a hook or permission gets downgraded so the gate no longer triggers,
the constraint goes away.

## 3. Blocked from editing the .harness directory

Tampering with `state.json` or evidence locks everything down, provided the lock
isn't bypassed. A determined edit gets through. Whitelisted `python`, `cp`, and
`chmod` can rewrite `.harness/` directly. The integrity ledger uses `hash_after` in
`manifest.jsonl`: append a correct entry and a rewritten evidence file never reports.
The chain holds only if every manifest record stays untouched, and the manifest lives
in git too. A write plus an appended entry reads as intact. This is audit integrity.

## 4. Passphrase authorization

The passphrase guards only the negative operations the user approves: approve, adopt,
abort, the ones that change ownership or roll back. Ordinary verify and step done
skip it, so automation can run. Any agent holding valid evidence can move through
every gate that doesn't check for a passphrase.

Whether the passphrase is really in the terminal comes down to grepping process
names for claude, codex, cursor, and the like. That's string matching in a process
tree, and it's gameable: spoof a process name, inject arguments, spawn through MCP,
pick an agent name off the list. Anything that doesn't look like a known agent name
gets missed. The passphrase is written once to `secret.json` in the app data; direct
read as the AI is blocked, but if the process or environment gets read, it leaks.

## 5. The second commit

The four hard asserts — `--out`, `--require`, `--forbid`, `--must-differ` — prove a
command ran and the artifact matched. They don't prove the covered object is the
layer the user saw. A past model opened `/api/stats` via CDP, saw `serverMetrics`,
concluded the dashboard UI was done, while the UI component rendered nothing. That
failure recurs. It's a hole in the mechanism, not a one-off.

The fix: evidence commits in two phases. Phase 1 runs the command with the four
asserts, writes evidence, and on success prints a one-time token to the terminal, and
stores its sha256. It also asks three questions folded into `--intent`: which layer
you verified, which layer you claim passed, and whether they are the same object.
Phase 2 (`verify --commit --token <t>`) checks the token binds to the phase-1
evidence and marks it committed. `step done` then requires committed evidence or
refuses to close.

The limits:

- The system trusts the model with the token. A model that decides to cheat skips the
  step; an audit trail is left either way.
- `--intent` is never checked. A model can write "I verified the DOM" when no
  browser was ever launched. The reader can see the claim covers the UI while the
  command only curl'd an API. Whether the claim is true is left to that reader.
- Freshness means later than the last publish or adopt. If a file changes between
  phase 1 and phase 2, phase 2 just marks existing evidence committed; the command is
  never re-run, so a concurrent edit won't trigger re-verification.
- `token` is optional. `step done --token` passes with committed evidence and a wrong
  token; integrity is only enforced at `--commit --token`. The token is a bookkeeping
  tag.
- Tokens replay. Bound to one step, so not usable elsewhere, but they never expire.
  The same evidence can commit repeatedly, each time marked committed. The manifest
  works the same way: append a `verify` entry and the rewrite is legitimate. The whole
  chain rests on someone remembering to append the manifest when they edit, and
  nothing checks that.

## 6. The verified layer vs the user-visible layer

The harness has no browser, no CDP, no screen. It only runs commands. Whether a card
renders in the DOM is outside its reach. Anything a user actually sees is covered by
curl-and-grep assertions from a layer above, and nothing knows the object was meant
to be a screen rather than an API. There's no third party either: every assertion is
written by the same AI that wrote the code.

What would close this: when you claim you verified a user-visible layer, show that
layer. A screenshot, a DOM contract. The harness doesn't require that. Browser and
screenshot tooling was left out because it bloats the runtime. The defense as it
stands: mandatory two-phase commit, `--intent` recording the claimed layer, and an
audit that can line the two up. This lowers the odds of an honest mistake. It can't
do more.

## On the way out

The cheapest way to fool this: point the acceptance command at any object that pops
the answer you want, curl an API, then write that you've confirmed the layer the user
sees. The harness can't stop that. It leaves a trace anyone can audit, checked line
by line against each phase-1 command. The mismatch between the script and the truth
is exactly what a human can find.

No line of code is the backstop. The person who opens the evidence and this file,
and compares what the model claimed to verify against what it actually did, is the
final check. The harness's job is to make the gap visible.