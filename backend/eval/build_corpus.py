"""Build the retrieval-lab corpus and gold queries.

    python eval/build_corpus.py

Writes data/eval_corpus/*.md (the 7 sample policies + 17 more, with deliberate near-duplicates) and
eval/retrieval_queries.json. Every gold snippet is asserted to exist in its document, so a typo
cannot silently corrupt the benchmark.

Query types:
  keyword    shares distinctive words with the answer passage
  paraphrase little vocabulary overlap, so lexical search struggles
  confusable a near-duplicate passage exists in another document; the question carries the cue that
             identifies the right one
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "policies"
CORPUS = ROOT / "data" / "eval_corpus"

DOCS = {
    "travel_policy": """# Travel Policy

All business travel must be approved by a manager before booking. Employees book through the corporate travel portal so that negotiated rates and duty-of-care tracking apply.

Domestic flights must be booked at least 14 days ahead in economy class. International flights longer than 8 hours may use premium economy. Business class is never reimbursed unless the CEO approves it in writing.

Hotel stays are capped at 160 USD per night in most cities and 230 USD per night in New York, San Francisco and London. Employees should choose hotels within 2 miles of the meeting location.

Per diem for meals while travelling is 55 USD per day domestically and 75 USD per day internationally. Per diem is not paid for days when meals are provided by a conference or client.

Rental cars are limited to compact or midsize vehicles. Employees must decline the rental company's insurance because the company's policy already covers collision damage.

Travel expenses must be submitted within 21 days of returning from the trip.
""",
    "procurement_policy": """# Procurement Policy

Purchases of goods or services below 1,000 USD can be made with a company card by any employee after manager approval. Purchases from 1,000 to 10,000 USD require two written quotes and approval from a department head.

Purchases above 10,000 USD require three competitive bids, a legal review of the contract, and approval from the CFO. Contracts longer than 12 months must also be reviewed by Legal regardless of value.

Vendors must complete a security questionnaire before receiving access to any company system. Vendors handling customer data must sign a data processing agreement.

Employees may not split a purchase into several smaller orders to stay under an approval threshold. Doing so is treated as a policy violation.

Software subscriptions are purchased only through IT so licenses can be tracked and renewals are not missed.
""",
    "onboarding_policy": """# Onboarding Policy

New hires receive their laptop and accounts on the Friday before their start date. Managers must request equipment at least 10 business days before the start date.

During the first week every new hire completes security awareness training, the code of conduct acknowledgement and benefits enrollment. Benefits enrollment must be completed within 30 days of the start date.

Each new hire is assigned an onboarding buddy for the first 90 days. The buddy meets with the new hire weekly and answers day to day questions.

The probation period is 90 days. A formal check-in happens at 30, 60 and 90 days, and the manager records feedback in the HR system.

Employees who leave the company must return company equipment within 14 days using the prepaid shipping label.
""",
    "performance_review_policy": """# Performance Review Policy

Performance reviews happen twice a year, in June and December. Each cycle has a self-assessment, a peer feedback round and a manager assessment.

Ratings use a five point scale from 1 (needs improvement) to 5 (exceptional). A rating of 2 or below triggers a written performance improvement plan lasting 60 days.

Promotions are considered once a year in January and require a rating of 4 or above in the two most recent cycles. Promotion requests need support from the manager and the department head.

Salary increases are decided after the December cycle and take effect on March 1. Merit increases are capped at 8 percent unless the employee is promoted.

Employees may appeal a rating within 10 business days by writing to HR. An HR business partner reviews the appeal and responds within 15 business days.
""",
    "data_retention_policy": """# Data Retention Policy

Customer records are kept for 7 years after the end of the contract and then securely deleted. Financial records are retained for 10 years to meet audit requirements.

Email is retained for 3 years. Chat messages are retained for 1 year. Employees must not delete messages that are subject to a legal hold.

Backups are encrypted and retained for 35 days. Backup restoration tests are performed every quarter by the infrastructure team.

Personal data of former employees is deleted 2 years after their last working day, except for payroll records that must be kept for 7 years.

Deletion requests from customers must be completed within 30 days and confirmed in writing.
""",
    "incident_response_policy": """# Incident Response Policy

