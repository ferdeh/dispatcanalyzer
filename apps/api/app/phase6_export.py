from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from .phase6_service import get_prediction_run


def workbook_bytes(sheets: list[tuple[str, list[str], list[list]]]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, headers, rows in sheets:
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="0B73BF")
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            width = min(50, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
            sheet.column_dimensions[column[0].column_letter].width = width
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def loading_order_template() -> bytes:
    return workbook_bytes(
        [("Loading Order", ["loading_order_no", "shift_gate_out", "spbu_no"], [["LO000001", "Shift 1", "SPBU001"]])]
    )


def mt_availability_template() -> bytes:
    return workbook_bytes(
        [("MT Availability", ["shift", "vehicle_registration_no"], [["Shift 1", "B9123ABC"]])]
    )


def validation_report(issues: list[dict]) -> bytes:
    headers = ["File", "Row", "Field", "Status", "Error Code", "Description"]
    rows = [[issue["file"], issue["row"], issue["field"], issue["status"], issue["error_code"], issue["description"]] for issue in issues]
    return workbook_bytes([("Validation", headers, rows)])


def prediction_export(db: Session, run_id: str) -> tuple[bytes, str]:
    payload = get_prediction_run(db, run_id)
    summary_rows = [
        ["prediction_run_id", payload["prediction_run_id"]],
        ["depot", payload["depot"]],
        ["model", payload["model"].get("model_name")],
        ["model_version", payload["model"].get("model_version")],
        ["run_datetime", payload["created_at"]],
        ["user", payload["created_by"]],
        *[[key, value] for key, value in payload["summary"].items()],
    ]
    shipment_rows = []
    assignment_rows = []
    candidate_rows = []
    for shipment in payload["shipments"]:
        for line in shipment["lines"]:
            shipment_rows.append(
                [
                    payload["prediction_run_id"],
                    shipment["shift"],
                    shipment["predicted_shipment_id"],
                    line["loading_order_no"],
                    line["spbu_no"],
                    shipment["shipment_prediction_score"],
                    shipment["shipment_confidence_level"],
                ]
            )
        assignment = shipment["assignment"]
        assignment_rows.append(
            [
                payload["prediction_run_id"],
                shipment["shift"],
                shipment["predicted_shipment_id"],
                assignment["assigned_vehicle_registration"],
                assignment["mt_assignment_score"],
                assignment["assignment_status"],
                shipment["is_manual_override"] or assignment["assignment_status"] == "MANUAL_OVERRIDE",
                assignment["unassigned_reason"],
            ]
        )
        for candidate in shipment["candidates"]:
            candidate_rows.append(
                [
                    shipment["predicted_shipment_id"],
                    candidate["vehicle_registration_no"],
                    candidate["candidate_rank"],
                    candidate["prediction_score"],
                    candidate["compatibility_status"],
                    candidate["exclusion_reason"],
                ]
            )
    validation_rows = [
        [issue["file"], issue["row"], issue["field"], issue["status"], issue["error_code"], issue["description"]]
        for issue in payload["validation"]
    ]
    content = workbook_bytes(
        [
            ("Summary", ["Metric", "Value"], summary_rows),
            (
                "Shipment Result",
                ["prediction_run_id", "shift", "predicted_shipment_id", "loading_order_no", "spbu_no", "shipment_prediction_score", "shipment_confidence_level"],
                shipment_rows,
            ),
            (
                "MT Assignment",
                ["prediction_run_id", "shift", "predicted_shipment_id", "vehicle_registration_no", "mt_assignment_score", "assignment_status", "override_status", "unassigned_reason"],
                assignment_rows,
            ),
            (
                "MT Candidates",
                ["predicted_shipment_id", "vehicle_registration_no", "candidate_rank", "prediction_score", "compatibility_status", "exclusion_reason"],
                candidate_rows,
            ),
            ("Validation", ["file", "row", "field", "status", "error_code", "description"], validation_rows),
        ]
    )
    return content, f"{payload['prediction_run_id']}-prediction-result.xlsx"
