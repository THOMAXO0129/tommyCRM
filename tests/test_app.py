import csv
import io
import tempfile
import unittest
from pathlib import Path

from app import create_app


class CRMApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "test.db"
        self.app = create_app({"TESTING": True, "DATABASE": str(database)})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def post(self, url, body):
        return self.client.post(url, json=body)

    def test_home_and_health(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/health").json["status"], "ok")

    def test_contact_and_company_relationship_and_csv_export(self):
        company_response = self.post("/api/companies", {"name": "Acme", "industry": "Software"})
        self.assertEqual(company_response.status_code, 201)
        company_id = company_response.json["id"]
        contact_response = self.post("/api/contacts", {
            "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
            "company_id": company_id, "notes": "=1+1",
        })
        self.assertEqual(contact_response.status_code, 201)
        self.assertEqual(self.client.get("/api/contacts?q=ada").json[0]["company_name"], "Acme")
        export = self.client.get("/api/export/contacts.csv")
        self.assertEqual(export.status_code, 200)
        parsed = list(csv.DictReader(io.StringIO(export.get_data(as_text=True).lstrip("\ufeff"))))
        self.assertEqual(parsed[0]["email"], "ada@example.com")
        self.assertEqual(parsed[0]["notes"], "'=1+1")

    def test_deal_task_and_activity_workflow(self):
        contact = self.post("/api/contacts", {"first_name": "Grace", "last_name": "Hopper"}).json
        deal = self.post("/api/deals", {
            "title": "Annual contract", "value": 12500, "stage": "Proposal",
            "contact_id": contact["id"], "expected_close": "2027-01-15",
        })
        self.assertEqual(deal.status_code, 201)
        task = self.post("/api/tasks", {
            "title": "Send proposal", "contact_id": contact["id"], "deal_id": deal.json["id"],
            "due_date": "2026-12-01", "priority": "High",
        })
        self.assertEqual(task.status_code, 201)
        self.assertEqual(len(self.client.get("/api/deals?q=Annual").json), 1)
        self.assertEqual(len(self.client.get("/api/tasks?q=proposal").json), 1)
        activity = self.post("/api/activities", {
            "kind": "Call", "subject": "Discovery call", "contact_id": contact["id"],
        })
        self.assertEqual(activity.status_code, 201)
        self.assertEqual(len(self.client.get("/api/activities?q=Discovery").json), 1)
        dashboard = self.client.get("/api/dashboard").json
        self.assertEqual(dashboard["open_deals"], 1)
        self.assertEqual(dashboard["pipeline_value"], 12500)
        self.assertEqual(dashboard["open_tasks"], 1)
        self.assertEqual(len(dashboard["recent_activities"]), 1)

    def test_validation_missing_record_and_delete_behavior(self):
        self.assertEqual(self.post("/api/contacts", {"first_name": "", "last_name": "Missing"}).status_code, 400)
        self.assertEqual(self.post("/api/contacts", {
            "first_name": "Invalid", "last_name": "Email", "email": "not-an-email",
        }).status_code, 400)
        self.assertEqual(self.post("/api/contacts", {
            "first_name": "Invalid", "last_name": "Status", "status": "Administrator",
        }).status_code, 400)
        self.assertEqual(self.post("/api/contacts", {
            "first_name": "Invalid", "last_name": "ID", "company_id": True,
        }).status_code, 400)
        self.assertEqual(self.post("/api/deals", {"title": "Bad", "value": 10, "stage": "Unknown"}).status_code, 400)
        self.assertEqual(self.post("/api/deals", {"title": "Bad", "value": "inf"}).status_code, 400)
        self.assertEqual(self.post("/api/deals", {
            "title": "Bad date", "value": 10, "expected_close": "2026-W47-5",
        }).status_code, 400)
        self.assertEqual(self.post("/api/tasks", {"title": "Bad", "due_date": "not-a-date"}).status_code, 400)
        self.assertEqual(self.client.get(f"/api/contacts?q={'x' * 201}").status_code, 400)
        self.assertEqual(self.client.put("/api/companies/999", json={"name": "Missing"}).status_code, 404)
        company = self.post("/api/companies", {"name": "Delete me"}).json
        self.assertEqual(self.client.delete(f"/api/companies/{company['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/companies/{company['id']}").status_code, 404)

    def test_search_treats_sql_wildcards_as_literal_characters(self):
        self.post("/api/contacts", {"first_name": "100%", "last_name": "Real"})
        self.post("/api/contacts", {"first_name": "1000", "last_name": "Other"})
        self.assertEqual(len(self.client.get("/api/contacts?q=%25").json), 1)

    def test_update_task_and_delete_contact_preserves_related_records(self):
        contact = self.post("/api/contacts", {"first_name": "Katherine", "last_name": "Johnson"}).json
        task = self.post("/api/tasks", {"title": "Follow up", "contact_id": contact["id"]}).json
        updated = self.client.put(f"/api/tasks/{task['id']}", json={
            "title": "Follow up tomorrow", "status": "Done",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["status"], "Done")
        self.assertEqual(self.client.delete(f"/api/contacts/{contact['id']}").status_code, 200)
        self.assertIsNone(self.client.get("/api/tasks").json[0]["contact_id"])


if __name__ == "__main__":
    unittest.main()
