# github-email-notifications

Centralized email notifications for GitHub repositories, sent through **Brevo SMTP** by a
GitHub Actions **reusable workflow**.

- All the logic lives in [`.github/workflows/send-notification.yml`](.github/workflows/send-notification.yml).
- Each client repository only contains [`.github/workflows/notify.yml`](.github/workflows/notify.yml) (about 35 lines, identical everywhere).
- Recipients, events and branches are configured per repository with GitHub Actions **Variables**; the SMTP credentials with **Secrets**.
- No dependencies: the script only uses the Python standard library, already available on `ubuntu-latest` runners.

```
client repo (private or public)                   Gybra/github-email-notifications (public)
┌──────────────────────────────┐   workflow_call   ┌─────────────────────────────────────────┐
│ .github/workflows/notify.yml │ ────────────────▶ │ .github/workflows/send-notification.yml │ ──▶ smtp-relay.brevo.com:587
│ vars.NOTIFICATION_*          │   inputs + 2      │  (embedded Python script)               │     STARTTLS
│ secrets.BREVO_SMTP_*         │   secrets         └─────────────────────────────────────────┘
└──────────────────────────────┘
```

## Supported events

| Value in `NOTIFICATION_EVENTS` | When                                    | Email subject                                       |
|--------------------------------|-----------------------------------------|-----------------------------------------------------|
| `push`                         | push to a monitored branch              | `[GitHub] repo — New push on main` (or `Force push on main`, `Branch main deleted`) |
| `pr_opened`                    | PR opened against a monitored branch    | `[GitHub] repo — Pull Request #42 opened`           |
| `pr_merged`                    | PR merged into a monitored branch       | `[GitHub] repo — Pull Request #42 merged`           |
| `pr_closed`                    | PR closed without merge (optional)      | `[GitHub] repo — Pull Request #42 closed`           |
| *(workflow_dispatch)*          | manual test, ignores event/branch filters | `[GitHub] repo — Test notification`               |

Default when `NOTIFICATION_EVENTS` is empty: `push,pr_opened,pr_merged`.

For PRs, `NOTIFICATION_BRANCHES` applies to the **target (base) branch**: a PR
`feature/x → develop` sends no email if you only monitor `main,master`.

### No duplicates on merge

Merging a PR produces two events: `pull_request_target` (closed, merged) and `push` on the
target branch. The push run queries the GitHub API (`GET /repos/{repo}/commits/{sha}/pulls`):
if the head commit is the `merge_commit_sha` of a PR merged into the same branch and `pr_merged`
is enabled, the push email is skipped. This works with merge commits, squash and rebase. If
`pr_merged` is disabled, the push is notified as usual. If the API does not respond, a possible
duplicate is preferred over a lost notification.

### Email content

Responsive HTML (table layout with inline styles, 600 px max width) plus a plain-text version.
It contains repository, event type, branch, author, date and time (`Europe/Rome` timezone),
PR title and number, latest commit message, a button linking to the PR, commit or comparison,
and the latest 10 commits. It does not include PR bodies, diffs or commit author email addresses.

## 1. Configure Brevo (once)

1. **Account**: create an account at <https://www.brevo.com> (the free plan is enough).
2. **Verified sender**: *Senders, Domains & Dedicated IPs → Senders → Add a sender*, enter a name
   and address (e.g. `notifications@yourdomain.com`) and confirm the code you receive by email.
3. **Authenticated domain (recommended)**: *Senders, Domains & Dedicated IPs → Domains → Add a domain*
   and add the DKIM and DMARC DNS records shown. Without authentication, emails easily end up in
   spam. Do not use a `@gmail.com` or `@outlook.com` address as sender: their DMARC policies block
   sending from third-party servers.
4. **SMTP credentials**: account menu → *SMTP & API* → **SMTP** tab.
   - **Login**: the `xxxxxx@smtp-brevo.com` value shown there, **not** your account email → `BREVO_SMTP_LOGIN`.
   - **Generate a new SMTP key**: copy the key (`xsmtpsib-...`) right away → `BREVO_SMTP_KEY`.
     You need an **SMTP key**, not an API key (`xkeysib-...`).

