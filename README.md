# Jobs Map · Germany

A free website that shows open roles in Germany on a map, at companies that offer visa or relocation support plus other German startups. Filter by city and by experience level, and apply on the company's own careers page.

- **Live data:** every day a GitHub robot pulls jobs straight from each company's careers system (Greenhouse, Lever, Ashby, Personio, Recruitee, SmartRecruiters).
- **Map:** OpenStreetMap, with one pin per company per city.
- **Filters:** city, level (entry-level, internships and students, experienced), keyword search, "visa-friendly companies only", and "job ad mentions visa / relocation".
- **Shareable links:** filters are kept in the address, e.g. `.../?city=Berlin&level=entry`.

Cost: free. You need a free GitHub account.

---

## Setup (about 15 minutes, once)

### 1. Create the repository
1. Sign in at [github.com](https://github.com) (or create a free account).
2. Click **+** (top right) → **New repository**.
3. Name it, for example `custom_name`. Choose **Public** (GitHub Pages is free for public repositories). Click **Create repository**.

### 2. Upload the files
1. On the new repository page, click **uploading an existing file**.
2. Unzip `visa-jobs-map.zip` on your computer. Open the unzipped folder, select **everything inside it**, and drag it into the browser window.
   - The `.github` folder is hidden on some computers. On Mac press `Cmd + Shift + .` in Finder to show it. On Windows enable **View → Hidden items**. It must be uploaded: it contains the daily update robot.
3. Click **Commit changes**.

Check that the repository now shows `index.html`, `companies.json`, `scripts/`, `data/` and `.github/`.

### 3. Turn on the website
1. In the repository, go to **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**. (Nothing else to choose; the robot publishes the site.)

### 4. Load the jobs for the first time
1. Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Update jobs** on the left → **Run workflow** → **Run workflow**.
3. Wait until it shows a green tick. The first run takes longer (5 to 15 minutes) because it looks up where each company posts its jobs.
4. Click the finished run: the link to your website is shown in the **deploy** box, like `https://YOUR-NAME.github.io/visa-jobs-map/`. It is also under **Settings → Pages**.

After that it updates by itself every day at about 5 am German time, and every time you edit a file.

If the robot fails with a "permission denied" error on push: **Settings → Actions → General → Workflow permissions → Read and write permissions → Save**, then run it again.

---

## Adding or fixing companies

All companies are in `companies.json`. Click it on GitHub, then the pencil icon to edit.

Add a company:
```json
{"name": "Company Name", "city": "Berlin", "visa": false, "offer": "", "sector": "", "group": "More startups"}
```
- Set `"visa": true` and write the `offer` if the company says it sponsors visas or helps with relocation.
- The robot finds the company's job board by itself. If it picks the wrong one, or finds nothing, tell it exactly where to look with an `ats` line, using the name in the company's careers link:

| Careers link looks like | Add this |
|---|---|
| `job-boards.greenhouse.io/acme` or `boards.greenhouse.io/acme` | `"ats": "greenhouse:acme"` |
| `jobs.lever.co/acme` | `"ats": "lever:acme"` |
| `jobs.ashbyhq.com/acme` | `"ats": "ashby:acme"` |
| `acme.jobs.personio.de` | `"ats": "personio:acme"` |
| `acme.recruitee.com` | `"ats": "recruitee:acme"` |
| `jobs.smartrecruiters.com/Acme` | `"ats": "smartrecruiters:Acme"` |
| Their own system (SAP, Siemens, Workday…) | `"ats": "none", "careers": "https://their-careers-page"` |

Large corporations with their own systems (SAP, Amazon, Google, Zalando…) appear only with a careers link, not with role counts.

`data/ats_map.json` stores what the robot found. Delete a company's line there to make it search again.

## Run it on your own computer (optional)
```bash
python3 scripts/fetch_jobs.py        # needs Python 3.9+, no extra packages
python3 -m http.server 8000          # then open http://localhost:8000
```

## Notes
- Levels are read from job titles ("Junior", "Graduate", "Working Student", "Senior"…) and, where the careers system provides it, its own level field. "Experienced" includes roles with no level in the title.
- "Job ad mentions visa / relocation" scans the job text for words like visa, Blue Card, relocation. It is a hint, not a promise: confirm with the recruiter.
- Map tiles © OpenStreetMap contributors, © CARTO.
