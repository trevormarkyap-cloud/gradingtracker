from flask import Flask, render_template, request, redirect, url_for, make_response
from sqlalchemy.exc import SQLAlchemyError
from models import db, Submission, Card, Customer
from datetime import datetime
from sqlalchemy import or_, func
import io
import csv

app = Flask(__name__)

# Config
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///grading.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()

# -------- HOME --------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))

# -------- SUBMISSIONS (ADMIN) --------

@app.route("/submissions")
def list_submissions():
    status = request.args.get("status")          # e.g. "At PSA", "Returned", etc.
    sort = request.args.get("sort", "newest")    # "newest", "oldest", "cards"
    page = request.args.get("page", 1, type=int) # for pagination

    query = Submission.query

    # Filter by status if provided
    if status and status != "All":
        query = query.filter(Submission.status == status)

    # Sorting
    if sort == "oldest":
        query = query.order_by(Submission.date_created.asc())
    elif sort == "cards":
        # Sort by number of cards (descending)
        query = (
            query
            .outerjoin(Submission.cards)   # uses relationship "cards"
            .group_by(Submission.id)
            .order_by(func.count(Card.id).desc())
        )
    else:
        # Default: newest first
        query = query.order_by(Submission.date_created.desc())

    # Pagination: 20 per page
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    submissions = pagination.items

    # Customer count per submission (unique customers in that batch)
    customer_counts = {}
    for s in submissions:
        customer_ids = {c.customer_id for c in s.cards if c.customer_id is not None}
        customer_counts[s.id] = len(customer_ids)

    return render_template(
        "submissions.html",
        submissions=submissions,
        current_status=status or "All",
        current_sort=sort,
        pagination=pagination,
        customer_counts=customer_counts,
    )

@app.route("/submissions/new", methods=["GET", "POST"])
def new_submission():
    if request.method == "POST":
        name = request.form["name"]
        company = request.form.get("company")       
        branch = request.form.get("branch")
        service_level = request.form.get("service_level")
        status = request.form.get("status", "Draft")

        submission = Submission(
            name=name,
            company=company,
            branch=branch,
            service_level=service_level,
            status=status,
        )
        db.session.add(submission)
        db.session.commit()
        return redirect(url_for("list_submissions"))

    return render_template("new_submission.html")

