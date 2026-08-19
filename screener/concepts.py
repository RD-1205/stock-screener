"""
Concept normalization: the actual moat.

XBRL is standardized in *format*, not in *vocabulary*. The SEC lets filers
choose from ~14,000 us-gaap tags, and there is no rule forcing two companies
in the same industry to pick the same one. Real examples:

    Apple      revenue -> RevenueFromContractWithCustomerExcludingAssessedTax
    Ford       revenue -> Revenues
    a 2013 10-K        -> SalesRevenueNet          (deprecated tag, still in history)
    an insurer         -> Revenues + PremiumsEarnedNet

If you screen on `Revenues` alone you silently get NULL for roughly half the
market, and your "cheapest stocks by P/S" list is garbage. Every commercial
data vendor's real product is this mapping table plus the analysts who
maintain it. Yours will be imperfect; that's fine, just make it auditable
(hence `source_concept` in the fundamentals table).

RESOLUTION RULE: candidates are tried in order, first non-null wins.
Order matters -- put the modern, most-specific tag first and the vague
catch-all last.
"""

from dataclasses import dataclass, field


DURATION = "duration"   # flow: measured over a period (revenue, net income)
INSTANT = "instant"     # stock: measured at a point in time (total assets)


@dataclass(frozen=True)
class Metric:
    name: str
    kind: str                       # DURATION or INSTANT
    unit: str                       # expected unit
    concepts: tuple                 # candidate us-gaap tags, in priority order
    sum_of: tuple = field(default=())  # if set, metric = sum of these tags instead


METRICS = [
    # ---------------- income statement (flows) ----------------
    Metric("revenue", DURATION, "USD", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
        "TotalRevenuesAndOtherIncome",
    )),
    Metric("cost_of_revenue", DURATION, "USD", (
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
    )),
    Metric("gross_profit", DURATION, "USD", (
        "GrossProfit",
    )),
    Metric("operating_income", DURATION, "USD", (
        "OperatingIncomeLoss",
    )),
    Metric("pretax_income", DURATION, "USD", (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    )),
    Metric("tax_expense", DURATION, "USD", (
        "IncomeTaxExpenseBenefit",
    )),
    Metric("interest_expense", DURATION, "USD", (
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestIncomeExpenseNet",
    )),
    Metric("net_income", DURATION, "USD", (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    )),
    Metric("eps_diluted", DURATION, "USD/shares", (
        "EarningsPerShareDiluted",
        "EarningsPerShareBasicAndDiluted",
    )),
    Metric("eps_basic", DURATION, "USD/shares", (
        "EarningsPerShareBasic",
        "EarningsPerShareBasicAndDiluted",
    )),
    Metric("shares_diluted", DURATION, "shares", (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    )),
    Metric("depreciation_amortization", DURATION, "USD", (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "Depreciation",
    )),

    # ---------------- balance sheet (instants) ----------------
    Metric("total_assets", INSTANT, "USD", (
        "Assets",
    )),
    Metric("current_assets", INSTANT, "USD", (
        "AssetsCurrent",
    )),
    Metric("total_liabilities", INSTANT, "USD", (
        "Liabilities",
    )),
    Metric("current_liabilities", INSTANT, "USD", (
        "LiabilitiesCurrent",
    )),
    Metric("total_equity", INSTANT, "USD", (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    )),
    Metric("cash", INSTANT, "USD", (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    )),
    Metric("short_term_investments", INSTANT, "USD", (
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    )),
    Metric("inventory", INSTANT, "USD", (
        "InventoryNet",
    )),
    Metric("receivables", INSTANT, "USD", (
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
    )),
    Metric("shares_outstanding", INSTANT, "shares", (
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssued",
        "EntityCommonStockSharesOutstanding",   # this one lives in the `dei` taxonomy
    )),

    # Debt is the classic trap: there is no single "total debt" tag.
    # Companies split it across current/noncurrent and across
    # notes/loans/capital-lease line items. Summing is the only way.
    Metric("short_term_debt", INSTANT, "USD", (
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
        "OtherShortTermBorrowings",
    )),
    Metric("long_term_debt", INSTANT, "USD", (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermNotesPayable",
    )),

    # ---------------- cash flow (flows) ----------------
    Metric("operating_cash_flow", DURATION, "USD", (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    )),
    Metric("capex", DURATION, "USD", (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
    )),
    Metric("dividends_paid", DURATION, "USD", (
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    )),
    Metric("buybacks", DURATION, "USD", (
        "PaymentsForRepurchaseOfCommonStock",
    )),
]

METRICS_BY_NAME = {m.name: m for m in METRICS}

# Flat lookup: raw concept -> list of (metric_name, priority_index).
# Built once so ingestion can decide in O(1) whether a tag is interesting.
CONCEPT_INDEX = {}
for _m in METRICS:
    for _i, _c in enumerate(_m.concepts):
        CONCEPT_INDEX.setdefault(_c, []).append((_m.name, _i))

INTERESTING_CONCEPTS = frozenset(CONCEPT_INDEX)


def resolve(metric_name, available):
    """Given a metric and a dict of {concept: value}, return (value, concept).

    Returns (None, None) if no candidate tag is present. This is the whole
    normalization rule in five lines -- the hard part was the table above.
    """
    m = METRICS_BY_NAME[metric_name]
    for concept in m.concepts:
        v = available.get(concept)
        if v is not None:
            return v, concept
    return None, None
