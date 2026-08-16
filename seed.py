"""
One-time catalogue seeder.

Populates categories and products from a fixed price list (sourced from an
existing shop's product menu). All prices are the source price x 0.8 (20%
off), rounded to 2 decimals. Stock is randomised 10-100 per product since the
source stock numbers were inconsistent snapshots, not reliable inventory.

No product photos are attached — the source only showed emoji/app icons, not
uploadable Telegram photos. Attach real images later per-product from the
admin panel ("🖼 Image" on a product's edit screen).

Safe to re-run: existing categories/products are matched by name and skipped,
so running this twice does not create duplicates.

Usage:
    python seed.py
"""

import random

import db

# (category, title, description, source_price_usd)
RAW_PRODUCTS = [
    # ---------------- AI Tools ----------------
    ("AI Tools", "Gemini Pro 18 Month 5TB", "18-month Gemini Pro subscription with 5TB storage.", 1.5),
    ("AI Tools", "Google One Gemini Pro 18M 5TB (Activation Link)", "Activation-link delivery, 18 months, 5TB storage.", 0.9),
    ("AI Tools", "Gemini Pro 1 Year 5TB (Old Stable Mail)", "1-year Gemini Pro on an aged stable mail account.", 2.7),
    ("AI Tools", "Gemini FULL FARM PRO Pixel 1 Year (3-Day Warranty)", "1-year Gemini Pro via Pixel farm method, 3-day warranty.", 3.0),
    ("AI Tools", "Google AI Pro 12 Month", "Full year of Google AI Pro.", 22.65),
    ("AI Tools", "Perplexity PRO 1 Year", "1-year Perplexity Pro subscription.", 40.0),
    ("AI Tools", "Perplexity Enterprise Pro Seat (1 Month)", "1 seat on Perplexity Enterprise Pro for 1 month.", 12.0),
    ("AI Tools", "ChatGPT Plus 1 Month (Pre-made Account)", "Ready-made account with 1 month ChatGPT Plus.", 2.2),
    ("AI Tools", "ChatGPT Go 3 Month Offer", "3-month ChatGPT Go promotional plan.", 2.0),
    ("AI Tools", "ChatGPT Plus UPI iCloud Account (7-Day Warranty)", "iCloud-linked account, UPI activation, 7-day warranty.", 3.5),
    ("AI Tools", "Claude 100$ API Credits (30-Day, Firsthand Warranty)", "$100 Claude API credit balance, firsthand warranty, 30 days.", 3.5),
    ("AI Tools", "Claude 500$ API Credits (30-Day, Firsthand Warranty)", "$500 Claude API credit balance, firsthand warranty, 30 days.", 13.0),
    ("AI Tools", "Claude Premium 6.25x 1 Month (25-Day Warranty)", "Claude Premium 6.25x usage tier, 25-day warranty.", 77.0),
    ("AI Tools", "Claude Premium 1 Month (Firsthand Warranty)", "Claude Premium seat, firsthand warranty, 1 month.", 71.0),
    ("AI Tools", "Claude Premium Seat (1 Month, Firsthand Warranty)", "Individual Claude Premium seat, firsthand warranty.", 74.25),
    ("AI Tools", "Grok 30-Day (5-Day Warranty)", "30 days of Grok access, 5-day warranty window.", 1.5),
    ("AI Tools", "Grok Super Account (9-10 Days)", "Grok Super tier account, valid 9-10 days.", 1.89),
    ("AI Tools", "Gamma Pro 12 Month", "Full year of Gamma Pro for AI presentations.", 31.0),
    ("AI Tools", "Manus Pro 12 Month", "Full year of Manus Pro AI agent access.", 45.0),
    ("AI Tools", "Higgsfield Pro 12 Month", "Full year of Higgsfield Pro AI video generation.", 75.0),
    ("AI Tools", "Leonardo.ai 8,500 Credits (No Seadance & MiniHead)", "8,500 Leonardo.ai image-gen credits.", 5.0),
    ("AI Tools", "Heygen Pro 3 Month (Promo, No Warranty)", "3 months of Heygen Pro AI video avatars, promo pricing.", 0.59),
    ("AI Tools", "QuillBot Premium 1 Month", "1-month QuillBot Premium writing assistant.", 2.4),
    ("AI Tools", "ElevenLabs Creator 12 Month", "Full year of ElevenLabs Creator plan for AI voice.", 50.0),
    ("AI Tools", "Veo 3 Ultra Extension 45K Credits (1 Month / 30-Day Warranty)", "45,000 credits for Veo 3 Ultra video generation, 30-day warranty.", 15.0),

    # ---------------- Dev & Productivity ----------------
    ("Dev & Productivity", "Cursor Pro 12 Month", "Full year of Cursor Pro AI coding assistant.", 85.5),
    ("Dev & Productivity", "Replit Core 12 Month", "Full year of Replit Core plan.", 32.0),
    ("Dev & Productivity", "Railway Hobby 12 Month", "Full year of Railway Hobby hosting plan.", 16.5),
    ("Dev & Productivity", "Supabase Pro 12 Month", "Full year of Supabase Pro plan.", 43.0),
    ("Dev & Productivity", "N8N Starter 12 Month", "Full year of N8N Starter workflow automation.", 23.0),
    ("Dev & Productivity", "Notion Business 12 Month", "Full year of Notion Business plan.", 23.5),
    ("Dev & Productivity", "Linear Business 12 Month", "Full year of Linear Business plan.", 11.2),
    ("Dev & Productivity", "Jam Team 10 Seats 12 Month", "10-seat Jam Team plan, full year.", 33.0),
    ("Dev & Productivity", "Gumloop Pro 12 Month", "Full year of Gumloop Pro automation.", 9.5),
    ("Dev & Productivity", "Granola Business 12 Month", "Full year of Granola Business plan.", 7.75),
    ("Dev & Productivity", "Granola Business 10 Seats 12 Month", "10-seat Granola Business plan, full year.", 15.0),
    ("Dev & Productivity", "PostHog Scale 12 Month", "Full year of PostHog Scale analytics plan.", 23.0),
    ("Dev & Productivity", "Resend Pro 12 Month", "Full year of Resend Pro email API plan.", 35.0),
    ("Dev & Productivity", "Warp Build 12 Month", "Full year of Warp Build terminal/AI plan.", 8.9),
    ("Dev & Productivity", "Mobbin 10x Seat 12 Month", "10-seat Mobbin design reference plan, full year.", 15.0),
    ("Dev & Productivity", "Coursera Premium 12 Month", "Full year of Coursera Premium courses.", 8.0),
    ("Dev & Productivity", "Coursera 1 Year", "1-year Coursera subscription.", 3.5),
    ("Dev & Productivity", "Wispr Flow Pro 12 Month", "Full year of Wispr Flow voice-to-text.", 32.0),
    ("Dev & Productivity", "Supercut AI Pro 10 Seats 12 Month", "10-seat Supercut AI Pro plan, full year.", 34.0),
    ("Dev & Productivity", "Supercut Pro 10 Seat 12 Month", "10-seat Supercut Pro plan, full year (alt listing).", 17.0),

    # ---------------- Design & Creative ----------------
    ("Design & Creative", "Adobe CC 4 Month (5-Day Warranty)", "4 months of Adobe Creative Cloud, 5-day warranty.", 4.09),
    ("Design & Creative", "Adobe Express Premium 12M India Activation Link", "12-month Adobe Express Premium, India activation link.", 1.55),
    ("Design & Creative", "Canva Admin 3 Year (2-Week Warranty)", "3-year Canva Admin access, 2-week warranty.", 14.0),
    ("Design & Creative", "Canva Edu Pro 1 Year (Unlimited Stock)", "1-year Canva Edu Pro, unlimited availability.", 0.6),
    ("Design & Creative", "CapCut Pro 6 Month (5-Day Warranty)", "6 months of CapCut Pro, 5-day warranty.", 14.8),
    ("Design & Creative", "CapCut Pro Team Account (30 Days)", "CapCut Pro team account, 30-day access.", 3.0),
    ("Design & Creative", "CapCut 6 Month Individual", "Individual CapCut Pro plan, 6 months.", 16.5),
    ("Design & Creative", "Figma Edu 2 Year (1-Week Warranty)", "2-year Figma Education plan, 1-week warranty.", 7.0),
    ("Design & Creative", "Figma EDU (Pro) 1-2 Year", "1-2 year Figma Education Pro plan.", 7.5),
    ("Design & Creative", "Framer Pro 12 Month", "Full year of Framer Pro website builder.", 8.2),
    ("Design & Creative", "Lovable Pro 12 Month", "Full year of Lovable Pro AI app builder.", 39.9),
    ("Design & Creative", "Lovable Pro Lite 12 Month (Link Delivery)", "Lite tier Lovable Pro, full year, link delivery.", 7.5),
    ("Design & Creative", "Magic Patterns 12 Month", "Full year of Magic Patterns UI design tool.", 10.5),
    ("Design & Creative", "Magic Patterns Starter 12 Month", "Starter tier Magic Patterns, full year.", 10.0),
    ("Design & Creative", "Runway Pro 12 Month", "Full year of Runway Pro AI video/creative suite.", 53.0),
    ("Design & Creative", "Miro Premium 1-3 Year", "Miro Premium plan, 1-3 year options.", 0.5),
    ("Design & Creative", "Gumloop Pro 12M (Design)", "Full year of Gumloop Pro (see Dev category for main listing).", 9.5),

    # ---------------- Streaming & Entertainment ----------------
    ("Streaming & Entertainment", "Netflix Premium 4K 1 Month (Shared, Full Warranty)", "Shared Netflix Premium 4K profile, 1 month, full warranty.", 2.79),
    ("Streaming & Entertainment", "Netflix Premium 4K 1 Month (Shared, Full Warranty) LINK", "Same as above, delivered via activation link.", 1.9),
    ("Streaming & Entertainment", "Netflix Admin 4K 1 Month (No Warranty)", "Admin-level Netflix 4K profile, 1 month, no warranty.", 2.6),
    ("Streaming & Entertainment", "Prime Video 6 Month (Ads-Free)", "6 months of ad-free Prime Video.", 2.5),
    ("Streaming & Entertainment", "Spotify 3 Months (Pre-made Account)", "Ready-made account with 3 months Spotify.", 3.09),
    ("Streaming & Entertainment", "Spotify Premium 1 Year (Pre-made Account)", "Ready-made account with 1 year Spotify Premium.", 18.5),
    ("Streaming & Entertainment", "Crunchyroll Premium 1 Month", "1 month of Crunchyroll Premium.", 0.9),
    ("Streaming & Entertainment", "Youtube 3 Month Redeem Link (Firsthand Warranty)", "3-month YouTube Premium redeem link, firsthand warranty.", 10.5),
    ("Streaming & Entertainment", "Youtube 3 Month Links", "3-month YouTube Premium redeem link (alt listing).", 2.0),

    # ---------------- VPN & Security ----------------
    ("VPN & Security", "Nord VPN 3 Month Links", "3-month NordVPN activation links.", 3.5),
    ("VPN & Security", "HMA Android/PC Key (20-30 Days)", "HMA VPN key for Android/PC, 20-30 days validity.", 0.49),
    ("VPN & Security", "HMA Android/PC Key (20-30 Days, Alt Listing)", "HMA VPN key, alternate stock listing.", 0.9),
    ("VPN & Security", "SurfShark 2 Month Account (Firsthand Warranty)", "2-month SurfShark account, firsthand warranty.", 2.95),
    ("VPN & Security", "SurfShark VPN 2 Month Coupon", "2-month SurfShark VPN coupon code.", 2.9),
    ("VPN & Security", "ExpressVPN Account (25-30 Days)", "ExpressVPN account, 25-30 days validity.", 1.0),

    # ---------------- Business & LinkedIn ----------------
    ("Business & LinkedIn", "LinkedIn Business 12 Month (Firsthand Warranty)", "1-year LinkedIn Business, firsthand warranty.", 54.0),
    ("Business & LinkedIn", "LinkedIn Business 2 Month New User (Firsthand Warranty)", "2-month LinkedIn Business for new users, firsthand warranty.", 4.6),
    ("Business & LinkedIn", "LinkedIn Career 12 Month (Firsthand Warranty)", "1-year LinkedIn Career plan, firsthand warranty.", 35.9),
    ("Business & LinkedIn", "LinkedIn Career 2 Month New User (Firsthand Warranty)", "2-month LinkedIn Career for new users, firsthand warranty.", 4.2),
    ("Business & LinkedIn", "LinkedIn Career 3 Month (New Accounts Only)", "3-month LinkedIn Career, new accounts only.", 1.3),
    ("Business & LinkedIn", "LinkedIn Sales Navigator 2 Month New User", "2-month LinkedIn Sales Navigator for new users.", 4.5),
    ("Business & LinkedIn", "Microsoft 365 Family 10 Month", "10 months of Microsoft 365 Family plan.", 10.0),
    ("Business & LinkedIn", "Microsoft 365 1 Year Premium", "1-year Microsoft 365 Premium plan.", 2.25),
    ("Business & LinkedIn", "I Love PDF 1 Year", "1-year iLovePDF premium tools.", 2.0),
    ("Business & LinkedIn", "Outlook Mails", "Fresh Outlook mail accounts.", 0.09),
    ("Business & LinkedIn", "Fresh iCloud Mail", "Freshly created iCloud mail account.", 0.12),
    ("Business & LinkedIn", "2FA Fresh Gmail", "Fresh Gmail account with 2FA enabled.", 1.0),
    ("Business & LinkedIn", "2FA Gmail 2023-2024", "2FA-enabled Gmail account, 2023-2024 batch.", 1.5),
]


