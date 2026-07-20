# Demo script

Four journeys. Run them in order — journey 3 depends on 1 and 2 having happened.

Before starting: `./scripts/verify.sh` must pass and `check-secrets.sh` must
report `SECRET GATE: READY`.

Reset between full rehearsals with `./scripts/reset.sh ~/.stead-demo/stead.sqlite`.

---

## 0. First contact

Send `/start` or "hello".

**Expect:** Stead introduces itself as an early private preview, says it learns
what she explicitly shares, maintains plans, schedules follow-ups and reminds
her, and that it won't make external changes without asking. Then it invites her
to talk — it does not interrogate her.

**Watch for:** any mention of Hermes, profiles, MCP or SQLite. That's a bug.

---

## 1. Dinner planning

> We're having six people over on Friday. Two are vegetarian. I normally shop on
> Thursday, and I prefer reminders the evening before.

**Expect:**
- At most one or two clarifying questions — not a form.
- A goal created for the dinner.
- Preparation tasks, including shopping.
- **"Two are vegetarian" recorded against the event, not as a household diet.**
- "I shop on Thursday" and "reminders the evening before" recorded as durable
  preferences.
- Reminders *proposed* with short references, and an explicit ask to approve.

**Then approve one and reject the other.** Check `list_approved_reminders`
contains only the approved one.

**The failure to watch for:** Stead scheduling anything before you said yes, or
turning "two guests are vegetarian" into "this household is vegetarian".

---

## 2. Forwarded school message

> Year 5 museum trip next Wednesday. Consent form and £12 payment are due Friday.
> Children need a packed lunch.

**Expect:**
- It asks **which child** — the message says "children", the household may have
  several, and it should not guess.
- The trip date and the Friday deadline extracted correctly.
- Three separate tasks: consent form, £12 payment, packed lunch.
- Reminders proposed against the Friday deadline.
- Nothing recorded about other children.

**Then say:** "Consent form is signed and I've paid the £12."

**Expect:** both tasks marked complete; only the packed lunch remains
outstanding. Stead should not claim it verified the payment — it only has your
word, and that is what it should say it has.

---

## 3. Cross-session memory

**Start a genuinely new conversation** — this is the point of the journey.

> How do I prefer to handle shopping and reminders?

**Expect:** Thursday shopping, reminders the evening before. Only confirmed
facts — nothing invented, nothing it merely inferred earlier.

**Then correct it:**

> Morning reminders work better for school items.

**Expect:** the school reminder preference changes to mornings, **and the
general evening-before preference survives untouched.** Ask it to read both back
to confirm.

**The failure to watch for:** one correction flattening every reminder
preference she has.

---

## 4. Prioritised briefing

> Review everything outstanding and tell me the four things that matter most.

**Expect:**
- **At most four items.**
- Completed consent form and payment absent.
- Packed lunch and any open dinner tasks present.
- A brief reason where a priority isn't self-evident.
- No manufactured urgency.

---

## 5. Compressed-time reminder (3–5 minutes)

Ask for a reminder a few minutes out:

> Remind me in four minutes to check the oven.

**Expect:** a proposal with a reference, and a request to approve. Approve it.

Wait. The reminder should arrive once, in Telegram, at roughly the right time.

**Then complete the task and confirm no repeat arrives.**

Check the audit trail records the delivery. `due_reminders` should return empty
afterwards — and a scheduled run that finds nothing due should send nothing at
all.

---

## Limitations to tell Kerstin

- Early preview. It will get things wrong.
- It knows only what she tells it in this chat. No email, calendar, banking or
  shops are connected.
- It cannot do anything in the outside world — it can plan, remind and track.
  If it says something was paid or submitted, that is a bug; it only knows what
  she reported.
- Reminders arrive in this Telegram chat only.
- At most two proactive messages a day unless she asks for more.
- Her data lives in a local database on one VM; `reset.sh` erases it.
