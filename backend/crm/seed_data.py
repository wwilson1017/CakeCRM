"""CakeCRM — CRM demo seed data.

Populates the CRM with fictional example data so new users can see what a
populated CRM looks like before entering their own. Ported from chatty's
crm_lite seed (fully fictional small-business personas), translated to Postgres
(%s placeholders on a caller-supplied connection, an all-tables-empty idempotence
guard, and setval() to advance the SERIAL sequences past the fixed demo ids), and
extended with companies (issue #13) so the company detail-page rollups demo.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _ts(days_ago: int, hour: int = 10) -> str:
    """UTC timestamp N days in the past, with an explicit +00 offset so the
    TIMESTAMPTZ columns parse as UTC regardless of the server session timezone."""
    dt = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S+00")


def seed_demo_data(conn) -> bool:
    """Insert example data into a fresh CRM. Runs on the caller's connection/
    transaction. Returns True if data was seeded, False if the CRM already has
    data (idempotent — checks every CRM table, not just contacts, since the demo
    rows use fixed ids that could collide with pre-existing deals/tasks)."""
    cur = conn.cursor()

    cur.execute(
        """SELECT (SELECT COUNT(*) FROM companies)
                + (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM tasks)
                + (SELECT COUNT(*) FROM activity_log)
                + (SELECT COUNT(*) FROM crm_chatter)
                + (SELECT COUNT(*) FROM crm_field_values)"""
    )
    if cur.fetchone()[0] > 0:
        logger.info("CRM already has data — skipping demo seed")
        return False

    # ── Companies ────────────────────────────────────────────────────────────
    # Seeded before contacts/deals so the FK links below resolve. Fixed ids 1-6
    # mirror the distinct non-empty company names on the demo contacts.
    cur.executemany(
        """INSERT INTO companies
           (id, name, domain, industry, phone, address, notes, source, status, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [
            (1, "The Green Table", "thegreentable.com", "Restaurant", "(555) 234-5678",
             "128 Market St", "Farm-to-table restaurant. Weekly bread and pastry buyer.",
             "referral", "active", _ts(45), _ts(2)),
            (2, "Park Family Farms", "parkfamilyfarms.com", "Agriculture", "(555) 456-7890",
             "County Road 12", "Organic produce supplier. Delivers Tuesdays and Fridays.",
             "referral", "active", _ts(60), _ts(5)),
            (3, "TechStart Inc", "techstart.io", "Technology", "(555) 567-8901",
             "900 Innovation Way", "Software startup. Weekly team lunches and monthly events.",
             "cold_call", "active", _ts(14), _ts(3)),
            (4, "Hometown Gifts", "hometowngifts.com", "Retail", "(555) 678-9012",
             "45 Main St", "Gift shop. Custom gift boxes for the holiday season.",
             "social", "active", _ts(20), _ts(4)),
            (5, "City Properties", "cityproperties.com", "Real Estate", "(555) 890-1234",
             "700 Center Ave", "Manages our retail space lease.",
             "other", "active", _ts(90), _ts(15)),
            (6, "Mike's Meals", "mikesmeals.com", "Food Service", "(555) 901-2345",
             "Food truck (mobile)", "Popular food truck. Discussed a collab pop-up event.",
             "event", "active", _ts(25), _ts(8)),
        ],
    )

    # ── Contacts ─────────────────────────────────────────────────────────────
    cur.executemany(
        """INSERT INTO contacts
           (id, name, email, phone, company, title, source, status, tags, notes, created_at, updated_at, company_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [
            (1, "Maria Santos", "maria@thegreentable.com", "(555) 234-5678",
             "The Green Table", "Chef & Owner", "referral", "active",
             "wholesale,restaurant", "Weekly bread and pastry buyer. Prefers sourdough and ciabatta.",
             _ts(45), _ts(2), 1),

            (2, "James Chen", "james@chenweddings.com", "(555) 345-6789",
             "", "Wedding Planner", "event", "active",
             "events,weddings", "Met at the Spring Bridal Expo. Plans 15-20 weddings per year.",
             _ts(30), _ts(1), None),

            (3, "Lisa Park", "lisa@parkfamilyfarms.com", "(555) 456-7890",
             "Park Family Farms", "Owner", "referral", "active",
             "supplier,organic", "Organic produce supplier. Delivers Tuesdays and Fridays.",
             _ts(60), _ts(5), 2),

            (4, "David Kim", "david.kim@techstart.io", "(555) 567-8901",
             "TechStart Inc", "Office Manager", "cold_call", "active",
             "corporate,catering", "Interested in weekly team lunches and monthly events.",
             _ts(14), _ts(3), 3),

            (5, "Rachel Torres", "rachel@hometowngifts.com", "(555) 678-9012",
             "Hometown Gifts", "Buyer", "social", "active",
             "wholesale,retail", "Wants custom gift boxes for the holiday season.",
             _ts(20), _ts(4), 4),

            (6, "Tom Bradley", "tom@tastytravels.blog", "(555) 789-0123",
             "", "Food Blogger", "website", "active",
             "media,influencer", "12K followers. Wants to feature us in a local eats roundup.",
             _ts(10), _ts(6), None),

            (7, "Angela Reeves", "angela@cityproperties.com", "(555) 890-1234",
             "City Properties", "Property Manager", "other", "inactive",
             "landlord", "Manages our retail space lease. Renewal due in 3 months.",
             _ts(90), _ts(15), 5),

            (8, "Mike Okafor", "mike@mikesmeals.com", "(555) 901-2345",
             "Mike's Meals", "Head Chef", "event", "active",
             "collaboration,food-truck", "Runs a popular food truck. Discussed a collab pop-up event.",
             _ts(25), _ts(8), 6),
        ],
    )

    # ── Deals ────────────────────────────────────────────────────────────────
    cur.executemany(
        """INSERT INTO deals
           (id, contact_id, title, stage, value, expected_close_date, probability, currency, notes, created_at, updated_at, company_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [
            (1, 1, "Weekly bread supply — The Green Table", "won", 2400.00,
             _ts(10, 9)[:10], 100, "USD",
             "6-month contract for sourdough, ciabatta, and focaccia. Delivers Mon/Thu.",
             _ts(40), _ts(10), 1),

            (2, 2, "Chen-Williams wedding cake", "proposal", 1800.00,
             _ts(-30, 9)[:10], 70, "USD",
             "Three-tier floral design. Tasting scheduled for next week.",
             _ts(20), _ts(1), None),

            (3, 4, "TechStart monthly catering", "negotiation", 5200.00,
             _ts(-21, 9)[:10], 60, "USD",
             "Weekly team lunches (40 people) plus one monthly event.",
             _ts(12), _ts(3), 3),

            (4, 5, "Holiday gift box wholesale", "qualified", 3500.00,
             _ts(-60, 9)[:10], 40, "USD",
             "150 custom boxes with cookies, brownies, and seasonal treats.",
             _ts(18), _ts(4), 4),

            (5, 3, "Farmers market booth supplies", "lead", 600.00,
             _ts(-14, 9)[:10], 20, "USD",
             "Seasonal jams and baked goods for the Saturday farmers market booth.",
             _ts(7), _ts(6), 2),

            (6, None, "Summer menu tasting event", "lead", 900.00,
             _ts(-45, 9)[:10], 15, "USD",
             "Open tasting event to launch the new summer menu. Venue TBD.",
             _ts(5), _ts(5), None),

            (7, 8, "Food truck collab — Mike's Meals", "lost", 1200.00,
             _ts(5, 9)[:10], 0, "USD",
             "Joint pop-up didn't work out — scheduling conflicts. Revisit in fall.",
             _ts(22), _ts(8), 6),
        ],
    )

    # ── Tasks ────────────────────────────────────────────────────────────────
    cur.executemany(
        """INSERT INTO tasks
           (id, contact_id, deal_id, title, description, due_date, completed, priority, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [
            (1, 2, 2, "Follow up on wedding cake tasting",
             "Confirm date and flavor preferences with James.",
             _ts(-1, 9)[:10], 0, "high", _ts(5), _ts(1)),

            (2, 4, 3, "Send revised catering menu",
             "Include vegetarian and gluten-free options as requested.",
             _ts(0, 9)[:10], 0, "high", _ts(4), _ts(2)),

            (3, 5, 4, "Order custom gift boxes",
             "Get samples from two packaging suppliers for Rachel to review.",
             _ts(-5, 9)[:10], 0, "medium", _ts(8), _ts(4)),

            (4, 7, None, "Review lease renewal terms",
             "Angela sent the draft. Check the rent increase clause.",
             _ts(-3, 9)[:10], 0, "medium", _ts(6), _ts(6)),

            (5, None, 5, "Prep samples for farmers market",
             "Bake sample-size jars of strawberry and peach jam.",
             _ts(-7, 9)[:10], 0, "low", _ts(3), _ts(3)),

            (6, None, None, "Update social media with new menu photos",
             "Post the summer menu preview on Instagram and Facebook.",
             _ts(-10, 9)[:10], 0, "low", _ts(2), _ts(2)),

            (7, 3, None, "Call Lisa about seasonal produce",
             "Discuss summer fruit availability and pricing.",
             _ts(2, 9)[:10], 0, "medium", _ts(5), _ts(3)),

            (8, 1, 1, "Invoice The Green Table — April",
             "Monthly invoice for bread supply contract.",
             _ts(3, 9)[:10], 1, "high", _ts(10), _ts(3)),
        ],
    )

    # ── Activity log ───────────────────────────────────────────────────────────
    cur.executemany(
        """INSERT INTO activity_log
           (id, contact_id, deal_id, activity, note, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        [
            (1, 1, 1, "meeting",
             "Quarterly check-in — Maria is happy with bread quality. Wants to add croissants.",
             _ts(2, 11)),

            (2, 2, 2, "call",
             "Discussed gluten-free tier options for the wedding cake.",
             _ts(1, 14)),

            (3, 4, 3, "email",
             "Sent sample catering menu with pricing for weekly lunches.",
             _ts(3, 9)),

            (4, 4, 3, "call",
             "David wants to add a monthly all-hands catering package. Sending revised quote.",
             _ts(2, 15)),

            (5, 5, 4, "email",
             "Sent photos of sample gift box designs. Rachel loves the rustic kraft option.",
             _ts(4, 10)),

            (6, 3, None, "call",
             "Lisa confirmed organic strawberries available through August. Locking in price.",
             _ts(5, 11)),

            (7, 6, None, "meeting",
             "Tom visited the bakery for a tasting. Great conversation — review goes live next week.",
             _ts(6, 13)),

            (8, 7, None, "email",
             "Angela sent the lease renewal draft. 4% increase — need to review.",
             _ts(7, 9)),

            (9, 8, 7, "note",
             "Mike's schedule too packed for summer collab. Will revisit in September.",
             _ts(8, 16)),

            (10, 2, 2, "email",
             "Sent wedding cake portfolio with three design options. James forwarded to the couple.",
             _ts(10, 10)),

            (11, 1, 1, "note",
             "Increased Thursday delivery to 30 loaves — Green Table is growing fast.",
             _ts(12, 8)),
        ],
    )

    # ── Chatter / notes ──────────────────────────────────────────────────────
    # Editable notes threaded on deals + contacts, shown alongside the activity
    # timeline. Polymorphic (entity_type, entity_id) — see chatter_service.
    cur.executemany(
        """INSERT INTO crm_chatter
           (id, entity_type, entity_id, message, created_at)
           VALUES (%s, %s, %s, %s, %s)""",
        [
            (1, "deal", 3,
             "David is comparing us against two other caterers — price is the sticking point. "
             "Lead with the dedicated account manager and flexible weekly menu swaps.",
             _ts(2, 12)),

            (2, "deal", 2,
             "Couple is leaning toward buttercream over fondant. Confirm the finish at the tasting.",
             _ts(1, 15)),

            (3, "deal", 4,
             "Rachel needs final box counts by Nov 1 to hit holiday production — flag early if we slip.",
             _ts(3, 11)),

            (4, "contact", 1,
             "Maria hinted at a second Green Table location opening in the fall — could double the "
             "standing bread order. Worth a proactive proposal.",
             _ts(2, 13)),
        ],
    )

    # Advance the SERIAL sequences past the fixed demo ids so the next real
    # insert gets a fresh id (fresh DB only — the empty guard above ensures this).
    for table in ("companies", "contacts", "deals", "tasks", "activity_log", "crm_chatter"):
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"
        )

    logger.info("CRM demo data seeded: 6 companies, 8 contacts, 7 deals, 8 tasks, 11 activities")
    return True