def price_for(source_price: float) -> float:
    return round(source_price * 0.8, 2)


def run():
    random.seed()  # system entropy; stock counts are cosmetic, not security-relevant
    cat_ids = {}
    existing_cats = {c["name"]: c["id"] for c in db.list_categories()}
    existing_products = {
        p["title"] for p in db.list_products(only_active=False, limit=10000)
    }

    created_cats = created_products = skipped = 0

    for cat_name, title, desc, source_price in RAW_PRODUCTS:
        if cat_name not in cat_ids:
            if cat_name in existing_cats:
                cat_ids[cat_name] = existing_cats[cat_name]
            else:
                cat_ids[cat_name] = db.add_category(cat_name)
                existing_cats[cat_name] = cat_ids[cat_name]
                created_cats += 1

        if title in existing_products:
            skipped += 1
            continue

        price = price_for(source_price)
        stock = random.randint(10, 100)
        db.add_product(
            title=title,
            description=desc,
            price=price,
            photo_file_id=None,
            delivery_content=None,
            category_id=cat_ids[cat_name],
            stock=stock,
        )
        existing_products.add(title)
        created_products += 1

    print(
        f"Seed complete. Categories created: {created_cats}. "
        f"Products created: {created_products}. Skipped (already existed): "
        f"{skipped}."
    )


if __name__ == "__main__":
    db.init()
    run()