@app.route("/submissions/<int:submission_id>")
def submission_detail(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    cards = Card.query.filter_by(submission_id=submission.id).all()

    total_cards = len(cards)
    graded_cards = [c for c in cards if c.psa_grade]
    num_graded = len(graded_cards)
    num_pending = total_cards - num_graded
    num_sold = sum(1 for c in cards if (c.sale_price or 0) > 0)

    total_sale_revenue = sum(c.sale_price or 0 for c in cards)
    # Simple profit idea: sale revenue minus batch cost
    simple_profit = total_sale_revenue - (submission.total_cost or 0)

    stats = {
        "total_cards": total_cards,
        "num_graded": num_graded,
        "num_pending": num_pending,
        "num_sold": num_sold,
        "total_sale_revenue": total_sale_revenue,
        "simple_profit": simple_profit,
    }

    return render_template(
        "submission_detail.html",
        submission=submission,
        cards=cards,
        stats=stats
    )

@app.route("/submissions/<int:submission_id>/edit", methods=["GET", "POST"])
def edit_submission(submission_id):
    submission = Submission.query.get_or_404(submission_id)

    if request.method == "POST":

        submission.name = request.form.get("name")
        submission.company = request.form.get("company")
        submission.branch = request.form.get("branch") 
        submission.service_level = request.form.get("service_level")
        new_status = request.form.get("status")
        submission.status = new_status

        def to_float(value):
            try:
                return float(value) if value else 0.0
            except ValueError:
                return 0.0

        submission.total_cost = to_float(request.form.get("total_cost"))
        submission.total_collected = to_float(request.form.get("total_collected"))
        submission.total_declared_value = to_float(request.form.get("total_declared_value"))

        db.session.commit()
        return redirect(url_for("submission_detail", submission_id=submission.id))

    # GET request → show the form
    return render_template("submission_edit.html", submission=submission)

@app.route("/submissions/<int:submission_id>/cards/bulk-edit", methods=["GET", "POST"])
def bulk_edit_cards(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    cards = Card.query.filter_by(submission_id=submission.id).all()

    if request.method == "POST":
        def to_float(v):
            try:
                return float(v) if v else 0.0
            except ValueError:
                return 0.0

        for card in cards:
            prefix = f"card_{card.id}_"
            card.psa_grade = request.form.get(prefix + "psa_grade") or None
            card.sale_price = to_float(request.form.get(prefix + "sale_price"))

        db.session.commit()
        return redirect(url_for("submission_detail", submission_id=submission.id))

    return render_template("bulk_edit_cards.html", submission=submission, cards=cards)

# -------- CARDS (ADMIN) --------

@app.route("/cards/new", methods=["GET", "POST"])
def new_card():
    if request.method == "POST":
        submission_id = int(request.form["submission_id"])
        card = Card(
            submission_id=submission_id,
            game=request.form.get("game"),
            language=request.form.get("language"),
            set_name=request.form.get("set_name"),
            card_name=request.form.get("card_name"),
            card_number=request.form.get("card_number"),
            variant=request.form.get("variant"),
            declared_value=float(request.form.get("declared_value") or 0),
            pregrade_estimate=request.form.get("pregrade_estimate")
        )
        db.session.add(card)
        db.session.commit()
        return redirect(url_for("submission_detail", submission_id=submission_id))

    submissions = Submission.query.all()
    return render_template("new_card.html", submissions=submissions)

@app.route("/cards/<int:card_id>/edit", methods=["GET", "POST"])
def edit_card(card_id):
    card = Card.query.get_or_404(card_id)

    if request.method == "POST":
        card.game = request.form.get("game")
        card.language = request.form.get("language")
        card.set_name = request.form.get("set_name")
        card.card_name = request.form.get("card_name")
        card.card_number = request.form.get("card_number")
        card.variant = request.form.get("variant")

        # Safe float conversion
        def to_float(value):
            try:
                return float(value) if value else 0.0
            except ValueError:
                return 0.0

        card.declared_value = to_float(request.form.get("declared_value"))
        card.sale_price = to_float(request.form.get("sale_price"))

        card.pregrade_estimate = request.form.get("pregrade_estimate")
        card.psa_grade = request.form.get("psa_grade")

        # Optional grade_date (YYYY-MM-DD)
        grade_date_str = request.form.get("grade_date")
        if grade_date_str:
            try:
                card.grade_date = datetime.strptime(grade_date_str, "%Y-%m-%d")
            except ValueError:
                # If invalid, just ignore and keep old value
                pass

        db.session.commit()
        return redirect(url_for("submission_detail", submission_id=card.submission_id))

    return render_template("card_edit.html", card=card)  

@app.route("/cards/<int:card_id>/delete", methods=["POST"])
def delete_card(card_id):
    card = Card.query.get_or_404(card_id)
    submission_id = card.submission_id
    db.session.delete(card)
    db.session.commit()
    return redirect(url_for("submission_detail", submission_id=submission_id))

@app.route("/submissions/<int:submission_id>/cards/new", methods=["GET", "POST"])
def submission_add_card(submission_id):
    submission = Submission.query.get_or_404(submission_id)

    if request.method == "POST":
        def to_float(v):
            try:
                return float(v) if v else 0.0
            except ValueError:
                return 0.0

        card = Card(
            submission_id=submission.id,
            game=request.form.get("game"),
            language=request.form.get("language"),
            set_name=request.form.get("set_name"),
            card_name=request.form.get("card_name"),
            card_number=request.form.get("card_number"),
            variant=request.form.get("variant"),
            declared_value=to_float(request.form.get("declared_value")),
            pregrade_estimate=request.form.get("pregrade_estimate"),
        )
        db.session.add(card)
        db.session.commit()
        return redirect(url_for("submission_detail", submission_id=submission.id))

    return render_template("submission_add_card.html", submission=submission)

@app.route("/cards")
def list_cards():
    # Filters from query parameters
    grade = request.args.get("grade")               # e.g. "10"
    submission_status = request.args.get("status")  # e.g. "At PSA"
    search = request.args.get("search")             # free text

    query = Card.query.join(Card.submission)  # join Submission for status filter

    if grade:
        query = query.filter(Card.psa_grade == grade)

    if submission_status and submission_status != "All":
        query = query.filter(Submission.status == submission_status)

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                Card.card_name.ilike(pattern),
                Card.set_name.ilike(pattern),
                Submission.name.ilike(pattern),
            )
        )

    cards = query.order_by(Submission.date_created.desc(), Card.id.desc()).all()

    # For the filter dropdowns
    statuses = ["All", "Draft", "Collecting cards", "Shipped", "At PSA", "Returned"]

    return render_template(
        "cards.html",
        cards=cards,
        current_grade=grade or "",
        current_status=submission_status or "All",
        current_search=search or "",
        statuses=statuses,
    )      