Parameters used (fixed in the workflow): host `smtp-relay.brevo.com`, port `587`, STARTTLS with
certificate verification, LOGIN authentication.

### Brevo quota (free plan: 300 emails/day)

Brevo counts **each recipient** as one email: a notification to 4 recipients uses 4 emails.

```
emails per day ≈ (pushes + PRs opened + PRs merged + …) × number of recipients, summed over all repos
```

Example: 10 repos with 5 events a day each and 3 recipients → 150 emails a day. The quota is
shared by the whole Brevo account, including any other use. The workflow refuses more than 50
recipients per email. Once the quota is exhausted Brevo replies with an SMTP error and the job
fails; the error stays visible in the logs.

## 2. Connect a client repository

### 2.1 Caller workflow

Copy [`.github/workflows/notify.yml`](.github/workflows/notify.yml) into the client repository at
the same path, and commit it to the **default branch**: `pull_request_target` and
`workflow_dispatch` always use the default-branch version.

```bash
cd /path/to/client-repo
mkdir -p .github/workflows
curl -fsSL https://raw.githubusercontent.com/Gybra/github-email-notifications/main/.github/workflows/notify.yml \
  -o .github/workflows/notify.yml
git add .github/workflows/notify.yml && git commit -m "ci: add email notifications" && git push
```

### 2.2 Secrets and Variables

*Settings → Secrets and variables → Actions*:

| Type     | Name                    | Required | Example / default                                     |
|----------|-------------------------|----------|-------------------------------------------------------|
| Secret   | `BREVO_SMTP_LOGIN`      | yes      | `123abc001@smtp-brevo.com`                            |
| Secret   | `BREVO_SMTP_KEY`        | yes      | `xsmtpsib-…`                                          |
| Variable | `NOTIFICATION_ENABLED`  | yes      | `true` (any other value = off)                        |
| Variable | `NOTIFICATION_EMAILS`   | yes      | `dev@acme.com,pm@acme.com`                            |
| Variable | `NOTIFICATION_FROM`     | yes      | `GitHub Notifications <notifications@acme.com>`       |
| Variable | `NOTIFICATION_BRANCHES` | no       | default `main,master`; globs allowed, e.g. `main,release/*` |
| Variable | `NOTIFICATION_EVENTS`   | no       | default `push,pr_opened,pr_merged`                    |

The same settings via CLI:

```bash
REPO=HexLab-Software/repo-name
gh secret set BREVO_SMTP_LOGIN --repo "$REPO"        # paste the value when prompted
gh secret set BREVO_SMTP_KEY   --repo "$REPO"
gh variable set NOTIFICATION_ENABLED --repo "$REPO" --body "true"
gh variable set NOTIFICATION_EMAILS  --repo "$REPO" --body "dev@acme.com,pm@acme.com"
gh variable set NOTIFICATION_FROM    --repo "$REPO" --body "GitHub Notifications <notifications@acme.com>"
# optional
gh variable set NOTIFICATION_BRANCHES --repo "$REPO" --body "main,master"
gh variable set NOTIFICATION_EVENTS   --repo "$REPO" --body "push,pr_opened,pr_merged,pr_closed"
```

See also [`.env.example`](.env.example), documentation only: no `.env` file is ever read.

**Organization secrets**: on a paid plan you can create `BREVO_SMTP_LOGIN` and `BREVO_SMTP_KEY`
once (*Organization → Settings → Secrets and variables → Actions*) and assign them to repositories.
**On the GitHub Free plan (as HexLab-Software is today) organization secrets and variables are not
available to private repositories**: in that case set them per repository. The caller does not
change, because repository secrets take precedence over organization secrets with the same name.

### 2.3 Organization Actions policy

If the client repository belongs to an organization with a restrictive actions policy, go to
*Organization → Settings → Actions → General → Policies* and choose "Allow all actions and reusable
workflows", or add to the allowlist:

```
Gybra/github-email-notifications/.github/workflows/send-notification.yml@*
```