Any employee who suspects a security incident must report it to the security team immediately using the incident hotline or the security channel. Reporting late is a violation even if the suspicion turns out to be wrong.

Incidents are classified by severity. Severity 1 incidents involve customer data exposure or a production outage and require a response within 15 minutes, 24 hours a day. Severity 2 incidents require a response within 2 hours during business hours.

A designated incident commander coordinates the response and keeps a timeline. The commander decides whether customers and regulators must be notified.

Regulators must be notified within 72 hours of confirming a breach of personal data. Affected customers are notified without undue delay and no later than 5 business days.

After every Severity 1 incident a blameless post-incident review is held within 5 business days, and action items are tracked until completion.
""",
    "acceptable_use_policy": """# Acceptable Use Policy

Company devices and accounts are provided for business use. Limited personal use is allowed if it does not interfere with work or create security risk.

Employees may not install software that has not been approved by IT. Pirated software, torrent clients and cryptocurrency miners are strictly prohibited.

Streaming video and large downloads should be avoided on the office network during business hours. Gambling and adult content sites are blocked on all company networks.

Company email must not be used to sign up for personal services such as social networks or shopping sites.

The company may monitor network traffic and company-owned devices for security purposes. Employees should have no expectation of privacy on company systems.
""",
    "byod_policy": """# Bring Your Own Device Policy

Employees may use a personal phone or tablet to access company email and chat if the device is enrolled in mobile device management. Personal laptops may not be used to access production systems.

Enrolled devices must have a passcode of at least 6 digits, automatic lock after 2 minutes and full-device encryption enabled. Jailbroken or rooted devices are not allowed.

The company can remotely wipe only the company data container on a personal device. A full device wipe is performed only if the employee agrees or the device is lost and cannot otherwise be secured.

The company reimburses 30 USD per month toward the mobile phone bill of employees who enroll a personal phone.

If a personal device is lost or stolen the employee must tell IT within 4 hours.
""",
    "overtime_policy": """# Overtime Policy

Non-exempt employees are paid 1.5 times their hourly rate for hours worked beyond 40 in a week. Work on a public holiday is paid at 2 times the hourly rate.

Overtime must be approved by a manager before it is worked. Unapproved overtime is still paid but may lead to a conversation about workload.

Exempt employees are not eligible for overtime pay. Managers should monitor workload to avoid sustained long hours.

Employees who work more than 6 hours must take an unpaid 30 minute meal break. Employees must have at least 11 hours of rest between shifts.

On-call engineers receive a stipend of 150 USD per week of on-call duty plus compensatory time for incidents handled outside business hours.
""",
    "public_holidays_policy": """# Public Holidays

The company observes 11 paid public holidays each year: New Year's Day, Martin Luther King Jr. Day, Presidents' Day, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving Day, the day after Thanksgiving, Christmas Eve and Christmas Day.

When a holiday falls on a Saturday it is observed on the preceding Friday, and when it falls on a Sunday it is observed on the following Monday.

Employees who need to work on a holiday may take a floating day off within 60 days. Floating days cannot be carried into the next calendar year.

The office is closed between Christmas Day and New Year's Day, but this closure uses the company's winter shutdown days and does not deduct from vacation balances.
""",
    "return_to_work_policy": """# Return to Work Policy

Employees returning from parental leave may work a phased schedule of 4 days per week for the first 4 weeks without a reduction in pay.

Nursing employees are provided a private room and reasonable break time to express milk during the working day for up to 12 months after the birth of a child.

Employees returning from medical leave of more than 4 weeks must provide a fitness-for-work note from their doctor. HR works with the employee and manager to agree any temporary adjustments.

Employees on leave keep their health insurance and the company continues to pay its share of the premium during approved leave.

Managers should schedule a return conversation in the week before the employee comes back to agree priorities.
""",
    "relocation_policy": """# Relocation Policy

Relocation assistance is offered for approved transfers of more than 50 miles. Employees must stay with the company for 12 months after the move or repay the relocation package on a pro rata basis.

The package covers moving household goods up to 10,000 pounds, one house hunting trip of 5 days, and temporary housing for up to 60 days.

A lump sum of 3,000 USD is paid for miscellaneous expenses. Relocation payments are taxable income and the company does not gross them up.

