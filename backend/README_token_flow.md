Docs: Company token refresh flow

- POST /companies/drive-exchange: exchange auth code -> stores access_token and refresh_token in company document.
- POST /companies/drive-creds: alternative to supply access/refresh from client.
- maybe_refresh_drive_token(company): called when reading company token; refreshes if expires soon and refresh_token present.

Frontend:
- Stores personal Google token in localStorage (`googleToken`).
- Stores company access token only in sessionStorage (`companyToken`) to avoid long-term persistence.
- Frontend polls /nguoi-dung/me and will pick up refreshed company token from backend.
