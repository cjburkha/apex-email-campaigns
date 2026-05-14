# AWS End User Messaging — Toll-Free Verification Submission Package

This document contains the exact text and artifacts to submit when registering
toll-free number **+1-855-612-7811** through AWS End User Messaging Toll-Free
Verification (Pinpoint SMS &rarr; Phone numbers &rarr; Request verification).

It is the field-by-field companion to [SMS-OPT-IN.md](SMS-OPT-IN.md).

## Prerequisites — must be live before submitting

The verification form requires public URLs. All three pages below must return HTTP 200
on the open internet (no login wall) and contain the required disclosures:

| URL | Page in windows-by-burkhardt repo |
|---|---|
| https://windowsbyburkhardt.com/sms-signup | [public/sms-signup.html](../windows-by-burkhardt/public/sms-signup.html) |
| https://windowsbyburkhardt.com/sms-terms | [public/sms-terms.html](../windows-by-burkhardt/public/sms-terms.html) |
| https://windowsbyburkhardt.com/privacy | [public/privacy.html](../windows-by-burkhardt/public/privacy.html) |

Once those are deployed, take **PNG screenshots** of each one (full-page, desktop
width) and have them ready to upload.

---

## Form field values

### Company information

| Field | Value |
|---|---|
| Company name | Windows by Burkhardt |
| Company website | https://windowsbyburkhardt.com |
| Address | *(fill in business address)* |
| EIN / Tax ID | *(fill in)* |
| Contact name | Chris Burkhardt |
| Contact email | chris@windowsbyburkhardt.com |
| Contact phone | 1-855-612-7811 |

### Use case

| Field | Value |
|---|---|
| Vertical | Home services / home improvement |
| Use case type | Mixed (account notifications + customer care follow-up) |
| Estimated monthly volume | *(e.g. 500 messages/month — fill in your actual estimate)* |
| Estimated subscriber count at peak | *(e.g. 300)* |

### Opt-in workflow description

Paste the following into the verification form's "Describe how end users opt in"
field. Adjust phrasing to match the live page if it changes.

> Windows by Burkhardt obtains explicit, written consent from each subscriber before
> sending any SMS message. Consent is collected through two channels:
>
> **(1) Web opt-in.** Homeowners visit our public signup page at
> https://windowsbyburkhardt.com/sms-signup, enter their name and mobile phone
> number, and check an un-checked consent box that reads: "Yes, I agree to receive
> recurring text messages from Windows by Burkhardt at the mobile number above
> regarding my estimate, appointments, and follow-up. Message frequency varies.
> Message and data rates may apply. Reply HELP for help, STOP to cancel. See our
> SMS Terms of Service and Privacy Policy." The page displays all required
> disclosures inline: program name, message frequency, HELP instructions, STOP
> instructions, "Message and data rates may apply," and links to our SMS Terms of
> Service and Privacy Policy. Consent is never bundled with email or phone-call
> consent, and the SMS consent box is never pre-checked.
>
> **(2) Mobile-originated keyword.** Customers may also opt in by texting the
> keyword JOIN to 1-855-612-7811 from their mobile phone, after seeing the same
> disclosures on printed materials, business cards, or in person at the time of a
> consultation.
>
> Upon receiving a valid opt-in through either channel, the system sends a single
> confirmation message that re-states the program name, message frequency, HELP and
> STOP instructions, and the "Message and data rates may apply" disclosure.
>
> For every subscriber we retain a record of the opt-in timestamp, the channel
> (web or keyword), the exact disclosure text shown, the source page URL or
> originating MSISDN, and the IP address and user agent (for web opt-ins). Records
> are retained for a minimum of four years.

### Opt-out workflow description

> Subscribers may opt out at any time by replying STOP, STOPALL, UNSUBSCRIBE,
> CANCEL, END, or QUIT to 1-855-612-7811. The toll-free number is configured in
> AWS End User Messaging to recognize all of these keywords (case-insensitive).
> Upon receiving an opt-out, the system sends one final confirmation message:
> "Windows by Burkhardt: You are unsubscribed and will receive no more messages.
> Reply START to resubscribe." After this confirmation, the subscriber's status is
> set to "opted_out" in our database and no further messages of any kind are sent
> to that number until the subscriber explicitly re-opts in (by texting START or by
> submitting the signup form again).
>
> Subscribers may also reply HELP or INFO at any time to receive: "Windows by
> Burkhardt support: 1-855-612-7811 / chris@windowsbyburkhardt.com. Msg&data rates
> may apply. Reply STOP to cancel."

### Sample messages

Submit at least two representative outbound samples. Suggested:

**Sample 1 — Welcome / confirmation**

> Windows by Burkhardt: You're subscribed to appointment & follow-up texts.
> Msg freq varies. Msg&data rates may apply. Reply HELP for help, STOP to cancel.

**Sample 2 — Appointment reminder**

> Windows by Burkhardt: Reminder of your free in-home consultation on Tue 5/20 at
> 2:00 PM. Reply C to confirm or R to reschedule. Reply STOP to opt out.

**Sample 3 — Post-install follow-up**

> Windows by Burkhardt: Your installation is complete — thank you! If you have a
> moment, we'd love your feedback: [link]. Reply STOP to opt out.

### Required uploads

| Artifact | Source |
|---|---|
| Screenshot of opt-in page showing all six disclosures inline | https://windowsbyburkhardt.com/sms-signup |
| Screenshot of SMS Terms of Service page | https://windowsbyburkhardt.com/sms-terms |
| Screenshot of Privacy Policy page showing the SMS section | https://windowsbyburkhardt.com/privacy |

---

## Pre-submission checklist

- [ ] All three pages deploy and load publicly (test in an incognito window)
- [ ] Opt-in checkbox is **un-checked** by default in the deployed page
- [ ] All six disclosures are visible **on the same page** as the checkbox (not behind a tab or link)
- [ ] SMS Terms page mentions: program, STOP, HELP, carriers, frequency, cost, eligibility, governing law
- [ ] Privacy Policy contains the clause excluding SMS opt-in data from third-party sharing
- [ ] Toll-free number 855-612-7811 is provisioned in the AWS account
- [ ] STOP, HELP, START keyword auto-responses are configured on the originator
- [ ] Volume estimate matches realistic expected sending (do not over-state)
- [ ] Screenshots captured at desktop width with full disclosures visible

## After submission

- Review typically takes 1&ndash;3 weeks
- Throughput on toll-free is limited until verification approves
- If denied: the response will identify which element was missing; fix it on the live page, take a new screenshot, and resubmit
- Do **not** send promotional messages from the number until status = "Verified"