International relocations require a separate assignment letter and approval from the executive team.
""",
    "tuition_reimbursement_policy": """# Tuition Reimbursement Policy

The company reimburses up to 5,250 USD per calendar year for degree programs and certifications that are related to the employee's role or a planned career path. This is separate from the annual learning budget used for courses, books and conferences.

Employees must have worked at the company for at least 12 months and must get written approval from their manager before enrolling.

Reimbursement is 100 percent for a grade of B or higher and 50 percent for a grade of C. No reimbursement is made for a grade below C.

Employees who leave within 12 months of receiving reimbursement must repay it in full.
""",
    "workplace_safety_policy": """# Workplace Safety Policy

Every employee is responsible for reporting hazards, near misses and injuries to their manager and to facilities within 24 hours.

Fire drills are held twice a year and evacuation routes are posted near every exit. Employees must gather at the assembly point and wait for the all-clear from the floor warden.

First aid kits are located in every kitchen, and certified first aiders are listed on the intranet. Automated defibrillators are located in the lobby and on each floor.

Employees who work with ergonomic equipment such as standing desks may request an ergonomic assessment from facilities, which is completed within 10 business days.

Severe weather closures are announced by email and text message before 6:30 am.
""",
    "whistleblower_policy": """# Whistleblower Policy

Employees who suspect fraud, accounting irregularities, bribery or violations of law should report them through the confidential ethics line, which is run by an independent third party and is available 24 hours a day.

Reports can be made anonymously. The ethics committee acknowledges a report within 3 business days and aims to complete its investigation within 60 days.

The company prohibits retaliation against anyone who raises a concern in good faith. Retaliation, including demotion, harassment or dismissal, is itself grounds for termination.

Employees who knowingly make false reports may face disciplinary action. Investigations are confidential and shared only with those who need to know.
""",
    "social_media_policy": """# Social Media Policy

Employees may use social media in a personal capacity but must make clear that their views are their own and not those of the company. Do not use the company logo as a profile picture.

Never share confidential information, unreleased product details, customer names or financial results on social media.

Only the communications team may respond to press or media inquiries on behalf of the company. Forward any media request to press@company.example within 1 business day.

Employees who see negative posts about the company should report them to communications rather than responding.
""",
    "customer_data_handling_policy": """# Customer Data Handling Policy

Customer data is classified as confidential. It may only be accessed by employees who need it for their job, and access is reviewed every quarter.

Customer data must never be copied to personal devices, personal cloud storage or email accounts. Production data may not be used in development or test environments unless it has been anonymized.

Exports of customer data larger than 1,000 records require written approval from the data protection officer.

Customer support agents must verify the identity of a caller with two identifying details before discussing account information.

