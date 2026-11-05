# Demo video script (2:30)

Target length **2:30**. The hard limit is under 3:00, so there is 30 seconds of slack for pauses and one retake of a click.
The video must show the app working, so almost every line below has something on screen at the same moment.
`python scripts/check_submission.py` checks that the timestamps add up and that each block can be spoken at a natural pace.

## Before you record

1. Start the server in a terminal that is **not** in the shot: `PAYPAL_MODE=mock python -m subsweep`
   (use your sandbox credentials instead if you have run the live setup: see [PayPal beat, variant A](#paypal-beat-which-variant)).
   `PAYPAL_MODE=mock` matters: without it, uploading the sample file gives no PayPal panel and no FitPass finding.
2. Open <http://127.0.0.1:8000> in a clean browser window, 1920x1080 (or 1440x900), 100% zoom, night theme, bookmarks bar hidden,
   notifications off. Close the `.env` file and any terminal that shows keys.
3. Have `sample_data/demo_statement.csv` ready in a folder window you can drag from.
4. Reload the page once so the landing animation starts from the top. Rehearse the whole run twice with a timer.
5. If you have set `ANTHROPIC_API_KEY`, the top bar shows the model name instead of "AI: built-in text". Say the Claude line in
   beat 4 only if that is what the screen shows.

## Script

| Time | On screen | Say |
|---|---|---|
| **0:00 to 0:20** The problem | Landing page. Let the headline finish, then scroll slowly into the "Your statement is noise" scene so the charges drift and then line up. | Every month, small charges leave your account. A free trial that never ended. A plan that crept up in price. The same service billed twice, once on your card and once through PayPal. On a statement full of coffee and groceries, none of it stands out. SubSweep finds it. |
| **0:20 to 0:40** Upload | Scroll back to the top. Drag `demo_statement.csv` onto the drop zone. The Overview opens. | I drop in a statement CSV. It can come from a bank, a card or PayPal. SubSweep detects the format and cleans up the messy merchant names, and it all stays on this computer. The only thing ever saved is which findings I dismiss. |
| **0:40 to 1:05** Detection | Overview: pause on the $1,697 and the health ring (45). Click **How is this calculated?** briefly. Open **Subscriptions**, expand the Netflix row (the arrow at the right) so the charge-history chart, with the price step in coral, and the confidence reason show. | Here is the picture: ten subscriptions, about seventeen hundred dollars a year, and a health score of forty-five. This is not machine learning, it is rules you can read. Open Netflix: every charge on a chart, the step up where the price rose, and the reason SubSweep is confident. Groceries and fuel are excluded, and it says which rule did that. |
| **1:05 to 1:30** A flagged duplicate | **Findings**. Scroll to **Streamly: 2 active subscriptions**. Click **Show data used**. | Now the findings. Streamly is billed twice: ten dollars a month and a hundred a year, both through PayPal. The explanation is in plain English, and I can see exactly what data it was built from. The wording comes from Claude, and every figure is checked against the data before I see it. Keep one, and that saves about a hundred dollars a year. |
| **1:30 to 1:55** PayPal status check | Back on **Overview**, scroll to the **PayPal status** panel; hover the FitPass Gym card (Cancelled, "Still charged on your statement"). Then **Findings**, the FitPass mismatch card, and point at **Manage in PayPal**. | PayPal adds ground truth. FitPass Gym shows as cancelled in PayPal on the eleventh of August, yet my statement has two more charges, forty-eight dollars. A bank statement alone could never show that. SubSweep only reads status. It cannot cancel anything, it links me to PayPal so I act myself. |
| **1:55 to 2:15** A drafted message | Open the Streamly finding again, click **Draft a message for me**, show the subject and body with the `[ACCOUNT NAME]` placeholders, click **Copy message**. | Let me act on the duplicate. SubSweep drafts a short, polite message, with placeholders for my own account details. It is never sent. There is a copy button, and that is all. I decide what happens next. |
| **2:15 to 2:30** One question | **Ask**. Click the suggestion **What do I spend on streaming?** (or type it). Show the answer, open **Show data used**, then cut to a closing card: SubSweep, and the repository address. | And I can ask my own data a question. What do I spend on streaming? Twenty-six dollars a month, Hulu and Netflix, with the data used right there. SubSweep: find what is quietly draining you. You decide. |

## PayPal beat: which variant

The PayPal beat (1:30 to 1:55) has to be true to what is on screen.

- **Variant A, live sandbox (preferred if you have done the live setup and approved the subscriptions).** The panel has no "Demo
  data" badge. Add this sentence at the end: "This status is read live from the PayPal sandbox."
- **Variant B, bundled demo data (the default).** The panel carries a "Demo data" badge and the notice "Showing bundled demo
  data (PayPal mock mode)". Add this sentence at the end, and point at the badge: "This panel is labelled demo data; with
  sandbox credentials the same screen reads live from PayPal."

Do not describe demo data as live PayPal data. The label on screen is part of the product.

## If the AI is not on

If the top bar says "AI: built-in text", replace the Claude sentence in the 1:05 beat with: "Here the wording is built in; with an
Anthropic key, Claude writes it, and every figure is still checked against the data."

## After recording

- Trim dead air, but keep every click visible. Keep the total under 3:00 (aim for 2:30).
- Add captions if you can (YouTube auto-captions are fine); judges often watch without sound.
- Upload to YouTube with visibility **Public**, put the repository link in the description, then open the link in a private
  window to confirm it plays without signing in.
