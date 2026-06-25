from pathlib import Path

import pandas as pd
from fpdf import FPDF


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def write_pdf(path: Path, title: str, lines: list[str]):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    for line in lines:
        pdf.multi_cell(usable_width, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def sales_ops():
    base = FIXTURE_DIR / "sales_ops"
    base.mkdir(parents=True, exist_ok=True)

    sales = pd.DataFrame([
        {"month": "2026-01", "region": "North", "product": "Alpha", "revenue": 124000, "units": 410, "discount_pct": 7, "channel": "Direct"},
        {"month": "2026-01", "region": "South", "product": "Beta", "revenue": 91000, "units": 360, "discount_pct": 11, "channel": "Partner"},
        {"month": "2026-02", "region": "North", "product": "Alpha", "revenue": 138000, "units": 445, "discount_pct": 6, "channel": "Direct"},
        {"month": "2026-02", "region": "West", "product": "Gamma", "revenue": 76000, "units": 250, "discount_pct": 13, "channel": "Online"},
        {"month": "2026-03", "region": "South", "product": "Alpha", "revenue": 147500, "units": 470, "discount_pct": 8, "channel": "Partner"},
        {"month": "2026-03", "region": "West", "product": "Beta", "revenue": 99000, "units": 330, "discount_pct": 10, "channel": "Online"},
        {"month": "2026-04", "region": "North", "product": "Gamma", "revenue": 83000, "units": 270, "discount_pct": 12, "channel": "Direct"},
        {"month": "2026-04", "region": "South", "product": "Beta", "revenue": 112000, "units": 390, "discount_pct": 9, "channel": "Partner"},
    ])
    targets = pd.DataFrame([
        {"region": "North", "quarterly_target": 330000, "owner": "Aline"},
        {"region": "South", "quarterly_target": 310000, "owner": "Bruno"},
        {"region": "West", "quarterly_target": 210000, "owner": "Carla"},
    ])
    sales.to_csv(base / "sales_pipeline.csv", index=False)
    targets.to_excel(base / "regional_targets.xlsx", index=False)
    write_pdf(base / "sales_context.pdf", "Sales Context", [
        "The board wants a concise view of regional revenue, product mix, and target attainment.",
        "Prioritize practical recommendations for channel allocation and discount discipline.",
    ])


def hr_retention():
    base = FIXTURE_DIR / "hr_retention"
    base.mkdir(parents=True, exist_ok=True)

    employees = pd.DataFrame([
        {"employee_id": 1, "department": "Engineering", "tenure_months": 18, "engagement": 72, "left_company": False, "manager_score": 82},
        {"employee_id": 2, "department": "Sales", "tenure_months": 9, "engagement": 54, "left_company": True, "manager_score": 61},
        {"employee_id": 3, "department": "Support", "tenure_months": 24, "engagement": 66, "left_company": False, "manager_score": 70},
        {"employee_id": 4, "department": "Sales", "tenure_months": 14, "engagement": 49, "left_company": True, "manager_score": 58},
        {"employee_id": 5, "department": "Engineering", "tenure_months": 32, "engagement": 81, "left_company": False, "manager_score": 88},
        {"employee_id": 6, "department": "Operations", "tenure_months": 7, "engagement": 57, "left_company": True, "manager_score": 64},
        {"employee_id": 7, "department": "Support", "tenure_months": 11, "engagement": 62, "left_company": False, "manager_score": 67},
        {"employee_id": 8, "department": "Operations", "tenure_months": 29, "engagement": 75, "left_company": False, "manager_score": 79},
    ])
    hiring = pd.DataFrame([
        {"department": "Engineering", "open_roles": 4, "time_to_fill_days": 42},
        {"department": "Sales", "open_roles": 7, "time_to_fill_days": 35},
        {"department": "Support", "open_roles": 3, "time_to_fill_days": 28},
        {"department": "Operations", "open_roles": 5, "time_to_fill_days": 31},
    ])
    employees.to_excel(base / "employee_retention.xlsx", index=False)
    hiring.to_csv(base / "hiring_pipeline.csv", index=False)
    write_pdf(base / "people_notes.pdf", "People Team Notes", [
        "Leadership is concerned about early-tenure attrition in Sales and Operations.",
        "The retention report should balance risk diagnosis with a concise intervention plan.",
    ])


def logistics_quality():
    base = FIXTURE_DIR / "logistics_quality"
    base.mkdir(parents=True, exist_ok=True)

    shipments = pd.DataFrame([
        {"week": "2026-W01", "warehouse": "A", "carrier": "FastShip", "shipments": 520, "late_pct": 4.2, "damage_pct": 0.8, "cost": 28400},
        {"week": "2026-W01", "warehouse": "B", "carrier": "RoadMax", "shipments": 430, "late_pct": 7.9, "damage_pct": 1.4, "cost": 24100},
        {"week": "2026-W02", "warehouse": "A", "carrier": "FastShip", "shipments": 545, "late_pct": 3.8, "damage_pct": 0.7, "cost": 29200},
        {"week": "2026-W02", "warehouse": "C", "carrier": "AirSwift", "shipments": 310, "late_pct": 2.1, "damage_pct": 0.5, "cost": 35100},
        {"week": "2026-W03", "warehouse": "B", "carrier": "RoadMax", "shipments": 470, "late_pct": 8.4, "damage_pct": 1.7, "cost": 26000},
        {"week": "2026-W03", "warehouse": "C", "carrier": "AirSwift", "shipments": 330, "late_pct": 2.4, "damage_pct": 0.6, "cost": 36500},
    ])
    incidents = pd.DataFrame([
        {"warehouse": "A", "incident_type": "Picking error", "count": 12},
        {"warehouse": "B", "incident_type": "Late pickup", "count": 31},
        {"warehouse": "B", "incident_type": "Damaged parcel", "count": 14},
        {"warehouse": "C", "incident_type": "Address issue", "count": 8},
    ])
    shipments.to_csv(base / "shipment_quality.csv", index=False)
    incidents.to_excel(base / "incident_log.xlsx", index=False)
    write_pdf(base / "operations_brief.pdf", "Operations Brief", [
        "The COO needs to understand whether cost, lateness, or damages are the dominant logistics issue.",
        "Highlight warehouse-carrier combinations that deserve immediate operational review.",
    ])


def education_outcomes():
    base = FIXTURE_DIR / "education_outcomes"
    base.mkdir(parents=True, exist_ok=True)

    students = pd.DataFrame([
        {"campus": "East", "program": "Data", "students": 120, "completion_rate": 0.82, "placement_rate": 0.74, "satisfaction": 8.4},
        {"campus": "East", "program": "Design", "students": 90, "completion_rate": 0.76, "placement_rate": 0.69, "satisfaction": 8.1},
        {"campus": "West", "program": "Data", "students": 140, "completion_rate": 0.79, "placement_rate": 0.71, "satisfaction": 8.0},
        {"campus": "West", "program": "Business", "students": 110, "completion_rate": 0.84, "placement_rate": 0.78, "satisfaction": 8.6},
        {"campus": "Central", "program": "Design", "students": 75, "completion_rate": 0.72, "placement_rate": 0.63, "satisfaction": 7.7},
        {"campus": "Central", "program": "Business", "students": 125, "completion_rate": 0.88, "placement_rate": 0.81, "satisfaction": 8.8},
    ])
    budget = pd.DataFrame([
        {"campus": "East", "instruction_budget": 620000, "career_services_budget": 160000},
        {"campus": "West", "instruction_budget": 710000, "career_services_budget": 175000},
        {"campus": "Central", "instruction_budget": 580000, "career_services_budget": 150000},
    ])
    students.to_excel(base / "student_outcomes.xlsx", index=False)
    budget.to_csv(base / "campus_budget.csv", index=False)
    write_pdf(base / "academic_context.pdf", "Academic Context", [
        "The provost wants a view of completion, placement, and satisfaction by campus and program.",
        "The report should identify where investment appears to produce the strongest outcomes.",
    ])


if __name__ == "__main__":
    sales_ops()
    hr_retention()
    logistics_quality()
    education_outcomes()
    print(f"Fixtures written to {FIXTURE_DIR}")