Any suspected exposure of customer data must be reported to the security team and the data protection officer within 1 hour.
""",
}

# (question, source document stem, gold snippet that must appear in the answer chunk, type)
K, P, C = "keyword", "paraphrase", "confusable"
QUERIES = [
    ("How many days ahead do I need to book a domestic flight?", "travel_policy", "at least 14 days ahead in economy class", K),
    ("What is the daily meal allowance for international trips?", "travel_policy", "75 USD per day internationally", K),
    ("Am I allowed to buy the rental car company's insurance?", "travel_policy", "must decline the rental company's insurance", P),
    ("How soon after I get back from a trip must I file my travel costs?", "travel_policy", "within 21 days of returning from the trip", C),
    ("Can I fly business class?", "travel_policy", "Business class is never reimbursed unless the CEO approves it in writing", P),
    ("What approvals do I need to buy something that costs 5,000 dollars?", "procurement_policy", "require two written quotes and approval from a department head", P),
    ("Who must sign off on a purchase above 10,000 USD?", "procurement_policy", "approval from the CFO", K),
    ("Is it okay to break one big order into several small ones?", "procurement_policy", "split a purchase into several smaller orders", P),
    ("Where do I buy software subscriptions?", "procurement_policy", "purchased only through IT", K),
    ("How long is the probation period for new hires?", "onboarding_policy", "The probation period is 90 days", K),
    ("When do new employees get their laptop?", "onboarding_policy", "laptop and accounts on the Friday before their start date", K),
    ("Who helps a new person during their first three months?", "onboarding_policy", "onboarding buddy for the first 90 days", P),
    ("By when must I enroll in benefits after joining the company?", "onboarding_policy", "within 30 days of the start date", C),
    ("How often are performance reviews held?", "performance_review_policy", "twice a year, in June and December", K),
    ("What happens if I get a rating of 2?", "performance_review_policy", "written performance improvement plan lasting 60 days", P),
    ("What is the maximum merit raise?", "performance_review_policy", "capped at 8 percent", P),
    ("How long do I have to dispute my rating?", "performance_review_policy", "within 10 business days by writing to HR", P),
    ("How long do we keep customer records after a contract ends?", "data_retention_policy", "7 years after the end of the contract", K),
    ("How long are chat messages kept?", "data_retention_policy", "Chat messages are retained for 1 year", K),
    ("How quickly must we honour a customer's request to erase their information?", "data_retention_policy", "within 30 days and confirmed in writing", P),
    ("How often are backup restores tested?", "data_retention_policy", "every quarter by the infrastructure team", P),
    ("How fast must we respond to a severity 1 incident?", "incident_response_policy", "within 15 minutes, 24 hours a day", K),
    ("How long do we have to tell regulators about a personal data breach?", "incident_response_policy", "within 72 hours of confirming a breach", K),
    ("When is the review meeting held after a major incident?", "incident_response_policy", "blameless post-incident review is held within 5 business days", P),
    ("Who decides if customers need to be told about an incident?", "incident_response_policy", "decides whether customers and regulators must be notified", P),
    ("Can I install a torrent client on my work laptop?", "acceptable_use_policy", "torrent clients and cryptocurrency miners are strictly prohibited", K),
    ("Can I check personal things on company equipment?", "acceptable_use_policy", "Limited personal use is allowed", P),
    ("Does the company watch what I do on its network?", "acceptable_use_policy", "may monitor network traffic", P),
    ("What passcode length is required on a personal phone used for work?", "byod_policy", "passcode of at least 6 digits", K),
    ("How soon must I report a lost personal phone?", "byod_policy", "tell IT within 4 hours", C),
    ("Does the company pay for my phone plan if I use my own phone?", "byod_policy", "30 USD per month toward the mobile phone bill", P),
    ("Can I use my own laptop to reach production?", "byod_policy", "Personal laptops may not be used to access production systems", K),
    ("How much more do I earn for working past 40 hours?", "overtime_policy", "1.5 times their hourly rate", P),
    ("How much is the on-call allowance?", "overtime_policy", "stipend of 150 USD per week", P),
    ("How much rest must I get between shifts?", "overtime_policy", "at least 11 hours of rest between shifts", K),
    ("How many public holidays do we get?", "public_holidays_policy", "observes 11 paid public holidays", K),
    ("What happens when a holiday lands on a weekend?", "public_holidays_policy", "observed on the preceding Friday", P),
    ("Does the end-of-year office closure use up my vacation days?", "public_holidays_policy", "does not deduct from vacation balances", C),
    ("Can I ease back in after having a baby?", "return_to_work_policy", "phased schedule of 4 days per week for the first 4 weeks", P),
    ("Is there a place to pump milk at work?", "return_to_work_policy", "private room and reasonable break time", P),
    ("Do I keep my health coverage while I am on leave?", "return_to_work_policy", "continues to pay its share of the premium during approved leave", C),
    ("How long must I stay after the company moves me to another city?", "relocation_policy", "stay with the company for 12 months after the move", P),
    ("How much temporary housing is covered when relocating?", "relocation_policy", "temporary housing for up to 60 days", K),
    ("Is the relocation lump sum taxed?", "relocation_policy", "taxable income", K),
    ("How much tuition can the company reimburse each year?", "tuition_reimbursement_policy", "up to 5,250 USD per calendar year", K),
    ("What grade do I need to get a full reimbursement for a course?", "tuition_reimbursement_policy", "100 percent for a grade of B or higher", P),
    ("Is the tuition benefit the same thing as the learning budget?", "tuition_reimbursement_policy", "separate from the annual learning budget", C),
    ("How quickly must a workplace injury be reported?", "workplace_safety_policy", "injuries to their manager and to facilities within 24 hours", K),
    ("Where are the defibrillators?", "workplace_safety_policy", "Automated defibrillators are located in the lobby and on each floor", K),
    ("How often are fire drills?", "workplace_safety_policy", "Fire drills are held twice a year", K),
    ("How do I report suspected fraud without giving my name?", "whistleblower_policy", "Reports can be made anonymously", C),
    ("How fast does the ethics committee acknowledge a report?", "whistleblower_policy", "acknowledges a report within 3 business days", K),
    ("What happens to someone who retaliates against a person who raised a concern?", "whistleblower_policy", "is itself grounds for termination", C),
    ("Can I use the company logo as my profile photo?", "social_media_policy", "Do not use the company logo as a profile picture", K),
    ("A journalist emailed me asking about the company. What should I do?", "social_media_policy", "Forward any media request to press@company.example within 1 business day", P),
    ("How often is access to customer data reviewed?", "customer_data_handling_policy", "access is reviewed every quarter", K),
    ("Can I use real customer data to test my code?", "customer_data_handling_policy", "unless it has been anonymized", P),
    ("How big an export of customer data needs approval?", "customer_data_handling_policy", "larger than 1,000 records require written approval from the data protection officer", K),
    ("What do support agents need to confirm before talking about an account?", "customer_data_handling_policy", "two identifying details", P),
    ("How fast must a suspected customer data leak be reported?", "customer_data_handling_policy", "security team and the data protection officer within 1 hour", C),
    # the 7 original sample policies
    ("What's the timeframe for returning a purchase to get my money back?", "refund_policy", "full refund within 30 days", P),
    ("Are digital downloads and gift cards returnable?", "refund_policy", "Digital products and gift cards are non-refundable", P),
    ("How many days off per year do full-time staff accrue?", "leave_policy", "20 days of paid vacation per year", P),
    ("How long can a new father stay home after a birth?", "leave_policy", "secondary caregivers receive 6 weeks", P),
    ("How many days can I work from home?", "remote_work_policy", "up to 3 days per week", P),
    ("What equipment money do remote workers get?", "remote_work_policy", "home office stipend of 500 USD for fully remote", P),
    ("Is alcohol on a client dinner reimbursable?", "expense_policy", "Alcohol is not reimbursable under any circumstances", K),
    ("How long after buying something do I have to submit my expense report with receipts?", "expense_policy", "within 30 days of the expense date", C),
    ("How long must my password be and how often does it change?", "security_policy", "at least 14 characters long and changed every 180 days", K),
    ("How quickly must I tell IT that my company laptop was stolen?", "security_policy", "within 2 hours of discovery", C),
    ("What does the company contribute to my retirement savings?", "benefits_policy", "401(k) retirement plan with a 4 percent employer match", P),
    ("Is there a discount on buying company shares?", "benefits_policy", "10 percent discount on the share price", P),
    ("What is the maximum value of a gift I can accept from a supplier?", "code_of_conduct", "gifts worth more than 75 USD", P),
    ("Can I trade stock if I know unannounced news?", "code_of_conduct", "Insider trading is prohibited", P),
]


def main() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    for old in CORPUS.glob("*.md"):
        old.unlink()
    texts: dict[str, str] = {p.stem: p.read_text(encoding="utf-8") for p in sorted(SAMPLE.glob("*.md"))}
    texts.update(DOCS)
    for name, text in texts.items():
        (CORPUS / f"{name}.md").write_text(text, encoding="utf-8")

    queries = []
    for i, (question, doc, snippet, qtype) in enumerate(QUERIES, 1):
        assert doc in texts, f"query {i}: unknown document {doc}"
        assert texts[doc].count(snippet) >= 1, f"query {i}: snippet not found in {doc}: {snippet!r}"
        queries.append({"id": f"r{i:02d}", "question": question, "source": f"{doc}.md", "snippet": snippet, "type": qtype})

    out = Path(__file__).parent / "retrieval_queries.json"
    out.write_text(json.dumps(queries, indent=1), encoding="utf-8")
    by_type = {t: sum(q["type"] == t for q in queries) for t in (K, P, C)}
    print(f"{len(texts)} documents -> {CORPUS}")
    print(f"{len(queries)} queries -> {out}  {by_type}")


if __name__ == "__main__":
    main()