### 2.4 Manual test

*Actions → Email notifications → Run workflow* (or `gh workflow run notify.yml --repo "$REPO"`).
You receive a "Test notification" email with the active configuration (events, branches, number
of recipients) and a link to the run. The test ignores `NOTIFICATION_EVENTS` and
`NOTIFICATION_BRANCHES`, but respects `NOTIFICATION_ENABLED`.

## 3. Per-repository configuration

- **Different recipients per repo**: each repo has its own `NOTIFICATION_EMAILS`. Allowed
  separators: comma, semicolon, spaces or newlines. Duplicates (including case differences) are
  removed; malformed addresses are dropped with a warning, and if none is left the job fails. All
  recipients appear in the `To` field of the same email.
- **Enable or disable single events**: edit `NOTIFICATION_EVENTS`, for example `pr_merged` to get
  only merges, or `push,pr_opened,pr_merged,pr_closed` for everything.
- **Turn everything off**: `NOTIFICATION_ENABLED=false`, or delete the variable. The job is skipped
  without starting a runner and without using minutes.
- **Branches**: the caller uses `on.push.branches: ['**']` to receive pushes on every branch.
  The reusable workflow then filters using `NOTIFICATION_BRANCHES` (globs supported, e.g. `release/*`).
  When the variable is missing or empty, the fallback remains `main,master`.
  Every push starts a runner even if the central filter later skips the email; this may consume Actions minutes in private repositories.
- **Timezone**: `Europe/Rome` by default; add `timezone: UTC` under `with:` in the caller to change it.

## 4. Security

- **Credentials**: only in GitHub Secrets and passed explicitly (`smtp_login`, `smtp_key`), without
  `secrets: inherit`. GitHub masks them in logs; the script never prints them and SMTP errors only
  show the server's code and message.
- **Workflow injection**: no event data is interpolated with `${{ }}` inside a script. The payload
  is read as JSON from `GITHUB_EVENT_PATH` and inputs arrive as environment variables. A test checks
  that the embedded script contains no `${{` expressions. Log messages are escaped to prevent
  workflow command injection (`::...::`).
- **HTML/header injection**: everything that comes from the event (PR titles, commit messages,
  branch names) is escaped with `html.escape`. Subject and headers are single-line, without control
  characters. Links are only rendered when they point to the GitHub server.
- **PRs from forks**: the caller uses `pull_request_target`, which has access to secrets and always
  runs the workflow version on the default branch. This is safe because **no code from the PR is
  ever checked out or executed**: the workflow does not use `actions/checkout`. Do not add steps
  that check out the PR in this workflow.
- **Least privilege**: `contents: read` and `pull-requests: read`, used only for merge
  de-duplication and for the PR commit list.
- **Reliability**: 5-minute job timeout and 30-second SMTP timeout. Temporary errors (4xx,
  disconnections, network errors) are retried at most 3 times (waits of 10 and 30 seconds).
  Permanent errors (535 authentication, sender refused, 5xx) fail immediately. An error on `QUIT`
  after a successful send never triggers a resend.
- **Concurrency**: one group per distinct event (repo + event + PR/ref + `updated_at`/sha).
  Re-deliveries of the same event are serialized, while different pushes or PRs never cancel each
  other (`cancel-in-progress: false`).
- **Public repository**: this repository is public, so it must not contain any sensitive data;
  recipients and credentials live only in client repositories. The run logs of this repository are
  public too, which is why the script never prints email addresses. The `notify.yml` here stays
  inactive until you set `NOTIFICATION_ENABLED=true` in this repository. If you enable it, anyone
  opening a PR can trigger an email.

## 5. GitHub Actions limitations to know

- **Reusable workflows and private repos**: a workflow in a **private** repository can only be
  called by repositories of the same owner, after enabling it in *Settings → Actions → General →
  Access*. That is why the central repository is **public**: it can be called by any repository,
  of both `Gybra` and `HexLab-Software`, private ones included. The run happens in the context of
  the calling repository, with its secrets and its minutes.
