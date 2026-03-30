from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    handle = db.Column(db.String(120))          # Telegram / IG
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    notes = db.Column(db.Text)

    # One customer → many cards
    cards = db.relationship("Card", back_populates="customer")


class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)   # e.g. "Jan 2026 Bulk #1"

    # Grading company + branch (PSA has USA/Japan)
    company = db.Column(db.String(50), default="PSA")  # PSA / BGS / CGC
    branch = db.Column(db.String(50), default="USA")   # USA / Japan (mainly for PSA)

    service_level = db.Column(db.String(50), default="Bulk")           # Bulk / Regular / Express

    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    date_shipped_out = db.Column(db.DateTime, nullable=True)
    date_psa_received = db.Column(db.DateTime, nullable=True)
    date_shipped_back = db.Column(db.DateTime, nullable=True)
    date_checked_in = db.Column(db.DateTime, nullable=True)

    total_declared_value = db.Column(db.Float, default=0)
    total_cost = db.Column(db.Float, default=0)        # grading + shipping
    total_collected = db.Column(db.Float, default=0)   # from customers

    # Draft / Collecting cards / Shipped / At PSA / Returned
    status = db.Column(db.String(50), default="Draft")

    # One submission → many cards
    # cascade so deleting a submission can remove its cards safely
    cards = db.relationship(
        "Card",
        back_populates="submission",
        cascade="all, delete-orphan"
    )

    @property
    def profit(self):
        """
        Profit from this batch:
        total sale revenue of all cards - batch cost.
        (Matches how we show stats elsewhere.)
        """
        revenue = sum(c.sale_price or 0 for c in self.cards)
        return revenue - (self.total_cost or 0)


class Card(db.Model):
    __tablename__ = "cards"

    id = db.Column(db.Integer, primary_key=True)

    submission_id = db.Column(
        db.Integer,
        db.ForeignKey("submissions.id"),
        nullable=False
    )
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.id"),
        nullable=True
    )

    # What the customer requested (may differ from the batch settings until payment confirmed)
    requested_branch = db.Column(db.String(50))          # USA / Japan
    requested_service_level = db.Column(db.String(50))   # Bulk / Value / Regular / Express

    game = db.Column(db.String(50))           # Pokemon / One Piece etc
    language = db.Column(db.String(20))       # ENG / JPN
    set_name = db.Column(db.String(120))
    card_name = db.Column(db.String(120))
    card_number = db.Column(db.String(50))
    variant = db.Column(db.String(120))       # SAR / FA / Gold etc

    declared_value = db.Column(db.Float, default=0)
    pregrade_estimate = db.Column(db.String(10))  # e.g. "9", "10"
    psa_grade = db.Column(db.String(10))
    psa_cert = db.Column(db.String(50))
    grade_date = db.Column(db.DateTime, nullable=True)

    # Price at which the card can be sold  
    sale_price = db.Column(db.Float, default=0)

    # Relationships back to parent objects
    submission = db.relationship("Submission", back_populates="cards")
    customer = db.relationship("Customer", back_populates="cards")