@app.route("/export/cards")
def export_cards():
    # Export all cards as CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        "card_id",
        "submission_id",
        "submission_name",
        "customer_id",
        "customer_name",
        "game",
        "language",
        "set_name",
        "card_name",
        "card_number",
        "variant",
        "declared_value",
        "pregrade_estimate",
        "psa_grade",
        "grade_date",
        "sale_price",
        "submission_status",
    ])

    cards = Card.query.join(Card.submission).outerjoin(Card.customer).all()
    for c in cards:
        writer.writerow([
            c.id,
            c.submission_id,
            c.submission.name if c.submission else "",
            c.customer_id,
            c.customer.name if c.customer else "",
            c.game,
            c.language,
            c.set_name,
            c.card_name,
            c.card_number,
            c.variant,
            c.declared_value,
            c.pregrade_estimate,
            c.psa_grade,
            c.grade_date.strftime("%Y-%m-%d") if c.grade_date else "",
            c.sale_price,
            c.submission.status if c.submission else "",
        ])

    csv_data = output.getvalue()
    response = make_response(csv_data)
    response.headers["Content-Disposition"] = "attachment; filename=cards.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

def _to_float(value, default=None):
    if value is None:
        return default
    s = str(value).strip()
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default

def _to_date(value):
    """Accepts YYYY-MM-DD or blank. Returns datetime or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None

def _read_csv_dicts(file_storage):
    """Returns (rows, error). rows is list[dict]."""
    try:
        raw = file_storage.read().decode("utf-8-sig")  # handles Excel BOM
    except Exception:
        return None, "Could not read file. Please upload a UTF-8 CSV."
    f = io.StringIO(raw)
    reader = csv.DictReader(f)
    if not reader.fieldnames:
        return None, "CSV has no header row."
    rows = [dict(r) for r in reader]
    return rows, None


@app.route("/admin/import")
def import_home():
    return render_template("import_home.html")


@app.route("/admin/import/cards", methods=["GET", "POST"])
def import_cards_csv():
    if request.method == "POST":
        file = request.files.get("file")
        dry_run = request.form.get("dry_run") == "1"

        if not file or file.filename == "":
            return render_template("import_cards.html", error="Please choose a CSV file.")
        # helps Pylance understand file is not None
        assert file is not None

        rows, err = _read_csv_dicts(file)
        if err:
            return render_template("import_cards.html", error=err)

        if not rows:
            return render_template("import_cards.html", error="CSV has no data rows.")

        headers = {h.strip() for h in rows[0].keys()}
        missing = {"card_id"} - headers
        if missing:
            return render_template("import_cards.html", error=f"Missing required columns: {', '.join(sorted(missing))}")

        allowed_fields = {
            "psa_grade", "psa_cert", "sale_price", "grade_date",
            "declared_value", "pregrade_estimate",
            "game", "language", "set_name", "card_name", "card_number", "variant",
        }

        errors = []
        updated = 0
        skipped = 0

        # Preload cards for efficiency
        card_ids = []
        for idx, r in enumerate(rows, start=2):  # row 1 is header
            cid = (r.get("card_id") or "").strip()
            if not cid.isdigit():
                errors.append(f"Row {idx}: card_id must be an integer.")
            else:
                card_ids.append(int(cid))

        if errors:
            return render_template("import_cards.html", error="Fix the errors below.", errors=errors)

        cards = Card.query.filter(Card.id.in_(card_ids)).all()
        card_map = {c.id: c for c in cards}

        for idx, r in enumerate(rows, start=2):
            cid = int((r.get("card_id") or "0").strip())
            card = card_map.get(cid)
            if not card:
                skipped += 1
                continue

            changed = False
            for key in allowed_fields:
                if key not in r:
                    continue
                val = r.get(key)

                if key in {"sale_price", "declared_value"}:
                    new_v = _to_float(val, default=card.sale_price if key == "sale_price" else card.declared_value)
                    if new_v is None:
                        errors.append(f"Row {idx}: {key} must be a number.")
                        continue
                    if getattr(card, key) != new_v:
                        setattr(card, key, new_v); changed = True

                elif key == "grade_date":
                    new_d = _to_date(val)
                    # Allow blank to clear date
                    if val is not None and str(val).strip() == "":
                        new_d = None
                    # If non-empty but invalid format
                    if str(val).strip() and new_d is None:
                        errors.append(f"Row {idx}: grade_date must be YYYY-MM-DD or blank.")
                        continue
                    if card.grade_date != new_d:
                        card.grade_date = new_d; changed = True

                else:
                    new_s = (val or "").strip()
                    # Treat blank as None for some fields
                    if key in {"psa_grade", "psa_cert", "pregrade_estimate"} and new_s == "":
                        new_s = None
                    if getattr(card, key) != new_s:
                        setattr(card, key, new_s); changed = True

            if changed:
                updated += 1

        if errors:
            return render_template("import_cards.html", error="Fix the errors below.", errors=errors)

        if dry_run:
            return render_template("import_result.html", title="Cards import (dry run)", updated=updated, skipped=skipped, dry_run=True)

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return render_template("import_cards.html", error=f"Database error: {str(e)}")

        return render_template("import_result.html", title="Cards import", updated=updated, skipped=skipped, dry_run=False)

    return render_template("import_cards.html", error=None)


@app.route("/admin/import/submissions", methods=["GET", "POST"])
def import_submissions_csv():
    if request.method == "POST":
        file = request.files.get("file")
        dry_run = request.form.get("dry_run") == "1"

        if not file or file.filename == "":
            return render_template("import_submissions.html", error="Please choose a CSV file.")
        # helps Pylance understand file is not None
        assert file is not None
        rows, err = _read_csv_dicts(file)
        if err:
            return render_template("import_submissions.html", error=err)
        if not rows:
            return render_template("import_submissions.html", error="CSV has no data rows.")

        headers = {h.strip() for h in rows[0].keys()}
        missing = {"submission_id"} - headers
        if missing:
            return render_template("import_submissions.html", error="Missing required column: submission_id")


        allowed_fields = {
            "name", "company", "branch", "service_level", "status",
            "total_declared_value", "total_cost", "total_collected",
            "date_shipped_out", "date_psa_received", "date_shipped_back", "date_checked_in",
        }

        errors = []
        updated = 0
        skipped = 0

        sub_ids = []
        for idx, r in enumerate(rows, start=2):
            sid = (r.get("submission_id") or "").strip()
            if not sid.isdigit():
                errors.append(f"Row {idx}: submission_id must be an integer.")
            else:
                sub_ids.append(int(sid))

        if errors:
            return render_template("import_submissions.html", error="Fix the errors below.", errors=errors)

        subs = Submission.query.filter(Submission.id.in_(sub_ids)).all()
        sub_map = {s.id: s for s in subs}

        for idx, r in enumerate(rows, start=2):
            sid = int((r.get("submission_id") or "0").strip())
            sub = sub_map.get(sid)
            if not sub:
                skipped += 1
                continue

            changed = False
            for key in allowed_fields:
                if key not in r:
                    continue
                val = r.get(key)

                if key in {"total_declared_value", "total_cost", "total_collected"}:
                    new_v = _to_float(val, default=getattr(sub, key))
                    if new_v is None:
                        errors.append(f"Row {idx}: {key} must be a number.")
                        continue
                    if getattr(sub, key) != new_v:
                        setattr(sub, key, new_v); changed = True

                elif key.startswith("date_"):
                    # Accept YYYY-MM-DD or blank
                    if val is not None and str(val).strip() == "":
                        new_d = None
                    else:
                        new_d = _to_date(val)
                        if str(val).strip() and new_d is None:
                            errors.append(f"Row {idx}: {key} must be YYYY-MM-DD or blank.")
                            continue
                    if getattr(sub, key) != new_d:
                        setattr(sub, key, new_d); changed = True

                else:
                    new_s = (val or "").strip()
                    if getattr(sub, key) != new_s:
                        setattr(sub, key, new_s); changed = True

            if changed:
                updated += 1

        if errors:
            return render_template("import_submissions.html", error="Fix the errors below.", errors=errors)

        if dry_run:
            return render_template("import_result.html", title="Submissions import (dry run)", updated=updated, skipped=skipped, dry_run=True)

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return render_template("import_submissions.html", error=f"Database error: {str(e)}")

        return render_template("import_result.html", title="Submissions import", updated=updated, skipped=skipped, dry_run=False)

    return render_template("import_submissions.html", error=None)

# -------- DASHBOARD --------

@app.route("/dashboard")
def dashboard():
    # --- Summary stats ---
    total_cards = Card.query.count()
    cards_at_psa = Card.query.join(Card.submission).filter(Submission.status == "At PSA").count()
    cards_returned = Card.query.join(Card.submission).filter(Submission.status == "Returned").count()

    # Active submissions: collecting, shipped, at PSA
    active_statuses = ["Collecting cards", "Shipped", "At PSA"]
    active_submissions = (
        Submission.query
        .filter(Submission.status.in_(active_statuses))
        .order_by(Submission.date_created.desc())
        .all()
    )

    # Recently returned submissions (last 5)
    recent_returned = (
        Submission.query
        .filter(Submission.status == "Returned")
        .order_by(Submission.date_created.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "dashboard.html",
        total_cards=total_cards,
        cards_at_psa=cards_at_psa,
        cards_returned=cards_returned,
        active_submissions=active_submissions,
        recent_returned=recent_returned,
    )

# -------- CUSTOMERS (ADMIN) --------

@app.route("/customers")
def list_customers():
    customers = Customer.query.order_by(Customer.name).all()
    return render_template("customers.html", customers=customers)

@app.route("/customers/new", methods=["GET", "POST"])
def new_customer():
    if request.method == "POST":
        customer = Customer(
            name=request.form["name"],
            handle=request.form.get("handle"),
            email=request.form.get("email"),
            phone=request.form.get("phone")
        )
        db.session.add(customer)
        db.session.commit()
        return redirect(url_for("list_customers"))
    return render_template("new_customer.html")

# -------- CUSTOMER SUBMISSION FORM --------

@app.route("/submit/<int:submission_id>", methods=["GET", "POST"])
def customer_submit(submission_id):
    submission = Submission.query.get_or_404(submission_id)

    if request.method == "POST":
        errors = []

        # --- Customer info ---
        name = request.form.get("name", "").strip()
        handle = request.form.get("handle", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()

        # Basic validation
        if not name:
            errors.append("Name is required.")
        if not email and not phone:
            errors.append("Please provide at least an email or a phone number so we can contact you.")

        # --- Card rows ---
        cards_data = []
        for i in range(1, 11):  # 10 card slots
            prefix = f"card{i}_"
            game = request.form.get(prefix + "game", "").strip()
            set_name = request.form.get(prefix + "set_name", "").strip()
            card_name = request.form.get(prefix + "card_name", "").strip()
            card_number = request.form.get(prefix + "card_number", "").strip()
            variant = request.form.get(prefix + "variant", "").strip()
            declared_value_str = request.form.get(prefix + "declared_value", "").strip()
            pregrade_estimate = request.form.get(prefix + "pregrade_estimate", "").strip()

            # Check if this row is "empty"
            if not (card_name or set_name or card_number or variant or declared_value_str):
                continue  # skip empty row

            # Declared value must be non-negative number (or blank)
            declared_value = 0.0
            if declared_value_str:
                try:
                    declared_value = float(declared_value_str)
                    if declared_value < 0:
                        errors.append(f"Declared value for Card {i} cannot be negative.")
                except ValueError:
                    errors.append(f"Declared value for Card {i} must be a number.")

            # Require at least card name AND set name for non-empty rows
            if not card_name or not set_name:
                errors.append(f"Card {i}: please fill in at least card name and set name.")

            cards_data.append({
                "game": game,
                "set_name": set_name,
                "card_name": card_name,
                "card_number": card_number,
                "variant": variant,
                "declared_value": declared_value,
                "pregrade_estimate": pregrade_estimate,
            })

        if not cards_data:
            errors.append("Please fill in at least one card.")

        # If there are errors, show them and repopulate the form
        if errors:
            return render_template(
                "customer_submit.html",
                submission=submission,
                errors=errors,
                form_data=request.form,
                card_slots=range(1, 11)
            )

        # --- Find or create customer ---
        customer = None
        if email:
            customer = Customer.query.filter_by(email=email).first()
        if not customer and phone:
            customer = Customer.query.filter_by(phone=phone).first()

        if not customer:
            customer = Customer(
                name=name,
                handle=handle,
                email=email,
                phone=phone
            )
            db.session.add(customer)
            db.session.flush()  # get customer.id

        # --- Create cards ---
        cards_created = 0
        for cd in cards_data:
            card = Card(
                submission_id=submission.id,
                customer_id=customer.id,
                game=cd["game"],
                set_name=cd["set_name"],
                card_name=cd["card_name"],
                card_number=cd["card_number"],
                variant=cd["variant"],
                declared_value=cd["declared_value"],
                pregrade_estimate=cd["pregrade_estimate"]
            )
            db.session.add(card)
            cards_created += 1

        db.session.commit()

        return render_template(
            "submit_success.html",
            submission=submission,
            customer=customer,
            cards_created=cards_created
        )

    # GET → show empty form
    return render_template("customer_submit.html", submission=submission, errors=None, form_data=None, card_slots=range(1, 11))

# -------- CUSTOMER GRADING STATUS (TRACKING) --------

@app.route("/track", methods=["GET", "POST"])
def track():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()

        if not email and not phone:
            error = "Please enter the email or phone number you used when submitting your cards."
            return render_template("track.html", error=error, form_data=request.form)

        query = Customer.query
        if email:
            query = query.filter(Customer.email == email)
        if phone:
            query = query.filter(Customer.phone == phone)

        customers = query.all()

        if not customers:
            error = (
                "We couldn't find any records for that contact. "
                "Please check for typos, or note that your cards may not have been keyed in yet."
            )
            return render_template("track.html", error=error, form_data=request.form)

        return render_template("track_results.html", customers=customers)

    # GET → show empty search form
    return render_template("track.html", error=None, form_data=None)

# -------- DELETE SUBMISSIONS --------

@app.route("/admin/delete_all_submissions")
def delete_all_submissions():
    # Delete all cards first (because they depend on submissions)
    Card.query.delete()
    # Delete all submissions
    Submission.query.delete()

    db.session.commit()
    return "All submissions and cards deleted."

@app.route("/submissions/<int:submission_id>/delete", methods=["POST"])
def delete_submission(submission_id):
    submission = Submission.query.get_or_404(submission_id)

    # Then delete the submission itself
    db.session.delete(submission)
    db.session.commit()

    return redirect(url_for("list_submissions"))

# -------- ERROR HANDLERS --------

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

if __name__ == "__main__":
    app.run(debug=True)