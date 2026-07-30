Fix the intermittent barcode product lookup problem in my existing AI Inventory Manager project.

IMPORTANT:
Do NOT redesign the UI.
Do NOT remove any existing features.
Do NOT change the existing barcode scanner unnecessarily.
Do NOT replace the entire project.
Analyze the existing implementation first and modify only what is necessary.

CURRENT PROBLEM:

In the Add Product page, I scan a real retail barcode.

The barcode itself is being detected correctly.

For example:
8901030735516

Sometimes scanning the SAME product correctly retrieves:
- Product name
- Brand
- Category
- Description
- Product image
- Weight/volume
- Manufacturer
- Country of origin
- Ingredients/nutrition when available
- Other available product information

But sometimes scanning the EXACT SAME barcode shows:

"Barcode XXXXX read cleanly, but details are not in public databases. Please enter product details below."

This is incorrect because the same barcode may have successfully returned product information during an earlier scan.

I want you to FIX this reliability problem.

--------------------------------------------------
1. ANALYZE THE EXISTING LOOKUP SYSTEM
--------------------------------------------------

Inspect the complete barcode lookup flow, especially:

barcode_lookup.py
app.py
scanner.js
scanner_page.html
add_item.html

and any database/cache code used by barcode lookup.

The current barcode_lookup.py queries multiple sources such as:

- Open Food Facts
- Open Beauty Facts
- Open Products Facts
- UPC ItemDB
- Go-UPC
- BarcodeLookup
- EAN Search
- BarcodesDB
- Open Library for ISBN
- existing Indian/GS1 prefix logic

KEEP the multi-source lookup system.

Do not remove working sources unless a source is permanently broken.

--------------------------------------------------
2. FIX FALSE "NOT FOUND" RESULTS
--------------------------------------------------

Currently network errors, API timeouts, HTTP errors, rate limits, parsing errors, and genuine "product not found" responses can end up producing the same result.

This must be fixed.

Internally distinguish between:

FOUND
NOT_FOUND
TEMPORARY_FAILURE
RATE_LIMITED
TIMEOUT
NETWORK_ERROR

Do not tell the user that the product is "not in public databases" merely because an API timed out.

If lookup temporarily fails, show something like:

"Product lookup temporarily failed. Retrying..."

or:

"Product details could not be retrieved right now. Please try again."

Only show a true "Not Found" message when the lookup system has reasonable evidence that the product really was not found.

--------------------------------------------------
3. ADD AUTOMATIC RETRY
--------------------------------------------------

Implement intelligent retries for temporary failures.

For example:

Attempt 1
→ query databases

If temporary network/API failure:
→ wait a short time
→ Attempt 2

If necessary:
→ Attempt 3

Use reasonable exponential backoff.

Example:

0 sec
1 sec
2 sec

Do NOT endlessly retry.

Do not retry definite 404/not-found responses unnecessarily.

--------------------------------------------------
4. MAKE THE CACHE RELIABLE
--------------------------------------------------

This is extremely important.

The current system already has barcode_cache logic.

If barcode:

8901030735516

successfully returns product information once, that information should be stored.

The next time the same barcode is scanned:

SCAN
→ check existing inventory/product DB
→ check barcode cache
→ if found return immediately
→ only then contact external databases

A previously successful barcode should NOT depend on public APIs every time.

Also investigate existing code containing:

except Exception:
    pass

especially around cache reading/writing.

Do NOT silently ignore cache/database failures.

Replace silent failures with proper logging.

For example:

logger.exception(...)
logger.warning(...)

while keeping the application running.

--------------------------------------------------
5. HANDLE RENDER PERSISTENCE CORRECTLY
--------------------------------------------------

The application is deployed on Render.

Investigate whether the current SQLite barcode cache can disappear because of Render's filesystem behavior/restarts/redeployments.

Do not rely only on temporary in-memory caching.

Use the project's persistent product/database storage strategy.

If necessary, store successful barcode lookup results in a persistent table.

Do not break the existing SQLite/database implementation.

--------------------------------------------------
6. SAVE SUCCESSFUL PRODUCT LOOKUPS
--------------------------------------------------

Whenever a public database successfully identifies a barcode, permanently remember the mapping.

Example:

barcode:
8901030735516

successful lookup:
{
    name: "...",
    brand: "...",
    category: "...",
    image_url: "...",
    description: "...",
    manufacturer: "...",
    ...
}

Store this result.

Future scan:

8901030735516
      ↓
local/persistent lookup
      ↓
product information
      ↓
NO external API required

If the product has already been manually added to the user's inventory with this barcode, prefer the inventory's known product information.

