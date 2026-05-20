from __future__ import annotations

VARIABLE_CATEGORIES = [
    "Home",
    "Maintenance - Archi",
    "Maintenance - Hillview",
    "Groceries",
    "Food",
    "Bills (Electricity)",
    "Bills (Singtel)",
    "Bills (Arlyn)",
    "Bills (Misc.)",
    "Bills (Insurance)",
    "Bills (Baby)",
    "Shopping - Me",
    "Shopping - My wife",
    "Education",
    "Travel",
    "Transport/Car",
    "Fitness",
    "Personal care",
    "Entertainment",
    "Gifts",
]

FIXED_CATEGORIES = [
    "Bills (Starhub)",
    "Mortgage - Hillview",
    "Mortgage - Archi",
    "Income Tax - A",
    "Income Tax - FX",
    "Property Tax - Hillview",
    "Property Tax - Archi",
    "Loan repayment",
    "Car loan",
    "Spotify + Netflix+Gomo",
]

ALL_CATEGORIES = VARIABLE_CATEGORIES + FIXED_CATEGORIES

CATEGORY_KEYWORDS = {
    "Home": ["home", "household", "ikea", "furniture", "renovation"],
    "Maintenance - Archi": ["archi maintenance", "archi mcst", "archi condo"],
    "Maintenance - Hillview": ["hillview maintenance", "hillview mcst", "hillview condo"],
    "Groceries": ["grocery", "groceries", "ntuc", "fairprice", "cold storage", "redmart", "sheng siong", "supermarket"],
    "Food": ["food", "dinner", "lunch", "breakfast", "coffee", "restaurant", "meal", "cafe", "mcdonald", "grabfood"],
    "Bills (Electricity)": ["electricity", "sp utilities", "utilities", "power", "water bill"],
    "Bills (Singtel)": ["singtel"],
    "Bills (Arlyn)": ["arlyn"],
    "Bills (Misc.)": ["bill", "bills", "misc bill"],
    "Bills (Insurance)": ["insurance", "aia", "prudential", "great eastern", "income insurance"],
    "Bills (Baby)": ["baby", "diaper", "diapers", "milk powder", "formula", "childcare"],
    "Education": ["education", "school", "course", "tuition", "book", "books"],
    "Travel": ["travel", "hotel", "flight", "airbnb", "airline", "trip"],
    "Transport/Car": ["grab", "gojek", "taxi", "petrol", "parking", "erp", "car", "transport", "mrt", "bus"],
    "Fitness": ["fitness", "gym", "yoga", "classpass", "trainer"],
    "Personal care": ["haircut", "salon", "skincare", "facial", "personal care", "massage"],
    "Entertainment": ["movie", "cinema", "entertainment", "concert", "game", "games"],
    "Gifts": ["gift", "present", "birthday", "wedding"],
}

SHOPPING_KEYWORDS = [
    "shopping",
    "shop",
    "shopee",
    "lazada",
    "amazon",
    "uniqlo",
    "zara",
    "clothes",
    "shirt",
    "dress",
    "shoes",
    "bag",
]
