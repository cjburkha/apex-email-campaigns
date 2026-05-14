# SMS Opt-In Compliance Process

This document describes the compliant SMS opt-in process for sending campaign messages
via **AWS End User Messaging (SMS)** from toll-free number **+1-855-612-7811**.

Source guidance:
- [How to build a compliant SMS opt-in process with AWS End User Messaging](https://aws.amazon.com/blogs/messaging-and-targeting/how-to-build-a-compliant-sms-opt-in-process-with-aws-end-user-messaging/)
- CTIA Messaging Principles & Best Practices (current edition)
- TCPA (47 U.S.C. § 227) and FCC implementing rules
- T-Mobile Code of Conduct, AT&T / Verizon carrier requirements
- Toll-Free Messaging Verification standards

> ⚠️ **No exceptions.** Even internal / B2B / transactional / one-time messages require documented
> opt-in consent. A pre-existing business relationship is **not** consent.

---

## 1. What counts as a valid opt-in

> **Explicit consent** is the intentional action taken by an end user to request a specific
> message from your service.

Valid opt-in mechanisms (any one is acceptable if implemented correctly):

| Mechanism | Example | What you must capture |
|---|---|---|
| Web form | "Yes, text me appointment reminders" checkbox (un-checked by default) | timestamp, IP, page URL, form snapshot |
| Keyword (MO) | Customer texts `JOIN` to 855-612-7811 | inbound message log, MSISDN, timestamp |
| Point-of-sale / paper | Signed estimate or job folder with SMS disclosure block | scanned signed copy, date, signer name |
| Verbal at job site | Estimator reads disclosure, customer agrees | recording **or** signed acknowledgement |
| IVR / phone | Caller presses 1 to receive texts | call recording, CTN, timestamp |

**Pre-checked boxes, bundled consent (e.g. "By submitting this form you agree to receive
calls, texts, and emails…" with no separate SMS checkbox), and "consent" obtained from
purchased lists are all invalid.** Carrier reviewers reject these.

---

## 2. Required disclosures at the point of opt-in

Every opt-in surface (web form, paper form, IVR script, in-person script) **must** display
or speak all six items below, in close proximity to the consent action:

1. **Program name / brand** — *"Windows by Burkhardt appointment & follow-up texts"*
2. **Message frequency** — *"Message frequency varies"* or *"Up to 4 msgs/month"*
3. **HELP instructions** — *"Text HELP or call 1-855-612-7811 for support"*
4. **STOP instructions** — *"Text STOP to opt out at any time"*
5. **Rate disclosure** — verbatim: *"Message and data rates may apply"*
6. **Links to Terms & Privacy** — publicly reachable (no login required) URLs to the
   SMS Terms of Service and Privacy Policy

### Reference opt-in block (web form)

```
[ ] I agree to receive recurring text messages from Windows by Burkhardt at the
    mobile number provided about my estimate, appointments, and follow-up.
    Message frequency varies. Message and data rates may apply.
    Reply HELP for help, STOP to cancel. See Terms (URL) and Privacy Policy (URL).
```

### Reference opt-in block (paper / in-person)

> By signing below I agree to receive recurring text messages from Windows by Burkhardt
> at the phone number written above regarding my estimate, scheduling, and follow-up.
> Msg frequency varies. Msg & data rates may apply. Text STOP to cancel, HELP for help.
> Terms: windowsbyburkhardt.com/sms-terms  Privacy: windowsbyburkhardt.com/privacy

---

## 3. Confirmation (welcome) message

Send **exactly one** confirmation message immediately after a valid opt-in. It must
re-state the program, frequency, HELP, STOP, and rate disclosure. Keep it ≤ 160 chars
if possible to avoid concatenation.

```
Windows by Burkhardt: You're subscribed to appointment & follow-up texts.
Msg freq varies. Msg&data rates may apply. Reply HELP for help, STOP to cancel.
```

If the recipient does **not** complete a double opt-in confirmation (where required —
see §6), do not send further messages.

---

## 4. Required keyword responses

The toll-free number **must** auto-respond to the following inbound keywords. AWS End
User Messaging supports keyword auto-responses on the originator configuration.

| Inbound keyword (case-insensitive) | Required outbound response |
|---|---|
| `STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT` | `Windows by Burkhardt: You are unsubscribed and will receive no more messages. Reply START to resubscribe.` |
| `HELP`, `INFO` | `Windows by Burkhardt support: 1-855-612-7811 / chris@windowsbyburkhardt.com. Msg&data rates may apply. Reply STOP to cancel.` |
| `START`, `UNSTOP` | (only if previously opted out) re-send the welcome message from §3 |

After a STOP response is sent, **no further messages of any kind** may go to that
number until the user explicitly re-opts in.

---

## 5. Record-keeping (what we must store)

For every subscriber, retain the following for at least **4 years** after the most
recent message (some carriers require longer; 4 yrs covers TCPA statute of limitations):

- E.164 phone number
- Opt-in method (web form / keyword / paper / IVR)
- Opt-in timestamp (UTC)
- Opt-in source artifact:
  - web form → snapshot of the form + IP address + user-agent + page URL
  - keyword → raw inbound MO message + carrier metadata
  - paper → scan of signed disclosure
  - verbal → recording or signed acknowledgement
- Exact disclosure text shown at time of opt-in (versioned)
- Confirmation message ID and delivery status
- All subsequent STOP / HELP / START events with timestamps

A subsequent re-opt-in **starts a new record** — do not overwrite prior history.

---

## 6. Special cases requiring double opt-in

A confirmation reply (e.g. user must text `YES` after the initial signup) is required
for:

- **Abandoned-cart / promotional re-engagement** (CTIA Guideline 3.16)
- **Programs with promotional + transactional mixed content**
- Any list assembled from a third-party lead source

Standard appointment / transactional reminders from a direct first-party signup do not
require double opt-in but still require everything in §1–§5.

---

## 7. Carrier registration (Toll-Free Verification)

Toll-free number **+1-855-612-7811** must be registered through AWS End User Messaging
Toll-Free Verification before high-throughput sending is allowed. The submission
package must include:

- Legal business name, address, EIN, website
- Use case description (appointment reminders, estimate follow-up, etc.)
- **Sample opt-in flow screenshot(s)** — must show the disclosures in §2
- **Sample message content** — at least 2 representative messages
- Opt-out language (verbatim from §4)
- Privacy Policy URL (must contain the SMS-specific clause in §8)
- Terms of Service URL (must contain the SMS-specific clauses in §9)

> Carrier reviewers reject submissions where the opt-in screenshot is behind a login,
> on an internal/non-public URL, or where any of the six required disclosures is
> missing. If consent is collected verbally or on paper, **upload a photo of the
> signed form or a transcript** with the registration.

---

## 8. Privacy Policy — required SMS clause

The Privacy Policy linked from the opt-in surface must contain (substantively):

> The above excludes text-messaging originator opt-in data and consent; this
> information will not be shared with any third parties for marketing or
> promotional purposes.

Carriers specifically look for this language. Sharing SMS consent data with
affiliates or partners will cause registration denial.

---

## 9. Terms of Service — required SMS clauses

At minimum, the SMS Terms of Service must cover:

1. **Program description** — who is sending, what kinds of messages
2. **Opt-out** — STOP keyword instructions and that one final confirmation will follow
3. **Help** — HELP keyword instructions and human support channel
4. **Carriers** — *"Carriers are not liable for delayed or undelivered messages."*
5. **Frequency** — expected message volume
6. **Cost** — *"Message and data rates may apply"*
7. **Eligibility / age** — typically 18+ or with parental consent
8. **Governing law / dispute resolution**

---

## 10. Implementation checklist

Before sending the first production SMS:

- [ ] Toll-free number 855-612-7811 registered & verification **approved** in AWS End User Messaging
- [ ] Web opt-in form deployed with all six §2 disclosures and an un-checked consent box
- [ ] SMS Terms of Service page live at a public URL
- [ ] Privacy Policy contains the §8 clause
- [ ] STOP / HELP / START auto-responses configured on the originator
- [ ] Welcome / confirmation message template approved
- [ ] Subscriber database captures all §5 fields
- [ ] Outbound send path checks subscriber `sms_opt_in_status` before every send and **skips** if not `subscribed`
- [ ] Internal runbook for handling complaints / carrier escalations
- [ ] Quarterly audit of opt-in records and STOP-rate (>2% STOP rate is a red flag)

---

## 11. Operator quick-reference

| Situation | Action |
|---|---|
| Lead gives phone number on a quote request form with no SMS checkbox | **Do not text.** Email or call only. |
| Lead checks the SMS box on the web form | Send welcome msg, record opt-in, set status = `subscribed` |
| Customer texts `JOIN` to 855-612-7811 | Auto-reply with welcome msg, record opt-in, status = `subscribed` |
| Customer texts `STOP` | Auto-reply STOP confirmation, status = `opted_out`, never send again |
| Customer texts `STOP` then later requests a new estimate by phone | Do **not** resume texts. Get fresh opt-in (form or `START`) first. |
| Estimator wants to text a past customer about a follow-up | Verify `sms_opt_in_status = subscribed` and that opt-in is on file |
| Carrier complaint / spam report received | Pause sending to that originator, gather opt-in record for that MSISDN, respond within 24 hrs |
