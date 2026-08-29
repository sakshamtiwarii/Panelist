"""
Panelist — dataset generator.

Produces realistic, seeded companies/students/rooms data for the
placement week scheduling problem.

Usage:
    python -m generator.generate --seed 42 --companies 35 --students 800 \
        --rooms 20 --days 4
    python -m generator.generate --seed 42 --students 40 --companies 6 \
        --rooms 3 --out ./data/small
    python -m generator.generate --seed 42 --load-factor 1.15 \
        --out ./data/oversubscribed

Realism requirements (see PLACEMENT_SCHEDULER_GUIDE.md section 2.1):
- Company shortlist sizes are heavy-tailed, not uniform: a few mass recruiters
  shortlist 280-400, a mid band sits at 60-150, and a long tail runs 18-45.
  Sizes are banded by tier with Pareto jitter inside each band, which matches
  the bimodal shape the spec describes better than one smooth curve does.
- High-CGPA students appear on many overlapping shortlists. Shortlist
  membership is sampled with a weight superlinear in (cgpa - cutoff), so the
  top of the cohort is contended over by many companies at once. This is what
  creates the actual scheduling conflicts the assignment is about.
- Priority tiers correlate with scheduling day: mass recruiters (large
  shortlists, low cutoffs, many panels) cluster on Day 1; niche high-cutoff
  companies with small headcounts land later in the week.

Instance difficulty: at natural sizes (no --load-factor) 800 students over
20 rooms x 4 days is oversubscribed roughly 2.5x -- which is the honest answer
to "can you interview every shortlisted student in one placement week", and is
why the spec says infeasibility "will" happen. Pass --load-factor to scale
demand down (0.9 = hard but fully solvable) for a demo where every interview
lands. Scaling is multiplicative, so the size shape is preserved either way.
"""

import argparse
import json
import math
import os
import random
import statistics

# --- Time grid -------------------------------------------------------------
# 15-minute base slots; interview durations are multiples of this.
SLOT_MINUTES = 15
DAY_START_MIN = 9 * 60          # 09:00
DAY_END_MIN = 17 * 60           # 17:00
LUNCH_START_MIN = 13 * 60       # 13:00
LUNCH_END_MIN = 14 * 60         # 14:00

BRANCHES = ["CSE", "IT", "ECE", "EEE", "MECH", "CIVIL", "CHEM"]
# Companies skew toward hiring from these; used for mild branch affinity.
TECH_BRANCHES = {"CSE", "IT", "ECE"}

