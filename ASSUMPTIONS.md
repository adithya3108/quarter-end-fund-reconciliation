# Assumptions

## Assumption 1: EUR/USD rate and date
- **What I assumed:** Ember Retail GmbH's Q4 mark (EUR 13,100,000) is converted at **1.0666 USD per EUR**, the ECB euro reference rate published on **Friday 30 December 2022**. That gives USD 13,972,460.00. Ember's cost stays at its historical USD 14,184,000 and is not re-converted.
- **Why:** 31 December 2022 was a Saturday and the ECB publishes no reference rates at weekends. 30 December was the last rate published on or before the valuation date, so it is the rate in force at the valuation date. In Q3, CVP used the ECB rate on the valuation date itself (0.9748 on 30 Sep 2022), and using the last available rate is the closest match to that practice. Using a rate published after the valuation date (2 January 2023) would bring in information from after the period end. The Eurosystem used the same 1.0666 rate for its own 30 December 2022 quarter-end revaluation. Source: ECB euro foreign exchange reference rates, USD, 30 Dec 2022 (ecb.europa.eu, euro reference exchange rates, USD page; also published in the Official Journal of the EU, 2023/C 1/01).
- **What would change if wrong:** Only Ember's USD value moves. Each 0.0010 change in the rate changes investments, NAV and the change in unrealized gain by USD 13,100. No R1 flag changes, because materiality is measured in EUR (see Assumption 2).

## Assumption 2: Materiality threshold for R1, and the currency it is measured in
- **What I assumed:** A mark changed materially when the absolute quarter-on-quarter change is **15% or more**, measured in the **company's own currency**. The USD change is reported too (`change_pct_usd`) for information but does not trigger R1.
- **Why:** 15% sits in the 10–25% band many funds use for a quarter. It's toward the cautious end on purpose: if the reviewer looks at a mark that turns out fine, it costs a few minutes, but if a wrong mark goes unchecked into an LP report, it costs far more. Measuring in local currency separates company performance from currency moves. Ember fell 2.96% in EUR but rose 6.18% in USD only because EUR/USD went from 0.9748 to 1.0666, and that is not a change in the company's value.
- **What would change if wrong:** At 15% the R1 flags are Bluetarn (+37.5%), Cedar (−31.9%) and Harbor Analytics (+17.8%). At 20% or 25%, Harbor drops out. At 10%, the list is the same as at 15% (the next largest move is Aurora at +4.5%). Measuring in USD would not change any flag at 15%, but it would make Ember's flag status depend on the exchange rate. NAV is unaffected because flags don't change values. The threshold can be changed with `--threshold`.

## Assumption 3: Ionmere Materials Ltd has no Q4 mark
- **What I assumed:** Ionmere is carried at its **cost of USD 14,000,000** (the 15 Dec 2022 subscription, which matches both the bank statement and Agent B's records). It is flagged R3, and its unrealized gain is zero.
- **Why:** CVP's note says it was acquired on 15 Dec 2022 and won't be valued until Q1 2023. Two weeks after an arm's-length purchase, the price paid is the best evidence of fair value, and carrying at cost until the first valuation is common practice (hint 2). Leaving it out would understate NAV by USD 14 million while the cash it used is already gone from the bank.
- **What would change if wrong:** NAV, investments and the change in unrealized gain would move one-for-one with the difference between Ionmere's fair value and USD 14 million. Cash, realized gain and every other bridge line would not change.

## Assumption 4: Which Q4 payments are accrual settlements
- **What I assumed:** Two payments settle liabilities that were already on the 30 September statement of net assets, so they are `accrual_settlement` and not Q4 expenses: Harbourside administration fees for Jul–Sep (USD 65,000) and the Coral & Finch FY21 audit fee (USD 120,000).
- **Why:** Both payee and amount exactly match the two liabilities at 30 Sep (total USD 185,000). Expensing them again would count the same cost twice. Agent B checks that every `accrual_settlement` matches a 30 Sep liability.
- **What would change if wrong:** Expenses would rise by up to USD 185,000, and the bridge would no longer balance against net assets.

## Assumption 5: Liabilities at 31 December are exactly sheet 2 of the NAV summary
- **What I assumed:** Liabilities at 31 Dec are the audit accrual (USD 130,000) plus the Q4 administration fee payable (USD 65,000), for a total of USD 195,000. Both are counted as Q4 expenses.
- **Why:** The brief defines liabilities this way, and both 30 Sep liabilities were paid during the quarter.
- **What would change if wrong:** Any other unpaid Q4 bill would reduce NAV and increase expenses by the same amount.

## Assumption 6: Expenses that cover a later period are still Q4 expenses
- **What I assumed:** The D&O insurance premium for 2023 (USD 42,000) is a Q4 expense, not a prepayment. The Q4 management fee (paid in advance on 3 Oct) is also a Q4 expense.
- **Why:** The brief says to treat every other expense paid in Q4 as a Q4 expense, even if it covers a later period. Under normal accrual accounting, the D&O premium would be a prepaid asset.
- **What would change if wrong:** Treating the D&O premium as prepaid would raise NAV by USD 42,000.

## Assumption 7: The R. Mehta Family Trust wire is LP10's capital call
- **What I assumed:** The 9 Nov "INCOMING TT R MEHTA FAM TRST ... CALL NO 7" wire of USD 800,000 is LP10's capital call, even though the wire text has no LP code.
- **Why:** The 30 Nov distribution line names "R MEHTA FAMILY TRUST (LP10)". USD 800,000 is also exactly 8% of LP10's USD 10m commitment, the same percentage every other LP paid (USD 20m total called on USD 250m committed). The code first lets the LLM make this match, and checks it afterwards.
- **What would change if wrong:** Only the `lp_code` field. The capital called total does not depend on it.

## Assumption 8: Smaller treatments
- The USD 3,500 legal refund (overbilling) reduces expenses. It is not income.
- Bank charges (maintenance fees and wire charges, USD 850 in total) are expenses.
- The Aurora interim dividend (USD 420,000) is income, not a return of capital, so it does not reduce Aurora's cost.
- Realized gain on Fjord Payments is the bank proceeds of USD 29,000,000 minus the USD 20,000,000 cost from the Q3 files, which gives USD 9,000,000. There are no transaction costs on the statement.
