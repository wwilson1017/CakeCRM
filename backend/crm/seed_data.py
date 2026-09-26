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


# Position of `completed` in the todo seed tuples below.
_TODO_COMPLETED_IDX = 6


def _todo_seed_params(rows: list[tuple]) -> list[tuple]:
    """Append two extra copies of each row's `completed` flag.

    The todo INSERT derives `status` and `completed_at` from `completed` with two SQL
    CASE expressions, and psycopg2 placeholders are positional — so the value has to be
    supplied once per use. Deriving beats hand-writing the status into every literal:
    the two columns can never disagree, which is exactly what the coherence CHECK
    added in #70 requires.
    """
    return [(*row, row[_TODO_COMPLETED_IDX], row[_TODO_COMPLETED_IDX]) for row in rows]


def seed_demo_data(conn) -> bool:
    """Insert example data into a fresh CRM. Runs on the caller's connection/
    transaction. Returns True if data was seeded, False if the CRM already has
    data (idempotent — checks every CRM table, not just contacts, since the demo
    rows use fixed ids that could collide with pre-existing deals/todos)."""
    cur = conn.cursor()

    cur.execute(
        """SELECT (SELECT COUNT(*) FROM companies)
                + (SELECT COUNT(*) FROM contacts)
                + (SELECT COUNT(*) FROM deals)
                + (SELECT COUNT(*) FROM todos)
                + (SELECT COUNT(*) FROM todo_projects)
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

            (9, "Priya Nair", "priya@lakesidecoffee.com", "(555) 012-3456",
             "Lakeside Coffee", "Owner", "referral", "active",
             "wholesale,cafe", "Two-location cafe. Wants a daily pastry case supplied before 6am.",
             _ts(16), _ts(2), None),

            (10, "Owen Blake", "owen@blakeevents.com", "(555) 123-4567",
             "Blake & Daughters Events", "Events Director", "event", "active",
             "events,corporate", "Plans corporate holiday parties for a dozen local firms.",
             _ts(11), _ts(3), None),

            (11, "Hannah Lee", "hannah@northsideschools.org", "(555) 234-5679",
             "Northside Schools", "Nutrition Coordinator", "website", "active",
             "schools,fundraiser", "Runs the district's fall fundraiser. Asked about cookie boxes.",
             _ts(9), _ts(4), None),
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
             _ts(28), _ts(16), 4),

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

            (8, 1, "Croissant add-on — The Green Table", "qualified", 1400.00,
             _ts(-20, 9)[:10], 45, "USD",
             "Maria wants croissants on the Thursday delivery. Pricing a 60-a-week run.",
             _ts(9), _ts(2), 1),

            (9, 4, "TechStart all-hands dessert bar", "proposal", 2600.00,
             _ts(-12, 9)[:10], 65, "USD",
             "Quarterly all-hands for 120 people. Dessert bar plus coffee service.",
             _ts(8), _ts(1), 3),

            (10, 6, "Local eats roundup feature", "lead", 300.00,
             _ts(-30, 9)[:10], 10, "USD",
             "Sponsored spot in Tom's roundup. Tasting box in exchange for the feature.",
             _ts(6), _ts(6), None),

            (11, 2, "Rivera anniversary cake", "qualified", 650.00,
             _ts(-25, 9)[:10], 35, "USD",
             "Two-tier lemon and raspberry for a 40th anniversary. James is the planner.",
             _ts(7), _ts(2), None),

            (12, 5, "Spring gift box run", "lead", 2200.00,
             _ts(-75, 9)[:10], 15, "USD",
             "Rachel floated a spring follow-up to the holiday boxes. Nothing scoped yet.",
             _ts(4), _ts(4), 4),

            (13, 8, "Fall pop-up — Mike's Meals", "proposal", 1500.00,
             _ts(-40, 9)[:10], 55, "USD",
             "Second try at the collab, September weekends. Mike has the dates this time.",
             _ts(6), _ts(1), 6),

            (14, 9, "Lakeside Coffee pastry case", "negotiation", 3100.00,
             _ts(-9, 9)[:10], 70, "USD",
             "Daily case for two cafes, delivered before 6am. Haggling over the Sunday run.",
             _ts(15), _ts(0), None),

            (15, 3, "Jam co-pack — Park Family Farms", "won", 1900.00,
             _ts(4, 9)[:10], 100, "USD",
             "We jar Lisa's strawberry and peach under her label. 400 jars for the season.",
             _ts(30), _ts(4), 2),

            (16, 10, "Corporate holiday party desserts", "proposal", 2800.00,
             _ts(-18, 9)[:10], 60, "USD",
             "Plated desserts for a 90-seat dinner. Owen wants a menu draft this week.",
             _ts(10), _ts(2), None),

            (17, 11, "School fundraiser cookie boxes", "lead", 800.00,
             _ts(-35, 9)[:10], 20, "USD",
             "Northside's fall fundraiser. Hannah asked for per-box pricing at three volumes.",
             _ts(3), _ts(3), None),
        ],
    )

    # ── Todos ────────────────────────────────────────────────────────────────
    # `status`/`completed_at` are DERIVED from each row's `completed` flag rather than
    # written into the literals below: since #70 the two columns are bound by a CHECK
    # constraint, so a seed that set one without the other would fail on first run.
    # _todo_seed_params appends the two extra copies of `completed` the CASEs need.
    cur.executemany(
        """INSERT INTO todos
           (id, contact_id, deal_id, title, description, due_date, completed, priority,
            created_at, updated_at, status, completed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   CASE WHEN %s = 1 THEN 'done' ELSE 'next_action' END,
                   CASE WHEN %s = 1 THEN now() END)""",
        _todo_seed_params([
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

            (9, 11, 17, "Send fundraiser pricing sheet to Hannah",
             "Per-box pricing at 100, 250 and 500 boxes.",
             _ts(-1, 9)[:10], 0, "medium", _ts(3), _ts(3)),

            (10, 9, 14, "Confirm pastry case delivery window with Priya",
             "She needs the case stocked before the 6am open at both cafes.",
             _ts(0, 9)[:10], 0, "high", _ts(4), _ts(1)),

            (11, 10, 16, "Draft dessert menu for the Blake holiday party",
             "Three plated options plus one vegan. Owen wants it before Friday.",
             _ts(2, 9)[:10], 0, "high", _ts(6), _ts(2)),

            (12, 4, 9, "Book the tasting room for the TechStart all-hands",
             "David wants to preview the dessert bar with two colleagues.",
             _ts(-3, 9)[:10], 0, "medium", _ts(5), _ts(2)),

            (13, None, None, "Renew food handler certificates",
             "Two expire this month. Book the online course for both.",
             _ts(4, 9)[:10], 0, "low", _ts(12), _ts(5)),

            (14, None, None, "Ask Rachel how many holiday gift boxes she needs",
             "", "", 0, "medium", _ts(1), _ts(1)),

            (15, None, None, "Book the farmers market stall for October",
             "", "", 0, "medium", _ts(1), _ts(1)),

            (16, None, None, "Get a quote on the new display fridge",
             "", "", 0, "low", _ts(0), _ts(0)),
        ]),
    )
    # The last three are raw captures that have not been triaged yet: they sit in the
    # Inbox until someone says what kind of action they are (GTD, #70). `completed = 0`
    # keeps the CHECK constraint happy; only the status moves.
    cur.execute("UPDATE todos SET status = 'inbox', source = 'capture_web' WHERE id IN (14, 15, 16)")
    # Two deals a rep has marked hot (#125). Deal 4 has had no touch of any kind —
    # `updated_at`, activity or note — for over DEFAULT_DEAL_STALE_DAYS, so it reaches
    # the Today panel's second rung (#131); deal 14 was touched yesterday, so it shows
    # only in the panel's expanded tail. Both states are worth having on screen.
    cur.execute("UPDATE deals SET deal_temperature = 'hot' WHERE id IN (4, 14)")

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
             _ts(16, 10)),

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

            (12, 9, 14, "call",
             "Priya is fine on price; the sticking point is a Sunday delivery. Offered a 7am Sunday drop.",
             _ts(1, 9)),

            (13, 10, 16, "email",
             "Owen sent the dinner headcount (90) and a note that the client's CEO is vegan.",
             _ts(2, 10)),

            (14, 4, 9, "meeting",
             "Walked David through the dessert bar layout. He wants a coffee station added.",
             _ts(3, 14)),

            (15, 3, 15, "note",
             "Jam co-pack signed. First 200 jars go out with the Tuesday produce run.",
             _ts(4, 11)),

            (16, 11, 17, "email",
             "Hannah confirmed the fundraiser dates: order forms go home Oct 6, pickup Oct 24.",
             _ts(2, 16)),
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
             _ts(15, 11)),

            (4, "contact", 1,
             "Maria hinted at a second Green Table location opening in the fall — could double the "
             "standing bread order. Worth a proactive proposal.",
             _ts(2, 13)),

            (5, "deal", 14,
             "If Sunday is the blocker, propose Saturday double-stock with a Sunday morning top-up "
             "from the downtown cafe's own kitchen.",
             _ts(1, 10)),

            (6, "deal", 16,
             "Vegan option must be a real dessert, not a fruit plate — Owen has been burned before.",
             _ts(2, 11)),
        ],
    )

    # Advance the SERIAL sequences past the fixed demo ids so the next real
    # insert gets a fresh id (fresh DB only — the empty guard above ensures this).
    for table in ("companies", "contacts", "deals", "todos", "activity_log", "crm_chatter"):
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"
        )

    logger.info("CRM demo data seeded: 6 companies, 11 contacts, 17 deals, 16 todos, 16 activities")
    return True