--------------------------------------------------
7. IMPROVE PARALLEL LOOKUP RELIABILITY
--------------------------------------------------

The existing system queries several sources concurrently.

Keep parallel lookup because it is useful.

However, inspect:

TIMEOUT
FULL_PARALLEL_TIMEOUT
ThreadPoolExecutor
as_completed()

The current global timeout may cause a valid but slower database result to be discarded.

Improve this without making scanning extremely slow.

Use sensible per-source timeouts and an overall lookup timeout.

Return immediately when a reliable source finds a valid product.

Cancel unnecessary remaining requests when appropriate.

But do not classify an overall timeout as genuine NOT_FOUND.

--------------------------------------------------
8. HANDLE RATE LIMITS
--------------------------------------------------

Some public barcode services may return:

HTTP 429

Detect this explicitly.

Do not interpret 429 as "product doesn't exist."

Mark that source as temporarily unavailable/rate-limited and allow another source or retry to handle it.

Also correctly handle:

408
429
500
502
503
504

as temporary failures where appropriate.

--------------------------------------------------
9. BARCODE NORMALIZATION
--------------------------------------------------

Keep and improve the existing barcode variant logic.

Normalize scanned values before lookup:

- trim whitespace
- digits-only when appropriate
- preserve valid leading zeros
- support EAN-13
- EAN-8
- UPC-A
- UPC-E
- GTIN-14
- ISBN
- Code 128 where applicable

Validate EAN/UPC check digits where possible.

Do not accidentally change a correctly scanned barcode into another product's barcode.

--------------------------------------------------
10. FRONTEND BEHAVIOR
--------------------------------------------------

Do NOT redesign the Add Product page.

Keep the existing UI/theme.

When scanning:

Show:
"Looking up product..."

If found:
automatically fill all available fields exactly as the application currently does.

If cached:
fill the fields immediately.

If temporary lookup failure:
show:

"Product lookup temporarily failed. Tap Retry."

Provide a Retry action if appropriate.

If genuinely not found:
show:

"Barcode read successfully, but product information was not found. You can enter the details manually."

Do NOT say the product is absent from public databases when the actual cause was a timeout/network error.

--------------------------------------------------
11. DO NOT USE FAKE AI PRODUCT INFORMATION
--------------------------------------------------

Do NOT generate fake product names based on barcode digits.

Do NOT guess a product name.

Do NOT invent brand, price, description, manufacturer or image.

Only auto-fill real retrieved information or information already stored by the inventory system.

Existing barcode_ai.py synthetic prediction must NOT silently replace a failed real database lookup.

It may remain in the project if another feature needs it, but normal retail barcode lookup should prefer real data.

--------------------------------------------------
12. LOGGING
--------------------------------------------------

Add useful server-side logging.

For every barcode lookup log something similar to:

[BARCODE] Looking up: 8901030735516
[CACHE] MISS
[OpenFoodFacts] FOUND

or:

[CACHE] MISS
[OpenFoodFacts] TIMEOUT
[UPCItemDB] RATE_LIMITED
[BarcodeLookup] NETWORK_ERROR
[BARCODE] TEMPORARY_FAILURE

or:

[CACHE] HIT 8901030735516

Do not expose sensitive information in logs.

--------------------------------------------------
13. IMPORTANT TEST
--------------------------------------------------

After implementing the fix, test the SAME barcode repeatedly.

Example:

8901030735516

Test:

Scan #1
Scan #2
Scan #3
Scan #4
Scan #5

If it is successfully identified during Scan #1, subsequent scans should preferably come from persistent cache/local product data and return the SAME product information.

Also test:

1. Internet working
2. Slow API
3. API timeout
4. HTTP 429
5. HTTP 500
6. Product genuinely absent
7. Cached product while external APIs are unavailable
8. Application restart
9. Repeated rapid scans
10. Invalid barcode

--------------------------------------------------
14. FINAL REQUIREMENT
--------------------------------------------------

Do not just explain the problem to me.

Actually inspect and MODIFY the project files.

Preserve the existing project structure and UI.

After making the changes:

1. Tell me exactly which files you modified.
2. Explain the root cause you found.
3. Explain how the new retry/cache/failure handling works.
4. Tell me if any Render configuration needs to be changed.
5. List any new environment variables or dependencies.
6. Make sure existing Add Product, Billing, Barcode Generator and Fast Scanner functionality still works.
7. Check for syntax/runtime errors before finishing.

PRIMARY GOAL:

Once a barcode has successfully returned real product information, the AI Inventory Manager should remember it reliably so scanning the SAME barcode later does not randomly change from "Found" to "Not Found".