# Ordered by expected campus hiring volume, because tier is assigned by RANK:
# the first ~10% become the mass recruiters, the next 30% mid-size, the rest
# niche. That ordering makes the generated structure match how an Indian
# placement week actually runs — the IT services firms mass-hire on Day 1 with
# low cutoffs and many panels, while the high-paying product and quant firms
# take a handful of top-CGPA students later in the week.
#
# Real names are used because they carry that structure intuitively: a reader
# knows what a TCS drive looks like versus a Jane Street one. The data itself
# — students, shortlists, cutoffs, panel counts — is entirely synthetic.
COMPANY_NAMES = [
    # Mass recruiters: very large shortlists, low cutoffs, many panels.
    "TCS", "Infosys", "Cognizant", "Wipro",
    # Mid-size: sizeable drives, moderate cutoffs.
    "Accenture", "Capgemini", "HCLTech", "Tech Mahindra", "Amazon",
    "Microsoft", "Oracle", "SAP", "Cisco", "Adobe",
    # Niche / high-paying: small headcounts, high cutoffs, few panels.
    "Salesforce", "Qualcomm", "NVIDIA", "Intel", "Google", "Atlassian",
    "Uber", "Flipkart", "Zomato", "Swiggy", "Razorpay", "PhonePe",
    "Zoho", "Freshworks", "Anthropic", "OpenAI", "Databricks", "Rubrik",
    "Jane Street", "D. E. Shaw", "Optiver",
    # Spare names, used only when --companies exceeds 35.
    "Tower Research", "Goldman Sachs", "Morgan Stanley", "Palantir",
    "Stripe", "Figma", "Snowflake", "Confluent", "MongoDB", "Datadog",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Generate Panelist dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--companies", type=int, default=35)
    parser.add_argument("--students", type=int, default=800)
    parser.add_argument("--rooms", type=int, default=20)
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument(
        "--load-factor",
        type=float,
        default=None,
        help="Optional: scale shortlists so interview demand hits this "
             "fraction of theoretical room capacity. Omit for natural, "
             "spec-realistic sizes (oversubscribed). 0.9 = hard but solvable.",
    )
    parser.add_argument("--out", type=str, default="./data")
    return parser.parse_args()


# --- Time grid helpers -----------------------------------------------------

def usable_slots_per_day():
    """Slot indices within a day, excluding the lunch window."""
    total = (DAY_END_MIN - DAY_START_MIN) // SLOT_MINUTES
    lunch_from = (LUNCH_START_MIN - DAY_START_MIN) // SLOT_MINUTES
    lunch_to = (LUNCH_END_MIN - DAY_START_MIN) // SLOT_MINUTES
    return [s for s in range(total) if not (lunch_from <= s < lunch_to)]


def slot_label(slot_index):
    minutes = DAY_START_MIN + slot_index * SLOT_MINUTES
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_config(days):
    slots = usable_slots_per_day()
    return {
        "days": days,
        "slot_minutes": SLOT_MINUTES,
        "day_start": slot_label(0),
        "day_end": f"{DAY_END_MIN // 60:02d}:{DAY_END_MIN % 60:02d}",
        "lunch": [
            f"{LUNCH_START_MIN // 60:02d}:{LUNCH_START_MIN % 60:02d}",
            f"{LUNCH_END_MIN // 60:02d}:{LUNCH_END_MIN % 60:02d}",
        ],
        "usable_slots_per_day": slots,
        "slots_per_day_count": len(slots),
        # The raw grid (lunch included) and the day's origin. Written here so
        # the dataset carries its whole time model: every reader derives slot
        # arithmetic from config via scheduler.timegrid instead of keeping a
        # private copy that a change to the hours above would silently
        # invalidate.
        "slots_per_day_raw": (DAY_END_MIN - DAY_START_MIN) // SLOT_MINUTES,
        "day_start_minutes": DAY_START_MIN,
    }


# --- Companies -------------------------------------------------------------

def generate_companies(rng, n, days):
    """Companies, ranked by shortlist size, with tier/day/cutoff correlated.

    Sizes are bimodal by tier rather than a single smooth curve, because that
    is what the spec actually describes and what a real placement week looks
    like: a few mass recruiters shortlisting 300+, a mid band, then a long
    tail at 20-40. Pareto jitter within each band keeps sizes from being
    suspiciously round.
    """
    names = (COMPANY_NAMES * (n // len(COMPANY_NAMES) + 1))[:n]

    def banded(lo, hi):
        """Draw within [lo, hi], skewed toward lo (Pareto tail, clipped)."""
        v = min(rng.paretovariate(1.6) - 1.0, 3.0) / 3.0
        return lo + v * (hi - lo)

    companies = []
    for rank, name in enumerate(names):
        pct = rank / max(1, n - 1)  # 0.0 = biggest recruiter, 1.0 = smallest

        if pct < 0.10:
            tier, day_bias = 1, 0                    # mass recruiters -> Day 1
            raw_size = banded(280, 400)
            cutoff = rng.uniform(6.0, 6.8)
            panels = rng.randint(8, 15)
            duration = rng.choice([15, 30])
        elif pct < 0.40:
            tier, day_bias = 2, rng.randint(0, 1)    # mid-size -> Days 1-2
            raw_size = banded(60, 150)
            cutoff = rng.uniform(6.8, 7.6)
            panels = rng.randint(3, 7)
            duration = rng.choice([30, 30, 45])
        else:
            # Niche companies land later in the week — but a one-day week has
            # no "later", and randint(1, 0) is an error rather than a choice.
            tier, day_bias = 3, rng.randint(min(1, days - 1), days - 1)
            raw_size = banded(18, 45)
            cutoff = rng.uniform(7.6, 9.0)
            panels = rng.randint(1, 3)
            duration = rng.choice([30, 45, 45])

        companies.append({
            "id": f"C{rank:03d}",
            "name": name,
            "tier": tier,
            "preferred_day": min(day_bias, days - 1),
            "cgpa_cutoff": round(cutoff, 2),
            "panel_count": panels,
            "interview_minutes": duration,
            "duration_slots": duration // SLOT_MINUTES,
            "tech_focused": rng.random() < (0.8 if tier <= 2 else 0.5),
            "_raw_size": raw_size,
        })
    return companies


def scale_shortlist_sizes(companies, rooms, days, config, load_factor):
    """Optionally scale shortlist sizes to hit a target room-capacity load.

    With load_factor None (the default) sizes are left at their natural,
    spec-realistic values -- which at 20 rooms x 4 days is oversubscribed.
    That is the honest instance: the spec says infeasibility "will" happen,
    and a realistic placement week genuinely cannot interview everyone.
    Pass an explicit --load-factor to scale demand down to a fully solvable
    instance for demos. Scaling is multiplicative, so the size *shape* is
    preserved -- only the difficulty moves.
    """
    capacity_slots = rooms * config["slots_per_day_count"] * days
    demand_slots = sum(c["_raw_size"] * c["duration_slots"] for c in companies)

    if load_factor is None or not demand_slots:
        scale = 1.0
    else:
        scale = (load_factor * capacity_slots) / demand_slots

    for c in companies:
        c["shortlist_size"] = max(5, round(c["_raw_size"] * scale))
        del c["_raw_size"]
    return capacity_slots


# --- Students --------------------------------------------------------------

def generate_students(rng, n):
    """Cohort with a right-skewed CGPA distribution (not uniform).

    Shortlist membership is assigned separately by `assign_shortlists`, so the
    company list is not needed here.
    """
    students = []
    for i in range(n):
        cgpa = rng.gauss(7.2, 0.9)
        cgpa = max(5.0, min(10.0, cgpa))
        branch = rng.choices(
            BRANCHES, weights=[28, 16, 18, 12, 14, 6, 6], k=1
        )[0]
        students.append({
            "id": f"S{i:04d}",
            "cgpa": round(cgpa, 2),
            "branch": branch,
            "shortlisted_by": [],
        })
    return students


def assign_shortlists(rng, students, companies):
    """Sample each company's shortlist, weighted superlinearly by CGPA.

    This is the correlation that makes the instance hard: the same high-CGPA
    students get drawn onto many shortlists, so their interviews compete for
    the same slots. Uniform sampling here would make the problem trivial and
    is explicitly graded down (guide section 2.1).
    """
    by_id = {s["id"]: s for s in students}

    for c in companies:
        eligible = [s for s in students if s["cgpa"] >= c["cgpa_cutoff"]]
        if not eligible:
            c["shortlist"] = []
            continue

        weights = []
        for s in eligible:
            # Superlinear in headroom above the cutoff -> top students dominate.
            headroom = s["cgpa"] - c["cgpa_cutoff"] + 0.1
            w = headroom ** 1.8
            if c["tech_focused"] and s["branch"] in TECH_BRANCHES:
                w *= 2.0
            weights.append(w)

        take = min(c["shortlist_size"], len(eligible))
        chosen = _weighted_sample_without_replacement(rng, eligible, weights, take)

        c["shortlist"] = [s["id"] for s in chosen]
        c["shortlist_size"] = len(c["shortlist"])
        for s in chosen:
            by_id[s["id"]]["shortlisted_by"].append(c["id"])


def _weighted_sample_without_replacement(rng, items, weights, k):
    """Efraimidis-Spirakis: key = u^(1/w), take the k largest."""
    keyed = []
    for item, w in zip(items, weights):
        if w <= 0:
            continue
        keyed.append((rng.random() ** (1.0 / w), item))
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [item for _, item in keyed[:k]]


# --- Rooms -----------------------------------------------------------------

def generate_rooms(rng, n, days, config):
    """Interview rooms, some with a blocked/reserved window for realism."""
    slots = config["usable_slots_per_day"]
    rooms = []
    for i in range(n):
        blocked = []
        if rng.random() < 0.25:
            day = rng.randrange(days)
            start = rng.choice(slots[:len(slots) // 2])
            length = rng.randint(4, 12)
            blocked.append({
                "day": day,
                "from_slot": start,
                "to_slot": start + length,
                "reason": rng.choice([
                    "reserved for pre-placement talk",
                    "AV equipment maintenance",
                    "reserved by department",
                ]),
            })
        rooms.append({
            "id": f"R{i:02d}",
            "name": f"Room {i + 1}",
            "blocked_windows": blocked,
        })
    return rooms


# --- Conflict-density readout ---------------------------------------------

def _pearson(xs, ys):
    if len(xs) < 2:
        return 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def conflict_density_report(companies, students, rooms, config, capacity_slots):
    """Confirm the instance is actually hard BEFORE any solver code runs.

    Checks the three realism properties the spec grades, plus the capacity
    pressure points the scheduler will have to resolve.
    """
    sizes = sorted((c["shortlist_size"] for c in companies), reverse=True)
    counts = [len(s["shortlisted_by"]) for s in students]
    contended = [s for s in students if len(s["shortlisted_by"]) >= 5]

    demand_slots = sum(
        c["shortlist_size"] * c["duration_slots"] for c in companies
    )
    total_interviews = sum(c["shortlist_size"] for c in companies)

    # Per-company panel throughput: a company can only run panel_count
    # interviews concurrently, so this is a hard ceiling independent of rooms.
    panel_bound = []
    for c in companies:
        ceiling = (
            c["panel_count"] * config["slots_per_day_count"] * config["days"]
        ) // c["duration_slots"]
        if c["shortlist_size"] > ceiling:
            panel_bound.append((c, c["shortlist_size"] - ceiling))

    # Company pairs sharing the most students -- these fight over the same slots.
    membership = {c["id"]: set(c["shortlist"]) for c in companies}
    pairs = []
    ids = [c["id"] for c in companies]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            overlap = len(membership[ids[i]] & membership[ids[j]])
            if overlap:
                pairs.append((overlap, ids[i], ids[j]))
    pairs.sort(reverse=True)

    name_of = {c["id"]: c["name"] for c in companies}
    load = demand_slots / capacity_slots if capacity_slots else 0.0

    lines = []
    add = lines.append
    add("=" * 66)
    add("CONFLICT DENSITY REPORT")
    add("=" * 66)
    add("")
    add(f"  Students {len(students):>5}   Companies {len(companies):>3}   "
        f"Rooms {len(rooms):>3}   Days {config['days']}")
    add(f"  Interviews to schedule: {total_interviews}")
    add("")
    add("-- Shortlist size distribution (power-law check) ------------------")
    add(f"  max {sizes[0]}   median {int(statistics.median(sizes))}   min {sizes[-1]}")
    add(f"  top 5: {sizes[:5]}")
    add(f"  companies with 200+ shortlisted: {sum(1 for s in sizes if s >= 200)}")
    add(f"  companies with <50 shortlisted:  {sum(1 for s in sizes if s < 50)}")
    add("")
    add("-- CGPA / shortlist correlation (the conflict driver) -------------")
    r = _pearson([s["cgpa"] for s in students], counts)
    add(f"  Pearson r (cgpa vs shortlist count): {r:+.3f}")
    add(f"  max shortlists on one student: {max(counts) if counts else 0}")
    add(f"  students on 5+ shortlists: {len(contended)} "
        f"({100 * len(contended) / len(students):.1f}%)")
    add(f"  students on 0 shortlists:  {sum(1 for c in counts if c == 0)}")
    if contended:
        top = sorted(students, key=lambda s: -len(s["shortlisted_by"]))[:3]
        for s in top:
            add(f"    {s['id']}  cgpa {s['cgpa']}  "
                f"on {len(s['shortlisted_by'])} shortlists")
    add("")
    add("-- Capacity pressure ----------------------------------------------")
    add(f"  demand {demand_slots} slot-units / capacity {capacity_slots}  "
        f"-> load factor {load:.2f}")
    if load > 1.0:
        add(f"  ** OVERSUBSCRIBED by {demand_slots - capacity_slots} slot-units "
            f"-- instance is infeasible by construction.")
    if panel_bound:
        add(f"  ** {len(panel_bound)} companies exceed their own panel ceiling:")
        for c, short in sorted(panel_bound, key=lambda t: -t[1])[:5]:
            add(f"     {c['id']} {c['name']}: {c['shortlist_size']} interviews, "
                f"{c['panel_count']} panels -> short by {short}")
    else:
        add("  all companies fit within their own panel throughput ceiling")
    add("")
    add("-- Most contended company pairs (shared students) -----------------")
    for overlap, a, b in pairs[:5]:
        add(f"  {overlap:>4} shared   {name_of[a]} <-> {name_of[b]}")
    add("")
    add("=" * 66)
    return "\n".join(lines)


# --- Entrypoint ------------------------------------------------------------

def build_dataset(seed=42, companies=35, students=800, rooms=20, days=4,
                  load_factor=None):
    """Generate a dataset in memory. Returns (dataset, density_report).

    Kept separate from file writing and from argparse so the API can call it
    directly. Spawning `python generator/generate.py` instead would tie the
    endpoint to the process's working directory and reduce every failure to a
    truncated stderr string.
    """
    # Validated here, in one place: every downstream step assumes a non-empty
    # cohort, and without this the failure surfaces as an IndexError or a
    # ZeroDivisionError from deep inside the density report — a 500 with a
    # traceback where the real answer is "that instance has no companies".
    for label, value in (("companies", companies), ("students", students),
                         ("rooms", rooms), ("days", days)):
        if value < 1:
            raise ValueError(f"{label} must be at least 1, got {value}")
    if load_factor is not None and load_factor <= 0:
        raise ValueError(f"load_factor must be positive, got {load_factor}")

    rng = random.Random(seed)

    config = build_config(days)
    company_list = generate_companies(rng, companies, days)
    capacity_slots = scale_shortlist_sizes(
        company_list, rooms, days, config, load_factor
    )
    student_list = generate_students(rng, students)
    assign_shortlists(rng, student_list, company_list)
    room_list = generate_rooms(rng, rooms, days, config)

    dataset = {
        "meta": {
            "seed": seed,
            "load_factor_target": load_factor,
            "students": students,
            "companies": companies,
            "rooms": rooms,
            "days": days,
        },
        "config": config,
        "companies": company_list,
        "students": student_list,
        "rooms": room_list,
    }
    report = conflict_density_report(
        company_list, student_list, room_list, config, capacity_slots
    )
    return dataset, report


def write_dataset(out, dataset, report):
    """Write the dataset and its density report to `out/`."""
    os.makedirs(out, exist_ok=True)
    for name in ("companies", "students", "rooms"):
        with open(os.path.join(out, f"{name}.json"), "w") as f:
            json.dump(dataset[name], f, indent=2)
    with open(os.path.join(out, "dataset.json"), "w") as f:
        json.dump(dataset, f, indent=2)
    with open(os.path.join(out, "density_report.txt"), "w") as f:
        f.write(report + "\n")


def main():
    args = parse_args()
    dataset, report = build_dataset(
        seed=args.seed, companies=args.companies, students=args.students,
        rooms=args.rooms, days=args.days, load_factor=args.load_factor,
    )
    write_dataset(args.out, dataset, report)
    print(report)
    print(f"\nWrote dataset to {args.out}/ (seed={args.seed})")


if __name__ == "__main__":
    main()