- **No cross-repo file access**: the calling repository's `GITHUB_TOKEN` cannot read other private
  repositories. The script is embedded in the workflow, so it travels with the same version (`@v1`)
  without a checkout or extra tokens.
- **Versioning**: callers use `@v1`. The template installation command above reads the current `main` branch;
  the reusable workflow still uses the `v1` tag. After a backward-compatible change, move the tag
  (`git tag -f v1 && git push -f origin v1`); for breaking changes create `v2`. If you want
  immutability, pin a commit SHA instead of the tag.
- **Variables in triggers**: `on.push.branches` does not accept `vars`, so the caller listens on
  all branches and the reusable workflow filters using `NOTIFICATION_BRANCHES`.
- **Events created by `GITHUB_TOKEN`**: pushes or merges performed by another workflow with
  `GITHUB_TOKEN` do not start new workflows, so they produce no notifications. Merges from the UI,
  the CLI or with a PAT/GitHub App do.
- **Dependabot**: runs started by Dependabot do not receive Actions secrets. If you need
  notifications on its PRs, also create `BREVO_SMTP_LOGIN` and `BREVO_SMTP_KEY` as *Dependabot secrets*.
- **Actions minutes**: on private repositories each job is rounded up to the minute (the Free plan
  includes 2000 minutes a month); on public repositories minutes are free. Jobs skipped by the `if`
  use no minutes. Every push to a monitored branch and every PR opened or closed uses about 1
  minute, even when the script decides not to send (event disabled or merge already notified).
- **Listed commits**: the email shows at most the latest 10 commits. For PRs, the first 100 commits
  are read through the API.

## 6. Troubleshooting

Open the run in *Actions → Email notifications → job "notify / Send email"*. The script writes
explicit `notice`, `warning` and `error` annotations.

| Symptom | Likely cause and fix |
|---|---|
| Job **skipped** | `NOTIFICATION_ENABLED` is not `true`. |
| No run on push | The branch is not in the caller's `on.push.branches`, or the caller is not on the branch you pushed to. |
| No run for PRs | `notify.yml` is not on the **default branch** (`pull_request_target` only reads that one). |
| `Secret smtp_login is required, but not provided` | The caller does not pass the secrets, or passes them with different names. |
| `SMTP credentials are empty` | `BREVO_SMTP_LOGIN` or `BREVO_SMTP_KEY` do not exist in the repo (Free plan: no org secrets for private repos), or the run was started by Dependabot. |
| `authentication failed (535 …)` | Wrong login (you need the `@smtp-brevo.com` one), revoked key, or an API key instead of an SMTP key. Regenerate the SMTP key. |
| `sender refused (…)` | `NOTIFICATION_FROM` is not a verified sender in Brevo. |
| `server rejected the message (5xx …)` | Daily quota exhausted, Brevo account suspended or under review: check the Brevo dashboard. |
| `temporary SMTP failure persisted after 3 attempts` | Brevo or the network is unavailable; run the job again with *Re-run*. |
| `cannot connect to smtp-relay.brevo.com:587` | On self-hosted runners, outbound port 587 is blocked by the firewall. |
| `GitHub API returned HTTP 403` | The caller reduced `permissions`: `contents: read` and `pull-requests: read` are needed. Without them, merge de-duplication and the PR commit list are skipped. |
| `NOTIFICATION_EMAILS is empty or has no valid address` | Variable missing or with malformed addresses. |
| Workflow not allowed / `not allowed to be used` | Organization Actions policy: see §2.3. |
| Emails in spam | Authenticate the sender domain in Brevo (DKIM and DMARC). |

## 7. Development

```bash
python3 -m unittest discover -s tests -v                           # tests (standard library only)
mkdir -p /tmp/previews && PREVIEW_DIR=/tmp/previews python3 -m unittest discover -s tests   # save the rendered HTML emails
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest  # lint the workflows
```

The tests extract the script from the `run:` block of `send-notification.yml`, so they check
exactly the code that runs in CI: configuration parsing, filters, merge de-duplication, escaping,
retries and SMTP error handling, against a fake SMTP server. No test sends real emails.
