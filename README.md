# Orbit CRM

CRM web application in Spanish for managing contacts, companies, sales opportunities, follow-up tasks, and customer activity. The interface is responsive and served by Flask; records are stored in SQLite.

## Requirements

- Python 3.10+
- pip

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The SQLite database is created automatically at `instance/crm.db`; override the path with `CRM_DATABASE` if needed.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Features

- Dashboard with contact, company, pipeline, and open-task totals.
- Full create, read, update, and delete workflows for contacts, companies, deals, and tasks.
- Activity log for notes, calls, emails, and meetings.
- Search and status/stage filters across CRM records, plus CSV export for contacts.
- Relational SQLite schema, foreign-key checks, input validation, and an API health endpoint.
- GitHub Actions runs the automated test suite for pushes to `main` and pull requests.
- Responsive interface for desktop and mobile.

## API

The JSON API is rooted at `/api`. Collection endpoints are `GET`/`POST /api/contacts`, `/api/companies`, `/api/deals`, `/api/tasks`; individual records support `PUT` and `DELETE` at `/<id>`. Activities support `GET`/`POST /api/activities` and `DELETE /api/activities/<id>`. Additional endpoints: `GET /api/dashboard`, `GET /api/export/contacts.csv`, and `GET /api/health`.

Request bodies must be JSON objects. Validation errors return `{"error": "..."}` with HTTP 400; missing records return HTTP 404. Contact statuses are Lead, Prospect, Customer, and Inactive. Supported deal stages are Prospecting, Qualified, Proposal, Negotiation, Won, and Lost. Task priorities are Low, Medium, and High; statuses are Open, In progress, and Done.

## Project structure

```text
.
├── .env.example
├── .gitignore
├── app.py
├── .github/
│   └── workflows/
│       └── tests.yml
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   └── style.css
└── tests/
    └── test_app.py
```

## Deployment notes

Set `CRM_DATABASE` to a persistent writable path and run the app behind a production WSGI server such as Waitress or Gunicorn. This starter is intended for a trusted, single-user/internal deployment; add authentication, authorization, HTTPS, backups, and operational monitoring before exposing it to the public internet.
