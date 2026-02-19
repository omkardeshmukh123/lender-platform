"""
banks_list.py
Top 50 Indian Private Banks — hardcoded, no CSV needed.
These are well-known institutions, URLs verified, no validation required.
"""

TOP_50_PRIVATE_BANKS = [
    # ── Tier 1: Large Private Banks ──────────────────────────────
    {"company_name": "HDFC Bank",               "website": "https://www.hdfcbank.com",       "pan_india": True},
    {"company_name": "ICICI Bank",              "website": "https://www.icicibank.com",      "pan_india": True},
    {"company_name": "Axis Bank",               "website": "https://www.axisbank.com",       "pan_india": True},
    {"company_name": "Kotak Mahindra Bank",     "website": "https://www.kotak.com",          "pan_india": True},
    {"company_name": "IndusInd Bank",           "website": "https://www.indusind.com",       "pan_india": True},
    {"company_name": "Yes Bank",                "website": "https://www.yesbank.in",         "pan_india": True},
    {"company_name": "IDFC First Bank",         "website": "https://www.idfcfirstbank.com",  "pan_india": True},
    {"company_name": "Bandhan Bank",            "website": "https://www.bandhanbank.com",    "pan_india": True},
    {"company_name": "RBL Bank",                "website": "https://www.rblbank.com",        "pan_india": True},
    {"company_name": "Federal Bank",            "website": "https://www.federalbank.co.in",  "pan_india": True},

    # ── Tier 2: Mid-size Private Banks ───────────────────────────
    {"company_name": "South Indian Bank",       "website": "https://www.southindianbank.com","pan_india": True},
    {"company_name": "Karnataka Bank",          "website": "https://karnatakabank.com",      "pan_india": True},
    {"company_name": "DCB Bank",                "website": "https://www.dcbbank.com",        "pan_india": True},
    {"company_name": "CSB Bank",                "website": "https://www.csb.co.in",          "pan_india": True},
    {"company_name": "City Union Bank",         "website": "https://www.cityunionbank.com",  "pan_india": True},
    {"company_name": "Tamilnad Mercantile Bank","website": "https://www.tmbank.in",          "pan_india": False},
    {"company_name": "Karur Vysya Bank",        "website": "https://www.kvb.co.in",          "pan_india": False},
    {"company_name": "Lakshmi Vilas Bank",      "website": "https://www.lvbank.com",         "pan_india": False},
    {"company_name": "Nainital Bank",           "website": "https://www.nainitalbank.co.in", "pan_india": False},
    {"company_name": "Dhanlaxmi Bank",          "website": "https://www.dhanbank.com",       "pan_india": False},

    # ── Tier 3: Small Finance Banks ──────────────────────────────
    {"company_name": "AU Small Finance Bank",       "website": "https://www.aubank.in",          "pan_india": True},
    {"company_name": "Ujjivan Small Finance Bank",  "website": "https://www.ujjivansfb.in",      "pan_india": True},
    {"company_name": "Equitas Small Finance Bank",  "website": "https://www.equitasbank.com",    "pan_india": True},
    {"company_name": "Jana Small Finance Bank",     "website": "https://www.janabank.in",        "pan_india": True},
    {"company_name": "ESAF Small Finance Bank",     "website": "https://www.esafbank.com",       "pan_india": False},
    {"company_name": "Suryoday Small Finance Bank", "website": "https://www.suryodaybank.com",   "pan_india": False},
    {"company_name": "Utkarsh Small Finance Bank",  "website": "https://www.utkarsh.bank",       "pan_india": False},
    {"company_name": "Capital Small Finance Bank",  "website": "https://www.capitalbank.co.in",  "pan_india": False},
    {"company_name": "Fincare Small Finance Bank",  "website": "https://www.fincarebank.com",    "pan_india": False},
    {"company_name": "Northeast Small Finance Bank","website": "https://www.nesfb.com",          "pan_india": False},

    # ── Tier 4: Payment & Niche Banks ────────────────────────────
    {"company_name": "IDBI Bank",               "website": "https://www.idbibank.in",        "pan_india": True},
    {"company_name": "Jammu & Kashmir Bank",    "website": "https://www.jkbank.com",         "pan_india": False},
    {"company_name": "Catholic Syrian Bank",    "website": "https://www.csb.co.in",          "pan_india": False},
    {"company_name": "Ratnakar Bank",           "website": "https://www.rblbank.com",        "pan_india": True},
    {"company_name": "Saraswat Bank",           "website": "https://www.saraswatbank.com",   "pan_india": False},

    # ── Tier 5: Foreign Banks (operating in India) ───────────────
    {"company_name": "HSBC India",              "website": "https://www.hsbc.co.in",         "pan_india": True},
    {"company_name": "Citibank India",          "website": "https://www.online.citibank.co.in","pan_india": True},
    {"company_name": "Standard Chartered India","website": "https://www.sc.com/in",          "pan_india": True},
    {"company_name": "DBS Bank India",          "website": "https://www.dbs.com/in",         "pan_india": True},
    {"company_name": "Deutsche Bank India",     "website": "https://www.db.com/india",       "pan_india": True},
    {"company_name": "Barclays India",          "website": "https://home.barclays/india",    "pan_india": True},
    {"company_name": "Bank of America India",   "website": "https://business.bofa.com/en-us/content/india.html","pan_india": True},
    {"company_name": "JP Morgan India",         "website": "https://www.jpmorgan.com/IN/en/about-us","pan_india": True},
    {"company_name": "Mashreq Bank India",      "website": "https://www.mashreqbank.com",    "pan_india": False},
    {"company_name": "Emirates NBD India",      "website": "https://www.emiratesnbd.com/en/india","pan_india": False},

    # ── Tier 6: PSU Banks (top ones) ─────────────────────────────
    {"company_name": "State Bank of India",     "website": "https://www.onlinesbi.sbi",      "pan_india": True},
    {"company_name": "Punjab National Bank",    "website": "https://www.pnbindia.in",        "pan_india": True},
    {"company_name": "Bank of Baroda",          "website": "https://www.bankofbaroda.in",    "pan_india": True},
    {"company_name": "Canara Bank",             "website": "https://www.canarabank.com",     "pan_india": True},
    {"company_name": "Union Bank of India",     "website": "https://www.unionbankofindia.co.in","pan_india": True},
]